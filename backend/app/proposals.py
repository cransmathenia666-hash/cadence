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
- `plan_blueprint`（`blueprint.py` 产，SPEC 决策 36）：沿对话出的**一棵树**（阶段 → 任务）。
  批准 = **按勾选建树**：`selected` 给的是勾中的阶段 / 任务下标，没勾的部分直接丢弃
  （蓝图是版本化的，想要可以再出一版）；不勾（`selected` 空）= 整份采纳。
- `profile_change`（`dialogue.py` 产，2026-09-18 T28 起）：计划对话里聊出的「我的状态变了」。
  批准 = **真的写进长期档案**（新增一条，走 `app/profile.py` 的同一道判重闸）——这是这一类
  的第一个生产者，也是「批准」第一次真的改档案；驳回只留痕。

裁定一律走台账的**业务终态**（`accepted` / `rejected`，SPEC 第 18 节第 22 条），
并顺手补上一直空着的 `decided_at`。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import blueprint, ledger, plan, profile
from .db import now_iso


class ProposalError(RuntimeError):
    """裁定链路上的明确错误：驳回没写理由、批准重排没选方向、选了不在选项里的方向。"""


class ProposalNotFound(ProposalError):
    """提案不存在 → 接口层翻成 404。"""


class ProposalConflict(ProposalError):
    """提案已经裁定过、或计划已收尾 → 接口层翻成 409。"""


# 只有「批准也不改任何东西」的类型才从这里取默认理由：批准时即使你没写理由，
# 台账那句话也要能读懂。有实质动作的类型（收尾计划 / 重排 / 建树 / 写档案）各自组词。
_APPROVE_REASONS = {
    "material_judgment": "批准：认可这次四问判断",
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
    selected: list[str] | None = None,
) -> dict[str, Any]:
    """裁定一条提案：批准（可选带方向 / 勾选）或驳回（必写理由）。

    全部前提先验完再动手：台账每个操作各自提交、没有请求级事务，
    验不过就一条都不写——提案保持 `pending`，你处理完可以再裁一次。
    蓝图那条路的「验」包括**建树前的查重**（`blueprint.resolve_build` 是只读的），
    所以这里先把要建的节点解析好，再改提案状态，最后才动手建。
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
        # 判据是「不是进行中」而不是「已收尾」：计划有四态（T27），
        # 暂停或作废的计划同样不该被这一条顺水收尾——它的状态不是这次提案能决定的。
        if str(target["status"]) != "active":
            raise ProposalConflict(
                f"这个计划已经不是进行中（{target['status']}），不必再裁一次"
            )
        closing_plan_id = int(raw_plan_id)

    # 蓝图：先只读地解析出要建哪些节点（查完重名），建的动作留到状态改完之后
    builds: list[Any] = []
    if approved and kind == blueprint.BLUEPRINT_KIND:
        builds = blueprint.resolve_build(conn, payload, selected)

    # 档案变更：同样先把「能不能写」验完（类别合法、不撞同类别一字不差的现有条目），
    # 写的动作留到状态改完之后——验不过就一条都不写，提案保持 pending 可重裁
    profile_category = ""
    profile_content = ""
    if approved and kind == "profile_change":
        profile_category = str(payload.get("category") or "").strip()
        profile_content = str(payload.get("content") or "").strip()
        if profile_category not in profile.PROFILE_TOKENS:
            raise ProposalError(
                f"提案里的类别「{profile_category}」不在约定的五个令牌里"
                f"（{' / '.join(profile.PROFILE_TOKENS)}），先把提案改对再来批准"
            )
        if not profile_content:
            raise ProposalError("提案里没有要写进档案的内容，批准它等于什么都没发生")
        try:
            profile.assert_no_duplicate(conn, profile_category, profile_content)
        except profile.ProfileConflict as error:
            raise ProposalConflict(str(error)) from error

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
        elif kind == blueprint.BLUEPRINT_KIND:
            task_count = sum(len(build.tasks) for build in builds)
            new_stages = sum(1 for build in builds if build.reuse_id is None)
            base = f"批准：按勾选建树（{new_stages} 个新阶段、{task_count} 件任务）"
        elif kind == "profile_change":
            base = f"批准：把这条写进长期档案（{profile_category}）"
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
    built: dict[str, Any] | None = None
    written: dict[str, Any] | None = None
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
    elif approved and kind == blueprint.BLUEPRINT_KIND:
        built = blueprint.apply_build(conn, int(payload.get("plan_id") or 0), builds)
        effect = "blueprint_built"
    elif approved and kind == "profile_change":
        try:
            written_id = profile.create_item(
                conn,
                category=profile_category,
                content=profile_content,
                reason=f"按提案 #{proposal_id} 批准写进档案：{str(payload.get('why') or '').strip() or '计划对话里聊出的变化'}",
                actor="user",
            )
        except (profile.ProfileError, ledger.LedgerError) as error:
            raise ProposalError(f"档案没能写进去：{error}") from error
        written = {"id": written_id, "category": profile_category, "content": profile_content}
        effect = "profile_written"

    return {
        "id": proposal_id,
        "kind": kind,
        "status": "accepted" if approved else "rejected",
        "effect": effect,
        "option": chosen if approved else None,
        "built": built,
        "written": written,
    }
