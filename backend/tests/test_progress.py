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


# ---------- T6：周检查点判定 ----------

TODAY_IS_SUNDAY = date(2026, 9, 20)  # 2026-09-20 是周日，本周 = 09-14 ~ 09-20


def log_weekly_sent(conn, at, kind="weekly_checkpoint"):
    """模拟触达环节已经问过一次（P4 才会真的发信，这里只造记录）。"""
    conn.execute(
        """INSERT INTO notification_log (channel, kind, subject, body, ok, sent_at)
           VALUES ('none', ?, '三问', '', 1, ?)""",
        (kind, at))
    conn.commit()


def test_week_bounds_start_on_monday(conn):
    start, end = plan.week_bounds(TODAY_IS_SUNDAY)

    assert (start, end) == (date(2026, 9, 14), date(2026, 9, 20))
    assert plan.week_key(TODAY_IS_SUNDAY) == "2026-W38"


def test_weekly_not_due_without_plan(conn):
    status = plan.weekly_status(conn, today=TODAY_IS_SUNDAY)

    assert status["due"] is False
    assert status["plan_id"] is None


def test_weekly_due_when_plan_exists(conn):
    plan_id, _, _ = make_plan(conn)

    status = plan.weekly_status(conn, today=TODAY_IS_SUNDAY)

    assert status["due"] is True
    assert status["plan_id"] == plan_id
    assert status["week"] == "2026-W38"


def test_weekly_not_due_when_already_asked_this_week(conn):
    """同一周只问一次——兜底提醒变成每周刷屏就没意义了。"""
    make_plan(conn)
    log_weekly_sent(conn, "2026-09-16T09:00:00+08:00")

    status = plan.weekly_status(conn, today=TODAY_IS_SUNDAY)

    assert status["due"] is False


def test_weekly_due_again_next_week(conn):
    make_plan(conn)
    log_weekly_sent(conn, "2026-09-16T09:00:00+08:00")

    status = plan.weekly_status(conn, today=date(2026, 9, 22))  # 下一周的周二

    assert status["due"] is True
    assert status["week"] == "2026-W39"


def test_weekly_on_track_when_reported_this_week(conn):
    """按时：本周有报告、没有过期未完成的节点。"""
    plan_id, stage_id, checkpoints = make_plan(
        conn, checkpoints=(("检查点 1", "2026-09-30"), ("检查点 2", None)))
    plan.submit_report(conn, checkpoints[0], "done", note="做完了", at="2026-09-16T20:00:00+08:00")

    status = plan.weekly_status(conn, today=TODAY_IS_SUNDAY)

    assert status["due"] is True
    assert status["behind"] is False
    assert status["behind_reason"] is None
    assert [r["note"] for r in status["reports_this_week"]] == ["做完了"]
    assert status["current_stage"]["id"] == stage_id
    assert status["stage_progress"]["done"] == 1


def test_weekly_marks_behind_when_node_overdue(conn):
    """落后：有过期还没做完的节点。"""
    _, stage_id, _ = make_plan(conn, checkpoints=(("检查点 1", "2026-09-13"),))

    status = plan.weekly_status(conn, today=TODAY_IS_SUNDAY)

    assert status["behind"] is True
    assert "落后" in status["behind_reason"]
    assert status["lag"]["lag_days"] == 7


def test_weekly_marks_no_report_when_nothing_this_week(conn):
    """无报告：本周一条都没提，哪怕没有过期节点也算未推进。"""
    make_plan(conn, checkpoints=(("检查点 1", "2026-09-30"),))

    status = plan.weekly_status(conn, today=TODAY_IS_SUNDAY)

    assert status["behind"] is False
    assert status["reports_this_week"] == []
    assert status["pushed"] is False
    assert "没有报告" in status["behind_reason"]


def test_weekly_replan_options_are_the_three_ways(conn):
    """SPEC 第 6 节：未推进时给减量 / 顺延 / 换交付物三条路。"""
    make_plan(conn, checkpoints=(("检查点 1", "2026-09-13"),))

    status = plan.weekly_status(conn, today=TODAY_IS_SUNDAY)

    assert [option["kind"] for option in status["replan_options"]] == [
        "reduce_scope", "postpone", "swap_deliverable"
    ]
    assert all(option["detail"] for option in status["replan_options"])
    assert "检查点 1" in status["replan_options"][0]["detail"]


