"""计划表：节点状态机、报告推进、落后量、周检查点判定。

为什么规则都集中在这里：SPEC 第 10 节把「什么算落后、什么算阶段完成」列进了
确定性优先清单——这类判断一律不调模型，必须可复现、可测试，所以只放一处。

写入仍然守台账铁律：真正的状态变更调用 `ledger.set_status`，
本模块只负责判断「这次迁移合不合法」和「迁完之后该产出什么提案」。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from typing import Any

from . import contract, ledger
from .db import atomic, now_iso

NODE_STATUSES = ("not_started", "in_progress", "done", "stuck", "skipped")

# 终态：到了这里这个节点就不用再管了。
SETTLED_STATUSES = ("done", "skipped")

# 成果闭环 P1：计划的双模式。legacy = 旧流程（任务收尾 + 交付物即完成），
# outcome = 成果契约流程（任务收尾 + 证据 + 用户逐条验收）。
COMPLETION_MODES = ("legacy", "outcome")
REVIEW_DECISIONS = ("accepted", "needs_work", "not_met")
CRITERIA_STATES = ("met", "unmet", "unknown")
CLOSE_KINDS = ("completed", "stopped")

# 旧计划升级为成果流程时，每个现有阶段必须明确选一种处置（方案 §9.2.5）：
#   include  —— 补全条件/证据映射，绑定当前契约，纳入 v2 完成门槛；
#   skipped  —— 明确跳过（留理由，进台账）；
#   history  —— 保留为历史：不绑定契约、不进 v2 完成门槛，旧报告与交付物原样只读。
UPGRADE_DISPOSITIONS = ("include", "skipped", "history")

# 台账的生命周期终态。方案 B 之后节点不可能再变成这两个（台账层直接禁止调用），
# 这里过滤是给历史数据、别的库、以及将来有人绕过接口写库兜底：
# 读到它就当这条记录不存在——否则会出现「已作废的阶段仍是当前阶段」
# 和「已作废的节点仍挡着同名重建」这两种幽灵。
INVALID_STATUSES = ("void", "superseded")


def not_invalid(column: str = "status") -> str:
    """排除台账生命周期终态的 SQL 片段。值只来自上面的常量，所以拼接是安全的。"""
    return f"{column} NOT IN ({', '.join(repr(value) for value in INVALID_STATUSES)})"

# 报告状态（你提交时的四选一）→ 节点状态。
# 为什么要有这层映射：报告说的是「我这边怎么样了」，节点状态是系统记的账，
# 两者不是一回事——「部分完成」落到节点上就是「进行中」。
REPORT_STATUSES = ("done", "partial", "stuck", "skipped")
REPORT_TO_NODE: dict[str, str] = {
    "done": "done",
    "partial": "in_progress",
    "stuck": "stuck",
    "skipped": "skipped",
}

# 合法迁移表。设计取舍：报告是你对现实的陈述，所以状态机对「往前推」很宽容
# （没开始也能直接报完成，因为现实里你常常是先做完了才回来记），
# 但不允许把已经完成的东西悄悄降级——done 只能重新打开成进行中，
# 不能改判成跳过或未开始，否则台账里会出现「完成过又不算数」的糊涂账。
LEGAL_TRANSITIONS: dict[str, set[str]] = {
    "not_started": {"in_progress", "done", "stuck", "skipped"},
    "in_progress": {"done", "stuck", "skipped"},
    "stuck": {"in_progress", "done", "skipped"},
    "done": {"in_progress"},
    "skipped": {"in_progress", "done"},
}


class PlanError(RuntimeError):
    """计划层面的明确错误：非法迁移、缺理由、节点不存在等。

    与台账同样的态度：明确报错，不静默忽略。
    """


class DuplicateNode(PlanError):
    """同一层级下已经有一条还没收尾的同名节点——多半是重复提交。

    单独一个类型，是为了让接口层能把它翻译成 409（冲突），
    而不是混在 400 里说不清到底是参数错还是状态冲突。
    """


class PlanConflict(PlanError):
    """与现状冲突的收尾/验收请求 → 接口层翻成 409。

    典型场景：成果计划还有未验收阶段就想按「完成」收尾；已收尾的计划
    想用另一种收尾语义覆盖。
    """


def plan_mode(conn: sqlite3.Connection, plan_id: int) -> str:
    """计划的双模式：outcome（成果契约流程）或 legacy（旧流程）。

    判定的唯一依据是 plan 行自己的 `completion_mode` 列——前端说了不算，
    传参说了也不算。读不到列（理论上只在未迁移的库里）按 legacy 兜底。
    """
    row = conn.execute(
        "SELECT completion_mode FROM plan WHERE id = ?", (plan_id,)
    ).fetchone()
    value = None if row is None else row["completion_mode"]
    return value if value in COMPLETION_MODES else "legacy"


def _stage_criteria_of(stage: sqlite3.Row) -> list[dict[str, Any]]:
    """阶段自己的验收条件（JSON）；没有就返回空列表。"""
    try:
        parsed = json.loads(stage["acceptance_criteria"] or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _stage_requirements_of(stage: sqlite3.Row) -> list[dict[str, Any]]:
    """阶段自己的证据要求（JSON）；没有就返回空列表。"""
    try:
        parsed = json.loads(stage["evidence_requirements"] or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def get_node(conn: sqlite3.Connection, node_id: int) -> sqlite3.Row | None:
    return conn.execute(
        f"SELECT * FROM plan_node WHERE id = ? AND {not_invalid()}", (node_id,)
    ).fetchone()


def get_stages(conn: sqlite3.Connection, plan_id: int) -> list[sqlite3.Row]:
    """计划下的阶段，按计划里的顺序。"""
    return list(
        conn.execute(
            f"""SELECT * FROM plan_node
               WHERE plan_id = ? AND level = 'stage' AND {not_invalid()}
               ORDER BY sort_order, id""",
            (plan_id,),
        ).fetchall()
    )


def find_open_duplicate(
    conn: sqlite3.Connection,
    plan_id: int,
    level: str,
    title: str,
    parent_id: int | None = None,
) -> sqlite3.Row | None:
    """找出同一层级下同名的、还没收尾的节点；没有就返回 None。

    为什么只把"还开着"的算重复：已经完成或跳过的同名节点代表"上一轮做完了"，
    你要重开一条同名的是正当需求；而一条还开着的同名节点，基本只可能是
    重复提交（前端双击、请求重发），再建一条只会让计划表变脏。
    """
    cleaned = str(title or "").strip()
    if not cleaned:
        return None
    # parent_id 对阶段而言是 NULL，而 SQL 里 NULL = NULL 不成立，所以两种情形分开写
    if parent_id is None:
        rows = conn.execute(
            f"""SELECT * FROM plan_node
               WHERE plan_id = ? AND parent_id IS NULL AND level = ? AND title = ?
                 AND {not_invalid()}""",
            (plan_id, level, cleaned),
        )
    else:
        rows = conn.execute(
            f"""SELECT * FROM plan_node
               WHERE plan_id = ? AND parent_id = ? AND level = ? AND title = ?
                 AND {not_invalid()}""",
            (plan_id, parent_id, level, cleaned),
        )
    for row in rows:
        if row["status"] not in SETTLED_STATUSES:
            return row
    return None


def assert_no_open_duplicate(
    conn: sqlite3.Connection,
    plan_id: int,
    level: str,
    title: str,
    parent_id: int | None = None,
) -> None:
    """同一层级下已有未收尾的同名节点就报错——这是防双击的那道闸。"""
    existing = find_open_duplicate(conn, plan_id, level, title, parent_id)
    if existing is None:
        return
    kind = "阶段" if level == "stage" else "检查点"
    raise DuplicateNode(
        f"这个计划里已经有一条还没收尾的同名{kind}「{str(title).strip()}」"
        f"（id={existing['id']}），不再重复创建"
    )


def normalize_stage_criteria(
    items: Any, *, prefix: str, label: str
) -> str | None:
    """把阶段验收条件 / 证据要求规范成 JSON 文本（成果闭环 P1）。

    稳定 id 由服务端按顺序补齐（`c1..` / `e1..`，阶段内唯一——方案 §4.3.1）；
    客户端给了 id 也收，但重复一律拒绝。全空返回 None。
    `prefix="c"` 是验收条件（text/required）；`prefix="e"` 是证据要求
    （kind/required/description，与成果契约的证据要求同一形状）。
    """
    if items is None:
        return None
    if not isinstance(items, list):
        raise PlanError(f"{label}要是一个对象数组")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise PlanError(f"{label}第 {index} 条不是对象")
        given = str(item.get("id") or "").strip()
        entry_id = given or f"{prefix}{index}"
        if entry_id in seen:
            raise PlanError(f"{label}的 id「{entry_id}」重复了")
        seen.add(entry_id)
        required = bool(item.get("required", True))
        if prefix == "e":
            kind = str(item.get("kind") or "").strip()
            if kind not in contract.EVIDENCE_KINDS:
                raise PlanError(
                    f"{label}第 {index} 条的类型「{kind}」不在允许范围"
                    f"（{' / '.join(contract.EVIDENCE_KINDS)}）"
                )
            result.append({
                "id": entry_id,
                "kind": kind,
                "required": required,
                "description": str(item.get("description") or "").strip(),
            })
        else:
            text = str(item.get("text") or "").strip()
            if not text:
                raise PlanError(f"{label}第 {index} 条不能为空")
            result.append({"id": entry_id, "text": text, "required": required})
    return json.dumps(result, ensure_ascii=False) if result else None


def _normalize_criterion_ids(
    conn: sqlite3.Connection, plan_id: int, ids: list[str] | None
) -> str | None:
    """阶段承接的成果契约条件 id：成果计划必须引用**当前契约**里存在的 id；
    旧流程的阶段不绑契约条件（它还没有契约）。"""
    if ids is None:
        return None
    cleaned = [str(item or "").strip() for item in ids if str(item or "").strip()]
    if not cleaned:
        return None
    if plan_mode(conn, plan_id) != "outcome":
        raise PlanError("旧流程的阶段不绑成果契约条件——先把计划升级补全成果契约")
    active = contract.active(conn, plan_id)
    if active is None:
        raise PlanError("这个计划还没有有效的成果契约，条件 id 无处引用")
    known = {item["id"] for item in json.loads(active["acceptance_criteria"])}
    unknown = sorted(set(cleaned) - known)
    if unknown:
        raise PlanError(
            f"contract_criterion_ids 里有当前契约不存在的条件 id：{'、'.join(unknown)}"
        )
    return json.dumps(cleaned, ensure_ascii=False)


def add_node(
    conn: sqlite3.Connection,
    plan_id: int,
    level: str,
    title: str,
    parent_id: int | None = None,
    deliverable: str | None = None,
    due_date: str | None = None,
    sort_order: int = 0,
    actor: str = "user",
    *,
    purpose: str | None = None,
    why_now: str | None = None,
    acceptance_criteria: Any = None,
    evidence_requirements: Any = None,
    contract_criterion_ids: list[str] | None = None,
) -> int:
    """新建一个阶段或检查点。

    建节点一律走这里：先挡重复再落库。放在这个模块而不是接口层，
    是为了让规则没地方绕过——以后 P3 由提案建节点时也会经过同一道闸。

    成果闭环 P1 新增的几个阶段字段（全部可选，老入口一个不传就跟以前一样）：
    `purpose` / `why_now` 是人话说明；`acceptance_criteria` / `evidence_requirements`
    是机器可校验的条件与证据要求；成果计划的阶段自动绑定当前契约版本（`contract_id`），
    `contract_criterion_ids` 必须引用当前契约里真实存在的条件 id。
    """
    if level != "stage":
        guarded = [
            ("purpose", purpose), ("why_now", why_now),
            ("acceptance_criteria", acceptance_criteria),
            ("evidence_requirements", evidence_requirements),
            ("contract_criterion_ids", contract_criterion_ids),
        ]
        if any(value is not None for _, value in guarded):
            raise PlanError("这些验收字段只属于阶段（任务与周打卡上没有这个概念）")

    values: dict[str, Any] = {}
    if level == "stage":
        criteria_json = normalize_stage_criteria(
            acceptance_criteria, prefix="c", label="验收条件"
        )
        requirements_json = normalize_stage_criteria(
            evidence_requirements, prefix="e", label="证据要求"
        )
        values.update({
            "purpose": (str(purpose).strip() or None) if purpose is not None else None,
            "why_now": (str(why_now).strip() or None) if why_now is not None else None,
            "acceptance_criteria": criteria_json,
            "evidence_requirements": requirements_json,
            "contract_criterion_ids": _normalize_criterion_ids(conn, plan_id, contract_criterion_ids),
        })
        if plan_mode(conn, plan_id) == "outcome":
            active = contract.active(conn, plan_id)
            if active is not None:
                values["contract_id"] = int(active["id"])

    assert_no_open_duplicate(conn, plan_id, level, title, parent_id)
    return ledger.create_active(
        conn,
        "plan_node",
        {
            "plan_id": plan_id,
            "parent_id": parent_id,
            "level": level,
            "title": str(title).strip(),
            "deliverable": deliverable,
            "due_date": due_date,
            "sort_order": sort_order,
            **values,
        },
        actor=actor,
    )


def assert_transition(before: str, after: str) -> None:
    """校验一次状态迁移。同状态视为合法（幂等），其余按 LEGAL_TRANSITIONS 判。"""
    if before == after:
        return
    if after not in LEGAL_TRANSITIONS.get(before, set()):
        raise PlanError(f"非法迁移：{before} → {after}")


def transition_node(
    conn: sqlite3.Connection,
    node_id: int,
    new_status: str,
    reason: str,
    actor: str = "user",
) -> str:
    """按状态机迁移一个节点。非法迁移抛 PlanError，且不留下任何痕迹。"""
    if new_status not in NODE_STATUSES:
        raise PlanError(f"未知状态：{new_status}；可用状态：{' / '.join(NODE_STATUSES)}")
    if not str(reason or "").strip():
        raise PlanError("状态迁移必须写明理由，否则台账回答不了「为什么变成这样」")

    node = get_node(conn, node_id)
    if node is None:
        raise PlanError(f"节点 id={node_id} 不存在")

    before = node["status"]
    if before == new_status:
        return before  # 幂等：重复设置同一状态不算错，也不写噪音流水
    assert_transition(before, new_status)

    return ledger.set_status(conn, "plan_node", node_id, new_status, actor=actor, reason=reason)


def stage_completion(conn: sqlite3.Connection, stage_id: int) -> dict[str, Any]:
    """阶段的任务完成度：只数**任务**层（周打卡不参与阶段完成判定，SPEC 决策 30/32）。

    「收尾」= 完成（打勾）或跳过。跳过是你裁定过的结果，不该把阶段永远卡在那里。
    `all_tasks_settled` 在「没有任务」时为 True——空集天然满足，老/空阶段不被卡死；
    `complete` 保留「有任务且全收尾」的口径，只给界面显示用。
    """
    nodes = conn.execute(
        f"""SELECT id, title, status FROM plan_node
           WHERE parent_id = ? AND level = 'task' AND {not_invalid()}
           ORDER BY sort_order, id""",
        (stage_id,),
    ).fetchall()
    settled = [node for node in nodes if node["status"] in SETTLED_STATUSES]
    return {
        "stage_id": stage_id,
        "total": len(nodes),
        "settled": len(settled),
        "done": len([node for node in nodes if node["status"] == "done"]),
        "skipped": len([node for node in nodes if node["status"] == "skipped"]),
        "complete": bool(nodes) and len(settled) == len(nodes),
        "all_tasks_settled": len(settled) == len(nodes),
        "open_titles": [node["title"] for node in nodes if node["status"] not in SETTLED_STATUSES],
    }


def deliverable_submission(conn: sqlite3.Connection, stage_id: int) -> sqlite3.Row | None:
    """阶段最新一次交付物提交；一次都没提交过就返回 None。

    重提交 = 新行，所以「当前值」永远是最新那一行，历史全在表里与台账流水里。
    """
    return conn.execute(
        """SELECT * FROM deliverable_submission
           WHERE node_id = ? ORDER BY created_at DESC, id DESC LIMIT 1""",
        (stage_id,),
    ).fetchone()


def stage_finished(conn: sqlite3.Connection, stage: sqlite3.Row) -> bool:
    """阶段是否完成——按计划的双模式分两条判定（成果闭环 P1，方案 §5.3）。

    **legacy（旧流程）**：① 阶段自己到了终态，或 ② 全部任务打勾/跳过 且 交付物已提交。

    **outcome（成果契约流程）**：跳过的阶段视同完成；其余阶段必须
    任务全收尾 **且** 当前验收（is_current=1）为 accepted **且** 每个必需条件
    状态为 met **且** 每个必需证据要求都有匹配类型的证据关联。报告把阶段推到
    done、只交链接、AI 说「看起来可以」，都不会让这里的判定通过——验收是
    用户逐条确认的结果，不是状态机的副产品。

    升级时被用户标成「保留为历史」的旧阶段（outcome 计划里没有绑定契约的阶段）
    不算「当前阶段」，也不进完成门槛——它们是只读历史，不是待验收的成果步骤。
    """
    mode = plan_mode(conn, int(stage["plan_id"]))
    if mode == "outcome" and stage["contract_id"] is None:
        return True
    if stage["status"] == "skipped":
        return True
    if mode == "outcome":
        review = current_stage_review(conn, int(stage["id"]))
        if review is None or str(review["decision"]) != "accepted":
            return False
        if not _review_satisfies(conn, stage, review):
            return False
        return stage_completion(conn, int(stage["id"]))["all_tasks_settled"]
    if stage["status"] in SETTLED_STATUSES:
        return True
    if not stage_completion(conn, int(stage["id"]))["all_tasks_settled"]:
        return False
    return deliverable_submission(conn, int(stage["id"])) is not None


# ---------- 证据提交与阶段验收（成果闭环 P1，方案 §5.3 / §6.3） ----------
#
# 提交证据与提交验收是两个独立动作：证据只增历史记录，验收才可能让阶段达标。
# 验收记录走追加式（每次一行，is_current 标当前），失效只翻当前性、永不改写——
# 「历史只读」由结构保证，不靠接口自觉。

def current_stage_review(conn: sqlite3.Connection, stage_id: int) -> sqlite3.Row | None:
    """阶段当前有效的验收记录；从没验收过或已全部失效就返回 None。"""
    return conn.execute(
        "SELECT * FROM stage_review WHERE node_id = ? AND is_current = 1"
        " ORDER BY id DESC LIMIT 1",
        (stage_id,),
    ).fetchone()


def stage_reviews(conn: sqlite3.Connection, stage_id: int, limit: int = 20) -> list[sqlite3.Row]:
    """阶段的验收历史，新的在前（只读；含已失效的）。"""
    return list(
        conn.execute(
            "SELECT * FROM stage_review WHERE node_id = ? ORDER BY id DESC LIMIT ?",
            (stage_id, limit),
        ).fetchall()
    )


def stage_evidence(conn: sqlite3.Connection, stage_id: int, limit: int = 50) -> list[sqlite3.Row]:
    """阶段提交过的证据，新的在前。"""
    return list(
        conn.execute(
            "SELECT * FROM evidence_submission WHERE node_id = ?"
            " ORDER BY created_at DESC, id DESC LIMIT ?",
            (stage_id, limit),
        ).fetchall()
    )


def submit_evidence(
    conn: sqlite3.Connection,
    node_id: int,
    kind: str,
    reference: str | None,
    note: str,
    actor: str = "user",
    *, is_legacy: bool = False,
) -> dict[str, Any]:
    """提交一条阶段证据：只新增历史记录，**不改阶段状态、不算完成**（方案 §3.7）。

    `kind=text` 允许没有链接（纯文字结果）；其余类型要给链接或引用。
    `is_legacy=True` 仅供旧交付物接口的兼容路径使用（kind 固定 legacy），
    用户提交走不了这个口子——旧链接不能自动满足任何新的必需证据要求。
    """
    expected_kind = "legacy" if is_legacy else kind
    if expected_kind not in (*contract.EVIDENCE_KINDS, "legacy"):
        raise PlanError(
            f"未知证据类型「{kind}」；可用：{' / '.join(contract.EVIDENCE_KINDS)}"
        )
    if not str(note or "").strip():
        raise PlanError("证据要写一句话说明——它证明了什么、看哪里")
    node = _require_level(conn, node_id, "stage", "提交证据")
    cleaned_reference = str(reference or "").strip()
    if expected_kind != "text" and not cleaned_reference:
        raise PlanError("这类证据要给链接或引用；纯文字结果请选 text 类型")

    timestamp = now_iso()
    with atomic(conn):
        cursor = conn.execute(
            """INSERT INTO evidence_submission (node_id, kind, reference, note, submitted_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (node_id, expected_kind, cleaned_reference or None, str(note).strip(), actor, timestamp),
        )
        submission_id = int(cursor.lastrowid)
        ledger.log_event(
            conn, "plan_node", node_id, "evidence_submit", None,
            f"{expected_kind} {cleaned_reference}".strip(), str(note).strip(), actor,
        )
    return {
        "id": submission_id,
        "node_id": node_id,
        "level": node["level"],
        "kind": expected_kind,
        "reference": cleaned_reference or None,
        "note": str(note).strip(),
        "created_at": timestamp,
    }


