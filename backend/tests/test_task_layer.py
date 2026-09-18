"""任务层与交付物验收（T23，SPEC 决策 30–32）。

覆盖四组：
① 阶段完成判定的组合矩阵（有/无任务 × 交付物已交/未交）；
② 打勾与跳过——只对任务有效，跳过必填理由，都走台账留痕；
③ 交付物提交——独立动作、可重提交、旧值留痕、只对阶段有效；
④ 落后量只吃带日期的任务；`plan_tree` 的展示形状（任务与周打卡分两组）。
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException

from app import db, ledger, plan
from app.main import (
    DeliverableIn,
    NodeFieldsIn,
    NodeIn,
    TaskSkipIn,
    post_deliverable,
    post_node_fields,
    post_task_check,
    post_task_skip,
)

TODAY = date(2026, 9, 17)


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "test.db"
    db.init(path)
    connection = db.connect(path)
    yield connection
    connection.close()


def make_plan(conn, due_date: str | None = None):
    """一个计划 + 一个阶段（可选到期日）。返回 (plan_id, stage_id)。"""
    plan_id = ledger.create_active(conn, "plan", {"goal": "测试计划"}, actor="user")
    stage_id = plan.add_node(conn, plan_id, "stage", "阶段 1", due_date=due_date)
    return plan_id, stage_id


def add_task(conn, plan_id, stage_id, title="任务 1", due_date=None):
    return plan.add_node(conn, plan_id, "task", title, parent_id=stage_id, due_date=due_date)


# ---------- ① 完成判定的组合矩阵 ----------

def test_finished_matrix(conn):
    plan_id, stage_id = make_plan(conn)

    # 无任务 + 无交付物 → 还没完成
    assert plan.stage_finished(conn, plan.get_node(conn, stage_id)) is False

    # 无任务 + 有交付物 → 完成（空集天然满足）
    plan.submit_deliverable(conn, stage_id, "https://example.com/a", "空阶段也交东西")
    assert plan.stage_finished(conn, plan.get_node(conn, stage_id)) is True

    # 有任务未完成 + 有交付物 → 不完成
    _, stage_two = make_plan(conn)
    task_id = add_task(conn, plan_id, stage_two, "任务 A")
    plan.submit_deliverable(conn, stage_two, "https://example.com/b", "先交一半")
    assert plan.stage_finished(conn, plan.get_node(conn, stage_two)) is False

    # 任务全完成 + 无交付物 → 不完成
    _, stage_three = make_plan(conn)
    task_b = add_task(conn, plan_id, stage_three, "任务 B")
    plan.check_task(conn, task_b)
    assert plan.stage_finished(conn, plan.get_node(conn, stage_three)) is False

    # 任务全完成 + 有交付物 → 完成
    plan.submit_deliverable(conn, stage_three, "https://example.com/c", "都齐了")
    assert plan.stage_finished(conn, plan.get_node(conn, stage_three)) is True
    assert task_id is not None


# ---------- ② 打勾与跳过 ----------

def test_check_task_only_for_tasks(conn):
    plan_id, stage_id = make_plan(conn)
    task_id = add_task(conn, plan_id, stage_id)

    result = plan.check_task(conn, task_id)

    assert result["node_status"] == "done"
    row = plan.get_node(conn, task_id)
    assert row["status"] == "done"
    events = ledger.history(conn, "plan_node", task_id)
    assert events[-1]["change_type"] == "status_change"
    assert events[-1]["reason"] == "打勾完成"

    # 只对任务有效：阶段与周打卡都不行
    with pytest.raises(plan.PlanError):
        plan.check_task(conn, stage_id)
    checkpoint_id = plan.add_node(conn, plan_id, "checkpoint", "本周打卡", parent_id=stage_id)
    with pytest.raises(plan.PlanError):
        plan.check_task(conn, checkpoint_id)


def test_skip_task_needs_reason_and_leaves_trace(conn):
    plan_id, stage_id = make_plan(conn)
    task_id = add_task(conn, plan_id, stage_id)

    with pytest.raises(plan.PlanError):
        plan.skip_task(conn, task_id, reason="   ")
    assert plan.get_node(conn, task_id)["status"] == "not_started"  # 没动它

    result = plan.skip_task(conn, task_id, reason="先做别的，这条这轮不要了")

    assert result["node_status"] == "skipped"
    assert ledger.history(conn, "plan_node", task_id)[-1]["reason"] == "先做别的，这条这轮不要了"
    assert plan.stage_completion(conn, stage_id)["all_tasks_settled"] is True  # 跳过算收尾


def test_skip_as_completed_unblocks_the_stage(conn):
    """跳过算完成的一种：任务全跳过 + 交付物提交 = 阶段完成。"""
    plan_id, stage_id = make_plan(conn)
    first = add_task(conn, plan_id, stage_id, "任务 A")
    second = add_task(conn, plan_id, stage_id, "任务 B")
    plan.check_task(conn, first)
    plan.skip_task(conn, second, reason="这条不需要了")

    plan.submit_deliverable(conn, stage_id, "https://example.com/repo", "做完了")

    assert plan.stage_finished(conn, plan.get_node(conn, stage_id)) is True


# ---------- ③ 交付物提交 ----------

def test_deliverable_resubmit_keeps_history(conn):
    _, stage_id = make_plan(conn)

    first = plan.submit_deliverable(conn, stage_id, "https://example.com/v1", "第一版")
    second = plan.submit_deliverable(conn, stage_id, "https://example.com/v2", "改进版")

    rows = conn.execute(
        "SELECT url, note FROM deliverable_submission WHERE node_id = ? ORDER BY id", (stage_id,)
    ).fetchall()
    assert [row["url"] for row in rows] == ["https://example.com/v1", "https://example.com/v2"]
    current = plan.deliverable_submission(conn, stage_id)
    assert current["url"] == second["url"]  # 「当前交付物」= 最新那一行
    events = [e for e in ledger.history(conn, "plan_node", stage_id)
              if e["change_type"] == "deliverable_submit"]
    assert [e["after_value"] for e in events] == ["https://example.com/v1", "https://example.com/v2"]
    assert first["submission_id"] != second["submission_id"]


def test_deliverable_validation(conn):
    plan_id, stage_id = make_plan(conn)
    task_id = add_task(conn, plan_id, stage_id)

    with pytest.raises(plan.PlanError):
        plan.submit_deliverable(conn, stage_id, "   ", "有说明")
    with pytest.raises(plan.PlanError):
        plan.submit_deliverable(conn, stage_id, "https://example.com", "  ")
    with pytest.raises(plan.PlanError):
        plan.submit_deliverable(conn, task_id, "https://example.com", "任务不能交交付物")  # 只对阶段


# ---------- ④ 落后量与展示形状 ----------

def test_only_tasks_with_due_date_enter_lag(conn):
    plan_id, stage_id = make_plan(conn)
    add_task(conn, plan_id, stage_id, "带日期的任务", due_date="2026-09-10")  # 已过期
    add_task(conn, plan_id, stage_id, "不带日期的任务")

    lag = plan.plan_lag(conn, plan_id, TODAY)

    assert lag["behind"] is True
    assert lag["worst"]["title"] == "带日期的任务"
    assert lag["lag_days"] == 7


def test_plan_tree_separates_tasks_and_checkpoints(conn):
    plan_id, stage_id = make_plan(conn)
    task_id = add_task(conn, plan_id, stage_id, "看完第 3 章")
    checkpoint_id = plan.add_node(conn, plan_id, "checkpoint", "本周打卡", parent_id=stage_id)
    plan.submit_deliverable(conn, stage_id, "https://example.com/repo", "交付说明")

    tree = plan.plan_tree(conn, plan_id=plan_id, today=TODAY)
    stage = tree["stages"][0]

    assert [node["id"] for node in stage["tasks"]] == [task_id]
    assert [node["id"] for node in stage["checkpoints"]] == [checkpoint_id]
    assert stage["deliverable_submission"]["url"] == "https://example.com/repo"
    assert stage["finished"] is False  # 任务还没打勾
    assert stage["progress"]["total"] == 1  # 进度只数任务


# ---------- 路由层：状态码分流 ----------

def test_routes_translate_errors(conn):
    plan_id, stage_id = make_plan(conn)
    task_id = add_task(conn, plan_id, stage_id)

    assert post_task_check(task_id, conn)["node_status"] == "done"
    with pytest.raises(HTTPException) as not_found:
        post_task_check(999, conn)
    assert not_found.value.status_code == 404
    with pytest.raises(HTTPException) as wrong_level:
        post_task_check(stage_id, conn)
    assert wrong_level.value.status_code == 400
    with pytest.raises(HTTPException) as no_reason:
        post_task_skip(task_id, TaskSkipIn(reason="  "), conn)
    assert no_reason.value.status_code == 400
    created = post_deliverable(stage_id, DeliverableIn(url="https://example.com/x", note="说明"), conn)
    assert created["url"] == "https://example.com/x"
    with pytest.raises(HTTPException) as missing:
        post_deliverable(999, DeliverableIn(url="https://example.com/x", note="说明"), conn)
    assert missing.value.status_code == 404


def test_node_in_accepts_task_level():
    payload = NodeIn(plan_id=1, level="task", title="看完第 3 章", parent_id=2)
    assert payload.level == "task"


# ---------- T30：改一个已经建好的节点（原地改 + 台账流水） ----------
#
# 这是 2026-09-18 用户拍板补上的写入口：在此之前只有「建节点」与「改状态」，
# 建好之后交付物写不进去、截止日挪不动。口径是「原地改 + 一条流水」，**id 不变**。

def test_editing_a_stage_deliverable_keeps_the_id_and_the_history(conn):
    """T26 那条限制由此解除：同名阶段复用之后，「要交的东西」现在写得进去了。"""
    plan_id, stage_id = make_plan(conn)

    result = plan.update_node_fields(
        conn, stage_id, deliverable="写一个能跑通的 CRUD 接口", reason="复用阶段后补交付物"
    )

    assert result["changed"] == ["deliverable"]
    assert result["before"] == {"deliverable": None}
    assert result["after"] == {"deliverable": "写一个能跑通的 CRUD 接口"}
    node = plan.get_node(conn, stage_id)
    assert node["id"] == stage_id  # id 一个没动
    assert node["deliverable"] == "写一个能跑通的 CRUD 接口"
    events = ledger.history(conn, "plan_node", stage_id)
    assert [event["change_type"] for event in events] == ["create", "update_fields"]
    assert events[-1]["reason"] == "复用阶段后补交付物"
    assert events[-1]["before_value"] == '{"deliverable": null}'
    assert events[-1]["after_value"] == '{"deliverable": "写一个能跑通的 CRUD 接口"}'


def test_editing_keeps_reports_pointing_at_the_node(conn):
    """id 不变的意义：报告仍指得到它（台账「取代」会把这些引用全断掉）。"""
    plan_id, stage_id = make_plan(conn)
    task_id = add_task(conn, plan_id, stage_id)
    plan.submit_report(conn, task_id, status="partial", note="写到一半")

    plan.update_node_fields(conn, task_id, title="读 MDN 的「请求与响应」一节", reason="把任务写具体")

    assert plan.get_node(conn, task_id)["title"] == "读 MDN 的「请求与响应」一节"
    reports = conn.execute("SELECT node_id FROM report WHERE id = 1").fetchone()
    assert reports["node_id"] == task_id  # 报告仍指着这个 id


def test_moving_a_due_date_moves_the_lag(conn):
    plan_id, stage_id = make_plan(conn)
    task_id = add_task(conn, plan_id, stage_id, due_date="2026-09-10")

    assert plan.node_lag_days(conn, plan.get_node(conn, task_id), TODAY) == 7

    plan.update_node_fields(conn, task_id, due_date="2026-09-20", reason="这周有事，往后挪")

    # 还没到期就是 0（不是 None——None 只在「没定日期」或「已跳过」时）
    assert plan.node_lag_days(conn, plan.get_node(conn, task_id), TODAY) == 0


def test_clearing_a_due_date_takes_it_out_of_the_lag(conn):
    plan_id, stage_id = make_plan(conn)
    task_id = add_task(conn, plan_id, stage_id, due_date="2026-09-10")

    plan.update_node_fields(conn, task_id, due_date="", reason="这条不设期了，别催我")

    assert plan.get_node(conn, task_id)["due_date"] is None


def test_editing_needs_a_reason_and_a_real_change(conn):
    plan_id, stage_id = make_plan(conn)

    with pytest.raises(plan.PlanError):
        plan.update_node_fields(conn, stage_id, deliverable="随便写点什么", reason="  ")
    with pytest.raises(plan.PlanError):  # 一个字段都没给
        plan.update_node_fields(conn, stage_id, reason="就是改一下")
    with pytest.raises(plan.PlanError):  # 给了但一模一样
        plan.update_node_fields(conn, stage_id, title="阶段 1", reason="改一下")
    assert len(ledger.history(conn, "plan_node", stage_id)) == 1  # 只有 create，没写噪音流水


def test_editing_a_title_still_goes_through_the_duplicate_gate(conn):
    """「改」不能成为绕过防重复闸的后门。"""
    plan_id, stage_id = make_plan(conn)
    task_id = add_task(conn, plan_id, stage_id, title="读 MDN")
    add_task(conn, plan_id, stage_id, title="另一个任务")

    with pytest.raises(plan.DuplicateNode):
        plan.update_node_fields(conn, task_id, title="另一个任务", reason="重命名")

    assert plan.get_node(conn, task_id)["title"] == "读 MDN"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"title": "  "},                    # 标题不许清成空
        {"deliverable": "任务也给个交付物"},  # 交付物只属于阶段
        {"due_date": "下周三"},              # 日期格式不合法
    ],
)
def test_refused_edits(conn, kwargs):
    plan_id, stage_id = make_plan(conn)
    task_id = add_task(conn, plan_id, stage_id)

    with pytest.raises(plan.PlanError):
        plan.update_node_fields(conn, task_id, reason="试试", **kwargs)


def test_editing_via_the_route_translates_errors(conn):
    """接口层只做翻译：不存在 404、规则拒绝 400。"""
    plan_id, stage_id = make_plan(conn)

    with pytest.raises(HTTPException) as missing:
        post_node_fields(999, NodeFieldsIn(title="新的", reason="改个名"), conn)
    assert missing.value.status_code == 404

    with pytest.raises(HTTPException) as refused:
        post_node_fields(stage_id, NodeFieldsIn(title="阶段 1", reason="改了等于没改"), conn)
    assert refused.value.status_code == 400

    done = post_node_fields(
        stage_id, NodeFieldsIn(deliverable="一份能看的对照表", reason="把交付物写具体"), conn
    )
    assert done["changed"] == ["deliverable"]