def test_no_replan_proposal_when_on_track(conn):
    """按时推进就不打扰你。"""
    # 故意留一个未完成的检查点：否则这条计划会先产出阶段推进提案，干扰本测试的断言
    _, _, checkpoints = make_plan(
        conn, checkpoints=(("检查点 1", "2026-09-30"), ("检查点 2", None)))
    plan.submit_report(conn, checkpoints[0], "done", note="做完了", at="2026-09-16T20:00:00+08:00")

    assert plan.ensure_weekly_replan_proposal(conn, today=TODAY_IS_SUNDAY) is None
    assert [row["kind"] for row in ledger.fetch_active(conn, "proposal")] == []


def test_replan_proposal_created_when_behind(conn):
    plan_id, stage_id, _ = make_plan(conn, checkpoints=(("检查点 1", "2026-09-13"),))

    proposal_id = plan.ensure_weekly_replan_proposal(conn, today=TODAY_IS_SUNDAY)

    assert proposal_id is not None
    row = conn.execute(
        "SELECT kind, status, payload FROM proposal WHERE id = ?", (proposal_id,)).fetchone()
    assert row["kind"] == "plan_replan"
    assert row["status"] == "pending"
    payload = json.loads(row["payload"])
    assert payload["week"] == "2026-W38"
    assert payload["plan_id"] == plan_id
    assert payload["stage_id"] == stage_id
    assert payload["lag_days"] == 7
    assert len(payload["options"]) == 3


def test_replan_proposal_created_when_no_report_this_week(conn):
    """没有过期节点，但本周一条报告都没有——那也是未推进。"""
    make_plan(conn, checkpoints=(("检查点 1", "2026-09-30"),))

    proposal_id = plan.ensure_weekly_replan_proposal(conn, today=TODAY_IS_SUNDAY)

    payload = json.loads(
        conn.execute("SELECT payload FROM proposal WHERE id = ?", (proposal_id,)).fetchone()["payload"])
    assert "没有报告" in payload["why"]


def test_replan_proposal_is_not_duplicated_within_the_week(conn):
    make_plan(conn, checkpoints=(("检查点 1", "2026-09-13"),))

    first = plan.ensure_weekly_replan_proposal(conn, today=TODAY_IS_SUNDAY)
    second = plan.ensure_weekly_replan_proposal(conn, today=TODAY_IS_SUNDAY)

    assert first is not None
    assert second is None
    assert len(ledger.fetch_active(conn, "proposal")) == 1


def test_replan_proposal_can_be_created_again_next_week(conn):
    make_plan(conn, checkpoints=(("检查点 1", "2026-09-13"),))

    first = plan.ensure_weekly_replan_proposal(conn, today=TODAY_IS_SUNDAY)
    second = plan.ensure_weekly_replan_proposal(conn, today=date(2026, 9, 27))

    assert first is not None
    assert second is not None


# ---------- 防重复建节点（双击会造重复，见 HANDOFF 候选队列） ----------

def count_nodes(conn, **where):
    sql = "SELECT COUNT(*) FROM plan_node"
    if where:
        sql += " WHERE " + " AND ".join(f"{column} = ?" for column in where)
    return conn.execute(sql, tuple(where.values())).fetchone()[0]


def test_add_node_creates_when_title_is_new(conn):
    """正常路径不能被误伤：标题不重复时照建。"""
    plan_id, stage_id, _ = make_plan(conn, checkpoints=(("检查点 1", None),))

    node_id = plan.add_node(conn, plan_id, "checkpoint", "检查点 2", parent_id=stage_id)

    assert node_id is not None
    assert count_nodes(conn, title="检查点 2") == 1


def test_add_node_rejects_duplicate_open_checkpoint(conn):
    """核心场景：同一阶段下连点两次，只建出一条。"""
    plan_id, stage_id, _ = make_plan(conn, checkpoints=(("检查点 1", None),))

    with pytest.raises(plan.DuplicateNode):
        plan.add_node(conn, plan_id, "checkpoint", "检查点 1", parent_id=stage_id)

    assert count_nodes(conn, parent_id=stage_id) == 1


