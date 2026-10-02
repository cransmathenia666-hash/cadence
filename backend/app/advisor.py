"""四问判断：把「我发现了一个资料，要不要学」交给 LLM，产出**可回溯**的四问答案。

为什么强调"可回溯"：SPEC 第 9 节成功标准 3 要求每条答案能指回长期档案里的**具体字段**，
而不是一段听着有道理的空话。所以这里的合格线很硬——每条答案都要带 `profile_item` 的 id；
指不回去就算不合格：宁可重试、宁可报错，也不放空话进库。

两条边界（和项目对 LLM 的一贯态度一致）：
- LLM 只产出**提案**：结果落进 `proposal` 表等你裁定，绝不直接改档案或计划。
- 调用次数由 `llm.Operation` 卡着（同一操作最多 3 次）；这里的"重试一次"用掉 2 次。

前端入口在 T14；本题（T12）只开后端这条链路。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import ledger, llm, plan
from .db import atomic, now_iso
from .providers import find
from .providers.find import Brief


class AdvisorError(RuntimeError):
    """决策链路上的明确错误：没档案、模型连着两次输出不合格、上游调用失败。

    一律明确报错而不是"返回一段凑合的建议"——这本产品的价值就在判断质量，
    悄悄给个平庸答案比报错更糟。
    """


class CandidateNotFound(AdvisorError):
    """候选不存在 → 接口层翻成 404。"""


class CandidateConflict(AdvisorError):
    """候选已裁定过，或采纳的前提不满足（没有 active 计划 / 有同名未收尾阶段）
    → 接口层翻成 409（与现状冲突，不是参数写错）。"""


class ThreadConflict(AdvisorError):
    """线程归属校验失败：线程不存在 / 属于别的计划 / 追问不属于该线程 → 接口层翻 409。

    为什么要专门一类：线程归属错了不该悄悄退化成「当作新问题」或「当作补充」——
    那正是 I-02 要修的「刷新、切计划后回答生成错误归属」的口子。归属错了就要人挡下来，
    让用户回到原线程去答。
    """


# 记在 llm_call.task 里的任务名，和 connectivity_test 之类区分开
TASK = "judge"

# 落进 proposal.kind 的取值。为什么不复用现有三个（profile_change / plan_replan /
# stage_advance）：它们分别是"改档案""重排计划""推进阶段"，都不是"判一个资料"。
# 只多一个字符串取值，不动表结构、不用迁移；T14 的裁定界面靠它分流。
MATERIAL_JUDGMENT_KIND = "material_judgment"

# 长期档案的五类（SPEC 第 5.1 节）。库里 category 是自由文本、没有数据库层面的约束，
# 这张表就是**约定的词表**：目前只有 `long_axis` 与 `life_log` 真实用过，另外三个是按
# 同一命名风格先定下来的。表外的类别不会丢——照样原样传给模型，只是不算进"缺失类别"。
PROFILE_CATEGORIES: dict[str, str] = {
    "life_habit": "生活习惯（睡眠/运动/作息）",
    "life_log": "生活记录（日程/课程/近况）",
    "current_state": "当前状态（精力/时间/压力）",
    "short_term_goal": "短期目标 + 当下痛点",
    "long_axis": "长期主线（职业方向）",
}

# 四问：键名固定、顺序固定。prompt 与校验共用这一份，免得两处各写一遍走偏。
QUESTIONS: dict[str, str] = {
    "worth_learning": "① 值不值得学（对长期主线 / 当下痛点的贡献）",
    "depth_target": "② 学到什么程度（浅尝 / 够用 / 熟练 / 精通）",
    "intensity": "③ 板块分级（深学 还是 过一遍）",
    "time_budget": "④ 时间预算（按当前状态排期）",
}

# 每问的判据来自哪几类档案——照抄 SPEC 第 4 节那张表，不在这里另编一套。
JUDGE_SOURCES: dict[str, tuple[str, ...]] = {
    "worth_learning": ("long_axis", "short_term_goal"),
    "depth_target": ("long_axis", "short_term_goal"),
    "intensity": ("long_axis", "current_state"),
    "time_budget": ("current_state", "life_habit", "life_log"),
}

SYSTEM_PROMPT = (
    "你是学习决策助手。你只输出一个 JSON 对象：不要解释、不要客套、不要 Markdown 代码块。"
)


# ---------- 输出的形状（LLM 的答案先过这里） ----------

class Answer(BaseModel):
    """一问的答案：一句话 + 它依据的档案 id。

    `answer` 不许为空——空答案就是"没回答"，不该被当成合格输出放过去。
    """

    answer: str = Field(min_length=1)
    profile_item_ids: list[int] = Field(default_factory=list)


class Judgment(BaseModel):
    """四问齐全才算合格。少一问会被 Pydantic 直接拦住（不补默认值，不猜）。"""

    worth_learning: Answer
    depth_target: Answer
    intensity: Answer
    time_budget: Answer


# ---------- 读档案 ----------

def read_profile(conn: sqlite3.Connection) -> dict[str, Any]:
    """长期档案的当前有效值（`GET /api/profile` 就返回这个）。

    "当前有效"由台账保证：`superseded` / `void` 的旧值不在这里出现。
    """
    rows = ledger.fetch_active(conn, "profile_item")
    items = [
        {
            "id": int(row["id"]),
            "category": row["category"],
            "content": row["content"],
            "valid_from": row["valid_from"],
        }
        for row in rows
    ]
    present = {item["category"] for item in items}
    return {
        "items": items,
        "missing_categories": [name for name in PROFILE_CATEGORIES if name not in present],
    }


def record_request(
    conn: sqlite3.Connection,
    kind: str,
    raw_text: str,
    plan_id: int | None = None,
    thread_id: int | None = None,
    utterance: str | None = None,
) -> int:
    """把你的这一轮输入记一行。

    `plan_id` = 这一轮针对哪个计划；为空表示「新方向（不属于任何计划）」（SPEC 决策 33 ①）。
    候选随请求继承这个归属，采纳时才知道该落进哪个计划。

    `thread_id` = 这一轮属于哪条探索线程（决策 44 ①）：线程身份 = 线程头请求的 id。
    `search` 且不传 `thread_id` 就是**新线程**——在同一事务里把该列回填成自己的 id，
    这样「新线程」与「续线程」落库后长得一样，读侧不用再猜。传了就按传入记（同线程续问）。
    旧记录该列为 NULL，各自算独立线程——**禁止**靠原话相同把它们合并成一段（I-01）。

    这张表是**追加式日志**、不是带状态机业务表，所以不经台账（`ledger` 只管有状态的
    对象）；和 `llm.record_call` 直接插一行记账是同一个道理。
    """
    cursor = conn.execute(
        "INSERT INTO learning_request (kind, raw_text, plan_id, thread_id, utterance, turn_status, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (kind, raw_text, plan_id, thread_id, utterance, "pending" if utterance is not None else None, now_iso()),
    )
    request_id = int(cursor.lastrowid)
    if kind == "search" and thread_id is None:
        # 新线程：身份 = 自己的 id。回填与 INSERT 同事务，中途失败就不会留下「无主」的一行。
        conn.execute(
            "UPDATE learning_request SET thread_id = ? WHERE id = ?", (request_id, request_id)
        )
    conn.commit()
    return request_id


def record_clarify(conn: sqlite3.Connection, request_id: int, clarify: dict[str, Any]) -> None:
    """把这一轮问出的追问记在请求行上（T36）。

    T25 定的是「追问不落库」，理由是它不该长出自己的表；但 T36 要「问过的不再问」——
    模型得在下一轮看得到「这件事我已经答过了」，而反馈流水是**从库里读**的。所以它落在
    `learning_request.clarify` 这一列上（可空 JSON），不另立表：追问与它所属的那一轮本就
    是同一条记录的一部分。

    只记「问了什么、缺哪类」；`answer` 由 `mark_clarify_answered` 在下一轮补上。
    """
    conn.execute(
        "UPDATE learning_request SET clarify = ? WHERE id = ?",
        (
            json.dumps(
                {
                    "question": str(clarify.get("question") or "").strip(),
                    "missing": str(clarify.get("missing") or "").strip(),
                    "answer": None,
                },
                ensure_ascii=False,
            ),
            request_id,
        ),
    )
    conn.commit()


def _stored_clarify(raw: Any) -> dict[str, Any] | None:
    """解 `learning_request.clarify` 那一列；解不开当没有。"""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or not str(data.get("question") or "").strip():
        return None
    answer = data.get("answer")
    return {
        "question": str(data["question"]).strip(),
        "missing": str(data.get("missing") or "").strip(),
        "answer": None if answer is None else str(answer).strip(),
    }


def pending_clarify(conn: sqlite3.Connection, plan_id: int | None) -> dict[str, Any] | None:
    """同一计划归属下**最近一轮问过、还没答**的追问；没有就返回 None。

    只看同一个计划范围：两段「找」问的是不同的事，串台会让回答落到别的计划上。
    """
    row = conn.execute(
        "SELECT id, clarify FROM learning_request"
        " WHERE kind = 'search' AND plan_id IS ? AND clarify IS NOT NULL"
        " ORDER BY id DESC LIMIT 1",
        (plan_id,),
    ).fetchone()
    if row is None:
        return None
    stored = _stored_clarify(row["clarify"])
    if stored is None or stored["answer"]:
        return None
    return {"request_id": int(row["id"]), **stored}


def mark_clarify_answered(
    conn: sqlite3.Connection, request_id: int, answer: str, owner_token: str | None = None
) -> bool:
    """把某一轮问出的追问标记成「已答」（T36）：`answer` 进库，反馈流水因此看得见。

    写入是 CAS（旧值比对，F4 复核整改）：置上 answer 的同时清掉 `claimed_at` 占用标记
    ——「已答」取代「作答中」。旧值比对失败说明别路刚改过这条 JSON（抢先接管了占用），
    此刻覆盖别人的写入就是并发重复消费——不覆盖，静默返回。
    """
    row = conn.execute(
        "SELECT clarify FROM learning_request WHERE id = ?", (request_id,)
    ).fetchone()
    if row is None or not row["clarify"]:
        return False
    try:
        data = json.loads(row["clarify"])
    except (TypeError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    if data.get("owner_token") != owner_token:
        return False
    if data.get("answer"):
        return False
    data["answer"] = str(answer).strip()
    data.pop("claimed_at", None)  # 「已答」取代「作答中」
    data.pop("owner_token", None)
    cursor = conn.execute(
        "UPDATE learning_request SET clarify = ? WHERE id = ? AND clarify = ?",
        (json.dumps(data, ensure_ascii=False), request_id, row["clarify"]),
    )
    if cursor.rowcount == 0:
        return False  # 别路刚写入过：不覆盖
    conn.commit()
    return True


# 追问占用的保鲜期（F4 复核整改），单位秒。占用记的是「有一路正在答这一条」：正常它要么
# 被成功轮次以「已答」取代，要么被失败路径的 release 摘掉；只有进程崩溃才会留下残留占用。
# 给它一个期限、到期视为无主可重新抢占——否则一次崩溃就把这条追问永久锁死。
CLAIM_TTL_SECONDS = 15 * 60

# 并发抢占失败的固定文案（F4）：不许含糊地说「出错」，要告诉用户该刷新看最新状态。
_CLAIM_TAKEN_MESSAGE = "这条追问刚被另一轮回答接管，刷新后看最新状态"


def _claim_is_stale(claimed_at: str, now: datetime) -> bool:
    """占用时间戳是否已过保鲜期。解不开的时间戳按残留处理（可抢占）——宁可多放行一次，
    也不让一条解不开时间的占用把追问变成永久锁。"""
    try:
        claimed = datetime.fromisoformat(str(claimed_at))
    except ValueError:
        return True
    if claimed.tzinfo is None:
        claimed = claimed.astimezone()
    return (now - claimed).total_seconds() >= CLAIM_TTL_SECONDS


def _claim_clarify(conn: sqlite3.Connection, request_id: int) -> str:
    """对一条待答追问做 CAS 原子占用（F4 复核整改）。

    占用记在 clarify JSON 的 `claimed_at` 字段上（不加列）：读出原 JSON → 写回带占用
    时间戳的 JSON，UPDATE 带旧值比对——同一追问被两路并发回答时只有一路写得进去，
    另一路拿到 ThreadConflict，回答不会被重复消费。已有 answer 的没有「作答中」可言。
    """
    row = conn.execute(
        "SELECT clarify FROM learning_request WHERE id = ?", (request_id,)
    ).fetchone()
    if row is None or not row["clarify"]:
        return
    try:
        data = json.loads(row["clarify"])
    except (TypeError, ValueError):
        return
    if not isinstance(data, dict) or data.get("answer"):
        return
    now = datetime.now().astimezone()
    claimed_at = data.get("claimed_at")
    if claimed_at is not None and not _claim_is_stale(str(claimed_at), now):
        raise ThreadConflict(_CLAIM_TAKEN_MESSAGE)
    fresh = dict(data)
    fresh["claimed_at"] = now.isoformat()
    fresh["owner_token"] = uuid.uuid4().hex
    cursor = conn.execute(
        "UPDATE learning_request SET clarify = ? WHERE id = ? AND clarify = ?",
        (json.dumps(fresh, ensure_ascii=False), request_id, row["clarify"]),
    )
    if cursor.rowcount == 0:
        # 旧值比对失败 = 别路刚刚抢先写入（占了或已答）——CAS 的失败就是它的意义
        raise ThreadConflict(_CLAIM_TAKEN_MESSAGE)
    conn.commit()
    return fresh["owner_token"]


def release_clarify_claim(
    conn: sqlite3.Connection, request_id: int, owner_token: str | None = None
) -> None:
    """把占用标记摘掉（CAS 摘，摘不掉就算了）——main.py 的模型失败路径**必须**调它。

    契约（F4）：失败必须放锁，原样重试才能接回同一条追问；不放锁，重试会被自己上一轮
    的残留占用挡住。摘不掉（别路已接管或已置答）就保留现状——那种情况下保留别人的
    写入才是对的，不能拿自己的旧快照盖回去。
    """
    row = conn.execute(
        "SELECT clarify FROM learning_request WHERE id = ?", (request_id,)
    ).fetchone()
    if row is None or not row["clarify"]:
        return
    try:
        data = json.loads(row["clarify"])
    except (TypeError, ValueError):
        return
    if not isinstance(data, dict) or "claimed_at" not in data:
        return
    if data.get("owner_token") != owner_token:
        return
    fresh = dict(data)
    fresh.pop("claimed_at", None)
    fresh.pop("owner_token", None)
    cursor = conn.execute(
        "UPDATE learning_request SET clarify = ? WHERE id = ? AND clarify = ?",
        (json.dumps(fresh, ensure_ascii=False), request_id, row["clarify"]),
    )
    if cursor.rowcount:
        conn.commit()


def compose_clarify_round(
    conn: sqlite3.Connection,
    raw_text: str,
    answer: str,
    plan_id: int | None,
    clarify_request_id: int | None = None,
) -> str:
    """回答追问的下一轮输入：**最初那句原话必须仍在场**，追问与回答缀在后面。

    有明确轮次时只接受该轮的未回答追问；没有明确轮次时保留旧的最近轮次兜底，
    兼容旧客户端，但新客户端不再依赖“同计划最近一条”猜测。
    """
    answer = answer.strip()
    asked: dict[str, Any] | None
    if clarify_request_id is None:
        asked = pending_clarify(conn, plan_id)
    else:
        row = conn.execute(
            "SELECT id, plan_id, clarify FROM learning_request "
            "WHERE id = ? AND kind = 'search'",
            (clarify_request_id,),
        ).fetchone()
        stored = None if row is None else _stored_clarify(row["clarify"])
        if row is None or row["plan_id"] != plan_id or stored is None or stored["answer"]:
            asked = None
        else:
            asked = {"request_id": int(row["id"]), **stored}
    if asked is None:
        # 没有待答的追问（页面刷新过、或已经答过一遍）：不吞掉你的话，缀在后面
        return f"{raw_text}\n补充：{answer}"
    mark_clarify_answered(conn, asked["request_id"], answer)
    return f"{raw_text}\n【追问】{asked['question']}\n【我的回答】{answer}"


# ---------- 探索线程（2026-09-28 双入口整改，SPEC 决策 44 ①） ----------
#
# 一段「找方向」的连续对话 = 一条探索线程：线程身份 = 线程头请求的 id，同线程后续行都记
# 这个 id（`learning_request.thread_id`）。线程为什么必须显式建模：此前「这轮接着上轮吗」
# 全靠猜——同句原文、同计划最近一条追问，猜错就生成错误归属的候选（I-01/I-02）。
# 旧记录该列为 NULL，各自算独立线程；不靠原话相同合并，认不准的关系就不认。


def _thread_rows_where(thread_id: int | None, alias: str | None = None) -> tuple[str, list[Any]]:
    """线程范围的一对（WHERE 片段, 参数）：`thread_id = T OR id = T`。

    为什么要 `OR id = T`：线程头自己那行 `thread_id` 就是自己的 id，但**成为线程头的旧记录**
    该列是 NULL——按 id 把它一并圈进来，旧记录才能作为线程头被新客户端续上。
    `thread_id` 传 None（全新线程、还没有任何行）时两个等式都不命中，天然得空集，
    不需要调用方特判。`alias` 给出时列名带表前缀（JOIN 查询里 `id` 有歧义）。
    """
    prefix = f"{alias}." if alias else ""
    return f"({prefix}thread_id = ? OR {prefix}id = ?)", [thread_id, thread_id]


def _thread_pending_clarify(conn: sqlite3.Connection, thread_id: int | None) -> dict[str, Any] | None:
    """这条线程内最近一条**未答**追问；没有就 None。形状同 `pending_clarify` 的返回。

    线程版取代「同计划最近一条」的猜测：追问的归属跟着线程走，不被页面上当前选中的
    计划带偏（I-02）。取一小批从新往旧找，跳过已答的——线程里问过又答过的行不该挡住
    更早的未答追问。
    """
    where, params = _thread_rows_where(thread_id)
    rows = conn.execute(
        f"SELECT id, clarify FROM learning_request"
        f" WHERE kind = 'search' AND {where} AND clarify IS NOT NULL"
        f" ORDER BY id DESC LIMIT 20",
        params,
    ).fetchall()
    for row in rows:  # 从最新往回，第一条未答的就是
        stored = _stored_clarify(row["clarify"])
        if stored is not None and not stored["answer"]:
            return {"request_id": int(row["id"]), **stored}
    return None


def _thread_shape(conn: sqlite3.Connection, thread_head: int | None) -> str | None:
    """线程的形态锁（II-02）：线程内最近一条**有候选**的请求的候选形状。

    形态由线程保持、不由每次模型输出决定——第一次出候选时定形，之后模型想换
    `directions`↔`path` 必须走用户确认（`allow_shape_switch`），不能靠自报字段漂移。
    线程还没出过候选（或 `thread_head` 为 None 的全新线程）→ None = 无锁，首轮自由定形。
    形状从 `candidate.payload` 解（与渲染同一把尺），旧候选没 payload 按 directions。
    """
    if thread_head is None:
        return None
    where, params = _thread_rows_where(thread_head, alias="r")
    row = conn.execute(
        f"SELECT c.payload FROM candidate c"
        f" JOIN learning_request r ON r.id = c.request_id"
        f" WHERE {where}"
        f" ORDER BY r.id DESC, c.rank ASC, c.id ASC LIMIT 1",
        params,
    ).fetchone()
    if row is None:
        return None
    return _shape_and_steps(row["payload"])[0]


def resolve_thread(
    conn: sqlite3.Connection,
    *,
    thread_id: int | None,
    plan_id: int | None,
    clarify_request_id: int | None,
    answer: str | None = None,
) -> dict[str, Any]:
    """确定本轮输入属于哪条探索线程。返回 `{"thread_id", "thread_head", "pending_clarify"}`。

    - `thread_id=None` 且无 `clarify_request_id` → **新线程**：thread_id 返回 None，
      由 `record_request` 回填成本行自己的 id。
    - `thread_id=None` 但带了 `clarify_request_id=R`（旧客户端兜底）→ R 必须真是一轮
      「找」（`kind='search'`，四问行冒充不了）；线程 = R 的 thread_id，R 自己是旧记录
      （该列 NULL）时线程 = R 自身、独立成段。反推出的**头行**计划归属也要与本轮
      plan_id 一致（F3 复核整改补的校验，此前漏了）——换计划回答旧追问要明确拒绝。
    - `thread_id=T` → T 必须是**一段探索的开头**（F3 复核整改）：存在 `id = T AND
      kind = 'search'` 的行，且它不是后续轮——后续轮的 thread_id 指向头、不等于自己，
      头自己的 thread_id 就是自己的 id（成为头的旧记录该列是 NULL，也按头认）。
      后续轮 id / 四问 id 冒充线程号一律 ThreadConflict，不再「查到一行就算数」。
      头行计划归属与本轮不一致（含 None ↔ 非 None）→ ThreadConflict（I-02）。

    `clarify_request_id` 给了就校验它属于该线程；它的追问已答不算错——`pending_clarify`
    记 None，回答会被 `build_round_input` 拼成「补充」。`pending_clarify` 是本轮要接的
    那条未答追问：用户点名了哪条就用哪条，没点名才取线程内最近一条。

    `answer` 非空且确有待答追问时（F4）：本轮要作答，先对那条追问做 **CAS 原子占用**
    ——同一条追问被两路并发回答时，后到的被明确挡下，回答不会被重复消费。
    """
    if thread_id is None:
        if clarify_request_id is None:
            return {"thread_id": None, "thread_head": None, "pending_clarify": None}
        # 旧客户端兜底：只带了「我在回答哪一轮」。那一轮必须真是一轮「找」，
        # 线程身份从它反推，不再按计划猜。
        row = conn.execute(
            "SELECT id, thread_id, plan_id FROM learning_request"
            " WHERE id = ? AND kind = 'search'",
            (clarify_request_id,),
        ).fetchone()
        if row is None:
            raise ThreadConflict(
                f"要回答的那一轮（请求 #{clarify_request_id}）不存在（或不是一轮「找」）"
            )
        head = int(row["thread_id"]) if row["thread_id"] is not None else int(row["id"])
        # F3：反推出的头行的计划归属也要核——不核的话，「换一个计划去答旧线程的追问」
        # 就会悄悄成立，回答生成错误归属的候选（I-02 的口子从兼容路径又开回来）。
        head_row = conn.execute(
            "SELECT plan_id FROM learning_request WHERE id = ?", (head,)
        ).fetchone()
        if head_row is None:
            raise ThreadConflict(f"探索线程 #{head} 不存在")
        head_plan = head_row["plan_id"]
        if row["plan_id"] != head_plan or head_plan != plan_id:
            scope = "新方向" if head_plan is None else f"计划 #{head_plan}"
            raise ThreadConflict(f"这段探索属于{scope}，请回到原线程再答，不要换计划续问")
        result = {
            "thread_id": head,
            "thread_head": head,
            "pending_clarify": None,
        }
        stored = _stored_clarify(conn.execute(
            "SELECT clarify FROM learning_request WHERE id = ?", (clarify_request_id,)
        ).fetchone()["clarify"])
        if stored is None and str(answer or "").strip():
            raise ThreadConflict(f"请求 #{clarify_request_id} 没有追问，不能作为回答目标")
        if stored is not None and not stored["answer"]:
            result["pending_clarify"] = {"request_id": clarify_request_id, **stored}
        _claim_pending_if_answering(conn, result, answer)
        return result

    # F3 严核：T 必须是一段探索的**开头**——行存在、是「找」、且不是后续轮。
    # 只查「有没有这个 id」会放过两类坏输入：后续轮的 id（拿它当线程号会把线程根挪到
    # 中间某轮上）、evaluate 类请求的 id（四问混进探索线程）。头的 thread_id 就是自己
    # 的 id（record_request 回填）；成为头的旧记录该列是 NULL，也按头认。
    head_row = conn.execute(
        "SELECT id, plan_id, thread_id FROM learning_request WHERE id = ? AND kind = 'search'",
        (thread_id,),
    ).fetchone()
    if head_row is None or (
        head_row["thread_id"] is not None and int(head_row["thread_id"]) != int(thread_id)
    ):
        raise ThreadConflict(f"探索线程 #{thread_id} 不存在（或不是一段探索的开头）")

    # 计划归属锁在线程头上：头请求属于哪个计划，这段探索就一直在那个计划里。
    # 含 None ↔ 非 None 的两个方向——「新方向」线程也不该被顺手挂到某个计划上续问。
    if head_row["plan_id"] != plan_id:
        scope = "新方向" if head_row["plan_id"] is None else f"计划 #{head_row['plan_id']}"
        raise ThreadConflict(f"这段探索属于{scope}，请回到原线程再答，不要换计划续问")

    pending: dict[str, Any] | None
    if clarify_request_id is not None:
        asked = conn.execute(
            "SELECT id, thread_id, kind, clarify FROM learning_request WHERE id = ?",
            (clarify_request_id,),
        ).fetchone()
        if asked is None or asked["kind"] != "search" or (
            asked["thread_id"] != thread_id and int(asked["id"]) != thread_id
        ):
            raise ThreadConflict(
                f"要回答的追问（请求 #{clarify_request_id}）不属于探索线程 #{thread_id}"
            )
        stored = _stored_clarify(asked["clarify"])
        if stored is not None and not stored["answer"]:
            pending = {"request_id": int(asked["id"]), **stored}  # 用户点名了这条，就用这条
        elif stored is not None:
            pending = None  # 已答过：不算错，回答按「补充」拼（build_round_input 负责）
        else:
            if str(answer or "").strip():
                raise ThreadConflict(f"请求 #{clarify_request_id} 没有追问，不能作为回答目标")
            pending = None
    else:
        pending = _thread_pending_clarify(conn, thread_id)

    result = {"thread_id": thread_id, "thread_head": thread_id, "pending_clarify": pending}
    _claim_pending_if_answering(conn, result, answer)
    return result


def _claim_pending_if_answering(
    conn: sqlite3.Connection, thread: dict[str, Any], answer: str | None
) -> None:
    """本轮要作答（`answer` 非空）且确有待答追问时，先原子占用那条追问（F4）。

    只挂「要作答」的轮：纯续问轮不碰占用——它不消费回答，拦它没有意义。
    占用失败抛 ThreadConflict，由接口层翻 409。
    """
    pending = thread.get("pending_clarify")
    if pending is not None and str(answer or "").strip():
        thread["claim_token"] = _claim_clarify(conn, int(pending["request_id"]))


def build_round_input(
    conn: sqlite3.Connection, *, utterance: str, answer: str | None, thread: dict[str, Any]
) -> str:
    """拼这一轮的模型输入：**原题永远在场，且不信任客户端**（F1 复核整改）。

    基底（打头的那段）：
    - 续聊轮（`thread["thread_head"]` 非 None）→ 一律从库里取**线程头请求的 raw_text**。
      此前拿调用方传的 raw_text 打头——续聊轮前端只发新话时原题就丢了；现在原题由
      后端自己取，客户端重发原题也不会拼出两份。
    - 新线程（thread_head 为 None）→ 基底就是本轮 utterance。

    本轮内容（缀在基底后面，三选一或都不缀）：
    - `answer` 非空且 `thread["pending_clarify"]` 非空 → 「【追问】…【我的回答】…」
      （格式沿用 `compose_clarify_round`）；
    - `answer` 非空但无待答追问 → 「补充：…」——那句话不该被吞掉（页面刷新过、
      追问已被人答过，都照缀）；
    - 无 `answer` 且 utterance 去空白后非空、又与基底不同 → 「【这轮要说】…」
      （客户端可能只发新话，也可能重发原题——重发就不缀，两种都要对）；
    - 都没有 → 原样返回基底。

    **不落任何状态**：「已答」标记挪到 `commit_round`（I-03），占用标记由
    `resolve_thread` 写（F4）——这一步只负责拼输入。
    """
    pending = (thread or {}).get("pending_clarify")
    head = (thread or {}).get("thread_head")
    text = str(answer or "").strip()
    if head is None:
        base = utterance
    else:
        row = conn.execute(
            "SELECT raw_text FROM learning_request WHERE id = ?", (int(head),)
        ).fetchone()
        if row is None:
            # resolve_thread 刚核过线程头存在，走到这里说明库被旁路改坏了：明说，别猜
            raise AdvisorError(f"线程头请求 #{head} 不存在，拼不出这一轮的输入")
        base = str(row["raw_text"] or "")
        if not base.strip():
            base = utterance  # 防御：头的原话不该是空的；真空了就用本轮的话，别拼出空基底
    if text:
        if pending:
            return f"{base}\n【追问】{pending['question']}\n【我的回答】{text}"
        return f"{base}\n补充：{text}"
    if utterance.strip() and utterance.strip() != base.strip():
        return f"{base}\n【这轮要说】{utterance}"
    return base


def commit_round(
    conn: sqlite3.Connection,
    *,
    request_id: int,
    thread: dict[str, Any],
    answer: str | None,
    clarify: dict[str, Any] | None,
    shape_change: dict[str, Any] | None = None,
    intent: str | None = None,
    reply: str | None = None,
) -> None:
    """一轮**成功收尾**后的状态落库——只在模型成功、结果落库之后调用。

    ① 线程里有待答追问、本轮也带了回答 → 把那条追问标成已答（CAS 写入，同时清掉
       F4 的占用标记——「已答」取代「作答中」）；answer 为空就跳过 ①——失败的轮次
       不该产生任何状态变化，追问保持待答、可以原样重试（I-03）；
    ② 本轮模型问出了新追问 → 记在本轮请求行上；
    ③ 本轮模型在 chat 轮提出了**形态切换请求**（F5 复核整改）→ 把
       `{"from", "to", "reason"}` 记在本轮请求行的 `shape_change` 列上。它只是提案，
       真正的形态移动发生在用户确认那一轮出了新候选之后；放行核验走
       `pending_shape_change`。
    """
    text = str(answer or "").strip()
    pending = (thread or {}).get("pending_clarify")
    if pending and text:
        if not mark_clarify_answered(
            conn, int(pending["request_id"]), text, thread.get("claim_token")
        ):
            raise ThreadConflict(_CLAIM_TAKEN_MESSAGE)
    if clarify:
        conn.execute(
            "UPDATE learning_request SET clarify = ? WHERE id = ?",
            (json.dumps({**clarify, "answer": None}, ensure_ascii=False), request_id),
        )
    conn.execute(
        "UPDATE learning_request SET shape_change = ?, intent = ?, reply = ?, turn_status = 'success' WHERE id = ?",
        (
            json.dumps({**shape_change, "decision": "pending"}, ensure_ascii=False) if shape_change else None,
            intent,
            reply,
            request_id,
        ),
    )
    conn.commit()


def fail_round(conn: sqlite3.Connection, request_id: int) -> None:
    """Mark a recorded but unsuccessful search turn; it must not enter model history."""
    conn.execute(
        "UPDATE learning_request SET turn_status = 'failed' WHERE id = ? AND turn_status = 'pending'",
        (request_id,),
    )
    conn.commit()


def _stored_shape_change(raw: Any) -> dict[str, Any] | None:
    """解 `learning_request.shape_change` 那一列；解不开当没有。"""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or not str(data.get("to") or "").strip():
        return None
    return {
        "from": str(data.get("from") or "").strip() or None,
        "to": str(data["to"]).strip(),
        "reason": str(data.get("reason") or "").strip(),
        "decision": data.get("decision") or "pending",
    }


def pending_shape_change(
    conn: sqlite3.Connection, thread_head: int | None
) -> dict[str, Any] | None:
    """线程内**最近一份**还没失效的形态切换提案（F5 复核整改）；没有就 None。

    提案是 chat 轮写在本轮请求行 `shape_change` 列上的一句「我认为该换形态，理由如下」
    （见 `commit_round`）。它的有效期只到**下一次出候选**为止：那之后线程内最近一条
    「有候选的请求」的 id 落在提案后面——无论是按确认切了形态、还是用户保持旧形态
    继续要了新版候选，提案都已经有了答案，再拿它当放行凭据就是陈旧凭据。

    返回 `{"request_id", "from", "to", "reason"}`；`to` 就是确认那一轮要求模型对齐的
    目标形态（main.py 查到提案后把它塞进 thread dict 的 `"expected_shape"`）。
    """
    if thread_head is None:
        return None
    where, params = _thread_rows_where(thread_head)
    row = conn.execute(
        f"SELECT id, shape_change FROM learning_request"
        f" WHERE kind = 'search' AND {where} AND shape_change IS NOT NULL"
        f" ORDER BY id DESC LIMIT 1",
        params,
    ).fetchone()
    if row is None:
        return None
    try:
        data = json.loads(row["shape_change"])
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("to") or data.get("decision") in ("keep", "switch"):
        return None
    where_r, params_r = _thread_rows_where(thread_head, alias="r")
    latest_candidates = conn.execute(
        f"SELECT r.id FROM learning_request r"
        f" JOIN candidate c ON c.request_id = r.id"
        f" WHERE r.kind = 'search' AND {where_r}"
        f" ORDER BY r.id DESC LIMIT 1",
        params_r,
    ).fetchone()
    if latest_candidates is not None and int(latest_candidates["id"]) > int(row["id"]):
        return None  # 提案之后线程里又出过候选：提案已被回答（切了或没切），失效
    return {
        "request_id": int(row["id"]),
        "from": data.get("from"),
        "to": data.get("to"),
        "reason": data.get("reason"),
        "decision": "pending",
    }


def keep_shape_change(
    conn: sqlite3.Connection, thread_id: int, shape_change_request_id: int
) -> dict[str, Any]:
    """Resolve the exact pending proposal; duplicate/stale decisions cannot consume another one."""
    with atomic(conn):
        proposal = pending_shape_change(conn, thread_id)
        if proposal is None or proposal["request_id"] != shape_change_request_id:
            raise ThreadConflict("这条形态切换提案已失效或不属于当前线程，请刷新后重试")
        row = conn.execute(
            "SELECT shape_change FROM learning_request WHERE id = ?", (shape_change_request_id,)
        ).fetchone()
        data = json.loads(row["shape_change"])
        data["decision"] = "keep"
        cursor = conn.execute(
            "UPDATE learning_request SET shape_change = ? WHERE id = ? AND shape_change = ?",
            (json.dumps(data, ensure_ascii=False), shape_change_request_id, row["shape_change"]),
        )
        if not cursor.rowcount:
            raise ThreadConflict("形态切换提案已被另一轮处理，请刷新")
        conn.commit()
    return {
        "thread_id": thread_id,
        "shape_change_request_id": shape_change_request_id,
        "decision": "keep",
        "shape": proposal["from"],
    }


# 已答追问单独进 prompt 的上限（条）。2026-09-26 走查抓到「答过的换个说法又问」：
# 已答清单原来只埋在反馈流水里，会被字符上限从最旧挤掉，也压不过「缺信息就追问」的主指令。
# 现在按计划归属单独取一份（与 `pending_clarify` 同一把尺）进 prompt 当禁区，并在验收里
# 拦下逐字 / 近逐字的重问；换了说法的主题级重问没有便宜的中文判据，硬做会误伤正当追问，
# 靠禁区段压 + 真机走查兜。
ANSWERED_CLARIFIES = 5


def _answered_clarifies(
    conn: sqlite3.Connection,
    plan_id: int | None = None,
    thread_id: int | None = None,
    *,
    thread_scoped: bool = False,
) -> list[tuple[str, str]]:
    """**已经答过**的追问（问题, 回答），时间正序、最多最近几轮。

    两种取法（C4③）：
    - 线程上下文（`thread_scoped=True`）：按线程取（`thread_id = T OR id = T`，含旧头）——
      已答事实跟着对话走，不被页面当前选中的计划带偏；
    - 兼容路径（`thread_scoped=False`）：沿用按计划归属取的旧口径（thread=None 的旧调用方）。

    与 `pending_clarify` 同一把尺：只问没答的不算——那类事实还不成立，下一轮它该继续问，
    不该被当成已答禁区。**先筛已答、再限条数**（I-05）：反过来先截 5 行会把已答的从
    窗口里挤出去，重问就是这么漏进来的。
    """
    if thread_scoped:
        where, params = _thread_rows_where(thread_id)
        rows = conn.execute(
            f"SELECT clarify FROM learning_request"
            f" WHERE kind = 'search' AND {where} AND clarify IS NOT NULL"
            f" ORDER BY id DESC LIMIT {FEEDBACK_SCAN_LIMIT}",
            params,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT clarify FROM learning_request"
            " WHERE kind = 'search' AND plan_id IS ? AND clarify IS NOT NULL"
            f" ORDER BY id DESC LIMIT {FEEDBACK_SCAN_LIMIT}",
            (plan_id,),
        ).fetchall()
    pairs: list[tuple[str, str]] = []
    for row in reversed(rows):  # 时间正序
        stored = _stored_clarify(row["clarify"])
        if stored is not None and stored["answer"]:
            pairs.append((stored["question"], stored["answer"]))
    return pairs[-ANSWERED_CLARIFIES:]  # 筛完才限：留最近几轮已答的


def plan_context(conn: sqlite3.Connection, plan_id: int | None) -> dict[str, Any] | None:
    """这一轮「找」针对的计划：目标 + 当前阶段 + 还开着的任务。

    带上它，候选才贴得上手头的计划（SPEC 决策 33 ① 的后半句）；为空表示「新方向」。
    """
    if plan_id is None:
        return None
    row = plan.resolve_plan(conn, plan_id)
    if row is None:
        raise AdvisorError(f"计划 id={plan_id} 不存在")
    stage = plan.current_stage(conn, int(row["id"]))
    return {
        "plan_id": int(row["id"]),
        "goal": row["goal"],
        "current_stage": None if stage is None else {
            "title": stage["title"],
            "deliverable": stage["deliverable"],
            "open_tasks": plan.stage_completion(conn, int(stage["id"]))["open_titles"],
        },
    }


# ---------- 主链路 ----------

def judge(
    conn: sqlite3.Connection,
    raw_text: str,
    *,
    provider_id: int | None = None,
    model: str | None = None,
    transport: llm.Transport | None = None,
) -> dict[str, Any]:
    """跑一次四问判断。**只回结果，不落库**——落提案由 `propose` 负责。

    不合格就带上"哪里不合格"重试一次；第二次还不合格就抛 `AdvisorError`，
    绝不把一段凑合的输出当成成功。
    """
    profile = read_profile(conn)
    if not profile["items"]:
        raise AdvisorError(
            "长期档案一条都没有，四问没有判据可依——先把档案补上（至少「长期主线」），再来问"
        )

    allowed_ids = {item["id"] for item in profile["items"]}
    messages: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _build_prompt(raw_text, profile)},
    ]

    operation = llm.Operation(conn, TASK, transport=transport)
    text = operation.chat(messages, provider_id=provider_id, model=model)
    judgment, problem = _check(text, allowed_ids)

    attempts = 1
    if problem is not None:
        # 重试一次：把"哪里不合格"原样告诉模型，比让它重新猜有效得多。
        # 这一下用掉第 2 次调用，仍在 Operation 的 3 次闸以内。
        attempts = 2
        messages = [
            *messages,
            {"role": "assistant", "content": text},
            {
                "role": "user",
                "content": f"你上面的输出不合格：{problem}。请只输出合格的 JSON 对象，不要任何解释。",
            },
        ]
        text = operation.chat(messages, provider_id=provider_id, model=model)
        judgment, problem = _check(text, allowed_ids)
        if problem is not None:
            raise AdvisorError(
                f"模型连着 {attempts} 次都没给出合格的四问输出（{problem}）；"
                f"已按上限中止，**没有落任何提案**"
            )

    if judgment is None:  # 理论上到不了：上面两条路径都保证 problem 为 None 时它必有值
        raise AdvisorError("内部状态异常：验收通过却没有解析出判断结果")

    return {
        "judgment": judgment.model_dump(),
        "profile_basis": {
            "total": len(profile["items"]),
            "missing_categories": profile["missing_categories"],
        },
        "attempts": attempts,
        "calls": operation.used,
    }


def propose(
    conn: sqlite3.Connection,
    *,
    request_id: int,
    raw_text: str,
    result: dict[str, Any],
) -> int:
    """把判断结果落成一条**待裁定**提案，返回提案 id。

    走 `ledger.create_active`（而不是直接 INSERT）：提案也是台账里登记过的东西，
    将来 `GET /api/proposals` 与裁定动作都靠这套口径找得到它。
    """
    payload = {
        "source_text": raw_text,
        "learning_request_id": request_id,
        "judgment": result["judgment"],
        "profile_basis": result["profile_basis"],
    }
    basis = result["profile_basis"]["total"]
    return ledger.create_active(
        conn,
        "proposal",
        {
            "kind": MATERIAL_JUDGMENT_KIND,
            "payload": json.dumps(payload, ensure_ascii=False),
            "reason": f"对「{_shorten(raw_text)}」的四问判断（依据 {basis} 条档案）",
        },
        actor="agent",
    )


# ---------- 内部：组 prompt 与验收输出 ----------

def _shorten(text: str, limit: int = 30) -> str:
    """把长输入截短放进 reason：理由是一句话，不该塞进整段原文。"""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[:limit]}…"


def _build_prompt(raw_text: str, profile: dict[str, Any]) -> str:
    lines = [
        "我要判断一个资料值不值得学，请你按四问回答。",
        "",
        "【我的长期档案】方括号里是类别，开头的 #数字 是这条档案的 id（id 只能从这里选）：",
    ]
    lines += [
        f"#{item['id']} [{item['category']}] {item['content']}" for item in profile["items"]
    ]

    if profile["missing_categories"]:
        names = "、".join(PROFILE_CATEGORIES[name] for name in profile["missing_categories"])
        lines += [
            "",
            f"【注意】这几类档案目前是空的：{names}。凡是要靠它们才能判断的，"
            "answer 里请如实说「依据不足」并讲清缺哪类信息，不要拿常理替我做决定。",
        ]

    lines += ["", "【四问与各自主要看哪几类档案】"]
    lines += [
        f"- {title}（键名 {key}）：主要看 " + "、".join(PROFILE_CATEGORIES[name] for name in JUDGE_SOURCES[key])
        for key, title in QUESTIONS.items()
    ]

    lines += [
        "",
        "【这次要判断的东西】",
        raw_text.strip(),
        "",
        "【硬性要求】",
        "- 四问的每一问都要给 profile_item_ids，写清你依据的是上面哪几条档案（写 # 后面的数字），"
        "只能从上面出现过的 id 里选；编一个不存在的 id 会被判为不合格。",
        "- 某一问在上面档案里确实找不到依据时，answer 要明说「依据不足」并说清缺哪类信息，"
        "profile_item_ids 给空数组。宁可说依据不足，也不许编造依据。",
        "- 只输出一个 JSON 对象，不要解释、不要 Markdown 代码块。键固定为这四个："
        + " / ".join(QUESTIONS)
        + '，每个值是 {"answer": "一句话", "profile_item_ids": [数字, ...]}。',
    ]
    return "\n".join(lines)


def _check(text: str, allowed_ids: set[int]) -> tuple[Judgment | None, str | None]:
    """验收模型输出：返回（合格的判断, None）或（None, 不合格的原因）。

    刻意**不抛异常**：不合格的原因要能被拿回去喂给模型重试一次，抛异常就断了这条路。
    """
    data = extract_json(text)
    if data is None:
        return None, "输出不是合法的 JSON 对象"

    try:
        judgment = Judgment.model_validate(data)
    except ValidationError as error:
        # 直接把 Pydantic 的字段级说明透传出去，不另编一套中文（同接口层"两张表里没有的
        # 就回退显示原文"的口径）。
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or '(根)'}: {item['msg']}"
            for item in error.errors()
        )
        return None, f"字段不合格（{details}）"

    cited = {item_id for answer in _answers(judgment) for item_id in answer.profile_item_ids}
    unknown = sorted(cited - allowed_ids)
    if unknown:
        return None, f"引用了不存在的档案 id：{unknown}（只能用我列给你的那些 id）"

    # 「每条答案能指回具体字段」的兜底：没给 id 的，必须明说「依据不足」。
    # 不堵这个口子，模型给四个空数组加四句漂亮话就能过验收——那正是要防的"空泛建议"。
    for key, answer in judgment.model_dump().items():
        if not answer["profile_item_ids"] and "依据不足" not in answer["answer"]:
            return None, (
                f"{key} 既没给 profile_item_ids，也没说「依据不足」——"
                "没有依据的答案不许当合格输出"
            )
    return judgment, None


def _answers(judgment: Judgment) -> list[Answer]:
    return [
        judgment.worth_learning,
        judgment.depth_target,
        judgment.intensity,
        judgment.time_budget,
    ]


def extract_json(text: str) -> dict[str, Any] | None:
    """从模型输出里取出 JSON 对象。

    为什么容一手代码块：模型很爱把 JSON 包在 ```json ... ``` 里，加了这层壳不代表内容错，
    为这个就判不合格纯属浪费一次调用。取第一个 `{` 到最后一个 `}` 之间的内容。

    公开（去掉前导下划线）是因为 `blueprint.py` 的对话与蓝图要用**同一套**取法：
    这条容错口径不该两个模块各写一遍。
    """
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw[:4].lower() == "json":
            raw = raw[4:]

    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None

    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


# ---------- 「找」：候选清单（T13；2026-09-28 双入口整改） ----------
#
# 与四问共用同一套判据（SPEC 第 4 节：「找」与「判」不做两套逻辑），也共用同一条纪律：
# LLM 只产出提案式的结果，落库要经用户裁定；输出先过 schema 校验，不合格带原因重试一次，
# 两次都不合格就如实报错、什么都不落。
#
# 与四问不同的只有两点：
# ① 候选是**一组**而不是一问一答，所以条数（3–5）本身就是校验项；
# ② 有一份**禁区**：已经否决过的候选一个字都不许再出现——这是成功标准 2 的后半句。
#
# 2026-09-28 起按决策 44 重组织（详见本文件「探索线程」一节）：
# ③ 一段连续的「找方向」是一条**探索线程**，追问归属、反馈流水、否决禁区、候选过期、
#    形态（directions/path）都跟着线程走，不再按「同计划最近一条」猜；同计划的另一条
#    线程互不夹带表态（复核整改 F2）；
# ④ 模型输出先报**意图**（chat / need_info / candidates）——寒暄与信息不足不再被逼出
#    一份凑数的清单；
# ⑤ 独立复核后补齐的缺口（F1–F6）：续聊输入的原题由后端从线程头取（不信任客户端）、
#    线程号必须真是线程头、同追问并发回答有原子占用与 TTL、形态切换提案落库核验、
#    只有明确重做（redo）才更替同线程旧候选。

TASK_FIND = "find"

# 深度四档，照抄 SPEC 第 4 节第 ② 问的说法
DEPTH_TARGETS: tuple[str, ...] = ("浅尝", "够用", "熟练", "精通")

# 候选形态，与 `candidate.kind` 列的注释一致
CANDIDATE_KINDS: tuple[str, ...] = ("concept", "doc", "project", "course")

# 条数约束（SPEC 第 9 节成功标准 2：3–5 条）
MIN_CANDIDATES = 3
MAX_CANDIDATES = 5

# 形状（2026-09-20 T34，SPEC 决策 41）：
#   directions —— 几条互相竞争的方向（现状），逐条采纳 / 否决
#   path       —— 一条路（一个伞候选 + 它的几个先后步骤），整条裁定一次
# 为什么要分：用户输入一个明确方向时，「找」回过一条路上的五个先后步骤，却按五个互相竞争的
# 方向摆出来——每条独立采纳/否决，而否决＝永久拉黑。步骤的去留该往后放（蓝图勾选 / 计划跳过），
# 方向层只裁定「这条路走不走」。
SHAPES: tuple[str, ...] = ("directions", "path")

# `path` 下步骤的条数区间（2–8）：给 1 个不叫一条路，给 9 个就不是「找」该管的粒度了
MIN_STEPS = 2
MAX_STEPS = 8

# 反馈流水（SPEC 决策 35 ①）：把最近几轮「找」的结果连着你的表态发进下一轮 prompt。
# 条数与字符上限写成常量，便于按实测调整——候选清单本来就慢（23–27 秒 / 约 5000 token），
# 这段是这轮新增的唯一负担，所以卡得比模型上下文紧得多：宁少说，不多烧。
FEEDBACK_ROUNDS = 5
FEEDBACK_CHAR_LIMIT = 1200

# 先筛后限的扫描宽度（I-05）：先圈出这多的候选轮、滤掉空轮，再取最近 `FEEDBACK_ROUNDS` 轮。
# 为什么不能直接 LIMIT 5：只问没答的轮、刚记下的本轮空行都会把真正有表态的旧轮挤出窗口。
FEEDBACK_SCAN_LIMIT = 40

# 只有这三种状态的候选进反馈流水：`proposed` 是「你还没表态」，喂回去等于让模型
# 自己给自己打分。过期与否决分开说——过期**不是**否决（决策 34），它只是「上一轮不算数了」。
FEEDBACK_STATUSES: tuple[str, ...] = ("accepted", "rejected", "expired")

_VERDICT_LABELS = {
    "accepted": "已采纳",
    "rejected": "已否决",
    "expired": "已过期（我没表态，不算否决）",
}

# 追问槽位里「缺哪类信息」必须点名的词（SPEC 决策 35 ②）。判据刻意宽松：中文名或英文
# token 都算，只要能看出问的是哪一类档案就行。为什么要卡这一条：不点名就成「你想学什么」
# 那种空泛追问——它把问题原样抛回给你，等于什么都没说。
CLARIFY_KEYS: tuple[str, ...] = tuple(
    name
    for token, label in PROFILE_CATEGORIES.items()
    for name in (label.split("（")[0].strip(), token)
)

# 追问只问「关于我的一件事」（2026-09-20 T36，SPEC 决策 41）：一句话能答完，且**不许问
# 「这条路怎么走」**——那是采纳之后规划对话的活。两条判据都能当场查，所以钉在这里而不是
# 只写在提示词里（提示词是请求，校验才是保证）。
CLARIFY_QUESTION_MAX = 60

# 反馈流水里「这轮问过、我也答过」的行首（T36）。写成一个常量是因为校验与测试都要认它，
# 措辞改一处就够。
CLARIFY_ANSWERED_LABEL = "我问过"

# 命中任一即判为「规划对话才该问的」：先学哪个、按什么顺序、分几步、怎么排……
# 刻意只收**明确的规划说法**，不收「先」「节奏」这类单独的词——「你平时的作息节奏」是
# 关于我的事实，不该被这里拦下。
PLAN_TALK_MARKERS: tuple[str, ...] = (
    "怎么走",
    "先学哪",
    "先做哪",
    "先从哪",
    "从哪开始",
    "从哪儿开始",
    "从何处开始",
    "什么顺序",
    "怎么排",
    "怎么安排",
    "分几步",
    "分几个阶段",
    "学习路线",
    "路线怎么",
    "打算怎么",
    "打算先",
    "优先学",
    "优先做",
    "怎么取舍",
    "怎么平衡",
)


class Candidate(BaseModel):
    """一条候选。`why` 与四问的 `answer` 同一条底线：要么指回档案，要么明说依据不足。"""

    title: str = Field(min_length=1)
    kind: Literal["concept", "doc", "project", "course"]
    why: str = Field(min_length=1)
    depth_target: Literal["浅尝", "够用", "熟练", "精通"]
    profile_item_ids: list[int] = Field(default_factory=list)


class Clarify(BaseModel):
    """追问槽位（SPEC 决策 35 ②，2026-09-28 起按决策 44 收进意图出口）。

    `missing` 要说清缺的是哪一类档案信息（校验见 `_clarify_problem`）。
    问出的追问由 `commit_round` 记在本轮请求行上（`learning_request.clarify`），
    下一轮的回答经 `build_round_input` 拼进输入、`commit_round` 标成已答——
    「问过的不再问」靠这份落库记录，不靠当次响应。
    """

    question: str = Field(min_length=1)
    missing: str = Field(min_length=1)


class PathStep(BaseModel):
    """一条路上的一个先后步骤（T34，SPEC 决策 41）。

    形状取蓝图阶段（`BlueprintStage`）的子集：去掉 `tasks`——「这一步下面拆几件活」
    是规划对话与蓝图勾选时的事，「找」只说到「有这一步、它交什么」这一层。
    """

    title: str = Field(min_length=1)
    # 这一步交出什么。可以留空（模型没给），出蓝图那一步会再要一次
    deliverable: str = ""
    # 为什么它必须排在这个位置——路径与方向的区别就在这个「顺序」上
    why: str = Field(min_length=1)

    @field_validator("title", "why")
    @classmethod
    def _require_text(cls, value: str) -> str:
        """只有空白的标题 / 理由不算给了——`min_length=1` 对「  」是放行的。"""
        cleaned = str(value).strip()
        if not cleaned:
            raise ValueError("不能是空白")
        return cleaned

    @field_validator("deliverable")
    @classmethod
    def _clean_deliverable(cls, value: str) -> str:
        return str(value or "").strip()


class ShapeChange(BaseModel):
    """模型请求**切换形态**的说明（II-02）：仅在 `intent="chat"` 那轮合法。

    `from` 是 Python 关键字，字段名用 `from_shape` + alias 承接模型输出里的 `"from"` 键；
    `populate_by_name` 让两种写法都能解析，回传给界面时按 alias 还原成 `"from"`。
    它只是**说明与请求**，不是切换本身——用户确认后那一轮由 main.py 放行
    （`allow_shape_switch`），形态锁才随之移动。
    """

    model_config = ConfigDict(populate_by_name=True)

    from_shape: Literal["directions", "path"] = Field(alias="from")
    to: Literal["directions", "path"]
    reason: str = Field(min_length=1)


class FoundList(BaseModel):
    """一次「找」的输出（2026-09-28 双入口整改：先报意图，再谈清单）。

    `intent` **必填、无默认值**：模型必须自报这一轮是给候选（candidates）、问一句
    （need_info）还是只是说话（chat）——缺了或取值不对就是不合格。没有这个出口，
    「hi」和「每周能投入几小时？」也会被逼出一份凑数的清单（II-01）。

    `shape` 与候选字段只在 `intent="candidates"` 那轮有意义（校验见 `_check_find`）；
    `reply` 在 chat / need_info 轮必须是人话。
    """

    intent: Literal["chat", "need_info", "candidates"]
    reply: str = ""
    shape: Literal["directions", "path"] | None = None
    candidates: list[Candidate] = Field(default_factory=list, max_length=MAX_CANDIDATES)
    steps: list[PathStep] = Field(default_factory=list, max_length=MAX_STEPS)
    recommended_start: str | None = None
    start_reason: str | None = None
    clarify: Clarify | None = None
    shape_change: ShapeChange | None = None


HISTORY_ROUNDS = 8
HISTORY_CHAR_LIMIT = 3000


def _thread_conversation(conn: sqlite3.Connection, thread_id: int | None, exclude_request_id: int | None) -> list[str]:
    """Recent completed user/assistant turns; oldest trimmed first, never cross threads."""
    if thread_id is None:
        return []
    where, params = _thread_rows_where(thread_id)
    rows = conn.execute(
        f"SELECT utterance, raw_text, intent, reply FROM learning_request WHERE kind = 'search' AND {where}"
        " AND id != ? AND (turn_status = 'success' OR turn_status IS NULL)"
        " ORDER BY id DESC LIMIT ?",
        [*params, exclude_request_id or -1, HISTORY_ROUNDS],
    ).fetchall()
    lines = []
    for row in reversed(rows):
        utterance = row["utterance"] if row["utterance"] is not None else row["raw_text"]
        lines.append(f"我：{utterance}")
        if row["reply"]:
            lines.append(f"助手（{row['intent'] or 'chat'}）：{row['reply']}")
    while len("\n".join(lines)) > HISTORY_CHAR_LIMIT and len(lines) > 2:
        lines = lines[2:] if lines[1].startswith("助手") else lines[1:]
    return lines


def _feedback_lines(
    conn: sqlite3.Connection,
    plan_id: int | None = None,
    thread_id: int | None = None,
    exclude_request_id: int | None = None,
    *,
    thread_scoped: bool = False,
) -> list[str]:
    """最近几轮「找」的流水，按时间正序（最早的一轮在前）。

    每行一条请求：时间 / 计划归属 / 这一轮每条候选的标题与裁定结果（否决带理由原文），
    外加**这一轮问过、我也答过的追问**（T36：`我问「…」→ 我答：…`）——它的用处是让模型
    别再问第二遍同一件事。只取 `search` 那一类请求——四问（`evaluate`）记录不进这段
    （决策 35 ①）：那问的是「一份资料值不值得学」，和「别给我推什么方向」不是一回事。
    一轮里既没有一句表态、也没有答过的追问，这一轮就没有可说的，跳过。

    作用域（C4①，I-04/I-05；F2 复核整改）：线程上下文（`thread_scoped=True`）下**一律只取
    本线程**的轮次（`thread_id = T OR id = T`，含旧头）——此前 plan_id 非 None 时按
    「plan_id = ?」圈同计划全部轮次，同计划另一条独立线程的表态会串进当前线程；现在
    计划归属只是行内展示信息，不再是圈选范围。并**排除本轮请求行**（它还没有表态，
    占窗口只会挤掉真有内容的旧轮）。兼容路径（`thread_scoped=False`）沿用旧口径：
    全量最近几轮。两条路径都**先滤空轮、再取最近 5 轮**——「只问没答的把已答挤出
    窗口」的毛病就出在反过来的顺序上。
    """
    if thread_scoped:
        # F2：只圈本线程。plan_id 不参与圈选——同计划不同线程互不夹带，是复核拍板的
        # 有意变更；行内的「计划 #N / 新方向」前缀照旧展示，让模型知道每轮的归属。
        where, params = _thread_rows_where(thread_id)
        where = f"kind = 'search' AND {where}"
        if exclude_request_id is not None:
            where += " AND id != ?"
            params = [*params, exclude_request_id]
        rounds = conn.execute(
            f"SELECT id, plan_id, created_at, clarify FROM learning_request"
            f" WHERE {where} ORDER BY id DESC LIMIT {FEEDBACK_SCAN_LIMIT}",
            params,
        ).fetchall()
        ordered = list(reversed(rounds))  # 时间正序
    else:
        rounds = conn.execute(
            "SELECT id, plan_id, created_at, clarify FROM learning_request"
            " WHERE kind = 'search' ORDER BY id DESC LIMIT ?",
            (FEEDBACK_ROUNDS,),
        ).fetchall()
        ordered = list(reversed(rounds))  # 时间正序

    lines: list[str] = []
    for request in ordered:
        rows = conn.execute(
            f"""SELECT title, status, reject_reason FROM candidate
                WHERE request_id = ? AND status IN ({', '.join('?' * len(FEEDBACK_STATUSES))})
                ORDER BY rank, id""",
            (request["id"], *FEEDBACK_STATUSES),
        ).fetchall()
        items: list[str] = []
        for row in rows:
            verdict = _VERDICT_LABELS.get(str(row["status"]), str(row["status"]))
            reason = str(row["reject_reason"] or "").strip()
            if str(row["status"]) == "rejected" and reason:
                verdict = f"{verdict}：{reason}"  # 理由原文——它比「否决」两个字有用得多
            items.append(f"{row['title']}（{verdict}）")
        asked = _stored_clarify(request["clarify"])
        if asked is not None and asked["answer"]:
            # 追问**问过也答过**才算数：只问了没答的，下一轮它该继续问，不该当成已知事实
            items.append(f"{CLARIFY_ANSWERED_LABEL}「{asked['question']}」→「{asked['answer']}」")
        if not items:
            continue  # 空轮先滤掉，最后才取最近 5 轮（先筛后限）
        scope = "新方向（不属于任何计划）" if request["plan_id"] is None else f"计划 #{request['plan_id']}"
        lines.append(f"- {_short_time(str(request['created_at']))}｜{scope}｜" + "；".join(items))
    if thread_scoped:
        lines = lines[-FEEDBACK_ROUNDS:]  # 滤完空轮才限条数
    return lines


def _feedback_block(
    conn: sqlite3.Connection,
    plan_id: int | None = None,
    thread_id: int | None = None,
    exclude_request_id: int | None = None,
    *,
    thread_scoped: bool = False,
) -> list[str]:
    """反馈流水那一段，已按 `FEEDBACK_CHAR_LIMIT` **从最旧截断**（决策 35 ①）。

    从最新往回收，收不下就丢掉更旧的——越近的表态越该被记住。单行就超上限时
    仍然留它（一条真实表态好过一片空白），这也是这道闸只保证「通常不超」的原因。
    """
    kept: list[str] = []
    used = 0
    for line in reversed(
        _feedback_lines(conn, plan_id, thread_id, exclude_request_id, thread_scoped=thread_scoped)
    ):
        if kept and used + len(line) > FEEDBACK_CHAR_LIMIT:
            break
        kept.insert(0, line)
        used += len(line)
    return kept


def _short_time(iso: str) -> str:
    """把 ISO 时间压成「2026-09-18 10:23」——精确到分钟足够，还省 prompt 字符。"""
    return iso.replace("T", " ")[:16]


def _normalize_title(title: str) -> str:
    """比标题时用的归一化：去首尾空白、去掉中间所有空白、转小写。

    故意不做模糊匹配：判据要能一眼看懂。代价是「学 Python」与「Python 基础」这种
    换了个说法的同一件事仍可能漏过——这条限制写进了交接文档，不在本轮加复杂度。
    """
    return "".join(title.split()).casefold()


def _rejected_titles(
    conn: sqlite3.Connection,
    plan_id: int | None = None,
    thread_id: int | None = None,
    *,
    thread_scoped: bool = False,
) -> list[str]:
    """已经被否决过的候选标题（去重、按 id 序）。

    为什么取 `rejected` 而不是所有历史：候选只有三种终态，`rejected` 才是「你别再推这个」，
    `accepted` 是「这个我要了」——后者不该进禁区（但也不该反复推，交给 prompt 里的档案去说）。

    作用域（C4②，I-04；F2 复核整改）：线程上下文（`thread_scoped=True`）下否决只在
    **本线程**内生效——同计划的另一条线程不被它拦（同计划不同线程互不夹带，有意的行为
    变更：A 线程否掉的方向不再挡 B 线程，此前按「plan_id = ?」圈选会把表态串进同计划
    的所有线程）。「以后都别推」的全局偏好按决策 44 ② 另走可核对的通道，不由旧否决
    静默扩权。兼容路径（`thread_scoped=False`）沿用旧口径：全量禁区。
    """
    if thread_scoped:
        # F2：只圈本线程，plan_id 不参与圈选（与 _feedback_lines 同一把尺）
        where, params = _thread_rows_where(thread_id, alias="r")
        rows = conn.execute(
            f"SELECT DISTINCT c.title FROM candidate c"
            f" JOIN learning_request r ON r.id = c.request_id"
            f" WHERE c.status = 'rejected' AND {where} ORDER BY c.id",
            params,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT DISTINCT title FROM candidate WHERE status = 'rejected' ORDER BY id"
        ).fetchall()
    return [str(row["title"]) for row in rows]


def _brief(
    raw_text: str,
    profile: dict[str, Any],
    banned: list[str],
    feedback: list[str],
    context: dict[str, Any] | None = None,
    clarified: list[tuple[str, str]] | None = None,
    conversation: list[str] | None = None,
) -> Brief:
    """把档案与判据组装成来源层要的输入（见 `providers/find.Brief`）。

    组装留在 advisor 而不是来源层：档案长什么样、哪些类别空着、每档深度看哪几类、
    这一轮针对哪个计划、最近表态过什么、哪些追问已经答过，这些都是「判」的知识；
    来源只管怎么把它讲给模型听。
    """
    plan_lines: list[str] = []
    if context is not None:
        plan_lines.append(f"计划目标：{context['goal']}")
        stage = context["current_stage"]
        if stage is None:
            plan_lines.append("这个计划还没有进行中的阶段（都收尾了或还没建）。")
        else:
            plan_lines.append(f"当前阶段：{stage['title']}")
            if stage["deliverable"]:
                plan_lines.append(f"该阶段要交的东西：{stage['deliverable']}")
            if stage["open_tasks"]:
                plan_lines.append("这个阶段还开着的任务：" + "、".join(stage["open_tasks"]))
    return Brief(
        raw_text=raw_text,
        profile_lines=[
            f"#{item['id']} [{item['category']}] {item['content']}" for item in profile["items"]
        ],
        missing_labels=[PROFILE_CATEGORIES[key] for key in profile["missing_categories"]],
        plan_context_lines=plan_lines,
        feedback_lines=feedback,
        clarified_lines=[
            f"我问「{question}」→ 我答：{answer}" for question, answer in (clarified or [])
        ],
        conversation_lines=conversation or [],
        depth_guide_lines=[
            f"{QUESTIONS[key]}：主要看 "
            + "、".join(PROFILE_CATEGORIES[name] for name in JUDGE_SOURCES[key])
            for key in ("depth_target", "intensity", "time_budget")
        ],
        depth_targets=DEPTH_TARGETS,
        kinds=CANDIDATE_KINDS,
        clarify_keys=CLARIFY_KEYS,
        min_candidates=MIN_CANDIDATES,
        max_candidates=MAX_CANDIDATES,
        min_steps=MIN_STEPS,
        max_steps=MAX_STEPS,
        banned_titles=banned,
    )


def find_candidates(
    conn: sqlite3.Connection,
    raw_text: str,
    *,
    plan_id: int | None = None,
    thread: dict[str, Any] | None = None,
    provider_id: int | None = None,
    model: str | None = None,
    transport: llm.Transport | None = None,
    source: find.Source | None = None,
) -> dict[str, Any]:
    """跑一次「我不知道该学什么」。**只回结果，不落库**——落库由 `propose_candidates` 负责。

    `plan_id` 传了就带该计划的上下文（目标 / 当前阶段 / 还开着的任务）进 prompt，
    候选更贴手头的计划（SPEC 决策 33 ①）；不传 = 「新方向」。

    `thread` 是 `resolve_thread` 的返回（main.py 可以往里塞 `"allow_shape_switch"`、
    `"expected_shape"`、`"redo"` 与 `"request_id"` 四个键）：给了就按**探索线程**装配本轮
    输入（C4）——反馈流水、否决禁区、已答追问与形态锁**一律按线程圈选**（F2 复核整改：
    同计划不同线程互不夹带）；`allow_shape_switch=True` 而 thread 里没有
    `"expected_shape"`（没有待确认提案）→ 直接 `ThreadConflict`（F5：放行凭据是线程里
    那份待确认提案，不是客户端布尔值）；确认那一轮模型给的形态还要与 `"expected_shape"`
    一致，否则判不合格重试。`"redo"` 只透传给结果（F6：落库后更替同线程旧候选与否由
    它管，普通候选轮两版并存）；不传 `thread`（旧调用方）沿用按计划兜底的现状，保证
    接线前旧路由照常工作。

    返回新增 `intent`（chat / need_info / candidates）、`reply` 与 `redo` 三键（II-01/F6）：
    只有 `intent="candidates"` 的结果才带有候选清单，chat / need_info 轮 `candidates` 为空、
    `reply` 必有人话。

    与 `judge` 同一条纪律：不合格带原因重试一次，第二次仍不合格就抛 `AdvisorError`，
    绝不把一份凑合的清单一分成三份端上来。
    """
    profile = read_profile(conn)
    if not profile["items"]:
        raise AdvisorError(
            "长期档案一条都没有，「找」没有判据可依——先去 /profile 补档案"
            "（至少「长期主线」与「短期目标」），再来问"
        )

    context = plan_context(conn, plan_id)
    chosen_source = source or find.DEFAULT_SOURCE
    allowed_ids = {item["id"] for item in profile["items"]}

    redo = False
    expected_shape: str | None = None
    if thread is not None:
        head = thread.get("thread_head")
        # 线程上下文：反馈/禁区/已答/形态锁一律按线程圈选（F2 复核整改）——同计划的
        # 另一条线程不串台；plan_id 只随流水行展示归属，不再是圈选范围
        feedback = _feedback_block(
            conn,
            plan_id,
            head,
            exclude_request_id=thread.get("request_id"),
            thread_scoped=True,
        )
        banned = _rejected_titles(conn, plan_id, head, thread_scoped=True)
        answered = _answered_clarifies(conn, plan_id, head, thread_scoped=True)
        conversation = _thread_conversation(conn, head, thread.get("request_id"))
        lock_shape = _thread_shape(conn, head)
        allow_switch = bool(thread.get("allow_shape_switch"))
        expected_shape = thread.get("expected_shape")
        redo = bool(thread.get("redo"))
        if allow_switch and not expected_shape:
            # F5：形态切换不是客户端布尔值说了算。放行凭据是线程里那份待确认提案，
            # main.py 查不到提案就不会塞 expected_shape——没有凭据就是请求本身错了。
            raise ThreadConflict("没有待确认的形态切换")
    else:
        feedback = _feedback_block(conn)
        banned = _rejected_titles(conn)
        answered = _answered_clarifies(conn, plan_id)
        lock_shape, allow_switch = None, False
        conversation = []

    messages = chosen_source.build_messages(
        _brief(raw_text, profile, banned, feedback, context, clarified=answered, conversation=conversation)
    )

    operation = llm.Operation(conn, TASK_FIND, transport=transport)
    text = operation.chat(messages, provider_id=provider_id, model=model)
    found, problem = _check_find(
        text, allowed_ids, banned, answered_questions=answered,
        lock_shape=lock_shape, allow_shape_switch=allow_switch,
        expected_shape=expected_shape,
    )

    attempts = 1
    if problem is not None:
        # 重试一次：把「哪里不合格」原样告诉模型，比让它重新猜有效得多。
        # 这一下用掉第 2 次调用，仍在 Operation 的 3 次闸以内。
        attempts = 2
        messages = [
            *messages,
            {"role": "assistant", "content": text},
            {
                "role": "user",
                "content": f"你上面的输出不合格：{problem}。请只输出合格的 JSON 对象，不要任何解释。",
            },
        ]
        text = operation.chat(messages, provider_id=provider_id, model=model)
        found, problem = _check_find(
            text, allowed_ids, banned, answered_questions=answered,
            lock_shape=lock_shape, allow_shape_switch=allow_switch,
            expected_shape=expected_shape,
        )
        if problem is not None:
            raise AdvisorError(
                f"模型连着 {attempts} 次都没给出合格的候选清单（{problem}）；"
                "已按上限中止，**没有落任何候选**"
            )

    if found is None:  # 理论上到不了：上面两条路径都保证 problem 为 None 时它必有值
        raise AdvisorError("内部状态异常：验收通过却没有解析出候选清单")

    return {
        "plan_id": None if context is None else context["plan_id"],
        # 意图出口（II-01）：这一轮到底是候选、追问还是说话，由调用方（main.py）分流
        "intent": found.intent,
        "reply": found.reply.strip() or None,
        # 形状（T34）：`directions` = 几条互相竞争的方向，`path` = 一条路；只在 candidates 轮有值
        "shape": found.shape,
        "candidates": [item.model_dump() for item in found.candidates],
        # 步骤草案只在 path 时有内容：它是「这一条路上的先后几步」，随后进伞候选的 payload，
        # 在采纳之后的规划对话里当底稿用（决策 41）
        "steps": [item.model_dump() for item in found.steps],
        "recommended_start": found.recommended_start,
        "start_reason": found.start_reason,
        # 追问记在本轮请求行上（commit_round 落库）；candidates 轮也可带（清单照给）
        "clarify": None if found.clarify is None else found.clarify.model_dump(),
        # 形态切换请求（II-02）：只是说明与请求，切不切由用户确认（main.py 放行）
        "shape_change": (
            None if found.shape_change is None else found.shape_change.model_dump(by_alias=True)
        ),
        # F6：本轮是否按「明确重做」处理——main.py 拿它调 propose_candidates，
        # 只有 redo 轮落新候选才更替同线程旧未裁定候选（普通轮两版并存）
        "redo": redo,
        "source": find.describe(chosen_source),
        "profile_basis": {
            "total": len(profile["items"]),
            "missing_categories": profile["missing_categories"],
        },
        "banned_titles": banned,
        "feedback_lines": feedback,
        "attempts": attempts,
        "calls": operation.used,
    }


_ANSWER_CLARIFY_PUNCT = "？?！!。．，,；;：:、~～·\"'“”‘’"


def _question_key(text: str) -> str:
    """追问比对用的归一化：按标题那套去空白转小写，再去掉标点。

    去标点是为了拦「同一句话加个语气」的重问——「每周能投入几小时？」与
    「每周能投入几小时呢？」只差一个问号的位置，闸眼要把它们看成同一句。
    语气词（呢 / 吗 / 啊）刻意不去：那是正文用词，去了会把比对放宽到误伤别的问句。
    """
    return _normalize_title(text).translate(str.maketrans("", "", _ANSWER_CLARIFY_PUNCT))


def _clarify_problem(
    clarify: Clarify, answered: list[tuple[str, str]] = ()
) -> str | None:
    """追问槽位的验收（决策 35 ②，2026-09-20 T36 收紧，2026-09-26 加第④条闸）。

    四条闸，各堵一种跑偏：
    ① `missing` 要点名哪一类档案信息——堵「你想学什么」这种把问题抛回给我的空泛追问；
    ② **一句话能答**——堵一次问好几件事、把追问写成一段话；
    ③ **不许问「这条路怎么走」**——先学哪个、按什么顺序、怎么排，那是采纳之后规划对话的活，
       在这里问等于让「找」替规划下结论。判据是明确的规划说法（`PLAN_TALK_MARKERS`），
       不是「先」「节奏」这类单独的词——「你平时的作息节奏」仍然是关于我的事实。
    ④ **不许重问已经答过的事**（2026-09-26 走查整改）——逐字 / 近逐字在这里硬拦（判据：
       去空白去标点归一化后相等，或一方是另一方长度 ≥6 的子串）；换了说法的主题级重问
       没有便宜的中文判据，硬做会误伤正当追问，靠 prompt 的禁区段压 + 真机走查兜。

    刻意**不**要求被追问的那一类此刻真的空着：档案里有一条不等于它够用，
    模型想问细一点是正当的。
    """
    if not clarify.question.strip() or not clarify.missing.strip():
        return "clarify 的 question 与 missing 缺一不可——缺一个就别给 clarify"
    if not any(name in clarify.missing for name in CLARIFY_KEYS):
        return (
            f"clarify 的 missing「{clarify.missing}」没说清缺哪类档案信息——"
            f"missing 要点名其中一类：{'、'.join(CLARIFY_KEYS)}。"
            "空泛的追问（例如「你想学什么」）不算合格"
        )

    question = clarify.question.strip()
    if len(question) > CLARIFY_QUESTION_MAX or "\n" in question:
        return (
            f"clarify 的 question 太长了（{len(question)} 字，上限 {CLARIFY_QUESTION_MAX}）——"
            "追问是一句话，问一个我能一句话答完的事实，别写成一段、别一次问好几件事"
        )
    if question.count("？") + question.count("?") > 1:
        return (
            "clarify 的 question 里问号不止一个——一次只问一件关于我的事实，"
            "几件事拆成下一轮再问"
        )
    hit = next((marker for marker in PLAN_TALK_MARKERS if marker in question), None)
    if hit is not None:
        return (
            f"clarify 的 question 问的是「这条路怎么走」（命中了「{hit}」）——"
            "那是采纳之后规划对话该问的（意向、节奏、取舍）。"
            "追问只问关于我的一件事：档案里缺的那类事实，一句话能答完"
        )

    # ④ 已答禁区（2026-09-26）：把回答原文带进报错，重试那一轮模型能直接拿去当已知用。
    new_q = _question_key(question)
    for old_question, old_answer in answered:
        old_q = _question_key(old_question)
        if not old_q:
            continue
        shorter, longer = sorted((old_q, new_q), key=len)
        if shorter == longer or (len(shorter) >= 6 and shorter in longer):
            return (
                f"这个问题我已经答过（我问过「{old_question}」→ 我答：{old_answer}）——"
                "换说法也不许再问第二遍，把我答的内容当已知事实用，问别的"
            )
    return None


def _shape_problem(found: FoundList) -> str | None:
    """形状与它的条数必须对得上（T34，SPEC 决策 41）。

    两种形状各有各的约束，**不许混着写**：
    - `directions`：3–5 条候选，`steps` 必须是空数组（它是 `path` 才用的字段）；
    - `path`：`candidates` 恰好 1 条伞候选，`steps` 给 2–8 个先后步骤。

    为什么 `path` 只允许一条候选：伞候选是「这条路」，步骤是路上的几步——把它们也摆成
    几条候选，否决其中一步就成了永久拉黑，而「这步暂时不做」和「这步永远别给我」完全是
    两件事（步骤的去留在蓝图勾选与计划跳过里）。
    """
    if found.shape == "directions":
        count = len(found.candidates)
        if not MIN_CANDIDATES <= count <= MAX_CANDIDATES:
            return (
                f"shape 是 directions，就要给 {MIN_CANDIDATES}–{MAX_CANDIDATES} 条"
                f"**互相竞争**的方向，现在给了 {count} 条；"
                "如果它们其实是同一条路上的先后步骤，shape 该写 path"
            )
        if found.steps:
            return (
                f"shape 是 directions，steps 必须是空数组（现在给了 {len(found.steps)} 个）——"
                "steps 只在 shape 为 path 时用：那条路只有一条伞候选，步骤放在 steps 里"
            )
        return None

    count = len(found.candidates)
    if count != 1:
        return (
            f"shape 是 path，candidates 只放 **1 条伞候选**（标题是这条路本身），"
            f"现在给了 {count} 条；先后步骤放进 steps，别当成几条互相竞争的方向"
        )
    steps = len(found.steps)
    if not MIN_STEPS <= steps <= MAX_STEPS:
        return (
            f"shape 是 path，steps 要给 {MIN_STEPS}–{MAX_STEPS} 个先后步骤，现在给了 {steps} 个"
        )
    return None


def _check_find(
    text: str,
    allowed_ids: set[int],
    banned: list[str],
    answered_questions: list[tuple[str, str]] = (),
    lock_shape: str | None = None,
    allow_shape_switch: bool = False,
    expected_shape: str | None = None,
) -> tuple[FoundList | None, str | None]:
    """验收模型输出：返回（合格的清单, None）或（None, 不合格的原因）。

    同 `_check`，刻意不抛异常——不合格的原因要能喂回模型重试一次。
    `answered_questions` 是已经答过的追问（问题, 回答），用来拦「换个说法再问一遍」。
    `lock_shape` 是线程的形态锁（II-02）：线程已定过形状，模型再报别的形状判不合格，
    用户确认切换（`allow_shape_switch`）那轮放行。
    `expected_shape` 是确认切换那轮的目标形态（F5）：用户确认的是「切到 X」，模型给的
    必须真是 X——自作主张给成别的形态等于偷换了用户的决定，判不合格带原因重试。

    验收按 `intent` 分派（II-01）：chat / need_info 轮**不落候选**，为凑数硬推的输出
    直接判不合格；candidates 轮走原有全部校验（形状条数 / 依据 id / 起点 / 禁区 / 追问闸）
    再加形态锁与目标形态核对。
    """
    data = extract_json(text)
    if data is None:
        return None, "输出不是合法的 JSON 对象"

    try:
        found = FoundList.model_validate(data)
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or '(根)'}: {item['msg']}"
            for item in error.errors()
        )
        return None, f"字段不合格（{details}）"

    if found.intent != "chat" and found.shape_change is not None:
        return None, "shape_change 只在 intent 为 chat（向用户解释形态冲突）那轮才允许出现"

    if found.intent == "chat":
        # 只是说话（寒暄 / 解释形态冲突）：带候选、带追问都是跑偏——想出候选就明说 candidates
        if found.candidates or found.steps:
            return None, (
                "intent 是 chat（只是聊一聊 / 解释形态冲突），不该给候选或步骤——"
                "要给候选请把 intent 改为 candidates；信息不够就改为 need_info"
            )
        if found.shape is not None:
            return None, "intent 是 chat，shape 必须留空——形状只在出候选那一轮才定"
        if found.clarify is not None:
            return None, "intent 是 chat，不要给 clarify——要问一句请把 intent 改为 need_info"
        if not found.reply.strip():
            return None, "intent 是 chat，reply 必须写一句给我的话，不能是空的"
        return found, None

    if found.intent == "need_info":
        # 信息不够先问一句：这一轮不给候选（II-01/II-03），凑数的清单比没有更糟
        if found.candidates or found.steps:
            return None, (
                "intent 是 need_info（先问一句），candidates 与 steps 必须是空数组——"
                "不要为凑数硬推，信息够了下一轮再出候选"
            )
        if not found.reply.strip():
            return None, "intent 是 need_info，reply 必须写一句说明为什么要问，不能是空的"
        if found.clarify is None:
            return None, "intent 是 need_info，就必须给 clarify——问一句关于我的事实"
        problem = _clarify_problem(found.clarify, answered_questions)
        if problem is not None:
            return None, problem
        return found, None

    # intent == "candidates"：信息足够、在请求推荐——原有全部校验照旧
    if found.shape is None:
        return None, (
            "intent 是 candidates 就必须自报 shape（directions 或 path）——"
            "不自报没法按形状校验条数与步骤"
        )
    problem = _shape_problem(found)
    if problem is not None:
        return None, problem

    # 形态锁（II-02）：线程已定的形状不许模型自己漂移。想换就改输出 intent="chat" 说明
    # 冲突理由，由用户确认——那是「确认那轮」放行（allow_shape_switch）之外唯一的合法路径。
    if lock_shape is not None and found.shape != lock_shape and not allow_shape_switch:
        return None, (
            f"这个探索线程的形态已定为 {lock_shape}；"
            '若你认为新事实推翻了原判断，请改输出 intent="chat" 并说明冲突理由'
            "（可带 shape_change），由用户确认后才能切换，不要自行换形态"
        )

    # F5：确认切换那一轮，模型给的形态必须与提案目标一致——用户确认的是「切到 X」，
    # 模型自作主张给成别的形态，等于偷换了用户的决定。判不合格带原因重试，
    # 原因里点明确认的目标形态是什么。
    if expected_shape is not None and found.shape != expected_shape:
        return None, (
            f"用户已确认这次切换到 {expected_shape} 形态；"
            f"请按 {expected_shape} 的规则重新输出候选"
            "（directions=3–5 条互相竞争的方向；path=1 条伞候选加先后步骤），"
            f"你给的还是 {found.shape}"
        )

    if found.clarify is not None:
        problem = _clarify_problem(found.clarify, answered_questions)
        if problem is not None:
            return None, problem

    cited = {item_id for candidate in found.candidates for item_id in candidate.profile_item_ids}
    unknown = sorted(cited - allowed_ids)
    if unknown:
        return None, f"引用了不存在的档案 id：{unknown}（只能用我列给你的那些 id）"

    # 与四问同一条兜底：没给依据的候选，`why` 里必须明说「依据不足」。
    for candidate in found.candidates:
        if not candidate.profile_item_ids and "依据不足" not in candidate.why:
            return None, (
                f"候选「{candidate.title}」的 why 既没给 profile_item_ids，也没说「依据不足」——"
                "没有依据的推荐不许当合格输出"
            )

    titles = [candidate.title for candidate in found.candidates]
    if not found.recommended_start or found.recommended_start not in titles:
        return None, (
            f"recommended_start「{found.recommended_start}」不是候选之一"
            f"（要一字不差复制某条的 title，现有：{titles}）"
        )
    if not (found.start_reason or "").strip():
        return None, "start_reason 必须写一句话：为什么先从这条开始"

    # 去重的硬保证：禁区里的标题一个字都不许再出现。
    # 为什么是「判不合格重试」而不是「悄悄过滤掉」：过滤会让条数掉到 3 条以下、也不告诉
    # 模型它又推了禁过的；判不合格会把禁区再讲一遍，第二次还犯就如实报错。
    banned_set = {_normalize_title(title) for title in banned}
    repeated = [title for title in titles if _normalize_title(title) in banned_set]
    if repeated:
        return None, f"这些是你以前否决过的，不许再出现：{repeated}（换个方向，别再推它们）"

    # 同批重复（II-04）：一份清单里两三条其实是同一件事，等于拿重复凑条数——判不合格
    # 带原因重试。归一化与禁区同一把尺（去空白转小写）：字面不同的同义改写没有便宜的
    # 中文判据，靠 prompt 的「宁可少给不许凑数」与真机走查兜。
    seen_titles: dict[str, str] = {}
    for title in titles:
        key = _normalize_title(title)
        if key in seen_titles:
            return None, (
                f"同批候选里有两条是同一件事：「{seen_titles[key]}」和「{title}」——"
                "拿重复凑数不算多条；给真正不同的方向，或者少给几条如实说"
            )
        seen_titles[key] = title

    return found, None


def expire_previous_candidates(
    conn: sqlite3.Connection,
    *,
    keep_request_id: int,
    plan_id: int | None,
    thread_id: int | None = None,
) -> list[int]:
    """新一轮「找」落库时，把**还没裁定的**上一版候选标记为过期（SPEC 决策 34）。

    过期 ≠ 否决：`_rejected_titles` 只取 `rejected`，所以过期的不进禁区，模型以后还能再推。

    作用域（C4④，II-03）：`thread_id` 给了就是**同线程版本更替**——本轮落库后同线程内
    其它请求下仍 proposed 的候选过期；跨线程（哪怕同计划）不自动过期，明确重做才更替。
    `thread_id=None`（兼容旧调用方）沿用按计划过期的旧口径。
    """
    if thread_id is not None:
        where, params = _thread_rows_where(thread_id, alias="r")
        rows = conn.execute(
            f"""SELECT c.id FROM candidate c
               JOIN learning_request r ON r.id = c.request_id
               WHERE c.status = 'proposed' AND c.request_id != ?
                 AND {where}""",
            [keep_request_id, *params],
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT c.id FROM candidate c
               JOIN learning_request r ON r.id = c.request_id
               WHERE c.status = 'proposed' AND c.request_id != ?
                 AND r.plan_id IS ?""",
            (keep_request_id, plan_id),
        ).fetchall()
    expired: list[int] = []
    for row in rows:
        ledger.set_status(
            conn,
            "candidate",
            int(row["id"]),
            "expired",
            actor="agent",
            reason=f"新一轮「找」（请求 #{keep_request_id}）落库，上一轮未裁定的候选自动过期",
        )
        expired.append(int(row["id"]))
    return expired


