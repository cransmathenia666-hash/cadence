"""计划级对话：蓝图落地之后，接着聊这个计划（SPEC 决策 37、41）。

为什么单独一个模块、单独一张表：这和 `blueprint.py` 里那段「定方向」的对话不是一回事——
那段绑在候选上、有 6 轮上限、终点是出一棵蓝图；这一段跟着计划走、**不限轮数**，
聊的是执行期的事（卡在哪、下一步先做哪个、要不要调整节奏），蓝图建成之后才真正开始用它。
两者生命周期与上限都不同，塞进一张表只会让每条查询先判断「这是哪种对话」。

**资料是它自己读的**（2026-09-20 起，SPEC 决策 40）：原先每一轮都把整棵计划树、最近
5 份报告、全部档案、这条方向的来历与蓝图拼成一大块发给模型——不管这一句问的是什么。
现在换成**受控工具循环**（`agent_runtime.py` + `agent_tools.py`）：它自己说要读什么，
系统去取，取来的才算它这一轮的依据。四个只读工具：当前计划、最近报告、长期档案、
计划来历；计划编号由系统注入，它读不到别的计划。

三条纪律：
- LLM 只产出**提案**：这一段对话里的「我的状态变了」要落成 `profile_change` 提案，
  写档案必须经你裁定（SPEC 第 8 节铁律）。对话本身不碰计划一个字。
- **每轮还能附一条可执行建议**（2026-09-18 T31 起，SPEC 决策 39）：改一个已有节点、
  或加一件任务 / 一个阶段。建议落成 `kind=plan_change` 的待裁定提案，你当场点「确认」
  才走写入口（`app/plan_change.py` 管这一类）。它自己**一个字段也写不动**。
- 调用卡在 `llm.Operation` 上：**每轮最多 3 次模型调用**（决策 40 起）——工具轮与
  「输出不合格带原因重说一次」共用这个额度，结构错误也计入上限；**最多 6 次只读工具调用**。
- 成本闸不是轮数而是**历史字符上限**（决策 6/37 的修订）：长期窗口不能用「聊六次就锁死」
  去卡，真正要防的是「代码写出死循环」——而这里每一次都由你手点一句才动一次，没有循环风险。

**输出是信封**（T31 起）：`{"reply": "人话", "suggestion": null 或一条建议}`；要读资料时
换成 `{"tool_calls": [{"name": ..., "args": {}}]}`。库里仍只存 `reply` 那一段人话——
历史拼回上下文与 6000 字符截断的口径一行不改。

**每轮留一行运行账**（`agent_run`，决策 40）：调了几次模型、读了哪几样、为什么停下。
它不是记忆（下一轮不读它），是给人排错与对账用的；界面上的「本轮依据」就是它。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from . import (
    agent_runtime,
    advisor,
    blueprint as blueprint_mod,
    ledger,
    llm,
    plan_change,
    profile,
)
from .db import now_iso

TASK_DIALOGUE = "plan_dialogue"
TASK_EXTRACT = "dialogue_extract"

# 落进 proposal.kind：用它已有的那一个（决策 6 里「档案变更」这一类的形状一直空着，
# T28 的计划对话成了它的第一个生产者）。
PROFILE_CHANGE_KIND = "profile_change"

# 历史累计字符上限：比候选对话（4000）宽一些，因为执行期要说的事实更多。超出从最早截断。
# 这笔账说的是**对话历史**；这一轮读来的资料另有一道闸（`agent_runtime.TOOL_TOTAL_CHAR_LIMIT`）。
DIALOGUE_CHAR_LIMIT = 6000

# 提炼档案提案时最多取几条。定成常量便于按实测调。
MAX_EXTRACTED_ITEMS = 3


class DialogueError(RuntimeError):
    """对话链路上的明确错误：模型输出不合格、没东西可提炼。"""


class DialogueNotFound(DialogueError):
    """计划不存在 → 接口层翻成 404。"""


class DialogueConflict(DialogueError):
    """与现状冲突：还没聊过就要提炼 → 接口层翻成 409。"""


# ---------- 读与写这张表 ----------

def plan_of(conn: sqlite3.Connection, plan_id: int) -> sqlite3.Row:
    """这段对话跟着哪个计划。

    **不按状态拦**：讨论不该被计划状态挡住——暂停了正是最需要商量的时刻，作废的
    也从「历史计划」进得来（回顾「当时为什么没做下去」是正当需求）。这与候选对话那条
    要求「计划进行中」不同，因为那条会往计划里建树，而这里只说话。
    """
    row = conn.execute("SELECT * FROM plan WHERE id = ?", (plan_id,)).fetchone()
    if row is None:
        raise DialogueNotFound(f"计划 id={plan_id} 不存在")
    return row


def messages_of(conn: sqlite3.Connection, plan_id: int) -> list[sqlite3.Row]:
    """这段对话的全部消息。`id` 也要取——建议与提案就是靠它关联的（决策 39）。"""
    return list(
        conn.execute(
            "SELECT id, role, content, created_at FROM plan_dialogue"
            " WHERE plan_id = ? ORDER BY id",
            (plan_id,),
        ).fetchall()
    )


def turns_used(conn: sqlite3.Connection, plan_id: int) -> int:
    """已经聊了几句 = 你发过几句话（不限轮数，这个数只用来显示）。"""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM plan_dialogue WHERE plan_id = ? AND role = 'user'",
        (plan_id,),
    ).fetchone()
    return int(row["n"])


def _record(conn: sqlite3.Connection, plan_id: int, role: str, content: str) -> int:
    """追加一句，返回它的行号。这张表是追加式日志、不经台账（同 learning_request 的先例）。

    返回行号是因为 `plan_change` 提案要记着「这条建议是从哪一句里冒出来的」
    （payload 里的 `dialogue_id`）——界面据此把确认条挂在那条消息下面。
    """
    cursor = conn.execute(
        "INSERT INTO plan_dialogue (plan_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (plan_id, role, content, now_iso()),
    )
    conn.commit()
    return int(cursor.lastrowid)


def _trim(rows: list[sqlite3.Row]) -> list[dict[str, str]]:
    """历史 → messages，按 `DIALOGUE_CHAR_LIMIT` **从最早截断**。

    最新那一句永远留着：它就是这一轮要回应的东西，截掉就没有对话可言了。
    """
    kept: list[dict[str, str]] = []
    used = 0
    for row in reversed(rows):
        content = str(row["content"])
        if kept and used + len(content) > DIALOGUE_CHAR_LIMIT:
            break
        kept.insert(0, {"role": "assistant" if str(row["role"]) == "assistant" else "user",
                        "content": content})
        used += len(content)
    return kept


SYSTEM_PROMPT = (
    "你是这个学习计划的陪跑顾问，正在和计划的主人讨论它的执行情况。"
    "**你不预先拿到他的业务数据**（阶段、任务、报告、档案、这个计划的来历都在系统里，"
    "要什么自己读）——别讲放之四海皆准的话，也别凭空编事实。默认控制在 200 字以内，"
    "除非他要求展开。\n"
    "每一轮你只输出一个 JSON 对象，两种形状选一种：\n"
    "① 要读资料：" '{"tool_calls": [{"name": "read_current_plan", "args": {}}]}'
    "——一次可以要好几样，能一次读完的别分几轮读；读不出结论就直说还缺什么，不要猜。\n"
    "② 直接回话：" '{"reply": "你要说的话", "suggestion": null 或一条建议}'
    "——不要解释、不要客套、不要 Markdown 代码块。\n"
    "- reply：直接说人话，可以用短段落或列表，不要客套开场。\n"
    "- suggestion：**一轮最多一条**，只在「改哪里、改成什么、为什么」都说得具体时才提；"
    "拿不准就在 reply 里先问一句、suggestion 给 null。只给点了名的节点编号——"
    "编号得是你从读到的资料里看到的 #号，不要自己编：\n"
    '   改节点 {"action": "update_node", "node_id": 12, "fields": {"due_date": "2026-10-08"}, '
    '"why": "为什么该这么改"}——fields 里只能出现 title / deliverable / due_date，只写要改的那几样；'
    "due_date 给空字符串表示清掉它。\n"
    '   加任务 {"action": "add_task", "node_id": 8, "tasks": [{"title": "任务名", '
    '"due_date": "2026-10-08"}, {"title": "第二件"}], "why": "..."}——node_id 给**阶段**的编号；'
    "tasks 里可以一次给几件（**最多 5 件**），他要是说「排一下」「列几条」就把该排的一次排完，"
    "别一件一件挤牙膏；due_date 拿不准就别给这个字段。\n"
    '   加阶段 {"action": "add_stage", "stage": {"title": "阶段名", '
    '"deliverable": "这个阶段结束时能看见、能验收的东西", "why": "为什么先做它"}, '
    '"tasks": [{"title": "第一件"}, {"title": "第二件"}], "why": "..."}'
    "——新阶段排在最后，必须带「要交的东西」；这个阶段拆出来的任务**一并放在 tasks 里**"
    "（最多 5 件），不要建完空阶段再一件一件补。\n"
    "- 你不能：删节点、替他打勾 / 跳过 / 交交付物、碰别的计划。"
    "「这块不做了」在计划里是用打勾 / 跳过 / 收尾表达的，不是你该提的建议。\n"
    "- 一次给一批不等于可以大改：只排你真说得清的那几件，剩下一律别凑数。\n"
    "- 建议只是建议：他点「确认」才会真改，所以别在 reply 里吹你已经改完了。"
)

EXTRACT_SYSTEM_PROMPT = (
    "你从一段对话里提炼「需要更新长期档案」的条目。你只输出一个 JSON 对象："
    "不要解释、不要客套、不要 Markdown 代码块。"
)


# ---------- 看 / 聊 ----------

class Reply(BaseModel):
    """这一轮的回话：人话（存进库里）+ **最多一条**可执行建议（决策 39）。

    `suggestion` 是**单个对象或 null**、不是数组：「一轮最多一条」从形状上就成立，
    不用靠字数限制去数。
    """

    reply: str = Field(min_length=1)
    suggestion: plan_change.Suggestion | None = None


def _details(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or '(根)'}: {item['msg']}"
        for item in error.errors()
    )


def _check_reply(
    conn: sqlite3.Connection, plan_id: int, text: str
) -> tuple[str | None, dict[str, Any] | None, str | None]:
    """验收这一轮：给出（人话，要落库的建议 payload 或 None，不合格原因或 None）。

    三件事一起判——输出是不是信封、人话有没有、建议（如果有）站不站得住。建议里
    「点名的节点不存在 / 不属于这个计划 / 改前＝改后 / 名字撞车」这类**批不了**的毛病
    也在这里拦下：宁可不提，也不落一条等你点了「确认」才报错的提案（决策 39）。
    """
    data = advisor.extract_json(text)
    if data is None:
        return None, None, "输出不是合法的 JSON 对象"

    try:
        reply = Reply.model_validate(data)
    except ValidationError as error:
        return None, None, f"字段不合格（{_details(error)}）"

    said = reply.reply.strip()
    if not said:
        return None, None, "reply 是空的——这一轮等于一个字都没说"
    if reply.suggestion is None:
        return said, None, None  # 没有建议就是纯聊天，什么都不落

    payload, problem = plan_change.check(conn, plan_id, reply.suggestion)
    if problem is not None:
        return None, None, f"建议不合格（{problem}）"
    return said, payload, None


def _suggestions(conn: sqlite3.Connection, plan_id: int) -> dict[int, dict[str, Any]]:
    """这段对话里冒出来过的建议：对话行号 → {proposal_id, summary, status}。

    关联靠提案 payload 里的 `dialogue_id`（**不加列、不动表结构**）：一条建议落一条
    `plan_change` 提案，提案里记着它是从对话的哪一行冒出来的。已裁定的也照样带回来——
    界面就不给按钮了，那一条「已确认 / 已忽略」还看得见。
    """
    rows = conn.execute(
        "SELECT * FROM proposal WHERE kind = ? ORDER BY id", (plan_change.KIND,)
    ).fetchall()
    landed: dict[int, dict[str, Any]] = {}
    for row in rows:
        payload = blueprint_mod.payload_of(row)
        if int(payload.get("plan_id") or 0) != int(plan_id):
            continue
        dialogue_id = payload.get("dialogue_id")
        if dialogue_id is None:
            continue
        landed[int(dialogue_id)] = {
            "proposal_id": int(row["id"]),
            "summary": str(payload.get("summary") or ""),
            "status": str(row["status"]),
        }
    return landed


def _runs(conn: sqlite3.Connection, plan_id: int) -> dict[int, dict[str, Any]]:
    """这段对话里每一轮的运行账：对话行号 → 这一轮读了什么、为什么停下。

    挂在哪一行由 `agent_run.dialogue_id` 说：答出来了就挂在助手那条回话上，失败时挂在你
    那一句上（那一轮没有回话）。界面上的「本轮依据」就是拿它渲染的——**刷新页面也还在**，
    不用把结果存在浏览器内存里。
    """
    rows = conn.execute(
        "SELECT * FROM agent_run WHERE plan_id = ? ORDER BY id", (plan_id,)
    ).fetchall()
    landed: dict[int, dict[str, Any]] = {}
    for row in rows:
        dialogue_id = row["dialogue_id"]
        if dialogue_id is None:
            continue
        landed[int(dialogue_id)] = public_run(row)
    return landed


def public_run(row: sqlite3.Row) -> dict[str, Any]:
    """一行 `agent_run` → 给界面看的形状。

    为什么这里还要现算 `tool_names`：库里存的是明细（每条带成败），而界面那行摘要要的是
    「读成了哪几样」——读失败的那条不该算依据。POST 的回执与这里因此永远同一个形状。
    """
    tools = _tools_of(row)
    return {
        "status": str(row["status"]),
        "stop_reason": str(row["stop_reason"]),
        "model_calls": int(row["model_calls"]),
        "tool_calls": int(row["tool_calls"]),
        "tool_names": list(
            dict.fromkeys(str(tool.get("name")) for tool in tools if tool.get("ok"))
        ),
        "tools": tools,
    }


def _tools_of(row: sqlite3.Row) -> list[dict[str, Any]]:
    """把 `agent_run.tools` 那段 JSON 解回来。库里那段坏了也不该让整个页面打不开。"""
    try:
        tools = json.loads(row["tools"] or "[]")
    except (TypeError, ValueError):
        return []
    return [tool for tool in tools if isinstance(tool, dict)] if isinstance(tools, list) else []


def _record_run(
    conn: sqlite3.Connection, plan_id: int, dialogue_id: int, runbook: agent_runtime.Runbook
) -> None:
    """把这一轮的运行账落一行。**成功的、撞上限的、失败的都落**——失败不记就无据可查。"""
    conn.execute(
        "INSERT INTO agent_run"
        " (plan_id, dialogue_id, status, stop_reason, model_calls, tool_calls, tools, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            plan_id,
            dialogue_id,
            runbook.status,
            runbook.stop_reason,
            runbook.model_calls,
            runbook.tool_calls,
            json.dumps([tool.as_dict() for tool in runbook.tools], ensure_ascii=False),
            now_iso(),
        ),
    )
    conn.commit()


def view(conn: sqlite3.Connection, plan_id: int) -> dict[str, Any]:
    """看这段对话：计划、历史、聊了几句、能不能提炼档案提案。

    每条助手消息另带两个可选字段：`suggestion`（T31：它那一轮提的建议与那条提案，
    `{proposal_id, summary, status}` 或 `None`）与 `run`（2026-09-20 决策 40：那一轮的
    运行账——读了哪几样、调了几次模型、为什么停下；界面据此渲染「本轮依据」，
    失败的运行挂在你那一句上）。
    """
    plan_of(conn, plan_id)
    rows = messages_of(conn, plan_id)
    used = turns_used(conn, plan_id)
    landed = _suggestions(conn, plan_id)
    runs = _runs(conn, plan_id)
    return {
        "plan_id": plan_id,
        "messages": [
            {
                **dict(row),
                "suggestion": landed.get(int(row["id"])),
                "run": runs.get(int(row["id"])),
            }
            for row in rows
        ],
        "turns_used": used,
        "char_limit": DIALOGUE_CHAR_LIMIT,
        # 至少聊过一句才有东西可提炼（与「蓝图要先聊过一轮」同一条纪律）
        "can_extract": used >= 1,
    }


def say(
    conn: sqlite3.Connection,
    plan_id: int,
    message: str,
    *,
    provider_id: int | None = None,
    model: str | None = None,
    transport: llm.Transport | None = None,
) -> dict[str, Any]:
    """聊一句：记下你的话 → 跑一轮**受控工具循环** → 记下它的回话（人话）+ 落一条建议提案。

    循环的规矩在 `agent_runtime`：**最多 3 次模型调用**（工具轮与「不合格重说一次」共用）、
    **最多 6 次只读工具调用**；资料不预装，它自己读。撞上限或一直不合格就如实报错，
    **没有落任何提案**——而你那句话已经留在对话里了，再说一句就接着走。

    无论成没成，这一轮都往 `agent_run` 落一行（失败挂在你那一句上）：读了什么、为什么停下。

    助手这一侧存的仍是**人话**（信封里的 `reply`），不是 JSON：下一轮拼进上下文的是
    一段像对话的话，不是它自己吐的壳。
    """
    plan_of(conn, plan_id)
    text = str(message or "").strip()
    if not text:
        raise DialogueError("总得说点什么")

    asked_id = _record(conn, plan_id, "user", text)
    try:
        outcome = agent_runtime.run(
            conn,
            plan_id,
            task=TASK_DIALOGUE,
            system_prompt=SYSTEM_PROMPT,
            history=_trim(messages_of(conn, plan_id)),
            check_final=lambda raw: _check_reply(conn, plan_id, raw),
            provider_id=provider_id,
            model=model,
            transport=transport,
        )
    except agent_runtime.AgentStop as stop:
        # 失败的那一轮也要留账：挂在你的那一句上——这一轮没有助手回话可挂
        _record_run(conn, plan_id, asked_id, stop.runbook)
        raise DialogueError(str(stop)) from None
    except llm.LlmError as error:
        # 上游没通（没配 provider、超时、返回异常）：同样留一行失败账再往上抛
        _record_run(
            conn,
            plan_id,
            asked_id,
            agent_runtime.Runbook(
                status=agent_runtime.STATUS_FAILED,
                stop_reason=f"调用模型失败：{error}",
            ),
        )
        raise

    reply_id = _record(conn, plan_id, "assistant", outcome.reply)
    suggestion = _land_suggestion(conn, plan_id, reply_id, outcome.suggestion)
    _record_run(conn, plan_id, reply_id, outcome.runbook)
    run = outcome.runbook.as_dict()
    return {
        "plan_id": plan_id,
        "reply": outcome.reply,
        "suggestion": suggestion,
        "proposal_id": None if suggestion is None else int(suggestion["proposal_id"]),
        "turns_used": turns_used(conn, plan_id),
        "calls": outcome.runbook.model_calls,
        # 2026-09-20 新增（决策 40）：这一轮读了什么、为什么停下。老字段一个没动。
        "run": run,
        "tools_used": run["tool_names"],
        "stop_reason": run["stop_reason"],
    }


def _land_suggestion(
    conn: sqlite3.Connection, plan_id: int, dialogue_id: int, payload: dict[str, Any] | None
) -> dict[str, Any] | None:
    """把验收过的建议落成一条 `kind=plan_change` 的待裁定提案（决策 39）。

    界面上的「确认」就是裁定它：确认 → 批准（改的原地改、加的建节点），忽略 → 驳回
    （台账记「聊天里先不动」）。所以每条建议都有归宿，不会在 `/proposals` 堆着。
    """
    if payload is None:
        return None
    landed = {**payload, "dialogue_id": int(dialogue_id)}
    proposal_id = ledger.create_active(
        conn,
        "proposal",
        {
            "kind": plan_change.KIND,
            "payload": json.dumps(landed, ensure_ascii=False),
            "reason": (
                f"计划 #{plan_id} 的对话里聊出的一条改动建议：{landed.get('summary')}"
                "——等你确认"
            ),
        },
        actor="agent",
    )
    return {
        "proposal_id": proposal_id,
        "summary": str(landed.get("summary") or ""),
        "status": "pending",
    }


# ---------- 提炼档案提案 ----------

class ProfileChange(BaseModel):
    """一条要写进档案的变更建议。`why` 必填：台账要能回答「为什么这么记」。"""

    category: str = Field(min_length=1)
    content: str = Field(min_length=1)
    why: str = Field(min_length=1)


class Extraction(BaseModel):
    """提炼结果。**允许 0 条**——这段对话里没有值得改档案的变化才是常见情形。"""

    items: list[ProfileChange] = Field(default_factory=list, max_length=MAX_EXTRACTED_ITEMS)


def _check_extraction(text: str) -> tuple[Extraction | None, str | None]:
    data = advisor.extract_json(text)
    if data is None:
        return None, "输出不是合法的 JSON 对象"

    try:
        extraction = Extraction.model_validate(data)
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or '(根)'}: {item['msg']}"
            for item in error.errors()
        )
        return None, f"字段不合格（{details}）"

    unknown = sorted({item.category for item in extraction.items if item.category not in profile.PROFILE_TOKENS})
    if unknown:
        return None, (
            f"类别 {unknown} 不在约定的五个令牌里"
            f"（{' / '.join(profile.PROFILE_TOKENS)}）——档案只有这五类"
        )
    return extraction, None


def propose_profile_changes(
    conn: sqlite3.Connection,
    plan_id: int,
    *,
    provider_id: int | None = None,
    model: str | None = None,
    transport: llm.Transport | None = None,
) -> dict[str, Any]:
    """把这段对话里聊出的变化提炼成待裁定的**档案变更**提案。

    刻意做成一个**你点按钮才发生**的动作，而不是每轮自动附带：闲聊不该往
    `/proposals` 里撒提案。1 次调用 + 不合格带原因重试 1 次（结构化输出按老规矩重试）。
    """
    plan_of(conn, plan_id)
    rows = messages_of(conn, plan_id)
    if turns_used(conn, plan_id) < 1:
        raise DialogueConflict("这段对话还没聊过，没有东西可提炼")

    messages = [
        {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
        {"role": "user", "content": _extract_brief(conn, plan_id)},
        *_trim(rows),
        {
            "role": "user",
            "content": (
                "【现在请提炼】从上面这段对话里找出「需要更新长期档案」的条目，"
                "只输出一个 JSON 对象："
                '{"items": [{"category": "当前状态", "content": "一两句提炼结论", "why": "为什么该这么记"}]}。'
                f"- category 只能是这五个之一：{' / '.join(profile.PROFILE_TOKENS)}。"
                f"- 最多 {MAX_EXTRACTED_ITEMS} 条。"
                "- 只提炼对话里**真实出现过的变化**（时间变了、状态变了、目标变了）；"
                '没有就回 {"items": []}——空着是正常的，不许硬凑。'
                "- content 是提炼结论，不是把原话抄一遍。只输出 JSON，不要解释。"
            ),
        },
    ]
    operation = llm.Operation(conn, TASK_EXTRACT, limit=2, transport=transport)
    raw = operation.chat(messages, provider_id=provider_id, model=model)
    extraction, problem = _check_extraction(raw)

    attempts = 1
    if problem is not None:
        attempts = 2
        messages = [
            *messages,
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": f"你上面的输出不合格：{problem}。请只输出合格的 JSON 对象，不要任何解释。",
            },
        ]
        raw = operation.chat(messages, provider_id=provider_id, model=model)
        extraction, problem = _check_extraction(raw)
        if problem is not None:
            raise DialogueError(
                f"模型连着 {attempts} 次都没给出合格的提炼结果（{problem}）；"
                "已按上限中止，**没有落任何提案**"
            )
    if extraction is None:  # 理论上到不了
        raise DialogueError("内部状态异常：验收通过却没有解析出提炼结果")

    created: list[dict[str, Any]] = []
    for item in extraction.items:
        proposal_id = ledger.create_active(
            conn,
            "proposal",
            {
                "kind": PROFILE_CHANGE_KIND,
                "payload": json.dumps(
                    {
                        "category": item.category,
                        "content": item.content,
                        "why": item.why,
                        "plan_id": plan_id,
                    },
                    ensure_ascii=False,
                ),
                "reason": f"计划 #{plan_id} 的对话里聊出的档案变更：{item.why}",
            },
            actor="agent",
        )
        created.append(
            {"proposal_id": proposal_id, "category": item.category, "content": item.content}
        )

    return {
        "plan_id": plan_id,
        "items": created,
        "attempts": attempts,
        "calls": operation.used,
    }


def _extract_brief(conn: sqlite3.Connection, plan_id: int) -> str:
    """提炼那一下的背景：现档案 + 计划目标。

    为什么要给现档案：不给他就分不清「这是新情况」还是「档案里早写着」，
    会提炼出一堆同义重复——而档案的写入那道判重闸只挡一字不差的。
    """
    read = advisor.read_profile(conn)
    goal = str(plan_of(conn, plan_id)["goal"])
    lines = [f"【计划目标】{goal}", "", "【我的长期档案（当前有效值）】"]
    lines += [
        f"#{item['id']} [{item['category']}] {item['content']}" for item in read["items"]
    ] or ["（一条都没有）"]
    return "\n".join(lines)