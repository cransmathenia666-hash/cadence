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


def make_plan_with_tasks(conn, task_titles=("任务 1", "任务 2")):
    """建一个计划：一个阶段 + 若干任务。返回 (plan_id, stage_id, [任务 id])。"""
    plan_id, stage_id, _ = make_plan(conn, checkpoint_titles=())
    tasks = [
        ledger.create_active(
            conn, "plan_node",
            {"plan_id": plan_id, "parent_id": stage_id, "level": "task",
             "title": title, "sort_order": index},
            actor="user")
        for index, title in enumerate(task_titles)
    ]
    return plan_id, stage_id, tasks


def settle_stage(conn, plan_id, stage_id, tasks=(), url="https://example.com/repo"):
    """按 2026-09-17 起的新判定把阶段做完成：现有任务全部打勾 + 交付物已提交。

    提交交付物那一步会触发推进提案（阶段就此完成）。
    """
    for task_id in tasks:
        plan.check_task(conn, task_id)
    plan.submit_deliverable(conn, stage_id, url, "交付说明")


def test_stage_completion_needs_every_task_settled(conn):
    _, stage_id, tasks = make_plan_with_tasks(conn)
    plan.check_task(conn, tasks[0])

    result = plan.stage_completion(conn, stage_id)

    assert result["complete"] is False
    assert result["total"] == 2
    assert result["settled"] == 1
    assert result["all_tasks_settled"] is False
    assert result["open_titles"] == ["任务 2"]


def test_stage_completion_counts_skipped_as_settled(conn):
    """跳过是你裁定过的结果，不该把阶段永远卡在那里。"""
    _, stage_id, tasks = make_plan_with_tasks(conn)
    plan.check_task(conn, tasks[0])
    plan.skip_task(conn, tasks[1], reason="这条不需要了")

    result = plan.stage_completion(conn, stage_id)
    assert result["complete"] is True
    assert result["all_tasks_settled"] is True


def test_checkpoints_do_not_participate_in_completion(conn):
    """周打卡退居节奏职能：它做没做完，都不影响阶段完成判定。"""
    plan_id, stage_id, _ = make_plan_with_tasks(conn, task_titles=())
    checkpoint_id = plan.add_node(conn, plan_id, "checkpoint", "本周打卡", parent_id=stage_id)
    plan.submit_report(conn, checkpoint_id, "done", "这周做了不少")

    assert plan.stage_completion(conn, stage_id)["all_tasks_settled"] is True  # 无任务 = 天然满足
    assert plan.stage_finished(conn, plan.get_node(conn, stage_id)) is False  # 交付物还没交

