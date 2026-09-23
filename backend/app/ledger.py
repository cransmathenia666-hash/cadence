"""状态台账：全系统唯一的状态写入口。

为什么必须唯一：台账存在的意义是能回答「上周为什么这么定」。
如果各处直接 UPDATE 业务表，历史就散了，这个问题永远回答不了。
所以任何状态变更都要经过这里，由它同时写业务表和 ledger_event。

长期档案的旧值只标记 superseded，不删除；作废只标记 void。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from .db import now_iso


class LedgerError(RuntimeError):
    """台账层面的明确错误：对象不存在、状态不允许、缺理由等。

    一律明确报错而不是静默忽略——静默会让台账出现「看起来成功、其实没写」的假象。
    """


@dataclass(frozen=True)
class EntitySpec:
    table: str
    value_column: str | None
    status_column: str = "status"
    active_status: str = "active"
    # 台账的「作废 / 取代」是否适用于这类记录。
    # 带业务状态机的实体为 False：它那一列装的是业务状态本身，
    # 「不再算数」应该由它自己的业务终态表达，台账不许往里写生命周期值。
    supports_lifecycle: bool = True


# 表名只来自这张固定注册表、不接受外部输入，因此下面拼接的表名是安全的。
#
# supports_lifecycle 这条分界线（方案 B）的判据是「它有没有一个表达否决的业务终态」：
# 节点有 skipped、候选和提案有 rejected，计划没有（closed 是「做完了」，不是「否决了」）。
# 对前四者，台账写进去的 superseded / void 会被业务查询当成「还没收尾」，
# 造出「已作废却仍占着当前阶段 / 仍挡着同名重建」的幽灵记录；对计划则没有这个问题，
# 因为计划的读取一律经 fetch_active，被取代的计划自然消失。
SPECS: dict[str, EntitySpec] = {
    "profile_item": EntitySpec(table="profile_item", value_column="content"),
    # 计划内记忆（2026-09-21 记忆系统）：与 profile_item 同一种东西——有「当前有效值」
    # 这个概念、也有「旧的说法不再算数」这个动作，所以它走台账的取代 / 作废。
    # 判据仍是上面那条：它没有表达否决的业务终态，只能由台账的 superseded / void 表达。
    "plan_memory": EntitySpec(table="plan_memory", value_column="content"),
    "candidate": EntitySpec(
        table="candidate", value_column="title", active_status="proposed",
        supports_lifecycle=False,
    ),
    "proposal": EntitySpec(
        table="proposal", value_column="payload", active_status="pending",
        supports_lifecycle=False,
    ),
    "plan": EntitySpec(table="plan", value_column="goal"),
    "plan_node": EntitySpec(
        table="plan_node", value_column="title", active_status="not_started",
        supports_lifecycle=False,
    ),
}

# 被禁掉生命周期操作时，告诉你「那该用什么」——只报错不给替代方案等于把人堵死。
_LIFECYCLE_HINT: dict[str, str] = {
    "plan_node": "节点用 skipped 表达「这件事不算数」",
    "candidate": "候选用 rejected 表达否决",
    "proposal": "提案用 rejected 表达否决",
}


def _reject_lifecycle(entity_type: str, action: str) -> LedgerError:
    hint = _LIFECYCLE_HINT.get(entity_type, "改用它自己的业务终态")
    return LedgerError(
        f"{entity_type} 不支持{action}：它那一列装的是业务状态，"
        f"写进 superseded / void 会造出「已作废却仍算未收尾」的幽灵记录；{hint}"
    )


def _spec(entity_type: str) -> EntitySpec:
    try:
        return SPECS[entity_type]
    except KeyError:
        raise LedgerError(f"未注册的对象类型：{entity_type}") from None


def _row(conn: sqlite3.Connection, spec: EntitySpec, entity_id: int) -> sqlite3.Row | None:
    return conn.execute(f"SELECT * FROM {spec.table} WHERE id = ?", (entity_id,)).fetchone()


def _value_of(spec: EntitySpec, source: Any) -> str | None:
    if not spec.value_column:
        return None
    try:
        value = source[spec.value_column]
    except (KeyError, IndexError):
        return None
    return None if value is None else str(value)


def _insert(conn: sqlite3.Connection, spec: EntitySpec, payload: dict[str, Any]) -> int:
    columns = ", ".join(payload)
    marks = ", ".join("?" for _ in payload)
    cursor = conn.execute(
        f"INSERT INTO {spec.table} ({columns}) VALUES ({marks})", tuple(payload.values())
    )
    return int(cursor.lastrowid)


def log_event(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: int,
    change_type: str,
    before_value: str | None = None,
    after_value: str | None = None,
    reason: str | None = None,
    actor: str = "user",
) -> None:
    """只写流水，不提交。提交交给调用方，保证业务写入与流水落在同一次事务里。"""
    conn.execute(
        """INSERT INTO ledger_event
           (entity_type, entity_id, change_type, before_value, after_value, reason, actor, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (entity_type, entity_id, change_type, before_value, after_value, reason, actor, now_iso()),
    )


