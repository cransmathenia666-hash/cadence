"""成果契约：校验、版本激活与只读历史（成果闭环 P1，方案 `docs/ideas/成果闭环重构开发方案.md` §4–§5）。

为什么单独一个模块：`outcome_contract` 的写入必须只有一个口子（方案 §5.4 写入口矩阵——
`contract.activate_version`），契约的非空约束、条数边界、稳定 id 与「一计划一 active」
这类规则也只在一处判。接口层与提案层都从这里走，谁也不直接 UPDATE 这张表。

三条规矩，与方案一一对应：

- **激活前先验全**：契约不完整（缺结果/价值/成功定义/验收条件、条数越界、重复 id）
  一律拒绝——一个验收条件都说不清的计划不配叫成果契约。
- **版本只增不改**：新版本激活时旧版本标 `superseded`（保留正文与激活时间），
  历史验收记录各自带当时的快照，永不改写。
- **不猜旧账**：激活只写契约与计划的双模式标记，不碰旧报告、旧交付物、旧 closed 的
  任何历史结论；只有验收条件或必需证据要求真的变化时，才把按旧版本判的**当前**验收
  失效（历史行原样保留，只翻 `is_current`）。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import ledger
from .db import now_iso

# 证据类型枚举（方案 §6.3）。`legacy` 只由旧交付物接口的兼容路径写入，用户提交走不了，
# 它也不满足任何 required 证据要求——旧链接不自动变成新验收。
EVIDENCE_KINDS: tuple[str, ...] = (
    "repository", "link", "document", "demo", "screenshot", "text", "other",
)

# 契约的条数边界（方案 §4.1）：验收条件 2–5 条、证据要求 1–5 条。
MIN_CRITERIA, MAX_CRITERIA = 2, 5
MIN_REQUIREMENTS, MAX_REQUIREMENTS = 1, 5

CRITERIA_STATUSES = ("active", "superseded")


class ContractError(RuntimeError):
    """契约层面的明确错误：校验不过、计划不存在或不在进行中、版本冲突。"""


class ContractConflict(ContractError):
    """与现状冲突（计划不在进行中等）→ 接口层翻成 409。"""


# ---------- 校验与规范化 ----------

def _clean_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ContractError(f"{label}不能为空")
    return text


def _assign_ids(items: list[dict[str, Any]], prefix: str) -> list[dict[str, Any]]:
    """补齐稳定 id：前端给不了的由服务端按顺序生成；给重复 id 的一律拒绝
    （方案 §4.3.1：稳定编号由服务端补齐，不接受自造的重复或跨契约 id）。"""
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        entry = dict(item)
        given = str(entry.get("id") or "").strip()
        entry["id"] = given or f"{prefix}-{index}"
        if entry["id"] in seen:
            raise ContractError(f"条件/证据要求的 id「{entry['id']}」重复了——每条都要有自己的编号")
        seen.add(entry["id"])
        result.append(entry)
    return result


def validate(data: dict[str, Any]) -> dict[str, Any]:
    """校验并规范化一份契约。返回带稳定 id 的字典；不合格抛 `ContractError`。

    这是契约的唯一判据：手工建计划、激活新版本、（后续批次的）蓝图批准都先过这里，
    谁也不自带一套「松一点」的校验。
    """
    if not isinstance(data, dict):
        raise ContractError("契约必须是一个 JSON 对象")

    contract: dict[str, Any] = {
        "title": _clean_text(data.get("title"), "成果名称"),
        "outcome": _clean_text(data.get("outcome"), "最终要产生的结果（outcome）"),
        "value": _clean_text(data.get("value"), "这个结果的价值（value）"),
        "success_statement": _clean_text(data.get("success_statement"), "做到什么算够（success_statement）"),
    }

    criteria = data.get("acceptance_criteria")
    if not isinstance(criteria, list) or not (MIN_CRITERIA <= len(criteria) <= MAX_CRITERIA):
        raise ContractError(
            f"验收条件要 {MIN_CRITERIA}–{MAX_CRITERIA} 条，现在给了"
            f"{len(criteria) if isinstance(criteria, list) else '非列表'}——"
            "少了说不清「做到什么算够」，多了等于没有标准"
        )
    normalized_criteria: list[dict[str, Any]] = []
    for index, item in enumerate(criteria, start=1):
        if not isinstance(item, dict):
            raise ContractError(f"第 {index} 条验收条件不是对象")
        normalized_criteria.append({
            # 客户端给的 id 也收（完整快照回放用），但重复一律拒绝——
            # 不接受自造的重复或跨契约 id（方案 §6.2）
            "id": str(item.get("id") or "").strip() or None,
            "text": _clean_text(item.get("text"), f"第 {index} 条验收条件的内容"),
            "required": bool(item.get("required", True)),
        })
    contract["acceptance_criteria"] = _assign_ids(normalized_criteria, "oc")

    requirements = data.get("evidence_requirements")
    if not isinstance(requirements, list) or not (MIN_REQUIREMENTS <= len(requirements) <= MAX_REQUIREMENTS):
        raise ContractError(
            f"证据要求要 {MIN_REQUIREMENTS}–{MAX_REQUIREMENTS} 条，现在给了"
            f"{len(requirements) if isinstance(requirements, list) else '非列表'}"
        )
    normalized_requirements: list[dict[str, Any]] = []
    for index, item in enumerate(requirements, start=1):
        if not isinstance(item, dict):
            raise ContractError(f"第 {index} 条证据要求不是对象")
        kind = str(item.get("kind") or "").strip()
        if kind not in EVIDENCE_KINDS:
            raise ContractError(
                f"第 {index} 条证据要求的类型「{kind}」不在允许范围"
                f"（{' / '.join(EVIDENCE_KINDS)}）"
            )
        normalized_requirements.append({
            "id": str(item.get("id") or "").strip() or None,
            "kind": kind,
            "required": bool(item.get("required", True)),
            "description": str(item.get("description") or "").strip(),
        })
    contract["evidence_requirements"] = _assign_ids(normalized_requirements, "ev")

    # 约束与停止条件可选：给了就逐条留非空字符串，空列表当「没写」
    for field, label in (("constraints", "约束"), ("stop_conditions", "停止条件")):
        raw = data.get(field)
        if raw is None:
            contract[field] = None
            continue
        if not isinstance(raw, list):
            raise ContractError(f"{label}要是一个字符串数组")
        entries = [str(item).strip() for item in raw if str(item or "").strip()]
        contract[field] = json.dumps(entries, ensure_ascii=False) if entries else None

    contract["source_candidate_id"] = (
        int(data["source_candidate_id"]) if data.get("source_candidate_id") is not None else None
    )
    return contract


def public(row: sqlite3.Row) -> dict[str, Any]:
    """契约行 → 响应形状：JSON 列解出来，正文与状态原样。"""
    def _loads(text: Any, fallback: Any) -> Any:
        try:
            return json.loads(text) if text is not None else fallback
        except (TypeError, ValueError):
            return fallback

    return {
        "id": int(row["id"]),
        "plan_id": int(row["plan_id"]),
        "version": int(row["version"]),
        "source_kind": row["source_kind"],
        "title": row["title"],
        "outcome": row["outcome"],
        "value": row["value"],
        "success_statement": row["success_statement"],
        "acceptance_criteria": _loads(row["acceptance_criteria"], []),
        "evidence_requirements": _loads(row["evidence_requirements"], []),
        "constraints": _loads(row["constraints"], None),
        "stop_conditions": _loads(row["stop_conditions"], None),
        "source_candidate_id": row["source_candidate_id"],
        "status": row["status"],
        "created_at": row["created_at"],
        "activated_at": row["activated_at"],
        "superseded_at": row["superseded_at"],
    }


# ---------- 读取（只读） ----------

def active(conn: sqlite3.Connection, plan_id: int) -> sqlite3.Row | None:
    """这个计划当前有效的契约；没有就返回 None（不猜、不造）。"""
    return conn.execute(
        "SELECT * FROM outcome_contract WHERE plan_id = ? AND status = 'active'"
        " ORDER BY version DESC LIMIT 1",
        (plan_id,),
    ).fetchone()


def get(conn: sqlite3.Connection, contract_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM outcome_contract WHERE id = ?", (contract_id,)
    ).fetchone()


def history(conn: sqlite3.Connection, plan_id: int) -> list[dict[str, Any]]:
    """全部版本，新的在前——历史只读，翻版不改写旧行。"""
    rows = conn.execute(
        "SELECT * FROM outcome_contract WHERE plan_id = ? ORDER BY version DESC, id DESC",
        (plan_id,),
    ).fetchall()
    return [public(row) for row in rows]


def next_version(conn: sqlite3.Connection, plan_id: int) -> int:
    row = conn.execute(
        "SELECT MAX(version) AS top FROM outcome_contract WHERE plan_id = ?", (plan_id,)
    ).fetchone()
    return int(row["top"] or 0) + 1


# ---------- 版本差异（确定性，不调模型） ----------

def _acceptance_fingerprint(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """验收条件的比对口径：id / text / required 全都要一样才算没变。"""
    return [
        {"id": item["id"], "text": item["text"], "required": item["required"]}
        for item in payload["acceptance_criteria"]
    ]


def _requirement_fingerprint(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """必需证据要求的比对口径：id / kind / required；描述是解释性文字，不触发失效。"""
    return [
        {"id": item["id"], "kind": item["kind"], "required": item["required"]}
        for item in payload["evidence_requirements"]
        if item["required"]
    ]


def affects(old_row: sqlite3.Row, new_payload: dict[str, Any]) -> bool:
    """新契约相对旧版本是否**影响验收**：验收条件或必需证据要求变了才影响；
    仅标题、价值说明等解释性文字变化不影响（方案 §4.4.3 / §4.4.5）。

    确定性比较：同样的两份契约，谁算都一样——不调模型，不留主观空间。
    """
    old_payload = public(old_row)
    return (
        _acceptance_fingerprint(old_payload) != _acceptance_fingerprint(new_payload)
        or _requirement_fingerprint(old_payload) != _requirement_fingerprint(new_payload)
    )


def affects_stage(
    old_row: sqlite3.Row,
    new_payload: dict[str, Any],
    criterion_ids: set[str],
) -> bool:
    """契约变化是否影响**某一个阶段**的当前验收（方案 §4.4.3）。

    与 `affects` 的区别：只看这个阶段承接的那几条契约条件（`criterion_ids`），
    别的阶段的条件改了不牵连它；契约级**必需**证据要求变化对所有阶段一视同仁
    （它是同一份验收口径）。所以「A 承接 oc-1、B 承接 oc-2，只改 oc-1」时
    只有 A 被判受影响，B 继续保留当前 accepted。
    """
    old_payload = public(old_row)
    ids = {str(item) for item in criterion_ids}

    def _stage_slice(payload: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"id": item["id"], "text": item["text"], "required": item["required"]}
            for item in payload["acceptance_criteria"]
            if str(item["id"]) in ids
        ]

    return (
        _stage_slice(old_payload) != _stage_slice(new_payload)
        or _requirement_fingerprint(old_payload) != _requirement_fingerprint(new_payload)
    )


# ---------- 唯一写入口：激活 ----------

def activate(
    conn: sqlite3.Connection,
    plan_id: int,
    data: dict[str, Any],
    *,
    source_kind: str = "blueprint",
    reason: str = "",
    actor: str = "user",
) -> dict[str, Any]:
    """激活一份契约：第一个版本直接落 active；已有 active 就先取代再落新版本。

    **调用方负责包 `db.atomic`**：契约激活、（批准建树时的）阶段建立、planning session
    关闭要在同一事务里完成（方案 §5.4），本函数只写自己的部分、不提前 commit。
    台账留两条流水：旧版本的 `supersede_contract` 与新版本的 `create_contract`，
    另外在计划上记一条 `contract_activate`（双模式标记的改前改后）。

    返回 `{"id", "version", "superseded_id", "reviews_invalidated"}`。
    """
    if source_kind not in ("blueprint", "manual", "migrated"):
        raise ContractError(f"未知的契约来源：{source_kind}")
    payload = validate(data)

    plan_row = conn.execute("SELECT * FROM plan WHERE id = ?", (plan_id,)).fetchone()
    if plan_row is None:
        raise ContractError(f"计划 id={plan_id} 不存在")
    if plan_row["status"] != "active":
        raise ContractConflict(
            f"计划 id={plan_id} 已不是进行中（{plan_row['status']}），不能激活契约"
        )

    timestamp = now_iso()
    old = active(conn, plan_id)
    superseded_id: int | None = None
    if old is not None:
        superseded_id = int(old["id"])
        conn.execute(
            "UPDATE outcome_contract SET status = 'superseded', superseded_at = ? WHERE id = ?",
            (timestamp, superseded_id),
        )
        ledger.log_event(
            conn, "outcome_contract", superseded_id, "supersede_contract",
            old["title"], payload["title"],
            str(reason or "").strip() or f"契约升级到第 {next_version(conn, plan_id)} 版",
            actor,
        )

    version = next_version(conn, plan_id)
    cursor = conn.execute(
        """INSERT INTO outcome_contract
           (plan_id, version, source_kind, title, outcome, value, success_statement,
            acceptance_criteria, evidence_requirements, constraints, stop_conditions,
            source_candidate_id, status, created_at, activated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
        (
            plan_id, version, source_kind, payload["title"], payload["outcome"],
            payload["value"], payload["success_statement"],
            json.dumps(payload["acceptance_criteria"], ensure_ascii=False),
            json.dumps(payload["evidence_requirements"], ensure_ascii=False),
            payload["constraints"], payload["stop_conditions"],
            payload["source_candidate_id"], timestamp, timestamp,
        ),
    )
    contract_id = int(cursor.lastrowid)
    ledger.log_event(
        conn, "outcome_contract", contract_id, "create_contract", None, payload["title"],
        str(reason or "").strip() or f"成果契约第 {version} 版激活", actor,
    )

    # 计划的双模式标记：契约激活即成果流程（flow_version=2 / outcome / ready）。
    # 改前改后进台账——「这个计划什么时候、为什么变成成果流程」要能回答。
    before = {
        "flow_version": plan_row["flow_version"],
        "completion_mode": plan_row["completion_mode"],
        "contract_review_status": plan_row["contract_review_status"],
    }
    after = {"flow_version": 2, "completion_mode": "outcome", "contract_review_status": "ready"}
    conn.execute(
        "UPDATE plan SET flow_version = 2, completion_mode = 'outcome',"
        " contract_review_status = 'ready' WHERE id = ?",
        (plan_id,),
    )
    ledger.log_event(
        conn, "plan", plan_id, "contract_activate",
        json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False),
        str(reason or "").strip() or f"成果契约第 {version} 版激活", actor,
    )

    # 契约变化影响验收时，按旧版本判的当前验收全部失效（历史行保留，只翻当前性）。
    # 延迟导入：plan.py 在模块头 import 本模块，这里不能反过来在模块头 import 它。
    invalidated = 0
    if old is not None and affects(old, payload):
        from . import plan  # noqa: PLC0415 —— 打破循环导入，见上
        invalidated = plan.invalidate_affected_reviews(conn, plan_id, old, payload)

    return {
        "id": contract_id,
        "version": version,
        "superseded_id": superseded_id,
        "reviews_invalidated": invalidated,
    }