def invalidate_current_review(
    conn: sqlite3.Connection, stage_id: int, reason: str, actor: str = "user"
) -> int | None:
    """使一个阶段**当前**的验收失效：只翻 is_current、写 invalidated_at，历史行原样保留。

    触发点（方案 §5.3 迁移表 + 本轮 P1 口径）：阶段放回、其下任务放回、阶段跳过、
    阶段验收条件或交付要求变更、契约版本变化影响验收。返回被失效的 review id；没有
    当前验收就返回 None（不写噪音流水）。
    """
    row = current_stage_review(conn, stage_id)
    if row is None:
        return None
    conn.execute(
        "UPDATE stage_review SET is_current = 0, invalidated_at = ? WHERE id = ?",
        (now_iso(), int(row["id"])),
    )
    ledger.log_event(
        conn, "plan_node", stage_id, "stage_review_invalidated",
        str(row["decision"]), None,
        str(reason or "").strip() or "阶段需要重新确认验收",
        actor,
    )
    return int(row["id"])


def _stage_criterion_ids(stage: sqlite3.Row) -> set[str]:
    """阶段承接的成果契约条件 id（JSON 数组）；解不开当作没有承接。"""
    try:
        parsed = json.loads(stage["contract_criterion_ids"] or "[]")
    except (TypeError, ValueError):
        return set()
    if not isinstance(parsed, list):
        return set()
    return {str(item) for item in parsed}


