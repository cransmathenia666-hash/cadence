"""提案裁定：agent 与规则产出的待裁定结果，只有你点头才动计划与档案。

四种 `kind` 的来源，以及「批准」各自意味着什么（2026-09-17 定「最小诚实版」，
**2026-09-18 T29 收窄**：`stage_advance` 与 `plan_replan` 整类删除；**T31 新增第四类**）：

- `material_judgment`（`advisor.py` 产）：四问判断的结论。批准只记账——它回答的是
  「这份资料值不值得学」，不是新方向；落新方向由**候选采纳**那条路负责。
  （T29 起这一类的裁定仍留在这里，判资料的**输入与历史**搬到了 `/judge` 页。）
- `plan_blueprint`（`blueprint.py` 产，SPEC 决策 36；OC-06 起为 **v2 payload**：成果契约
  + 阶段树）。旧 v1（无契约）只读兼容、**不允许直接批准**；v2 的批准要「契约激活 + 建树
  + 规划会话关闭」原子完成（方案 §5.4）——那条链路 OC-07 才接上，当前明确拒绝批准，
  用户可把蓝图**退回规划**（会话恢复 active）或驳回。
- `profile_change`（`dialogue.py` 产，2026-09-18 T28 起）：计划对话里聊出的「我的状态变了」。
  批准 = **真的写进长期档案**（新增一条，走 `app/profile.py` 的同一道判重闸）——这是这一类
  的第一个生产者，也是「批准」第一次真的改档案；驳回只留痕。
- `plan_change`（`dialogue.py` 产，2026-09-18 T31 起，SPEC 决策 39）：计划对话里附带的
  **一条可执行建议**（改一个已有节点 / 往某个阶段加任务 / 加一个阶段带它的任务）。批准 =
  按 `action` 分流：改的走 `plan.update_node_fields`（原地改、**id 不变**、台账一条流水），
  加的走 `plan.add_node`（同一道防重名闸；**一次可以建一小批**——2026-09-19 用户走查放宽，见
  `plan_change.MAX_TASKS`）。界面上那条「确认」就是这里的批准，**「忽略」= 驳回**（理由固定
  「聊天里先不动」）——所以每条建议都有归宿。这一类与 `profile_change` 的分工：一个动
  计划，一个动档案。
- `memory_change`（`memory.py` 产，2026-09-21 记忆系统）：记忆收件箱里的一条候选
  （新增 / 取代 / 复核处理）。批准 = **真的写长期记忆**——`add` 落新条目、`supersede` 走
  台账取代（旧值留痕）、`review` 按 `decision` 续期或作废。**批准前再验一次**：目标那条
  记忆可能已经被改掉了，那时给出 409 而不是照着旧假设写。批量批准走
  `POST /api/memory/batch-approve`，只收「新增 + 用户陈述」——取代、Agent 推断、彻底删除
  一律逐条。

裁定一律走台账的**业务终态**（`accepted` / `rejected`，SPEC 第 18 节第 22 条），
并顺手补上一直空着的 `decided_at`。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import advisor, blueprint, contract, ledger, memory, plan, plan_change, profile
from .db import atomic, now_iso


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
    # 契约修改有实质动作，正常走下面按 summary 组词的分支；这里只兜「payload 缺 summary」的底
    plan_change.CONTRACT_CHANGE_KIND: "批准：按对话里的建议激活新版成果契约",
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
    confirm_contract: bool = False,
    contract_overrides: dict[str, Any] | None = None,
    landing_mode: str | None = None,
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

    # 蓝图（OC-06/OC-07 起）：批准入口按 payload 版本分流。
    # - 旧 v1（没有成果契约）：只读兼容，不允许直接批准——缺失的契约绝不半猜，
    #   请重新生成 v2 蓝图（方案 §9.4）。
    # - v2（带契约 + 阶段树）：必须显式确认契约（confirm_contract），缺了就地拒绝、
    #   提案保持 pending；确认了就走 approve_atomic 的「预检 + 原子批准」——
    #   契约激活、建树、提案终态、会话关闭同一事务完成（方案 §5.4），提前返回。
    if approved and kind == blueprint.BLUEPRINT_KIND:
        if not blueprint.is_v2_payload(payload):
            raise ProposalError(
                "这是旧版蓝图（没有成果契约），不能直接批准——"
                "请在规划对话里重新生成 v2 蓝图后再来"
            )
        if not confirm_contract:
            raise ProposalError(
                "批准蓝图必须先确认成果契约：勾选「我认可这个成果定义和验收条件」"
                "（confirm_contract）——没看见契约就落库不是批准"
            )
        return blueprint.approve_atomic(
            conn,
            proposal_id,
            payload,
            selected=selected,
            contract_overrides=contract_overrides,
            landing_mode=landing_mode,
            reason=reason,
        )

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

    # 成果契约修改（OC-09）：同样先把「能不能动」验完——计划还在进行中、契约没换版
    # （提案按别的版本拟的就地拒绝）、现版 + 拟改合并后仍过得了 contract.validate。
    # 验不过就一条都不写，提案保持 pending 可重裁；写（激活新版本）留到状态改完之后。
    contract_change: dict[str, Any] | None = None
    if approved and kind == plan_change.CONTRACT_CHANGE_KIND:
        try:
            contract_change = plan_change.resolve_contract_change(conn, payload)
        except plan_change.PlanChangeNotFound as error:
            raise ProposalNotFound(str(error)) from error
        except plan_change.PlanChangeConflict as error:
            raise ProposalConflict(str(error)) from error
        except plan_change.PlanChangeError as error:
            raise ProposalError(str(error)) from error

    # 记忆候选（2026-09-21 记忆系统）：同样先把「能不能写」验完（目标那条记忆还在不在、
    # 是否仍有效、内容有没有和某条现行记忆撞成完全一样），写的动作留到状态改完之后
    if approved and kind == memory.KIND:
        try:
            memory.precheck(conn, payload)
        except memory.MemoryNotFound as error:
            raise ProposalNotFound(str(error)) from error
        except memory.MemoryConflict as error:
            raise ProposalConflict(str(error)) from error
        except memory.MemoryError as error:
            raise ProposalError(str(error)) from error

    if approved:
        if kind == "profile_change":
            base = f"批准：把这条写进长期档案（{profile_category}）"
        elif kind == plan_change.KIND:
            # 台账那句话说清「到底批准了哪一条」——summary 是后端拼的人话一行
            base = f"批准：{payload.get('summary') or '按聊天里的建议改动计划'}"
        elif kind == plan_change.CONTRACT_CHANGE_KIND:
            base = f"批准：{payload.get('summary') or '按聊天里的建议修改成果契约'}"
        elif kind == memory.KIND:
            label = memory.ACTIONS.get(str(payload.get("action")), str(payload.get("action")))
            base = f"批准：{label}一条长期记忆"
        else:
            base = _APPROVE_REASONS.get(kind, f"批准：{kind}")
    else:
        base = "驳回"

    # 规划会话（OC-05）：出它的会话随裁定流转——批准 = 规划完成（converted），
    # 驳回 = 退回规划对话继续聊（active），对话历史不丢。老提案没有会话归属，跳过。
    session_id = int(payload["planning_session_id"]) if (
        kind == blueprint.BLUEPRINT_KIND and payload.get("planning_session_id")
    ) else None

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
    remembered: dict[str, Any] | None = None
    contract_result: dict[str, Any] | None = None
    if approved and kind == "profile_change":
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
    elif approved and kind == memory.KIND:
        # 验已经全验过了（precheck），这里才是唯一一次写：新增落条目、取代走台账、复核续期 / 作废
        try:
            remembered = memory.apply(conn, payload, proposal_id=proposal_id)
        except (memory.MemoryError, ledger.LedgerError) as error:
            raise ProposalError(f"记忆没能写进去：{error}") from error
        effect = str(remembered["effect"])
    elif approved and kind == plan_change.CONTRACT_CHANGE_KIND and contract_change is not None:
        # 成果契约修改（OC-09）：验已经全验过了（resolve_contract_change），这里才是唯一
        # 一次写——复用 `contract.activate` 激活新版本：旧版 superseded、计划双模式标记、
        # 受影响的当前验收按既有 invalidate 路径失效、台账流水。activate 自己不提交，
        # 按它的约定包在 `db.atomic` 里：激活失败整体回滚（提案已 accepted 的那笔账
        # 与既有 plan_change 分支同一口径——预检已把可预见的失败全拦在前面）。
        try:
            with atomic(conn):
                activated = contract.activate(
                    conn,
                    int(contract_change["plan_id"]),
                    contract_change["merged"],
                    source_kind="manual",
                    reason=(
                        f"按提案 #{proposal_id} 批准修改成果契约："
                        f"{str(payload.get('why') or '').strip() or '计划对话里聊出的契约调整'}"
                    ),
                    actor="user",
                )
        except (contract.ContractError, contract.ContractConflict, ledger.LedgerError) as error:
            raise ProposalError(f"新版成果契约没能激活：{error}") from error
        effect = "contract_activated"
        contract_result = {
            "id": int(activated["id"]),
            "version": int(activated["version"]),
            "superseded_id": activated["superseded_id"],
            "reviews_invalidated": int(activated["reviews_invalidated"]),
        }

    if not approved and session_id is not None:
        # 只有还停在 blueprint_pending 的会话才「退回规划」；会话已经过期 / 被放弃 /
        # 已转正（终态，不可重开）时，驳回过期旧稿不该把它复活——原样留只读。
        session_row = advisor.get_planning_session(conn, session_id)
        if session_row is not None and str(session_row["status"]) == "blueprint_pending":
            advisor.set_session_status(
                conn,
                session_id,
                "active",
                reason=f"蓝图提案 #{proposal_id} 被驳回，退回规划对话继续聊",
                actor="user",
            )

    # 确认落地后往对话历史追加一条「已完成」事实行（2026-10-04 走查修复）：
    # 模型下一轮读历史时看得见「这条建议已经批准落地」，不再重复追问、重复发确认卡。
    # plan_dialogue 是追加式日志不经台账（同 dialogue._record 的口径）；只给计划对话类
    # 提案补记，回执里那句 summary 就是「到底改了什么」的人话。
    if approved and kind in (plan_change.KIND, plan_change.CONTRACT_CHANGE_KIND):
        note = str(payload.get("summary") or "").strip()
        target_plan_id = payload.get("plan_id")
        if note and target_plan_id is not None:
            conn.execute(
                "INSERT INTO plan_dialogue (plan_id, role, content, questions, report_id, created_at)"
                " VALUES (?, 'assistant', ?, NULL, NULL, ?)",
                (int(target_plan_id), f"（已按你的确认完成：{note}）", now_iso()),
            )

    return {
        "id": proposal_id,
        "kind": kind,
        "status": "accepted" if approved else "rejected",
        "effect": effect,
        "built": built,
        "written": written,
        "added": added,
        "updated": updated,
        "remembered": remembered,
        # OC-09：contract_change 批准的回执带新版本号（effect=contract_activated 时非空）
        "contract": contract_result,
    }
