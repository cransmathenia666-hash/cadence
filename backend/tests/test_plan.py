"""计划节点状态机单测（T4）。

只测确定性逻辑：合法/非法迁移、阶段完成时的提案产出。
时间相关的判定在 test_progress.py（T5/T6）。
"""

from __future__ import annotations

import json

import pytest

from app import db, ledger, plan

# 合法迁移表：与 app/plan.py 的 LEGAL_TRANSITIONS 同源，这里逐条覆盖，
# 目的是把「哪些能迁、哪些不能」写成可执行的规格。
LEGAL_PAIRS = [
    ("not_started", "in_progress"),
    ("not_started", "done"),
    ("not_started", "stuck"),
    ("not_started", "skipped"),
    ("in_progress", "done"),
    ("in_progress", "stuck"),
    ("in_progress", "skipped"),
    ("stuck", "in_progress"),
    ("stuck", "done"),
    ("stuck", "skipped"),
    ("done", "in_progress"),
    ("skipped", "in_progress"),
    ("skipped", "done"),
]

ILLEGAL_PAIRS = [
    ("done", "skipped"),      # 已完成不能再改判为跳过
    ("done", "stuck"),        # 已完成谈不上卡住
    ("done", "not_started"),  # 不能退回未开始
    ("in_progress", "not_started"),
    ("skipped", "not_started"),
    ("stuck", "not_started"),
]


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "test.db"
    db.init(path)
    connection = db.connect(path)
    yield connection
    connection.close()


def make_plan(conn, checkpoint_titles=("检查点 1", "检查点 2")):
    """建一个计划：一个阶段 + 若干检查点。返回 (plan_id, stage_id, [检查点 id])。"""
    plan_id = ledger.create_active(conn, "plan", {"goal": "Web 后端最小集"}, actor="user")
    stage_id = ledger.create_active(
        conn, "plan_node",
        {"plan_id": plan_id, "level": "stage", "title": "阶段 2",
         "deliverable": "接口能读写，数据落 SQLite", "sort_order": 10},
        actor="user")
    checkpoints = [
        ledger.create_active(
            conn, "plan_node",
            {"plan_id": plan_id, "parent_id": stage_id, "level": "checkpoint",
             "title": title, "sort_order": index},
            actor="user")
        for index, title in enumerate(checkpoint_titles)
    ]
    return plan_id, stage_id, checkpoints


def node_status(conn, node_id):
    return conn.execute("SELECT status FROM plan_node WHERE id = ?", (node_id,)).fetchone()["status"]


def set_up_as(conn, node_id, status):
    """把节点置为某个状态——用合法迁移走，避免测试里直接 UPDATE 绕过状态机。"""
    plan.transition_node(conn, node_id, status, reason="准备测试前置状态")


@pytest.mark.parametrize("before,after", LEGAL_PAIRS)
def test_legal_transition_is_applied(conn, before, after):
    _, _, checkpoints = make_plan(conn)
    node_id = checkpoints[0]
    set_up_as(conn, node_id, before)

    assert plan.transition_node(conn, node_id, after, reason="测试迁移") == after
    assert node_status(conn, node_id) == after


@pytest.mark.parametrize("before,after", ILLEGAL_PAIRS)
def test_illegal_transition_raises_and_changes_nothing(conn, before, after):
    _, _, checkpoints = make_plan(conn)
    node_id = checkpoints[0]
    set_up_as(conn, node_id, before)

    with pytest.raises(plan.PlanError):
        plan.transition_node(conn, node_id, after, reason="试图非法迁移")

    assert node_status(conn, node_id) == before
    # 非法迁移不能留下流水：只应有「建节点 + 前置状态那一次」
    assert [e["change_type"] for e in ledger.history(conn, "plan_node", node_id)] == [
        "create", "status_change"
    ]


def test_unknown_status_raises(conn):
    _, _, checkpoints = make_plan(conn)

    with pytest.raises(plan.PlanError):
        plan.transition_node(conn, checkpoints[0], "差不多完成了", reason="乱写状态")


def test_transition_requires_reason(conn):
    _, _, checkpoints = make_plan(conn)

    with pytest.raises(plan.PlanError):
        plan.transition_node(conn, checkpoints[0], "in_progress", reason="   ")


def test_transition_on_missing_node_raises(conn):
    make_plan(conn)

    with pytest.raises(plan.PlanError):
        plan.transition_node(conn, 9999, "in_progress", reason="不存在的节点")