def invalidate_affected_reviews(
    conn: sqlite3.Connection,
    plan_id: int,
    old_row: sqlite3.Row,
    new_payload: dict[str, Any],
) -> int:
    """契约升级时，只失效**受影响阶段**的当前验收（方案 §4.4.3 / §6.3）。

    受影响 = 该阶段承接的契约条件发生变化，或契约级必需证据要求发生变化；
    未受影响的阶段继续保留当前 accepted，不被连带失效。

    按每条 review 自己的 `contract_id` 与新契约比较：先发生一次只改说明的版本、
    再改验收标准时，更早版本留下的 current 验收同样会被这个逐阶段比较抓到。
    """
    rows = conn.execute(
        """SELECT review.node_id, review.contract_id
           FROM stage_review AS review
           JOIN plan_node AS node ON node.id = review.node_id
           WHERE node.plan_id = ? AND review.is_current = 1""",
        (plan_id,),
    ).fetchall()
    count = 0
    for row in rows:
        stage = get_node(conn, int(row["node_id"]))
        if stage is None:
            continue
        review_contract = contract.get(conn, int(row["contract_id"]))
        if review_contract is None:
            continue
        if not contract.affects_stage(
            review_contract, new_payload, _stage_criterion_ids(stage)
        ):
            continue
        if invalidate_current_review(
            conn, int(row["node_id"]),
            "成果契约升级：这个阶段承接的验收条件或必需证据要求已变化，需按新标准重看",
        ) is not None:
            count += 1
    return count


def _review_satisfies(
    conn: sqlite3.Connection, stage: sqlite3.Row, review: sqlite3.Row
) -> bool:
    """一条 accepted 验收在当下是否仍然成立：必需条件全 met，且每个必需证据要求
    都有验收时关联的、类型匹配的证据**仍然存在**（证据行不删，正常总在）。

    这里再做一次契约版本兼容检查，作为数据层兜底：正常升级会主动失效受影响的
    current review，但如果旧数据经过说明性版本后又发生标准变化，更早版本的 review
    也不能绕过完成门槛。**逐阶段**比较——只看这个阶段承接的条件，别的阶段的标准
    变化不牵连它（方案 §4.4.3「未受影响阶段继续保留当前 accepted」）。
    """
    active_contract = contract.active(conn, int(stage["plan_id"]))
    review_contract = contract.get(conn, int(review["contract_id"]))
    if active_contract is None or review_contract is None:
        return False
    if contract.affects_stage(
        review_contract, contract.public(active_contract), _stage_criterion_ids(stage)
    ):
        return False

    try:
        state = json.loads(review["criteria_state"])
    except (TypeError, ValueError):
        return False
    for criterion in _stage_criteria_of(stage):
        if criterion.get("required") and state.get(str(criterion.get("id"))) != "met":
            return False
    try:
        submission_ids = json.loads(review["submission_ids"])
    except (TypeError, ValueError):
        return False
    kinds: set[str] = set()
    for submission_id in submission_ids if isinstance(submission_ids, list) else []:
        row = conn.execute(
            "SELECT kind FROM evidence_submission WHERE id = ? AND node_id = ?",
            (int(submission_id), int(stage["id"])),
        ).fetchone()
        if row is not None:
            kinds.add(str(row["kind"]))
    for requirement in _stage_requirements_of(stage):
        if requirement.get("required") and str(requirement.get("kind")) not in kinds:
            return False
    return True