def create_active(
    conn: sqlite3.Connection,
    entity_type: str,
    values: dict[str, Any],
    actor: str = "user",
    reason: str | None = None,
) -> int:
    """新建一条当前有效记录，并在台账留痕。"""
    spec = _spec(entity_type)
    payload = dict(values)
    if spec.value_column and not str(payload.get(spec.value_column) or "").strip():
        raise LedgerError(f"{entity_type}.{spec.value_column} 不能为空")

    timestamp = now_iso()
    payload.setdefault(spec.status_column, spec.active_status)
    payload.setdefault("valid_from", timestamp)
    payload.setdefault("created_at", timestamp)

    new_id = _insert(conn, spec, payload)
    log_event(conn, entity_type, new_id, "create", None, _value_of(spec, payload), reason, actor)
    conn.commit()
    return new_id


def supersede(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: int,
    new_values: dict[str, Any],
    reason: str,
    actor: str = "user",
) -> int:
    """用新值取代旧值：旧行标记 superseded，新行成为当前有效值。"""
    spec = _spec(entity_type)
    if not spec.supports_lifecycle:
        raise _reject_lifecycle(entity_type, "取代")
    old = _row(conn, spec, entity_id)
    if old is None:
        raise LedgerError(f"{entity_type} id={entity_id} 不存在")
    current_status = old[spec.status_column]
    if current_status != spec.active_status:
        raise LedgerError(
            f"只能取代处于 {spec.active_status} 的记录，id={entity_id} 当前状态是 {current_status}"
        )
    if not str(reason or "").strip():
        raise LedgerError("取代必须写明理由，否则台账回答不了「为什么改」")

    # 以旧行为底，套上新值：这样没有显式改动的字段（如 category）会被继承。
    merged = {key: old[key] for key in old.keys() if key not in ("id", "superseded_by")}
    merged.update(new_values or {})
    merged[spec.status_column] = spec.active_status
    merged["valid_from"] = now_iso()
    merged["created_at"] = now_iso()
    if spec.value_column and not str(merged.get(spec.value_column) or "").strip():
        raise LedgerError(f"{entity_type}.{spec.value_column} 不能为空")

    new_id = _insert(conn, spec, merged)
    conn.execute(
        f"UPDATE {spec.table} SET {spec.status_column} = 'superseded', superseded_by = ? WHERE id = ?",
        (new_id, entity_id),
    )
    log_event(
        conn, entity_type, entity_id, "supersede",
        _value_of(spec, old), _value_of(spec, merged), reason, actor,
    )
    conn.commit()
    return new_id


def void(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: int,
    reason: str,
    actor: str = "user",
) -> None:
    """作废一条当前有效记录（信息过期、决定被推翻等）。"""
    spec = _spec(entity_type)
    if not spec.supports_lifecycle:
        raise _reject_lifecycle(entity_type, "作废")
    old = _row(conn, spec, entity_id)
    if old is None:
        raise LedgerError(f"{entity_type} id={entity_id} 不存在")
    if old[spec.status_column] != spec.active_status:
        raise LedgerError(
            f"只能作废处于 {spec.active_status} 的记录，id={entity_id} 当前状态是 {old[spec.status_column]}"
        )
    if not str(reason or "").strip():
        raise LedgerError("作废必须写明理由")

    conn.execute(f"UPDATE {spec.table} SET {spec.status_column} = 'void' WHERE id = ?", (entity_id,))
    log_event(conn, entity_type, entity_id, "void", _value_of(spec, old), None, reason, actor)
    conn.commit()


def set_status(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: int,
    new_status: str,
    actor: str = "user",
    reason: str | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """通用状态迁移（如计划节点 not_started -> in_progress）。

    状态机规则由调用方判断，台账只负责写入与留痕。
    同状态重复设置会被忽略——否则台账很快被噪音淹没。

    `extra` 是与这次迁移同属一回事的附加字段（例如否决候选时的 `reject_reason`）：
    并进同一条 UPDATE，免得业务表被改两次、也没机会绕过台账。
    列名与 `fetch_active` 的 filters 同一个信任口径——只来自本仓库内部调用，不接受外部输入。
    """
    spec = _spec(entity_type)
    row = _row(conn, spec, entity_id)
    if row is None:
        raise LedgerError(f"{entity_type} id={entity_id} 不存在")
    before = row[spec.status_column]
    if before == new_status:
        return before

    assignments = [f"{spec.status_column} = ?"]
    params: list[Any] = [new_status]
    for column, value in (extra or {}).items():
        assignments.append(f"{column} = ?")
        params.append(value)
    params.append(entity_id)
    conn.execute(f"UPDATE {spec.table} SET {', '.join(assignments)} WHERE id = ?", tuple(params))
    log_event(conn, entity_type, entity_id, "status_change", before, new_status, reason, actor)
    conn.commit()
    return new_status


def fetch_active(conn: sqlite3.Connection, entity_type: str, **filters: Any) -> list[sqlite3.Row]:
    """取当前有效记录。filters 的列名来自本模块内部调用，不接受外部拼串。"""
    spec = _spec(entity_type)
    sql = f"SELECT * FROM {spec.table} WHERE {spec.status_column} = ?"
    params: list[Any] = [spec.active_status]
    for column, value in filters.items():
        sql += f" AND {column} = ?"
        params.append(value)
    sql += " ORDER BY id"
    return list(conn.execute(sql, tuple(params)).fetchall())


def history(conn: sqlite3.Connection, entity_type: str, entity_id: int) -> list[sqlite3.Row]:
    """某个对象在台账里的全部流水，按发生顺序。"""
    return list(
        conn.execute(
            "SELECT * FROM ledger_event WHERE entity_type = ? AND entity_id = ? ORDER BY id",
            (entity_type, entity_id),
        ).fetchall()
    )
