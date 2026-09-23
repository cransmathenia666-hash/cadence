"""记忆系统：三层记忆的读写、候选契约、经历检索与彻底删除（方案 `docs/记忆系统.md`）。

**三层记忆各自落在哪**（方案第 2 节）：

- **工作记忆**：沿用 `agent_runtime` 这一轮的目标、工具结果与剩余额度，运行结束即清空。
  本模块不碰它——第一版不做中断续跑，它不需要落表。
- **经历记忆**：**不复制数据**。计划对话、规划对话、报告、候选裁定、提案裁定、节点字段
  变更这六类记录本来就在库里；`search_experiences` 每次现查现拼（本模块只读、不建第二份
  经历表）。排序是确定性的（时间 + 来源 + 编号），第一版不引向量库或 Embedding。
- **长期记忆**：分两级——全局的沿用 `profile_item`（并补上事实时间 / 复核时间 / 来源性质
  三列元数据），计划内的存 `plan_memory`。**只有这一层会污染判断**，所以只有它需要批准、
  需要来源证据、需要复核到期。

**两条铁律**（方案第 3、4 节）：

1. **系统只产候选，写入账本要用户点头**。`scan` 的产出全部落成 `kind=memory_change` 的
   待裁定提案（复用 `proposal` 表，不另立队列表）；`add` 且属于用户明确陈述的可以**批量**
   批准，取代 / Agent 推断 / 彻底删除一律逐条确认。
2. **确定性校验先于落库**：来源存在、摘录能在来源里一字不差找到、取代目标仍有效、
   没有完全重复——四条任一不过就判不合格、不落候选。近似重复**只提示、不自动合并**
   （相似度用标准库的 `difflib`，确定且没有新依赖）。

**彻底删除**（方案第 8 节）：先给影响预览、再二次确认；清掉记忆正文、候选 payload 与证据
摘录，并按证据关系把来源里的原句替换成一句「已按用户要求删除」（来源行另有业务意义时只
替换那一句，状态与时间保留）。清完**全库复扫一遍**，证明不了全部副本都清掉就拒绝宣称成功
——「看起来删了」比「没删」更危险。最后只留不含内容的墓碑。

**记忆变化感知**（2026-09-21 走查整改第 3 条，见本文件「三·五」一节）：模型每轮从零决定
读什么，没人告诉它「记忆库自你上次读之后变过」，它就沿用历史里的旧判断。补法是**增量只用来
生成一句摘要行、依据仍靠整份重读**：水印从 `agent_run` 取（不加列），变化清单从台账流水与
墓碑表取，范围按全局 / 计划切。
"""

from __future__ import annotations

import difflib
import json
import sqlite3
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from . import advisor, blueprint as blueprint_mod, ledger, llm
from .db import now_iso

# ---------- 常量：作用域、取值、类别 ----------

SCOPE_GLOBAL = "global"
SCOPE_PLAN = "plan"
SCOPES = (SCOPE_GLOBAL, SCOPE_PLAN)

# 来源性质：用户明说的 / Agent 推断的 / 记忆系统落地之前的历史手工条目。
# 只有前两种能由模型写进候选——`legacy_manual` 是给老档案补的标记，不是可提交的取值。
SOURCE_KINDS: dict[str, str] = {
    "user_stated": "用户陈述",
    "agent_inferred": "Agent 推断",
    "legacy_manual": "历史手工录入",
}
CANDIDATE_SOURCE_KINDS = ("user_stated", "agent_inferred")

# 计划内记忆的三种内容：约束 / 决定 / 偏好（全局那一级仍用档案的五个类别）。
MEMORY_KINDS: dict[str, str] = {
    "constraint": "约束",
    "decision": "决定",
    "preference": "偏好",
}

# 候选的三种动作与「待复核」的两种处置。
ACTION_ADD = "add"
ACTION_SUPERSEDE = "supersede"
ACTION_REVIEW = "review"
ACTIONS: dict[str, str] = {
    ACTION_ADD: "新增",
    ACTION_SUPERSEDE: "取代",
    ACTION_REVIEW: "复核",
}
REVIEW_DECISIONS: dict[str, str] = {"renew": "续期", "void": "作废"}

# 落进 `proposal.kind` 的取值（记忆候选队列复用提案表，方案第 3 节第 5 条）。
KIND = "memory_change"

# 经历六类：与 `memory_evidence.source_type` 一一对应。
# `EXPERIENCE_SOURCES` 是令牌 → 中文名；反过来的别名表（`SOURCE_TYPE_ALIASES`）让模型
# 写中文也认——2026-09-21 走查里它把类别写成「当前状态」被判错，同一类毛病。
EXPERIENCE_SOURCES: dict[str, str] = {
    "plan_dialogue": "计划对话",
    "plan_chat": "规划对话",
    "report": "报告",
    "candidate": "候选裁定",
    "proposal": "提案裁定",
    "field_change": "计划变更",
}
SOURCE_TYPE_ALIASES: dict[str, str] = {label: name for name, label in EXPERIENCE_SOURCES.items()}


def resolve_source_type(value: Any) -> str | None:
    """来源类型：令牌与中文名都收。认不出来给 `None`（由调用方报错）。"""
    text = "".join(str(value or "").split())
    if text in EXPERIENCE_SOURCES:
        return text
    return SOURCE_TYPE_ALIASES.get(text)


def source_type_error(value: Any) -> str:
    """来源类型不认时的**人话**报错——不再把英文令牌串甩进「本轮依据」。"""
    return (
        f"来源类型只能是 {' / '.join(EXPERIENCE_SOURCES.values())} 之一"
        f"（你写的是「{value}」）"
    )

# ---------- 常量：闸门与上限 ----------

# 复核默认周期：到期后进「待复核」，Agent 默认不再使用；续期就再推这么多天。
REVIEW_DAYS = 90

# 近似重复的提示门槛（difflib 相似度）。**只提示**——自动合并会悄悄改掉用户说过的话。
NEAR_DUPLICATE_RATIO = 0.8

# 经历检索一次返回几条（方案第 5 节：最多 8 条）。
SEARCH_LIMIT = 8

# 一批扫描最多看多少条经历、总共多少字符；超出的留到下一批（游标只推进已处理的部分）。
SCAN_BATCH_EXPERIENCES = 20
SCAN_BATCH_CHARS = 8000

# 一批扫描最多落几条候选，以及模型调用次数（1 次 + 不合格带原因重试 1 次，老规矩）。
MAX_SCAN_CANDIDATES = 10
SCAN_CALL_LIMIT = 2

# 扫描的三种触发（与 `memory_scan.trigger` 对应）。
TRIGGER_MANUAL = "manual"
TRIGGER_WEEKLY = "weekly"
TRIGGER_PLAN_CLOSE = "plan_close"
TRIGGER_LABELS: dict[str, str] = {
    TRIGGER_MANUAL: "手动扫描",
    TRIGGER_WEEKLY: "每周扫描",
    TRIGGER_PLAN_CLOSE: "计划收尾",
}

# 彻底删除时替换原句用的那句话：来源行仍要保住它自己的状态与时间，只把这一句换掉。
PURGED_TEXT = "已按用户要求删除"


class MemoryError(RuntimeError):
    """记忆链路上的明确错误（接口层翻成 400）。"""


class MemoryNotFound(MemoryError):
    """记忆 / 计划 / 来源不存在 → 接口层翻成 404。"""


class MemoryConflict(MemoryError):
    """与现状冲突（目标已不是当前有效值、已经有完全一样的条目）→ 409。"""


# ============================================================
# 一、读记忆
# ============================================================

def _today() -> str:
    return date.today().isoformat()


def _table_of(scope: str) -> str:
    """全局记忆住在 `profile_item`，计划内记忆住在 `plan_memory`。"""
    if scope == SCOPE_GLOBAL:
        return "profile_item"
    if scope == SCOPE_PLAN:
        return "plan_memory"
    raise MemoryError(f"作用域「{scope}」不认识；只有 {SCOPE_GLOBAL}（全局）与 {SCOPE_PLAN}（计划内）")


def _review_due(row: sqlite3.Row, today: str | None = None) -> bool:
    """到没到复核时间。没写复核时间的永不到期（不是「立刻到期」）。"""
    review_at = row["review_at"]
    if not review_at:
        return False
    return str(review_at)[:10] <= (today or _today())


def evidence_of(conn: sqlite3.Connection, scope: str, memory_id: int) -> list[dict[str, Any]]:
    """一条长期记忆的来源证据，按编号排（先记的先列）。"""
    rows = conn.execute(
        "SELECT * FROM memory_evidence WHERE scope = ? AND memory_id = ? ORDER BY id",
        (scope, memory_id),
    ).fetchall()
    return [
        {
            "source_type": str(row["source_type"]),
            "source_label": EXPERIENCE_SOURCES.get(str(row["source_type"]), str(row["source_type"])),
            "source_id": int(row["source_id"]),
            "excerpt": str(row["excerpt"]),
            "source_time": row["source_time"],
        }
        for row in rows
    ]


