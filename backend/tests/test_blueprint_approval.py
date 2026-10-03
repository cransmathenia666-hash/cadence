"""蓝图批准的原子建树与契约激活（成果闭环 OC-07，路由级）。

方案见 `docs/ideas/成果闭环重构开发方案.md` §5.4 / §6.2，覆盖七组：

① happy path：确认契约 + landing_mode 分流（new_plan 同事务建计划 / continue_plan 延续
   已有计划）→ 契约激活、建树（成果字段全量写 plan_node）、提案终态、会话 converted
   一次完成；
② 回滚矩阵：契约激活失败不建树、建树失败不激活契约、会话关闭失败全回滚——
   任何一步失败整体回滚，提案保持 pending、会话保持 blueprint_pending；
③ 确认门槛：缺 confirm_contract 就地拒绝（400），提案保持 pending、什么都不写；
④ contract_overrides：经 contract.validate 同一套校验合并出最终契约快照，
   不合格整单拒绝；未知字段拒绝；
⑤ selected 语义：None = 整份采纳（既有语义不变），勾选 = 只建勾中的；
⑥ landing_mode 冲突：与会话落点不一致当场拒绝（只读预检，什么都不写）；
⑦ 历史 payload 可还原：原稿契约、overrides、最终契约快照、勾选都留在提案 payload 里。

一律用假上游打桩（路由不带 transport，替身直接换掉 `llm.Operation`）、临时库。
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app import advisor, blueprint, contract, db, ledger, llm, main, plan
from app.main import PlanBlueprintIn, PlanChatIn, ProposalDecideIn, VerdictIn


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "test.db"
    db.init(path)
    connection = db.connect(path)
    yield connection
    connection.close()


@pytest.fixture()
def fake_llm(monkeypatch):
    """替身 `llm.Operation`：按脚本逐轮回内容，并记下每次收到的 messages。"""
    queue: list[str] = []
    calls: list[list[dict]] = []

    class FakeOperation:
        def __init__(self, _conn, _task, limit=None, transport=None):
            self.used = 0

        def chat(self, messages, provider_id=None, model=None):
            calls.append(messages)
            if not queue:
                raise AssertionError("假模型被多调了一次：脚本里的回答已经用完")
            self.used += 1
            return queue.pop(0)

    monkeypatch.setattr(llm, "Operation", FakeOperation)

    def feed(*texts: str) -> None:
        queue.extend(texts)

    feed.calls = calls  # type: ignore[attr-defined]
    return feed


def add_profile(conn, category: str = "long_axis", content: str = "通用工程基础") -> int:
    return ledger.create_active(
        conn, "profile_item", {"category": category, "content": content}, actor="user"
    )


def chat_reply(questions: list[str], *, ready: bool = False, note: str = "先问几句") -> str:
    return json.dumps({"questions": questions, "ready": ready, "note": note}, ensure_ascii=False)


def contract_json(**over) -> dict:
    data = {
        "title": "做出能演示的后端小项目",
        "outcome": "一个能运行、能演示的后端小项目",
        "value": "作品集需要一个真实项目",
        "success_statement": "能运行、能当面演示、能讲清关键取舍",
        "acceptance_criteria": [
            {"id": "oc-1", "text": "能运行并演示一个含错误处理的接口", "required": True},
            {"id": "oc-2", "text": "能向他人讲清项目的技术取舍", "required": False},
        ],
        "evidence_requirements": [
            {"id": "ev-1", "kind": "repository", "required": True, "description": "可运行的代码仓库"},
        ],
    }
    data.update(over)
    return data


def stage(title: str, **over) -> dict:
    data = {
        "title": title,
        "purpose": "为最终成果解决基础问题",
        "why_now": "它是后续一切的基础",
        "deliverable": "交一个能跑通的小东西",
        "acceptance_criteria": [{"text": "交出该阶段可验收的结果", "required": True}],
        "evidence_requirements": [{"kind": "repository", "required": False, "description": "该阶段的产出"}],
        "contract_criterion_ids": ["oc-1"],
        "tasks": [{"title": "读 MDN"}, {"title": "写一个接口"}],
    }
    data.update(over)
    return data


def blueprint_json(*stages: dict, goal: str = "能自己写后端接口", contract: dict | None = None) -> str:
    return json.dumps(
        {"goal": goal, "contract": contract or contract_json(), "stages": list(stages)},
        ensure_ascii=False,
    )


def adopt(conn, *, plan_id: int | None = None, title: str = "学 HTTP") -> dict:
    """走路由采纳一条候选，返回采纳回执（含 planning_session_id）。"""
    request_id = advisor.record_request(conn, "search", "我不知道该学什么")
    candidate_id = ledger.create_active(
        conn,
        "candidate",
        {"request_id": request_id, "title": title, "why": "对主线有直接帮助", "depth_target": "够用", "rank": 1},
        actor="agent",
        reason="测试用",
    )
    return main.post_candidate_verdict(candidate_id, VerdictIn(accept=True, plan_id=plan_id), conn)


def ready_pending_proposal(conn, fake_llm, *, plan_id: int | None = None) -> dict:
    """新方向（或有落点）的会话：聊成一轮 → 出方案，返回采纳回执 + 提案 id + 会话 id。"""
    add_profile(conn)
    result = adopt(conn, plan_id=plan_id)
    session_id = int(result["planning_session_id"])
    fake_llm(chat_reply([], ready=True), blueprint_json(stage("学 HTTP")))
    main.post_plan_chat(
        PlanChatIn(planning_session_id=session_id, message="我想先把 HTTP 弄明白"), conn
    )
    created = main.post_plan_blueprint(PlanBlueprintIn(planning_session_id=session_id), conn)
    assert created["payload_version"] == 2
    return {
        "candidate_id": int(result["id"]),
        "session_id": session_id,
        "proposal_id": int(created["proposal_id"]),
        "landing_plan_id": result["landing_plan_id"],
    }


def payload_of(conn, proposal_id: int) -> dict:
    row = conn.execute("SELECT payload FROM proposal WHERE id = ?", (proposal_id,)).fetchone()
    return json.loads(row["payload"])


# ---------- ① happy path：new_plan 同事务建计划 ----------

def test_atomic_approval_new_plan_creates_plan_contract_tree_and_closes_session(conn, fake_llm):
    ctx = ready_pending_proposal(conn, fake_llm, plan_id=None)  # 新方向：没有落点计划
    plan_count_before = int(conn.execute("SELECT COUNT(*) AS n FROM plan").fetchone()["n"])

    result = main.post_proposal_decide(
        ctx["proposal_id"],
        ProposalDecideIn(approved=True, confirm_contract=True, landing_mode="new_plan"),
        conn,
    )

    assert result["effect"] == "blueprint_built"
    assert result["landing_mode"] == "new_plan"
    new_plan_id = int(result["plan_id"])
    # ① 计划是这次批准新创建的 active 正式计划，且已是成果流程
    assert int(conn.execute("SELECT COUNT(*) AS n FROM plan").fetchone()["n"]) == plan_count_before + 1
    plan_row = conn.execute("SELECT * FROM plan WHERE id = ?", (new_plan_id,)).fetchone()
    assert plan_row["status"] == "active"
    assert int(plan_row["flow_version"]) == 2 and plan_row["completion_mode"] == "outcome"
    # ② 契约激活：v1、active、来源候选可追溯
    active = contract.active(conn, new_plan_id)
    assert active is not None and int(active["version"]) == 1
    assert int(active["source_candidate_id"]) == ctx["candidate_id"]
    assert int(active["id"]) == int(result["contract_id"])
    # ③ 建树：成果字段全量写进 plan_node
    stage_row = conn.execute(
        "SELECT * FROM plan_node WHERE plan_id = ? AND level = 'stage'", (new_plan_id,)
    ).fetchone()
    assert stage_row["title"] == "学 HTTP"
    assert stage_row["purpose"] == "为最终成果解决基础问题"
    assert stage_row["why_now"] == "它是后续一切的基础"
    assert json.loads(stage_row["acceptance_criteria"])[0]["text"] == "交出该阶段可验收的结果"
    assert json.loads(stage_row["contract_criterion_ids"]) == ["oc-1"]
    assert int(stage_row["contract_id"]) == int(active["id"])
    tasks = conn.execute(
        "SELECT title FROM plan_node WHERE parent_id = ? AND level = 'task' ORDER BY sort_order",
        (stage_row["id"],),
    ).fetchall()
    assert [str(row["title"]) for row in tasks] == ["读 MDN", "写一个接口"]
    # ③' 蓝图批准建的每个阶段自动配一个周打卡（报告 → 复盘卡挂它）
    checkpoint = conn.execute(
        "SELECT title FROM plan_node WHERE parent_id = ? AND level = 'checkpoint'",
        (stage_row["id"],),
    ).fetchone()
    assert checkpoint is not None and checkpoint["title"] == "学 HTTP·周打卡"
    # ④ 会话 converted（终态）
    session = advisor.get_planning_session(conn, ctx["session_id"])
    assert session["status"] == "converted" and session["closed_at"] is not None
    # ⑤ 提案终态 accepted，payload 历史可还原
    row = conn.execute("SELECT status FROM proposal WHERE id = ?", (ctx["proposal_id"],)).fetchone()
    assert row["status"] == "accepted"
    stored = payload_of(conn, ctx["proposal_id"])
    assert stored["approval"]["final_contract"]["title"] == active["title"]
    assert stored["approval"]["confirm_contract"] is True
    assert stored["approval"]["landing_mode"] == "new_plan"
    assert stored["contract"]["title"] == "做出能演示的后端小项目"  # 原稿还在


def test_atomic_approval_continue_plan_on_the_landing_plan(conn, fake_llm):
    plan_id = ledger.create_active(conn, "plan", {"goal": "已有的计划"}, actor="user")
    # F6 口径：延续只允许已有 active 契约的计划——先把落点升到成果流程（模拟一次已完成的升级）
    contract.activate(
        conn, plan_id, contract_json(), source_kind="manual", reason="测试：先激活第一版契约"
    )
    ctx = ready_pending_proposal(conn, fake_llm, plan_id=plan_id)

    result = main.post_proposal_decide(
        ctx["proposal_id"],
        ProposalDecideIn(approved=True, confirm_contract=True, landing_mode="continue_plan"),
        conn,
    )

    assert result["plan_id"] == plan_id  # 延续落点计划，不新建
    assert int(conn.execute("SELECT COUNT(*) AS n FROM plan").fetchone()["n"]) == 1
    # 延续把契约升级到第 2 版（旧版 superseded，历史保留）
    active = contract.active(conn, plan_id)
    assert int(active["version"]) == 2
    assert result["built"]["stages"]  # 树建进了落点计划
    session = advisor.get_planning_session(conn, ctx["session_id"])
    assert session["status"] == "converted"


def test_continue_plan_refuses_a_legacy_plan_without_a_contract(conn, fake_llm):
    """F6：continue_plan 不能把 legacy 计划悄悄切进成果流程（要先走「升级为成果闭环」）。"""
    plan_id = ledger.create_active(conn, "plan", {"goal": "老计划"}, actor="user")
    plan.add_node(conn, plan_id, "stage", "老阶段", deliverable="老交付物")
    ctx = ready_pending_proposal(conn, fake_llm, plan_id=plan_id)

    with pytest.raises(HTTPException) as refused:
        main.post_proposal_decide(
            ctx["proposal_id"],
            ProposalDecideIn(approved=True, confirm_contract=True, landing_mode="continue_plan"),
            conn,
        )
    assert refused.value.status_code == 400
    assert "升级为成果闭环" in str(refused.value.detail)

    # 什么都没写：计划仍是 legacy、没有契约、没有新节点，老阶段原样
    plan_row = conn.execute("SELECT * FROM plan WHERE id = ?", (plan_id,)).fetchone()
    assert plan_row["completion_mode"] == "legacy" and int(plan_row["flow_version"]) == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM outcome_contract").fetchone()["n"] == 0
    assert int(conn.execute("SELECT COUNT(*) AS n FROM plan_node").fetchone()["n"]) == 1


def test_approval_reusing_a_same_title_stage_succeeds_and_writes_v2_fields(conn, fake_llm):
    """F1：命中「复用同名阶段」时批准必须成功——复用走 update_node_fields，
    但它不能在外层事务里再开一个 atomic（复核确认的 RuntimeError 已修）。"""
    plan_id = ledger.create_active(conn, "plan", {"goal": "已有的计划"}, actor="user")
    contract.activate(conn, plan_id, contract_json(), source_kind="manual", reason="先激活第一版")
    existing_stage = plan.add_node(conn, plan_id, "stage", "学 HTTP", deliverable="旧的交付物")
    ctx = ready_pending_proposal(conn, fake_llm, plan_id=plan_id)

    result = main.post_proposal_decide(
        ctx["proposal_id"],
        ProposalDecideIn(approved=True, confirm_contract=True, landing_mode="continue_plan"),
        conn,
    )

    assert result["effect"] == "blueprint_built"
    stages = plan.get_stages(conn, plan_id)
    assert len(stages) == 1  # 复用：没有第二条同名阶段
    assert int(stages[0]["id"]) == existing_stage
    reused = plan.get_node(conn, existing_stage)
    assert reused["deliverable"] == "交一个能跑通的小东西"  # 按这一版写进去
    assert reused["purpose"] == "为最终成果解决基础问题"
    assert json.loads(reused["acceptance_criteria"])[0]["text"] == "交出该阶段可验收的结果"
    assert json.loads(reused["contract_criterion_ids"]) == ["oc-1"]
    assert int(reused["contract_id"]) == int(result["contract_id"])  # 绑到本次激活的契约
    # 任务照建（两件）；复用阶段此前没有打卡时也会补上
    tasks = conn.execute(
        "SELECT title FROM plan_node WHERE parent_id = ? AND level = 'task' ORDER BY sort_order",
        (existing_stage,),
    ).fetchall()
    assert [str(row["title"]) for row in tasks] == ["读 MDN", "写一个接口"]
    # 复用字段更新走了台账流水
    events = [
        event for event in ledger.history(conn, "plan_node", existing_stage)
        if event["change_type"] == "update_fields"
    ]
    assert len(events) == 1


def test_new_plan_approval_backfills_the_landing_on_session_and_candidate(conn, fake_llm):
    """F4：new_plan 批准后，会话与候选的规划落点要回填成新建的正式计划。"""
    ctx = ready_pending_proposal(conn, fake_llm, plan_id=None)

    result = main.post_proposal_decide(
        ctx["proposal_id"],
        ProposalDecideIn(approved=True, confirm_contract=True, landing_mode="new_plan"),
        conn,
    )
    new_plan_id = int(result["plan_id"])

    session = advisor.get_planning_session(conn, ctx["session_id"])
    assert session["status"] == "converted"
    assert int(session["landing_plan_id"]) == new_plan_id  # 转正落点回填
    assert session["closed_reason"] and str(new_plan_id) in session["closed_reason"]
    candidate = conn.execute(
        "SELECT landing_plan_id FROM candidate WHERE id = ?", (ctx["candidate_id"],)
    ).fetchone()
    assert int(candidate["landing_plan_id"]) == new_plan_id
    # 回看会话：落点不再是空，界面答得出「转正成了哪个计划」
    view = main.get_plan_chat(planning_session_id=ctx["session_id"], conn=conn)
    assert view["plan_id"] == new_plan_id
    # 候选台账里也有这条转正流水
    events = [
        event["change_type"] for event in ledger.history(conn, "candidate", ctx["candidate_id"])
    ]
    assert "update_fields" in events


def test_legacy_chat_cannot_bypass_a_live_session(conn, fake_llm):
    """F5：候选有活会话时，legacy 形态（candidate_id + plan_id）不能再另开线程/换落点。"""
    other_plan = ledger.create_active(conn, "plan", {"goal": "别处的计划"}, actor="user")
    result = adopt(conn)  # 新方向：会话落点为 None
    session_id = int(result["planning_session_id"])
    candidate_id = int(result["id"])

    # legacy 出方案想落到别处：被会话落点挡下（不信任客户端另传的 plan_id）
    with pytest.raises(HTTPException) as blocked:
        main.post_plan_blueprint(
            PlanBlueprintIn(candidate_id=candidate_id, plan_id=other_plan), conn
        )
    assert blocked.value.status_code == 409
    # legacy 聊一轮带别处 plan_id：同样被挡
    with pytest.raises(HTTPException) as blocked_chat:
        main.post_plan_chat(
            PlanChatIn(candidate_id=candidate_id, message="串线", plan_id=other_plan), conn
        )
    assert blocked_chat.value.status_code == 409
    # 什么都没落：没有 legacy 线程、没有提案、会话仍 active
    assert conn.execute("SELECT COUNT(*) AS n FROM plan_chat").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM proposal").fetchone()["n"] == 0
    assert advisor.get_planning_session(conn, session_id)["status"] == "active"


def test_two_sessions_sharing_a_landing_plan_do_not_invalidate_each_other(conn, fake_llm):
    """F2：两条会话落到同一计划时，后者的新版只取代**自己会话**的待批稿，
    前者的待批稿与会话状态都不受影响（会话级「树 = 版本」）。"""
    plan_id = ledger.create_active(conn, "plan", {"goal": "共用落点"}, actor="user")
    contract.activate(conn, plan_id, contract_json(), source_kind="manual", reason="先激活第一版")
    add_profile(conn)

    def open_session(title: str, stage_title: str) -> dict:
        request_id = advisor.record_request(conn, "search", "找个方向")
        candidate_id = ledger.create_active(
            conn,
            "candidate",
            {"request_id": request_id, "title": title, "why": "w", "depth_target": "够用", "rank": 1},
            actor="agent",
            reason="测试用",
        )
        decided = main.post_candidate_verdict(
            candidate_id, VerdictIn(accept=True, plan_id=plan_id), conn
        )
        session_id = int(decided["planning_session_id"])
        fake_llm(chat_reply([], ready=True), blueprint_json(stage(stage_title)))
        main.post_plan_chat(PlanChatIn(planning_session_id=session_id, message="聊聊"), conn)
        created = main.post_plan_blueprint(PlanBlueprintIn(planning_session_id=session_id), conn)
        return {"session_id": session_id, "proposal_id": int(created["proposal_id"])}

    first = open_session("学 HTTP", "搭 HTTP 底座")
    second = open_session("学数据库", "搭数据库底座")

    # 两条会话各自的待批稿都在；没有谁被静默作废
    assert conn.execute(
        "SELECT status FROM proposal WHERE id = ?", (first["proposal_id"],)
    ).fetchone()["status"] == "pending"
    assert conn.execute(
        "SELECT status FROM proposal WHERE id = ?", (second["proposal_id"],)
    ).fetchone()["status"] == "pending"
    assert advisor.get_planning_session(conn, first["session_id"])["status"] == "blueprint_pending"

    # 第一条照常可以批准；第二条不受影响，之后也能批准
    approved_first = main.post_proposal_decide(
        first["proposal_id"],
        ProposalDecideIn(approved=True, confirm_contract=True, landing_mode="continue_plan"),
        conn,
    )
    assert approved_first["effect"] == "blueprint_built"
    assert advisor.get_planning_session(conn, first["session_id"])["status"] == "converted"
    assert advisor.get_planning_session(conn, second["session_id"])["status"] == "blueprint_pending"

    approved_second = main.post_proposal_decide(
        second["proposal_id"],
        ProposalDecideIn(approved=True, confirm_contract=True, landing_mode="continue_plan"),
        conn,
    )
    assert approved_second["effect"] == "blueprint_built"
    assert advisor.get_planning_session(conn, second["session_id"])["status"] == "converted"
    titles = [str(row["title"]) for row in plan.get_stages(conn, plan_id)]
    assert titles == ["搭 HTTP 底座", "搭数据库底座"]


def test_selected_none_means_the_whole_blueprint_and_partial_builds_only_ticked(conn, fake_llm):
    """selected=None = 整份采纳（既有语义保留）；勾选只建勾中的。"""
    # 整份：payload 里的阶段整段采纳（2 件任务全建）
    ctx = ready_pending_proposal(conn, fake_llm)
    result = main.post_proposal_decide(
        ctx["proposal_id"],
        ProposalDecideIn(approved=True, confirm_contract=True, landing_mode="new_plan"),
        conn,
    )
    assert len(result["built"]["stages"]) == 1 and len(result["built"]["tasks"]) == 2

    # 勾选：只要任务 0（「读 MDN」），「写一个接口」丢弃
    ctx2 = ready_pending_proposal_named(conn, fake_llm)
    result2 = main.post_proposal_decide(
        ctx2["proposal_id"],
        ProposalDecideIn(
            approved=True, confirm_contract=True, landing_mode="new_plan", selected=["0.0"]
        ),
        conn,
    )
    assert [item["title"] for item in result2["built"]["tasks"]] == ["读 MDN"]


def ready_pending_proposal_named(conn, fake_llm, *, title: str = "学后端") -> dict:
    """同 ready_pending_proposal，但候选标题不同，避免阶段/候选标题撞防重名闸。"""
    request_id = advisor.record_request(conn, "search", "再找一个方向")
    candidate_id = ledger.create_active(
        conn,
        "candidate",
        {"request_id": request_id, "title": title, "why": "对主线有直接帮助", "depth_target": "够用", "rank": 1},
        actor="agent",
        reason="测试用",
    )
    add_profile(conn)
    decided = main.post_candidate_verdict(candidate_id, VerdictIn(accept=True), conn)
    session_id = int(decided["planning_session_id"])
    fake_llm(chat_reply([], ready=True), blueprint_json(stage("学 HTTP")))
    main.post_plan_chat(
        PlanChatIn(planning_session_id=session_id, message="聊聊这个"), conn
    )
    created = main.post_plan_blueprint(PlanBlueprintIn(planning_session_id=session_id), conn)
    return {"session_id": session_id, "proposal_id": int(created["proposal_id"])}


# ---------- ③ 缺确认标记：就地拒绝，提案保持 pending ----------

def test_missing_confirm_contract_is_refused_and_nothing_is_written(conn, fake_llm):
    ctx = ready_pending_proposal(conn, fake_llm, plan_id=None)
    plan_count_before = int(conn.execute("SELECT COUNT(*) AS n FROM plan").fetchone()["n"])

    with pytest.raises(HTTPException) as refused:
        main.post_proposal_decide(
            ctx["proposal_id"],
            ProposalDecideIn(approved=True, confirm_contract=False, landing_mode="new_plan"),
            conn,
        )
    assert refused.value.status_code == 400

    # 什么都不写：没有计划、没有契约、没有节点；提案 pending、会话还在 blueprint_pending
    assert int(conn.execute("SELECT COUNT(*) AS n FROM plan").fetchone()["n"]) == plan_count_before
    assert conn.execute("SELECT COUNT(*) AS n FROM plan_node").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM outcome_contract").fetchone()["n"] == 0
    row = conn.execute("SELECT status FROM proposal WHERE id = ?", (ctx["proposal_id"],)).fetchone()
    assert row["status"] == "pending"
    session = advisor.get_planning_session(conn, ctx["session_id"])
    assert session["status"] == "blueprint_pending"


# ---------- ④ contract_overrides：同一套校验、历史可还原 ----------

def test_contract_overrides_are_merged_validated_and_snapshotted(conn, fake_llm):
    ctx = ready_pending_proposal(conn, fake_llm, plan_id=None)

    result = main.post_proposal_decide(
        ctx["proposal_id"],
        ProposalDecideIn(
            approved=True,
            confirm_contract=True,
            landing_mode="new_plan",
            contract_overrides={"title": "做一个能演示的记账小程序"},
        ),
        conn,
    )

    active = contract.active(conn, int(result["plan_id"]))
    assert active["title"] == "做一个能演示的记账小程序"
    # 未覆盖的字段按蓝图值继承
    assert active["outcome"] == "一个能运行、能演示的后端小项目"
    stored = payload_of(conn, ctx["proposal_id"])
    assert stored["contract"]["title"] == "做出能演示的后端小项目"  # 原稿
    assert stored["approval"]["contract_overrides"] == {"title": "做一个能演示的记账小程序"}
    assert stored["approval"]["final_contract"]["title"] == "做一个能演示的记账小程序"


@pytest.mark.parametrize("overrides", [
    {"acceptance_criteria": []},                      # 必填字段清空 → 校验拒绝
    {"acceptance_criteria": [{"text": "只有一条", "required": True}]},  # 条数越界
    {"not_a_field": "x"},                             # 未知字段
])
def test_bad_contract_overrides_are_refused_and_nothing_is_written(conn, fake_llm, overrides):
    ctx = ready_pending_proposal(conn, fake_llm, plan_id=None)

    with pytest.raises(HTTPException) as refused:
        main.post_proposal_decide(
            ctx["proposal_id"],
            ProposalDecideIn(
                approved=True,
                confirm_contract=True,
                landing_mode="new_plan",
                contract_overrides=overrides,
            ),
            conn,
        )
    assert refused.value.status_code == 400

    assert conn.execute("SELECT COUNT(*) AS n FROM plan_node").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM outcome_contract").fetchone()["n"] == 0
    row = conn.execute("SELECT status FROM proposal WHERE id = ?", (ctx["proposal_id"],)).fetchone()
    assert row["status"] == "pending"


# ---------- ⑥ landing_mode 冲突：只读预检拦下 ----------

def test_landing_mode_conflicting_with_the_session_landing_is_refused(conn, fake_llm):
    plan_id = ledger.create_active(conn, "plan", {"goal": "已有的计划"}, actor="user")
    ctx = ready_pending_proposal(conn, fake_llm, plan_id=plan_id)

    # 落点已有计划却要新建 → 409
    with pytest.raises(HTTPException) as conflict:
        main.post_proposal_decide(
            ctx["proposal_id"],
            ProposalDecideIn(approved=True, confirm_contract=True, landing_mode="new_plan"),
            conn,
        )
    assert conflict.value.status_code == 409
    assert conn.execute("SELECT COUNT(*) AS n FROM outcome_contract").fetchone()["n"] == 0

    # revise_plan 但落点计划还没有契约 → 400（先 continue_plan 激活第一版）
    with pytest.raises(HTTPException) as revise:
        main.post_proposal_decide(
            ctx["proposal_id"],
            ProposalDecideIn(approved=True, confirm_contract=True, landing_mode="revise_plan"),
            conn,
        )
    assert revise.value.status_code == 400
    row = conn.execute("SELECT status FROM proposal WHERE id = ?", (ctx["proposal_id"],)).fetchone()
    assert row["status"] == "pending"


# ---------- ② 回滚矩阵 ----------

def test_contract_activation_failure_builds_no_tree(conn, fake_llm, monkeypatch):
    """契约激活失败 → 不建树、不建计划、提案与会话原样。"""
    ctx = ready_pending_proposal(conn, fake_llm, plan_id=None)
    plan_count_before = int(conn.execute("SELECT COUNT(*) AS n FROM plan").fetchone()["n"])

    def boom(*args, **kwargs):
        raise contract.ContractError("强制失败：契约激活不了")

    monkeypatch.setattr(blueprint.contract, "activate", boom)

    with pytest.raises(HTTPException) as failed:
        main.post_proposal_decide(
            ctx["proposal_id"],
            ProposalDecideIn(approved=True, confirm_contract=True, landing_mode="new_plan"),
            conn,
        )
    assert failed.value.status_code == 400

    assert int(conn.execute("SELECT COUNT(*) AS n FROM plan").fetchone()["n"]) == plan_count_before
    assert conn.execute("SELECT COUNT(*) AS n FROM plan_node").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM outcome_contract").fetchone()["n"] == 0
    assert conn.execute("SELECT status FROM proposal WHERE id = ?", (ctx["proposal_id"],)).fetchone()["status"] == "pending"
    assert advisor.get_planning_session(conn, ctx["session_id"])["status"] == "blueprint_pending"


def test_tree_build_failure_activates_no_contract(conn, fake_llm, monkeypatch):
    """建树失败 → 已激活的**新版本**契约也一起回滚（旧版本保留）；提案 pending。"""
    plan_id = ledger.create_active(conn, "plan", {"goal": "已有的计划"}, actor="user")
    # F6 口径：延续要求落点已有 active 契约；先放一版 v1，失败后它必须原样保留
    contract.activate(
        conn, plan_id, contract_json(), source_kind="manual", reason="测试：先激活第一版契约"
    )
    ctx = ready_pending_proposal(conn, fake_llm, plan_id=plan_id)

    calls = {"n": 0}

    def boom(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] >= 2:  # 第一个节点（阶段）建成，第二个（任务）失败
            raise plan.PlanError("强制失败：建树到一半炸了")
        return real_add_node(*args, **kwargs)

    real_add_node = plan.add_node
    monkeypatch.setattr(plan, "add_node", boom)

    with pytest.raises(HTTPException) as failed:
        main.post_proposal_decide(
            ctx["proposal_id"],
            ProposalDecideIn(approved=True, confirm_contract=True, landing_mode="continue_plan"),
            conn,
        )
    assert failed.value.status_code == 400

    assert conn.execute("SELECT COUNT(*) AS n FROM plan_node").fetchone()["n"] == 0
    # 只剩预置的 v1：这次批准要落的新版本随事务回滚
    assert conn.execute("SELECT COUNT(*) AS n FROM outcome_contract").fetchone()["n"] == 1
    active = contract.active(conn, plan_id)
    assert active is not None and int(active["version"]) == 1
    assert conn.execute("SELECT status FROM proposal WHERE id = ?", (ctx["proposal_id"],)).fetchone()["status"] == "pending"
    assert advisor.get_planning_session(conn, ctx["session_id"])["status"] == "blueprint_pending"


def test_session_close_failure_rolls_back_everything(conn, fake_llm, monkeypatch):
    """会话关闭失败 → 全回滚：不留半棵树、半份契约，也不留下半个 session。"""
    ctx = ready_pending_proposal(conn, fake_llm, plan_id=None)
    plan_count_before = int(conn.execute("SELECT COUNT(*) AS n FROM plan").fetchone()["n"])

    def boom(*args, **kwargs):
        raise advisor.AdvisorError("强制失败：会话关不上")

    monkeypatch.setattr(advisor, "set_session_status", boom)

    with pytest.raises(HTTPException) as failed:
        main.post_proposal_decide(
            ctx["proposal_id"],
            ProposalDecideIn(approved=True, confirm_contract=True, landing_mode="new_plan"),
            conn,
        )
    assert failed.value.status_code == 400

    assert int(conn.execute("SELECT COUNT(*) AS n FROM plan").fetchone()["n"]) == plan_count_before
    assert conn.execute("SELECT COUNT(*) AS n FROM plan_node").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM outcome_contract").fetchone()["n"] == 0
    assert conn.execute("SELECT status FROM proposal WHERE id = ?", (ctx["proposal_id"],)).fetchone()["status"] == "pending"
    assert advisor.get_planning_session(conn, ctx["session_id"])["status"] == "blueprint_pending"