def review_stage(
    conn: sqlite3.Connection,
    node_id: int,
    *,
    contract_id: int,
    submission_ids: list[int] | None = None,
    criteria_state: dict[str, str] | None = None,
    decision: str,
    note: str,
    actor: str = "user",
) -> dict[str, Any]:
    """提交一条阶段验收（方案 §6.3 的全部门槛集中在这里）：

    - 只对阶段；legacy 计划、没绑契约的阶段不能提交 v2 验收。
    - `contract_id` 必须等于计划**当前有效**的契约——后端是唯一判断源，
      客户端拿旧版本号来验收会被拒（历史快照各自留在旧记录里，不伪装）。
    - `criteria_state` 必须恰好覆盖该阶段验收条件的稳定 id，取值 met/unmet/unknown。
    - `submission_ids` 必须全部属于本阶段——跨阶段引用一律拒绝。
    - `accepted` 还要求：全部必需条件 met、每个必需证据要求有匹配类型的证据、
      至少关联一条本阶段证据。
    - 重复提交（同阶段、同契约、同条件状态、同证据集合、同决定、同说明）幂等返回，
      不写噪音 review。

    验收记录、旧验收失效、阶段状态迁移与台账流水在**同一事务**里落盘。
    """
    if decision not in REVIEW_DECISIONS:
        raise PlanError(f"未知验收决定：{decision}；可用：{' / '.join(REVIEW_DECISIONS)}")
    if not str(note or "").strip():
        raise PlanError("验收必须写一句说明——它回答「这次是凭什么判的」")
    node = _require_level(conn, node_id, "stage", "验收")
    if node["status"] == "skipped":
        raise PlanConflict("这个阶段已经跳过了——跳过是裁定过的结果，不再需要验收")

    plan_id = int(node["plan_id"])
    if plan_mode(conn, plan_id) != "outcome":
        raise PlanError("这个计划还是旧流程：先补全成果契约升级为成果计划，才能提交阶段验收")
    active = contract.active(conn, plan_id)
    if active is None:
        raise PlanError("这个计划还没有有效的成果契约，无处挂验收")
    if int(contract_id) != int(active["id"]):
        raise PlanError(
            f"契约版本已经更新（当前第 {active['version']} 版，id={active['id']}）——"
            "验收必须按当前版本判，旧版本的验收已随升级失效"
        )

    criteria = _stage_criteria_of(node)
    if not criteria:
        raise PlanError("这个阶段还没有验收条件——先在字段入口把条件补上，再谈验收")
    known_ids = {str(item["id"]) for item in criteria}
    state = {str(key): str(value or "").strip() for key, value in (criteria_state or {}).items()}
    if set(state) != known_ids:
        missing = sorted(known_ids - set(state))
        extra = sorted(set(state) - known_ids)
        raise PlanError(
            "criteria_state 必须恰好覆盖这个阶段的全部验收条件"
            + (f"：缺 {'、'.join(missing)}" if missing else "")
            + (f"；不认识的 id：{'、'.join(extra)}" if extra else "")
        )
    bad_values = sorted({value for value in state.values() if value not in CRITERIA_STATES})
    if bad_values:
        raise PlanError(f"条件状态只能是 {' / '.join(CRITERIA_STATES)}，出现了：{'、'.join(bad_values)}")

    cleaned_ids = sorted({int(item) for item in (submission_ids or [])})
    submissions = [
        conn.execute(
            "SELECT * FROM evidence_submission WHERE id = ? AND node_id = ?",
            (item, node_id),
        ).fetchone()
        for item in cleaned_ids
    ]
    if any(row is None for row in submissions):
        raise PlanError("有些证据不属于当前阶段（或不存在）——验收只能关联本阶段提交的证据")

    if decision == "accepted":
        unmet = [
            str(item["text"]) for item in criteria
            if item.get("required") and state.get(str(item["id"])) != "met"
        ]
        if unmet:
            raise PlanError(
                f"还有 {len(unmet)} 个必需条件未满足，不能按「达标」验收：{unmet[0]}"
                + (f" 等 {len(unmet)} 条" if len(unmet) > 1 else "")
            )
        if not cleaned_ids:
            raise PlanError("达标验收必须至少关联一条本阶段提交的证据——只打勾任务或只报完成都不算")
        available_kinds = {str(row["kind"]) for row in submissions if row is not None}
        missing = [
            str(item.get("description") or item.get("kind"))
            for item in _stage_requirements_of(node)
            if item.get("required") and str(item.get("kind")) not in available_kinds
        ]
        if missing:
            raise PlanError(f"还缺必需证据：{'；'.join(missing)}——补交证据后再验收")

    # 幂等：同一阶段、同一契约、同样的条件状态、同样的证据集合、同样的决定与说明，
    # 直接返回已有记录——重复验收不该制造内容完全相同的噪音行。
    current = current_stage_review(conn, node_id)
    if current is not None:
        try:
            same_state = json.loads(current["criteria_state"]) == state
            same_ids = json.loads(current["submission_ids"]) == cleaned_ids
        except (TypeError, ValueError):
            same_state = same_ids = False
        if (
            int(current["contract_id"]) == int(contract_id)
            and str(current["decision"]) == decision
            and str(current["note"]) == str(note).strip()
            and same_state
            and same_ids
        ):
            result = _review_result(conn, current, node)
            result["duplicate"] = True
            return result

    criteria_snapshot = json.dumps(criteria, ensure_ascii=False)
    submission_snapshot = json.dumps(
        [
            {
                "id": int(row["id"]),
                "kind": row["kind"],
                "reference": row["reference"],
                "note": row["note"],
                "created_at": row["created_at"],
            }
            for row in submissions
            if row is not None
        ],
        ensure_ascii=False,
    )
    cleaned_note = str(note).strip()
    with atomic(conn):
        invalidated = invalidate_current_review(
            conn, node_id, f"新的验收（{decision}）替代了这一条", actor
        )
        timestamp = now_iso()
        cursor = conn.execute(
            """INSERT INTO stage_review
               (node_id, contract_id, decision, criteria_snapshot, criteria_state,
                submission_snapshot, submission_ids, note, is_current, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (
                node_id, int(contract_id), decision, criteria_snapshot,
                json.dumps(state, ensure_ascii=False), submission_snapshot,
                json.dumps(cleaned_ids), cleaned_note, timestamp,
            ),
        )
        review_id = int(cursor.lastrowid)
        ledger.log_event(
            conn, "plan_node", node_id, "stage_review", None, decision,
            f"验收 #{review_id}：{cleaned_note}", actor,
        )
        # accepted 把阶段推到 done，needs_work / not_met 拉回 in_progress——
        # 都是普通状态机迁移（非法组合在这里抛错、整体回滚，不会留半截）。
        target = "done" if decision == "accepted" else "in_progress"
        if str(node["status"]) != target:
            assert_transition(str(node["status"]), target)
            ledger.set_status(
                conn, "plan_node", node_id, target, actor=actor,
                reason=f"阶段验收：{decision}",
            )
    row = conn.execute("SELECT * FROM stage_review WHERE id = ?", (review_id,)).fetchone()
    result = _review_result(conn, row, node)
    result["invalidated_review_id"] = invalidated
    result["duplicate"] = False
    return result


def _review_result(
    conn: sqlite3.Connection, row: sqlite3.Row, node: sqlite3.Row
) -> dict[str, Any]:
    def _loads(text: Any, fallback: Any) -> Any:
        try:
            return json.loads(text) if text is not None else fallback
        except (TypeError, ValueError):
            return fallback

    return {
        "id": int(row["id"]),
        "node_id": int(row["node_id"]),
        "contract_id": int(row["contract_id"]),
        "decision": row["decision"],
        # 条件与证据的**当时正文**：契约改版、条件整组替换、证据追加之后，
        # 历史验收仍要能还原「当时是凭什么判的」（方案 §4.3.1 / §6.3）。
        "criteria_snapshot": _loads(row["criteria_snapshot"], []),
        "submission_snapshot": _loads(row["submission_snapshot"], []),
        "criteria_state": _loads(row["criteria_state"], {}),
        "submission_ids": _loads(row["submission_ids"], []),
        "note": row["note"],
        "is_current": bool(row["is_current"]),
        "invalidated_at": row["invalidated_at"],
        "created_at": row["created_at"],
        "stage_status": str(conn.execute(
            "SELECT status FROM plan_node WHERE id = ?", (int(node["id"]),)
        ).fetchone()["status"]),
    }


def review_public(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """验收行 → 响应形状（历史只读展示用）。"""
    if row is None:
        return None

    def _loads(text: Any, fallback: Any) -> Any:
        try:
            return json.loads(text) if text is not None else fallback
        except (TypeError, ValueError):
            return fallback

    return {
        "id": int(row["id"]),
        "decision": row["decision"],
        "contract_id": int(row["contract_id"]),
        # 验收记录必须自带足以还原历史的快照与时间（方案 §6.3）：条件正文与
        # 关联证据的快照原样给前端，历史不靠台账自行拼装。
        "criteria_snapshot": _loads(row["criteria_snapshot"], []),
        "submission_snapshot": _loads(row["submission_snapshot"], []),
        "criteria_state": _loads(row["criteria_state"], {}),
        "submission_ids": _loads(row["submission_ids"], []),
        "note": row["note"],
        "is_current": bool(row["is_current"]),
        "invalidated_at": row["invalidated_at"],
        "created_at": row["created_at"],
    }


def stage_acceptance(conn: sqlite3.Connection, stage: sqlite3.Row) -> dict[str, Any]:
    """阶段的验收全貌：状态 + 条件 + 证据 + 验收历史（GET /api/plan 的展示原料）。

    状态取值：skipped / pending / accepted / needs_work / not_met / invalidated
    （有过验收但当前已失效，页面据此显示「需要按新标准重看」）。
    """
    stage_id = int(stage["id"])
    reviews = stage_reviews(conn, stage_id)
    current = next((row for row in reviews if row["is_current"]), None)
    if stage["status"] == "skipped":
        status = "skipped"
    elif current is not None:
        status = str(current["decision"])
    elif reviews:
        status = "invalidated"
    else:
        status = "pending"
    return {
        "status": status,
        "contract_id": stage["contract_id"],
        "criteria": _stage_criteria_of(stage),
        "evidence_requirements": _stage_requirements_of(stage),
        "latest_review": review_public(current),
        "reviews": [review_public(row) for row in reviews],
        "evidence": [
            {
                "id": int(row["id"]),
                "kind": row["kind"],
                "reference": row["reference"],
                "note": row["note"],
                "submitted_by": row["submitted_by"],
                "created_at": row["created_at"],
            }
            for row in stage_evidence(conn, stage_id)
        ],
    }


# ---------- 报告：执行世界回到系统的唯一信号 ----------

def submit_report(
    conn: sqlite3.Connection,
    node_id: int,
    status: str,
    note: str,
    artifact_url: str | None = None,
    material_feedback: str | None = None,
    at: str | None = None,
) -> dict[str, Any]:
    """收一条报告：落报告行 → 台账留痕 → 按状态机推进节点 → 必要时产出推进提案。

    为什么先把能判的都判掉：非法迁移或缺一句话说明，如果写到一半才报错，
    库里会留下「有报告、状态却没动」的假记录，比直接报错更难查。
    `at` 是给测试用的时间桩，正常调用不用传。
    """
    if status not in REPORT_STATUSES:
        raise PlanError(f"未知报告状态：{status}；可用状态：{' / '.join(REPORT_STATUSES)}")
    if not str(note or "").strip():
        raise PlanError("报告必须写一句话说明")

    node = get_node(conn, node_id)
    if node is None:
        raise PlanError(f"节点 id={node_id} 不存在")

    before = node["status"]
    target = REPORT_TO_NODE[status]
    assert_transition(before, target)

    timestamp = at or now_iso()
    cursor = conn.execute(
        """INSERT INTO report (node_id, status, note, artifact_url, material_feedback, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (node_id, status, note, artifact_url, material_feedback, timestamp),
    )
    report_id = int(cursor.lastrowid)
    ledger.log_event(conn, "report", report_id, "create", None, status, note, actor="user")

    # 状态没变时 ledger.set_status 会直接返回、不提交，所以这里统一提交一次，
    # 保证「报告行 + 台账流水」一定落盘。
    ledger.set_status(conn, "plan_node", node_id, target, actor="user", reason=f"报告：{note}")
    conn.commit()

    # 这里曾经会「顺势产出推进提案」；T29 把这整类删了——阶段完成与否只由计划表显示，
    # 不再是一次裁定（决策 28/30 的修订）。留着这个键是为了不动前端与冒烟脚本的形状。
    proposal_id = None

    return {
        "report_id": report_id,
        "node_id": node_id,
        "report_status": status,
        "node_status_before": before,
        "node_status": target,
        "proposal_id": proposal_id,
    }


# ---------- 任务与交付物（2026-09-17 起的三级结构，SPEC 决策 30–32） ----------

def _require_level(conn: sqlite3.Connection, node_id: int, level: str, action: str) -> sqlite3.Row:
    """动作只认自己的层级：打勾/跳过只对任务，交付物只对阶段。"""
    node = get_node(conn, node_id)
    if node is None:
        raise PlanError(f"节点 id={node_id} 不存在")
    if node["level"] != level:
        raise PlanError(f"id={node_id} 是 {node['level']}，「{action}」只对 {level} 用")
    return node


def check_task(conn: sqlite3.Connection, node_id: int, actor: str = "user") -> dict[str, Any]:
    """任务打勾：一步到完成，不写理由（决策 31）。迁移仍走状态机与台账。"""
    node = _require_level(conn, node_id, "task", "打勾")
    before = node["status"]
    assert_transition(before, "done")
    ledger.set_status(conn, "plan_node", node_id, "done", actor=actor, reason="打勾完成")
    return {
        "node_id": node_id,
        "node_status_before": before,
        "node_status": "done",
        "proposal_id": None,  # T29：不再顺产推进提案
    }


# 「跳过」认哪几层（T35，SPEC 决策 41）：阶段与任务。周打卡不认——它是节奏节点，
# 报告本来就带 `skipped` 这个状态，不需要第二个「不做了」的说法。
SKIPPABLE_LEVELS: tuple[str, ...] = ("stage", "task")


def skip_node(
    conn: sqlite3.Connection, node_id: int, reason: str, actor: str = "user"
) -> dict[str, Any]:
    """跳过：**跳过算完成的一种**，但必须写一句理由——它是裁定，要留痕。

    改自 T23 的 `skip_task`（T35 放开到阶段，SPEC 决策 41）。阶段与任务共用这一条路：
    「不要了」在计划里只有这一个出口（没有真删除——报告与交付物指着节点编号，删了引用全断）。

    阶段跳过时**其下的任务原样留着**：它们是「当时打算做什么」的痕迹，不是待办；
    落后量不再算这个阶段（`node_lag_days` 对 skipped 一律返回 None），
    阶段完成判定直接满足（`stage_finished` 看 `SETTLED_STATUSES`）。
    """
    if not str(reason or "").strip():
        raise PlanError("跳过必须写一句理由——它进台账，回答「当时为什么不做」")
    node = get_node(conn, node_id)
    if node is None:
        raise PlanError(f"节点 id={node_id} 不存在")
    if node["level"] not in SKIPPABLE_LEVELS:
        raise PlanError(
            f"id={node_id} 是 {node['level']}，「跳过」只对阶段与任务用"
            f"（周打卡用报告里的「跳过」那个状态）"
        )
    before = node["status"]
    assert_transition(before, "skipped")
    with atomic(conn):
        ledger.set_status(
            conn, "plan_node", node_id, "skipped", actor=actor, reason=str(reason).strip()
        )
        invalidated = None
        if node["level"] == "stage":
            # 跳过是裁定：当前验收随之失效（历史行保留），阶段状态走 skipped
            invalidated = invalidate_current_review(
                conn, node_id, f"阶段跳过：{str(reason).strip()}", actor
            )
    return {
        "node_id": node_id,
        "level": node["level"],
        "node_status_before": before,
        "node_status": "skipped",
        "invalidated_review_id": invalidated,  # 成果闭环 P1：跳过使当前验收失效
        "proposal_id": None,  # T29：不再顺产推进提案
    }


def skip_task(
    conn: sqlite3.Connection, node_id: int, reason: str, actor: str = "user"
) -> dict[str, Any]:
    """任务跳过：只认任务层（阶段跳过走 `skip_node`，`/skip` 路由两条都收）。"""
    _require_level(conn, node_id, "task", "跳过")
    return skip_node(conn, node_id, reason, actor)


def reopen_node(
    conn: sqlite3.Connection, node_id: int, reason: str, actor: str = "user"
) -> dict[str, Any]:
    """放回：把**已完成 / 已跳过的阶段或任务**退回「进行中」（2026-09-28 补的出口）。

    为什么要有它：打勾是一键动作（决策 31），手滑一次原来就没有回头路——状态机其实
    一直允许 `done → in_progress` 与 `skipped → in_progress`，缺的只是这条写入口。
    与跳过同一层口径（阶段与任务），同一道理由闸：理由进台账，回答「为什么又把它放回来」。
    周打卡不给放回——它的状态由报告推进，改主意就再交一份报告。
    """
    if not str(reason or "").strip():
        raise PlanError("放回必须写一句理由——它进台账，回答「为什么又把它放回来」")
    node = get_node(conn, node_id)
    if node is None:
        raise PlanError(f"节点 id={node_id} 不存在")
    if node["level"] not in SKIPPABLE_LEVELS:
        raise PlanError(
            f"id={node_id} 是 {node['level']}，「放回」只对阶段与任务用"
            f"（周打卡的状态由报告推进）"
        )
    before = node["status"]
    if before not in SETTLED_STATUSES:
        raise PlanError(f"id={node_id} 现在还是「{before}」，没完成也没什么可放回的")
    assert_transition(before, "in_progress")
    with atomic(conn):
        ledger.set_status(
            conn, "plan_node", node_id, "in_progress", actor=actor, reason=str(reason).strip()
        )
        # 成果闭环 P1：放回使**受影响阶段**的当前验收失效——阶段自己放回、或它下面的
        # 任务放回（任务收尾是阶段完成的条件之一）。历史验收行保留，只翻当前性。
        invalidated = None
        if node["level"] == "stage":
            invalidated = invalidate_current_review(
                conn, node_id, f"阶段放回：{str(reason).strip()}", actor
            )
        elif node["parent_id"] is not None:
            invalidated = invalidate_current_review(
                conn, int(node["parent_id"]),
                f"任务「{node['title']}」放回：{str(reason).strip()}", actor,
            )
    return {
        "node_id": node_id,
        "level": node["level"],
        "node_status_before": before,
        "node_status": "in_progress",
        "invalidated_review_id": invalidated,  # 成果闭环 P1：放回使受影响当前验收失效
        "proposal_id": None,  # T29：不再顺产推进提案
    }


def submit_deliverable(
    conn: sqlite3.Connection, node_id: int, url: str, note: str, actor: str = "user"
) -> dict[str, Any]:
    """提交交付物：阶段上的独立动作（决策 32）。

    可重新提交——每次落一行（旧值天然留痕），「当前交付物」= 最新那一行。
    成果闭环 P1 起同一次提交**同时**落一条 `evidence_submission(kind=legacy)`：
    旧接口保留兼容、旧链接在证据区可读，但 `legacy` 不满足任何必需证据要求——
    旧链接不自动变成新流程的验收（方案 §9.2.4）。
    """
    if not str(url or "").strip():
        raise PlanError("交付物要填链接——仓库、能访问的 URL、录屏都行")
    if not str(note or "").strip():
        raise PlanError("交付物要写一句话说明")
    _require_level(conn, node_id, "stage", "提交交付物")

    cleaned_url, cleaned_note = str(url).strip(), str(note).strip()
    timestamp = now_iso()
    with atomic(conn):
        cursor = conn.execute(
            "INSERT INTO deliverable_submission (node_id, url, note, created_at) VALUES (?, ?, ?, ?)",
            (node_id, cleaned_url, cleaned_note, timestamp),
        )
        submission_id = int(cursor.lastrowid)
        ledger.log_event(
            conn, "plan_node", node_id, "deliverable_submit", None, cleaned_url, cleaned_note, actor
        )
        evidence_id = int(conn.execute(
            """INSERT INTO evidence_submission (node_id, kind, reference, note, submitted_by, created_at)
               VALUES (?, 'legacy', ?, ?, ?, ?)""",
            (node_id, cleaned_url, cleaned_note, actor, timestamp),
        ).lastrowid)
        ledger.log_event(
            conn, "plan_node", node_id, "evidence_submit", None,
            f"legacy {cleaned_url}", cleaned_note, actor,
        )
    return {
        "submission_id": submission_id,
        "evidence_id": evidence_id,  # 成果闭环 P1：同一份提交在证据区也可见（kind=legacy）
        "node_id": node_id,
        "url": cleaned_url,
        "note": cleaned_note,
        "created_at": timestamp,
        "proposal_id": None,  # T29：不再顺产推进提案
    }


# ---------- 落后量 ----------


# 三类可改字段（决策 38）。交付物只属于阶段：它是「这个阶段交出了什么」，
# 任务与周打卡上没有这个概念（T23 的判定只看阶段层的 deliverable）。
EDITABLE_FIELDS: tuple[str, ...] = ("title", "deliverable", "due_date")


# 「没传这个字段」的哨兵：contract_id 的 None（解绑）是合法值，不能用 None 区分传没传。
_FIELD_UNSET: Any = object()


def update_node_fields(
    conn: sqlite3.Connection,
    node_id: int,
    *,
    reason: str,
    title: str | None = None,
    deliverable: str | None = None,
    due_date: str | None = None,
    purpose: Any = None,
    why_now: Any = None,
    contract_criterion_ids: Any = None,
    contract_id: Any = _FIELD_UNSET,
    acceptance_criteria: Any = None,
    evidence_requirements: Any = None,
    actor: str = "user",
) -> dict[str, Any]:
    """原地改一个**已经建好**的节点的字段（决策 38，2026-09-18 用户拍板）。

    为什么是「原地改 + 一条流水」而不是台账「取代」：取代会让 id 变，报告与交付物
    提交全指不到原来那条；而台账对 `plan_node` 本就明禁生命周期操作（决策 22——
    节点的「不算数」由 `skipped` 表达，改字段不属于生命周期事件）。所以这里用
    `ledger.log_event` 记一条 `change_type='update_fields'` 的流水（谁 / 何时 /
    改前改后 / 理由），配一次原地 UPDATE——**id 与所有引用一个不动**。

    规矩：只传要改的字段；**传空字符串表示清空**（`deliverable` / `due_date` 可以清，
    `title` 不许清）；**理由必填**——台账回答不了「为什么改」的话，这条流水就是噪音；
    一个字段都没真变就报错（不写噪音流水）。改标题同样过防重复闸。

    成果闭环 P1 新增：阶段可以整组替换 `acceptance_criteria` / `evidence_requirements`
    （传 None = 不动；传数组 = 整组换，id 由服务端重排）。**阶段验收条件或交付要求
    变更会使该阶段当前验收失效**（历史行保留，方案 §5.3）。

    成果闭环 OC-07 新增（蓝图批准复用已有阶段时写成果字段）：阶段还可以改
    `purpose` / `why_now` / `contract_criterion_ids`（None = 不动，空 = 清空）与
    `contract_id`（哨兵 `_FIELD_UNSET` 区分「没传」与「传 None 解绑」）。
    """
    cleaned_reason = str(reason or "").strip()
    if not cleaned_reason:
        raise PlanError("改节点字段必须写明理由——它进台账，回答「为什么改」")

    node = get_node(conn, node_id)
    if node is None:
        raise PlanError(f"节点 id={node_id} 不存在")

    wanted: dict[str, Any] = {}
    if title is not None:
        cleaned = str(title).strip()
        if not cleaned:
            raise PlanError("标题不能改成空的——想「不要它了」就跳过它，别留个没名字的节点")
        wanted["title"] = cleaned
    if deliverable is not None:
        if node["level"] != "stage":
            raise PlanError(
                f"交付物只属于阶段，id={node_id} 是 {node['level']}；"
                "任务与周打卡上的产出用报告说明"
            )
        wanted["deliverable"] = str(deliverable).strip() or None
    if due_date is not None:
        cleaned = str(due_date).strip()
        if not cleaned:
            wanted["due_date"] = None  # 清空：不带日期就不进落后量（决策 31）
        else:
            parsed = parse_date(cleaned)
            if parsed is None:
                raise PlanError(f"截止日期「{cleaned}」看不懂——写成 YYYY-MM-DD，或者传空字符串清掉它")
            wanted["due_date"] = parsed.isoformat()
    if node["level"] == "stage":
        if acceptance_criteria is not None:
            wanted["acceptance_criteria"] = normalize_stage_criteria(
                acceptance_criteria, prefix="c", label="验收条件"
            )
        if evidence_requirements is not None:
            wanted["evidence_requirements"] = normalize_stage_criteria(
                evidence_requirements, prefix="e", label="证据要求"
            )
        # 成果闭环 OC-07：蓝图批准复用已有阶段时，成果字段也从这里写（改前改后进台账）。
        # purpose / why_now 传 "" 表示清空；contract_criterion_ids 传 [] 表示清空；
        # contract_id 用哨兵区分「没传」与「传 None（解绑）」。
        if purpose is not None:
            wanted["purpose"] = str(purpose).strip() or None
        if why_now is not None:
            wanted["why_now"] = str(why_now).strip() or None
        if contract_criterion_ids is not None:
            wanted["contract_criterion_ids"] = _normalize_criterion_ids(
                conn, int(node["plan_id"]), contract_criterion_ids
            )
        if contract_id is not _FIELD_UNSET:
            wanted["contract_id"] = None if contract_id is None else int(contract_id)
    elif (
        acceptance_criteria is not None or evidence_requirements is not None
        or purpose is not None or why_now is not None
        or contract_criterion_ids is not None or contract_id is not _FIELD_UNSET
    ):
        raise PlanError("验收条件、证据要求与成果字段只属于阶段")
    if not wanted:
        raise PlanError("没有要改的字段：至少要给出要改的字段里的一个")

    # 只留真的变了的：一个字都没变就报错，免得台账被「改了等于没改」的流水灌满
    changed = {
        field: value for field, value in wanted.items() if node[field] != value
    }
    if not changed:
        raise PlanError("这几个字段和现在一模一样，没什么可改的")

    if "title" in changed:
        # 改标题也要过同一道防重复闸：不然「改」就成了绕过它的后门
        assert_no_open_duplicate(
            conn, int(node["plan_id"]), str(node["level"]), changed["title"], node["parent_id"]
        )

    before = {field: node[field] for field in changed}

    def _write() -> None:
        assignments = ", ".join(f"{field} = ?" for field in changed)
        conn.execute(
            f"UPDATE plan_node SET {assignments} WHERE id = ?",
            (*changed.values(), node_id),
        )
        ledger.log_event(
            conn,
            "plan_node",
            node_id,
            "update_fields",
            json.dumps(before, ensure_ascii=False),
            json.dumps(changed, ensure_ascii=False),
            cleaned_reason,
            actor,
        )
        # 成果闭环 P1：阶段条件或交付要求变了，按旧条件判的当前验收就不能再算数
        if node["level"] == "stage" and changed.keys() & {
            "deliverable", "acceptance_criteria", "evidence_requirements"
        }:
            invalidate_current_review(
                conn, node_id, f"阶段条件/交付要求变更（{ '、'.join(sorted(changed)) }）", actor
            )

    # 调用方已经持有事务时（如蓝图批准的原子写入里复用同名阶段），不再自开一个
    # ——db.atomic 在已开事务上会直接 RuntimeError，外层才是这一笔的原子边界。
    if conn.in_transaction:
        _write()
    else:
        with atomic(conn):
            _write()
    return {
        "node_id": node_id,
        "changed": sorted(changed),
        "before": before,
        "after": changed,
    }

def parse_date(value: Any) -> date | None:
    """把 due_date / created_at 这类文本解析成日期；解析不了就当没有这个信息。"""
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _completion_date(conn: sqlite3.Connection, node_id: int) -> date | None:
    """节点「实际完成日」= 最近一条 done 报告的日期。"""
    row = conn.execute(
        """SELECT created_at FROM report
           WHERE node_id = ? AND status = 'done'
           ORDER BY created_at DESC, id DESC LIMIT 1""",
        (node_id,),
    ).fetchone()
    return parse_date(row["created_at"]) if row is not None else None


def node_lag_days(conn: sqlite3.Connection, node: sqlite3.Row, today: date) -> int | None:
    """一个节点落后几天。None 表示无从判断（没定计划完成日，或已跳过）。

    三种情况分开算，别混成一笔账：
    - 还没做完：落后 = 今天 - 计划完成日（没到期就是 0）
    - 已完成：落后 = 实际完成日 - 计划完成日（提前做完给负数，那是好事）
    - 已跳过：不算落后，那是你裁定过的结果
    """
    due = parse_date(node["due_date"])
    if due is None:
        return None
    if node["status"] == "skipped":
        return None
    if node["status"] == "done":
        completion = _completion_date(conn, int(node["id"]))
        return None if completion is None else (completion - due).days
    return max(0, (today - due).days)


def plan_lag(conn: sqlite3.Connection, plan_id: int, today: date) -> dict[str, Any]:
    """计划级落后量 = 还没做完的节点里最严重的那个。

    只看未完成节点：已经做完的节点即使当时晚于计划，那也只是「这次晚了几天」
    （节点级 lag_days 看得到），不该让整个计划一直挂着「落后」的牌子——
    「落后」要回答的是「现在有什么堵着」，不是「历史上晚过几次」。

    取最大而不是取平均：落后看的是最长的那块短板。平均一下会把
    「三天没动」和「拖了十天」混成「大概一周」，没有决策价值。
    """
    worst: sqlite3.Row | None = None
    worst_lag = 0
    for node in conn.execute(
        f"SELECT * FROM plan_node WHERE plan_id = ? AND {not_invalid()} ORDER BY sort_order, id",
        (plan_id,),
    ):
        if node["status"] in SETTLED_STATUSES:
            continue
        lag = node_lag_days(conn, node, today)
        if lag is None or lag <= 0:
            continue
        if lag > worst_lag:
            worst, worst_lag = node, lag
    return {
        "lag_days": worst_lag,
        "behind": worst_lag > 0,
        "worst": None if worst is None else {
            "id": worst["id"],
            "title": worst["title"],
            "due_date": worst["due_date"],
            "lag_days": worst_lag,
        },
    }


# ---------- 计划全貌（GET /api/plan 的数据形状） ----------

def stage_is_settled(conn: sqlite3.Connection, stage: sqlite3.Row) -> bool:
    """阶段是否收尾——判定规则见 `stage_finished`（任务全收尾 + 交付物已提交）。"""
    return stage_finished(conn, stage)


def current_stage(conn: sqlite3.Connection, plan_id: int) -> sqlite3.Row | None:
    """当前阶段 = 第一个没收尾的阶段；全部收尾了返回 None。"""
    for stage in get_stages(conn, plan_id):
        if not stage_is_settled(conn, stage):
            return stage
    return None


def resolve_plan(conn: sqlite3.Connection, plan_id: int | None = None) -> sqlite3.Row | None:
    """指定了就取那个计划；没指定就取最新建的一个 active 计划。"""
    if plan_id is not None:
        return conn.execute("SELECT * FROM plan WHERE id = ?", (plan_id,)).fetchone()
    actives = ledger.fetch_active(conn, "plan")
    return actives[-1] if actives else None


# ---------- 计划的生命周期与列表（T24：多计划 + 严格分开，SPEC 决策 33；T27 拆四态） ----------
#
# `plan.status` 四个取值（无数据库约束，加取值不用迁移）：
#   active 进行中        —— 默认列表里只有它
#   paused 暂时不做      —— 可逆的搁置，随时「继续做」回来
#   closed 做完了        —— 业务终态，可以「重开」
#   void   这件事根本不该做 —— 台账的单向门，不能回头
# 「能回到 active 吗」是这条分界线的判据：paused / closed 能，void 不能。
#
# 作废仍只从 active 走：`ledger.void` 的前置条件是记录处于 `active_status`
# （所有实体共用的台账规则，本任务不动它）。要作废一个暂停的计划得两步——
# 先「继续做」再作废；界面也不给 paused / closed 项提供作废按钮。

def _ending_event(conn: sqlite3.Connection, plan_id: int, status: str) -> sqlite3.Row | None:
    """计划**离开进行中**那一刻的台账流水；还在进行中就返回 None。

    取最后一条而不是第一条：暂停 → 继续 → 再收尾这种序列里，只有最后那条
    回答得了「它现在这个状态是什么时候、因为什么来的」。
    判据用 change_type 而不是 before/after 的值：作废写的是 `void`，
    其余状态迁移写 `status_change`，两种都算一次「结束」。
    """
    if status == "active":
        return None
    for event in reversed(ledger.history(conn, "plan", plan_id)):
        if event["change_type"] in ("status_change", "void"):
            return event
    return None


def list_plans(conn: sqlite3.Connection, include_inactive: bool = False) -> list[dict[str, Any]]:
    """列出计划。默认只给**进行中**的；`include_inactive=True` 连暂停 / 收尾 / 作废一起给。

    不在进行中的计划不进默认列表，但行还在——「垃圾清得掉、做过的事查得到」。
    每项带当前阶段与阶段进度，界面拿它做切换器，不用再逐个取计划树；
    再带 `ended_at` / `ended_reason`（离开进行中那一刻的时间与理由，进行中时为 None），
    界面拿它在历史段里说清「这个计划当初是怎么停下的」。
    """
    result: list[dict[str, Any]] = []
    for row in conn.execute("SELECT * FROM plan ORDER BY id"):
        if not include_inactive and row["status"] != "active":
            continue
        plan_id = int(row["id"])
        stages = get_stages(conn, plan_id)
        stage = current_stage(conn, plan_id)
        ended = _ending_event(conn, plan_id, str(row["status"]))
        result.append({
            "id": plan_id,
            "goal": row["goal"],
            "status": row["status"],
            "valid_from": row["valid_from"],
            "ended_at": None if ended is None else ended["created_at"],
            "ended_reason": None if ended is None else ended["reason"],
            "current_stage": None if stage is None else {
                "id": stage["id"],
                "title": stage["title"],
            },
            "stages": len(stages),
            "stages_finished": len([item for item in stages if stage_finished(conn, item)]),
        })
    return result


def require_plan(conn: sqlite3.Connection, plan_id: int) -> sqlite3.Row:
    """取一个计划，没有就报错（接口层翻成 404）。"""
    row = resolve_plan(conn, plan_id)
    if row is None:
        raise PlanError(f"计划 id={plan_id} 不存在")
    return row


def _contract_bound_stages(conn: sqlite3.Connection, plan_id: int) -> list[sqlite3.Row]:
    """成果流程里真正参与完成门槛的阶段：绑定了契约版本的阶段。

    升级时被用户标成「保留为历史」的旧阶段不绑契约，也就不该拦住计划完成——
    它们是只读历史，不是待验收的成果步骤（方案 §9.2.5）。
    """
    return [stage for stage in get_stages(conn, plan_id) if stage["contract_id"] is not None]


def contract_coverage(conn: sqlite3.Connection, plan_id: int) -> dict[str, Any]:
    """返回当前成果契约的阶段承接与实际达标覆盖。

    契约条件不能只存在于契约正文里：至少要有阶段承接每个必需条件，且该条件要
    由一个真正 accepted 的阶段兑现。跳过阶段只表示不做这一步，不能替成果契约
    把必需条件算成已满足。只统计绑定契约的阶段——「保留为历史」的旧阶段不算承接，
    否则一条老阶段就能替新契约把必需条件「占位」掉。
    """
    active = contract.active(conn, plan_id)
    if active is None:
        return {
            "required": [],
            "covered": [],
            "satisfied": [],
            "missing": [],
            "unsatisfied": [],
            "complete": False,
        }
    criteria = json.loads(active["acceptance_criteria"] or "[]")
    required = [str(item["id"]) for item in criteria if item.get("required")]
    covered: set[str] = set()
    satisfied: set[str] = set()
    for stage in _contract_bound_stages(conn, plan_id):
        try:
            ids = json.loads(stage["contract_criterion_ids"] or "[]")
        except (TypeError, ValueError):
            ids = []
        stage_ids = {str(item) for item in ids} if isinstance(ids, list) else set()
        covered.update(stage_ids.intersection(required))
        if stage["status"] == "skipped":
            continue
        if stage_finished(conn, stage):
            satisfied.update(stage_ids.intersection(required))
    missing = [item for item in required if item not in covered]
    unsatisfied = [item for item in required if item not in satisfied]
    return {
        "required": required,
        "covered": [item for item in required if item in covered],
        "satisfied": [item for item in required if item in satisfied],
        "missing": missing,
        "unsatisfied": unsatisfied,
        "complete": not missing and not unsatisfied,
    }


def plan_can_complete(conn: sqlite3.Connection, plan_id: int) -> bool:
    """成果计划能否按「完成」收尾（方案 §5.3）：

    有当前有效的成果契约，每个**绑定契约的阶段**都已验收达标或明确跳过，且契约的
    每个必需条件都被阶段承接并由 accepted 阶段兑现。跳过阶段不替成果契约满足必需
    条件；升级时保留为历史的旧阶段不进这道门槛（否则一条只读老阶段会把计划永远卡住）。
    """
    if contract.active(conn, plan_id) is None:
        return False
    stages = _contract_bound_stages(conn, plan_id)
    return all(stage_finished(conn, stage) for stage in stages) and contract_coverage(
        conn, plan_id
    )["complete"]


def upgrade_plan(
    conn: sqlite3.Connection,
    plan_id: int,
    data: dict[str, Any],
    stages: list[dict[str, Any]],
    *,
    reason: str = "",
    actor: str = "user",
) -> dict[str, Any]:
    """把一份 legacy 旧计划**明确升级**为成果流程（方案 §9.2.5）。

    这是旧计划唯一的新流程入口，也是 §9.2「不猜旧账」的落点：一次事务里接收
    **完整成果契约**与**每个现有阶段的处置**，全部校验通过后才一起落盘——
    创建第一条 `outcome_contract(source_kind='migrated')`、把「纳入」的阶段绑定
    到当前契约并写入条件/证据要求、把「跳过」的阶段按业务终态记下、把「保留为历史」
    的阶段原样留作只读；随后才把计划切到 `completion_mode=outcome`。
    任何一步失败，事务回滚，契约不落、阶段不动、模式不切——没有半成品。

    规矩：
    - 只对**进行中**的 legacy 计划开放；已成果化的走「激活新版本契约」。
    - 每个现有阶段都要显式给一种处置（`include` / `skipped` / `history`），
      漏一个都拒绝——「没提到的阶段怎么办」不能由系统替用户猜。
    - `include` 的阶段至少一条验收条件；`contract_criterion_ids` 必须引用本份契约里
      真实存在的条件 id（不接受跨契约或自造的 id）。
    - `skipped` 必须写明理由（跳过是裁定，要进台账）；已完成的老阶段也能在这一步被
      明确跳过——升级是显式重定基，不是普通状态迁移。
    - 旧报告、旧交付物、旧 closed 结论一律不回写；升级后只有新提交的证据与新验收
      才算 v2 的当前依据（旧交付物仍是只读的 `kind=legacy` 证据）。
    """
    row = require_plan(conn, plan_id)
    if row["status"] != "active":
        raise PlanConflict(
            f"计划 id={plan_id} 不在进行中（{row['status']}）——要升级先「继续做」回来"
        )
    if plan_mode(conn, plan_id) == "outcome":
        raise PlanConflict(
            "这个计划已经是成果流程了：改验收标准请走「激活新版本契约」，不用再升级"
        )

    existing = {int(stage["id"]): stage for stage in get_stages(conn, plan_id)}
    seen: set[int] = set()
    prepared: list[tuple[sqlite3.Row, str, dict[str, Any], str, list[str]]] = []
    for entry in stages:
        if not isinstance(entry, dict):
            raise PlanError("每个阶段处置必须是一个对象")
        raw_id = entry.get("stage_id")
        if raw_id is None:
            raise PlanError("阶段处置缺少 stage_id")
        stage_id = int(raw_id)
        if stage_id in seen:
            raise PlanError(f"阶段 id={stage_id} 的处置给了不止一次")
        seen.add(stage_id)
        stage = existing.get(stage_id)
        if stage is None:
            raise PlanError(f"阶段 id={stage_id} 不属于这个计划（或已经不在）")
        disposition = str(entry.get("disposition") or "").strip()
        if disposition not in UPGRADE_DISPOSITIONS:
            raise PlanError(
                f"阶段「{stage['title']}」的处置「{disposition}」不认识；"
                f"只能是 {' / '.join(UPGRADE_DISPOSITIONS)}"
            )
        entry_reason = str(entry.get("reason") or "").strip()
        if disposition == "include":
            criteria_json = normalize_stage_criteria(
                entry.get("acceptance_criteria"), prefix="c",
                label=f"阶段「{stage['title']}」的验收条件",
            )
            if criteria_json is None:
                raise PlanError(f"纳入成果流程的阶段「{stage['title']}」至少要有一条验收条件")
            requirements_json = normalize_stage_criteria(
                entry.get("evidence_requirements"), prefix="e",
                label=f"阶段「{stage['title']}」的证据要求",
            )
            raw_ids = entry.get("contract_criterion_ids") or []
            if not isinstance(raw_ids, list):
                raise PlanError(f"阶段「{stage['title']}」的 contract_criterion_ids 要是一个数组")
            criterion_ids = [str(item).strip() for item in raw_ids if str(item or "").strip()]
            prepared.append((
                stage, disposition,
                {"acceptance_criteria": criteria_json, "evidence_requirements": requirements_json},
                entry_reason, criterion_ids,
            ))
        elif disposition == "skipped":
            if not entry_reason:
                raise PlanError(f"把阶段「{stage['title']}」标为跳过要写明理由——它进台账")
            status = str(stage["status"])
            # 常规状态机不允许 done → skipped（「完成过」不能被改判成「没做」）。
            # 但升级是显式重定基：方案 §9.2.5 把「跳过」列为每个现有阶段都可选的
            # 处置，用户明确说不把这条老阶段纳入成果流程时，允许带着理由把它标成
            # 跳过；旧报告与旧交付物原样留作只读历史。其余状态仍走同一道状态机闸。
            if status not in ("skipped", "done"):
                assert_transition(status, "skipped")
            prepared.append((stage, disposition, {}, entry_reason, []))
        else:  # history
            prepared.append((stage, disposition, {}, entry_reason, []))

    missing = sorted(set(existing) - seen)
    if missing:
        titles = "、".join(f"「{existing[item]['title']}」(id={item})" for item in missing)
        raise PlanError(
            f"还有阶段没说怎么处理：{titles}——每个现有阶段都要明确"
            "「纳入成果流程 / 跳过 / 保留为历史」"
        )

    # 契约先验全（条数、字段、证据类型），也拿它核对条件 id 的引用。
    payload = contract.validate(data)
    known_ids = {str(item["id"]) for item in payload["acceptance_criteria"]}
    for stage, disposition, values, entry_reason, criterion_ids in prepared:
        if disposition != "include":
            continue
        unknown = sorted(set(criterion_ids) - known_ids)
        if unknown:
            raise PlanError(
                f"阶段「{stage['title']}」引用了本份契约里不存在的条件 id：{'、'.join(unknown)}"
            )

    # 从这里开始落盘：契约、阶段绑定、旧阶段处置、模式切换在同一事务里完成。
    with atomic(conn):
        activated = contract.activate(
            conn, plan_id, data, source_kind="migrated",
            reason=str(reason or "").strip() or "把旧计划升级为成果闭环", actor=actor,
        )
        contract_id = int(activated["id"])
        included: list[int] = []
        skipped: list[int] = []
        kept: list[int] = []
        for stage, disposition, values, entry_reason, criterion_ids in prepared:
            stage_id = int(stage["id"])
            if disposition == "include":
                before = {
                    "contract_id": stage["contract_id"],
                    "acceptance_criteria": stage["acceptance_criteria"],
                    "evidence_requirements": stage["evidence_requirements"],
                    "contract_criterion_ids": stage["contract_criterion_ids"],
                }
                criterion_json = json.dumps(criterion_ids, ensure_ascii=False) if criterion_ids else None
                after = {
                    "contract_id": contract_id,
                    "acceptance_criteria": values["acceptance_criteria"],
                    "evidence_requirements": values["evidence_requirements"],
                    "contract_criterion_ids": criterion_json,
                }
                conn.execute(
                    "UPDATE plan_node SET contract_id = ?, acceptance_criteria = ?,"
                    " evidence_requirements = ?, contract_criterion_ids = ? WHERE id = ?",
                    (
                        contract_id, values["acceptance_criteria"],
                        values["evidence_requirements"], criterion_json, stage_id,
                    ),
                )
                ledger.log_event(
                    conn, "plan_node", stage_id, "upgrade_include",
                    json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False),
                    entry_reason or "升级：纳入成果流程并绑定当前契约", actor,
                )
                included.append(stage_id)
            elif disposition == "skipped":
                ledger.set_status(
                    conn, "plan_node", stage_id, "skipped", actor=actor,
                    reason=entry_reason,
                )
                skipped.append(stage_id)
            else:
                ledger.log_event(
                    conn, "plan_node", stage_id, "upgrade_history",
                    str(stage["status"]), str(stage["status"]),
                    entry_reason or "升级：保留为历史，不纳入成果流程", actor,
                )
                kept.append(stage_id)
        ledger.log_event(
            conn, "plan", plan_id, "upgrade_outcome",
            json.dumps({"flow_version": row["flow_version"], "completion_mode": "legacy"}, ensure_ascii=False),
            json.dumps(
                {"flow_version": 2, "completion_mode": "outcome", "contract_id": contract_id,
                 "included": included, "skipped": skipped, "history": kept},
                ensure_ascii=False,
            ),
            str(reason or "").strip() or "把旧计划升级为成果闭环", actor,
        )
        contract_row = contract.get(conn, contract_id)

    return {
        "plan_id": plan_id,
        "flow_version": 2,
        "completion_mode": "outcome",
        "contract": None if contract_row is None else contract.public(contract_row),
        "included": included,
        "skipped": skipped,
        "history": kept,
        "reviews_invalidated": activated["reviews_invalidated"],
    }


