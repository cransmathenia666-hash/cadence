"""提案裁定：agent 与规则产出的待裁定结果，只有你点头才动计划与档案。

四种 `kind` 的来源，以及「批准」各自意味着什么（2026-09-17 定「最小诚实版」，
**2026-09-18 T29 收窄**：`stage_advance` 与 `plan_replan` 整类删除；**T31 新增第四类**）：

- `material_judgment`（`advisor.py` 产）：四问判断的结论。批准只记账——它回答的是
  「这份资料值不值得学」，不是新方向；落新方向由**候选采纳**那条路负责。
  （T29 起这一类的裁定仍留在这里，判资料的**输入与历史**搬到了 `/judge` 页。）
- `plan_blueprint`（`blueprint.py` 产，SPEC 决策 36）：沿对话出的**一棵树**（阶段 → 任务）。
  批准 = **按勾选建树**：`selected` 给的是勾中的阶段 / 任务下标，没勾的部分直接丢弃
  （蓝图是版本化的，想要可以再出一版）；不勾（`selected` 空）= 整份采纳。
- `profile_change`（`dialogue.py` 产，2026-09-18 T28 起）：计划对话里聊出的「我的状态变了」。
  批准 = **真的写进长期档案**（新增一条，走 `app/profile.py` 的同一道判重闸）——这是这一类
  的第一个生产者，也是「批准」第一次真的改档案；驳回只留痕。
- `plan_change`（`dialogue.py` 产，2026-09-18 T31 起，SPEC 决策 39）：计划对话里附带的
  **一条可执行建议**（改一个已有节点 / 加一件任务 / 加一个阶段）。批准 = 按 `action` 分流：
  改的走 `plan.update_node_fields`（原地改、**id 不变**、台账一条流水），加的走 `plan.add_node`
  （同一道防重名闸）。界面上那条「确认」就是这里的批准，**「忽略」= 驳回**（理由固定
  「聊天里先不动」）——所以每条建议都有归宿。这一类与 `profile_change` 的分工：一个动
  计划，一个动档案。

裁定一律走台账的**业务终态**（`accepted` / `rejected`，SPEC 第 18 节第 22 条），
并顺手补上一直空着的 `decided_at`。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import blueprint, ledger, plan, plan_change, profile
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


def list_decided(
    conn: sqlite3.Connection, kind: str, limit: int = 20
) -> dict[str, Any]:
    """某一类提案里**已经裁定过**的那些，最近的在前（T29 的「判资料」历史用它）。

    为什么要有它：`/judge` 页把裁定环节交回这里之后，过去判过的资料就查不到了——
    留一条**只读**的历史（不能改、也不能重裁），比让人记不住强。
    """
    rows = conn.execute(
        "SELECT * FROM proposal WHERE kind = ? AND status != 'pending'"
        " ORDER BY id DESC LIMIT ?",
        (kind, limit),
    ).fetchall()
    return {"items": [{**_public(row), "status": row["status"]} for row in rows]}


def _compose_reason(base: str, reason: str | None) -> str:
    extra = str(reason or "").strip()
    return base if not extra else f"{base}——{extra}"


def decide(
    conn: sqlite3.Connection,
    proposal_id: int,
    *,
    approved: bool,
    reason: str | None = None,
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

    # T29：`stage_advance` 与 `plan_replan` 整类删除，不再有生产者。
    # 库里可能还留着老类型（历史或别处写进来的），这里明确拒绝而不是当通用类型放行——
    # 批准一条「批准也不改任何东西」的老提案没有意义，还不如说清它已经不作数。
    if approved and kind in ("stage_advance", "plan_replan"):
        raise ProposalError(
            f"「{kind}」这类提案已在 T29 整类删除（规则不再产、也不再裁定）——"
            "阶段推进与落后的处理改在计划页上：落后会显示成一句提醒，收尾计划有按钮。"
            "驳回它仍可——那会留一条「当时判过它不作数」的台账记录"
        )

    # 计划改动（T31）：同样先把「能不能动」验完（节点还在不在、字段能不能改、名字撞不撞），
    # 动的动作留到状态改完之后——验不过就一条都不写，提案保持 pending 可重裁
    change: plan_change.ChangePlan | None = None
    if approved and kind == plan_change.KIND:
        try:
            change = plan_change.resolve(conn, payload)
        except plan_change.PlanChangeNotFound as error:
            raise ProposalNotFound(str(error)) from error
        except plan_change.PlanChangeConflict as error:
            raise ProposalConflict(str(error)) from error
        except plan_change.PlanChangeError as error:
            raise ProposalError(str(error)) from error
        except plan.PlanError as error:
            # resolve 里的防重名闸复用 `plan.assert_no_open_duplicate`，它抛的是 plan 的错误
            raise ProposalError(str(error)) from error

    if approved:
        if kind == blueprint.BLUEPRINT_KIND:
            task_count = sum(len(build.tasks) for build in builds)
            new_stages = sum(1 for build in builds if build.reuse_id is None)
            base = f"批准：按勾选建树（{new_stages} 个新阶段、{task_count} 件任务）"
        elif kind == "profile_change":
            base = f"批准：把这条写进长期档案（{profile_category}）"
        elif kind == plan_change.KIND:
            # 台账那句话说清「到底批准了哪一条」——summary 是后端拼的人话一行
            base = f"批准：{payload.get('summary') or '按聊天里的建议改动计划'}"
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
    added: dict[str, Any] | None = None
    updated: dict[str, Any] | None = None
    if approved and kind == blueprint.BLUEPRINT_KIND:
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
    elif approved and kind == plan_change.KIND and change is not None:
        # 验已经全验过了（resolve），这里才是唯一一次写：改的原地改、加的建节点
        try:
            result = plan_change.apply(conn, change)
        except (plan.PlanError, ledger.LedgerError) as error:
            raise ProposalError(f"改动没能落地：{error}") from error
        if change.action == plan_change.UPDATE_NODE:
            updated = result
            effect = "node_updated"
        else:
            added = result
            effect = "node_added"

    return {
        "id": proposal_id,
        "kind": kind,
        "status": "accepted" if approved else "rejected",
        "effect": effect,
        "built": built,
        "written": written,
        "added": added,
        "updated": updated,
    }