def test_same_status_transition_is_noop(conn):
    """重复设置同一状态不报错，也不写噪音流水。"""
    _, _, checkpoints = make_plan(conn)
    plan.transition_node(conn, checkpoints[0], "in_progress", reason="开做")

    assert plan.transition_node(conn, checkpoints[0], "in_progress", reason="又点了一次") == "in_progress"
    assert [e["change_type"] for e in ledger.history(conn, "plan_node", checkpoints[0])] == [
        "create", "status_change"
    ]


def test_transition_leaves_before_after_in_ledger(conn):
    _, _, checkpoints = make_plan(conn)

    plan.transition_node(conn, checkpoints[0], "done", reason="写完了")

    events = [e for e in ledger.history(conn, "plan_node", checkpoints[0]) if e["change_type"] == "status_change"]
    assert len(events) == 1
    assert events[0]["before_value"] == "not_started"
    assert events[0]["after_value"] == "done"
    assert events[0]["reason"] == "写完了"


def test_stage_completion_needs_every_checkpoint_settled(conn):
    _, stage_id, checkpoints = make_plan(conn)
    plan.transition_node(conn, checkpoints[0], "done", reason="做完第一个")

    result = plan.stage_completion(conn, stage_id)

    assert result["complete"] is False
    assert result["total"] == 2
    assert result["settled"] == 1
    assert result["open_titles"] == ["检查点 2"]


def test_stage_completion_counts_skipped_as_settled(conn):
    """跳过是你裁定过的结果，不该把阶段永远卡在那里。"""
    _, stage_id, checkpoints = make_plan(conn)
    plan.transition_node(conn, checkpoints[0], "done", reason="做完")
    plan.transition_node(conn, checkpoints[1], "skipped", reason="这条不需要了")

    assert plan.stage_completion(conn, stage_id)["complete"] is True


def test_no_advance_proposal_while_checkpoint_open(conn):
    _, stage_id, checkpoints = make_plan(conn)
    plan.transition_node(conn, checkpoints[0], "done", reason="做完第一个")

    assert plan.maybe_stage_advance_proposal(conn, stage_id) is None
    assert ledger.fetch_active(conn, "proposal") == []


def test_advance_proposal_points_to_next_stage(conn):
    plan_id, stage_id, checkpoints = make_plan(conn)
    next_stage = ledger.create_active(
        conn, "plan_node",
        {"plan_id": plan_id, "level": "stage", "title": "阶段 3", "deliverable": "公网能访问",
         "sort_order": 20},
        actor="user")
    for node_id in checkpoints:
        plan.transition_node(conn, node_id, "done", reason="做完了")

    proposal_id = plan.maybe_stage_advance_proposal(conn, stage_id)

    assert proposal_id is not None
    row = conn.execute("SELECT kind, payload, status FROM proposal WHERE id = ?", (proposal_id,)).fetchone()
    assert row["kind"] == "stage_advance"
    assert row["status"] == "pending"
    payload = json.loads(row["payload"])
    assert payload["stage_id"] == stage_id
    assert payload["next_stage_id"] == next_stage
    assert "阶段 3" in payload["question"]


def test_advance_proposal_is_not_duplicated(conn):
    """同一阶段重复触发只产出一条待裁定提案。"""
    _, stage_id, checkpoints = make_plan(conn)
    for node_id in checkpoints:
        plan.transition_node(conn, node_id, "done", reason="做完了")

    first = plan.maybe_stage_advance_proposal(conn, stage_id)
    second = plan.maybe_stage_advance_proposal(conn, stage_id)

    assert first is not None
    assert second is None
    assert len(ledger.fetch_active(conn, "proposal")) == 1


def test_advance_proposal_when_no_next_stage(conn):
    """最后一个阶段做完了也要问一句，不能默默结束。"""
    _, stage_id, checkpoints = make_plan(conn)
    for node_id in checkpoints:
        plan.transition_node(conn, node_id, "done", reason="做完了")

    proposal_id = plan.maybe_stage_advance_proposal(conn, stage_id)

    payload = json.loads(
        conn.execute("SELECT payload FROM proposal WHERE id = ?", (proposal_id,)).fetchone()["payload"])
    assert payload["next_stage_id"] is None
    assert "收尾" in payload["question"]
