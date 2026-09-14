"""台账单测。

只测确定性逻辑。LLM 相关的输出质量不在这里测（见 SPEC 第 15 节）。
"""

from __future__ import annotations

import pytest

from app import db, ledger


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "test.db"
    db.init(path)
    connection = db.connect(path)
    yield connection
    connection.close()


def test_supersede_keeps_old_value_in_history(conn):
    """取代之后旧值不可见，但仍然留在流水里——这是台账存在的理由。"""
    old_id = ledger.create_active(
        conn, "profile_item",
        {"category": "long_axis", "content": "旧主线：先把 Python 学完"}, actor="user")

    new_id = ledger.supersede(
        conn, "profile_item", old_id,
        {"content": "新主线：通用工程基础 + 能上线的项目"},
        reason="方向调整", actor="user")

    assert [row["content"] for row in ledger.fetch_active(conn, "profile_item")] == [
        "新主线：通用工程基础 + 能上线的项目"
    ]

    old = conn.execute(
        "SELECT status, superseded_by, category FROM profile_item WHERE id = ?", (old_id,)
    ).fetchone()
    assert old["status"] == "superseded"
    assert old["superseded_by"] == new_id
    assert old["category"] == "long_axis"  # 未显式改动的字段被继承

    events = [e for e in ledger.history(conn, "profile_item", old_id) if e["change_type"] == "supersede"]
    assert len(events) == 1
    assert events[0]["before_value"] == "旧主线：先把 Python 学完"
    assert events[0]["after_value"] == "新主线：通用工程基础 + 能上线的项目"
    assert events[0]["reason"] == "方向调整"


def test_fetch_active_can_filter_by_category(conn):
    ledger.create_active(conn, "profile_item", {"category": "long_axis", "content": "主线"}, actor="user")
    ledger.create_active(conn, "profile_item", {"category": "habit", "content": "23:30 睡"}, actor="user")

    assert [r["content"] for r in ledger.fetch_active(conn, "profile_item", category="habit")] == ["23:30 睡"]


def test_void_records_reason_and_hides_value(conn):
    item_id = ledger.create_active(
        conn, "profile_item", {"category": "life_log", "content": "选了某门课"}, actor="user")

    ledger.void(conn, "profile_item", item_id, reason="退课了", actor="user")

    assert ledger.fetch_active(conn, "profile_item") == []
    events = ledger.history(conn, "profile_item", item_id)
    assert [e["change_type"] for e in events] == ["create", "void"]
    assert events[-1]["reason"] == "退课了"


def test_blank_value_is_rejected(conn):
    with pytest.raises(ledger.LedgerError):
        ledger.create_active(conn, "profile_item", {"category": "habit", "content": "   "}, actor="user")


def test_supersede_requires_reason(conn):
    item_id = ledger.create_active(
        conn, "profile_item", {"category": "habit", "content": "旧作息"}, actor="user")

    with pytest.raises(ledger.LedgerError):
        ledger.supersede(conn, "profile_item", item_id, {"content": "新作息"}, reason="  ", actor="user")


def test_superseding_twice_raises_and_leaves_data_intact(conn):
    """重复取代要明确报错，且不能留下半截数据。"""
    first = ledger.create_active(
        conn, "profile_item", {"category": "habit", "content": "第一版"}, actor="user")
    ledger.supersede(conn, "profile_item", first, {"content": "第二版"}, reason="改一次", actor="user")

    with pytest.raises(ledger.LedgerError):
        ledger.supersede(conn, "profile_item", first, {"content": "第三版"}, reason="再改", actor="user")

    assert [r["content"] for r in ledger.fetch_active(conn, "profile_item")] == ["第二版"]
    assert len(ledger.history(conn, "profile_item", first)) == 2  # create + 一次 supersede


def test_void_on_missing_row_raises(conn):
    with pytest.raises(ledger.LedgerError):
        ledger.void(conn, "profile_item", 9999, reason="不存在", actor="user")


def test_void_requires_reason(conn):
    item_id = ledger.create_active(
        conn, "profile_item", {"category": "habit", "content": "旧状态"}, actor="user")

    with pytest.raises(ledger.LedgerError):
        ledger.void(conn, "profile_item", item_id, reason="", actor="user")


def test_set_status_is_idempotent(conn):
    node_id = ledger.create_active(
        conn, "plan_node",
        {"plan_id": 1, "level": "checkpoint", "title": "接上 SQLite 读写"}, actor="user")

    assert ledger.set_status(conn, "plan_node", node_id, "in_progress", reason="开做") == "in_progress"
    ledger.set_status(conn, "plan_node", node_id, "in_progress", reason="重复设置")  # 同状态被忽略

    assert [e["change_type"] for e in ledger.history(conn, "plan_node", node_id)] == [
        "create", "status_change"
    ]


def test_unknown_entity_type_raises(conn):
    with pytest.raises(ledger.LedgerError):
        ledger.create_active(conn, "没注册的类型", {"content": "x"}, actor="user")
