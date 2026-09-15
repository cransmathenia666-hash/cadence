"""报告推进、落后量、周检查点判定的单测（T5 / T6）。

时间一律用显式传入的固定值（`today=` / `at=`），不读系统时钟——
否则测试会随日期漂移，周二绿、周五红。
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from app import db, ledger, plan

TODAY = date(2026, 9, 20)


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "test.db"
    db.init(path)
    connection = db.connect(path)
    yield connection
    connection.close()


def make_plan(conn, checkpoints=(("检查点 1", None), ("检查点 2", None))):
    """建一个阶段 + 若干检查点。checkpoints 是 (标题, 计划完成日) 的序列。"""
    plan_id = ledger.create_active(conn, "plan", {"goal": "Web 后端最小集"}, actor="user")
    stage_id = ledger.create_active(
        conn, "plan_node",
        {"plan_id": plan_id, "level": "stage", "title": "阶段 2",
         "deliverable": "接口能读写，数据落 SQLite", "sort_order": 10},
        actor="user")
    checkpoint_ids = [
        ledger.create_active(
            conn, "plan_node",
            {"plan_id": plan_id, "parent_id": stage_id, "level": "checkpoint",
             "title": title, "due_date": due_date, "sort_order": index},
            actor="user")
        for index, (title, due_date) in enumerate(checkpoints)
    ]
    return plan_id, stage_id, checkpoint_ids


def node_status(conn, node_id):
    return conn.execute("SELECT status FROM plan_node WHERE id = ?", (node_id,)).fetchone()["status"]


# ---------- T5：报告推进 ----------

@pytest.mark.parametrize(
    "report_status,expected_node_status",
    [("done", "done"), ("partial", "in_progress"), ("stuck", "stuck"), ("skipped", "skipped")],
)
def test_report_status_maps_to_node_status(conn, report_status, expected_node_status):
    _, _, checkpoints = make_plan(conn)

    result = plan.submit_report(conn, checkpoints[0], report_status, note="一句话说明")

    assert result["node_status"] == expected_node_status
    assert node_status(conn, checkpoints[0]) == expected_node_status


def test_report_leaves_both_report_row_and_ledger_flow(conn):
    """成功标准 1：报告落库，且台账留下 before/after。"""
    _, _, checkpoints = make_plan(conn)

    result = plan.submit_report(
        conn, checkpoints[0], "done", note="路由骨架跑通",
        artifact_url="https://example.com/repo", material_feedback="官方文档够用")

    row = conn.execute("SELECT * FROM report WHERE id = ?", (result["report_id"],)).fetchone()
    assert row["node_id"] == checkpoints[0]
    assert row["status"] == "done"
    assert row["note"] == "路由骨架跑通"
    assert row["artifact_url"] == "https://example.com/repo"
    assert row["material_feedback"] == "官方文档够用"

    events = ledger.history(conn, "plan_node", checkpoints[0])
    assert [e["change_type"] for e in events] == ["create", "status_change"]
    assert events[-1]["before_value"] == "not_started"
    assert events[-1]["after_value"] == "done"

    report_events = ledger.history(conn, "report", result["report_id"])
    assert [e["change_type"] for e in report_events] == ["create"]


def test_report_requires_note(conn):
    _, _, checkpoints = make_plan(conn)

    with pytest.raises(plan.PlanError):
        plan.submit_report(conn, checkpoints[0], "done", note="   ")

    assert node_status(conn, checkpoints[0]) == "not_started"
    assert conn.execute("SELECT COUNT(*) FROM report").fetchone()[0] == 0


def test_report_with_unknown_status_raises(conn):
    _, _, checkpoints = make_plan(conn)

    with pytest.raises(plan.PlanError):
        plan.submit_report(conn, checkpoints[0], "差不多完成", note="乱写状态")


def test_report_on_missing_node_raises(conn):
    make_plan(conn)

    with pytest.raises(plan.PlanError):
        plan.submit_report(conn, 9999, "done", note="不存在的节点")


def test_report_is_saved_even_when_node_status_does_not_change(conn):
    """已经是进行中，再报一次「部分完成」：状态不变，但报告必须留下来。"""
    _, _, checkpoints = make_plan(conn)
    plan.submit_report(conn, checkpoints[0], "partial", note="开做了")

    result = plan.submit_report(conn, checkpoints[0], "partial", note="又推进了一点")

    assert result["node_status_before"] == "in_progress"
    assert conn.execute("SELECT COUNT(*) FROM report").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM ledger_event WHERE entity_type = 'report'").fetchone()[0] == 2


def test_report_can_reopen_a_done_node(conn):
    """报错了可以纠正：已完成节点收到「部分完成」会重新打开，并在台账留痕。"""
    _, _, checkpoints = make_plan(conn)
    plan.submit_report(conn, checkpoints[0], "done", note="以为做完了")

    plan.submit_report(conn, checkpoints[0], "partial", note="其实还差测试")

    assert node_status(conn, checkpoints[0]) == "in_progress"
    events = [e for e in ledger.history(conn, "plan_node", checkpoints[0]) if e["change_type"] == "status_change"]
    assert [(e["before_value"], e["after_value"]) for e in events] == [
        ("not_started", "done"), ("done", "in_progress")
    ]


def test_report_on_last_checkpoint_produces_advance_proposal(conn):
    """成功标准 1 后半：最后一条检查点完成时，产出「是否进入下一阶段」提案。"""
    _, _, checkpoints = make_plan(conn, checkpoints=(("检查点 1", None),))

    result = plan.submit_report(conn, checkpoints[0], "done", note="做完了")

    assert result["proposal_id"] is not None
    proposal = conn.execute(
        "SELECT kind, payload FROM proposal WHERE id = ?", (result["proposal_id"],)).fetchone()
    assert proposal["kind"] == "stage_advance"
    assert json.loads(proposal["payload"])["stage_id"] is not None


def test_report_on_open_stage_produces_no_proposal(conn):
    _, _, checkpoints = make_plan(conn)

    result = plan.submit_report(conn, checkpoints[0], "done", note="只做完一个")

    assert result["proposal_id"] is None
    assert ledger.fetch_active(conn, "proposal") == []


# ---------- T5：落后量 ----------

def test_lag_is_zero_before_due_date(conn):
    _, stage_id, checkpoints = make_plan(conn, checkpoints=(("检查点 1", "2026-09-30"),))

    node = plan.get_node(conn, checkpoints[0])

    assert plan.node_lag_days(conn, node, TODAY) == 0


def test_lag_counts_days_for_unfinished_overdue_node(conn):
    _, _, checkpoints = make_plan(conn, checkpoints=(("检查点 1", "2026-09-13"),))

    node = plan.get_node(conn, checkpoints[0])

    assert plan.node_lag_days(conn, node, TODAY) == 7


def test_lag_of_node_without_due_date_is_unknown(conn):
    _, _, checkpoints = make_plan(conn)

    node = plan.get_node(conn, checkpoints[0])

    assert plan.node_lag_days(conn, node, TODAY) is None


def test_lag_ignores_skipped_node(conn):
    """你裁定跳过的东西不该继续算落后。"""
    _, _, checkpoints = make_plan(conn, checkpoints=(("检查点 1", "2026-09-01"),))
    plan.submit_report(conn, checkpoints[0], "skipped", note="这条不做")

    node = plan.get_node(conn, checkpoints[0])

    assert plan.node_lag_days(conn, node, TODAY) is None


def test_lag_of_done_node_uses_completion_time(conn):
    """完成节点看的是「实际完成日 vs 计划完成日」，落后几天如实记。"""
    _, _, checkpoints = make_plan(conn, checkpoints=(("检查点 1", "2026-09-18"),))
    plan.submit_report(conn, checkpoints[0], "done", note="做完了", at="2026-09-20T21:00:00+08:00")

    node = plan.get_node(conn, checkpoints[0])

    assert plan.node_lag_days(conn, node, TODAY) == 2


def test_lag_of_early_done_node_is_negative(conn):
    """提前完成给负数，别把它说成落后。"""
    _, _, checkpoints = make_plan(conn, checkpoints=(("检查点 1", "2026-09-25"),))
    plan.submit_report(conn, checkpoints[0], "done", note="提前做完", at="2026-09-20T21:00:00+08:00")

    node = plan.get_node(conn, checkpoints[0])

    assert plan.node_lag_days(conn, node, TODAY) == -5


def test_plan_lag_reports_the_worst_unfinished_node(conn):
    plan_id, _, checkpoints = make_plan(
        conn, checkpoints=(("检查点 1", "2026-09-17"), ("检查点 2", "2026-09-10"), ("检查点 3", None)))
    plan.submit_report(conn, checkpoints[0], "done", note="做完了", at="2026-09-18T10:00:00+08:00")

    lag = plan.plan_lag(conn, plan_id, TODAY)

    assert lag["lag_days"] == 10
    assert lag["behind"] is True
    assert lag["worst"]["title"] == "检查点 2"


def test_plan_lag_ignores_settled_nodes(conn):
    """已经做完的节点即使当时晚了，也不该让整个计划一直挂着「落后」。"""
    plan_id, _, checkpoints = make_plan(conn, checkpoints=(("检查点 1", "2026-09-10"),))
    plan.submit_report(conn, checkpoints[0], "done", note="晚了两周才做完", at="2026-09-20T10:00:00+08:00")

    lag = plan.plan_lag(conn, plan_id, TODAY)

    assert lag["lag_days"] == 0
    assert lag["behind"] is False
    # 但节点级仍看得到「这次晚了 10 天」
    assert plan.node_lag_days(conn, plan.get_node(conn, checkpoints[0]), TODAY) == 10


def test_plan_lag_is_zero_when_nothing_overdue(conn):
    plan_id, _, _ = make_plan(conn, checkpoints=(("检查点 1", "2026-09-30"),))

    lag = plan.plan_lag(conn, plan_id, TODAY)

    assert lag["lag_days"] == 0
    assert lag["behind"] is False
    assert lag["worst"] is None


# ---------- T5：GET /api/plan 的数据形状 ----------

def test_plan_tree_returns_stages_checkpoints_and_current_stage(conn):
    plan_id, stage_id, checkpoints = make_plan(
        conn, checkpoints=(("检查点 1", "2026-09-13"), ("检查点 2", None)))
    plan.submit_report(conn, checkpoints[0], "done", note="做完了", at="2026-09-13T10:00:00+08:00")

    tree = plan.plan_tree(conn, plan_id=plan_id, today=TODAY)

    assert tree["plan"]["goal"] == "Web 后端最小集"
    assert tree["current_stage"]["id"] == stage_id
    assert tree["current_stage"]["title"] == "阶段 2"
    assert [cp["title"] for cp in tree["stages"][0]["checkpoints"]] == ["检查点 1", "检查点 2"]
    assert tree["stages"][0]["checkpoints"][0]["status"] == "done"
    assert tree["stages"][0]["checkpoints"][1]["status"] == "not_started"
    assert tree["lag"]["lag_days"] == 0


def test_plan_tree_without_any_plan_returns_none(conn):
    tree = plan.plan_tree(conn, today=TODAY)

    assert tree["plan"] is None
    assert tree["stages"] == []
    assert tree["current_stage"] is None


def test_current_stage_skips_settled_stages(conn):
    plan_id = ledger.create_active(conn, "plan", {"goal": "目标"}, actor="user")
    first = ledger.create_active(
        conn, "plan_node",
        {"plan_id": plan_id, "level": "stage", "title": "阶段 1", "sort_order": 10}, actor="user")
    second = ledger.create_active(
        conn, "plan_node",
        {"plan_id": plan_id, "level": "stage", "title": "阶段 2", "sort_order": 20}, actor="user")
    done_checkpoint = ledger.create_active(
        conn, "plan_node",
        {"plan_id": plan_id, "parent_id": first, "level": "checkpoint", "title": "检查点 1"},
        actor="user")
    plan.submit_report(conn, done_checkpoint, "done", note="做完了")

    tree = plan.plan_tree(conn, plan_id=plan_id, today=TODAY)

    assert tree["current_stage"]["id"] == second