def propose_candidates(
    conn: sqlite3.Connection,
    *,
    request_id: int,
    result: dict[str, Any],
    thread_id: int | None = None,
    redo: bool = False,
) -> list[int]:
    if not result.get("candidates"):
        return []
    with atomic(conn):
        return _propose_candidates_locked(
            conn, request_id=request_id, result=result, thread_id=thread_id, redo=redo
        )


def _propose_candidates_locked(
    conn: sqlite3.Connection,
    *,
    request_id: int,
    result: dict[str, Any],
    thread_id: int | None = None,
    redo: bool = False,
) -> list[int]:
    """把候选清单落成 `proposed` 候选行，返回候选 id（按优先级顺序）。

    走 `ledger.create_active`（同 `propose`）：候选也是台账里登记过的东西，
    将来 `GET /api/candidates` 与裁定动作都靠这套口径找得到它。
    `rank` 就是列表顺序——顺序即优先级，前端不再自己排。

    `path` 形状（T34）落**一行**伞候选，先后步骤存在它的 `payload` 里（新列，与
    `proposal.payload` 同一用法）——步骤不是候选，不单独裁定、不进禁区。

    没有候选的轮（intent 为 chat / need_info）到这里就该是空手：直接返回、**不触发过期**
    ——只有新版候选真的落库，旧版才更替（II-03）；一句寒暄不该把挂着的候选清掉。

    落库的最后一步是**版本更替**（决策 34 + C4④ + F6 复核整改）：`thread_id` 给了时，
    只有 `redo=True`（用户明确要求重做）才做**同线程过期更替**；普通候选轮照常落候选
    但**不动**旧的未裁定候选——两版并存，旧的仍可裁定（已采纳的永不过期，语义不变）。
    此前任何候选轮都更替旧版，模型自报一轮候选就能把用户还没表态的旧清单顶掉。
    `thread_id=None`（兼容旧调用方）沿用按计划过期的旧口径，行为不变。过期不进禁区
    ——过期只是「这轮不算数了」。过期按伞候选走，步骤随 payload 一起失效
    （伞候选过期 = 这条路这轮不算数了）。
    """
    if not result.get("candidates"):
        return []
    is_path = result.get("shape") == "path"
    steps_payload = (
        json.dumps({"shape": "path", "steps": result["steps"]}, ensure_ascii=False)
        if is_path
        else None
    )
    ids: list[int] = []
    for rank, candidate in enumerate(result["candidates"], start=1):
        is_recommended = candidate["title"] == result["recommended_start"]
        reason = f"「找」的第 {rank} 条候选（{result['source']['name']}）"
        if is_path:
            reason = f"「找」给的这一条路（{result['source']['name']}），含 {len(result['steps'])} 个先后步骤"
        if is_recommended:
            reason += f"；建议先从这条开始：{result['start_reason']}"
        ids.append(
            ledger.create_active(
                conn,
                "candidate",
                {
                    "request_id": request_id,
                    "title": candidate["title"],
                    "kind": candidate["kind"],
                    "why": candidate["why"],
                    "depth_target": candidate["depth_target"],
                    "rank": rank,
                    "is_recommended": 1 if is_recommended else 0,
                    "payload": steps_payload,
                },
                actor="agent",
                reason=reason,
            )
        )
    request_row = conn.execute(
        "SELECT plan_id FROM learning_request WHERE id = ?", (request_id,)
    ).fetchone()
    plan_id = None if request_row is None else request_row["plan_id"]
    if thread_id is not None:
        # F6：同线程更替只在明确重做时发生；普通轮两版并存（见 docstring）
        if redo:
            expire_previous_candidates(
                conn, keep_request_id=request_id, plan_id=plan_id, thread_id=thread_id
            )
    else:
        # 旧调用方兜底（thread_id=None）：沿用按计划过期的旧口径
        expire_previous_candidates(
            conn, keep_request_id=request_id, plan_id=plan_id, thread_id=None
        )
    return ids


