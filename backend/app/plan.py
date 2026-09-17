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

from . import ledger
from .db import now_iso

NODE_STATUSES = ("not_started", "in_progress", "done", "stuck", "skipped")

# 终态：到了这里这个节点就不用再管了。
SETTLED_STATUSES = ("done", "skipped")

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
) -> int:
    """新建一个阶段或检查点。

    建节点一律走这里：先挡重复再落库。放在这个模块而不是接口层，
    是为了让规则没地方绕过——以后 P3 由提案建节点时也会经过同一道闸。
    """
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
    """阶段是否完成（2026-09-17 起的新判定，SPEC 决策 30）：

    ① 阶段自己到了终态，或 ② **全部任务打勾/跳过 且 交付物已提交**。
    没有任务的阶段 = 任务条件天然满足（空集），只看交付物——老/空阶段不被卡死。
    """
    if stage["status"] in SETTLED_STATUSES:
        return True
    if not stage_completion(conn, int(stage["id"]))["all_tasks_settled"]:
        return False
    return deliverable_submission(conn, int(stage["id"])) is not None


def _next_stage(conn: sqlite3.Connection, stage: sqlite3.Row) -> sqlite3.Row | None:
    stages = get_stages(conn, stage["plan_id"])
    for index, candidate in enumerate(stages):
        if candidate["id"] == stage["id"]:
            return stages[index + 1] if index + 1 < len(stages) else None
    return None


def _pending_stage_proposal(conn: sqlite3.Connection, stage_id: int) -> int | None:
    """该阶段是否已有待裁定的推进提案——有就不再产一条，避免刷屏。"""
    for row in ledger.fetch_active(conn, "proposal"):
        if row["kind"] != "stage_advance":
            continue
        try:
            payload = json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError):
            continue
        if payload.get("stage_id") == stage_id:
            return int(row["id"])
    return None


def maybe_stage_advance_proposal(
    conn: sqlite3.Connection, stage_id: int, actor: str = "agent"
) -> int | None:
    """阶段检查点全部收尾后，产出「是否进入下一阶段」的提案。

    产出的是**提案**而不是直接推进：进不进入下一阶段由你裁定（SPEC 第 8 节铁律）。
    返回提案 id；条件不满足或已有待裁定提案时返回 None。
    """
    stage = get_node(conn, stage_id)
    if stage is None:
        raise PlanError(f"节点 id={stage_id} 不存在")
    if stage["level"] != "stage":
        raise PlanError(f"只有阶段节点才会产出推进提案，id={stage_id} 是 {stage['level']}")

    result = stage_completion(conn, stage_id)
    if not stage_finished(conn, stage):
        return None
    if _pending_stage_proposal(conn, stage_id) is not None:
        return None

    submission = deliverable_submission(conn, stage_id)
    next_stage = _next_stage(conn, stage)
    if next_stage is None:
        question = (
            f"阶段「{stage['title']}」的任务全部完成、交付物已提交"
            f"（任务完成 {result['done']} / 跳过 {result['skipped']}），"
            f"后面没有更多阶段了，是否收尾这个计划？"
        )
    else:
        question = (
            f"阶段「{stage['title']}」的任务全部完成、交付物已提交"
            f"（任务完成 {result['done']} / 跳过 {result['skipped']}），"
            f"是否进入下一阶段「{next_stage['title']}」？"
        )

    payload = {
        "plan_id": stage["plan_id"],
        "stage_id": stage_id,
        "stage_title": stage["title"],
        "next_stage_id": next_stage["id"] if next_stage is not None else None,
        "next_stage_title": next_stage["title"] if next_stage is not None else None,
        "settled": result["settled"],
        "done": result["done"],
        "skipped": result["skipped"],
        "deliverable_url": None if submission is None else submission["url"],
        "question": question,
    }
    return ledger.create_active(
        conn,
        "proposal",
        {
            "kind": "stage_advance",
            "payload": json.dumps(payload, ensure_ascii=False),
            "reason": question,
        },
        actor=actor,
    )


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

    # 节点收尾后看看它所属阶段是不是也收尾了；是就产出「是否进入下一阶段」提案。
    parent = get_node(conn, int(node["parent_id"])) if node["parent_id"] is not None else None
    if node["level"] == "stage":
        proposal_id = maybe_stage_advance_proposal(conn, node_id)
    elif parent is not None and parent["level"] == "stage":
        proposal_id = maybe_stage_advance_proposal(conn, int(parent["id"]))
    else:
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


def _propose_after_child_settles(conn: sqlite3.Connection, node: sqlite3.Row) -> int | None:
    """子节点收尾后看看它所属阶段是不是也完成了；是就产出推进提案（任务/报告共用）。"""
    parent = get_node(conn, int(node["parent_id"])) if node["parent_id"] is not None else None
    if parent is None or parent["level"] != "stage":
        return None
    return maybe_stage_advance_proposal(conn, int(parent["id"]))


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
        "proposal_id": _propose_after_child_settles(conn, node),
    }