_CLOSE_KIND_LABELS = {"completed": "完成", "stopped": "提前停止", None: "旧流程收尾"}


def close_plan(
    conn: sqlite3.Connection, plan_id: int, reason: str | None = None,
    *, close_kind: str | None = None, actor: str = "user",
) -> dict[str, Any]:
    """收尾一个计划：进历史，不再出现在默认计划列表里。收尾语义按双模式分流（方案 §3.9 / §6.5）。

    - **outcome（成果计划）**：必须明确 `close_kind`——`completed` 要过完成门槛
      （`plan_can_complete`），过不了回 409 不动数据；`stopped` 允许未完成但必须写
      停止理由。不默认替用户猜。
    - **legacy（旧流程计划）**：不传 `close_kind` 走旧接口语义（完成收尾，按 legacy
      判定，`closure_kind` 留空——不把旧 closed 猜成新验收）；显式传 `completed` 会被拒
      （先升级补全契约）；`stopped` 可用，同样要写理由。

    `closure_kind` 是 closed 状态的解释，不是第五个计划状态——四态不变。
    已收尾的计划重复 close 只有在同一种收尾语义下幂等，不能换一种覆盖。
    """
    row = require_plan(conn, plan_id)
    if row["status"] in INVALID_STATUSES:
        raise PlanError(f"计划 id={plan_id} 已经作废了，不能再收尾")

    mode = plan_mode(conn, plan_id)
    if mode == "outcome" and close_kind is None:
        raise PlanError(
            "成果计划收尾必须明确 close_kind：completed（按成果完成收尾）"
            "或 stopped（提前停止）——系统不替你猜"
        )
    if close_kind is not None:
        if close_kind not in CLOSE_KINDS:
            raise PlanError(f"未知收尾类型：{close_kind}；可用：{' / '.join(CLOSE_KINDS)}")
        if close_kind == "completed" and mode != "outcome":
            raise PlanError(
                "这个计划还是旧流程：要先升级补全成果契约，才能按「完成」收尾——"
                "现在只能按「停止」（close_kind=stopped）收尾"
            )
        if close_kind == "stopped" and not str(reason or "").strip():
            raise PlanError("停止收尾必须写明理由——它进台账，回答「为什么中途停止」")
        if (
            close_kind == "completed" and row["status"] != "closed"
            and not plan_can_complete(conn, plan_id)
        ):
            raise PlanConflict(
                "还有未验收（或未达标）的阶段，不能按「完成」收尾——"
                "先逐条验收，或改用「提前停止」（close_kind=stopped）并写明原因"
            )

    if row["status"] == "closed":
        existing_kind = row["closure_kind"]
        if close_kind != existing_kind:
            raise PlanConflict(
                f"计划 id={plan_id} 已经按「{_CLOSE_KIND_LABELS.get(existing_kind, existing_kind)}」"
                f"收尾过了，不能用另一种收尾（{_CLOSE_KIND_LABELS.get(close_kind, close_kind)}）覆盖"
            )
        return {"plan_id": plan_id, "status": "closed", "changed": False}

    extra: dict[str, Any] = {}
    if close_kind is not None:
        extra = {
            "closure_kind": close_kind,
            "closure_reason": str(reason or "").strip() or None,
        }
    ledger.set_status(
        conn, "plan", plan_id, "closed", actor=actor,
        reason=str(reason or "计划收尾").strip(),
        extra=extra,
    )
    return {"plan_id": plan_id, "status": "closed", "changed": True, "closure_kind": close_kind}