def list_candidates(
    conn: sqlite3.Connection,
    request_id: int | None = None,
    thread_id: int | None = None,
) -> dict[str, Any]:
    """取某轮「找」的候选清单。不传 `request_id` 就取最近一轮有候选的那次请求。

    `thread_id` 给了（且未指定 `request_id`）就取**该探索线程内**最近一轮有候选的请求
    （C4④的读侧）：恢复会话时按线程找回上一版清单，而不是全局捞最新一轮。
    返回里**带上状态**（`proposed` / `accepted` / `rejected` / `expired`）：界面要能把
    「你已经否掉过哪些」「哪些被新一轮顶掉了」也显示出来——去重是「不再推荐」，
    不是「假装它没发生过」。同时带上这一轮的计划归属（`plan_id`，为空 = 新方向）与
    线程归属（`thread_id`，为空 = 旧记录或未按线程查）。
    """
    if request_id is None and thread_id is not None:
        where, params = _thread_rows_where(thread_id, alias="r")
        latest = conn.execute(
            f"""SELECT c.request_id FROM candidate c
                JOIN learning_request r ON r.id = c.request_id
                WHERE {where} ORDER BY c.id DESC LIMIT 1""",
            params,
        ).fetchone()
        if latest is None:
            # 这条线程还没出过候选：就此打住——不许回头去捞**别的线程**的最新一轮
            return {
                "request_id": None,
                "raw_text": None,
                "plan_id": None,
                "thread_id": thread_id,
                "candidates": [],
                "recommended": None,
            }
        request_id = int(latest["request_id"])
    if request_id is None:
        latest = conn.execute("SELECT request_id FROM candidate ORDER BY id DESC LIMIT 1").fetchone()
        request_id = int(latest["request_id"]) if latest is not None else None
    if request_id is None:
        return {
            "request_id": None,
            "raw_text": None,
            "plan_id": None,
            "thread_id": thread_id,
            "candidates": [],
            "recommended": None,
        }

    request_row = conn.execute(
        "SELECT id, kind, raw_text, plan_id, thread_id, created_at FROM learning_request WHERE id = ?",
        (request_id,),
    ).fetchone()
    rows = conn.execute(
        "SELECT id, title, kind, why, depth_target, rank, is_recommended, status,"
        " reject_reason, payload, landing_plan_id FROM candidate WHERE request_id = ?"
        " ORDER BY rank, id",
        (request_id,),
    ).fetchall()
    candidates = [dict(row) for row in rows]
    plan_id = None if request_row is None else request_row["plan_id"]
    # 线程归属 = 该行自己的 thread_id（即线程头 id）；旧记录是 NULL，就按它自己算
    row_thread_id = None if request_row is None else request_row["thread_id"]
    for item in candidates:
        item["plan_id"] = plan_id  # 候选随请求继承归属（SPEC 决策 33 ①）
        # 形状从 payload 解出来（T34）：老形状的候选 payload 是空的，按 directions 渲染。
        # 原始 JSON 不往外发——界面要的是解好的 steps。
        item["shape"], item["steps"] = _shape_and_steps(item.pop("payload", None))
    recommended = next((item for item in candidates if item["is_recommended"]), None)
    return {
        "request_id": request_id,
        "raw_text": None if request_row is None else request_row["raw_text"],
        "plan_id": plan_id,
        "thread_id": row_thread_id,
        "created_at": None if request_row is None else request_row["created_at"],
        "candidates": candidates,
        "recommended": recommended,
    }


