"""成果闭环 P1（路由级）：成果契约 · 证据提交 · 阶段验收 · 双模式收尾 · 原子回滚。

方案见 `docs/ideas/成果闭环重构开发方案.md` §3.7 / §5.3 / §6.3 / §6.5 / §9.2，覆盖八组：

① 契约校验：缺字段、条数越界、空文本、重复 id、非法证据类型一律拒绝；
② 完整契约手工建计划（不经 LLM）；旧入口保持 legacy、只标 needs_review、不建空契约；
③ 契约版本读取与激活：旧版本 superseded、历史只读、确定性差异决定验收失效；
④ 证据提交：只增历史记录，不完成阶段；
⑤ 验收门槛：条件全覆盖、证据属本阶段、accepted 要必需条件全 met + 必需证据齐；
⑥ 失效：任务/阶段放回、条件或交付要求变更、契约升级都使受影响当前验收失效，历史保留；
⑦ 收尾：completed 过门槛（否则 409）、stopped 必写理由、不换语义覆盖、legacy 旧语义不变；
⑧ 原子回滚：强制失败时验收/建计划一条都不落半截。

只交链接、只报 done、AI 说可以，都不会让 outcome 阶段完成——这条贯穿 ④–⑦ 反复断言。
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi import HTTPException

from app import contract, db, ledger, plan
from app.main import (
    CriterionIn,
    DeliverableIn,
    EvidenceIn,
    EvidenceRequirementIn,
    NodeFieldsIn,
    NodeIn,
    PlanCloseIn,
    PlanUpgradeIn,
    PlanWithContractIn,
    ReportIn,
    StageReviewIn,
    StageUpgradeIn,
    create_node,
    create_plan,
    get_plan,
    get_plan_contract,
    post_deliverable,
    post_node_fields,
    post_node_reopen,
    post_plan_close,
    post_plan_reopen,
    post_plan_contract,
    post_plan_upgrade,
    post_plan_with_contract,
    post_report,
    post_stage_evidence,
    post_stage_review,
    post_task_check,
    post_task_skip,
)


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "test.db"
    db.init(path)
    connection = db.connect(path)
    yield connection
    connection.close()


# ---------- 公共原料 ----------

def contract_payload(**over) -> dict:
    """一份合格的契约。`over` 里的键直接覆盖，便于造坏数据。"""
    data = {
        "title": "做出能演示的 API 项目",
        "outcome": "一个可运行的 CRUD API 仓库",
        "value": "实习作品集需要一个真实项目",
        "success_statement": "能运行、带错误处理与测试、能当面演示",
        "acceptance_criteria": [
            {"text": "能运行一个包含错误处理的 CRUD API", "required": True},
            {"text": "至少三个接口有自动化测试", "required": True},
        ],
        "evidence_requirements": [
            {"kind": "repository", "required": True, "description": "可运行的代码仓库"}
        ],
    }
    data.update(over)
    return data


def make_outcome_plan(conn) -> tuple[int, int]:
    """成果计划 + 一个带条件与证据要求的阶段（带一件任务）。返回 (plan_id, contract_id)。

    阶段承接**全部**必需契约条件（oc-1 / oc-2）——完成门槛要求每个必需条件都被阶段
    承接并由 accepted 阶段兑现，happy-path 的 fixture 必须先把这一步做满，否则
    `plan_can_complete` 本来就该是 False（那是 `test_completed_close_blocked_*`
    专门覆盖的缺口场景）。
    """
    created = post_plan_with_contract(
        PlanWithContractIn(contract=contract_payload()), conn
    )
    plan_id, contract_id = created["id"], created["contract"]["id"]
    create_node(
        NodeIn(
            plan_id=plan_id, level="stage", title="阶段 1",
            purpose="搭出骨架", why_now="其他一切依赖它",
            acceptance_criteria=[CriterionIn(text="CRUD 接口能跑通")],
            evidence_requirements=[EvidenceRequirementIn(kind="repository", description="仓库")],
            contract_criterion_ids=["oc-1", "oc-2"],
        ),
        conn,
    )
    stage_id = plan.get_stages(conn, plan_id)[0]["id"]
    create_node(NodeIn(plan_id=plan_id, level="task", title="任务 1", parent_id=stage_id), conn)
    return plan_id, contract_id


def stage_row(conn, stage_id: int):
    return plan.get_node(conn, stage_id)


def stage_id_of(conn, plan_id: int) -> int:
    return int(plan.get_stages(conn, plan_id)[0]["id"])


def met_state(conn, stage_id: int) -> dict[str, str]:
    return {
        str(item["id"]): "met" for item in plan._stage_criteria_of(stage_row(conn, stage_id))
    }


def submit_evidence(conn, stage_id: int, kind: str = "repository") -> dict:
    return post_stage_evidence(
        stage_id,
        EvidenceIn(kind=kind, reference="https://github.com/x/y", note="这份证据说明接口能跑"),
        conn,
    )


def accept_review(conn, stage_id: int, contract_id: int, submission_ids: list[int]) -> dict:
    return post_stage_review(
        stage_id,
        StageReviewIn(
            contract_id=contract_id,
            submission_ids=submission_ids,
            criteria_state=met_state(conn, stage_id),
            decision="accepted",
            note="逐条确认过了",
        ),
        conn,
    )


# ---------- ① 契约校验 ----------

def test_valid_contract_gets_stable_ids():
    normalized = contract.validate(contract_payload())

    assert [item["id"] for item in normalized["acceptance_criteria"]] == ["oc-1", "oc-2"]
    assert [item["id"] for item in normalized["evidence_requirements"]] == ["ev-1"]
    assert normalized["acceptance_criteria"][0]["required"] is True


@pytest.mark.parametrize(
    "over",
    [
        {"outcome": "  "},                                  # 缺结果
        {"value": None},                                    # 缺价值
        {"success_statement": ""},                          # 缺「做到什么算够」
        {"acceptance_criteria": [{"text": "只有一条", "required": True}]},   # 条数不足
        {"acceptance_criteria": [                                    # 条数越界
            {"text": f"条件 {i}", "required": True} for i in range(6)
        ]},
        {"acceptance_criteria": [{"text": "  ", "required": True}, {"text": "x"}]},  # 空文本
        {"acceptance_criteria": [                                    # 服务端补齐后撞 id
            {"id": "oc-1", "text": "甲"}, {"id": "oc-1", "text": "乙"},
        ]},
        {"evidence_requirements": [{"kind": "video", "required": True}]},   # 非法证据类型
        {"evidence_requirements": []},                      # 一条证据要求都没有
    ],
)
def test_invalid_contracts_are_rejected(over):
    with pytest.raises(contract.ContractError):
        contract.validate(contract_payload(**over))


# ---------- ② 完整契约手工建计划；旧入口保持 legacy ----------

def test_manual_plan_with_full_contract_is_outcome_from_birth(conn):
    created = post_plan_with_contract(PlanWithContractIn(contract=contract_payload()), conn)

    plan_id = created["id"]
    row = conn.execute("SELECT * FROM plan WHERE id = ?", (plan_id,)).fetchone()
    assert row["completion_mode"] == "outcome"
    assert row["flow_version"] == 2
    assert row["contract_review_status"] == "ready"
    active = contract.active(conn, plan_id)
    assert active is not None and int(active["version"]) == 1
    assert active["source_kind"] == "manual"
    # 不经 LLM：没有任何模型调用记账
    assert conn.execute("SELECT COUNT(*) AS n FROM llm_call").fetchone()["n"] == 0
    # 台账留了契约与计划两条激活痕迹
    types = [event["change_type"] for event in ledger.history(conn, "outcome_contract", int(active["id"]))]
    assert types == ["create_contract"]
    assert any(event["change_type"] == "contract_activate" for event in ledger.history(conn, "plan", plan_id))


def test_manual_plan_goal_defaults_to_contract_title(conn):
    created = post_plan_with_contract(PlanWithContractIn(contract=contract_payload()), conn)
    assert created["goal"] == contract_payload()["title"]


def test_invalid_contract_creates_nothing_at_all(conn):
    # 客户端自造的重复 id：过了 Pydantic（字段类型都对），被业务校验拒绝
    with pytest.raises(HTTPException) as caught:
        post_plan_with_contract(
            PlanWithContractIn(contract=contract_payload(acceptance_criteria=[
                {"id": "oc-1", "text": "甲", "required": True},
                {"id": "oc-1", "text": "乙", "required": True},
            ])),
            conn,
        )
    assert caught.value.status_code == 400
    # 原子回滚：计划行也没落——不存在「没有契约的成果计划」半成品
    assert conn.execute("SELECT COUNT(*) AS n FROM plan").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM outcome_contract").fetchone()["n"] == 0


def test_legacy_entry_stays_legacy_and_only_marks_needs_review(conn):
    created = create_plan(_GoalOnly(goal="旧流程计划"), conn)
    plan_id = created["id"]

    row = conn.execute("SELECT * FROM plan WHERE id = ?", (plan_id,)).fetchone()
    assert row["completion_mode"] == "legacy"
    assert row["flow_version"] == 1
    assert row["contract_review_status"] == "needs_review"
    # 不创建空契约、不猜验收标准
    assert contract.active(conn, plan_id) is None
    assert conn.execute("SELECT COUNT(*) AS n FROM outcome_contract").fetchone()["n"] == 0

    tree = get_plan(plan_id=plan_id, conn=conn)
    assert tree["completion_mode"] == "legacy"
    assert tree["upgrade_required"] is True
    assert tree["contract"] is None
    assert tree["contract_status"] == "needs_review"
    assert tree["can_complete"] is None


class _GoalOnly:
    """最小请求桩：`create_plan` 只读 `payload.goal`。"""

    def __init__(self, goal: str) -> None:
        self.goal = goal


def test_outcome_stage_rejects_unknown_contract_criterion_ids(conn):
    plan_id, _ = make_outcome_plan(conn)
    with pytest.raises(HTTPException) as caught:
        create_node(
            NodeIn(
                plan_id=plan_id, level="stage", title="阶段 2",
                contract_criterion_ids=["oc-99"],
            ),
            conn,
        )
    assert caught.value.status_code == 400


def test_legacy_stage_rejects_contract_criterion_ids(conn):
    created = create_plan(_GoalOnly(goal="旧流程"), conn)
    with pytest.raises(HTTPException) as caught:
        create_node(
            NodeIn(
                plan_id=created["id"], level="stage", title="阶段 1",
                contract_criterion_ids=["oc-1"],
            ),
            conn,
        )
    assert caught.value.status_code == 400


# ---------- ③ 契约版本读取与激活 ----------

class _ActivateIn:
    """契约激活请求桩：直接携带构造好的 OutcomeContractIn。"""

    def __init__(self, payload, reason: str | None = None) -> None:
        self.contract = payload
        self.reason = reason


def _outcome_contract_in(data: dict):
    return PlanWithContractIn(contract=data).contract  # 复用同一套 Pydantic 校验


def test_contract_versions_are_read_and_superseded_without_rewrite(conn):
    plan_id, first_id = make_outcome_plan(conn)

    listed = get_plan_contract(plan_id, conn=conn)
    assert listed["contract"]["id"] == first_id
    assert [item["version"] for item in listed["history"]] == [1]

    # 仅标题/价值说明变化：不影响验收，旧验收不失效
    activated = post_plan_contract(
        plan_id,
        _ActivateIn(_outcome_contract_in(contract_payload(title="新名字", value="更值了")), "改个说法"),
        conn,
    )
    assert activated["reviews_invalidated"] == 0

    old = contract.get(conn, first_id)
    assert old["status"] == "superseded" and old["superseded_at"] is not None
    active = contract.active(conn, plan_id)
    assert int(active["version"]) == 2
    assert len(get_plan_contract(plan_id, conn=conn)["history"]) == 2
    # 正文未被改写：旧版本还是原来的标题
    assert old["title"] == contract_payload()["title"]


def test_contract_upgrade_with_changed_criteria_invalidates_current_review(conn):
    plan_id, contract_id = make_outcome_plan(conn)
    stage_id = stage_id_of(conn, plan_id)
    task_id = int(conn.execute(
        "SELECT id FROM plan_node WHERE parent_id = ?", (stage_id,)
    ).fetchone()["id"])
    post_task_check(task_id, conn)
    evidence = submit_evidence(conn, stage_id)
    accept_review(conn, stage_id, contract_id, [evidence["id"]])
    assert plan.stage_finished(conn, stage_row(conn, stage_id)) is True

    # 必需验收条件变了 → 按旧版判的当前验收必须失效
    payload = contract_payload()
    payload["acceptance_criteria"][1]["text"] = "至少五个接口有自动化测试"
    activated = post_plan_contract(
        plan_id, _ActivateIn(_outcome_contract_in(payload), "标准提高了"), conn,
    )
    assert activated["reviews_invalidated"] == 1

    review = plan.current_stage_review(conn, stage_id)
    assert review is None  # 当前的没了……
    history_rows = conn.execute(
        "SELECT * FROM stage_review WHERE node_id = ?", (stage_id,)
    ).fetchall()
    assert len(history_rows) == 1 and history_rows[0]["is_current"] == 0
    assert history_rows[0]["invalidated_at"] is not None  # ……历史行原样保留、只读
    assert plan.stage_finished(conn, stage_row(conn, stage_id)) is False
    tree = get_plan(plan_id=plan_id, conn=conn)
    assert tree["stages"][0]["acceptance"]["status"] == "invalidated"


def test_contract_activation_refused_on_legacy_or_closed_plans(conn):
    legacy = create_plan(_GoalOnly(goal="旧流程"), conn)
    with pytest.raises(HTTPException) as caught:
        post_plan_contract(
            legacy["id"], _ActivateIn(_outcome_contract_in(contract_payload())), conn,
        )
    assert caught.value.status_code == 400

    plan_id, _ = make_outcome_plan(conn)
    post_plan_close(plan_id, PlanCloseIn(close_kind="stopped", reason="先放着"), conn)
    with pytest.raises(HTTPException) as caught:
        post_plan_contract(
            plan_id, _ActivateIn(_outcome_contract_in(contract_payload())), conn,
        )
    assert caught.value.status_code == 409


# ---------- ④ 证据提交：只增历史，不完成阶段 ----------

def test_submitting_evidence_does_not_finish_the_stage(conn):
    plan_id, _ = make_outcome_plan(conn)
    stage_id = stage_id_of(conn, plan_id)

    result = submit_evidence(conn, stage_id)

    assert result["kind"] == "repository"
    assert stage_row(conn, stage_id)["status"] == "not_started"  # 状态纹丝不动
    assert plan.stage_finished(conn, stage_row(conn, stage_id)) is False
    tree = get_plan(plan_id=plan_id, conn=conn)
    assert tree["stages"][0]["acceptance"]["status"] == "pending"
    assert tree["stages"][0]["finished"] is False
    assert [item["kind"] for item in tree["stages"][0]["acceptance"]["evidence"]] == ["repository"]
    # 台账留痕，但没有 stage_review
    assert any(e["change_type"] == "evidence_submit" for e in ledger.history(conn, "plan_node", stage_id))
    assert conn.execute("SELECT COUNT(*) AS n FROM stage_review").fetchone()["n"] == 0


def test_evidence_rules(conn):
    plan_id, _ = make_outcome_plan(conn)
    stage_id = stage_id_of(conn, plan_id)
    task_id = int(conn.execute(
        "SELECT id FROM plan_node WHERE parent_id = ?", (stage_id,)
    ).fetchone()["id"])

    with pytest.raises(HTTPException) as caught:  # 只对阶段
        post_stage_evidence(task_id, EvidenceIn(kind="link", reference="https://x", note="n"), conn)
    assert caught.value.status_code == 400
    with pytest.raises(HTTPException):  # 要有说明
        post_stage_evidence(stage_id, EvidenceIn(kind="link", reference="https://x", note=" "), conn)
    with pytest.raises(HTTPException):  # 非 text 要有链接
        post_stage_evidence(stage_id, EvidenceIn(kind="link", reference=None, note="n"), conn)
    ok = post_stage_evidence(stage_id, EvidenceIn(kind="text", reference=None, note="纯文字结果"), conn)
    assert ok["kind"] == "text" and ok["reference"] is None


def test_legacy_deliverable_still_works_and_becomes_legacy_evidence(conn):
    plan_id, contract_id = make_outcome_plan(conn)
    stage_id = stage_id_of(conn, plan_id)

    result = post_deliverable(stage_id, DeliverableIn(url="https://old.example.com", note="旧入口"), conn)

    assert result["evidence_id"] > 0
    kinds = [row["kind"] for row in conn.execute(
        "SELECT kind FROM evidence_submission WHERE node_id = ?", (stage_id,)
    ).fetchall()]
    assert kinds == ["legacy"]
    # legacy 证据不满足必需证据要求：光有旧链接不能达标
    with pytest.raises(HTTPException) as caught:
        post_stage_review(
            stage_id,
            StageReviewIn(
                contract_id=contract_id, submission_ids=[result["evidence_id"]],
                criteria_state=met_state(conn, stage_id), decision="accepted", note="有链接了",
            ),
            conn,
        )
    assert caught.value.status_code == 400
    assert "必需证据" in str(caught.value.detail)


def test_done_report_does_not_finish_an_outcome_stage(conn):
    plan_id, _ = make_outcome_plan(conn)
    stage_id = stage_id_of(conn, plan_id)

    post_task_check(int(conn.execute(
        "SELECT id FROM plan_node WHERE parent_id = ?", (stage_id,)
    ).fetchone()["id"]), conn)
    post_deliverable(stage_id, DeliverableIn(url="https://x.example.com", note="链接贴上了"), conn)
    post_report(ReportIn(node_id=stage_id, status="done", note="这阶段做完了"), conn)

    assert stage_row(conn, stage_id)["status"] == "done"  # 报告只推进执行状态……
    assert plan.stage_finished(conn, stage_row(conn, stage_id)) is False  # ……但不是验收
    tree = get_plan(plan_id=plan_id, conn=conn)
    assert tree["stages"][0]["finished"] is False
    assert tree["stages"][0]["acceptance"]["status"] == "pending"


# ---------- ⑤ 阶段验收门槛 ----------

def test_review_only_for_outcome_stages(conn):
    legacy = create_plan(_GoalOnly(goal="旧流程"), conn)
    legacy_stage = create_node(NodeIn(plan_id=legacy["id"], level="stage", title="阶段 1"), conn)
    with pytest.raises(HTTPException) as caught:
        post_stage_review(
            legacy_stage["id"],
            StageReviewIn(contract_id=1, criteria_state={}, decision="needs_work", note="n"),
            conn,
        )
    assert caught.value.status_code == 400

    plan_id, _ = make_outcome_plan(conn)
    task_id = int(conn.execute(
        "SELECT id FROM plan_node WHERE level = 'task' AND plan_id = ?", (plan_id,)
    ).fetchone()["id"])
    with pytest.raises(HTTPException) as caught:  # 任务节点不能验收
        post_stage_review(
            task_id,
            StageReviewIn(contract_id=1, criteria_state={}, decision="needs_work", note="n"),
            conn,
        )
    assert caught.value.status_code == 400


def test_review_requires_current_contract_full_coverage_and_stage_evidence(conn):
    plan_id, contract_id = make_outcome_plan(conn)
    stage_id = stage_id_of(conn, plan_id)
    evidence = submit_evidence(conn, stage_id)

    def review(**over):
        base = dict(
            contract_id=contract_id, submission_ids=[evidence["id"]],
            criteria_state=met_state(conn, stage_id), decision="accepted", note="逐条确认过了",
        )
        base.update(over)
        return post_stage_review(stage_id, StageReviewIn(**base), conn)

    with pytest.raises(HTTPException) as caught:  # 契约版本不对（拿旧版本号来验收）
        review(contract_id=contract_id + 999)
    assert caught.value.status_code == 400
    with pytest.raises(HTTPException) as caught:  # 条件没全覆盖
        review(criteria_state={})
    assert caught.value.status_code == 400
    with pytest.raises(HTTPException) as caught:  # 条件状态取值不合法
        review(criteria_state={key: "maybe" for key in met_state(conn, stage_id)})
    assert caught.value.status_code == 400
    with pytest.raises(HTTPException) as caught:  # 跨阶段证据
        other = make_outcome_plan(conn)
        other_stage = stage_id_of(conn, other[0])
        other_evidence = submit_evidence(conn, other_stage)
        review(submission_ids=[other_evidence["id"]])
    assert caught.value.status_code == 400

    # 必需条件没全 met 不能 accepted
    state = met_state(conn, stage_id)
    first = sorted(state)[0]
    state[first] = "unknown"
    with pytest.raises(HTTPException) as caught:
        post_stage_review(
            stage_id,
            StageReviewIn(contract_id=contract_id, submission_ids=[evidence["id"]],
                          criteria_state=state, decision="accepted", note="差不多吧"),
            conn,
        )
    assert caught.value.status_code == 400
    assert "必需条件" in str(caught.value.detail)


def test_accepted_review_finishes_the_stage_and_plans_completion(conn):
    plan_id, contract_id = make_outcome_plan(conn)
    stage_id = stage_id_of(conn, plan_id)
    task_id = int(conn.execute(
        "SELECT id FROM plan_node WHERE parent_id = ?", (stage_id,)
    ).fetchone()["id"])
    post_task_check(task_id, conn)
    evidence = submit_evidence(conn, stage_id)

    result = accept_review(conn, stage_id, contract_id, [evidence["id"]])

    assert result["decision"] == "accepted"
    assert result["stage_status"] == "done"
    assert plan.stage_finished(conn, stage_row(conn, stage_id)) is True
    assert plan.plan_can_complete(conn, plan_id) is True
    tree = get_plan(plan_id=plan_id, conn=conn)
    assert tree["stages"][0]["acceptance"]["status"] == "accepted"
    assert tree["stages"][0]["finished"] is True
    assert tree["can_complete"] is True
    review = plan.current_stage_review(conn, stage_id)
    assert review is not None and review["is_current"] == 1
    snapshot = json.loads(review["criteria_snapshot"])
    assert snapshot[0]["text"] == "CRUD 接口能跑通"  # 条件快照保存了当时的样子
    assert json.loads(review["submission_snapshot"])[0]["kind"] == "repository"


def test_needs_work_records_the_gap_without_finishing(conn):
    plan_id, contract_id = make_outcome_plan(conn)
    stage_id = stage_id_of(conn, plan_id)
    evidence = submit_evidence(conn, stage_id)

    result = post_stage_review(
        stage_id,
        StageReviewIn(
            contract_id=contract_id, submission_ids=[evidence["id"]],
            criteria_state={key: "unmet" for key in met_state(conn, stage_id)},
            decision="needs_work", note="接口还跑不起来",
        ),
        conn,
    )

    assert result["stage_status"] == "in_progress"
    assert plan.stage_finished(conn, stage_row(conn, stage_id)) is False
    assert get_plan(plan_id=plan_id, conn=conn)["stages"][0]["acceptance"]["status"] == "needs_work"
    assert plan.plan_can_complete(conn, plan_id) is False


def test_duplicate_review_is_idempotent_not_noise(conn):
    plan_id, contract_id = make_outcome_plan(conn)
    stage_id = stage_id_of(conn, plan_id)
    evidence = submit_evidence(conn, stage_id)
    first = accept_review(conn, stage_id, contract_id, [evidence["id"]])

    again = accept_review(conn, stage_id, contract_id, [evidence["id"]])

    assert again["duplicate"] is True and again["id"] == first["id"]
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM stage_review WHERE node_id = ?", (stage_id,)
    ).fetchone()["n"] == 1
    # 说明变了就是新的一次判断：新行落库，旧行失效但保留
    third = post_stage_review(
        stage_id,
        StageReviewIn(
            contract_id=contract_id, submission_ids=[evidence["id"]],
            criteria_state=met_state(conn, stage_id), decision="accepted", note="再看了一遍还是过",
        ),
        conn,
    )
    assert third["duplicate"] is False and third["id"] != first["id"]
    rows = conn.execute(
        "SELECT is_current FROM stage_review WHERE node_id = ? ORDER BY id", (stage_id,)
    ).fetchall()
    assert [row["is_current"] for row in rows] == [0, 1]


def test_accepting_without_any_evidence_is_refused(conn):
    plan_id, contract_id = make_outcome_plan(conn)
    stage_id = stage_id_of(conn, plan_id)

    with pytest.raises(HTTPException) as caught:
        post_stage_review(
            stage_id,
            StageReviewIn(
                contract_id=contract_id, submission_ids=[],
                criteria_state=met_state(conn, stage_id), decision="accepted", note="没交证据",
            ),
            conn,
        )
    assert caught.value.status_code == 400
    assert "证据" in str(caught.value.detail)
    assert conn.execute("SELECT COUNT(*) AS n FROM stage_review").fetchone()["n"] == 0


# ---------- ⑥ 失效：放回 / 跳过 / 条件变更 ----------

def _accepted_stage(conn):
    plan_id, contract_id = make_outcome_plan(conn)
    stage_id = stage_id_of(conn, plan_id)
    task_id = int(conn.execute(
        "SELECT id FROM plan_node WHERE parent_id = ?", (stage_id,)
    ).fetchone()["id"])
    post_task_check(task_id, conn)
    evidence = submit_evidence(conn, stage_id)
    accept_review(conn, stage_id, contract_id, [evidence["id"]])
    return plan_id, stage_id, task_id, evidence["id"]


def test_reopening_a_task_invalidates_the_stage_review(conn):
    plan_id, stage_id, task_id, _ = _accepted_stage(conn)

    result = post_node_reopen(task_id, type("R", (), {"reason": "点错了"})(), conn)

    assert result["invalidated_review_id"] is not None
    assert plan.current_stage_review(conn, stage_id) is None
    assert plan.stage_finished(conn, stage_row(conn, stage_id)) is False
    history_rows = conn.execute(
        "SELECT * FROM stage_review WHERE node_id = ?", (stage_id,)
    ).fetchall()
    assert len(history_rows) == 1 and history_rows[0]["is_current"] == 0  # 历史只读


def test_reopening_a_stage_invalidates_its_review(conn):
    plan_id, stage_id, _, _ = _accepted_stage(conn)

    result = post_node_reopen(stage_id, type("R", (), {"reason": "有新想法"})(), conn)

    assert result["invalidated_review_id"] is not None
    assert plan.stage_finished(conn, stage_row(conn, stage_id)) is False


def test_skipping_a_stage_invalidates_review_and_refuses_new_ones(conn):
    plan_id, contract_id = make_outcome_plan(conn)
    stage_id = stage_id_of(conn, plan_id)
    evidence = submit_evidence(conn, stage_id)
    # 先给一条 needs_work（阶段回到 in_progress，才能被跳过——done 不能直接 skip）
    post_stage_review(
        stage_id,
        StageReviewIn(
            contract_id=contract_id, submission_ids=[evidence["id"]],
            criteria_state={key: "unmet" for key in met_state(conn, stage_id)},
            decision="needs_work", note="还差得远",
        ),
        conn,
    )

    result = post_task_skip(stage_id, type("S", (), {"reason": "这步不做了"})(), conn)

    assert result["invalidated_review_id"] is not None
    assert plan.current_stage_review(conn, stage_id) is None
    assert plan.stage_finished(conn, stage_row(conn, stage_id)) is True  # 跳过视同完成
    with pytest.raises(HTTPException) as caught:  # 跳过是裁定，不再需要验收
        post_stage_review(
            stage_id,
            StageReviewIn(
                contract_id=1, submission_ids=[], criteria_state={}, decision="needs_work", note="n"
            ),
            conn,
        )
    assert caught.value.status_code == 409


def test_changing_stage_criteria_or_deliverable_invalidates_review(conn):
    plan_id, stage_id, _, _ = _accepted_stage(conn)

    # 只改截止日：不影响验收
    post_node_fields(stage_id, NodeFieldsIn(due_date="2026-10-15", reason="挪个日期"), conn)
    assert plan.current_stage_review(conn, stage_id) is not None

    # 换交付物：当前验收失效
    post_node_fields(
        stage_id, NodeFieldsIn(deliverable="改成交付一份对照表", reason="范围变了"), conn
    )
    assert plan.current_stage_review(conn, stage_id) is None

    # 整组替换验收条件：同样失效，且 id 由服务端重排
    result = post_node_fields(
        stage_id,
        NodeFieldsIn(
            acceptance_criteria=[CriterionIn(text="新的条件"), CriterionIn(text="另一条")],
            reason="按新标准来",
        ),
        conn,
    )
    assert result["changed"] == ["acceptance_criteria"]
    criteria = plan._stage_criteria_of(stage_row(conn, stage_id))
    assert [item["id"] for item in criteria] == ["c1", "c2"]
    assert plan.current_stage_review(conn, stage_id) is None


# ---------- ⑦ 收尾：completed / stopped 双语义 ----------

def test_outcome_close_requires_explicit_kind(conn):
    plan_id, _ = make_outcome_plan(conn)
    with pytest.raises(HTTPException) as caught:
        post_plan_close(plan_id, PlanCloseIn(reason="不猜了"), conn)
    assert caught.value.status_code == 400
    assert conn.execute("SELECT status FROM plan WHERE id = ?", (plan_id,)).fetchone()["status"] == "active"


def test_completed_close_is_blocked_until_everything_is_accepted(conn):
    plan_id, _ = make_outcome_plan(conn)

    with pytest.raises(HTTPException) as caught:  # 阶段还没验收
        post_plan_close(plan_id, PlanCloseIn(close_kind="completed"), conn)
    assert caught.value.status_code == 409
    assert conn.execute("SELECT status FROM plan WHERE id = ?", (plan_id,)).fetchone()["status"] == "active"

    # 走完整链路之后才能按完成收尾
    contract_id = int(contract.active(conn, plan_id)["id"])
    stage_id = stage_id_of(conn, plan_id)
    task_id = int(conn.execute(
        "SELECT id FROM plan_node WHERE parent_id = ?", (stage_id,)
    ).fetchone()["id"])
    post_task_check(task_id, conn)
    evidence = submit_evidence(conn, stage_id)
    accept_review(conn, stage_id, contract_id, [evidence["id"]])

    result = post_plan_close(plan_id, PlanCloseIn(close_kind="completed", reason="都验收过了"), conn)
    assert result["status"] == "closed" and result["closure_kind"] == "completed"
    row = conn.execute("SELECT * FROM plan WHERE id = ?", (plan_id,)).fetchone()
    assert row["closure_kind"] == "completed" and row["closure_reason"] == "都验收过了"

    # 幂等：同一种收尾重复调用不写噪音；换一种语义覆盖被 409 拒
    again = post_plan_close(plan_id, PlanCloseIn(close_kind="completed"), conn)
    assert again["changed"] is False
    with pytest.raises(HTTPException) as caught:
        post_plan_close(plan_id, PlanCloseIn(close_kind="stopped", reason="改口"), conn)
    assert caught.value.status_code == 409


def test_stopped_close_records_the_reason_and_survives_reopen(conn):
    plan_id, contract_id = make_outcome_plan(conn)

    with pytest.raises(HTTPException) as caught:  # 停止必须写理由
        post_plan_close(plan_id, PlanCloseIn(close_kind="stopped"), conn)
    assert caught.value.status_code == 400

    result = post_plan_close(plan_id, PlanCloseIn(close_kind="stopped", reason="方向变了"), conn)
    assert result["closure_kind"] == "stopped"
    row = conn.execute("SELECT * FROM plan WHERE id = ?", (plan_id,)).fetchone()
    assert row["status"] == "closed" and row["closure_kind"] == "stopped"
    assert row["closure_reason"] == "方向变了"
    assert plan.list_plans(conn) == []

    # 停止不是终点：重开后走完仍可按「完成」收尾，closure_kind 被新事实更新
    post_plan_reopen(plan_id, type("R", (), {"reason": "又想做了"})(), conn)
    stage_id = stage_id_of(conn, plan_id)
    task_id = int(conn.execute(
        "SELECT id FROM plan_node WHERE parent_id = ?", (stage_id,)
    ).fetchone()["id"])
    post_task_check(task_id, conn)
    evidence = submit_evidence(conn, stage_id)
    accept_review(conn, stage_id, contract_id, [evidence["id"]])

    final = post_plan_close(plan_id, PlanCloseIn(close_kind="completed", reason="这回真做完了"), conn)
    assert final["closure_kind"] == "completed"
    events = [e for e in ledger.history(conn, "plan", plan_id) if e["change_type"] == "status_change"]
    assert events[-1]["after_value"] == "closed"
    assert conn.execute("SELECT closure_kind FROM plan WHERE id = ?", (plan_id,)).fetchone()["closure_kind"] == "completed"


def test_legacy_close_keeps_its_old_semantics(conn):
    legacy = create_plan(_GoalOnly(goal="旧流程计划"), conn)
    plan_id = legacy["id"]
    create_node(NodeIn(plan_id=plan_id, level="stage", title="阶段 1"), conn)

    with pytest.raises(HTTPException) as caught:  # legacy 不能按「完成」收尾：先升级
        post_plan_close(plan_id, PlanCloseIn(close_kind="completed"), conn)
    assert caught.value.status_code == 400

    # 旧客户端只传 reason：走旧语义完成收尾，closure_kind 留空——不猜成新验收
    result = post_plan_close(plan_id, PlanCloseIn(reason="旧口径收尾"), conn)
    assert result["status"] == "closed"
    row = conn.execute("SELECT * FROM plan WHERE id = ?", (plan_id,)).fetchone()
    assert row["closure_kind"] is None and row["closure_reason"] is None

    # legacy 也能按「停止」收尾
    other = create_plan(_GoalOnly(goal="另一个旧计划"), conn)
    stopped = post_plan_close(other["id"], PlanCloseIn(close_kind="stopped", reason="不做了"), conn)
    assert stopped["closure_kind"] == "stopped"
    assert conn.execute(
        "SELECT closure_kind FROM plan WHERE id = ?", (other["id"],)
    ).fetchone()["closure_kind"] == "stopped"


# ---------- ⑧ 原子回滚 ----------

def test_forced_failure_rolls_back_the_whole_review(conn, monkeypatch):
    plan_id, contract_id = make_outcome_plan(conn)
    stage_id = stage_id_of(conn, plan_id)
    evidence = submit_evidence(conn, stage_id)
    original = ledger.set_status

    def boom(conn_, entity_type, entity_id, new_status, **kwargs):
        if entity_type == "plan_node":
            raise RuntimeError("强制失败：模拟状态迁移中途崩溃")
        return original(conn_, entity_type, entity_id, new_status, **kwargs)

    monkeypatch.setattr(ledger, "set_status", boom)

    with pytest.raises(RuntimeError):
        accept_review(conn, stage_id, contract_id, [evidence["id"]])

    # 验收行、失效、阶段状态——一个都没落
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM stage_review WHERE node_id = ?", (stage_id,)
    ).fetchone()["n"] == 0
    assert stage_row(conn, stage_id)["status"] == "not_started"
    assert not any(
        e["change_type"] in ("stage_review", "stage_review_invalidated")
        for e in ledger.history(conn, "plan_node", stage_id)
    )
    monkeypatch.undo()
    # 修好之后同一次请求可以原样重试成功
    result = accept_review(conn, stage_id, contract_id, [evidence["id"]])
    assert result["stage_status"] == "done"


def test_forced_failure_rolls_back_contract_activation(conn, monkeypatch):
    plan_id, first_id = make_outcome_plan(conn)
    original = ledger.log_event

    def boom(conn_, entity_type, entity_id, change_type, *args, **kwargs):
        if change_type == "create_contract":
            raise RuntimeError("强制失败：模拟契约落库中途崩溃")
        return original(conn_, entity_type, entity_id, change_type, *args, **kwargs)

    monkeypatch.setattr(ledger, "log_event", boom)

    with pytest.raises(RuntimeError):
        post_plan_contract(
            plan_id,
            type("A", (), {"contract": _outcome_contract_in(contract_payload(title="新版本")), "reason": None})(),
            conn,
        )

    # 旧契约还是 active，新版本一行没落，计划的双模式标记没动
    assert contract.get(conn, first_id)["status"] == "active"
    versions = conn.execute(
        "SELECT COUNT(*) AS n FROM outcome_contract WHERE plan_id = ?", (plan_id,)
    ).fetchone()["n"]
    assert versions == 1
    assert conn.execute(
        "SELECT completion_mode, contract_review_status FROM plan WHERE id = ?", (plan_id,)
    ).fetchone()["completion_mode"] == "outcome"
    monkeypatch.undo()


def test_forced_failure_rolls_back_manual_plan_creation(conn, monkeypatch):
    original = ledger.log_event

    def boom(conn_, entity_type, entity_id, change_type, *args, **kwargs):
        if change_type == "create_contract":
            raise RuntimeError("强制失败：模拟建计划中途崩溃")
        return original(conn_, entity_type, entity_id, change_type, *args, **kwargs)

    monkeypatch.setattr(ledger, "log_event", boom)

    with pytest.raises(RuntimeError):
        post_plan_with_contract(PlanWithContractIn(contract=contract_payload()), conn)

    assert conn.execute("SELECT COUNT(*) AS n FROM plan").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM outcome_contract").fetchone()["n"] == 0


# ---------- 老库 additive migration（方案 §9.2） ----------

def test_old_database_migrates_additively_and_marks_needs_review(tmp_path):
    """老库补列 / 补表 / 建索引：索引必须在补列之后建；init 连跑两次；老数据只标不猜。"""
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE plan (
          id INTEGER PRIMARY KEY, goal TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'active', superseded_by INTEGER,
          valid_from TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE plan_node (
          id INTEGER PRIMARY KEY, plan_id INTEGER NOT NULL, parent_id INTEGER,
          level TEXT NOT NULL, title TEXT NOT NULL, deliverable TEXT, depth_target TEXT,
          due_date TEXT, status TEXT NOT NULL DEFAULT 'not_started',
          sort_order INTEGER NOT NULL DEFAULT 0, superseded_by INTEGER,
          valid_from TEXT NOT NULL, created_at TEXT NOT NULL);
        INSERT INTO plan (goal, valid_from, created_at)
          VALUES ('老计划', '2026-09-01T00:00:00+08:00', '2026-09-01T00:00:00+08:00');
    """)
    old.commit()
    old.close()

    db.init(path)
    db.init(path)  # 连跑两次：不删数据、不重复加列

    migrated = db.connect(path)
    try:
        row = migrated.execute("SELECT * FROM plan").fetchone()
        assert row["goal"] == "老计划"  # 老数据原样
        assert row["completion_mode"] == "legacy" and row["flow_version"] == 1
        assert row["contract_review_status"] == "needs_review"  # 只标「尚未补契约」
        assert row["closure_kind"] is None  # 不把老状态猜成新收尾语义
        assert migrated.execute("SELECT COUNT(*) AS n FROM outcome_contract").fetchone()["n"] == 0
        # 老表新列上的索引建成了（列不存在时建索引会直接报错——顺序错了这里就红）
        indexes = {
            item["name"] for item in migrated.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        assert {"idx_plan_completion_mode", "idx_node_contract"} <= indexes
        # 补完列的老计划仍能走 legacy 读取
        tree = plan.plan_tree(migrated, plan_id=int(row["id"]))
        assert tree["completion_mode"] == "legacy" and tree["upgrade_required"] is True
    finally:
        migrated.close()


# ---------- ⑨ 完成门槛的契约覆盖（本轮补：缺口 / 只被跳过阶段承接） ----------

def _two_criteria_plan(conn) -> tuple[int, int]:
    """合同两个必需条件，但先不建任何阶段——由各用例自己决定怎么承接。"""
    created = post_plan_with_contract(PlanWithContractIn(contract=contract_payload()), conn)
    return created["id"], created["contract"]["id"]


def test_completed_close_blocked_when_required_criterion_has_no_stage(conn):
    """必需成果条件没有任何阶段承接：阶段本身达标也不能按完成收尾。"""
    plan_id, contract_id = _two_criteria_plan(conn)
    create_node(
        NodeIn(
            plan_id=plan_id, level="stage", title="阶段 1",
            acceptance_criteria=[CriterionIn(text="接口能跑")],
            evidence_requirements=[EvidenceRequirementIn(kind="repository")],
            contract_criterion_ids=["oc-1"],  # oc-2 无人承接
        ),
        conn,
    )
    stage_id = stage_id_of(conn, plan_id)
    evidence = submit_evidence(conn, stage_id)
    accept_review(conn, stage_id, contract_id, [evidence["id"]])
    assert plan.stage_finished(conn, stage_row(conn, stage_id)) is True  # 这一阶段本身达标

    coverage = plan.contract_coverage(conn, plan_id)
    assert coverage["required"] == ["oc-1", "oc-2"]
    assert coverage["covered"] == ["oc-1"]
    assert coverage["missing"] == ["oc-2"]
    assert coverage["complete"] is False
    assert plan.plan_can_complete(conn, plan_id) is False

    tree = get_plan(plan_id=plan_id, conn=conn)
    assert tree["can_complete"] is False
    assert tree["coverage"]["missing"] == ["oc-2"]  # 后端给出缺口，前端不自行推断

    with pytest.raises(HTTPException) as caught:
        post_plan_close(plan_id, PlanCloseIn(close_kind="completed", reason="想收尾"), conn)
    assert caught.value.status_code == 409
    assert conn.execute(
        "SELECT status FROM plan WHERE id = ?", (plan_id,)
    ).fetchone()["status"] == "active"


def test_completed_close_blocked_when_required_criterion_only_skipped(conn):
    """某个必需条件只被跳过阶段承接：跳过不等于兑现，不能按完成收尾。"""
    plan_id, contract_id = _two_criteria_plan(conn)
    create_node(
        NodeIn(
            plan_id=plan_id, level="stage", title="阶段 1",
            acceptance_criteria=[CriterionIn(text="接口能跑")],
            evidence_requirements=[EvidenceRequirementIn(kind="repository")],
            contract_criterion_ids=["oc-1"],
        ),
        conn,
    )
    create_node(
        NodeIn(
            plan_id=plan_id, level="stage", title="阶段 2",
            acceptance_criteria=[CriterionIn(text="测试齐了")],
            contract_criterion_ids=["oc-2"],
        ),
        conn,
    )
    stages = plan.get_stages(conn, plan_id)
    first, second = int(stages[0]["id"]), int(stages[1]["id"])
    post_task_skip(second, type("S", (), {"reason": "这步不做了"})(), conn)

    evidence = submit_evidence(conn, first)
    accept_review(conn, first, contract_id, [evidence["id"]])

    coverage = plan.contract_coverage(conn, plan_id)
    assert coverage["covered"] == ["oc-1", "oc-2"]      # 承接了……
    assert coverage["unsatisfied"] == ["oc-2"]          # ……但只被跳过阶段承接，不算兑现
    assert coverage["missing"] == []
    assert coverage["complete"] is False
    assert plan.plan_can_complete(conn, plan_id) is False
    assert get_plan(plan_id=plan_id, conn=conn)["coverage"]["unsatisfied"] == ["oc-2"]

    with pytest.raises(HTTPException) as caught:
        post_plan_close(plan_id, PlanCloseIn(close_kind="completed", reason="想收尾"), conn)
    assert caught.value.status_code == 409


# ---------- ⑩ 跨版本失效：先改说明、后改验收标准 ----------

def test_review_invalidated_across_narrative_then_standard_change(conn):
    """说明性版本不该失效验收；但等到标准真的变化时，更早版本的 current 验收也不能漏过。"""
    plan_id, contract_id = make_outcome_plan(conn)
    stage_id = stage_id_of(conn, plan_id)
    task_id = int(conn.execute(
        "SELECT id FROM plan_node WHERE parent_id = ?", (stage_id,)
    ).fetchone()["id"])
    post_task_check(task_id, conn)
    evidence = submit_evidence(conn, stage_id)
    accept_review(conn, stage_id, contract_id, [evidence["id"]])
    assert plan.current_stage_review(conn, stage_id) is not None

    # 第一跳只改说明：不影响验收，current 验收原样保留
    first = post_plan_contract(
        plan_id,
        _ActivateIn(_outcome_contract_in(contract_payload(title="换个名字")), "改个说法"),
        conn,
    )
    assert first["reviews_invalidated"] == 0
    assert plan.current_stage_review(conn, stage_id) is not None

    # 第二跳才动验收条件：按更早（说明性）版本留下的 current 验收必须一起失效
    changed = contract_payload()
    changed["acceptance_criteria"][1]["text"] = "至少五个接口有自动化测试"
    second = post_plan_contract(
        plan_id, _ActivateIn(_outcome_contract_in(changed), "标准提高了"), conn,
    )
    assert second["reviews_invalidated"] == 1
    assert plan.current_stage_review(conn, stage_id) is None
    assert plan.stage_finished(conn, stage_row(conn, stage_id)) is False
    assert get_plan(plan_id=plan_id, conn=conn)["stages"][0]["acceptance"]["status"] == "invalidated"

    # 数据层兜底：即便把 current 强行翻回来，版本差异也必须让完成门槛拦住它
    conn.execute(
        "UPDATE stage_review SET is_current = 1, invalidated_at = NULL WHERE node_id = ?",
        (stage_id,),
    )
    conn.commit()
    assert plan.stage_finished(conn, stage_row(conn, stage_id)) is False
    assert plan.plan_can_complete(conn, plan_id) is False


# ---------- ⑪ legacy 升级入口（方案 §9.2.5） ----------

def _legacy_with_stages(conn, titles: list[str]) -> tuple[int, list[int]]:
    legacy = create_plan(_GoalOnly(goal="旧计划"), conn)
    plan_id = legacy["id"]
    ids = [
        create_node(NodeIn(plan_id=plan_id, level="stage", title=title), conn)["id"]
        for title in titles
    ]
    return plan_id, ids


def test_legacy_plan_upgrade_is_explicit_and_completable(conn):
    plan_id, (stage_a, stage_b, stage_c) = _legacy_with_stages(conn, ["旧阶段 A", "旧阶段 B", "旧阶段 C"])
    # 旧阶段已有旧交付物：升级不回写它，只在新证据区留一条只读 legacy
    post_deliverable(stage_a, DeliverableIn(url="https://old.example.com", note="旧链接"), conn)

    result = post_plan_upgrade(
        plan_id,
        PlanUpgradeIn(
            contract=contract_payload(),
            stages=[
                StageUpgradeIn(
                    stage_id=stage_a, disposition="include",
                    acceptance_criteria=[CriterionIn(text="接口能跑")],
                    evidence_requirements=[EvidenceRequirementIn(kind="repository")],
                    contract_criterion_ids=["oc-1", "oc-2"],
                ),
                StageUpgradeIn(stage_id=stage_b, disposition="skipped", reason="这步不做了"),
                StageUpgradeIn(stage_id=stage_c, disposition="history", reason="留着当历史"),
            ],
            reason="升级为成果闭环",
        ),
        conn,
    )

    assert result["completion_mode"] == "outcome"
    assert result["included"] == [stage_a]
    assert result["skipped"] == [stage_b]
    assert result["history"] == [stage_c]

    row = conn.execute("SELECT * FROM plan WHERE id = ?", (plan_id,)).fetchone()
    assert row["completion_mode"] == "outcome" and row["flow_version"] == 2
    assert row["contract_review_status"] == "ready"
    active = contract.active(conn, plan_id)
    assert active is not None and active["source_kind"] == "migrated"

    # 纳入的阶段：绑定当前契约、写入条件；跳过与历史阶段各按处置落定
    included = plan.get_node(conn, stage_a)
    assert int(included["contract_id"]) == int(active["id"])
    assert [item["id"] for item in plan._stage_criteria_of(included)] == ["c1"]
    assert plan.get_node(conn, stage_b)["status"] == "skipped"
    assert plan.get_node(conn, stage_c)["contract_id"] is None  # 保留为历史：不绑契约
    kinds = [r["kind"] for r in conn.execute(
        "SELECT kind FROM evidence_submission WHERE node_id = ?", (stage_a,)
    ).fetchall()]
    assert kinds == ["legacy"]  # 旧交付物仍是只读历史，没被伪装成新验收

    tree = get_plan(plan_id=plan_id, conn=conn)
    assert tree["upgrade_required"] is False
    assert tree["completion_mode"] == "outcome"
    by_id = {stage["id"]: stage for stage in tree["stages"]}
    assert by_id[stage_a]["in_outcome"] is True
    assert by_id[stage_c]["in_outcome"] is False
    # 历史 / 跳过的阶段不拦完成门槛；此时只差 A 阶段还没被 accepted 兑现
    assert tree["coverage"]["missing"] == []
    assert tree["coverage"]["unsatisfied"] == ["oc-1", "oc-2"]
    assert tree["can_complete"] is False

    # 升级后正常走 v2：交本阶段证据 + 逐条验收，计划就能按完成收尾
    fresh = submit_evidence(conn, stage_a)
    accept_review(conn, stage_a, int(active["id"]), [fresh["id"]])
    assert plan.plan_can_complete(conn, plan_id) is True
    closed = post_plan_close(plan_id, PlanCloseIn(close_kind="completed", reason="升级后做完"), conn)
    assert closed["closure_kind"] == "completed"


def test_legacy_upgrade_requires_a_disposition_for_every_stage(conn):
    plan_id, (stage_a, stage_b) = _legacy_with_stages(conn, ["旧阶段 A", "旧阶段 B"])

    with pytest.raises(HTTPException) as caught:
        post_plan_upgrade(
            plan_id,
            PlanUpgradeIn(
                contract=contract_payload(),
                stages=[
                    StageUpgradeIn(
                        stage_id=stage_a, disposition="include",
                        acceptance_criteria=[CriterionIn(text="接口能跑")],
                        contract_criterion_ids=["oc-1"],
                    ),
                ],
            ),
            conn,
        )
    assert caught.value.status_code == 400
    assert "没说怎么处理" in str(caught.value.detail)
    # 校验不过：一行契约都没落，模式与阶段原样
    assert conn.execute("SELECT COUNT(*) AS n FROM outcome_contract").fetchone()["n"] == 0
    assert plan.plan_mode(conn, plan_id) == "legacy"
    assert plan.get_node(conn, stage_b)["status"] == "not_started"


def test_legacy_upgrade_is_atomic_and_refuses_bad_references(conn):
    plan_id, (stage_a, stage_b) = _legacy_with_stages(conn, ["旧阶段 A", "旧阶段 B"])

    # 引用了契约里不存在的条件 id：拒绝，且不落任何东西
    with pytest.raises(HTTPException) as caught:
        post_plan_upgrade(
            plan_id,
            PlanUpgradeIn(
                contract=contract_payload(),
                stages=[
                    StageUpgradeIn(
                        stage_id=stage_a, disposition="include",
                        acceptance_criteria=[CriterionIn(text="接口能跑")],
                        contract_criterion_ids=["oc-99"],
                    ),
                    StageUpgradeIn(stage_id=stage_b, disposition="skipped", reason="不做了"),
                ],
            ),
            conn,
        )
    assert caught.value.status_code == 400
    assert conn.execute("SELECT COUNT(*) AS n FROM outcome_contract").fetchone()["n"] == 0
    assert plan.plan_mode(conn, plan_id) == "legacy"
    assert plan.get_node(conn, stage_b)["status"] == "not_started"

    # 跳过不写理由也要拒
    with pytest.raises(HTTPException) as caught:
        post_plan_upgrade(
            plan_id,
            PlanUpgradeIn(
                contract=contract_payload(),
                stages=[
                    StageUpgradeIn(
                        stage_id=stage_a, disposition="include",
                        acceptance_criteria=[CriterionIn(text="接口能跑")],
                        contract_criterion_ids=["oc-1"],
                    ),
                    StageUpgradeIn(stage_id=stage_b, disposition="skipped"),
                ],
            ),
            conn,
        )
    assert caught.value.status_code == 400


def test_legacy_upgrade_failure_rolls_back_the_whole_transaction(conn, monkeypatch):
    plan_id, (stage_a, stage_b) = _legacy_with_stages(conn, ["旧阶段 A", "旧阶段 B"])
    original = ledger.log_event

    def boom(conn_, entity_type, entity_id, change_type, *args, **kwargs):
        if change_type == "upgrade_include":
            raise RuntimeError("强制失败：升级中途崩溃")
        return original(conn_, entity_type, entity_id, change_type, *args, **kwargs)

    monkeypatch.setattr(ledger, "log_event", boom)
    with pytest.raises(RuntimeError):
        post_plan_upgrade(
            plan_id,
            PlanUpgradeIn(
                contract=contract_payload(),
                stages=[
                    StageUpgradeIn(
                        stage_id=stage_a, disposition="include",
                        acceptance_criteria=[CriterionIn(text="接口能跑")],
                        contract_criterion_ids=["oc-1"],
                    ),
                    StageUpgradeIn(stage_id=stage_b, disposition="skipped", reason="不做了"),
                ],
            ),
            conn,
        )
    monkeypatch.undo()

    # 契约、阶段绑定、跳过、模式切换——一个都没落
    assert conn.execute("SELECT COUNT(*) AS n FROM outcome_contract").fetchone()["n"] == 0
    row = conn.execute(
        "SELECT completion_mode, flow_version, contract_review_status FROM plan WHERE id = ?",
        (plan_id,),
    ).fetchone()
    assert row["completion_mode"] == "legacy" and row["flow_version"] == 1
    assert row["contract_review_status"] == "needs_review"
    assert plan.get_node(conn, stage_a)["contract_id"] is None
    assert plan.get_node(conn, stage_a)["acceptance_criteria"] is None
    assert plan.get_node(conn, stage_b)["status"] == "not_started"


def test_upgrade_only_for_active_legacy_plans(conn):
    # 已经成果化的计划：走「激活新版本」，不再升级
    outcome_id, _ = make_outcome_plan(conn)
    with pytest.raises(HTTPException) as caught:
        post_plan_upgrade(outcome_id, PlanUpgradeIn(contract=contract_payload()), conn)
    assert caught.value.status_code == 409

    # 已收尾的旧计划：先重开再升级
    legacy = create_plan(_GoalOnly(goal="旧计划"), conn)
    post_plan_close(legacy["id"], PlanCloseIn(reason="旧口径收尾"), conn)
    with pytest.raises(HTTPException) as caught:
        post_plan_upgrade(legacy["id"], PlanUpgradeIn(contract=contract_payload()), conn)
    assert caught.value.status_code == 409
    assert plan.plan_mode(conn, legacy["id"]) == "legacy"


def test_legacy_plan_stays_read_only_and_points_to_upgrade(conn):
    plan_id, (stage_id,) = _legacy_with_stages(conn, ["旧阶段"])

    # 只读：契约可以为空、标 needs_review，不猜标准；计划树也不给 coverage
    view = get_plan_contract(plan_id, conn=conn)
    assert view["contract"] is None and view["history"] == []
    tree = get_plan(plan_id=plan_id, conn=conn)
    assert tree["contract"] is None
    assert tree["coverage"] is None
    assert tree["upgrade_required"] is True
    assert tree["completion_mode"] == "legacy"

    # 只改契约、不升级：明确指路到升级入口，不偷偷改模式
    with pytest.raises(HTTPException) as caught:
        post_plan_contract(
            plan_id, _ActivateIn(_outcome_contract_in(contract_payload())), conn,
        )
    assert caught.value.status_code == 400
    assert "upgrade" in str(caught.value.detail)
    assert plan.plan_mode(conn, plan_id) == "legacy"
    assert plan.get_node(conn, stage_id)["contract_id"] is None


# ---------- ⑫ required evidence 的匹配口径 ----------

def test_required_evidence_must_be_linked_this_stage_at_review_time(conn):
    """必需证据要求：至少一条本阶段、验收时关联、kind 匹配的证据——后台有对的也不行。"""
    plan_id, contract_id = make_outcome_plan(conn)
    stage_id = stage_id_of(conn, plan_id)
    task_id = int(conn.execute(
        "SELECT id FROM plan_node WHERE parent_id = ?", (stage_id,)
    ).fetchone()["id"])
    post_task_check(task_id, conn)

    repo = submit_evidence(conn, stage_id, kind="repository")
    link = post_stage_evidence(
        stage_id, EvidenceIn(kind="link", reference="https://x.example.com", note="只是条链接"), conn,
    )

    # 本阶段有仓库证据，但验收只关联了 link 那条：kind 不匹配 → 拒
    with pytest.raises(HTTPException) as caught:
        post_stage_review(
            stage_id,
            StageReviewIn(
                contract_id=contract_id, submission_ids=[link["id"]],
                criteria_state=met_state(conn, stage_id), decision="accepted", note="有链接了",
            ),
            conn,
        )
    assert caught.value.status_code == 400
    assert "必需证据" in str(caught.value.detail)

    # 验收时关联上那条仓库证据（本阶段、kind 匹配）→ 通过
    accept_review(conn, stage_id, contract_id, [repo["id"]])
    assert plan.stage_finished(conn, stage_row(conn, stage_id)) is True


# ---------- ⑬ 契约升级的失效范围：只失效受影响阶段（方案 §4.4.3 / §6.3） ----------

def _covered_two_stage_plan(conn) -> tuple[int, int, int, int]:
    """两个阶段分别承接 oc-1 / oc-2，都可 accepted；返回 (plan_id, contract_id, a, b)。"""
    plan_id, contract_id = _two_criteria_plan(conn)
    create_node(
        NodeIn(
            plan_id=plan_id, level="stage", title="阶段 A",
            acceptance_criteria=[CriterionIn(text="A 的事能跑")],
            evidence_requirements=[EvidenceRequirementIn(kind="repository")],
            contract_criterion_ids=["oc-1"],
        ),
        conn,
    )
    create_node(
        NodeIn(
            plan_id=plan_id, level="stage", title="阶段 B",
            acceptance_criteria=[CriterionIn(text="B 的事齐了")],
            evidence_requirements=[EvidenceRequirementIn(kind="repository")],
            contract_criterion_ids=["oc-2"],
        ),
        conn,
    )
    stages = plan.get_stages(conn, plan_id)
    a, b = int(stages[0]["id"]), int(stages[1]["id"])
    for stage_id in (a, b):
        evidence = submit_evidence(conn, stage_id)
        accept_review(conn, stage_id, contract_id, [evidence["id"]])
    return plan_id, contract_id, a, b


def test_contract_upgrade_only_invalidates_affected_stages(conn):
    """只改 oc-1：承接 oc-2 的阶段继续保留当前 accepted，不被连带失效。"""
    plan_id, contract_id, a, b = _covered_two_stage_plan(conn)
    assert plan.plan_can_complete(conn, plan_id) is True
    assert plan.stage_finished(conn, stage_row(conn, b)) is True

    changed = contract_payload()
    changed["acceptance_criteria"][0]["text"] = "甲换了个说法（标准变了）"
    result = post_plan_contract(
        plan_id, _ActivateIn(_outcome_contract_in(changed), "只改了 oc-1"), conn,
    )

    assert result["reviews_invalidated"] == 1
    assert plan.current_stage_review(conn, a) is None       # A 承接 oc-1：受影响
    assert plan.current_stage_review(conn, b) is not None   # B 承接 oc-2：继续 current
    assert plan.stage_finished(conn, stage_row(conn, a)) is False
    assert plan.stage_finished(conn, stage_row(conn, b)) is True

    tree = get_plan(plan_id=plan_id, conn=conn)
    by_id = {stage["id"]: stage for stage in tree["stages"]}
    assert by_id[a]["acceptance"]["status"] == "invalidated"
    assert by_id[b]["acceptance"]["status"] == "accepted"

    # 数据层兜底也必须是逐阶段的：把 A 的旧 review 强行翻回 current，完成门槛仍按
    # A 承接的条件变化拦住它；B 不受影响。
    conn.execute(
        "UPDATE stage_review SET is_current = 1, invalidated_at = NULL WHERE node_id = ?",
        (a,),
    )
    conn.commit()
    assert plan.stage_finished(conn, stage_row(conn, a)) is False
    assert plan.stage_finished(conn, stage_row(conn, b)) is True
    assert plan.plan_can_complete(conn, plan_id) is False  # A 还没按新标准重看


def test_contract_upgrade_of_required_evidence_affects_every_stage(conn):
    """契约级必需证据要求变化对每个阶段都是同一份验收口径：一并失效。"""
    plan_id, contract_id, a, b = _covered_two_stage_plan(conn)

    changed = contract_payload()
    changed["evidence_requirements"][0]["kind"] = "demo"
    result = post_plan_contract(
        plan_id, _ActivateIn(_outcome_contract_in(changed), "必需证据换了类型"), conn,
    )

    assert result["reviews_invalidated"] == 2
    assert plan.current_stage_review(conn, a) is None
    assert plan.current_stage_review(conn, b) is None


# ---------- ⑭ 历史验收必须能还原当时正文（方案 §4.3.1 / §6.3） ----------

def test_review_snapshots_are_exposed_in_api_shapes(conn):
    plan_id, contract_id = make_outcome_plan(conn)
    stage_id = stage_id_of(conn, plan_id)
    task_id = int(conn.execute(
        "SELECT id FROM plan_node WHERE parent_id = ?", (stage_id,)
    ).fetchone()["id"])
    post_task_check(task_id, conn)
    evidence = submit_evidence(conn, stage_id)

    receipt = accept_review(conn, stage_id, contract_id, [evidence["id"]])

    # 提交回执带着条件与证据的当时正文
    assert receipt["criteria_snapshot"][0]["text"] == "CRUD 接口能跑通"
    assert receipt["submission_snapshot"][0]["kind"] == "repository"
    assert receipt["submission_snapshot"][0]["reference"] == "https://github.com/x/y"

    # GET /api/plan 的当前验收与验收历史同样带着快照
    acceptance = get_plan(plan_id=plan_id, conn=conn)["stages"][0]["acceptance"]
    latest = acceptance["latest_review"]
    assert latest["criteria_snapshot"][0]["text"] == "CRUD 接口能跑通"
    assert latest["submission_snapshot"][0]["kind"] == "repository"
    assert acceptance["reviews"][0]["criteria_snapshot"] == latest["criteria_snapshot"]

    # 条件整组替换后，历史 review 仍能还原当时的条件正文
    post_node_fields(
        stage_id,
        NodeFieldsIn(acceptance_criteria=[CriterionIn(text="全新条件")], reason="换标准"),
        conn,
    )
    history_review = get_plan(plan_id=plan_id, conn=conn)["stages"][0]["acceptance"]["reviews"][0]
    assert history_review["criteria_snapshot"][0]["text"] == "CRUD 接口能跑通"


# ---------- ⑮ 升级时「跳过」对已完成的老阶段也成立（方案 §9.2.5） ----------

def test_legacy_upgrade_can_explicitly_skip_an_already_done_stage(conn):
    plan_id, (stage_a, stage_b) = _legacy_with_stages(conn, ["旧阶段 A", "旧阶段 B"])
    post_report(ReportIn(node_id=stage_a, status="done", note="旧流程里做完了"), conn)
    assert plan.get_node(conn, stage_a)["status"] == "done"

    # 常规跳过仍不允许把「完成过」的节点改判成没做（状态机闸没被放宽）
    with pytest.raises(HTTPException) as caught:
        post_task_skip(stage_a, type("S", (), {"reason": "想跳过"})(), conn)
    assert caught.value.status_code == 400
    assert plan.get_node(conn, stage_a)["status"] == "done"

    # 但升级是显式重定基：§9.2.5 把「跳过」列为每个现有阶段都可选的处置
    result = post_plan_upgrade(
        plan_id,
        PlanUpgradeIn(
            contract=contract_payload(),
            stages=[
                StageUpgradeIn(
                    stage_id=stage_a, disposition="skipped",
                    reason="这条老步骤不纳入成果流程",
                ),
                StageUpgradeIn(stage_id=stage_b, disposition="history", reason="保留为历史"),
            ],
        ),
        conn,
    )

    assert result["skipped"] == [stage_a] and result["history"] == [stage_b]
    assert plan.get_node(conn, stage_a)["status"] == "skipped"
    assert plan.plan_mode(conn, plan_id) == "outcome"
    # 已跳过的老阶段不再需要验收；旧报告原样留作历史
    assert plan.stage_finished(conn, stage_row(conn, stage_a)) is True
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM report WHERE node_id = ?", (stage_a,)
    ).fetchone()["n"] == 1