def pause_plan(
    conn: sqlite3.Connection, plan_id: int, reason: str | None = None, actor: str = "user"
) -> dict[str, Any]:
    """暂停一个计划（暂时不做）：从默认列表挪进历史，随时能「继续做」回来。

    与作废的分界就在能不能回头——暂停是可逆的搁置，作废是单向门。
    已经收尾的不给暂停：「做完了」不是「先不做」，要退回来得先重开。
    作废更不给（`ledger.void` 只认 active 的记录），两步走：先继续做、再作废。
    """
    row = require_plan(conn, plan_id)
    if row["status"] in INVALID_STATUSES:
        raise PlanError(f"计划 id={plan_id} 已经作废了，不能再暂停")
    if row["status"] == "closed":
        raise PlanError(f"计划 id={plan_id} 已经收尾了——要接着做就「重开」它，不能直接暂停")
    if row["status"] == "paused":
        return {"plan_id": plan_id, "status": "paused", "changed": False}
    ledger.set_status(
        conn, "plan", plan_id, "paused", actor=actor,
        reason=str(reason or "暂时不做了").strip(),
    )
    return {"plan_id": plan_id, "status": "paused", "changed": True}


def reopen_plan(
    conn: sqlite3.Connection, plan_id: int, reason: str | None = None, actor: str = "user"
) -> dict[str, Any]:
    """把一个暂停或收尾的计划放回进行中（继续做 / 重开）。可重复调用，幂等。

    作废的不给重开——它是单向门。要重新做这件事就新建一个计划，
    这样台账里「当初为什么扔掉它」那句话永远查得到，不会被一次重开抹掉。
    """
    row = require_plan(conn, plan_id)
    if row["status"] in INVALID_STATUSES:
        raise PlanError(
            f"计划 id={plan_id} 已经作废了，作废是单向门——要重新做就新建一个计划"
        )
    if row["status"] == "active":
        return {"plan_id": plan_id, "status": "active", "changed": False}
    ledger.set_status(
        conn, "plan", plan_id, "active", actor=actor,
        reason=str(reason or "继续做").strip(),
    )
    return {"plan_id": plan_id, "status": "active", "changed": True}