def list_search_requests(
    conn: sqlite3.Connection,
    plan_id: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """读取「找方向」的历史轮次，供前端恢复会话上下文。每行带线程归属（旧记录为 None）。"""
    bounded_limit = max(1, min(int(limit), 200))
    select = "SELECT id, raw_text, utterance, intent, reply, turn_status, plan_id, thread_id, clarify, shape_change, created_at FROM learning_request "
    if plan_id is None:
        rows = conn.execute(
            select + "WHERE kind = 'search' ORDER BY id DESC LIMIT ?",
            (bounded_limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            select + "WHERE kind = 'search' AND plan_id IS ? ORDER BY id DESC LIMIT ?",
            (plan_id, bounded_limit),
        ).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        request_id = int(row["id"])
        candidate_count = conn.execute(
            "SELECT COUNT(*) AS count FROM candidate WHERE request_id = ?",
            (request_id,),
        ).fetchone()["count"]
        result.append(
            {
                "request_id": request_id,
                "raw_text": row["raw_text"],
                "utterance": row["utterance"] if row["utterance"] is not None else row["raw_text"],
                "intent": row["intent"] or ("candidates" if candidate_count else "need_info" if row["clarify"] else None),
                "reply": row["reply"],
                "status": row["turn_status"] or "success",
                "plan_id": row["plan_id"],
                "thread_id": row["thread_id"],
                "created_at": row["created_at"],
                "clarify": _stored_clarify(row["clarify"]),
                # 待确认的形态切换提案（若有）：前端刷新后据此恢复「等用户拍板」的确认条
                "shape_change": _stored_shape_change(row["shape_change"]),
                "candidate_count": int(candidate_count),
                "has_candidates": int(candidate_count) > 0,
            }
        )
    return result


def _shape_and_steps(payload: Any) -> tuple[str, list[dict[str, Any]]]:
    """从候选的 `payload` 那列解出（形状, 步骤草案）。

    解不开、或是老形状的候选（没 payload）一律当 `directions`、步骤为空——真库里躺着
    2026-09-20 之前落的候选，它们本来就都是一条条独立的方向，按 directions 渲染才对。
    公开（去掉前导下划线）是因为 `blueprint.py` 的规划对话区要拿同一份草案给人看。
    """
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return "directions", []
    if not isinstance(data, dict) or data.get("shape") != "path":
        return "directions", []
    steps = data.get("steps")
    return "path", steps if isinstance(steps, list) else []


def candidate_steps(row: sqlite3.Row) -> list[dict[str, Any]]:
    """一条候选自己带的**步骤草案**（T34）——只有 `path` 候选有。

    对话区与规划对话的上下文都用它：草案不是成品，是出蓝图时的底稿（决策 41）。
    """
    return _shape_and_steps(row["payload"])[1]


def decide_candidate(
    conn: sqlite3.Connection,
    candidate_id: int,
    *,
    accept: bool,
    reason: str | None = None,
    plan_id: int | None = None,
) -> dict[str, Any]:
    """采纳或否决一条候选（2026-10-01 成果闭环 OC-05 起：采纳 = 进入规划，不再建阶段）。

    与提案裁定同一条口径（SPEC 第 18 节第 22 条）：候选的「不再算数」由自己的业务终态
    表达（`accepted` / `rejected`），不动台账的生命周期列。否决必须写理由——它同时进
    `reject_reason` 列与台账流水，下次「找」把标题当禁区用。

    采纳（方案 §3.3）只做三件事，**不再调用 plan.add_node**：

    ① 候选标 `accepted`；
    ② 解析并记录**规划落点**（`candidate.landing_plan_id`，T37 的列保留，语义从
       「已建阶段的归属」改为「规划落点」）。「新方向」的候选没有现存 active 计划也能
       采纳——落点为空，正式计划要等蓝图批准时才创建，**绝不新增 plan.status=draft**；
       落点定到某个已有计划的只记归属，不改计划结构（不建阶段、不建任务）。
    ③ 创建（或挂接）这条候选的 `planning_session`，状态 `active`。

    整个采纳包在一个事务（`db.atomic`）里：落点解析不过、会话建不出来，候选都保持
    proposed 可重试，绝不留下「已采纳却没进规划」的半截状态。
    """
    row = conn.execute("SELECT * FROM candidate WHERE id = ?", (candidate_id,)).fetchone()
    if row is None:
        raise CandidateNotFound(f"候选 id={candidate_id} 不存在")
    if row["status"] != "proposed":
        raise CandidateConflict(
            f"候选 id={candidate_id} 已经裁定过了（{row['status']}），不能再改"
        )

    target = "accepted" if accept else "rejected"
    if not accept and not str(reason or "").strip():
        raise AdvisorError("否决必须写明理由——它会被当成禁区，下次「找」不再推荐它")

    target_plan_id: int | None = None
    session_id: int | None = None
    created_session = False
    if accept:
        with atomic(conn):
            # 预检（只读）：落点定不下来就整个不动，候选保持 proposed 可重试
            target_plan_id = planning_landing(conn, row, plan_id)
            session_id, created_session = create_planning_session(conn, candidate_id, target_plan_id)
            ledger.set_status(
                conn,
                "candidate",
                candidate_id,
                target,
                actor="user",
                # 采纳落点并进同一条 UPDATE（T37：落点要留得住）
                extra={"landing_plan_id": target_plan_id},
            )
    else:
        ledger.set_status(
            conn,
            "candidate",
            candidate_id,
            target,
            actor="user",
            reason=reason.strip(),
            # 否决理由进同一条 UPDATE：它同时进 reject_reason 列与台账流水
            extra={"reject_reason": reason.strip()},
        )

    shape, steps = _shape_and_steps(row["payload"])
    return {
        "id": candidate_id,
        "status": target,
        "reject_reason": None if accept else reason,
        # 兼容字段（方案 §6.1）：plan_id 的语义从「已建阶段的归属」改为「规划落点」，
        # 旧客户端仍可读取；「新方向」的采纳它为 None。
        "plan_id": target_plan_id,
        "landing_plan_id": target_plan_id,
        # 兼容字段：采纳不再建阶段，恒为 None——旧前端读到的一律是「没有阶段」。
        "node_id": None,
        # 新流程回执：规划会话与它的状态（方案 §3.3 的响应形状）。
        "planning_session_id": session_id,
        "planning_status": "needs_blueprint" if accept else None,
        "created_planning_session": created_session,
        "message": (
            "已选定这个方向，下一步先把成果和验收标准说清楚；"
            "正式阶段会在蓝图批准后建立。"
            if accept
            else None
        ),
        # 采纳一条路径候选（T34）时把步骤草案一并回带：界面与规划对话要拿它当底稿。
        # 否决时它是空的——否掉的是那条路，没有「剩下的几步」可言。
        "shape": shape,
        "steps": steps if accept else [],
    }


# ---------- 规划会话（成果闭环 OC-05，方案 §5.2 planning_session） ----------
#
# 「候选已采纳、蓝图未批准」的临时规划容器。它不占用计划列表、不参与报告 / 落后量 /
# 收尾，也不新增 plan.status=draft——正式计划与阶段只由蓝图批准建立（OC-07 的原子批准）。

# 无活动期限（天数）：超过它的 active / blueprint_pending 会话可被标成 expired。
# 写成常量便于按实测调整（方案 §5.2：「超出配置的无活动期限进入 expired」）。
PLANNING_SESSION_TTL_DAYS = 14

# 「还活着」的会话状态；其余三个（converted / abandoned / expired）都是终态。
PLANNING_SESSION_ACTIVE: tuple[str, ...] = ("active", "blueprint_pending")

# planning_session 的状态机（方案 §5.2）：正向路径 active → blueprint_pending → converted；
# abandoned / expired 是两个终态，都不允许重新打开。blueprint_pending → active 是
# 「蓝图被驳回 / 被退回对话」的回退边——对话历史不丢，接着聊。
_SESSION_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "active": ("blueprint_pending", "abandoned", "expired"),
    "blueprint_pending": ("active", "converted", "abandoned", "expired"),
    "converted": (),
    "abandoned": (),
    "expired": (),
}


def get_planning_session(conn: sqlite3.Connection, session_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM planning_session WHERE id = ?", (session_id,)
    ).fetchone()


def active_planning_session(conn: sqlite3.Connection, candidate_id: int) -> sqlite3.Row | None:
    """这条候选当前**活着**的规划会话（active 或 blueprint_pending）；没有就 None。"""
    return conn.execute(
        "SELECT * FROM planning_session WHERE candidate_id = ?"
        f" AND status IN ({', '.join('?' * len(PLANNING_SESSION_ACTIVE))})"
        " ORDER BY id DESC LIMIT 1",
        (candidate_id, *PLANNING_SESSION_ACTIVE),
    ).fetchone()


def latest_planning_session(conn: sqlite3.Connection, candidate_id: int) -> sqlite3.Row | None:
    """这条候选**最近一条**规划会话（含终态）；从没进过规划就 None。

    用途：判断一条候选是不是「进过规划」——进过的候选，它的对话一律按会话走，
    不再回落到旧的 (候选, 计划) 线程（免得在会话之外另开一条线程、把树建到落点之外）。
    """
    return conn.execute(
        "SELECT * FROM planning_session WHERE candidate_id = ? ORDER BY id DESC LIMIT 1",
        (candidate_id,),
    ).fetchone()


def planning_landing(
    conn: sqlite3.Connection, candidate: sqlite3.Row, explicit_plan_id: int | None
) -> int | None:
    """采纳时的**规划落点**：显式指定 > 候选自带的归属；都定不下来就是「新方向」→ None。

    与旧 `landing_plan`（严格版）的差别只在最后一档：落不进任何计划**不再报错**——
    方案 §3.3 采纳只进入规划，新方向的候选没有现存 active 计划也能采纳，正式计划在
    蓝图批准时才创建。落点定了的仍当场核对（只读预检）：计划必须存在且进行中——
    往一个已收尾的计划「延续规划」没有意义。

    显式指定与既定事实（候选归属 / 已落定的落点）不一致照旧报错：落点是一个既定事实，
    不是每次调用都能重新表决的。
    """
    landed = candidate["landing_plan_id"]
    if landed is not None:
        if explicit_plan_id is not None and int(landed) != int(explicit_plan_id):
            raise CandidateConflict(
                f"这条候选采纳时已经落进计划 #{landed}，不能改成 #{explicit_plan_id}"
            )
        target: int | None = int(landed)
    else:
        request_row = conn.execute(
            "SELECT plan_id FROM learning_request WHERE id = ?", (candidate["request_id"],)
        ).fetchone()
        inherited = None if request_row is None else request_row["plan_id"]
        if inherited is not None and explicit_plan_id is not None and int(inherited) != int(explicit_plan_id):
            raise CandidateConflict(
                f"这条候选属于计划 #{inherited}，不能落到计划 #{explicit_plan_id}"
            )
        chosen = inherited if inherited is not None else explicit_plan_id
        target = None if chosen is None else int(chosen)

    if target is not None:
        # 当场核对：计划可能在这条候选落进去之后被收尾或作废了
        plan_row = plan.resolve_plan(conn, target)
        if plan_row is None:
            raise CandidateConflict(f"计划 id={target} 不存在")
        if plan_row["status"] != "active":
            raise CandidateConflict(
                f"计划 id={target} 已不是进行中（{plan_row['status']}），不能往里规划"
            )
    return target


def landing_plan(
    conn: sqlite3.Connection, candidate: sqlite3.Row, explicit_plan_id: int | None
) -> int:
    """这条候选的规划落点——**严格版**：落不下来就报错（不许偷偷落最新）。

    供既有的规划对话链路（`blueprint.resolve_plan` 的 legacy 路径）用：那段对话必须挂在
    一个计划名下才能出蓝图。采纳本身的落点解析走 `planning_landing`（允许「新方向」为空）。

    公开（去掉前导下划线）是因为 `blueprint.py` 的对话与蓝图要用**同一套**归属校验：
    采纳落哪个计划、这段对话属于哪个计划，必须是同一个答案，否则蓝图会建到别的计划里。
    """
    target = planning_landing(conn, candidate, explicit_plan_id)
    if target is None:
        raise CandidateConflict(
            "这条候选没有计划归属（「新方向」）——规划对话要按会话（planning_session_id）走，"
            "或先指明落在哪个计划"
        )
    return target


def create_planning_session(
    conn: sqlite3.Connection, candidate_id: int, landing_plan_id: int | None, *, actor: str = "user"
) -> tuple[int, bool]:
    """为一条刚采纳的候选创建（或挂接）规划会话。返回 `(session_id, 是否新建)`。

    走台账 `create_active`：创建动作留流水。同一候选同时只有一条活着的会话——重复采纳
    被候选自己的状态机挡住，这里再兜一层：已有 active / blueprint_pending 的会话就挂接
    它，不重复建（方案 §5.2：候选再次进入规划时创建新的 session，旧 session 只读保留）。
    """
    existing = active_planning_session(conn, candidate_id)
    if existing is not None:
        return int(existing["id"]), False
    now = datetime.now().astimezone()
    timestamp = now.isoformat(timespec="seconds")
    session_id = ledger.create_active(
        conn,
        "planning_session",
        {
            "candidate_id": candidate_id,
            "landing_plan_id": landing_plan_id,
            "status": "active",
            "created_at": timestamp,
            "last_activity_at": timestamp,
            "expires_at": (now + timedelta(days=PLANNING_SESSION_TTL_DAYS)).isoformat(
                timespec="seconds"
            ),
        },
        actor=actor,
        reason="采纳候选，进入规划（蓝图批准前不建正式阶段）",
    )
    return session_id, True


def set_session_status(
    conn: sqlite3.Connection,
    session_id: int,
    new_status: str,
    *,
    reason: str | None = None,
    actor: str = "user",
    extra: dict[str, Any] | None = None,
) -> str:
    """规划会话的状态迁移。只允许 `_SESSION_TRANSITIONS` 表里的边，终态不可重开。

    走台账 `set_status`：业务表与流水一次写入。converted / abandoned / expired 是终态，
    迁过去时把 `closed_at` / `closed_reason` 一并写上——「这个会话什么时候、为什么结束」
    要能回答。同状态重复设置直接返回（幂等，不产生噪音流水）。

    `extra` 是这次迁移同属一回事的附加字段（如新方向批准时回填 `landing_plan_id`），
    并入同一条 UPDATE —— 与 ledger.set_status 的 extra 同一口径。
    """
    row = get_planning_session(conn, session_id)
    if row is None:
        raise AdvisorError(f"规划会话 id={session_id} 不存在")
    current = str(row["status"])
    if current == new_status:
        return current
    if new_status not in _SESSION_TRANSITIONS[current]:
        raise AdvisorError(
            f"规划会话不能从「{current}」变成「{new_status}」——"
            "终态（converted / abandoned / expired）不可重开"
        )
    fields: dict[str, Any] = dict(extra or {})
    if new_status in ("converted", "abandoned", "expired"):
        fields.update({"closed_at": now_iso(), "closed_reason": reason})
    return ledger.set_status(
        conn, "planning_session", session_id, new_status,
        actor=actor, reason=reason, extra=fields or None,
    )


def touch_session(conn: sqlite3.Connection, session_id: int) -> None:
    """用户动作 / 模型成功回复之后，推进规划会话的活动时间（滑动的无活动期限）。

    `last_activity_at` / `expires_at` 是运营账、不是业务状态——同 plan_chat 追加式的
    先例，不经台账（台账回答「状态为什么变」，不回答「最后一句是什么时候说的」）。
    """
    now = datetime.now().astimezone()
    conn.execute(
        "UPDATE planning_session SET last_activity_at = ?, expires_at = ? WHERE id = ?",
        (
            now.isoformat(timespec="seconds"),
            (now + timedelta(days=PLANNING_SESSION_TTL_DAYS)).isoformat(timespec="seconds"),
            session_id,
        ),
    )
    conn.commit()


def reopen_planning_session(conn: sqlite3.Connection, candidate_id: int, *, actor: str = "user") -> dict[str, Any]:
    """给一条**已采纳**的候选重新开一段规划（方案 §5.2：候选再次进入规划时创建新的 session）。

    旧会话（expired / abandoned / converted）一律只读保留，不重开；这段新会话沿用候选
    自己的规划落点（`landing_plan_id`）。已有活着（active / blueprint_pending）的会话时
    幂等返回它，不重复建。

    落点核对与采纳同一条口径：落点计划还在且进行中才继续；计划没了或已收尾就如实报错
    （不悄悄把落点改成「新方向」——那等于替用户重表决了一次落点）。
    """
    row = conn.execute("SELECT * FROM candidate WHERE id = ?", (candidate_id,)).fetchone()
    if row is None:
        raise CandidateNotFound(f"候选 id={candidate_id} 不存在")
    if str(row["status"]) != "accepted":
        raise CandidateConflict(
            f"候选 id={candidate_id} 还没采纳（当前 {row['status']}）——"
            "先采纳它，再谈重新开始规划"
        )

    existing = active_planning_session(conn, candidate_id)
    if existing is not None:
        return {
            "candidate_id": candidate_id,
            "planning_session_id": int(existing["id"]),
            "created": False,
            "landing_plan_id": existing["landing_plan_id"],
            "planning_status": "blueprint_pending"
            if str(existing["status"]) == "blueprint_pending"
            else "needs_blueprint",
        }

    landing = row["landing_plan_id"]
    target: int | None = None
    if landing is not None:
        plan_row = plan.resolve_plan(conn, int(landing))
        if plan_row is None:
            raise CandidateConflict(
                f"这条候选原先的规划落点是计划 #{landing}，但那个计划已经不在了——"
                "重新采纳一条方向，或改用新的规划落点"
            )
        if str(plan_row["status"]) != "active":
            raise CandidateConflict(
                f"这条候选原先的规划落点是计划 #{landing}，它已不是进行中"
                f"（{plan_row['status']}）——先把那边恢复，或重新采纳一条方向"
            )
        target = int(landing)

    session_id, created = create_planning_session(conn, candidate_id, target, actor=actor)
    return {
        "candidate_id": candidate_id,
        "planning_session_id": session_id,
        "created": created,
        "landing_plan_id": target,
        "planning_status": "needs_blueprint",
    }


def expire_stale_sessions(conn: sqlite3.Connection) -> list[int]:
    """把超过无活动期限的 active / blueprint_pending 会话标成 expired（终态）。

    供周期任务调用（本批次只提供入口，不接定时 job）；读取路径上另有一道懒过期
    （`blueprint._ensure_session_alive`），两处最终都走 `set_session_status`。
    过期不是否决：会话只读保留，候选仍是 accepted。
    """
    rows = conn.execute(
        "SELECT id FROM planning_session"
        f" WHERE status IN ({', '.join('?' * len(PLANNING_SESSION_ACTIVE))})"
        " AND expires_at IS NOT NULL AND expires_at < ?",
        (*PLANNING_SESSION_ACTIVE, now_iso()),
    ).fetchall()
    expired: list[int] = []
    for row in rows:
        try:
            set_session_status(
                conn,
                int(row["id"]),
                "expired",
                reason="超过无活动期限，规划会话自动过期",
                actor="agent",
            )
        except AdvisorError:
            continue  # 并发下别路刚处理过：不重复留痕
        expired.append(int(row["id"]))
    return expired