def public_memory(
    conn: sqlite3.Connection, scope: str, row: sqlite3.Row, today: str | None = None
) -> dict[str, Any]:
    """一条记忆 → 给界面看的形状（类别 / 来源性质的中文名由后端算好，前端只显示）。"""
    is_global = scope == SCOPE_GLOBAL
    return {
        "id": int(row["id"]),
        "scope": scope,
        "scope_label": "全局" if is_global else "计划内",
        "plan_id": None if is_global else int(row["plan_id"]),
        "category": str(row["category"]) if is_global else None,
        "category_label": (
            advisor.PROFILE_CATEGORIES.get(str(row["category"]), str(row["category"]))
            if is_global
            else None
        ),
        "kind": None if is_global else str(row["kind"]),
        "kind_label": None if is_global else MEMORY_KINDS.get(str(row["kind"]), str(row["kind"])),
        "content": str(row["content"]),
        "source_kind": row["source_kind"],
        "source_kind_label": SOURCE_KINDS.get(str(row["source_kind"] or ""), "来源不明"),
        "fact_time": row["fact_time"],
        "review_at": row["review_at"],
        "review_due": _review_due(row, today),
        "status": str(row["status"]),
        "valid_from": row["valid_from"],
        "created_at": row["created_at"],
        "evidence": evidence_of(conn, scope, int(row["id"])),
    }


def active_memories(
    conn: sqlite3.Connection, scope: str, *, plan_id: int | None = None
) -> list[sqlite3.Row]:
    """当前有效的记忆。计划那一级必须给 `plan_id`——**读不到别的计划**。"""
    if scope == SCOPE_GLOBAL:
        return ledger.fetch_active(conn, "profile_item")
    if plan_id is None:
        raise MemoryError("读计划内记忆必须说明是哪个计划")
    return ledger.fetch_active(conn, "plan_memory", plan_id=plan_id)


def list_memories(
    conn: sqlite3.Connection, *, plan_id: int | None = None
) -> dict[str, Any]:
    """这个页面的主取数：全局记忆、这个计划的记忆、以及两边「已到复核时间」的那些。

    到期的**同时列在这里**（页面上的「待复核」标签页用它），但**默认不参与回答**——
    这一点由 `read_memories` 工具兑现（它把到期的单独标出来、不放进默认结果）。
    """
    today = _today()
    global_rows = active_memories(conn, SCOPE_GLOBAL)
    plan_rows = active_memories(conn, SCOPE_PLAN, plan_id=plan_id) if plan_id else []
    global_items = [public_memory(conn, SCOPE_GLOBAL, row, today) for row in global_rows]
    plan_items = [public_memory(conn, SCOPE_PLAN, row, today) for row in plan_rows]
    due = [item for item in [*global_items, *plan_items] if item["review_due"]]
    return {
        "today": today,
        "plan_id": plan_id,
        "global": global_items,
        "plan": plan_items,
        "due": due,
        "counts": {
            "global": len(global_items),
            "plan": len(plan_items),
            "due": len(due),
        },
    }


def read_memories(conn: sqlite3.Connection, plan_id: int) -> dict[str, Any]:
    """给 Agent 用的分区读法（方案第 5 节）：**到复核时间的单独标出、不进默认结果**。"""
    listing = list_memories(conn, plan_id=plan_id)
    keep = lambda items: [item for item in items if not item["review_due"]]  # noqa: E731
    return {
        "global": keep(listing["global"]),
        "plan": keep(listing["plan"]),
        "due": listing["due"],
        "due_count": len(listing["due"]),
    }


# ============================================================
# 二、写记忆（三条用户直连的写路径，全走台账）
# ============================================================

def _default_review_at() -> str:
    return (date.today() + timedelta(days=REVIEW_DAYS)).isoformat()


def _iso_date(value: Any, field: str) -> str | None:
    """日期只收零填充的 ISO 写法（与接口层 `due_date` 同一口径）。空值表示「不设」。"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise MemoryError(f"{field} 要写成 2026-09-21 这样的日期，收到的是「{text}」") from None


def _require_plan(conn: sqlite3.Connection, plan_id: Any) -> int:
    try:
        plan_id = int(plan_id)
    except (TypeError, ValueError):
        raise MemoryError("计划内记忆必须说明是哪个计划") from None
    row = conn.execute("SELECT id FROM plan WHERE id = ?", (plan_id,)).fetchone()
    if row is None:
        raise MemoryNotFound(f"计划 id={plan_id} 不存在")
    return plan_id


def add_memory(
    conn: sqlite3.Connection,
    *,
    scope: str,
    content: str,
    plan_id: int | None = None,
    category: str | None = None,
    kind: str | None = None,
    source_kind: str = "user_stated",
    fact_time: str | None = None,
    review_at: str | None = None,
    reason: str | None = None,
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """新增一条长期记忆。全局走 `profile_item`、计划内走 `plan_memory`，都经台账留痕。

    用户自己敲进来的这条**不要求**来源证据（他本人就是来源）；模型提的候选才必须带——
    这条分界线在 `validate_candidate` 里，不在这里。
    """
    table = _table_of(scope)
    cleaned = str(content or "").strip()
    if not cleaned:
        raise MemoryError("记忆内容不能为空")
    if scope == SCOPE_GLOBAL:
        if category not in advisor.PROFILE_CATEGORIES:
            raise MemoryError(
                f"全局记忆的类别「{category}」不在约定的五个令牌里："
                f"{' / '.join(advisor.PROFILE_CATEGORIES)}"
            )
    else:
        plan_id = _require_plan(conn, plan_id)
        kind = str(kind or "")
        if kind not in MEMORY_KINDS:
            raise MemoryError(
                f"计划内记忆的类别「{kind}」不认识，只能给：{' / '.join(MEMORY_KINDS)}"
            )
    if source_kind not in SOURCE_KINDS:
        raise MemoryError(f"来源性质「{source_kind}」不认识，只能给：{' / '.join(SOURCE_KINDS)}")

    _reject_exact_duplicate(conn, scope, content=cleaned, plan_id=plan_id, table=table)
    values: dict[str, Any] = {
        "content": cleaned,
        "source_kind": source_kind,
        "fact_time": _iso_date(fact_time, "事实时间"),
        "review_at": _iso_date(review_at, "复核时间"),
    }
    if scope == SCOPE_GLOBAL:
        values["category"] = category
    else:
        values["plan_id"] = plan_id
        values["kind"] = kind
    memory_id = ledger.create_active(conn, table, values, actor="user", reason=reason)
    _attach_evidence(conn, scope, memory_id, evidence or [])
    return public_memory(conn, scope, _row_of(conn, scope, memory_id))


def supersede_memory(
    conn: sqlite3.Connection,
    scope: str,
    memory_id: int,
    *,
    content: str,
    reason: str,
    fact_time: str | None = None,
    review_at: str | None = None,
    source_kind: str | None = None,
) -> dict[str, Any]:
    """改一条记忆：走台账「取代」，旧值留痕、理由必填（与档案的「改」同一条路）。"""
    table = _table_of(scope)
    row = _require_active(conn, scope, memory_id)
    cleaned = str(content or "").strip()
    if not cleaned:
        raise MemoryError("记忆内容不能为空")
    if not str(reason or "").strip():
        raise MemoryError("取代必须写明理由，否则台账回答不了「为什么改」")
    _reject_exact_duplicate(conn, scope, content=cleaned, plan_id=row["plan_id"] if scope == SCOPE_PLAN else None, table=table)
    values: dict[str, Any] = {"content": cleaned}
    if fact_time is not None:
        values["fact_time"] = _iso_date(fact_time, "事实时间")
    if review_at is not None:
        values["review_at"] = _iso_date(review_at, "复核时间")
    if source_kind is not None:
        if source_kind not in SOURCE_KINDS:
            raise MemoryError(f"来源性质「{source_kind}」不认识")
        values["source_kind"] = source_kind
    new_id = ledger.supersede(conn, table, memory_id, values, reason=str(reason).strip())
    # 取代出来的新条目**继承旧条目的证据**：不继承，来源一栏会凭空空掉。
    _copy_evidence(conn, scope, memory_id, new_id)
    return public_memory(conn, scope, _row_of(conn, scope, new_id))


def void_memory(conn: sqlite3.Connection, scope: str, memory_id: int, *, reason: str) -> None:
    """作废一条记忆：从此不参与判断，旧值仍留在台账里（普通纠错走这条，留痕）。"""
    if not str(reason or "").strip():
        raise MemoryError("作废必须写明理由")
    _require_active(conn, scope, memory_id)
    ledger.void(conn, _table_of(scope), memory_id, reason=str(reason).strip())


def renew_memory(
    conn: sqlite3.Connection,
    scope: str,
    memory_id: int,
    *,
    review_at: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """「待复核」里选「还作数」：只把复核时间往后推，正文一个字不动。

    它**不产生新行**（不是取代）：内容没变，变的是「什么时候再回头看它一次」。
    """
    _require_active(conn, scope, memory_id)
    table = _table_of(scope)
    next_at = _iso_date(review_at, "复核时间") or _default_review_at()
    conn.execute(f"UPDATE {table} SET review_at = ? WHERE id = ?", (next_at, memory_id))
    ledger.log_event(
        conn,
        _entity_type_of(scope),
        memory_id,
        "review",
        None,
        next_at,
        str(reason or "").strip() or f"复核后延长到 {next_at}",
        "user",
    )
    conn.commit()
    return public_memory(conn, scope, _row_of(conn, scope, memory_id))


def _entity_type_of(scope: str) -> str:
    return "profile_item" if scope == SCOPE_GLOBAL else "plan_memory"


def _row_of(conn: sqlite3.Connection, scope: str, memory_id: int) -> sqlite3.Row:
    row = conn.execute(
        f"SELECT * FROM {_table_of(scope)} WHERE id = ?", (int(memory_id),)
    ).fetchone()
    if row is None:
        raise MemoryNotFound(f"记忆 id={memory_id} 不存在")
    return row


def _require_active(conn: sqlite3.Connection, scope: str, memory_id: int) -> sqlite3.Row:
    """取一条**当前有效**的记忆。已作废 / 已被取代的是 409（不是 404）——它存在过。"""
    row = _row_of(conn, scope, memory_id)
    if str(row["status"]) != "active":
        raise MemoryConflict(
            f"记忆 id={memory_id} 已不是当前有效值（{row['status']}），只能改动当前有效的那些"
        )
    return row


def _target_of(conn: sqlite3.Connection, payload: dict[str, Any]) -> sqlite3.Row:
    """取出候选要动的那条记忆，并**核对它属于候选说的那个计划**（计划隔离）。"""
    scope = str(payload.get("scope"))
    target = _require_active(conn, scope, int(payload.get("target_id") or 0))
    if (
        scope == SCOPE_PLAN
        and payload.get("plan_id") is not None
        and int(target["plan_id"]) != int(payload["plan_id"])
    ):
        raise MemoryConflict(
            f"记忆 id={target['id']} 不属于计划 {payload['plan_id']}"
            "——计划内记忆只在这个计划里"
        )
    return target


# ---------- 判重：完全一样就拒，像但不等于只提示 ----------

def _normalize(text: str) -> str:
    return "".join(str(text or "").split()).lower()


def _active_rows_of_scope(
    conn: sqlite3.Connection, scope: str, plan_id: int | None, table: str
) -> list[sqlite3.Row]:
    sql = f"SELECT * FROM {table} WHERE status = 'active'"
    params: list[Any] = []
    if scope == SCOPE_PLAN:
        sql += " AND plan_id = ?"
        params.append(plan_id)
    return list(conn.execute(sql, tuple(params)))


def _reject_exact_duplicate(
    conn: sqlite3.Connection,
    scope: str,
    *,
    content: str,
    plan_id: int | None,
    table: str,
    exclude_id: int | None = None,
) -> None:
    """同作用域下**一字不差**的当前有效条目不许再有第二条（同档案判重那道闸）。

    `exclude_id` 给「取代」用：被取代的那条自己当然不能算重复。
    """
    for row in _active_rows_of_scope(conn, scope, plan_id, table):
        if exclude_id is not None and int(row["id"]) == int(exclude_id):
            continue
        if _normalize(row["content"]) == _normalize(content):
            raise MemoryConflict(
                f"记忆里已经有这条：#{row['id']}（一字不差）。不用再补；"
                "想改它就用「改」，不想让它算数就「作废」"
            )


def duplicate_hint(
    conn: sqlite3.Connection,
    scope: str,
    *,
    content: str,
    plan_id: int | None,
    exclude_id: int | None = None,
) -> str | None:
    """**疑似**与某条重复时给一句提示。近似重复只能提示，不许自动合并。

    合并等于替用户改口——「三个月内不碰新框架」和「近期不碰新框架」是不是一回事，
    只有他知道。
    """
    mine = _normalize(content)
    best: tuple[float, int] | None = None
    for row in _active_rows_of_scope(conn, scope, plan_id, _table_of(scope)):
        if exclude_id is not None and int(row["id"]) == int(exclude_id):
            continue
        other = _normalize(row["content"])
        if not other or other == mine:
            continue
        ratio = difflib.SequenceMatcher(None, mine, other).ratio()
        if ratio >= NEAR_DUPLICATE_RATIO and (best is None or ratio > best[0]):
            best = (ratio, int(row["id"]))
    if best is None:
        return None
    return f"疑似与 #{best[1]} 重复（像到 {int(best[0] * 100)}%）——要不要合成一条，你定"


# ---------- 证据 ----------

def _attach_evidence(
    conn: sqlite3.Connection, scope: str, memory_id: int, evidence: list[dict[str, Any]]
) -> None:
    """挂来源证据。摘录必须**一字不差**能在来源里找到——由 `validate_candidate` 先验过。"""
    for item in evidence:
        conn.execute(
            """INSERT INTO memory_evidence
               (scope, memory_id, source_type, source_id, excerpt, source_time, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                scope,
                int(memory_id),
                str(item["source_type"]),
                int(item["source_id"]),
                str(item["excerpt"]),
                item.get("source_time"),
                now_iso(),
            ),
        )
    conn.commit()