def void_plan(conn: sqlite3.Connection, plan_id: int, reason: str, actor: str = "user") -> dict[str, Any]:
    """作废一个计划（不算数了）。理由必填——台账的作废要回答「当时为什么扔」。"""
    if not str(reason or "").strip():
        raise PlanError("作废计划必须写一句理由——它进台账，回答「当时为什么扔」")
    row = require_plan(conn, plan_id)
    if row["status"] in INVALID_STATUSES:
        raise PlanError(f"计划 id={plan_id} 已经作废过了")
    ledger.void(conn, "plan", plan_id, reason=str(reason).strip(), actor=actor)
    return {"plan_id": plan_id, "status": "void"}


def _child_dict(conn: sqlite3.Connection, node: sqlite3.Row, today: date) -> dict[str, Any]:
    """阶段下挂的子节点（任务 / 周打卡）共用的展示形状。"""
    return {
        "id": node["id"],
        "title": node["title"],
        "status": node["status"],
        "due_date": node["due_date"],
        "sort_order": node["sort_order"],
        "lag_days": node_lag_days(conn, node, today),
    }


def plan_tree(
    conn: sqlite3.Connection, plan_id: int | None = None, today: date | None = None
) -> dict[str, Any]:
    """计划 + 节点树 + 当前阶段 + 落后量。前端只负责展示，不做任何业务计算。"""
    today = today or date.today()
    plan_row = resolve_plan(conn, plan_id)
    if plan_row is None:
        return {
            "plan": None,
            "current_stage": None,
            "stages": [],
            "lag": {"lag_days": 0, "behind": False, "worst": None},
        }

    weekly = weekly_status(conn, today=today, plan_id=int(plan_row["id"]))
    active_contract = contract.active(conn, int(plan_row["id"]))
    stages = []
    for stage in get_stages(conn, int(plan_row["id"])):
        children = conn.execute(
            f"""SELECT * FROM plan_node
               WHERE parent_id = ? AND {not_invalid()} ORDER BY sort_order, id""",
            (stage["id"],),
        ).fetchall()
        submission = deliverable_submission(conn, int(stage["id"]))
        stages.append({
            "id": stage["id"],
            "title": stage["title"],
            "deliverable": stage["deliverable"],
            "due_date": stage["due_date"],
            "status": stage["status"],
            "sort_order": stage["sort_order"],
            "lag_days": node_lag_days(conn, stage, today),
            "progress": stage_completion(conn, int(stage["id"])),
            "finished": stage_finished(conn, stage),
            "purpose": stage["purpose"],
            "why_now": stage["why_now"],
            "contract_id": stage["contract_id"],
            # 是否纳入成果完成门槛：升级时「保留为历史」的旧阶段不绑契约，也不拦完成。
            # 由后端算好给出，前端不靠 contract_id 是否存在自行推断。
            "in_outcome": stage["contract_id"] is not None,
            "acceptance": stage_acceptance(conn, stage),
            "deliverable_submission": None if submission is None else {
                "url": submission["url"],
                "note": submission["note"],
                "created_at": submission["created_at"],
            },
            "tasks": [
                _child_dict(conn, node, today) for node in children if node["level"] == "task"
            ],
            "checkpoints": [
                _child_dict(conn, node, today)
                for node in children
                if node["level"] == "checkpoint"
            ],
        })

    current = current_stage(conn, int(plan_row["id"]))
    mode = plan_mode(conn, int(plan_row["id"]))
    review_status = plan_row["contract_review_status"]
    return {
        "plan": {
            "id": plan_row["id"],
            "goal": plan_row["goal"],
            "status": plan_row["status"],
            "valid_from": plan_row["valid_from"],
            # 成果闭环 P1：双模式与收尾解释（closure_kind 是 closed 的解释，不是第五态）
            "flow_version": plan_row["flow_version"],
            "completion_mode": mode,
            "contract_review_status": review_status,
            "closure_kind": plan_row["closure_kind"],
            "closure_reason": plan_row["closure_reason"],
        },
        # 成果闭环 P1：契约与验收的展示原料——前端只展示，不自行计算完成条件
        "completion_mode": mode,
        "upgrade_required": active_contract is None and mode == "legacy",
        "contract": None if active_contract is None else contract.public(active_contract),
        "contract_history": contract.history(conn, int(plan_row["id"])),
        "contract_status": (
            "active" if active_contract is not None
            else ("needs_review" if review_status == "needs_review" else "missing")
        ),
        # 成果计划专属：现在能不能按「完成」收尾（前端只展示，不算门槛——门槛在 close 里）
        "can_complete": plan_can_complete(conn, int(plan_row["id"])) if mode == "outcome" else None,
        # 成果计划专属：契约覆盖的后端口径（哪个必需条件没被阶段承接 / 没被验收兑现）。
        # 前端据此显示「还差什么」，不自行推断完成条件（缺承接 vs 已承接未达标是两种）。
        "coverage": contract_coverage(conn, int(plan_row["id"])) if mode == "outcome" else None,
        "current_stage": None if current is None else {
            "id": current["id"],
            "title": current["title"],
            "deliverable": current["deliverable"],
            "status": current["status"],
            "progress": stage_completion(conn, int(current["id"])),
        },
        "lag": plan_lag(conn, int(plan_row["id"]), today),
        # T29：落后不再产「重排」提案等人裁定，改成计划页上的一句话提醒——
        # 原因是算出来的，三个方向只是**建议**（不再有裁定入口）。
        "behind_reason": weekly["behind_reason"],
        "advice": weekly["replan_options"],
        "stages": stages,
    }