def test_add_node_treats_surrounding_spaces_as_same_title(conn):
    """标题前后有空格也算同一条，否则" 检查点 1"能绕过去。"""
    plan_id, stage_id, _ = make_plan(conn, checkpoints=(("检查点 1", None),))

    with pytest.raises(plan.DuplicateNode):
        plan.add_node(conn, plan_id, "checkpoint", "  检查点 1  ", parent_id=stage_id)


def test_add_node_rejects_duplicate_stage_title(conn):
    """阶段同样适用：同一个计划下不该有两个还开着的同名阶段。"""
    plan_id, _, _ = make_plan(conn)  # 已有一个阶段「阶段 2」

    with pytest.raises(plan.DuplicateNode):
        plan.add_node(conn, plan_id, "stage", "阶段 2")


def test_same_title_in_another_stage_is_allowed(conn):
    """另一个阶段下的同名检查点不是同一条，必须放行。"""
    plan_id, _, _ = make_plan(conn, checkpoints=(("检查点 1", None),))
    second_stage = plan.add_node(conn, plan_id, "stage", "阶段 3", sort_order=20)

    node_id = plan.add_node(conn, plan_id, "checkpoint", "检查点 1", parent_id=second_stage)

    assert node_id is not None


@pytest.mark.parametrize("existing_status,should_block", [
    ("not_started", True),
    ("in_progress", True),
    ("stuck", True),
    ("done", False),
    ("skipped", False),
])
def test_duplicate_rule_only_blocks_unsettled(conn, existing_status, should_block):
    """已收尾的同名节点不该挡路：那是"上一轮做完了"，重开一条是正当需求。"""
    plan_id, stage_id, checkpoints = make_plan(conn, checkpoints=(("检查点 1", None),))
    plan.transition_node(conn, checkpoints[0], existing_status, reason="准备前置状态")

    if should_block:
        with pytest.raises(plan.DuplicateNode):
            plan.add_node(conn, plan_id, "checkpoint", "检查点 1", parent_id=stage_id)
    else:
        assert plan.add_node(conn, plan_id, "checkpoint", "检查点 1", parent_id=stage_id) is not None


# ---------- 台账生命周期终态的兜底（方案 B）：幽灵记录必须看不见 ----------

def void_row(conn, node_id):
    """绕过台账直接把状态改成 void——模拟方案 B 之前（或被别的程序）写下的历史数据。

    现在台账层已经禁止对节点调 void，所以只能这样造出幽灵来验证兜底有效。
    """
    conn.execute("UPDATE plan_node SET status = 'void' WHERE id = ?", (node_id,))
    conn.commit()


def test_legacy_voided_node_is_invisible(conn):
    """已作废的节点一律当作不存在：取不到、不占位、不算落后。"""
    plan_id, stage_id, checkpoints = make_plan(conn, checkpoints=(("检查点 1", "2026-09-13"),))
    void_row(conn, checkpoints[0])

    assert plan.get_node(conn, checkpoints[0]) is None
    # 关键：幽灵节点不能继续霸着这个标题，否则你再也建不出同名的一条
    assert plan.add_node(conn, plan_id, "checkpoint", "检查点 1", parent_id=stage_id) is not None
    assert plan.plan_lag(conn, plan_id, TODAY)["lag_days"] == 0


def test_legacy_voided_stage_is_not_current_stage(conn):
    """已作废的阶段不能继续当「当前阶段」，否则这条计划永远推不动。"""
    plan_id, stage_id, _ = make_plan(conn)
    void_row(conn, stage_id)

    assert plan.get_stages(conn, plan_id) == []
    assert plan.current_stage(conn, plan_id) is None


def test_legacy_voided_checkpoint_does_not_count_as_settled(conn):
    """作废的检查点要从「阶段进度」的分母里消失，不能靠充数把阶段做完。"""
    _, stage_id, checkpoints = make_plan(conn, checkpoints=(("检查点 1", None), ("检查点 2", None)))
    plan.submit_report(conn, checkpoints[0], "done", note="做完了")
    void_row(conn, checkpoints[1])

    completion = plan.stage_completion(conn, stage_id)

    assert completion["total"] == 1
    assert completion["complete"] is True