def _copy_evidence(conn: sqlite3.Connection, scope: str, old_id: int, new_id: int) -> None:
    for item in evidence_of(conn, scope, old_id):
        conn.execute(
            """INSERT INTO memory_evidence
               (scope, memory_id, source_type, source_id, excerpt, source_time, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                scope,
                int(new_id),
                item["source_type"],
                item["source_id"],
                item["excerpt"],
                item["source_time"],
                now_iso(),
            ),
        )
    conn.commit()


# ============================================================
# 三、经历检索（六类现查现拼，不建第二份表）
# ============================================================

def _dialogue_experiences(conn: sqlite3.Connection, plan_id: int | None) -> list[dict[str, Any]]:
    sql = "SELECT id, plan_id, role, content, created_at FROM plan_dialogue"
    params: tuple[Any, ...] = ()
    if plan_id is not None:
        sql += " WHERE plan_id = ?"
        params = (plan_id,)
    return [
        {
            "source_type": "plan_dialogue",
            "source_id": int(row["id"]),
            "plan_id": int(row["plan_id"]),
            "date": str(row["created_at"]),
            "text": f"{'我' if str(row['role']) == 'user' else '它'}：{row['content']}",
        }
        for row in conn.execute(sql + " ORDER BY id", params)
    ]


def _chat_experiences(conn: sqlite3.Connection, plan_id: int | None) -> list[dict[str, Any]]:
    """规划对话。助手那一侧存的是 JSON 原文，喂出去前先渲染成人话（与蓝图同一条渲染）。"""
    sql = "SELECT id, plan_id, role, content, created_at FROM plan_chat"
    params: tuple[Any, ...] = ()
    if plan_id is not None:
        sql += " WHERE plan_id = ?"
        params = (plan_id,)
    out: list[dict[str, Any]] = []
    for row in conn.execute(sql + " ORDER BY id", params):
        is_user = str(row["role"]) == "user"
        text = str(row["content"]) if is_user else blueprint_mod.render_reply(str(row["content"]))
        out.append(
            {
                "source_type": "plan_chat",
                "source_id": int(row["id"]),
                "plan_id": int(row["plan_id"]),
                "date": str(row["created_at"]),
                "text": f"{'我' if is_user else '它'}：{text}",
            }
        )
    return out


def _report_experiences(conn: sqlite3.Connection, plan_id: int | None) -> list[dict[str, Any]]:
    sql = (
        "SELECT r.id, r.status, r.note, r.artifact_url, r.material_feedback, r.created_at,"
        " n.title, n.plan_id FROM report r JOIN plan_node n ON n.id = r.node_id"
    )
    params: tuple[Any, ...] = ()
    if plan_id is not None:
        sql += " WHERE n.plan_id = ?"
        params = (plan_id,)
    out: list[dict[str, Any]] = []
    for row in conn.execute(sql + " ORDER BY r.id", params):
        text = f"报告「{row['title']}」{row['status']}：{row['note'] or '（没写说明）'}"
        if row["artifact_url"]:
            text += f"｜产物 {row['artifact_url']}"
        if row["material_feedback"]:
            text += f"｜资料评价 {row['material_feedback']}"
        out.append(
            {
                "source_type": "report",
                "source_id": int(row["id"]),
                "plan_id": int(row["plan_id"]),
                "date": str(row["created_at"]),
                "text": text,
            }
        )
    return out


def _candidate_experiences(conn: sqlite3.Connection, plan_id: int | None) -> list[dict[str, Any]]:
    sql = (
        "SELECT c.id, c.title, c.why, c.status, c.reject_reason, c.created_at,"
        " r.plan_id FROM candidate c JOIN learning_request r ON r.id = c.request_id"
    )
    params: tuple[Any, ...] = ()
    if plan_id is not None:
        sql += " WHERE r.plan_id = ?"
        params = (plan_id,)
    labels = {"accepted": "已采纳", "rejected": "已否决", "expired": "已过期", "proposed": "还没裁定"}
    out: list[dict[str, Any]] = []
    for row in conn.execute(sql + " ORDER BY c.id", params):
        text = (
            f"候选方向「{row['title']}」{labels.get(str(row['status']), row['status'])}"
            f"（当初的理由：{row['why'] or '没写'}）"
        )
        if row["reject_reason"]:
            text += f"｜否决理由：{row['reject_reason']}"
        out.append(
            {
                "source_type": "candidate",
                "source_id": int(row["id"]),
                "plan_id": int(row["plan_id"]) if row["plan_id"] is not None else None,
                "date": str(row["created_at"]),
                "text": text,
            }
        )
    return out


def _proposal_experiences(conn: sqlite3.Connection, plan_id: int | None) -> list[dict[str, Any]]:
    """提案裁定。计划归属从 payload 里读（蓝图 / 档案变更 / 计划改动都带 plan_id）。

    **记忆候选本身不算经历**：它是记忆系统的产出，不是「发生过的事」。把它算进来会转成一个
    圈——一次扫描落几条候选，下一批就把这几条候选当成新经历再扫一遍。
    """
    out: list[dict[str, Any]] = []
    for row in conn.execute("SELECT * FROM proposal WHERE kind != ? ORDER BY id", (KIND,)):
        payload = _json_of(row["payload"])
        owner = payload.get("plan_id")
        owner = int(owner) if isinstance(owner, (int, str)) and str(owner).isdigit() else None
        if plan_id is not None and owner != plan_id:
            continue
        summary = str(payload.get("summary") or payload.get("why") or "").strip()
        text = (
            f"{payload.get('kind') or row['kind']} 提案 {row['status']}"
            f"（{summary or row['reason'] or '没写理由'}）"
        )
        out.append(
            {
                "source_type": "proposal",
                "source_id": int(row["id"]),
                "plan_id": owner,
                "date": str(row["decided_at"] or row["created_at"]),
                "text": text,
            }
        )
    return out


def _field_change_experiences(
    conn: sqlite3.Connection, plan_id: int | None
) -> list[dict[str, Any]]:
    """计划变更 = 节点字段那一类流水（T30 起，改前改后都在里面）。"""
    sql = (
        "SELECT e.id, e.before_value, e.after_value, e.reason, e.created_at,"
        " n.title, n.plan_id FROM ledger_event e JOIN plan_node n ON n.id = e.entity_id"
        " WHERE e.entity_type = 'plan_node' AND e.change_type = 'update_fields'"
    )
    params: tuple[Any, ...] = ()
    if plan_id is not None:
        sql += " AND n.plan_id = ?"
        params = (plan_id,)
    out: list[dict[str, Any]] = []
    for row in conn.execute(sql + " ORDER BY e.id", params):
        out.append(
            {
                "source_type": "field_change",
                "source_id": int(row["id"]),
                "plan_id": int(row["plan_id"]),
                "date": str(row["created_at"]),
                "text": (
                    f"节点「{row['title']}」改了字段：{row['before_value']} → "
                    f"{row['after_value']}（理由：{row['reason'] or '没写'}）"
                ),
            }
        )
    return out


_EXPERIENCE_BUILDERS = (
    _dialogue_experiences,
    _chat_experiences,
    _report_experiences,
    _candidate_experiences,
    _proposal_experiences,
    _field_change_experiences,
)


def experiences(
    conn: sqlite3.Connection, plan_id: int | None = None
) -> list[dict[str, Any]]:
    """六类经历**合并成一条时间线**，最早的在前面。

    为什么按时间正序：它是扫描与检索共用的底座，而扫描的游标要能「只推进处理过的那部分」
    ——正序取前缀时，被排在前缀之外的那些下次仍然 > 游标，不会漏。
    """
    merged: list[dict[str, Any]] = []
    for builder in _EXPERIENCE_BUILDERS:
        merged += builder(conn, plan_id)
    merged.sort(key=lambda item: (item["date"], item["source_type"], item["source_id"]))
    return merged


def search_experiences(
    conn: sqlite3.Connection,
    plan_id: int,
    *,
    keyword: str | None = None,
    source_type: str | None = None,
    since: str | None = None,
    limit: int = SEARCH_LIMIT,
) -> dict[str, Any]:
    """按关键词 / 来源类型 / 时间检索经历，**最多 8 条**，最新的在前。

    确定性排序 + 子串匹配：第一版刻意不引向量库或 Embedding（方案第 5 节）。
    计划编号由系统注入——这个函数只看得见传进来的那一个计划。
    """
    if source_type is not None:
        resolved = resolve_source_type(source_type)
        if resolved is None:
            raise MemoryError(source_type_error(source_type))
        source_type = resolved
    since_date = _iso_date(since, "起始时间")
    word = _normalize(keyword or "")
    hits: list[dict[str, Any]] = []
    for item in experiences(conn, plan_id):
        if source_type is not None and item["source_type"] != source_type:
            continue
        if since_date is not None and item["date"][:10] < since_date:
            continue
        if word and word not in _normalize(item["text"]):
            continue
        hits.append(item)
    hits.sort(key=lambda item: (item["date"], item["source_type"], item["source_id"]), reverse=True)
    kept = hits[:limit]
    return {
        "plan_id": plan_id,
        "source_type": source_type,  # 解出来的令牌（中文别名也在这里被翻过来）
        "matched": len(hits),
        "returned": len(kept),
        "items": [
            {
                "source_type": item["source_type"],
                "source_label": EXPERIENCE_SOURCES[item["source_type"]],
                "source_id": item["source_id"],
                "date": item["date"][:10],
                "excerpt": item["text"][:200],
            }
            for item in kept
        ],
    }


def _experience_text(conn: sqlite3.Connection, source_type: str, source_id: int) -> str | None:
    """单条经历的正文——校验「摘录能在来源里找到」时用它。

    刻意不整表扫描：只取那一条，按同一个拼法拼出来。拼法与列表那条路共用同一组函数，
    所以「列表里看到的」与「校验时比对的」永远是同一段文字。
    """
    for builder in _EXPERIENCE_BUILDERS:
        for item in builder(conn, None):
            if item["source_type"] == source_type and item["source_id"] == int(source_id):
                return str(item["text"])
    return None


# ============================================================
# 三·五、记忆变化感知：水印 + 变化清单 + 摘要行（走查整改第 3 条，2026-09-21）
# ============================================================
#
# 要解决的问题：模型每一轮都是**从零决定读什么**的，没有任何东西告诉它「记忆库自你上次读
# 之后变过」；而对话历史里还留着它早先（记忆变化之前）的判断与回答，于是它沿用旧认知、
# 不再重读（走查截图 2）。**方案是增量只用来生成一句提醒，依据仍靠整份重读**——只给增量时
# 旧文本还躺在历史里，压不住旧认知。
#
# 两边的料都是现成的，不给对话表加状态列（对话表是追加式日志，这是这个项目的老规矩）：
# **水印**从运行账 `agent_run` 取（它本来就记着每一轮读了什么、什么时候读的），
# **变化清单**从台账流水取（新增 / 取代 / 作废都带时间戳），彻底删除的行已经没了，
# 所以并上墓碑表 `memory_deletion`。

# 运行账里「读记忆」那一步的工具名——「这段对话上次读记忆是什么时候」就靠它认。
MEMORY_READ_TOOL = "read_memories"

# 摘要行里那些动作的说法（台账的 change_type + 墓碑那一路的 `purged`）。
CHANGE_LABELS: dict[str, str] = {
    "create": "新增",
    "supersede": "被取代",
    "void": "作废",
    "purged": "被彻底删除",
}


def last_read_at(conn: sqlite3.Connection, plan_id: int) -> str | None:
    """这段对话**上次读到记忆**是什么时候：运行账里最近一条「读成了 read_memories」的时间。

    从最新往回找，遇到第一条读成的就停——往后那几轮没读过记忆，不影响水印是「最后一次读」。
    只认 `ok` 的那条：读失败不算读过，否则失败一次就把变化吞掉，那一轮之后它再也不会被提醒。
    """
    rows = conn.execute(
        "SELECT tools, created_at FROM agent_run WHERE plan_id = ? ORDER BY id DESC",
        (int(plan_id),),
    ).fetchall()
    for row in rows:
        if any(
            tool.get("name") == MEMORY_READ_TOOL and tool.get("ok")
            for tool in _json_list(row["tools"])
        ):
            return str(row["created_at"])
    return None


def changes_since(
    conn: sqlite3.Connection, plan_id: int, since: str
) -> list[dict[str, Any]]:
    """水印之后的记忆变化：新增 / 取代 / 作废（台账流水）＋ 彻底删除（墓碑）。

    范围按作用域切：**全局记忆的变化对所有对话都算**，计划内记忆的变化只算它那一个计划
    ——别的计划的记忆这一段对话本来就读不到，报给它只会让它去读不存在的东西。

    同一段记忆在这段时间里被折腾过好几次的，只报**最后一次**那个动作：摘要行是提醒，
    不是流水账。顺序按发生时间，读起来才像「这段时间发生了什么」。
    """
    found: dict[tuple[str, int], dict[str, Any]] = {}

    events = conn.execute(
        "SELECT * FROM ledger_event WHERE entity_type IN ('profile_item', 'plan_memory')"
        " AND change_type IN ('create', 'supersede', 'void') AND created_at > ?"
        " ORDER BY id",
        (since,),
    ).fetchall()
    for row in events:
        scope = SCOPE_GLOBAL if str(row["entity_type"]) == "profile_item" else SCOPE_PLAN
        memory_id = int(row["entity_id"])
        if scope == SCOPE_PLAN and _plan_of_memory(conn, memory_id) != int(plan_id):
            continue
        found[(scope, memory_id)] = {
            "scope": scope,
            "memory_id": memory_id,
            "change": str(row["change_type"]),
            "at": str(row["created_at"]),
        }

    tombstones = conn.execute(
        "SELECT * FROM memory_deletion WHERE deleted_at > ? ORDER BY id", (since,)
    ).fetchall()
    for row in tombstones:
        scope = str(row["scope"])
        if scope == SCOPE_PLAN and (
            row["plan_id"] is None or int(row["plan_id"]) != int(plan_id)
        ):
            continue
        found[(scope, int(row["memory_id"]))] = {
            "scope": scope,
            "memory_id": int(row["memory_id"]),
            "change": "purged",
            "at": str(row["deleted_at"]),
        }

    return sorted(found.values(), key=lambda item: (item["at"], item["memory_id"]))


def _plan_of_memory(conn: sqlite3.Connection, memory_id: int) -> int | None:
    """计划内记忆属于哪个计划。行已经没了（彻底删除）时给 `None`——那条由墓碑负责。"""
    row = conn.execute("SELECT plan_id FROM plan_memory WHERE id = ?", (int(memory_id),)).fetchone()
    return None if row is None else int(row["plan_id"])


def change_digest(conn: sqlite3.Connection, plan_id: int) -> str | None:
    """这一轮开头要不要多一行记忆提醒：有变化才给，没有就不给。

    **这段对话从没读过记忆时也不给**：那种对话的历史里没有基于记忆的结论，没有旧认知要顶，
    硬塞一句提醒只是噪音（而且第一轮它本来就该自己判断要不要读）。
    """
    since = last_read_at(conn, plan_id)
    if since is None:
        return None
    changes = changes_since(conn, plan_id, since)
    if not changes:
        return None
    listed = "、".join(
        f"#{item['memory_id']} {CHANGE_LABELS.get(item['change'], item['change'])}"
        for item in changes
    )
    stamp = since[:16].replace("T", " ")
    return (
        f"【记忆库变了】记忆库自你上次读（{stamp}）之后变过 {len(changes)} 条：{listed}。"
        "涉及它们就先读一次记忆（read_memories）——读的是**现在**这份，"
        "别拿你早先读到的旧说法当依据。"
    )


def _json_list(raw: Any) -> list[dict[str, Any]]:
    """一段 JSON 数组解成字典列表；坏了就当空——运行账读不动不该让这一轮跑不起来。"""
    try:
        parsed = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


# ============================================================
# 四、候选契约与确定性校验
# ============================================================

class Evidence(BaseModel):
    source_type: str = Field(min_length=1)
    source_id: int
    excerpt: str = Field(min_length=1)


class Candidate(BaseModel):
    """模型提的一条记忆候选。字段先按形状收，语义留给 `validate_candidate` 判。"""

    action: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    plan_id: int | None = None
    category: str | None = None
    kind: str | None = None
    content: str | None = None
    source_kind: str = "agent_inferred"
    fact_time: str | None = None
    review_at: str | None = None
    reason: str = Field(min_length=1)
    target_id: int | None = None
    decision: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)


class ScanOutput(BaseModel):
    """一批扫描的产出。**允许 0 条**——大多数批次里没有值得长期记住的东西才是常态。"""

    memories: list[Candidate] = Field(default_factory=list, max_length=MAX_SCAN_CANDIDATES)


def validate_candidate(
    conn: sqlite3.Connection, data: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    """确定性校验（方案第 4 节）：给出（可落库的 payload，不合格原因）。

    四条硬校验：**来源存在**、**摘录能在来源里一字不差找到**、**取代/复核的目标仍有效**、
    **没有完全重复**。近似重复只往 payload 里塞一句提示，**不拦、也不合并**。
    """
    try:
        candidate = Candidate.model_validate(data)
    except ValidationError as error:
        return None, _details(error)

    if candidate.action not in ACTIONS:
        return None, f"动作「{candidate.action}」不认识，只能给：{' / '.join(ACTIONS)}"
    if candidate.scope not in SCOPES:
        return None, f"作用域「{candidate.scope}」不认识，只能给：{' / '.join(SCOPES)}"
    if candidate.source_kind not in CANDIDATE_SOURCE_KINDS:
        return None, (
            f"source_kind「{candidate.source_kind}」不能由模型给，只能是 "
            f"{' / '.join(CANDIDATE_SOURCE_KINDS)}——你自己推断的必须写 agent_inferred"
        )

    payload: dict[str, Any] = {"action": candidate.action, "scope": candidate.scope}

    if candidate.scope == SCOPE_PLAN:
        try:
            payload["plan_id"] = _require_plan(conn, candidate.plan_id)
        except MemoryNotFound:
            return None, f"计划 id={candidate.plan_id} 不存在"
        if candidate.kind not in MEMORY_KINDS:
            return None, (
                f"计划内记忆的类别「{candidate.kind}」不认识，只能给：{' / '.join(MEMORY_KINDS)}"
            )
        payload["kind"] = candidate.kind
    else:
        if candidate.category not in advisor.PROFILE_CATEGORIES:
            return None, (
                f"全局记忆的类别「{candidate.category}」不在约定的五个令牌里："
                f"{' / '.join(advisor.PROFILE_CATEGORIES)}"
            )
        payload["category"] = candidate.category

    fact_time = _iso_date(candidate.fact_time, "事实时间")
    review_at = _iso_date(candidate.review_at, "复核时间")

    if candidate.action in (ACTION_ADD, ACTION_SUPERSEDE):
        content = str(candidate.content or "").strip()
        if not content:
            return None, f"{ACTIONS[candidate.action]}必须给 content"
        payload["content"] = content
        payload["source_kind"] = candidate.source_kind
        payload["fact_time"] = fact_time
        payload["review_at"] = review_at

    target: sqlite3.Row | None = None
    if candidate.action in (ACTION_SUPERSEDE, ACTION_REVIEW):
        if candidate.target_id is None:
            return None, f"{ACTIONS[candidate.action]}必须给 target_id（要动的是哪一条）"
        try:
            target = _require_active(conn, candidate.scope, int(candidate.target_id))
        except MemoryNotFound:
            return None, f"要动的记忆 id={candidate.target_id} 不存在"
        except MemoryConflict as error:
            return None, str(error)
        payload["target_id"] = int(candidate.target_id)
        payload["target_content"] = str(target["content"])
        # 计划隔离：要动的那条必须是**这个计划**里的记忆。少了这一条，一个计划里的候选
        # 能拿着别个计划的行号去改它——作用域是计划内记忆的全部意义。
        if candidate.scope == SCOPE_PLAN and int(target["plan_id"]) != int(payload["plan_id"]):
            return None, (
                f"记忆 id={candidate.target_id} 不属于计划 {payload['plan_id']}——"
                "计划内记忆只在这个计划里，读不到也动不了别的计划"
            )

    # 判重放在「目标已确认」之后：取代时被取代的那条自己不算重复，其余一字不差的仍要拦。
    if candidate.action in (ACTION_ADD, ACTION_SUPERSEDE):
        if target is not None and payload["content"] == str(target["content"]).strip():
            return None, "取代的新内容和旧内容一模一样，这条改动等于没发生"
        exclude = None if target is None else int(target["id"])
        try:
            _reject_exact_duplicate(
                conn,
                candidate.scope,
                content=str(payload["content"]),
                plan_id=payload.get("plan_id"),
                table=_table_of(candidate.scope),
                exclude_id=exclude,
            )
        except MemoryConflict as error:
            return None, str(error)
        hint = duplicate_hint(
            conn,
            candidate.scope,
            content=str(payload["content"]),
            plan_id=payload.get("plan_id"),
            exclude_id=exclude,
        )
        if hint:
            payload["duplicate_hint"] = hint

    if candidate.action == ACTION_REVIEW:
        if candidate.decision not in REVIEW_DECISIONS:
            return None, (
                f"复核要说明怎么处理：{' / '.join(REVIEW_DECISIONS)}"
            )
        payload["decision"] = candidate.decision
        payload["review_at"] = review_at or _default_review_at()
        payload.pop("content", None)

    payload["reason"] = candidate.reason.strip()

    if not candidate.evidence:
        return None, "每条候选至少要给一条来源证据（source_type + source_id + excerpt）"
    landed_evidence: list[dict[str, Any]] = []
    for item in candidate.evidence:
        if item.source_type not in EXPERIENCE_SOURCES:
            return None, (
                f"来源类型「{item.source_type}」不认识，只能给：{' / '.join(EXPERIENCE_SOURCES)}"
            )
        source_text = _experience_text(conn, item.source_type, item.source_id)
        if source_text is None:
            return None, (
                f"来源不存在：{EXPERIENCE_SOURCES[item.source_type]} #{item.source_id}"
            )
        excerpt = item.excerpt.strip()
        if excerpt not in source_text:
            return None, (
                f"摘录在来源里找不到（{EXPERIENCE_SOURCES[item.source_type]} "
                f"#{item.source_id}）——摘录必须是从那条经历里一字不差抄下来的一段"
            )
        landed_evidence.append(
            {
                "source_type": item.source_type,
                "source_id": int(item.source_id),
                "excerpt": excerpt,
                "source_time": _source_time(conn, item.source_type, item.source_id),
            }
        )
    payload["evidence"] = landed_evidence
    return payload, None


def _source_time(conn: sqlite3.Connection, source_type: str, source_id: int) -> str | None:
    """那条来源发生的日期——证据上带一个时间，用户复核时对得上。"""
    for item in _source_time_lookup(conn, source_type, source_id):
        return item
    return None


def _source_time_lookup(conn: sqlite3.Connection, source_type: str, source_id: int) -> list[str]:
    table = {
        "plan_dialogue": "plan_dialogue",
        "plan_chat": "plan_chat",
        "report": "report",
        "candidate": "candidate",
        "proposal": "proposal",
        "field_change": "ledger_event",
    }[source_type]
    row = conn.execute(f"SELECT created_at FROM {table} WHERE id = ?", (int(source_id),)).fetchone()
    return [str(row["created_at"])[:10]] if row is not None else []


def _details(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or '(根)'}: {item['msg']}"
        for item in error.errors()
    )


def _json_of(raw: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


# ---------- 收件箱（复用 proposal 表） ----------

def public_candidate(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    """一条记忆候选 → 给界面看的形状：动作、适用范围、陈述或推断、证据原话、复核时间。

    另外算两样：`batch_eligible`（只有「新增 + 用户明确陈述」才允许混进批量批准）与
    `target_content`（取代 / 复核时把要被动的那条摆出来，免得盲签）。
    """
    payload = _json_of(row["payload"])
    action = str(payload.get("action") or "")
    scope = str(payload.get("scope") or "")
    eligible = action == ACTION_ADD and payload.get("source_kind") == "user_stated"
    return {
        "proposal_id": int(row["id"]),
        "action": action,
        "action_label": ACTIONS.get(action, action),
        "scope": scope,
        "scope_label": "全局" if scope == SCOPE_GLOBAL else "计划内",
        "plan_id": payload.get("plan_id"),
        "category": payload.get("category"),
        "category_label": advisor.PROFILE_CATEGORIES.get(str(payload.get("category") or "")),
        "kind": payload.get("kind"),
        "kind_label": MEMORY_KINDS.get(str(payload.get("kind") or "")),
        "content": payload.get("content"),
        "target_id": payload.get("target_id"),
        "target_content": payload.get("target_content"),
        "decision": payload.get("decision"),
        "decision_label": REVIEW_DECISIONS.get(str(payload.get("decision") or "")),
        "review_at": payload.get("review_at"),
        "fact_time": payload.get("fact_time"),
        "source_kind": payload.get("source_kind"),
        "source_kind_label": SOURCE_KINDS.get(str(payload.get("source_kind") or ""), ""),
        "reason": payload.get("reason") or row["reason"],
        "duplicate_hint": payload.get("duplicate_hint"),
        # 来源的中文名由后端补上（候选 payload 里存的是机器可读的那两个字段）——
        # 前端只显示，不自己查表。
        "evidence": [
            {**item, "source_label": EXPERIENCE_SOURCES.get(str(item.get("source_type")), "")}
            for item in (payload.get("evidence") or [])
        ],
        "batch_eligible": eligible,
        "purged": bool(payload.get("purged")),
        "created_at": row["created_at"],
    }


def list_inbox(conn: sqlite3.Connection) -> dict[str, Any]:
    """记忆收件箱：还没裁定的记忆候选，按提出顺序排。"""
    rows = conn.execute(
        "SELECT * FROM proposal WHERE kind = ? AND status = 'pending' ORDER BY id", (KIND,)
    ).fetchall()
    items = [public_candidate(conn, row) for row in rows]
    return {"candidates": items, "count": len(items)}


def land_candidate(
    conn: sqlite3.Connection, payload: dict[str, Any], *, source: str
) -> int:
    """把一条**已经验过**的候选落成待裁定提案。返回提案编号。"""
    label = (
        f"{ACTIONS.get(str(payload.get('action')), payload.get('action'))}"
        f"（{'全局' if payload.get('scope') == SCOPE_GLOBAL else '计划内'}）"
    )
    return ledger.create_active(
        conn,
        "proposal",
        {
            "kind": KIND,
            "payload": json.dumps(payload, ensure_ascii=False),
            "reason": f"{source}：{label}——{payload.get('reason')}",
        },
        actor="agent",
    )


# ============================================================
# 五、批准：候选 → 真记忆
# ============================================================

def apply(conn: sqlite3.Connection, payload: dict[str, Any], *, proposal_id: int) -> dict[str, Any]:
    """批准一条记忆候选时**唯一**的写入口（`proposals.decide` 只调它）。

    写之前**再验一次**：从落候选到批准之间，用户完全可能已经把那条旧记忆改掉了——
    那时候「取代它」这个动作的前提就没了，必须报冲突、让候选留着待处理，而不是照着旧假设写。
    """
    action = str(payload.get("action"))
    scope = str(payload.get("scope"))
    if scope not in SCOPES:
        raise MemoryError(f"候选里的作用域「{scope}」不认识")
    table = _table_of(scope)
    note = f"按记忆候选提案 #{proposal_id} 批准"

    if action == ACTION_ADD:
        values: dict[str, Any] = {
            "content": str(payload.get("content") or "").strip(),
            "source_kind": payload.get("source_kind") or "agent_inferred",
            "fact_time": payload.get("fact_time"),
            "review_at": payload.get("review_at"),
        }
        if scope == SCOPE_GLOBAL:
            values["category"] = payload.get("category")
        else:
            values["plan_id"] = payload.get("plan_id")
            values["kind"] = payload.get("kind")
        _reject_exact_duplicate(
            conn, scope, content=values["content"], plan_id=values.get("plan_id"), table=table
        )
        memory_id = ledger.create_active(conn, table, values, actor="user", reason=note)
        _attach_evidence(conn, scope, memory_id, payload.get("evidence") or [])
        return {"effect": "memory_added", "memory": public_memory(conn, scope, _row_of(conn, scope, memory_id))}

    target_id = int(payload.get("target_id") or 0)
    target = _target_of(conn, payload)

    if action == ACTION_SUPERSEDE:
        new_values: dict[str, Any] = {
            "content": str(payload.get("content") or "").strip(),
            "source_kind": payload.get("source_kind") or "agent_inferred",
            "fact_time": payload.get("fact_time"),
            "review_at": payload.get("review_at"),
        }
        new_id = ledger.supersede(
            conn, table, target_id, new_values, reason=f"{note}：{payload.get('reason')}"
        )
        _copy_evidence(conn, scope, target_id, new_id)
        _attach_evidence(conn, scope, new_id, payload.get("evidence") or [])
        return {
            "effect": "memory_superseded",
            "before": str(target["content"]),
            "memory": public_memory(conn, scope, _row_of(conn, scope, new_id)),
        }

    if action == ACTION_REVIEW:
        decision = str(payload.get("decision"))
        if decision == "renew":
            renewed = renew_memory(
                conn,
                scope,
                target_id,
                review_at=payload.get("review_at"),
                reason=f"{note}：{payload.get('reason')}",
            )
            return {"effect": "memory_renewed", "memory": renewed}
        if decision == "void":
            ledger.void(
                conn, table, target_id, reason=f"{note}：{payload.get('reason')}"
            )
            return {"effect": "memory_voided", "memory": public_memory(conn, scope, _row_of(conn, scope, target_id))}
        raise MemoryError(f"复核的处置「{decision}」不认识")

    raise MemoryError(f"动作「{action}」不认识")


def batch_approve(
    conn: sqlite3.Connection, proposal_ids: list[int], *, reason: str | None = None
) -> dict[str, Any]:
    """批量批准：**只收「新增 + 用户明确陈述」**的候选（方案第 3 节）。

    取代、Agent 推断、彻底删除一律逐条——前两者会改变已经成立的事实，后者不可恢复，
    混在批量里等于让用户在一屏之内签掉自己没读过的东西。
    """
    approved: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for proposal_id in proposal_ids:
        row = conn.execute("SELECT * FROM proposal WHERE id = ?", (int(proposal_id),)).fetchone()
        if row is None or str(row["kind"]) != KIND or str(row["status"]) != "pending":
            skipped.append({"proposal_id": int(proposal_id), "why": "不是待裁定的记忆候选"})
            continue
        payload = _json_of(row["payload"])
        eligible = (
            str(payload.get("action")) == ACTION_ADD
            and payload.get("source_kind") == "user_stated"
        )
        if not eligible:
            skipped.append(
                {
                    "proposal_id": int(proposal_id),
                    "why": "不是「新增 + 用户陈述」，只能逐条确认",
                }
            )
            continue
        try:
            precheck(conn, payload)
        except MemoryError as error:
            skipped.append({"proposal_id": int(proposal_id), "why": str(error)})
            continue
        ledger.set_status(
            conn,
            "proposal",
            int(proposal_id),
            "accepted",
            actor="user",
            reason=f"批量批准：{str(reason or '').strip() or '收件箱里这些新增'}",
            extra={"decided_at": now_iso()},
        )
        approved.append({"proposal_id": int(proposal_id), **apply(conn, payload, proposal_id=int(proposal_id))})
    return {"approved": approved, "skipped": skipped}


def precheck(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """批准前的**再验一次**：从落候选到批准之间，世界完全可能已经变了。

    两条会变的前提单独判，状态码才说得准：目标已不是当前有效值 → `MemoryConflict`；
    内容与某条现行记忆一字不差 → `MemoryConflict`；目标根本不在了 → `MemoryNotFound`。
    """
    scope = str(payload.get("scope"))
    if scope not in SCOPES:
        raise MemoryError(f"候选里的作用域「{scope}」不认识")
    target_id = payload.get("target_id")
    if target_id is not None:
        _target_of(conn, payload)
    if payload.get("action") in (ACTION_ADD, ACTION_SUPERSEDE):
        _reject_exact_duplicate(
            conn,
            scope,
            content=str(payload.get("content") or ""),
            plan_id=payload.get("plan_id"),
            table=_table_of(scope),
            exclude_id=None if target_id is None else int(target_id),
        )
    problem = _validate_evidence(conn, payload)
    if problem is not None:
        raise MemoryError(problem)


def _validate_evidence(conn: sqlite3.Connection, payload: dict[str, Any]) -> str | None:
    """来源还在不在、摘录还找不找得到——批准前重跑一遍（来源是会被清掉的）。"""
    evidence = payload.get("evidence") or []
    if not evidence:
        return None  # 用户手敲的条目本来就可以没有来源；模型候选在落库时就拦过
    return validate_candidate(conn, {
        **payload,
        "evidence": [
            {
                "source_type": item["source_type"],
                "source_id": item["source_id"],
                "excerpt": item["excerpt"],
            }
            for item in evidence
        ],
    })[1]


# ============================================================
# 六、扫描：发现值得记住的内容 → 只产候选
# ============================================================

SCAN_SYSTEM_PROMPT = (
    "你从一批「刚发生过的经历」里找出**值得长期记住**的内容，产出记忆候选。"
    "你只输出一个 JSON 对象：不要解释、不要客套、不要 Markdown 代码块。"
)


def _scan_brief(conn: sqlite3.Connection, plan_id: int | None, batch: list[dict[str, Any]]) -> str:
    """这一批要发给模型的东西：编号过的经历 + 现有的记忆（取代 / 复核要先认得出目标）。"""
    lines = [
        "【这一批最新的经历】每条的 #编号就是它的来源编号（evidence 里要原样引用）",
    ]
    lines += [
        f"#{item['source_id']} [{EXPERIENCE_SOURCES[item['source_type']]}]"
        f" {item['date'][:10]} {item['text']}"
        for item in batch
    ]
    lines.append("")
    lines.append("【现有的记忆】复核 / 取代要用这里的 #编号（这是记忆自身编号，不是来源编号）")
    listing = list_memories(conn, plan_id=plan_id)
    for item in [*listing["global"], *listing["plan"]]:
        where = "全局" if item["scope"] == SCOPE_GLOBAL else f"计划 {item['plan_id']}"
        label = item["category_label"] or item["kind_label"]
        due = "｜已到复核时间" if item["review_due"] else ""
        lines.append(
            f"- 记忆#{item['id']}（{where}·{label}·{item['source_kind_label']}{due}）{item['content']}"
        )
    if not listing["global"] and not listing["plan"]:
        lines.append("（现在一条记忆都没有）")
    lines.append("")
    lines.append(
        "【输出】只输出一个 JSON 对象："
        '{"memories": [...]}。每条候选的形状：\n'
        '{"action": "add", "scope": "global", "category": "当前状态", "content": "要记住的事实",'
        ' "source_kind": "user_stated", "fact_time": "2026-09-21", "review_at": "2026-12-20",'
        ' "reason": "为什么值得记", "evidence": [{"source_type": "plan_dialogue", "source_id": 12,'
        ' "excerpt": "那条经历里一字不差的一段"}]}\n'
        '- scope 给 "plan" 时把 category 换成 kind（constraint / decision / preference），'
        "并给 plan_id。\n"
        '- action 给 "supersede" 时另给 target_id（要取代的记忆 #编号）与新的 content。\n'
        '- action 给 "review" 时给 target_id + decision（renew 续期 / void 作废）+ reason。\n'
        "【纪律】\n"
        "- evidence 的 source_type 只能是 plan_dialogue / plan_chat / report / candidate /"
        " proposal / field_change，source_id 用上面经历的 #编号，excerpt 必须**一字不差**"
        "从那条经历里抄一段——抄不出来就别提这条。\n"
        "- 用户明说的写 user_stated，你自己推断出来的必须写 agent_inferred。\n"
        "- 只记**长期成立**的东西（他的偏好、约束、已经定下的决定）；一次性的进展、"
        "寒暄、已经写在计划表里的事实都不要记。\n"
        f"- 最多 {MAX_SCAN_CANDIDATES} 条；没有值得记的就回 "
        '{"memories": []}，空着是正常的。'
    )
    return "\n".join(lines)


def _new_experiences(
    conn: sqlite3.Connection, plan_id: int | None, cursor: dict[str, int]
) -> list[dict[str, Any]]:
    """游标之后的新经历（含全局那批：`plan_id=None` 时也把不带计划的经历带进来）。"""
    return [
        item
        for item in experiences(conn, plan_id)
        if item["source_id"] > cursor.get(item["source_type"], 0)
    ]


def _batch(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """按条数与字符数取一批，并算出**这一批之后**的游标（只推进处理过的那部分）。"""
    kept: list[dict[str, Any]] = []
    used = 0
    for item in items[:SCAN_BATCH_EXPERIENCES]:
        size = len(item["text"])
        if kept and used + size > SCAN_BATCH_CHARS:
            break
        kept.append(item)
        used += size
    cursor: dict[str, int] = {}
    for item in kept:
        cursor[item["source_type"]] = max(cursor.get(item["source_type"], 0), item["source_id"])
    return kept, cursor


def latest_cursor(conn: sqlite3.Connection, plan_id: int | None) -> dict[str, int]:
    """这个作用域上一次**成功**扫描留下的游标（失败的不算）。"""
    row = conn.execute(
        "SELECT cursor FROM memory_scan WHERE status = 'ok' AND"
        + (" plan_id = ?" if plan_id is not None else " plan_id IS NULL")
        + " ORDER BY id DESC LIMIT 1",
        (plan_id,) if plan_id is not None else (),
    ).fetchone()
    return _json_of(row["cursor"]) if row is not None else {}


def scan(
    conn: sqlite3.Connection,
    *,
    trigger: str,
    plan_id: int | None = None,
    provider_id: int | None = None,
    model: str | None = None,
    transport: llm.Transport | None = None,
    record_id: int | None = None,
) -> dict[str, Any]:
    """扫一批新经历，产出**候选**（不写任何长期记忆）。

    只有扫描**成功**才推进游标——「没有候选」也是成功；模型输出结构不对就带原因重试一次，
    两次仍不对就记一行 `failed` 并**不落任何候选**、游标不动（下一批重来）。
    """
    if trigger not in TRIGGER_LABELS:
        raise MemoryError(f"扫描触发「{trigger}」不认识，只能给：{' / '.join(TRIGGER_LABELS)}")
    if plan_id is not None:
        plan_id = _require_plan(conn, plan_id)

    started_at = now_iso()
    if record_id is None:
        record_id = int(
            conn.execute(
                "INSERT INTO memory_scan (trigger, plan_id, status, created_at)"
                " VALUES (?, ?, 'pending', ?)",
                (trigger, plan_id, started_at),
            ).lastrowid
        )
        conn.commit()

    cursor = latest_cursor(conn, plan_id)
    pending = _new_experiences(conn, plan_id, cursor)
    batch, next_cursor = _batch(pending)
    if not batch:
        return _finish_scan(
            conn, record_id, cursor, scanned=0, candidates=0, error=None, has_more=False
        )

    messages = [
        {"role": "system", "content": SCAN_SYSTEM_PROMPT},
        {"role": "user", "content": _scan_brief(conn, plan_id, batch)},
    ]
    operation = llm.Operation(conn, "memory_scan", limit=SCAN_CALL_LIMIT, transport=transport)
    try:
        raw = operation.chat(messages, provider_id=provider_id, model=model)
        output, problem = _as_scan_output(raw)
        if problem is not None:
            messages = [
                *messages,
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": f"你上面的输出不合格：{problem}。请只输出合格的 JSON 对象，不要解释。",
                },
            ]
            raw = operation.chat(messages, provider_id=provider_id, model=model)
            output, problem = _as_scan_output(raw)
        if problem is not None or output is None:
            return _finish_scan(
                conn,
                record_id,
                cursor,
                scanned=len(batch),
                candidates=0,
                error=f"模型连着 {SCAN_CALL_LIMIT} 次都没给出合格的扫描输出（{problem}）；"
                "没有落任何候选",
                has_more=False,
            )
    except llm.LlmError as error:
        return _finish_scan(
            conn,
            record_id,
            cursor,
            scanned=len(batch),
            candidates=0,
            error=f"调用模型失败：{error}",
            has_more=False,
        )

    landed: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for candidate in output.memories:
        payload, why = validate_candidate(conn, candidate.model_dump())
        if payload is None:
            # 单条不合格就丢掉它，**绝不落半条**——整批里合格的那些照常进收件箱。
            dropped.append({"content": candidate.content or "", "why": why})
            continue
        proposal_id = land_candidate(conn, payload, source=f"{TRIGGER_LABELS[trigger]}产出")
        landed.append(
            {
                "proposal_id": proposal_id,
                "content": payload.get("content") or payload.get("target_content"),
            }
        )

    report = _finish_scan(
        conn,
        record_id,
        next_cursor,
        scanned=len(batch),
        candidates=len(landed),
        error=None,
        has_more=len(pending) > len(batch),
    )
    report["landed"] = landed
    report["dropped"] = dropped
    return report


def _as_scan_output(raw: str) -> tuple[ScanOutput | None, str | None]:
    data = advisor.extract_json(raw)
    if data is None:
        return None, "输出不是合法的 JSON 对象"
    try:
        return ScanOutput.model_validate(data), None
    except ValidationError as error:
        return None, _details(error)


def _finish_scan(
    conn: sqlite3.Connection,
    record_id: int,
    cursor: dict[str, int],
    *,
    scanned: int,
    candidates: int,
    error: str | None,
    has_more: bool,
) -> dict[str, Any]:
    """收尾一行扫描记录：**成功才写游标**（失败时下一批从原处重来），失败带上原因。"""
    conn.execute(
        "UPDATE memory_scan SET status = ?, cursor = ?, scanned = ?, candidates = ?,"
        " error = ?, finished_at = ? WHERE id = ?",
        (
            "failed" if error else "ok",
            None if error else json.dumps(cursor, ensure_ascii=False),
            scanned,
            candidates,
            error,
            now_iso(),
            record_id,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM memory_scan WHERE id = ?", (record_id,)).fetchone()
    return {
        "scan_id": record_id,
        "trigger": str(row["trigger"]),
        "trigger_label": TRIGGER_LABELS.get(str(row["trigger"]), str(row["trigger"])),
        "plan_id": None if row["plan_id"] is None else int(row["plan_id"]),
        "status": str(row["status"]),
        "scanned": scanned,
        "candidates": candidates,
        "landed": [],
        "dropped": [],
        "has_more": has_more,
        "error": error,
    }


def register_pending_scan(conn: sqlite3.Connection, plan_id: int, *, trigger: str = TRIGGER_PLAN_CLOSE) -> int:
    """登记一条「待扫描」——**计划收尾不在这里同步调模型**（方案第 6 节）。

    收尾是一次业务动作，不该被一次模型调用拖住、也不该因为模型不通而失败；所以只登记，
    由周任务或记忆页之后来处理它。
    """
    record_id = int(
        conn.execute(
            "INSERT INTO memory_scan (trigger, plan_id, status, created_at)"
            " VALUES (?, ?, 'pending', ?)",
            (trigger, plan_id, now_iso()),
        ).lastrowid
    )
    conn.commit()
    return record_id


def pending_scans(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM memory_scan WHERE status = 'pending' ORDER BY id"))


def run_pending(
    conn: sqlite3.Connection,
    *,
    provider_id: int | None = None,
    model: str | None = None,
    transport: llm.Transport | None = None,
) -> list[dict[str, Any]]:
    """把登记下来还没跑的扫描（计划收尾留下的那些）逐个做掉。"""
    reports: list[dict[str, Any]] = []
    for row in pending_scans(conn):
        reports.append(
            scan(
                conn,
                trigger=str(row["trigger"]),
                plan_id=None if row["plan_id"] is None else int(row["plan_id"]),
                provider_id=provider_id,
                model=model,
                transport=transport,
                record_id=int(row["id"]),
            )
        )
    return reports


def weekly_scans(
    conn: sqlite3.Connection,
    *,
    provider_id: int | None = None,
    model: str | None = None,
    transport: llm.Transport | None = None,
) -> list[dict[str, Any]]:
    """每周那一批（方案第 6 节）：先把欠着的待扫描做掉，再给每个进行中的计划扫一批。

    为什么**按计划分开扫**而不是一次混扫：计划内记忆的作用域就是单个计划，一次混扫会让
    模型分不清「这条是哪儿的」——分开扫，`scope=plan` 的候选才永远落在对的那个计划上。
    全局那些记忆由各计划扫出来时由模型自己标 `scope=global`，不需要再单开一路。
    """
    reports = run_pending(conn, provider_id=provider_id, model=model, transport=transport)
    for row in conn.execute("SELECT id FROM plan WHERE status = 'active' ORDER BY id"):
        reports.append(
            scan(
                conn,
                trigger=TRIGGER_WEEKLY,
                plan_id=int(row["id"]),
                provider_id=provider_id,
                model=model,
                transport=transport,
            )
        )
    return reports


def scan_report(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM memory_scan ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [
        {
            "scan_id": int(row["id"]),
            "trigger": str(row["trigger"]),
            "trigger_label": TRIGGER_LABELS.get(str(row["trigger"]), str(row["trigger"])),
            "plan_id": row["plan_id"],
            "status": str(row["status"]),
            "scanned": int(row["scanned"]),
            "candidates": int(row["candidates"]),
            "error": row["error"],
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
        }
        for row in rows
    ]


# ============================================================
# 七、彻底删除：预览 → 二次确认 → 全库复扫
# ============================================================

# 「内容可能留在这儿」的全部位置。清理按证据关系做**精确替换**，修复不了的地方一律如实报出。
_TEXT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("profile_item", "content"),
    ("plan_memory", "content"),
    ("memory_evidence", "excerpt"),
    ("proposal", "payload"),
    ("plan_dialogue", "content"),
    ("plan_chat", "content"),
    ("report", "note"),
    ("candidate", "why"),
    ("candidate", "title"),
    ("ledger_event", "reason"),
    ("ledger_event", "before_value"),
    ("ledger_event", "after_value"),
)


def _copies(conn: sqlite3.Connection, text: str) -> list[dict[str, Any]]:
    """全库找这段文字还留在哪（只做子串匹配，不猜、不改写）。"""
    hits: list[dict[str, Any]] = []
    if not text.strip():
        return hits
    for table, column in _TEXT_COLUMNS:
        rows = conn.execute(
            f"SELECT id FROM {table} WHERE {column} LIKE '%' || ? || '%'", (text,)
        ).fetchall()
        hits += [{"table": table, "column": column, "id": int(row["id"])} for row in rows]
    return hits


def purge_preview(conn: sqlite3.Connection, scope: str, memory_id: int) -> dict[str, Any]:
    """彻底删除的影响预览：动的是哪些地方、删完还剩几处删不掉。**这一步不改任何数据。**"""
    row = _row_of(conn, scope, memory_id)
    content = str(row["content"])
    proposals = [
        int(item["id"])
        for item in conn.execute("SELECT * FROM proposal WHERE kind = ?", (KIND,)).fetchall()
        if _json_of(item["payload"]).get("target_id") == int(memory_id)
    ]
    return {
        "scope": scope,
        "id": int(memory_id),
        "content": content,
        "evidence": evidence_of(conn, scope, memory_id),
        "candidate_proposals": proposals,
        "copies": _copies(conn, content),
        "irreversible": True,
        "note": (
            "彻底删除不可恢复：正文、候选里的内容、证据摘录都会消失，来源里的原句会被换成"
            "「" + PURGED_TEXT + "」，最后只留下一条不含内容的墓碑。"
        ),
    }


def purge(conn: sqlite3.Connection, scope: str, memory_id: int, *, reason: str) -> dict[str, Any]:
    """执行彻底删除，然后**全库复扫**证明清干净了。

    清不干净（比如同一句话还写在某份报告里，而那条关系没建立过证据）就**拒绝宣称成功**：
    返回 `complete: false` 与残留位置，由用户决定下一步。没删掉就说删掉了，是这一整块里
    最不能犯的错。
    """
    row = _row_of(conn, scope, memory_id)
    content = str(row["content"])
    owner = None if scope == SCOPE_GLOBAL else int(row["plan_id"])
    affected = 0

    # 1) 证据摘录 + 来源里被引用的那一句
    for item in conn.execute(
        "SELECT * FROM memory_evidence WHERE scope = ? AND memory_id = ?",
        (scope, int(memory_id)),
    ).fetchall():
        affected += _scrub_source(conn, str(item["source_type"]), int(item["source_id"]), str(item["excerpt"]))
        conn.execute("DELETE FROM memory_evidence WHERE id = ?", (int(item["id"]),))
        affected += 1

    # 2) 引用了这条记忆的候选：payload 里的正文一并抹掉（留一个「这里删过了」的标记）
    for item in conn.execute("SELECT * FROM proposal WHERE kind = ?", (KIND,)).fetchall():
        payload = _json_of(item["payload"])
        if payload.get("target_id") != int(memory_id):
            continue
        conn.execute(
            "UPDATE proposal SET payload = ? WHERE id = ?",
            (
                json.dumps(
                    {"action": payload.get("action"), "purged": True, "note": PURGED_TEXT},
                    ensure_ascii=False,
                ),
                int(item["id"]),
            ),
        )
        affected += 1

    # 3) 这条记忆自己的台账流水：`create` / `supersede` 那几行的 before/after 就是正文的副本。
    #    行留着（「什么时候记过、什么时候删的」这条审计事实不能丢），只把正文那一段换掉。
    entity_type = _entity_type_of(scope)
    affected += _scrub_ledger(conn, entity_type, content)

    # 4) 记忆正文本身（物理删除——这一层没有「留痕」的位置，墓碑替它留痕）
    conn.execute(f"DELETE FROM {_table_of(scope)} WHERE id = ?", (int(memory_id),))
    affected += 1

    leftover = _copies(conn, content)
    conn.execute(
        "INSERT INTO memory_deletion (scope, memory_id, plan_id, reason, affected, deleted_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (scope, int(memory_id), owner, str(reason or "").strip() or None, affected, now_iso()),
    )
    conn.commit()
    return {
        "scope": scope,
        "id": int(memory_id),
        "affected": affected,
        "complete": not leftover,
        "leftover": leftover,
        "note": (
            "已经清干净：正文、候选、证据与来源里引用的原句都不在了，只剩墓碑。"
            if not leftover
            else "有地方没清掉——见 leftover，这几处要自己决定怎么处理；**不敢说删除成功**。"
        ),
    }


def _scrub_ledger(conn: sqlite3.Connection, entity_type: str, content: str) -> int:
    """把这条记忆留在台账流水里的正文换掉，**行数与时间都留着**。

    为什么要动台账：新增一条记忆时，`ledger.create_active` 会把正文写进 `after_value`——
    那是正文的另一份副本。只清业务表，「彻底删除」就只是句好听的话；行本身不能删，
    它记的「什么时候记过这条、什么时候按用户要求删的」正是审计要留的那部分。

    为什么**按内容找**而不是按 id 找：「取代」那条流水的 `entity_id` 挂的是**旧行**的编号
    （`ledger.supersede` 的写法），照新行 id 去清会正好漏掉它——而它偏偏就是新正文的副本。
    所以这里按「这一类的流水里出现过这段话」来清（冒烟第 16 步先踩到这个坑）。
    """
    touched = 0
    for column in ("before_value", "after_value", "reason"):
        cursor = conn.execute(
            f"UPDATE ledger_event SET {column} = REPLACE({column}, ?, ?)"
            f" WHERE entity_type = ? AND {column} LIKE '%' || ? || '%'",
            (content, PURGED_TEXT, entity_type, content),
        )
        touched += max(cursor.rowcount, 0)
    return touched


def _scrub_source(
    conn: sqlite3.Connection, source_type: str, source_id: int, excerpt: str
) -> int:
    """把来源里的证据原句换成「已按用户要求删除」，**保留行的状态与时间**。

    来源行往往另有业务意义（一句「这周没动」的报告、一条否决理由），整行删掉会把别的
    事实一起抹掉——所以只换那一句。
    """
    columns = {
        "plan_dialogue": ("plan_dialogue", ("content",)),
        "plan_chat": ("plan_chat", ("content",)),
        "report": ("report", ("note",)),
        "candidate": ("candidate", ("why",)),
        "proposal": ("proposal", ("payload",)),
        "field_change": ("ledger_event", ("reason", "before_value", "after_value")),
    }.get(source_type)
    if columns is None:
        return 0
    table, names = columns
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (int(source_id),)).fetchone()
    if row is None:
        return 0
    changed = 0
    for name in names:
        value = row[name]
        if value and excerpt in str(value):
            conn.execute(
                f"UPDATE {table} SET {name} = ? WHERE id = ?",
                (str(value).replace(excerpt, PURGED_TEXT), int(source_id)),
            )
            changed += 1
    return changed
