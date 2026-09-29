"""对话式规划：采纳一条候选之后，先把意向聊清楚，再出一棵「蓝图」等它裁定。

为什么要有这一段（SPEC 决策 36）：候选只说了「学什么方向」，而一棵能执行的树要回答
「先做什么、交什么、分几步」——这几个答案在你脑子里，不聊就问不出来。所以在「采纳」
与「建树」之间插一段对话，而不是让模型对着一条标题静默生成。

三条纪律，与项目其它链路一致：
- LLM 只产出**提案**：蓝图落成 `pending` 提案，建树必须经你裁定（可勾选部分采纳）。
- 调用次数卡在 `llm.Operation` 上：对话**每轮 1 次**（决策 6 修订，不重试），
  蓝图生成 1 次 + 不合格按老规矩带原因重试 1 次。
- 生成前**至少聊成一轮**（III-01：模型回话失败那轮只留下你的话，不算数），且**最后一轮
  必须是它答的那句**（上一轮还没聊成不许出方案）：不允许「刚采纳就静默出树」，那正是
  决策 36 要避免的形态。
- 生成时就拦下**明知批不了**的稿子（III-02）：蓝图自身的形状毛病在 `_check_blueprint`；
  与现有计划的冲突（任务撞上同名开着的任务）在 `_plan_conflict_problem`——都带原因重试
  一次，两次不合格一条提案不落。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from . import advisor, ledger, llm, plan
from .db import now_iso

# 落进 llm_call.task / proposal.kind 的名字
TASK_CHAT = "plan_chat"
TASK_BLUEPRINT = "plan_blueprint"
BLUEPRINT_KIND = "plan_blueprint"


class BlueprintError(RuntimeError):
    """规划链路上的明确错误：模型输出不合格、蓝图还没到该出的时机。"""


class BlueprintNotFound(BlueprintError):
    """候选 / 计划不存在 → 接口层翻成 404。"""


class BlueprintConflict(BlueprintError):
    """与现状冲突：候选还没采纳、计划定不下来 / 已收尾、对话轮数到顶 → 接口层翻成 409。"""


# ---------- 对话（SPEC 决策 36 的「对话规格」） ----------

# 整段对话的轮数上限与历史字符上限。轮数按**用户发的话**数（一问一答算一轮）；
# 字符上限只管**历史**（背景那一段 —— 档案、候选方向、已有阶段 —— 不算在里头，
# 它是每轮都要给的事实，不是越积越多的对话）。
MAX_TURNS = 6
CHAT_CHAR_LIMIT = 4000
MAX_QUESTIONS = 3


class ChatReply(BaseModel):
    """模型这一轮的回话：最多 3 个问题，外加「我这边够了」的信号。

    `ready=True` 表示它认为意向问清、可以出方案了——那时可以不问问题。
    """

    questions: list[str] = Field(default_factory=list, max_length=MAX_QUESTIONS)
    ready: bool = False
    note: str = ""


def _accepted_candidate(conn: sqlite3.Connection, candidate_id: int) -> sqlite3.Row:
    """取一条**已采纳**的候选。对话是采纳之后才开的——没采纳就是状态冲突，不是参数错。"""
    row = conn.execute("SELECT * FROM candidate WHERE id = ?", (candidate_id,)).fetchone()
    if row is None:
        raise BlueprintNotFound(f"候选 id={candidate_id} 不存在")
    if str(row["status"]) != "accepted":
        raise BlueprintConflict(
            f"候选 id={candidate_id} 还没采纳（当前 {row['status']}）——"
            "规划对话是在「采纳」之后才开的"
        )
    return row


def resolve_plan(
    conn: sqlite3.Connection, candidate: sqlite3.Row, explicit_plan_id: int | None
) -> int:
    """这段对话属于哪个计划：**调用方说明 > 已有对话记着的 > 候选自带的采纳落点/归属**。

    中间那一档为什么必须存在：候选是「新方向」时（`learning_request.plan_id` 为空），
    落点是采纳那一刻现选的——那个事实当时只活在两个地方：当刻的响应，和 `plan_chat`
    里记下的那几行。页面刷新一次就只剩后者了。所以「聊一句」与「出方案」都得和
    `view` 一样认 `plan_chat` 的账，否则会出现「对话明明聊成了、最后一步却说没有计划
    归属」——那是同一个流程里两套判断标准，不是用户选错了。

    最后一档复用 `advisor.landing_plan`：采纳落到哪个计划（**2026-09-21 T37 起这一步
    就记在 `candidate.landing_plan_id` 上，不再依赖「你有没有先聊过一句」**）、
    这段对话记在哪个计划名下、蓝图往哪个计划里建，必须是同一个答案。
    """
    recorded = thread_plan(conn, int(candidate["id"]))
    if recorded is not None:
        if explicit_plan_id is not None and int(explicit_plan_id) != recorded:
            raise BlueprintConflict(
                f"这段对话已经记在计划 #{recorded} 名下，不能改成 #{explicit_plan_id}"
            )
        # 记着的那条也要当场核对：计划可能在这段对话之后收尾或作废了。不查的话，会在
        # 「出方案」时落一条永远批不了的蓝图提案（要等批准那一步才拦），白聊一场。
        plan_row = plan.resolve_plan(conn, recorded)
        if plan_row is None:
            raise BlueprintConflict(f"这段对话记在计划 #{recorded} 名下，但那个计划已经不在了")
        if str(plan_row["status"]) != "active":
            raise BlueprintConflict(
                f"这段对话所属的计划 #{recorded} 已不是进行中（{plan_row['status']}）——"
                "换个计划，或先把那边收个尾"
            )
        return recorded
    try:
        return advisor.landing_plan(conn, candidate, explicit_plan_id)
    except advisor.CandidateConflict as error:
        raise BlueprintConflict(str(error)) from error


def thread_plan(conn: sqlite3.Connection, candidate_id: int) -> int | None:
    """这条候选已经有对话的话，那段对话记在哪个计划名下。"""
    row = conn.execute(
        "SELECT plan_id FROM plan_chat WHERE candidate_id = ? ORDER BY id DESC LIMIT 1",
        (candidate_id,),
    ).fetchone()
    return None if row is None else int(row["plan_id"])


def thread(conn: sqlite3.Connection, candidate_id: int, plan_id: int) -> list[sqlite3.Row]:
    """一段对话的全部消息，按发生顺序。"""
    return list(
        conn.execute(
            "SELECT role, content, created_at FROM plan_chat"
            " WHERE candidate_id = ? AND plan_id = ? ORDER BY id",
            (candidate_id, plan_id),
        ).fetchall()
    )


def turns_used(conn: sqlite3.Connection, candidate_id: int, plan_id: int) -> int:
    """已经聊了几轮 = 你发过几句话。"""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM plan_chat"
        " WHERE candidate_id = ? AND plan_id = ? AND role = 'user'",
        (candidate_id, plan_id),
    ).fetchone()
    return int(row["n"])


def _ready_to_generate(rows: list[sqlite3.Row]) -> bool:
    """Only the latest successful assistant reply can authorize a blueprint."""
    if not rows or str(rows[-1]["role"]) != "assistant":
        return False
    reply, problem = _check_reply(str(rows[-1]["content"]))
    return problem is None and reply is not None and reply.ready and not reply.questions


def valid_turns_used(conn: sqlite3.Connection, candidate_id: int, plan_id: int) -> int:
    """聊**成**了几轮 = 模型成功回话的行数（III-01）。

    每条成功回话对应它前面那条用户消息——那一问一答才算把意向聊进一步。模型回话失败的
    那轮只在对话里留下你的一句话、没有 assistant 行，所以不算数：不然拿一句失败消息
    就能凑数出蓝图，等于没聊就静默生成，正是决策 36 要避免的形态。
    """
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM plan_chat"
        " WHERE candidate_id = ? AND plan_id = ? AND role = 'assistant'",
        (candidate_id, plan_id),
    ).fetchone()
    return int(row["n"])


def _record(
    conn: sqlite3.Connection, plan_id: int, candidate_id: int, role: str, content: str
) -> None:
    """追加一句。这张表是追加式日志、不经台账（同 learning_request 的先例）。"""
    conn.execute(
        "INSERT INTO plan_chat (plan_id, candidate_id, role, content, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (plan_id, candidate_id, role, content, now_iso()),
    )
    conn.commit()


def payload_of(row: sqlite3.Row) -> dict[str, Any]:
    """解 payload 那列 JSON；解不开当空对象。

    这一段与 `proposals.payload_of` 是同一件事，刻意没去调用它：`proposals.decide`
    反过来要调本模块的 `build_tree`（蓝图批准 = 建树），互相 import 会绕成环。
    两处都只有几行，且都只认「本仓库自己写进去的 JSON」。
    """
    try:
        parsed = json.loads(row["payload"])
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def pending_blueprint(
    conn: sqlite3.Connection, plan_id: int, candidate_id: int | None = None
) -> sqlite3.Row | None:
    """这个计划当前待裁定的蓝图。**同一计划同时只有一份**——树 = 版本（决策 36）。

    plan_id 藏在 payload 的 JSON 里（没有这一列），所以取回来在 Python 里筛：
    pending 的蓝图本来就只有几条，不值得为它去碰 SQLite 的 JSON 函数。

    `candidate_id` 给了就再筛候选归属（2026-09-28 整改 III-02）：待批稿只给**出它的那条
    候选**的对话看，同计划另一条候选的对话里不外显——不然那边会把它当成自己的稿子。
    None 保持计划级语义（不筛候选）：`_supersede_previous` 那条版本更替路径按「树 = 版本」
    的计划级口径走，**不许改**。
    """
    rows = conn.execute(
        "SELECT * FROM proposal WHERE kind = ? AND status = 'pending' ORDER BY id DESC",
        (BLUEPRINT_KIND,),
    ).fetchall()
    for row in rows:
        payload = payload_of(row)
        if int(payload.get("plan_id") or 0) != int(plan_id):
            continue
        if candidate_id is not None and int(payload.get("candidate_id") or 0) != int(candidate_id):
            continue
        return row
    return None


# ---------- 组 prompt ----------

SYSTEM_PROMPT = (
    "你是学习规划助手。你只输出一个 JSON 对象：不要解释、不要客套、不要 Markdown 代码块。"
)


def _require_profile(conn: sqlite3.Connection) -> dict[str, Any]:
    """规划同样要有判据——没有档案连「为什么这么排」都说不出来，先让你去补。"""
    profile = advisor.read_profile(conn)
    if not profile["items"]:
        raise BlueprintError(
            "长期档案一条都没有，规划没有判据可依——先去 /profile 补档案"
            "（至少「长期主线」与「短期目标」），再来聊"
        )
    return profile


def _background(conn: sqlite3.Connection, candidate: sqlite3.Row, plan_id: int) -> str:
    """每轮都要给的事实：档案 + 这条方向 + 计划现在长什么样。

    它不是「越积越多的对话」，所以不算进 `CHAT_CHAR_LIMIT`——那是留给历史的。
    """
    profile = _require_profile(conn)
    plan_row = plan.resolve_plan(conn, plan_id)
    stages = plan.get_stages(conn, plan_id)
    lines = [
        "我在按你的建议做规划。请先和我把意向聊清楚，再出一棵可执行的树。",
        "",
        "【这条方向】上一轮我采纳的候选：",
        f"- 标题：{candidate['title']}",
        f"- 当时的理由：{candidate['why']}",
        f"- 建议深度：{candidate['depth_target']}",
    ]
    steps = advisor.candidate_steps(candidate)
    if steps:
        # T34（决策 41）：`path` 形状的候选自己带着「这一条路上的先后几步」。它是**底稿**
        # 不是成品——规划对话要做的是在它上面增减，而不是从零再问一遍。
        lines += [
            "",
            "【这条路它给的先后步骤（草案，不是成品）】",
        ]
        for index, step in enumerate(steps, start=1):
            head = f"{index}. {str(step.get('title') or '').strip()}"
            deliverable = str(step.get("deliverable") or "").strip()
            why = str(step.get("why") or "").strip()
            lines.append(head)
            if deliverable:
                lines.append(f"   要交的东西：{deliverable}")
            if why:
                lines.append(f"   为什么排在这个位置：{why}")
        lines += [
            "**以这份草案为底稿**：哪儿要合并、哪儿要补一步、哪儿这轮先不做，你直接说；"
            "别把它当作没看见、从零再问我一遍。",
        ]
    lines += [
        "",
        "【我的长期档案】方括号里是类别（判断要对着它说，别讲放之四海皆准的话）：",
    ]
    lines += [f"#{item['id']} [{item['category']}] {item['content']}" for item in profile["items"]]
    if profile["missing_categories"]:
        names = "、".join(advisor.PROFILE_CATEGORIES[name] for name in profile["missing_categories"])
        lines += ["", f"【注意】这几类档案目前是空的：{names}。要靠它们才能定的，问我就行。"]

    lines += ["", "【这个计划现在的样子】"]
    lines.append(f"- 目标：{plan_row['goal']}")
    if stages:
        lines.append("- 已有的阶段（按顺序，**不要重复建**）：")
        for stage in stages:
            deliverable = stage["deliverable"] or "（还没定）"
            lines.append(f"  · #{stage['id']} {stage['title']}｜要交的东西：{deliverable}")
        lines.append(
            "  注意：采纳这条候选时已经自动建了同名阶段——它就是这条方向的落脚点，"
            "要给它排任务就照抄它的标题，别再建一个同名的。"
        )
    else:
        lines.append("- 还没有阶段。")

    lines += [
        "",
        "【怎么聊】",
        f"- 每轮最多问 {MAX_QUESTIONS} 个问题，问**非知道不可**的（能投入多少时间、先做哪块、"
        "想交出什么）；档案里已经写着的别再问一遍。",
        "- 觉得意向够了就把 `ready` 设成 true，并在 `note` 里说一句你打算怎么排。",
        "- 只输出一个 JSON 对象，形状："
        '{"questions": ["问题一", "问题二"], "ready": false, "note": "一句话"}。',
    ]
    return "\n".join(lines)


def render_reply(content: str) -> str:
    """把助手那一侧的 JSON 原文渲染成人话，再喂回下一轮。

    库里存 JSON（界面要按结构显示），但没必要让模型下一轮去读自己的 JSON——
    摊成几行更像一次真实对话，也更省 token。

    公开（去掉前导下划线）是因为计划级对话（`dialogue.py`）要**把这段定方向的历史
    也喂进它的上下文**：同一种渲染不该两处各写一遍。
    """
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        return content
    if not isinstance(data, dict):
        return content
    lines = [f"{index}. {text}" for index, text in enumerate(data.get("questions") or [], start=1)]
    if data.get("note"):
        lines.append(str(data["note"]))
    if data.get("ready"):
        lines.append("（我这边信息够了，可以出方案。）")
    return "\n".join(lines) or content


def _history_messages(rows: list[sqlite3.Row]) -> list[dict[str, str]]:
    """对话历史 → messages，并按 `CHAT_CHAR_LIMIT` **从最早截断**（决策 36）。

    最新那一句永远留着：它是这一轮要回答的东西，截掉就没有提问可言了。
    """
    kept: list[dict[str, str]] = []
    used = 0
    for row in reversed(rows):
        assistant = str(row["role"]) == "assistant"
        content = render_reply(str(row["content"])) if assistant else str(row["content"])
        if kept and used + len(content) > CHAT_CHAR_LIMIT:
            break
        kept.insert(0, {"role": "assistant" if assistant else "user", "content": content})
        used += len(content)
    return kept


def _details(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or '(根)'}: {item['msg']}"
        for item in error.errors()
    )


def _check_reply(text: str) -> tuple[ChatReply | None, str | None]:
    """验收这一轮的回话。刻意不抛异常——不合格的原因要能讲清楚给你看。"""
    data = advisor.extract_json(text)
    if data is None:
        return None, "输出不是合法的 JSON 对象"

    try:
        reply = ChatReply.model_validate(data)
    except ValidationError as error:
        return None, f"字段不合格（{_details(error)}）"

    questions = [item.strip() for item in reply.questions]
    if any(not item for item in questions):
        return None, "questions 里有空问题——要么问一句完整的话，要么别把它放进数组"
    if not reply.ready and not questions:
        return None, "既没把 ready 设成 true、又一个问题都没问：这一轮等于什么都没说"
    return reply.model_copy(update={"questions": questions}), None


# ---------- 对话链路（主入口） ----------

def view(conn: sqlite3.Connection, candidate_id: int, plan_id: int | None = None) -> dict[str, Any]:
    """看这段对话：历史消息 + 聊了几轮 + 能不能出方案 + 有没有**本候选的**待裁定蓝图。

    计划归属与 say/generate 走**同一道闸**（III-02）：调用方说了 plan_id 就过
    `resolve_plan` 核对——与这段对话已记的归属/落点对不上就当场报冲突，不悄悄换成
    调用方说的那个，不然待批蓝图会错挂到同计划另一条候选的对话里展示。都定不下来
    （「新方向」的候选、又还没聊过）就先返回空的，让界面提示你先指明落在哪个计划。
    """
    row = _accepted_candidate(conn, candidate_id)
    if plan_id is not None:
        target = resolve_plan(conn, row, plan_id)
    else:
        target = thread_plan(conn, candidate_id)
        if target is None:
            try:
                target = resolve_plan(conn, row, None)
            except BlueprintConflict:
                target = None

    used = 0 if target is None else turns_used(conn, candidate_id, target)
    valid = 0 if target is None else valid_turns_used(conn, candidate_id, target)
    rows = [] if target is None else thread(conn, candidate_id, target)
    # III-02：查待批稿带上**本候选**的归属——本候选没有待批稿就返回 None，
    # 同计划另一条候选的那份不外显（计划级「同时只有一份」的版本语义不变）
    pending = None if target is None else pending_blueprint(conn, target, candidate_id)
    return {
        "candidate_id": candidate_id,
        "plan_id": target,
        "messages": [dict(item) for item in rows],
        "turns_used": used,
        "valid_turns_used": valid,
        "max_turns": MAX_TURNS,
        # 生成前至少要**聊成**一轮（III-01）：模型回话失败的那轮不算数（决策 36：不是一次性静默生成）
        "can_generate": valid >= 1 and _ready_to_generate(rows),
        # T34：这条候选自带的步骤草案（只有 path 形状的候选有）。对话区顶部把它列出来，
        # 让你看得见「它在照哪份底稿聊」；刷新页面也还在（从 payload 解，不靠当刻响应）。
        "steps": advisor.candidate_steps(row),
        "blueprint": None if pending is None else {
            "id": int(pending["id"]),
            "created_at": pending["created_at"],
            # III-02：待批蓝图是谁家的也从 payload 解出来——前端据此核对没挂错候选
            "candidate_id": payload_of(pending).get("candidate_id"),
        },
    }


def say(
    conn: sqlite3.Connection,
    candidate_id: int,
    message: str,
    *,
    plan_id: int | None = None,
    provider_id: int | None = None,
    model: str | None = None,
    transport: llm.Transport | None = None,
) -> dict[str, Any]:
    """聊一轮：记下你的话 → 调一次模型 → 记下它的回话。

    **每轮 1 次调用、不重试**（决策 6 修订的「每轮 1 次调用，整段上限 6 轮」）。
    所以输出不合格时如实报错，而你那句话**已经留在对话里**了——再说一句就接着走，
    不用把说过的话重打一遍。代价是这一轮算用掉了。
    """
    row = _accepted_candidate(conn, candidate_id)
    target = resolve_plan(conn, row, plan_id)
    _require_profile(conn)  # 先验档案：不合格就别把你的话记进去
    used = turns_used(conn, candidate_id, target)
    if used >= MAX_TURNS:
        raise BlueprintConflict(
            f"这段对话已经聊满 {MAX_TURNS} 轮（决策 36 的上限）——"
            "说「够了，出方案」吧，把它落成蓝图"
        )
    text = str(message or "").strip()
    if not text:
        raise BlueprintError("总得说点什么——这一轮的后半句是模型的问题，前半句是你的回答")

    _record(conn, target, candidate_id, "user", text)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _background(conn, row, target)},
        *_history_messages(thread(conn, candidate_id, target)),
    ]
    operation = llm.Operation(conn, TASK_CHAT, limit=1, transport=transport)
    raw = operation.chat(messages, provider_id=provider_id, model=model)
    reply, problem = _check_reply(raw)
    if problem is not None:
        raise BlueprintError(
            f"模型这一轮没给出合格的回话（{problem}）；那一轮只给 1 次调用（决策 6 修订），"
            "所以没有重试——你那句话还在对话里，再说一句就接着聊"
        )
    if reply is None:  # 理论上到不了
        raise BlueprintError("内部状态异常：验收通过却没有解析出回话")

    _record(conn, target, candidate_id, "assistant", raw)
    return {
        "candidate_id": candidate_id,
        "plan_id": target,
        "reply": reply.model_dump(),
        "turns_used": used + 1,
        "max_turns": MAX_TURNS,
        "can_generate": _ready_to_generate(thread(conn, candidate_id, target)),
        "calls": operation.used,
    }


# ---------- 蓝图：生成、版本取代、勾选建树 ----------

# 蓝图的规模上限：阶段数与每个阶段的任务数。定成常量是为了让「模型跑飞了」有个明确的
# 拦截面——一棵八阶段的树不是规划，是胡写。
MAX_STAGES = 6
MAX_TASKS_PER_STAGE = 8


class BlueprintTask(BaseModel):
    """一个任务。`due_date` 可选——带了才进落后量（同决策 31）。"""

    title: str = Field(min_length=1)
    due_date: str | None = None


class BlueprintStage(BaseModel):
    """一个阶段：名字 + **可验收的交付物** + 为什么先做它 + 任务。"""

    title: str = Field(min_length=1)
    deliverable: str = Field(min_length=1)
    why: str = ""
    tasks: list[BlueprintTask] = Field(default_factory=list, max_length=MAX_TASKS_PER_STAGE)


class Blueprint(BaseModel):
    """一棵树。阶段至少一个——没有阶段的蓝图没有意义。"""

    goal: str = Field(min_length=1)
    stages: list[BlueprintStage] = Field(min_length=1, max_length=MAX_STAGES)


def _check_blueprint(text: str) -> tuple[Blueprint | None, str | None]:
    """验收模型的蓝图。

    校验前移（III-02）：标题空白、同阶段任务重名这类毛病，原先要等批准按钮那一步才炸——
    落一条**明知批不了**的提案只会让你白点一次。所以生成时就拦下，不合格带原因重试 1 次。
    `due_date` 必须真是日期，否则判不合格让重试——悄悄丢掉一个日期比报错更糟：
    那等于把你以为排好的期给吞了。
    """
    data = advisor.extract_json(text)
    if data is None:
        return None, "输出不是合法的 JSON 对象"

    try:
        blueprint = Blueprint.model_validate(data)
    except ValidationError as error:
        return None, f"字段不合格（{_details(error)}）"

    # Pydantic 的 min_length=1 挡不住纯空白，目标、交付物和标题都要再过 strip
    if not blueprint.goal.strip():
        return None, "蓝图目标是空白——得写清这棵树要达成什么"
    for index, stage in enumerate(blueprint.stages, start=1):
        if not stage.deliverable.strip():
            return None, f"第 {index} 个阶段的交付物是空白——得写清要交什么"
    titles = [stage.title.strip() for stage in blueprint.stages]
    blanks = [index + 1 for index, title in enumerate(titles) if not title]
    if blanks:
        return None, (
            f"第 {'、'.join(str(item) for item in blanks)} 个阶段的标题是空白——阶段得有个名字"
        )
    repeated = sorted({title for title in titles if titles.count(title) > 1})
    if repeated:
        return None, f"阶段标题重名了：{repeated}——同一个阶段别拆成两条，改掉再来"

    for stage in blueprint.stages:
        seen: set[str] = set()  # 同一阶段内任务别重名；跨阶段同名合法（不同父节点）
        for task in stage.tasks:
            title = task.title.strip()
            if not title:
                return None, (
                    f"阶段「{stage.title.strip()}」下有任务没写标题（或全是空白）——"
                    "任务得有个名字"
                )
            if title in seen:
                return None, (
                    f"阶段「{stage.title.strip()}」下有两件同名任务：「{title}」——"
                    "同一阶段里任务别重名（不同阶段之间同名没关系），改掉再来"
                )
            seen.add(title)
            raw = str(task.due_date or "").strip()
            if not raw:
                task.due_date = None
                continue
            parsed = plan.parse_date(raw)
            if parsed is None:
                return None, (
                    f"任务「{task.title}」的 due_date「{raw}」不是日期——"
                    "要么写成 YYYY-MM-DD，要么干脆不给这个字段"
                )
            task.due_date = parsed.isoformat()
    return blueprint, None


def _plan_conflict_problem(
    conn: sqlite3.Connection, plan_id: int, blueprint: Blueprint
) -> str | None:
    """生成期与**现有计划**的冲突预检（2026-09-28 整改 III-02 的后半句）。

    `_check_blueprint` 管的是蓝图自己的形状（空白标题、同阶段重名）；这道管的是它与现状
    的冲突：给一个**同名还开着**的阶段排任务、任务名又撞上那个阶段里还开着的任务——
    批准时必然撞防重名闸（`resolve_build` 的 `_check_task_conflicts`）。落一条明知批不了
    的稿子只会让你白点一次批准，所以生成时就拦下，带原因重试一次。
    """
    for stage in blueprint.stages:
        existing = plan.find_open_duplicate(conn, plan_id, "stage", stage.title.strip())
        if existing is None:
            continue
        open_titles = set(plan.stage_completion(conn, int(existing["id"]))["open_titles"])
        clash = sorted(
            {task.title.strip() for task in stage.tasks if task.title.strip() in open_titles}
        )
        if clash:
            return (
                f"阶段「{stage.title.strip()}」里已经开着同名任务：{'、'.join(clash)}——"
                "这份蓝图批准时会撞防重名闸；先去掉这几件任务，或在计划里把它们处理掉再出"
            )
    return None


def generate_blueprint(
    conn: sqlite3.Connection,
    candidate_id: int,
    *,
    plan_id: int | None = None,
    provider_id: int | None = None,
    model: str | None = None,
    transport: llm.Transport | None = None,
) -> dict[str, Any]:
    """沿对话出一版蓝图，落成一条 `pending` 提案（决策 36）。

    **树 = 版本**：同一计划同时只有一份待裁定蓝图，新版落库时把旧的标成业务终态
    `superseded`。不走台账的取代——决策 22 明令禁止对提案做生命周期操作，理由（会造出
    「已作废却仍算 pending」的幽灵记录）在 `ledger.py` 里写着。

    生成门槛（2026-09-28 整改 III-01）有两道：**聊成的轮数至少一轮**（回话失败那轮只留下
    你的话，不算数），且**最后一轮必须是它答的那句**——上一轮还没聊成就不许出方案。
    输出验收（III-02）除了蓝图自身的形状，还会预检与现有计划的任务撞车；不合格带原因
    重试 1 次，两次都不合格就报错、一条提案不落。
    """
    row = _accepted_candidate(conn, candidate_id)
    target = resolve_plan(conn, row, plan_id)
    _require_profile(conn)
    used = turns_used(conn, candidate_id, target)
    # III-01：门槛数的是**聊成**的轮数——模型回话失败那轮只留下你的话，不算数
    if valid_turns_used(conn, candidate_id, target) < 1:
        raise BlueprintConflict(
            "先聊成一轮再出方案——只留下话、没聊成的不算；"
            "决策 36 要的是「先把意向问清楚」，不是对着一条标题静默生成一棵树"
        )
    # III-01 的后半句：聊成过不等于聊到头——最后一条消息还是**你的话**（它的回话失败、
    # 或者还没回）时，方案是对着一段没聊完的话出的；先回去把上一轮聊成再出。
    thread_rows = thread(conn, candidate_id, target)
    if str(thread_rows[-1]["role"]) != "assistant":
        raise BlueprintConflict("上一轮还没聊成，先接着聊（或让它把上一句答完）再出方案")
    if not _ready_to_generate(thread_rows):
        raise BlueprintConflict("上一轮助手还没确认信息足够、且不再追问；先答完问题再出方案")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _background(conn, row, target)},
        *_history_messages(thread(conn, candidate_id, target)),
        {"role": "user", "content": _blueprint_brief(conn, target)},
    ]
    # 1 次 + 不合格带原因重试 1 次（决策 6 修订）
    operation = llm.Operation(conn, TASK_BLUEPRINT, limit=2, transport=transport)
    raw = operation.chat(messages, provider_id=provider_id, model=model)
    blueprint, problem = _check_blueprint(raw)
    if problem is None and blueprint is not None:
        problem = _plan_conflict_problem(conn, target, blueprint)

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
        blueprint, problem = _check_blueprint(raw)
        if problem is None and blueprint is not None:
            problem = _plan_conflict_problem(conn, target, blueprint)
        if problem is not None:
            raise BlueprintError(
                f"模型连着 {attempts} 次都没给出合格的蓝图（{problem}）；"
                "已按上限中止，**没有落任何提案**——对话还在，可以再让它出一次"
            )
    if blueprint is None:  # 理论上到不了
        raise BlueprintError("内部状态异常：验收通过却没有解析出蓝图")

    version = _blueprint_version(conn, target) + 1
    payload = {
        "plan_id": target,
        "candidate_id": candidate_id,
        "goal": blueprint.goal,
        "stages": [stage.model_dump() for stage in blueprint.stages],
    }
    proposal_id = ledger.create_active(
        conn,
        "proposal",
        {
            "kind": BLUEPRINT_KIND,
            "payload": json.dumps(payload, ensure_ascii=False),
            "reason": f"计划 #{target} 的第 {version} 版蓝图（{len(blueprint.stages)} 个阶段，"
            f"聊了 {used} 轮）——等你勾选采纳",
        },
        actor="agent",
    )
    superseded = _supersede_previous(conn, target, keep_id=proposal_id)

    return {
        "proposal_id": proposal_id,
        "plan_id": target,
        "candidate_id": candidate_id,
        "version": version,
        "goal": blueprint.goal,
        "stages": payload["stages"],
        "superseded_ids": superseded,
        "attempts": attempts,
        "calls": operation.used,
    }


def _blueprint_brief(conn: sqlite3.Connection, plan_id: int) -> str:
    """出方案那一下的硬性要求。已有阶段要照抄标题——同名阶段会被复用而不是重复建。"""
    lines = [
        "【现在请出方案】把上面聊清的意向落成一棵树。只输出一个 JSON 对象，形状：",
        '{"goal": "这棵树要达成的一句话目标", "stages": [{"title": "阶段名",'
        ' "deliverable": "这个阶段结束时能看见、能验收的东西（一句话）", "why": "为什么先做它",'
        ' "tasks": [{"title": "任务名", "due_date": "2026-10-01"}]}]}',
        "",
        "- 阶段 1–" f"{MAX_STAGES} 个，按先后顺序；每个阶段 0–{MAX_TASKS_PER_STAGE} 个任务。",
        "- **已有阶段的标题照抄**（一个字都别改）——我会把任务挂到它下面，不会重复建。",
        "- 阶段标题之间不要重名；同一阶段里的任务标题也别重名（不同阶段之间同名没关系）。",
        "- 阶段与任务的标题都不能是空白。",
        "- `deliverable` 必须是一句话能验收的东西（例如「写完一个能跑通的 CRUD 接口」），"
        "不是「提高工程能力」这种没法验的。",
        "- `due_date` 只在真能给出日期时写，格式 YYYY-MM-DD；拿不准就别给这个字段。",
        "- 只输出 JSON，不要解释、不要 Markdown 代码块。",
    ]
    stages = plan.get_stages(conn, plan_id)
    if stages:
        lines.insert(4, "- 已存在的阶段标题：" + "、".join(f"「{row['title']}」" for row in stages))
    return "\n".join(lines)


def _blueprint_version(conn: sqlite3.Connection, plan_id: int) -> int:
    """这个计划已经出过几版蓝图（含被取代的）——只用来写一句能读懂的版本号。"""
    rows = conn.execute(
        "SELECT payload FROM proposal WHERE kind = ?", (BLUEPRINT_KIND,)
    ).fetchall()
    return sum(1 for row in rows if int(payload_of(row).get("plan_id") or 0) == int(plan_id))


def _supersede_previous(
    conn: sqlite3.Connection, plan_id: int, *, keep_id: int
) -> list[int]:
    """把同计划上一版还在 pending 的蓝图标成业务终态 `superseded`（树 = 版本）。

    判据与候选的 `expired` 同一路：它是**业务终态**、不是台账的生命周期操作。
    `superseded` 不进任何禁区，也不影响裁定记录——旧版还查得到、看得见。
    """
    rows = conn.execute(
        "SELECT * FROM proposal WHERE kind = ? AND status = 'pending' ORDER BY id",
        (BLUEPRINT_KIND,),
    ).fetchall()
    superseded: list[int] = []
    for row in rows:
        if int(row["id"]) == int(keep_id):
            continue
        if int(payload_of(row).get("plan_id") or 0) != int(plan_id):
            continue
        ledger.set_status(
            conn,
            "proposal",
            int(row["id"]),
            "superseded",
            actor="agent",
            reason=f"计划 #{plan_id} 出了新版蓝图（提案 #{keep_id}），这一版不再算数",
        )
        superseded.append(int(row["id"]))
    return superseded


# ---------- 批准蓝图 = 按勾选建树（决策 36） ----------

@dataclass
class StageBuild:
    """一个阶段落到计划里的样子：复用已有的那条，还是新建一条。"""

    title: str
    deliverable: str | None
    why: str
    # 复用的已有阶段 id（采纳候选时自动建的那个同名阶段）；None = 要新建
    reuse_id: int | None = None
    tasks: list[dict[str, Any]] = field(default_factory=list)


def _selected(stages: list[dict[str, Any]], selected: list[str] | None) -> dict[int, list[int] | None]:
    """把勾选解成 {阶段下标: 任务下标列表 or None}；None = 这个阶段整段都要。

    形状：`"2"` = 第 3 个阶段整段；`"2.1"` = 第 3 个阶段里的第 2 个任务。下标从 0 起
    （就是 payload 里 `stages` 的顺序）。**一个都没给 = 整份都要**——不勾直接批准，
    等于整份采纳；这跟「一个都没勾就什么都不建」不是一回事，后者只会让人困惑。
    """
    if not selected:
        return {index: None for index in range(len(stages))}

    wanted: dict[int, list[int] | None] = {}
    for raw in selected:
        text = str(raw).strip()
        if not text:
            continue
        head, _, tail = text.partition(".")
        if not head.isdigit():
            raise BlueprintError(
                f"勾选「{text}」看不懂——用「阶段下标」或「阶段下标.任务下标」（下标从 0 开始）"
            )
        stage_index = int(head)
        if stage_index >= len(stages):
            raise BlueprintError(f"勾选「{text}」超界了：这份蓝图只有 {len(stages)} 个阶段")
        if not tail:
            wanted[stage_index] = None
            continue
        if not tail.isdigit():
            raise BlueprintError(
                f"勾选「{text}」看不懂——用「阶段下标」或「阶段下标.任务下标」（下标从 0 开始）"
            )
        task_index = int(tail)
        tasks = stages[stage_index].get("tasks") or []
        if task_index >= len(tasks):
            raise BlueprintError(
                f"勾选「{text}」超界了：第 {stage_index + 1} 个阶段只有 {len(tasks)} 件任务"
            )
        if stage_index in wanted and wanted[stage_index] is None:
            continue  # 整个阶段都要了，没必要再记单个任务
        wanted.setdefault(stage_index, []).append(task_index)
    return wanted


def resolve_build(
    conn: sqlite3.Connection, payload: dict[str, Any], selected: list[str] | None
) -> list[StageBuild]:
    """把「勾了什么」解析成「要建哪些节点」，并在**一条都不写**的前提下把所有冲突查完。

    为什么非要先查完：台账每个操作各自提交、没有请求级事务，建到一半撞上重名会留下
    半截的树。所以这里有两条硬规矩——同名阶段**复用**（采纳候选时已经自动建了同名阶段，
    它是这条方向的落脚点），同名任务**报错**（那是模型写坏了，该驳回重出一版）。
    """
    plan_id = int(payload.get("plan_id") or 0)
    plan_row = plan.resolve_plan(conn, plan_id)
    if plan_row is None:
        raise BlueprintNotFound(f"蓝图里的计划 id={plan_id} 已不存在，先去计划表核对一下")
    if str(plan_row["status"]) != "active":
        raise BlueprintConflict(
            f"计划 #{plan_id} 已不是进行中（{plan_row['status']}），不能往里建树"
        )

    stages: list[dict[str, Any]] = payload.get("stages") or []
    builds: list[StageBuild] = []
    by_title: dict[str, StageBuild] = {}
    for stage_index, task_indexes in _selected(stages, selected).items():
        entry = stages[stage_index]
        title = str(entry.get("title") or "").strip()
        build = by_title.get(title)
        if build is None:
            existing = plan.find_open_duplicate(conn, plan_id, "stage", title)
            build = StageBuild(
                title=title,
                deliverable=str(entry.get("deliverable") or "").strip() or None,
                why=str(entry.get("why") or "").strip(),
                reuse_id=None if existing is None else int(existing["id"]),
            )
            by_title[title] = build
            builds.append(build)
        tasks = entry.get("tasks") or []
        wanted_tasks = range(len(tasks)) if task_indexes is None else task_indexes
        for index in wanted_tasks:
            build.tasks.append(tasks[index])

    if not builds:
        raise BlueprintError(
            "一个都没勾——至少要勾一个阶段或任务（整份都要就别勾，直接批准）"
        )
    _check_task_conflicts(conn, plan_id, builds)
    return builds


def _check_task_conflicts(
    conn: sqlite3.Connection, plan_id: int, builds: list[StageBuild]
) -> None:
    """任务重名：蓝图自己内部重名、或要挂进已有阶段却撞上它开着的同名任务。"""
    for build in builds:
        titles = [str(task.get("title") or "").strip() for task in build.tasks]
        repeated = sorted({title for title in titles if titles.count(title) > 1})
        if repeated:
            raise BlueprintError(
                f"蓝图里阶段「{build.title}」下有两件同名任务：{'、'.join(repeated)}——"
                "驳回它，让模型再出一版"
            )
        if build.reuse_id is None:
            continue
        open_titles = set(plan.stage_completion(conn, build.reuse_id)["open_titles"])
        clash = sorted(title for title in titles if title in open_titles)
        if clash:
            raise BlueprintError(
                f"阶段「{build.title}」里已经开着同名任务：{'、'.join(clash)}——"
                "重复建会撞防重复闸，先在计划表里把这几条处理掉再来批准"
            )


def apply_build(
    conn: sqlite3.Connection, plan_id: int, builds: list[StageBuild]
) -> dict[str, Any]:
    """真正建节点。走到这里冲突都验过了，每个 add_node 都还会再走一遍防重复闸。"""
    created_stages: list[dict[str, Any]] = []
    created_tasks: list[dict[str, Any]] = []
    notes: list[str] = []
    # 新建的阶段排在已有阶段之后（采纳时自动建的那个阶段在 0）
    next_order = max([int(row["sort_order"]) for row in plan.get_stages(conn, plan_id)] + [0]) + 1
    for build in builds:
        if build.reuse_id is None:
            stage_id = plan.add_node(
                conn,
                plan_id,
                "stage",
                build.title,
                deliverable=build.deliverable,
                sort_order=next_order,
                actor="user",
            )
            next_order += 1
            created_stages.append(
                {"id": stage_id, "title": build.title, "deliverable": build.deliverable}
            )
        else:
            stage_id = build.reuse_id
            reused = plan.get_node(conn, stage_id)
            note = (
                f"阶段「{build.title}」这个计划里已经有（#{stage_id}），没有重复建——"
                "任务挂到它下面了"
            )
            # 复用的阶段也能改字段了（T30 的写入口，2026-09-18）：蓝图里写的「要交的东西」
            # 按这一版写进去——改前改后走同一道台账流水，不是悄悄覆盖。
            if build.deliverable and reused is not None and reused["deliverable"] != build.deliverable:
                before = reused["deliverable"]
                plan.update_node_fields(
                    conn,
                    stage_id,
                    deliverable=build.deliverable,
                    reason=f"批准这一版蓝图：把「要交的东西」写进已有的同名阶段",
                )
                note += (
                    f"；它「要交的东西」"
                    + (f"从「{before}」改成了" if before else "写成了")
                    + f"「{build.deliverable}」（改前改后进了台账）"
                )
            notes.append(note)
        for index, task in enumerate(build.tasks, start=1):
            title = str(task.get("title") or "").strip()
            node_id = plan.add_node(
                conn,
                plan_id,
                "task",
                title,
                parent_id=stage_id,
                due_date=task.get("due_date"),
                sort_order=index,
                actor="user",
            )
            created_tasks.append({"id": node_id, "title": title, "stage_id": stage_id})
    return {"plan_id": plan_id, "stages": created_stages, "tasks": created_tasks, "notes": notes}


def build_tree(
    conn: sqlite3.Connection, payload: dict[str, Any], selected: list[str] | None = None
) -> dict[str, Any]:
    """批准一份蓝图：预检 + 建树（独立入口，`proposals.decide` 里分两步调用同一对函数）。"""
    builds = resolve_build(conn, payload, selected)
    return apply_build(conn, int(payload.get("plan_id") or 0), builds)