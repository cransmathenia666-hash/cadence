"""复盘卡与报告回流字段（成果闭环 OC-08，方案 §3.8 / §5.3 / §6.4）。

覆盖五组：
① 旧报告行兼容读取：OC-08 之前的旧报告行新列全空，读取按旧报告解释；db.init 连跑两次稳定；
② 六条判定规则逐条命中（继续 / 缩小顺延 / 调整对话 / 补条件 / 补契约 / 停止入口）；
③ 优先级冲突时的取舍：停止 > 补契约 > 继续 > 逾期 > 连续卡住 > 补条件，落选信号留在 facts；
④ 提交报告零模型调用：默认 provider 指向没人监听的端口——任何一次模型调用都会在
   transport 层当场失败（连接拒绝 → 接口 500），再加「调用流水零行」的断言双保险；
⑤ stop 只给入口不 void；无契约计划不宣称完成。

时间一律显式传入（`today=`），不读系统时钟——否则测试会随日期漂移。
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException

from app import db, ledger, llm, plan
from app.main import (
    CriterionIn,
    DeliverableIn,
    EvidenceIn,
    EvidenceRequirementIn,
    NodeIn,
    PlanWithContractIn,
    ReportIn,
    StageReviewIn,
    create_node,
    get_plan_review_card,
    post_deliverable,
    post_plan_with_contract,
    post_report,
    post_stage_evidence,
    post_stage_review,
    post_task_check,
)

TODAY = date(2026, 10, 2)


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "test.db"
    db.init(path)
    connection = db.connect(path)
    yield connection
    connection.close()


# ---------- 公共原料 ----------

def contract_payload(**over) -> dict:
    """一份合格契约：三条必需条件——阶段 1 承接 oc-1/oc-2，阶段 2 承接 oc-3。"""
    data = {
        "title": "做出能演示的 API 项目",
        "outcome": "一个可运行的 CRUD API 仓库",
        "value": "实习作品集需要一个真实项目",
        "success_statement": "能运行、带错误处理与测试、能当面演示",
        "acceptance_criteria": [
            {"text": "能运行一个包含错误处理的 CRUD API", "required": True},
            {"text": "至少三个接口有自动化测试", "required": True},
            {"text": "部署脚本可重复执行", "required": True},
        ],
        "evidence_requirements": [
            {"kind": "repository", "required": True, "description": "可运行的代码仓库"}
        ],
    }
    data.update(over)
    return data


def make_legacy_plan(conn, tasks=(("任务 1", None), ("任务 2", None))):
    """旧流程计划：一个阶段 + 任务。tasks 是 (标题, 计划完成日) 序列。"""
    plan_id = ledger.create_active(conn, "plan", {"goal": "Web 后端最小集"}, actor="user")
    stage_id = int(ledger.create_active(
        conn, "plan_node",
        {"plan_id": plan_id, "level": "stage", "title": "阶段 1",
         "deliverable": "接口能读写", "sort_order": 0},
        actor="user"))
    task_ids = [
        int(ledger.create_active(
            conn, "plan_node",
            {"plan_id": plan_id, "parent_id": stage_id, "level": "task",
             "title": title, "due_date": due, "sort_order": index},
            actor="user"))
        for index, (title, due) in enumerate(tasks)
    ]
    return plan_id, stage_id, task_ids


def make_outcome_plan(conn, *, with_stage2: bool = True, task_due: str | None = None) -> dict:
    """成果计划：契约 + 阶段 1（承接 oc-1/oc-2，两个任务）+ 阶段 2（承接 oc-3，两个任务）。"""
    created = post_plan_with_contract(PlanWithContractIn(contract=contract_payload()), conn)
    plan_id, contract_id = int(created["id"]), int(created["contract"]["id"])
    create_node(NodeIn(
        plan_id=plan_id, level="stage", title="阶段 1",
        purpose="搭出骨架", why_now="其他一切依赖它",
        acceptance_criteria=[CriterionIn(text="CRUD 接口能跑通")],
        evidence_requirements=[EvidenceRequirementIn(kind="repository", description="仓库")],
        contract_criterion_ids=["oc-1", "oc-2"],
    ), conn)
    stage1 = int(plan.get_stages(conn, plan_id)[0]["id"])
    task1 = [
        int(create_node(NodeIn(
            plan_id=plan_id, level="task", title=title,
            parent_id=stage1, due_date=task_due), conn)["id"])
        for title in ("任务 1a", "任务 1b")
    ]
    stage2, task2 = None, []
    if with_stage2:
        create_node(NodeIn(
            plan_id=plan_id, level="stage", title="阶段 2",
            acceptance_criteria=[CriterionIn(text="部署脚本可重复执行")],
            evidence_requirements=[EvidenceRequirementIn(kind="link", description="部署记录")],
            contract_criterion_ids=["oc-3"],
        ), conn)
        stage2 = int(plan.get_stages(conn, plan_id)[1]["id"])
        task2 = [
            int(create_node(NodeIn(
                plan_id=plan_id, level="task", title=title, parent_id=stage2), conn)["id"])
            for title in ("任务 2a", "任务 2b")
        ]
    return {
        "plan_id": plan_id, "contract_id": contract_id,
        "stage1": stage1, "task1": task1, "stage2": stage2, "task2": task2,
    }


def make_outcome_without_contract(conn, *, task_due: str | None = None):
    """成果模式但**没有有效契约行**的计划（直接拼库内状态：契约缺失/被撤后的样子）。"""
    plan_id = int(ledger.create_active(conn, "plan", {
        "goal": "无契约的成果计划",
        "completion_mode": "outcome",
        "contract_review_status": "ready",
    }, actor="user"))
    stage_id = int(ledger.create_active(
        conn, "plan_node",
        {"plan_id": plan_id, "level": "stage", "title": "阶段 1",
         "deliverable": "接口能读写", "sort_order": 0},
        actor="user"))
    task_ids = [int(ledger.create_active(
        conn, "plan_node",
        {"plan_id": plan_id, "parent_id": stage_id, "level": "task",
         "title": "任务 1", "due_date": task_due, "sort_order": 0},
        actor="user"))]
    return plan_id, stage_id, task_ids


def accept_stage(conn, data: dict) -> None:
    """把阶段 1 推到验收达标：任务全打勾 + 交仓库证据 + accepted 验收。"""
    for task_id in data["task1"]:
        post_task_check(task_id, conn)
    evidence = post_stage_evidence(
        data["stage1"],
        EvidenceIn(kind="repository", reference="https://github.com/x/y", note="这份证据说明接口能跑"),
        conn,
    )
    post_stage_review(data["stage1"], StageReviewIn(
        contract_id=data["contract_id"],
        submission_ids=[evidence["id"]],
        criteria_state={
            str(item["id"]): "met"
            for item in plan._stage_criteria_of(plan.get_node(conn, data["stage1"]))
        },
        decision="accepted", note="逐条确认过了",
    ), conn)


def needs_work_stage(conn, data: dict) -> None:
    """给阶段 1 交证据并打一条 needs_work 验收（条件全记 unmet）。"""
    evidence = post_stage_evidence(
        data["stage1"],
        EvidenceIn(kind="repository", reference="https://github.com/x/y", note="先交一版"),
        conn,
    )
    post_stage_review(data["stage1"], StageReviewIn(
        contract_id=data["contract_id"],
        submission_ids=[evidence["id"]],
        criteria_state={
            str(item["id"]): "unmet"
            for item in plan._stage_criteria_of(plan.get_node(conn, data["stage1"]))
        },
        decision="needs_work", note="接口还跑不通",
    ), conn)


def make_provider(conn) -> int:
    """默认 provider 指向没人监听的端口——真有模型调用就会当场连接失败。"""
    provider_id = llm.create_provider(
        conn,
        name="假提供商",
        base_url="http://127.0.0.1:9999/v1",
        api_key="sk-fake-1234567890abcd",
        default_model="fake-model",
    )
    llm.update_provider(conn, provider_id, set_as_default=True)
    return provider_id


# ---------- ① 旧报告行兼容读取 ----------

def test_old_report_row_reads_compatible_and_init_is_idempotent(conn, tmp_path):
    plan_id, stage_id, task_ids = make_legacy_plan(conn)
    # 模拟 OC-08 之前的旧报告行：只写旧列。
    old_id = int(conn.execute(
        "INSERT INTO report (node_id, status, note, artifact_url, material_feedback, created_at)"
        " VALUES (?, 'done', '旧报告', NULL, NULL, '2026-09-20T10:00:00+08:00')",
        (task_ids[0],),
    ).lastrowid)
    conn.commit()

    # db.init 连跑两次：不报错、不重复加列。
    db.init(tmp_path / "test.db")
    db.init(tmp_path / "test.db")
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(report)")]
    assert len(columns) == len(set(columns)) == 11

    # 旧行读出来就是旧报告的形状：新列按空解释，不替它猜。
    public = plan.report_public(conn.execute("SELECT * FROM report WHERE id = ?", (old_id,)).fetchone())
    assert public["status"] == "done" and public["note"] == "旧报告"
    assert public["stage_node_ids"] == [] and public["progressed_criteria"] == []
    assert public["next_action"] is None and public["review_requested"] is False

    # 旧行照常参与复盘卡：不报错，按旧报告解释（没有停止、没有连续卡住）。
    card = plan.review_card(conn, plan_id)
    assert card["facts"]["latest_report"]["id"] == old_id
    assert card["facts"]["latest_report"]["next_action"] is None
    assert card["facts"]["stuck_streak"]["count"] == 0
    assert card["rule"] == "keep_pace"


def test_new_report_fields_roundtrip_and_response_attaches_card(conn):
    plan_id, stage_id, task_ids = make_legacy_plan(conn)
    result = post_report(ReportIn(
        node_id=task_ids[0], status="partial", note="本周推进了接口",
        stage_node_ids=[stage_id], progressed_criteria=["c1", "c2"],
        next_action="defer", review_requested=True,
    ), conn)

    # 旧契约的键一个不少，新键 review_card 附上。
    for key in ("report_id", "node_id", "report_status", "node_status_before",
                "node_status", "proposal_id", "review_card"):
        assert key in result

    row = conn.execute("SELECT * FROM report WHERE id = ?", (result["report_id"],)).fetchone()
    public = plan.report_public(row)
    assert public["stage_node_ids"] == [stage_id]
    assert public["progressed_criteria"] == ["c1", "c2"]
    assert public["next_action"] == "defer" and public["review_requested"] is True


def test_report_without_new_fields_still_works(conn):
    """旧客户端不传新字段照常提交，库中新列落 NULL，复盘卡照给。"""
    plan_id, stage_id, task_ids = make_legacy_plan(conn)
    result = post_report(ReportIn(node_id=task_ids[0], status="done", note="做完了"), conn)

    row = conn.execute("SELECT * FROM report WHERE id = ?", (result["report_id"],)).fetchone()
    assert row["stage_node_ids"] is None and row["progressed_criteria"] is None
    assert row["next_action"] is None and row["review_requested"] == 0
    assert result["review_card"]["rule"] == "keep_pace"


def test_unknown_next_action_is_rejected(conn):
    _, _, task_ids = make_legacy_plan(conn)
    with pytest.raises(plan.PlanError) as caught:
        plan.submit_report(conn, task_ids[0], "partial", "说明", next_action="pause")
    assert "下一步建议" in str(caught.value)


# ---------- ② 六条判定规则逐条命中 ----------

def test_rule_continue_next_stage(conn):
    """当前阶段已验收且存在下一阶段 → 建议继续下一阶段。"""
    data = make_outcome_plan(conn)
    accept_stage(conn, data)

    card = plan.review_card(conn, data["plan_id"])
    assert card["rule"] == "continue_next_stage" and card["action"] == "continue"
    assert "阶段 1" in card["conclusion"] and "阶段 2" in card["conclusion"]
    assert card["facts"]["finished_stage"]["title"] == "阶段 1"
    assert card["facts"]["current_stage"]["title"] == "阶段 2"
    assert card["facts"]["current_stage"]["acceptance_status"] == "pending"


def test_rule_overdue_narrow_or_defer_lists_tightest_first(conn):
    """任务逾期但阶段未验收 → 建议缩小或顺延，最紧节点排最前并写进理由。"""
    plan_id, stage_id, task_ids = make_legacy_plan(
        conn, tasks=(("慢任务", "2026-09-20"), ("更慢任务", "2026-09-10")))

    card = plan.review_card(conn, plan_id, today=TODAY)
    assert card["rule"] == "overdue_narrow_or_defer" and card["action"] == "narrow_or_defer"
    assert "缩小范围或顺延" in card["conclusion"]
    # 最紧 = 落后天数最多的在前，且进了理由。
    assert card["facts"]["overdue_nodes"][0]["title"] == "更慢任务"
    assert "更慢任务" in card["reason"]
    assert len(card["facts"]["overdue_nodes"]) == 2


def test_rule_two_stuck_reports_opens_dialogue(conn):
    """最近连续两次报告 stuck → 建议打开调整对话；插入非 stuck 后连续性归零。"""
    plan_id, stage_id, task_ids = make_legacy_plan(conn)
    plan.submit_report(conn, task_ids[0], "stuck", "卡在环境上")
    plan.submit_report(conn, task_ids[1], "stuck", "换了个方向还是卡")

    card = plan.review_card(conn, plan_id)
    assert card["rule"] == "two_stuck_reports" and card["action"] == "open_dialogue"
    assert card["facts"]["stuck_streak"]["count"] == 2
    assert len(card["facts"]["stuck_streak"]["report_ids"]) == 2

    plan.submit_report(conn, stage_id, "partial", "有进展了")
    card2 = plan.review_card(conn, plan_id)
    assert card2["facts"]["stuck_streak"]["count"] == 0
    assert card2["rule"] == "keep_pace"


def test_rule_complete_criteria_after_needs_work(conn):
    """有证据但验收 needs_work → 建议补齐指定条件，缺口点名到条件 id。"""
    data = make_outcome_plan(conn, with_stage2=False)
    needs_work_stage(conn, data)

    card = plan.review_card(conn, data["plan_id"])
    assert card["rule"] == "complete_criteria" and card["action"] == "complete_criteria"
    assert "needs_work" in card["conclusion"]
    assert card["facts"]["acceptance_gaps"] == [
        {"criterion_id": "c1", "text": "CRUD 接口能跑通", "state": "unmet"}
    ]
    assert "c1" in card["reason"]
    assert card["facts"]["evidence_gaps"] == []       # 仓库证据已交，证据不缺


def test_rule_no_contract_blocks_completion_claims(conn):
    """成果计划没有有效契约 → 提示先补契约，不允许宣称完成。"""
    plan_id, stage_id, task_ids = make_outcome_without_contract(conn)
    plan.submit_report(conn, stage_id, "partial", "推进中")

    card = plan.review_card(conn, plan_id)
    assert card["rule"] == "no_contract" and card["action"] == "add_contract"
    assert "不能宣称完成" in card["conclusion"]
    assert card["facts"]["has_active_contract"] is False


def test_rule_user_stop_gives_entry_and_does_not_void(conn):
    """用户本报告选择停止 → 给停止入口；绝不自动作废或收尾。"""
    plan_id, stage_id, task_ids = make_legacy_plan(conn)
    result = post_report(ReportIn(
        node_id=task_ids[0], status="partial", note="不想做了", next_action="stop"), conn)

    card = result["review_card"]
    assert card["rule"] == "user_stop" and card["action"] == "stop"
    assert "停止" in card["conclusion"]
    assert any(choice["label"] == "停止计划" for choice in card["choices"])
    # 只给入口：计划还是 active，台账里没有 void，也没有收尾。
    assert conn.execute(
        "SELECT status FROM plan WHERE id = ?", (plan_id,)
    ).fetchone()["status"] == "active"
    assert all(event["change_type"] != "void" for event in ledger.history(conn, "plan", plan_id))


def test_read_only_route_reflects_latest_report_choice(conn):
    """只读路由用同一张卡：不传报告也随最近一次报告的选择走；计划不存在回 404。"""
    plan_id, stage_id, task_ids = make_legacy_plan(conn)
    post_report(ReportIn(
        node_id=task_ids[0], status="partial", note="不想做了", next_action="stop"), conn)

    card = get_plan_review_card(plan_id, conn)
    assert card["plan_id"] == plan_id and card["rule"] == "user_stop"

    with pytest.raises(HTTPException) as caught:
        get_plan_review_card(999, conn)
    assert caught.value.status_code == 404


# ---------- ③ 优先级冲突时的取舍 ----------

def test_stop_beats_every_other_signal(conn):
    """逾期 + 连续卡住 + 本报告停止 → 停止入口优先；落选信号留在 facts。"""
    plan_id, stage_id, task_ids = make_legacy_plan(
        conn, tasks=(("任务 1", "2026-09-20"), ("任务 2", None)))
    plan.submit_report(conn, task_ids[0], "stuck", "卡了")
    plan.submit_report(conn, stage_id, "stuck", "整体也卡")
    result = post_report(ReportIn(
        node_id=task_ids[1], status="partial", note="先停一停", next_action="stop"), conn)

    card = result["review_card"]
    assert card["rule"] == "user_stop"
    assert card["facts"]["overdue_nodes"]          # 逾期信号没有丢
    assert card["facts"]["stuck_streak"]["count"] == 0  # 最新一条不是 stuck，连续性如实归零


def test_no_contract_beats_overdue(conn):
    """成果计划缺契约 + 任务逾期 → 先补契约；逾期明细仍在 facts。"""
    plan_id, stage_id, task_ids = make_outcome_without_contract(conn, task_due="2026-09-20")
    plan.submit_report(conn, stage_id, "partial", "推进中")

    card = plan.review_card(conn, plan_id, today=TODAY)
    assert card["rule"] == "no_contract"
    assert card["facts"]["overdue_nodes"][0]["title"] == "任务 1"


def test_continue_next_stage_beats_two_stuck(conn):
    """上一阶段刚验收 + 下一阶段两次卡住报告 → 继续优先；卡住信号留在 facts。"""
    data = make_outcome_plan(conn)
    accept_stage(conn, data)
    plan.submit_report(conn, data["task2"][0], "stuck", "卡了")
    plan.submit_report(conn, data["task2"][1], "stuck", "还卡")

    card = plan.review_card(conn, data["plan_id"])
    assert card["rule"] == "continue_next_stage"
    assert card["facts"]["stuck_streak"]["count"] == 2


def test_overdue_beats_two_stuck_and_needs_work(conn):
    """逾期 + 连续两次卡住 + needs_work 验收 → 取「缩小或顺延」；缺口与卡住仍在 facts。"""
    data = make_outcome_plan(conn, with_stage2=False, task_due="2026-09-20")
    needs_work_stage(conn, data)
    plan.submit_report(conn, data["task1"][0], "stuck", "卡了")
    plan.submit_report(conn, data["task1"][1], "stuck", "还卡")

    card = plan.review_card(conn, data["plan_id"], today=TODAY)
    assert card["rule"] == "overdue_narrow_or_defer"
    assert card["facts"]["stuck_streak"]["count"] == 2
    assert card["facts"]["acceptance_gaps"][0]["state"] == "unmet"


# ---------- ④ 提交报告零模型调用 ----------

def test_submitting_a_report_makes_zero_model_calls(conn):
    """报告与复盘卡全程不碰模型——哪怕用户勾了「希望 AI 提调整建议」也不出手。

    transport 断言：默认 provider 已配置且指向没人监听的端口，任何一次模型调用都会
    在 transport 层当场失败（连接拒绝 → 接口 500，测试直接红）；调用流水零行是第二道保险。
    """
    make_provider(conn)
    plan_id, stage_id, task_ids = make_legacy_plan(conn)
    result = post_report(ReportIn(
        node_id=task_ids[0], status="partial", note="本周推进",
        stage_node_ids=[stage_id], progressed_criteria=["c1"],
        next_action="continue", review_requested=True,
    ), conn)

    assert result["review_card"]["rule"] == "keep_pace"
    assert llm.list_calls(conn) == []
    get_plan_review_card(plan_id, conn)        # 只读路由同样不碰模型
    assert llm.list_calls(conn) == []


# ---------- ⑤ 无契约计划不宣称完成 ----------

def test_legacy_all_settled_without_contract_never_claims_completion(conn):
    """旧流程所有阶段收尾、但没有契约 → 结论明确「不能宣称成果完成」，不给完成入口。"""
    plan_id, stage_id, task_ids = make_legacy_plan(conn)
    post_task_check(task_ids[0], conn)
    post_task_check(task_ids[1], conn)
    post_deliverable(stage_id, DeliverableIn(url="https://x.example.com", note="交付物"), conn)
    plan.submit_report(conn, stage_id, "done", "阶段做完")

    card = plan.review_card(conn, plan_id)
    assert card["rule"] == "no_contract"
    assert "不能宣称成果完成" in card["conclusion"]
    assert card["facts"]["has_active_contract"] is False
    assert all(choice["action"] != "close_completed" for choice in card["choices"])