# ---------- 周检查点判定 ----------

def week_bounds(day: date) -> tuple[date, date]:
    """所在自然周的范围（周一 ~ 周日）。

    为什么以周一为界：ISO 周和后面 Markdown 导出的周文件名都从周一开始，
    三处口径统一，省得以后对不上账。
    """
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def week_key(day: date) -> str:
    """周标识，形如 `2026-W38`——与导出文件名 `周检查点-YYYY-WW.md` 同一套写法。"""
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def reports_between(
    conn: sqlite3.Connection, plan_id: int, start: date, end: date
) -> list[sqlite3.Row]:
    """某个计划在 [start, end] 之间收到的报告。

    为什么在 Python 里筛日期而不是写进 SQL：`created_at` 是带时区的文本，
    SQLite 的日期函数不认这种格式，自己解析更可靠（与落后量用的是同一套 parse_date）。
    """
    rows = conn.execute(
        f"""SELECT report.* FROM report
           JOIN plan_node ON plan_node.id = report.node_id
           WHERE plan_node.plan_id = ? AND plan_node.{not_invalid()}
           ORDER BY report.created_at, report.id""",
        (plan_id,),
    ).fetchall()
    return [
        row for row in rows
        if (day := parse_date(row["created_at"])) is not None and start <= day <= end
    ]


def _weekly_already_asked(conn: sqlite3.Connection, start: date, end: date) -> bool:
    """本周是否已经问过——按触达记录判，避免兜底提醒变成每周刷屏。"""
    for row in conn.execute("SELECT sent_at FROM notification_log WHERE kind = 'weekly_checkpoint'"):
        day = parse_date(row["sent_at"])
        if day is not None and start <= day <= end:
            return True
    return False


def _replan_options(
    stage: sqlite3.Row | None, lag: dict[str, Any], progress: dict[str, Any] | None
) -> list[dict[str, str]]:
    """SPEC 第 6 节的三个出路：减量 / 顺延 / 换交付物。

    给的是**选项**不是决定：为什么这么调、调多少，最终由你裁定（第 8 节铁律）。
    """
    if lag["behind"]:
        target = lag["worst"]["title"]
        postpone_days = lag["worst"]["lag_days"]
    else:
        open_titles = (progress or {}).get("open_titles") or []
        target = open_titles[0] if open_titles else "当前阶段"
        postpone_days = 7  # 没有过期节点时按「往后挪一周」给建议
    deliverable = "当前目标"
    if stage is not None:
        deliverable = stage["deliverable"] or stage["title"]
    return [
        {"kind": "reduce_scope", "label": "减量",
         "detail": f"把「{target}」的范围缩到这一周做得完的量"},
        {"kind": "postpone", "label": "顺延",
         "detail": f"把「{target}」的计划完成日往后推 {postpone_days} 天，重新对一次现实"},
        {"kind": "swap_deliverable", "label": "换交付物",
         "detail": f"给这一阶段换一个同样能证明「{deliverable}」的交付物"},
    ]


def weekly_status(
    conn: sqlite3.Connection, today: date | None = None, plan_id: int | None = None
) -> dict[str, Any]:
    """本周检查点的判定结果：该不该问、以及问的时候要用的原料。

    这里只做判定和备料，不负责把三问写成邮件——那是 P4 触达的活（T15/T16）。
    """
    today = today or date.today()
    start, end = week_bounds(today)
    status: dict[str, Any] = {
        "due": False,
        "week": week_key(today),
        "week_start": start.isoformat(),
        "week_end": end.isoformat(),
        "plan_id": None,
        "current_stage": None,
        "stage_progress": None,
        "reports_this_week": [],
        "pushed": False,
        "behind": False,
        "behind_reason": None,
        "lag": {"lag_days": 0, "behind": False, "worst": None},
        "replan_options": [],
    }

    plan_row = resolve_plan(conn, plan_id)
    if plan_row is None:
        status["behind_reason"] = "还没有计划，暂时没什么可检查的"
        return status

    target_plan_id = int(plan_row["id"])
    lag = plan_lag(conn, target_plan_id, today)
    reports = reports_between(conn, target_plan_id, start, end)
    stage = current_stage(conn, target_plan_id)
    progress = None if stage is None else stage_completion(conn, int(stage["id"]))

    if lag["behind"]:
        behind_reason = (
            f"「{lag['worst']['title']}」到期 {lag['worst']['due_date']}，"
            f"落后 {lag['lag_days']} 天还没做完"
        )
    elif not reports:
        behind_reason = "本周没有报告，看不出这周推到哪了"
    else:
        behind_reason = None

    status.update({
        "due": not _weekly_already_asked(conn, start, end),
        "plan_id": target_plan_id,
        "current_stage": None if stage is None else {
            "id": stage["id"],
            "title": stage["title"],
            "deliverable": stage["deliverable"],
        },
        "stage_progress": progress,
        "reports_this_week": [dict(row) for row in reports],
        "pushed": bool(reports),
        "behind": lag["behind"],
        "behind_reason": behind_reason,
        "lag": lag,
        "replan_options": [] if behind_reason is None else _replan_options(stage, lag, progress),
    })
    return status
