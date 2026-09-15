"""计划表：节点状态机、报告推进、落后量、周检查点判定。

为什么规则都集中在这里：SPEC 第 10 节把「什么算落后、什么算阶段完成」列进了
确定性优先清单——这类判断一律不调模型，必须可复现、可测试，所以只放一处。

写入仍然守台账铁律：真正的状态变更调用 `ledger.set_status`，
本模块只负责判断「这次迁移合不合法」和「迁完之后该产出什么提案」。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import ledger

NODE_STATUSES = ("not_started", "in_progress", "done", "stuck", "skipped")

# 终态：到了这里这个节点就不用再管了。
SETTLED_STATUSES = ("done", "skipped")

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


def get_node(conn: sqlite3.Connection, node_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM plan_node WHERE id = ?", (node_id,)).fetchone()


def get_stages(conn: sqlite3.Connection, plan_id: int) -> list[sqlite3.Row]:
    """计划下的阶段，按计划里的顺序。"""
    return list(
        conn.execute(
            """SELECT * FROM plan_node
               WHERE plan_id = ? AND level = 'stage'
               ORDER BY sort_order, id""",
            (plan_id,),
        ).fetchall()
    )


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
    if new_status not in LEGAL_TRANSITIONS.get(before, set()):
        raise PlanError(f"非法迁移：{before} → {new_status}（节点 id={node_id}）")

    return ledger.set_status(conn, "plan_node", node_id, new_status, actor=actor, reason=reason)


def stage_completion(conn: sqlite3.Connection, stage_id: int) -> dict[str, Any]:
    """阶段完成判定：它的检查点是不是都收尾了。

    「收尾」= 完成或跳过。跳过是你裁定过的结果，不该把阶段永远卡在那里。
    """
    nodes = conn.execute(
        """SELECT id, title, status FROM plan_node
           WHERE parent_id = ? ORDER BY sort_order, id""",
        (stage_id,),
    ).fetchall()
    settled = [node for node in nodes if node["status"] in SETTLED_STATUSES]
    return {
        "stage_id": stage_id,
        "total": len(nodes),
        "settled": len(settled),
        "done": len([node for node in nodes if node["status"] == "done"]),
        "skipped": len([node for node in nodes if node["status"] == "skipped"]),
        # 没有检查点的阶段不算完成，避免空阶段自动触发推进提案
        "complete": bool(nodes) and len(settled) == len(nodes),
        "open_titles": [node["title"] for node in nodes if node["status"] not in SETTLED_STATUSES],
    }


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
    if not result["complete"]:
        return None
    if _pending_stage_proposal(conn, stage_id) is not None:
        return None

    next_stage = _next_stage(conn, stage)
    if next_stage is None:
        question = (
            f"阶段「{stage['title']}」的检查点已全部收尾"
            f"（完成 {result['done']} / 跳过 {result['skipped']}），"
            f"后面没有更多阶段了，是否收尾这个计划？"
        )
    else:
        question = (
            f"阶段「{stage['title']}」的检查点已全部收尾"
            f"（完成 {result['done']} / 跳过 {result['skipped']}），"
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
