"""计划级对话：蓝图落地之后，接着聊这个计划（SPEC 决策 37）。

为什么单独一个模块、单独一张表：这和 `blueprint.py` 里那段「定方向」的对话不是一回事——
那段绑在候选上、有 6 轮上限、终点是出一棵蓝图；这一段跟着计划走、**不限轮数**，
聊的是执行期的事（卡在哪、下一步先做哪个、要不要调整节奏），蓝图建成之后才真正开始用它。
两者生命周期与上限都不同，塞进一张表只会让每条查询先判断「这是哪种对话」。

**上下文给什么**（2026-09-18 起）：不只看「计划现在长什么样」，还看「它是怎么来的」——
采纳过哪条方向、出蓝图之前聊了什么、蓝图里每阶段的理由、以及哪些当时没被勾中所以没建。
用户的原话是「只要是这个计划里面的，都该让它知道」。

三条纪律：
- LLM 只产出**提案**：这一段对话里的「我的状态变了」要落成 `profile_change` 提案，
  写档案必须经你裁定（SPEC 第 8 节铁律）。对话本身不碰计划一个字。
- 调用卡在 `llm.Operation` 上：**每轮 1 次调用、不重试**；提炼档案提案 1 次 + 不合格重试 1 次。
- 成本闸不是轮数而是**历史字符上限**（决策 6/37 的修订）：长期窗口不能用「聊六次就锁死」
  去卡，真正要防的是「代码写出死循环」——而这里每一次都由你手点一句才动一次，没有循环风险。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from . import advisor, blueprint as blueprint_mod, ledger, llm, plan, profile
from .db import now_iso

TASK_DIALOGUE = "plan_dialogue"
TASK_EXTRACT = "dialogue_extract"

# 落进 proposal.kind：用它已有的那一个（决策 6 里「档案变更」这一类的形状一直空着，
# T28 的计划对话成了它的第一个生产者）。
PROFILE_CHANGE_KIND = "profile_change"

# 历史累计字符上限：比候选对话（4000）宽一些，因为执行期要说的事实更多。超出从最早截断。
DIALOGUE_CHAR_LIMIT = 6000

# 提炼档案提案时最多取几条、看最近几份报告。都定成常量便于按实测调。
MAX_EXTRACTED_ITEMS = 3
RECENT_REPORTS = 5

# 「这条方向是怎么来的」与「蓝图长什么样」这两段也要进上下文（2026-09-18 用户要求：
# 只要是这个计划里的，它都该知道——包括当时没勾的部分与每个阶段的理由）。
# 这段事实每轮都要重发，所以各给一个字符上限；超出从最旧的截断。
LINEAGE_CHAR_LIMIT = 1500
BLUEPRINT_CHAR_LIMIT = 2500


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
    return list(
        conn.execute(
            "SELECT role, content, created_at FROM plan_dialogue"
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


def _record(conn: sqlite3.Connection, plan_id: int, role: str, content: str) -> None:
    """追加一句。这张表是追加式日志、不经台账（同 learning_request 的先例）。"""
    conn.execute(
        "INSERT INTO plan_dialogue (plan_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (plan_id, role, content, now_iso()),
    )
    conn.commit()


# ---------- 组上下文：它凭什么给出有用的回答 ----------

def context_text(conn: sqlite3.Connection, plan_id: int) -> str:
    """每一轮都要给的事实：这个计划现在长什么样 + 最近发生了什么。

    为什么给这么多、而不像候选对话那样只给「当前阶段」：执行期的对话要能回答
    「卡在哪、下一步先做哪个」，不给足事实它就只能说套话。事实每轮重新拼一遍，
    所以你在计划表里打了勾、交了交付物，下一轮它就知道了。
    """
    tree = plan.plan_tree(conn, plan_id)
    plan_row = tree["plan"]
    lines = [
        "【这个计划】",
        f"- 目标：{plan_row['goal']}",
        f"- 状态：{plan_row['status']}",
    ]
    lag = tree["lag"]
    if lag["behind"]:
        lines.append(f"- 落后情况：落后 {lag['lag_days']} 天（最紧的一条是「{lag['worst']}」）")
    else:
        lines.append("- 落后情况：没落后")

    lines.append("")
    lines.append("【阶段与任务】按顺序（[ ] 未开始 / [~] 进行中 / [x] 完成 / [-] 跳过）")
    marks = {"not_started": " ", "in_progress": "~", "done": "x", "stuck": "!", "skipped": "-"}
    for stage in tree["stages"]:
        head = "（已完成）" if stage["finished"] else ""
        lines.append(f"- 阶段「{stage['title']}」{head}")
        if stage["deliverable"]:
            lines.append(f"  要交的东西：{stage['deliverable']}")
        submission = stage["deliverable_submission"]
        if submission is not None:
            lines.append(f"  已提交交付物：{submission['url']}（{submission['note']}）")
        tasks = stage["tasks"]
        if not tasks:
            lines.append("  （这个阶段还没有任务）")
        for task in tasks:
            due = f"｜截止 {task['due_date']}" if task["due_date"] else ""
            late = f"｜落后 {task['lag_days']} 天" if task["lag_days"] else ""
            lines.append(f"  [{marks.get(task['status'], '?')}] {task['title']}{due}{late}")

    lines += ["", *_lineage(conn, plan_id)]
    lines += ["", *_blueprints(conn, plan_id)]

    reports = _recent_reports(conn, plan_id)
    lines += ["", f"【最近 {len(reports)} 份报告】" if reports else "【最近报告】还没有报告"]
    lines += [
        f"- {str(row['created_at'])[:10]}「{row['title']}」{row['status']}：{row['note'] or '（没写说明）'}"
        for row in reports
    ]

    read = advisor.read_profile(conn)
    lines += ["", "【我的长期档案】方括号里是类别（判断要对着它说）："]
    lines += [
        f"#{item['id']} [{item['category']}] {item['content']}" for item in read["items"]
    ] or ["（一条都没有——所以判据主要来自上面的计划事实）"]
    if read["missing_categories"]:
        names = "、".join(advisor.PROFILE_CATEGORIES[name] for name in read["missing_categories"])
        lines += [f"（这几类还空着：{names}——要靠它们才能定的，问我。）"]
    return "\n".join(lines)


def _lineage(conn: sqlite3.Connection, plan_id: int) -> list[str]:
    """这个计划是怎么来的：采纳过哪条方向、出蓝图之前聊了什么。

    为什么要给：不给它就只能看见「现在这棵树」，答不了「当初为什么这么排」——
    而这类问题恰恰是执行期最常问的。数据在 `plan_chat` 里（定方向那段对话）。
    """
    rows = conn.execute(
        "SELECT DISTINCT candidate_id FROM plan_chat WHERE plan_id = ? ORDER BY candidate_id",
        (plan_id,),
    ).fetchall()
    if not rows:
        return ["【这条方向的来历】没有记录（这个计划不是从采纳一条候选来的，或是更早建的）"]

    lines = ["【这条方向的来历】"]
    for row in rows:
        candidate = conn.execute(
            "SELECT id, title, why, depth_target, status FROM candidate WHERE id = ?",
            (row["candidate_id"],),
        ).fetchone()
        if candidate is None:
            continue
        lines.append(
            f"- 采纳的方向：{candidate['title']}（当时给的理由：{candidate['why']}；"
            f"建议深度 {candidate['depth_target']}）"
        )
        said: list[str] = []
        for message in conn.execute(
            "SELECT role, content FROM plan_chat WHERE plan_id = ? AND candidate_id = ? ORDER BY id",
            (plan_id, row["candidate_id"]),
        ):
            text_of = (
                blueprint_mod.render_reply(str(message["content"]))
                if str(message["role"]) == "assistant"
                else str(message["content"])
            )
            said.append(f"  {'它' if message['role'] == 'assistant' else '我'}：{text_of}")
        kept, used = [], 0
        for line in reversed(said):  # 从最新往回收，收不下就丢更早的
            if kept and used + len(line) > LINEAGE_CHAR_LIMIT:
                break
            kept.insert(0, line)
            used += len(line)
        lines += kept or ["  （那段对话没有记录）"]
    return lines


def _blueprints(conn: sqlite3.Connection, plan_id: int) -> list[str]:
    """这个计划的蓝图：最近的待裁定那版，与已经建进计划的那版。

    除了阶段与任务本身，还标出**哪些当时没被勾中、因此没建**——那是「为什么计划里
    没有这一块」的答案。更早的版本不铺开（都被顶掉了），只报个数。
    """
    rows = conn.execute(
        "SELECT * FROM proposal WHERE kind = ? ORDER BY id DESC",
        (blueprint_mod.BLUEPRINT_KIND,),
    ).fetchall()
    mine = [row for row in rows if int(blueprint_mod.payload_of(row).get("plan_id") or 0) == int(plan_id)]
    if not mine:
        return ["【蓝图】没有记录（这个计划不是从蓝图建起来的，或是更早建的）"]

    taken = {
        str(stage["title"]): {str(task["title"]) for task in stage["tasks"]}
        for stage in plan.plan_tree(conn, plan_id)["stages"]
    }
    wanted: list[str] = []
    for row in mine:
        status = str(row["status"])
        if status == "superseded":
            continue  # 被新版顶掉的版本不值一提，下面只报个数
        if status == "pending":
            label = "待你勾选的那一版"
        elif status == "accepted":
            label = "已经建进计划的这一版"
        else:
            label = f"（{status}）"
        payload = blueprint_mod.payload_of(row)
        block = [f"- {label}：目标「{payload.get('goal')}」"]
        for stage in payload.get("stages") or []:
            title = str(stage.get("title"))
            built = title in taken
            block.append(f"  · 阶段「{title}」{'（已建）' if built else '（当时没勾，没建）'}")
            block.append(f"    要交的东西：{stage.get('deliverable')}")
            if stage.get("why"):
                block.append(f"    为什么先做它：{stage['why']}")
            for task in stage.get("tasks") or []:
                task_title = str(task.get("title"))
                done = task_title in taken.get(title, set())
                due = f"｜截止 {task['due_date']}" if task.get("due_date") else ""
                block.append(f"    - {task_title}{due}{'' if done else '（当时没勾，没建）'}")
                if len(chr(10).join(block)) > BLUEPRINT_CHAR_LIMIT:
                    break
        wanted += block
        if len(chr(10).join(wanted)) > BLUEPRINT_CHAR_LIMIT:
            wanted.append("  （这版太长，只列到这里）")
            break
    dropped = [row for row in mine if str(row["status"]) == "superseded"]
    head = ["【蓝图】"]
    if dropped:
        head.append(f"（另有 {len(dropped)} 版更早的已被新版顶掉，不再列出）")
    return head + wanted


def _recent_reports(conn: sqlite3.Connection, plan_id: int) -> list[sqlite3.Row]:
    """这个计划下最近几份报告，按时间正序（最新的在最后）。"""
    rows = conn.execute(
        """SELECT r.status, r.note, r.created_at, n.title
           FROM report r JOIN plan_node n ON n.id = r.node_id
           WHERE n.plan_id = ? ORDER BY r.id DESC LIMIT ?""",
        (plan_id, RECENT_REPORTS),
    ).fetchall()
    return list(reversed(rows))


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
    "直接说人话：可以用短段落或列表，**不要输出 JSON**、不要客套开场。"
    "对着下面给的事实说（他的阶段、开着的任务、最近的报告、落后情况），别讲放之四海皆准的话。"
    "你只能讨论与建议，不能改计划：要改结构就告诉他去计划表里做什么（打勾 / 跳过 / 交交付物）。"
    "信息不够就问一句——但不要每轮都抛一串问题。默认控制在 200 字以内，除非他要求展开。"
)

EXTRACT_SYSTEM_PROMPT = (
    "你从一段对话里提炼「需要更新长期档案」的条目。你只输出一个 JSON 对象："
    "不要解释、不要客套、不要 Markdown 代码块。"
)


# ---------- 看 / 聊 ----------

def view(conn: sqlite3.Connection, plan_id: int) -> dict[str, Any]:
    """看这段对话：计划、历史、聊了几句、能不能提炼档案提案。"""
    plan_of(conn, plan_id)
    rows = messages_of(conn, plan_id)
    used = turns_used(conn, plan_id)
    return {
        "plan_id": plan_id,
        "messages": [dict(row) for row in rows],
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
    """聊一句：记下你的话 → 调 1 次模型 → 记下它的回话。

    **不重试**（每轮 1 次调用）：输出为空就如实报错，而你那句话已经留在对话里了——
    再说一句就接着走。助手这一侧存的是**人话**（不是 JSON）：这里不需要解析它的输出，
    逼它包一层 JSON 只会让回答变别扭、还多一类失败。
    """
    plan_of(conn, plan_id)
    text = str(message or "").strip()
    if not text:
        raise DialogueError("总得说点什么")

    _record(conn, plan_id, "user", text)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": context_text(conn, plan_id)},
        *_trim(messages_of(conn, plan_id)),
    ]
    operation = llm.Operation(conn, TASK_DIALOGUE, limit=1, transport=transport)
    reply = str(operation.chat(messages, provider_id=provider_id, model=model) or "").strip()
    if not reply:
        raise DialogueError(
            "模型这次一个字都没回；这一轮只给 1 次调用（决策 6 修订），所以没有重试——"
            "你那句话还在对话里，再说一句就接着聊"
        )

    _record(conn, plan_id, "assistant", reply)
    return {
        "plan_id": plan_id,
        "reply": reply,
        "turns_used": turns_used(conn, plan_id),
        "calls": operation.used,
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