def skip_task(
    conn: sqlite3.Connection, node_id: int, reason: str, actor: str = "user"
) -> dict[str, Any]:
    """任务跳过：跳过算完成的一种，但必须写一句理由——它是裁定，要留痕。"""
    if not str(reason or "").strip():
        raise PlanError("跳过任务必须写一句理由——它进台账，回答「当时为什么不做」")
    node = _require_level(conn, node_id, "task", "跳过")
    before = node["status"]
    assert_transition(before, "skipped")
    ledger.set_status(
        conn, "plan_node", node_id, "skipped", actor=actor, reason=str(reason).strip()
    )
    return {
        "node_id": node_id,
        "node_status_before": before,
        "node_status": "skipped",
        "proposal_id": _propose_after_child_settles(conn, node),
    }


def submit_deliverable(
    conn: sqlite3.Connection, node_id: int, url: str, note: str, actor: str = "user"
) -> dict[str, Any]:
    """提交交付物：阶段上的独立动作（决策 32）。

    可重新提交——每次落一行（旧值天然留痕），「当前交付物」= 最新那一行。
    提交后照例看看阶段是不是就此完成，是就产出推进提案。
    """
    if not str(url or "").strip():
        raise PlanError("交付物要填链接——仓库、能访问的 URL、录屏都行")
    if not str(note or "").strip():
        raise PlanError("交付物要写一句话说明")
    _require_level(conn, node_id, "stage", "提交交付物")

    cleaned_url, cleaned_note = str(url).strip(), str(note).strip()
    timestamp = now_iso()
    cursor = conn.execute(
        "INSERT INTO deliverable_submission (node_id, url, note, created_at) VALUES (?, ?, ?, ?)",
        (node_id, cleaned_url, cleaned_note, timestamp),
    )
    submission_id = int(cursor.lastrowid)
    ledger.log_event(
        conn, "plan_node", node_id, "deliverable_submit", None, cleaned_url, cleaned_note, actor
    )
    # 提交行与台账流水一起落盘：这次没有状态迁移，set_status 不一定会写东西
    conn.commit()

    return {
        "submission_id": submission_id,
        "node_id": node_id,
        "url": cleaned_url,
        "note": cleaned_note,
        "created_at": timestamp,
        "proposal_id": maybe_stage_advance_proposal(conn, node_id),
    }


# ---------- 落后量 ----------

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
    return {
        "plan": {
            "id": plan_row["id"],
            "goal": plan_row["goal"],
            "status": plan_row["status"],
            "valid_from": plan_row["valid_from"],
        },
        "current_stage": None if current is None else {
            "id": current["id"],
            "title": current["title"],
            "deliverable": current["deliverable"],
            "status": current["status"],
            "progress": stage_completion(conn, int(current["id"])),
        },
        "lag": plan_lag(conn, int(plan_row["id"]), today),
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


def _pending_replan_for_week(conn: sqlite3.Connection, week: str) -> int | None:
    """本周是否已经产出过重排提案——有就不再来一条，免得每周开机刷一堆。"""
    for row in ledger.fetch_active(conn, "proposal"):
        if row["kind"] != "plan_replan":
            continue
        try:
            payload = json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError):
            continue
        if payload.get("week") == week:
            return int(row["id"])
    return None


def ensure_weekly_replan_proposal(
    conn: sqlite3.Connection,
    today: date | None = None,
    plan_id: int | None = None,
    actor: str = "agent",
) -> int | None:
    """未推进时产出重排提案：落后，或本周一条报告都没有。

    产出的是**提案**，不是直接改计划——减量、顺延还是换交付物，
    最终由你裁定（SPEC 第 8 节铁律）。同一周只产一条。
    """
    today = today or date.today()
    status = weekly_status(conn, today=today, plan_id=plan_id)
    if status["plan_id"] is None or status["behind_reason"] is None:
        return None
    if _pending_replan_for_week(conn, status["week"]) is not None:
        return None

    stage = status["current_stage"]
    payload = {
        "week": status["week"],
        "plan_id": status["plan_id"],
        "stage_id": None if stage is None else stage["id"],
        "stage_title": None if stage is None else stage["title"],
        "why": status["behind_reason"],
        "lag_days": status["lag"]["lag_days"],
        "options": status["replan_options"],
    }
    return ledger.create_active(
        conn,
        "proposal",
        {
            "kind": "plan_replan",
            "payload": json.dumps(payload, ensure_ascii=False),
            "reason": status["behind_reason"],
        },
        actor=actor,
    )
