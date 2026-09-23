"""受控工具循环：一轮对话里 Agent 怎么读资料、什么时候停（SPEC 决策 40）。

2026-09-20 落地（方案 A 的「剥离记忆系统」那一版）。原先计划对话是**一次性**的：
把全部业务事实拼成一大块、调一次模型、拿到人话就结束。改成循环之后，它先说自己
要读什么，系统去取，取回来再决定要不要接着读——**每轮读到的才算它这一轮的依据**。

四道闸，都是结构上的约束，不靠写代码的人自觉：

1. **最多 `MAX_MODEL_CALLS` 次模型调用**（3 次）。工具轮与「输出不合格重说一次」**共用**
   这个额度：结构错误也计入上限（SPEC 决策 6 的老规矩）。
2. **最多 `MAX_TOOL_CALLS` 次只读工具调用**（6 次）。一次模型调用里可以批量要好几样
   （省得为读两样资料多花一轮模型），但加起来不能超过 6 次；要超了，超出的那些直接
   不执行并如实告诉它「额度用完了」。
3. **工具结果的字符总闸**（`TOOL_TOTAL_CHAR_LIMIT`）。本轮读来的资料累加起来超过它就
   **从最旧的那一轮往下丢**（并留一句「更早的一次读取已省略」）——优先保当前问题与最新事实。
4. **停止必有原因**。答完了、撞上限、输出一直不合格，三种都写进 `agent_run` 并带中文一句话。

**输出验收有两档**（2026-09-21 走查整改第 2 条）：第一次不合格照老规矩带原因重说一次
（这一档是在教它套壳）；重试之后仍然是一段纯散文（不带 JSON 壳）的，由调用方的散文兜底
收下——整轮不至于连一句好好的回答一起丢掉。兜底**只在重试过一次之后**生效，且不落任何提案。

循环里**不吞错**：上游调用失败（`llm.LlmError`）原样往上抛，由调用方记一行失败的运行。
工具层的错（名字不认识、参数非法）不中断这一轮，而是原文回给模型让它自己纠正——
反复问不存在的东西最终会撞上限，那一刻才中止，并且**什么都不落**。

**它不写任何业务数据**：这个循环只读资料、只产出一段人话与（最多）一条建议；建议变成
提案、提案要你点头才生效，全在 `dialogue.py` 与 `plan_change.py` 那条既有的路上。
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, Field, ValidationError

from . import advisor, agent_tools, llm

# 这一轮的模型调用上限。工具轮与「不合格重说一次」共用它。
MAX_MODEL_CALLS = 3

# 这一轮的只读工具调用上限（一次模型调用可以批量要好几样，加起来不超过这个数）。
MAX_TOOL_CALLS = 6

# 本轮读来的资料总字符上限。超了就从最旧的那一轮往下丢。
TOOL_TOTAL_CHAR_LIMIT = 14000

# 运行结果三态。`limit` 与 `failed` 都会中止这一轮，区别只在「为什么」。
STATUS_OK = "ok"
STATUS_LIMIT = "limit"
STATUS_FAILED = "failed"


class AgentStop(RuntimeError):
    """这一轮没跑成：撞了上限，或者模型一直给不出合格输出。

    `message` 是给用户看的中文一句话（接口层翻成 400 的 `detail`）；`runbook` 是这一轮
    的现场，调用方拿它往 `agent_run` 写一行——**失败也要留账**，否则「它为什么没答上来」
    就成了无据可查。中止时**不落任何提案、不改任何业务数据**。
    """

    def __init__(self, message: str, runbook: "Runbook") -> None:
        super().__init__(message)
        self.runbook = runbook


@dataclass
class ToolUse:
    """一次工具调用：读了什么、带什么参数、成没成、花了多久、读到了什么（一句摘要）。"""

    name: str
    args: str
    ok: bool
    summary: str
    duration_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "args": self.args,
            "ok": self.ok,
            "summary": self.summary,
            "duration_ms": self.duration_ms,
        }


@dataclass
class Runbook:
    """这一轮的现场——`agent_run` 那一行的内容（不含 id 与时间，那两样由库给）。"""

    status: str
    stop_reason: str
    model_calls: int = 0
    tool_calls: int = 0
    tools: list[ToolUse] = field(default_factory=list)

    @property
    def tool_names(self) -> list[str]:
        """这一轮真读成的工具名，按读的顺序、去重——界面「本轮依据」直接用。"""
        return list(dict.fromkeys(tool.name for tool in self.tools if tool.ok))

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "stop_reason": self.stop_reason,
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "tool_names": self.tool_names,
            "tools": [tool.as_dict() for tool in self.tools],
        }


@dataclass
class Outcome:
    """跑成的那一轮：人话 + （最多）一条建议 + 现场。"""

    reply: str
    suggestion: dict[str, Any] | None
    runbook: Runbook


class ToolCall(BaseModel):
    """模型要读的一样东西。`name` 必须非空；参数只认登记过的键（在 `execute` 里查）。"""

    name: str = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)


class ToolRequest(BaseModel):
    """一次「我要读资料」的请求。**至少一样**——空数组不是请求，那是形状错。"""

    tool_calls: list[ToolCall] = Field(min_length=1)


# 最终输出的验收器：接原始文本，给（人话，要落库的建议 payload 或 None，不合格原因或 None）。
# 由调用方给（`dialogue._check_reply`）——信封里 `suggestion` 那一半的语义归它管，
# 这个模块只管「循环怎么转、什么时候停」。
FinalCheck = Callable[[str], tuple[str | None, dict[str, Any] | None, str | None]]

# 散文兜底验收器：模型 slipped 成纯散文（不带 JSON 壳）时接原始文本、给那段人话；
# 「这不是散文」就给 None。由调用方给（`dialogue._prose_reply`）。
ProseFallback = Callable[[str], str | None]


def run(
    conn: sqlite3.Connection,
    plan_id: int,
    *,
    task: str,
    system_prompt: str,
    history: list[dict[str, str]],
    check_final: FinalCheck,
    prose_fallback: ProseFallback | None = None,
    notice: str | None = None,
    provider_id: int | None = None,
    model: str | None = None,
    transport: llm.Transport | None = None,
) -> Outcome:
    """跑一轮：带着工具目录与最近历史，让模型自己决定读什么，直到它给出合格的人话。

    `history` 是**已经截断过**的历史（含用户这一句，最后一条）。资料不预装：这一轮它
    能看到的事实只有历史里说过的、和它自己读来的。

    `prose_fallback` 是**回话出口的那一道宽松**（2026-09-21 走查整改第 2 条）：模型偶尔
    不套信封、直接说一段人话，那一整轮就连人话一起丢掉、界面只剩一条红条。所以规则是
    **先照老规矩带原因重试一次，重试后仍然是散文就把它当回话收下**（建议记为空）。
    它只在「已经重试过一次之后」被问——第一次仍是严格判不合格，宽松只是兜底，不是通行证。
    「要读资料」与「建议」两处一行没动：解析不出 `tool_calls` 就不算读资料，散文里也永远
    解析不出建议（所以兜底不落任何提案）。

    `notice` 是这一轮开头那一段可选的提醒（工具目录与额度之后），目前是「记忆库变了」。
    """
    operation = llm.Operation(conn, task, limit=MAX_MODEL_CALLS, transport=transport)
    runbook = Runbook(status=STATUS_FAILED, stop_reason="")
    rounds: list[list[dict[str, str]]] = []
    problem = "它还没来得及说话"
    asked_for_tools = False
    retried = False

    for attempt in range(MAX_MODEL_CALLS):
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _opening(attempt, runbook.tool_calls, notice)},
            *history,
            *_kept_rounds(rounds),
        ]
        raw = operation.chat(messages, provider_id=provider_id, model=model)
        runbook.model_calls = operation.used

        request, problem = _as_tool_request(raw)
        if request is not None:
            asked_for_tools = True
            results = [
                _read(conn, plan_id, runbook, call)
                if len(runbook.tools) < MAX_TOOL_CALLS
                else f"【{call.name} 这次没读】这一轮的读资料额度"
                     f"（{MAX_TOOL_CALLS} 次）已经用完了。"
                for call in request.tool_calls
            ]
            rounds.append(
                [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": _results_text(attempt, runbook, results)},
                ]
            )
            continue

        asked_for_tools = False
        if problem is None:
            reply, payload, problem = check_final(raw)
            if problem is None:
                runbook.status = STATUS_OK
                runbook.stop_reason = (
                    f"答完了（调模型 {runbook.model_calls} 次"
                    f"、读资料 {runbook.tool_calls} 次）"
                )
                if reply is None:  # 理论上到不了：验收通过却没有拿到人话
                    raise AgentStop("内部状态异常：验收通过却没有拿到人话", runbook)
                return Outcome(reply=reply, suggestion=payload, runbook=runbook)

        # 不合格（含 tool_calls 写成别的样子这种形状错）：先看散文兜底，再带原因重说一次。
        # 兜底**只在已经重试过一次之后**生效：第一次仍严格判不合格（严格才是常态，
        # 宽松只是接住它偶尔 slips 的那一下）。这次重说**也计入**上面那道模型调用上限。
        if retried and prose_fallback is not None:
            prose = prose_fallback(raw)
            if prose:
                runbook.status = STATUS_OK
                runbook.stop_reason = (
                    f"答完了（调模型 {runbook.model_calls} 次、读资料 {runbook.tool_calls} 次；"
                    "它没套信封，这一轮用了散文兜底、没提建议）"
                )
                return Outcome(reply=prose, suggestion=None, runbook=runbook)

        rounds.append(
            [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": _retry_hint(problem)},
            ]
        )
        retried = True

    if asked_for_tools:
        runbook.status = STATUS_LIMIT
        runbook.stop_reason = _limit_reason(runbook)
        raise AgentStop(
            f"这一轮按上限中止（{_limit_reason(runbook)}）。你这句话还在对话里，"
            "再说一句就接着聊——**没有替你猜一个答案**。",
            runbook,
        )

    runbook.status = STATUS_FAILED
    runbook.stop_reason = f"模型连着 {runbook.model_calls} 次都没给出合格的输出（{problem}）"
    raise AgentStop(
        f"{runbook.stop_reason}；已按上限中止，**没有落任何提案**——你那句话还在对话里，"
        "再说一句就接着聊",
        runbook,
    )


# ---------- 上下文的两段固定内容 ----------

def _opening(attempt: int, tool_calls_used: int, notice: str | None = None) -> str:
    """工具目录 + 剩余额度（+ 有变化时那一行记忆提醒）。每一轮重发，额度是这一轮当下还剩多少。

    `notice` 放在这一段之后：它是「系统刚看见的变化」，不属于它可以选的资料目录，也不是额度。
    """
    head = (
        f"{agent_tools.catalog_text()}\n"
        f"【额度】这一轮你还能调模型 {MAX_MODEL_CALLS - attempt} 次、"
        f"读资料 {MAX_TOOL_CALLS - tool_calls_used} 次；能一次读完的别分几轮读。"
        "读不出结论就直说还缺什么，不要猜。"
    )
    return f"{head}\n{notice}" if notice else head


def _results_text(attempt: int, runbook: Runbook, results: list[str]) -> str:
    """把这一轮读来的资料回填给模型。数量与额度都如实说，免得它以为还有无限次。"""
    body = "\n\n".join(results) if results else "（这次什么都没读到）"
    return (
        f"【你读到的资料】\n{body}\n\n"
        f"（还剩 {MAX_MODEL_CALLS - attempt - 1} 次模型调用；"
        f"读资料额度还剩 {MAX_TOOL_CALLS - runbook.tool_calls} 次）"
    )


def _retry_hint(problem: str) -> str:
    """输出不合格时的重说提示——**必须带上原因**，否则它只会再错一遍。"""
    return (
        f"你上面的输出不合格：{problem}。"
        "请按上面说的形状再输出一个合格的 JSON 对象，不要任何解释。"
    )


# ---------- 执行一次工具调用 ----------

def _read(conn: sqlite3.Connection, plan_id: int, runbook: Runbook, call: ToolCall) -> str:
    """读一样资料并记账。工具层的错不中断这一轮：原文回给模型，让它自己换个问法。"""
    started = time.perf_counter()
    try:
        outcome = agent_tools.execute(conn, plan_id, call.name, call.args)
    except agent_tools.ToolError as error:
        runbook.tool_calls += 1
        runbook.tools.append(
            ToolUse(
                name=str(call.name),
                args=agent_tools.summarize_call(call.name, call.args),
                ok=False,
                summary=str(error),
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        )
        return f"【{call.name} 没读成】{error}。改一下再来，或者先用已经读到的回答。"

    runbook.tool_calls += 1
    runbook.tools.append(
        ToolUse(
            name=str(call.name),
            args=agent_tools.summarize_call(call.name, call.args),
            ok=True,
            summary=outcome.summary,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    )
    return outcome.text


def _as_tool_request(raw: str) -> tuple[ToolRequest | None, str | None]:
    """这段输出是「要读资料」还是「最终回话」？

    判据只有一个：**有没有一个非空的 `tool_calls` 数组**。有就是读资料（那一轮里别的东西
    一概不看——它一边说「我要读 X」一边给人话，我们以读为准，读完它自然会再说一次）；
    没有（键不在、或者给的是空数组）就当最终回话，交给调用方的验收器去判形状。
    """
    data = advisor.extract_json(raw) if str(raw or "").strip() else None
    if data is None or "tool_calls" not in data:
        return None, None
    if isinstance(data["tool_calls"], list) and not data["tool_calls"]:
        return None, None  # 空数组不是「要读」，那是「不用读」——按最终回话去验
    try:
        return ToolRequest.model_validate(data), None
    except ValidationError as error:
        return None, f"tool_calls 写得不对（{_details(error)}）"


def _details(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or '(根)'}: {item['msg']}"
        for item in error.errors()
    )


# ---------- 上下文总闸：工具结果从最旧的往下丢 ----------

def _kept_rounds(rounds: list[list[dict[str, str]]]) -> list[dict[str, str]]:
    """把这一轮读来的资料按 `TOOL_TOTAL_CHAR_LIMIT` **从最旧的那一轮**往下收。

    为什么要有这道闸：单次资料各自有上限，但连着读六样就是六份上限。真正要保的是
    「当前问题 + 最新读到的事实」，更早的读取结果价值最低——丢了并留一句实话，
    比悄悄塞满上下文好。最新那一轮无论如何都留着（它就是刚读来的）。
    """
    kept: list[dict[str, str]] = []
    used = 0
    for group in reversed(rounds):
        size = sum(len(item["content"]) for item in group)
        if kept and used + size > TOOL_TOTAL_CHAR_LIMIT:
            kept.insert(
                0,
                {
                    "role": "user",
                    "content": "（更早的一次读取结果太长，已经省略——需要就重新读一次。）",
                },
            )
            break
        kept = [*group, *kept]
        used += size
    return kept


def _limit_reason(runbook: Runbook) -> str:
    """撞上限时如实说「读了什么、还缺什么」——这是唯一诚实的收场。"""
    read_names = "、".join(runbook.tool_names) or "（一样都没读成）"
    missing = [name for name in agent_tools.TOOLS if name not in runbook.tool_names]
    reason = f"读过：{read_names}"
    if missing:
        reason += f"；还没读：{'、'.join(missing)}"
    return reason