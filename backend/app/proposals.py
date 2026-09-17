"""提案裁定：agent 与规则产出的待裁定结果，只有你点头才动计划与档案。

四种 `kind` 的来源，以及「批准」各自意味着什么（2026-09-17 用户拍板「最小诚实版」）：

- `stage_advance`（`plan.py` 产）：阶段的检查点全收尾后问「进不进下一阶段」。
  当前阶段是**算出来的**（第一个没收尾的阶段），所以「进入下一阶段」批准后没有可写的
  结构——阶段一收尾，当前阶段自己就往前走；唯一有实质动作的是「后面没有更多阶段了」
  那一条，批准 = 把这个计划收尾（`closed`）。
- `plan_replan`（`plan.py` 产）：落后、或整周一条报告都没有时给三个出路
  （减量 / 顺延 / 换交付物）。批准要求你选一个方向，选中的方向进台账——**改节点字段
  的写入口还不存在**（台账的改写只对档案与计划开放），所以这一轮批准只留决策记录，
  计划仍由你手工改；界面会把该方向的原文摆出来照着改。
- `material_judgment`（`advisor.py` 产）：四问判断的结论。批准只记账——它回答的是
  「这份资料值不值得学」，不是新方向；落新方向由**候选采纳**那条路负责。
- `profile_change`：还没有生产者（未来的档案提炼 agent 路线会产，见 SPEC 第 17 节第 3 条），
  批准同样只记账；等它真有了生产者，再来这一步定义「批准即写档案」的形状。

裁定一律走台账的**业务终态**（`accepted` / `rejected`，SPEC 第 18 节第 22 条），
并顺手补上一直空着的 `decided_at`。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import ledger, plan
from .db import now_iso


class ProposalError(RuntimeError):
    """裁定链路上的明确错误：驳回没写理由、批准重排没选方向、选了不在选项里的方向。"""


class ProposalNotFound(ProposalError):
    """提案不存在 → 接口层翻成 404。"""


class ProposalConflict(ProposalError):
    """提案已经裁定过、或计划已收尾 → 接口层翻成 409。"""


# 四种类型各自的默认裁定理由：批准时不写理由也要让台账那句话能读懂。
_APPROVE_REASONS = {
    "material_judgment": "批准：认可这次四问判断",
    "profile_change": "批准：认可这条档案变更",
}


def _require_pending(conn: sqlite3.Connection, proposal_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM proposal WHERE id = ?", (proposal_id,)).fetchone()
    if row is None:
        raise ProposalNotFound(f"提案 id={proposal_id} 不存在")
    if row["status"] != "pending":
        raise ProposalConflict(f"提案 id={proposal_id} 已经裁定过了（{row['status']}），不能再改")
    return row


def payload_of(row: sqlite3.Row) -> dict[str, Any]:
    """把 payload 那列 JSON 解出来。

    坏 JSON 不让整张列表 500：解不开就当空对象——这一列只由本仓库写入，
    真是坏的说明有 bug，前端少显示几行比整页打不开强。
    """
    try:
        parsed = json.loads(row["payload"])
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _public(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "payload": payload_of(row),
        "reason": row["reason"],
        "created_at": row["created_at"],
        "decided_at": row["decided_at"],
    }


def list_pending(conn: sqlite3.Connection, kind: str | None = None) -> dict[str, Any]:
    """待裁定的提案，按提出顺序排；`kind` 传了就只取那一类。"""
    sql = "SELECT * FROM proposal WHERE status = 'pending'"
    params: list[Any] = []
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    rows = conn.execute(sql + " ORDER BY id", tuple(params)).fetchall()
    return {"proposals": [_public(row) for row in rows]}


def _option(payload: dict[str, Any], kind: str) -> dict[str, Any]:
    for item in payload.get("options") or []:
        if isinstance(item, dict) and str(item.get("kind")) == kind:
            return item
    return {}


def _compose_reason(base: str, reason: str | None) -> str:
    extra = str(reason or "").strip()
    return base if not extra else f"{base}——{extra}"


def decide(
    conn: sqlite3.Connection,
    proposal_id: int,
    *,
    approved: bool,
    reason: str | None = None,
    option: str | None = None,
) -> dict[str, Any]:
    """裁定一条提案：批准（可选带方向）或驳回（必写理由）。

    全部前提先验完再动手：台账每个操作各自提交、没有请求级事务，
    验不过就一条都不写——提案保持 `pending`，你处理完可以再裁一次。
    """
    row = _require_pending(conn, proposal_id)
    kind = str(row["kind"])
    payload = payload_of(row)

    if not approved and not str(reason or "").strip():
        raise ProposalError("驳回必须写明理由——它进台账，回答「当时为什么不同意」")

    # 批准「后面没有更多阶段了」那一类推进提案 = 收尾计划，先把目标计划验清楚
    closing_plan_id: int | None = None
    if approved and kind == "stage_advance" and payload.get("next_stage_id") is None:
        raw_plan_id = payload.get("plan_id")
        target = plan.resolve_plan(conn, int(raw_plan_id)) if raw_plan_id is not None else None
        if target is None:
            raise ProposalError(f"提案里的计划 id={raw_plan_id} 已不存在，先去计划表核对一下")
        if str(target["status"]) == "closed":
            raise ProposalConflict("这个计划已经收尾了，不必再裁一次")
        closing_plan_id = int(raw_plan_id)

    chosen: str | None = None
    if approved and kind == "plan_replan":
        chosen = str(option or "").strip()
        allowed = [str(item.get("kind")) for item in payload.get("options") or [] if isinstance(item, dict)]
        if not chosen:
            raise ProposalError("批准重排提案要选一个方向——减量、顺延还是换交付物")
        if chosen not in allowed:
            raise ProposalError(
                f"方向「{chosen}」不在这次提案给的选项里（{'、'.join(allowed) or '无'}）"
            )

    if approved:
        if kind == "plan_replan":
            base = f"批准：{_option(payload, chosen or '').get('label') or chosen}"
        elif kind == "stage_advance" and closing_plan_id is None:
            base = f"批准：进入下一阶段「{payload.get('next_stage_title')}」"
        elif closing_plan_id is not None:
            base = "批准：收尾这个计划"
        else:
            base = _APPROVE_REASONS.get(kind, f"批准：{kind}")
    else:
        base = "驳回"

    ledger.set_status(
        conn,
        "proposal",
        proposal_id,
        "accepted" if approved else "rejected",
        actor="user",
        reason=_compose_reason(base, reason),
        extra={"decided_at": now_iso()},
    )

    effect = "recorded_only"
    if approved and closing_plan_id is not None:
        ledger.set_status(
            conn,
            "plan",
            closing_plan_id,
            "closed",
            actor="user",
            reason=f"按提案 #{proposal_id} 收尾：阶段都收尾了，后面没有更多阶段",
        )
        effect = "plan_closed"
    elif approved and kind == "plan_replan":
        effect = "replan_recorded"

    return {
        "id": proposal_id,
        "kind": kind,
        "status": "accepted" if approved else "rejected",
        "effect": effect,
        "option": chosen if approved else None,
    }
