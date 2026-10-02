"""规划会话（成果闭环 OC-05，路由级）：采纳 = 进入规划，不再直接建阶段。

方案见 `docs/ideas/成果闭环重构开发方案.md` §3.3 / §5.2 / §6.1 / §6.2，覆盖六组：

① 接受新候选不建阶段：采纳只标 accepted + 记规划落点 + 开 planning_session，
   计划结构（plan / plan_node）一个不碰，回执里 node_id 恒为 None；
② 新方向采纳：没有现存 active 计划也能采纳——不创建正式计划、不出现 draft，
   规划会话的落点在转换（蓝图批准）前为空；
③ 已有计划落点：只记录归属，不改计划结构；
④ 会话归属与状态流转：active → blueprint_pending（出方案）→ converted（批准）；
   驳回退回 active；blueprint_pending / 终态发消息被拦；重复采纳与冲突落点 409；
⑤ 会话对话的服务端解析：候选与落点从会话来，客户端另传的 plan_id / 别的候选一律拦下；
   六轮上限、至少聊成一轮、ready 门槛不变；消息行带 planning_session_id；
⑥ 旧数据兼容：没有会话的旧 accepted 候选与旧消息行按 legacy 只读兼容读取。

一律用假上游打桩（路由不带 transport，替身直接换掉 `llm.Operation`）、临时库、不打真实接口。
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app import advisor, blueprint, db, ledger, llm, main, plan
from app.main import BlueprintReturnIn, PlanBlueprintIn, PlanChatIn, ProposalDecideIn, VerdictIn


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "test.db"
    db.init(path)
    connection = db.connect(path)
    yield connection
    connection.close()


@pytest.fixture()
def fake_llm(monkeypatch):
    """替身 `llm.Operation`：按脚本逐轮回内容，并记下每次收到的 messages。

    路由层不带 transport（那是领域层的参数），所以这里直接换掉 Operation——
    脚本用完了还被调就直接报错（防悄悄多调一次）。
    """
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


def stage(title: str, *, deliverable: str = "交一个能跑通的小东西", tasks=None, why="先打基础"):
    return {"title": title, "deliverable": deliverable, "why": why, "tasks": list(tasks or [])}


def task(title: str) -> dict:
    return {"title": title}


# 蓝图 v2（OC-06）契约底稿：oc-1 必需且被所有默认阶段承接；契约校验要求 2 条起
BASE_CONTRACT: dict = {
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


def v2_stage(stage_dict: dict) -> dict:
    upgraded = dict(stage_dict)
    upgraded.setdefault("purpose", "为最终成果解决基础问题")
    upgraded.setdefault("why_now", str(upgraded.get("why") or "先打基础"))
    upgraded.setdefault(
        "acceptance_criteria", [{"text": "交出该阶段可验收的结果", "required": True}]
    )
    upgraded.setdefault(
        "evidence_requirements",
        [{"kind": "repository", "required": False, "description": "该阶段的产出"}],
    )
    upgraded.setdefault("contract_criterion_ids", ["oc-1"])
    return upgraded


def blueprint_json(*stages: dict, goal: str = "能自己写后端接口") -> str:
    return json.dumps(
        {
            "goal": goal,
            "contract": {**BASE_CONTRACT},
            "stages": [v2_stage(item) for item in stages],
        },
        ensure_ascii=False,
    )


def adopt_via_route(conn, *, title: str = "学 HTTP", plan_id: int | None = None) -> dict:
    """走**路由**采纳一条候选（请求没有计划归属的「新方向」），返回采纳回执。"""
    request_id = advisor.record_request(conn, "search", "我不知道该学什么")
    candidate_id = ledger.create_active(
        conn,
        "candidate",
        {"request_id": request_id, "title": title, "why": "对主线有直接帮助", "depth_target": "够用", "rank": 1},
        actor="agent",
        reason="测试用",
    )
    return main.post_candidate_verdict(candidate_id, VerdictIn(accept=True, plan_id=plan_id), conn)


def session_row(conn, session_id: int):
    return advisor.get_planning_session(conn, session_id)


# ---------- ① 接受新候选：不建阶段，进入规划 ----------

def test_accepting_a_candidate_builds_no_stage_and_opens_a_session(conn):
    plan_id = ledger.create_active(conn, "plan", {"goal": "已有计划"}, actor="user")
    result = adopt_via_route(conn, plan_id=plan_id)

    assert result["status"] == "accepted"
    assert result["plan_id"] == plan_id  # 兼容字段：语义已是「规划落点」
    assert result["landing_plan_id"] == plan_id
    assert result["node_id"] is None  # 不再有「已建阶段」的回执
    assert result["planning_status"] == "needs_blueprint"
    assert result["created_planning_session"] is True
    assert "蓝图批准" in result["message"]

    # 计划结构一个不碰
    assert conn.execute("SELECT COUNT(*) AS n FROM plan_node").fetchone()["n"] == 0
    # 会话落库且走台账留痕
    session = session_row(conn, result["planning_session_id"])
    assert session["status"] == "active"
    assert int(session["candidate_id"]) == int(result["id"])
    assert int(session["landing_plan_id"]) == plan_id
    assert session["closed_at"] is None  # 还没到终态
    events = ledger.history(conn, "planning_session", result["planning_session_id"])
    assert [event["change_type"] for event in events] == ["create"]
    # 候选自己的台账：accept 带落点
    candidate_events = ledger.history(conn, "candidate", int(result["id"]))
    assert candidate_events[-1]["change_type"] == "status_change"
    assert candidate_events[-1]["after_value"] == "accepted"


# ---------- ② 新方向：没有现存计划也能采纳 ----------

def test_new_direction_adopt_opens_a_session_without_any_plan(conn):
    result = adopt_via_route(conn)  # 没建任何计划、也不传 plan_id

    assert result["status"] == "accepted"
    assert result["plan_id"] is None and result["landing_plan_id"] is None
    assert result["node_id"] is None
    # 不创建正式计划，也不新增 plan.status=draft 之类的中间状态
    assert conn.execute("SELECT COUNT(*) AS n FROM plan").fetchone()["n"] == 0
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM plan WHERE status = 'draft'"
    ).fetchone()["n"] == 0
    session = session_row(conn, result["planning_session_id"])
    assert session["status"] == "active"
    assert session["landing_plan_id"] is None  # 转换（蓝图批准）前没有落点


# ---------- ③ 已有计划落点：只记归属，不改结构 ----------

def test_existing_plan_landing_keeps_the_plan_structure_untouched(conn):
    plan_id = ledger.create_active(conn, "plan", {"goal": "进行中的计划"}, actor="user")
    stage_id = plan.add_node(conn, plan_id, "stage", "已有阶段", deliverable="原来要交的东西")
    plan.add_node(conn, plan_id, "task", "已有任务", parent_id=stage_id)

    result = adopt_via_route(conn, title="学 HTTP", plan_id=plan_id)

    assert result["status"] == "accepted"
    stages = plan.get_stages(conn, plan_id)
    assert [str(row["title"]) for row in stages] == ["已有阶段"]  # 没有同名新阶段
    tasks = conn.execute(
        "SELECT title FROM plan_node WHERE parent_id = ?", (stage_id,)
    ).fetchall()
    assert [str(row["title"]) for row in tasks] == ["已有任务"]  # 没有新任务
    assert conn.execute(
        "SELECT deliverable FROM plan_node WHERE id = ?", (stage_id,)
    ).fetchone()["deliverable"] == "原来要交的东西"  # 已有字段也没被改
    session = session_row(conn, result["planning_session_id"])
    assert int(session["landing_plan_id"]) == plan_id  # 只记归属


# ---------- ④ 会话归属与状态流转 ----------

def test_duplicate_adoption_and_conflicting_landing_are_conflicts(conn):
    plan_id = ledger.create_active(conn, "plan", {"goal": "计划"}, actor="user")
    result = adopt_via_route(conn, plan_id=plan_id)
    candidate_id = int(result["id"])

    # 重复采纳（无论方向）都是 409：候选已裁定
    with pytest.raises(HTTPException) as repeat:
        main.post_candidate_verdict(candidate_id, VerdictIn(accept=True, plan_id=plan_id), conn)
    assert repeat.value.status_code == 409
    with pytest.raises(HTTPException) as repeat_reject:
        main.post_candidate_verdict(
            candidate_id, VerdictIn(accept=False, reason="反悔"), conn
        )
    assert repeat_reject.value.status_code == 409

    # 冲突落点：候选归属计划 A，显式指定计划 B → 409，候选保持 proposed 不被改写
    request_id = advisor.record_request(conn, "search", "问过", plan_id)
    other_plan = ledger.create_active(conn, "plan", {"goal": "另一个计划"}, actor="user")
    attributed = ledger.create_active(
        conn,
        "candidate",
        {"request_id": request_id, "title": "学后端", "why": "w", "depth_target": "够用", "rank": 1},
        actor="agent",
        reason="测试用",
    )
    with pytest.raises(HTTPException) as conflict:
        main.post_candidate_verdict(attributed, VerdictIn(accept=True, plan_id=other_plan), conn)
    assert conflict.value.status_code == 409
    row = conn.execute("SELECT status FROM candidate WHERE id = ?", (attributed,)).fetchone()
    assert row["status"] == "proposed"  # 没动它，可重试


def test_session_lifecycle_active_to_pending_back_to_active_and_converted(conn, fake_llm):
    """状态流转：active → blueprint_pending（出方案）→（退回/驳回）→ active →
    blueprint_pending → converted（终态只读；批准关闭会话的原子链路在 OC-07 接上）。"""
    add_profile(conn)
    plan_id = ledger.create_active(conn, "plan", {"goal": "延续这个计划"}, actor="user")
    result = adopt_via_route(conn, plan_id=plan_id)
    session_id = int(result["planning_session_id"])

    fake_llm(
        chat_reply([], ready=True),
        blueprint_json(stage("学 HTTP", tasks=[task("读 MDN")])),
        chat_reply([], ready=True),
        blueprint_json(stage("学 HTTP", tasks=[task("写一个客户端")])),
        chat_reply([], ready=True),
        blueprint_json(stage("学 HTTP", tasks=[task("补一份演示笔记")])),
    )
    main.post_plan_chat(
        PlanChatIn(planning_session_id=session_id, message="我想先把 HTTP 弄明白"), conn
    )
    created = main.post_plan_blueprint(PlanBlueprintIn(planning_session_id=session_id), conn)

    # 生成成功：v2 提案落库（version=2 + 契约 + 阶段树）、会话进入 blueprint_pending
    assert created["planning_session_id"] == session_id
    assert created["payload_version"] == 2
    assert session_row(conn, session_id)["status"] == "blueprint_pending"
    payload = json.loads(
        conn.execute("SELECT payload FROM proposal WHERE id = ?", (created["proposal_id"],)).fetchone()["payload"]
    )
    assert payload["version"] == 2
    assert payload["planning_session_id"] == session_id
    assert payload["contract"]["acceptance_criteria"][0]["id"] == "oc-1"
    assert payload["stages"][0]["contract_criterion_ids"] == ["oc-1"]
    assert payload["stages"][0]["acceptance_criteria"]

    # blueprint_pending 只能读，不能继续聊；视图带待批稿编号
    with pytest.raises(HTTPException) as blocked:
        main.post_plan_chat(
            PlanChatIn(planning_session_id=session_id, message="还想再聊一句"), conn
        )
    assert blocked.value.status_code == 409
    view = main.get_plan_chat(planning_session_id=session_id, conn=conn)
    assert view["pending_blueprint_proposal_id"] == int(created["proposal_id"])
    assert view["planning_status"] == "blueprint_pending"

    # 退回入口：pending 且属于该会话 → 提案 superseded、会话回 active，对话历史保留
    returned = main.post_blueprint_return(
        int(created["proposal_id"]),
        BlueprintReturnIn(planning_session_id=session_id, reason="先别裁，成果标准还想再改改"),
        conn,
    )
    assert returned["proposal_status"] == "superseded"
    assert returned["session_status"] == "active"
    view = main.get_plan_chat(planning_session_id=session_id, conn=conn)
    assert view["planning_session"]["status"] == "active"
    assert view["planning_status"] == "needs_blueprint"
    assert view["pending_blueprint_proposal_id"] is None
    assert len(view["messages"]) == 2  # 上一轮的对话还在

    # 驳回同样退回 active（既有语义保留）
    main.post_plan_chat(
        PlanChatIn(planning_session_id=session_id, message="改成做一个客户端吧"), conn
    )
    created2 = main.post_plan_blueprint(PlanBlueprintIn(planning_session_id=session_id), conn)
    decided = main.post_proposal_decide(
        created2["proposal_id"], ProposalDecideIn(approved=False, reason="阶段拆得不对"), conn
    )
    assert decided["status"] == "rejected"
    assert session_row(conn, session_id)["status"] == "active"

    # converted 是 blueprint_pending 的正向终点（OC-07 的原子批准负责触发）；
    # 终态之后会话只读、closed_at/closed_reason 落库
    main.post_plan_chat(
        PlanChatIn(planning_session_id=session_id, message="那就这版吧"), conn
    )
    created3 = main.post_plan_blueprint(PlanBlueprintIn(planning_session_id=session_id), conn)
    advisor.set_session_status(
        conn, session_id, "converted", reason="蓝图提案 #{0} 批准，规划完成".format(created3["proposal_id"])
    )
    session = session_row(conn, session_id)
    assert session["status"] == "converted"
    assert session["closed_at"] is not None and "批准" in session["closed_reason"]
    with pytest.raises(HTTPException) as readonly:
        main.post_plan_chat(
            PlanChatIn(planning_session_id=session_id, message="还想聊"), conn
        )
    assert readonly.value.status_code == 409
    events = [
        event["after_value"]
        for event in ledger.history(conn, "planning_session", session_id)
        if event["change_type"] == "status_change"
    ]
    assert events == [
        "blueprint_pending", "active",  # 生成 → 退回
        "blueprint_pending", "active",  # 生成 → 驳回
        "blueprint_pending", "converted",  # 生成 → 规划完成
    ]


def test_return_entry_guards(conn, fake_llm):
    """退回入口的前提：pending + 属于该会话 + 理由必填。"""
    add_profile(conn)
    plan_id = ledger.create_active(conn, "plan", {"goal": "计划"}, actor="user")
    result = adopt_via_route(conn, plan_id=plan_id)
    session_id = int(result["planning_session_id"])
    fake_llm(chat_reply([], ready=True), blueprint_json(stage("学 HTTP")))
    main.post_plan_chat(PlanChatIn(planning_session_id=session_id, message="聊聊"), conn)
    created = main.post_plan_blueprint(PlanBlueprintIn(planning_session_id=session_id), conn)

    # 理由必填（路由层校验 422 之前，业务层也会拦；这里走业务层）
    with pytest.raises(HTTPException) as no_reason:
        main.post_blueprint_return(
            int(created["proposal_id"]),
            BlueprintReturnIn(planning_session_id=session_id, reason=" "), conn
        )
    assert no_reason.value.status_code == 400

    # 别的会话的待批稿不能从这里退回
    other = adopt_via_route(conn, title="学后端", plan_id=plan_id)
    with pytest.raises(HTTPException) as hijack:
        main.post_blueprint_return(
            int(created["proposal_id"]),
            BlueprintReturnIn(planning_session_id=int(other["planning_session_id"]), reason="串线"),
            conn,
        )
    assert hijack.value.status_code == 409

    # 已裁定过的提案不能退回
    decided = main.post_proposal_decide(
        created["proposal_id"], ProposalDecideIn(approved=False, reason="不要了"), conn
    )
    assert decided["status"] == "rejected"
    with pytest.raises(HTTPException) as decided_conflict:
        main.post_blueprint_return(
            int(created["proposal_id"]),
            BlueprintReturnIn(planning_session_id=session_id, reason="反悔"),
            conn,
        )
    assert decided_conflict.value.status_code == 409


# ---------- ⑤ 会话对话：服务端解析归属，门槛不变 ----------

def test_session_chat_resolves_candidate_and_landing_server_side(conn, fake_llm):
    add_profile(conn)
    plan_id = ledger.create_active(conn, "plan", {"goal": "落点计划"}, actor="user")
    result = adopt_via_route(conn, plan_id=plan_id)
    session_id = int(result["planning_session_id"])
    other_plan = ledger.create_active(conn, "plan", {"goal": "别的计划"}, actor="user")

    # 客户端另传一个不同的 plan_id 想串线 → 409；不传则由服务端从会话解析落点
    with pytest.raises(HTTPException) as hijack:
        main.post_plan_chat(
            PlanChatIn(planning_session_id=session_id, message="第一句", plan_id=other_plan), conn
        )
    assert hijack.value.status_code == 409
    fake_llm(chat_reply(["每周几小时？"]))
    said = main.post_plan_chat(
        PlanChatIn(planning_session_id=session_id, message="第一句"), conn
    )
    assert said["plan_id"] == plan_id
    assert said["planning_session_id"] == session_id

    # 别的候选冒充这段会话 → 409
    other_candidate = ledger.create_active(
        conn,
        "candidate",
        {
            "request_id": advisor.record_request(conn, "search", "另一条"),
            "title": "别的方向", "why": "w", "depth_target": "够用", "rank": 1,
        },
        actor="agent",
        reason="测试用",
    )
    with pytest.raises(HTTPException) as cross:
        main.post_plan_chat(
            PlanChatIn(candidate_id=other_candidate, planning_session_id=session_id, message="串线"), conn
        )
    assert cross.value.status_code == 409

    # 消息行按会话归属落库；plan_id 记服务端解析的落点
    rows = conn.execute(
        "SELECT plan_id, candidate_id, planning_session_id, role FROM plan_chat"
        " WHERE planning_session_id = ? ORDER BY id",
        (session_id,),
    ).fetchall()
    assert [str(row["role"]) for row in rows] == ["user", "assistant"]
    assert all(int(row["plan_id"]) == plan_id for row in rows)
    assert all(int(row["candidate_id"]) == int(result["id"]) for row in rows)


def test_session_chat_keeps_the_turn_cap_and_generation_gates(conn, fake_llm):
    add_profile(conn)
    result = adopt_via_route(conn)  # 新方向：没有落点计划也能聊
    session_id = int(result["planning_session_id"])

    fake_llm(*[chat_reply(["下一问？"])] * 6)
    for index in range(6):
        main.post_plan_chat(
            PlanChatIn(planning_session_id=session_id, message=f"第 {index + 1} 句"), conn
        )
    with pytest.raises(HTTPException) as capped:
        main.post_plan_chat(
            PlanChatIn(planning_session_id=session_id, message="第七句"), conn
        )
    assert capped.value.status_code == 409  # 六轮上限（决策 36）原样生效
    assert len(fake_llm.calls) == 6  # 被拦下那句一次模型都没调

    # 生成门槛：至少聊成一轮 + ready；会话 active 才能出方案
    plan_id = ledger.create_active(conn, "plan", {"goal": "补一个落点"}, actor="user")
    result2 = adopt_via_route(conn, title="学后端", plan_id=plan_id)
    session2 = int(result2["planning_session_id"])
    with pytest.raises(HTTPException) as too_early:
        main.post_plan_blueprint(PlanBlueprintIn(planning_session_id=session2), conn)
    assert too_early.value.status_code == 409  # 一轮都没聊成
    fake_llm(chat_reply([], ready=True), blueprint_json(stage("学 HTTP", tasks=[task("读 MDN")])))
    main.post_plan_chat(
        PlanChatIn(planning_session_id=session2, message="我想做个后端"), conn
    )
    created = main.post_plan_blueprint(PlanBlueprintIn(planning_session_id=session2), conn)
    assert created["stages"][0]["title"] == "学 HTTP"


def test_planless_session_full_run_to_new_plan_approval(conn, fake_llm):
    """「新方向」的会话（OC-07 起）能出方案；批准 landing_mode=new_plan 时，
    正式计划与契约、阶段树在批准事务里一起落库，会话转正为 converted。"""
    add_profile(conn)
    result = adopt_via_route(conn)
    session_id = int(result["planning_session_id"])
    assert conn.execute("SELECT COUNT(*) AS n FROM plan").fetchone()["n"] == 0

    fake_llm(chat_reply([], ready=True), blueprint_json(stage("学 HTTP")))
    main.post_plan_chat(
        PlanChatIn(planning_session_id=session_id, message="先把成果聊清"), conn
    )
    created = main.post_plan_blueprint(PlanBlueprintIn(planning_session_id=session_id), conn)
    assert session_row(conn, session_id)["status"] == "blueprint_pending"

    approved = main.post_proposal_decide(
        created["proposal_id"],
        ProposalDecideIn(approved=True, confirm_contract=True, landing_mode="new_plan"),
        conn,
    )
    new_plan_id = int(approved["plan_id"])
    plan_row = conn.execute("SELECT * FROM plan WHERE id = ?", (new_plan_id,)).fetchone()
    assert plan_row["status"] == "active" and plan_row["completion_mode"] == "outcome"
    assert [str(row["title"]) for row in plan.get_stages(conn, new_plan_id)] == ["学 HTTP"]
    assert session_row(conn, session_id)["status"] == "converted"


def test_expired_session_refuses_messages_and_marks_itself_expired(conn):
    add_profile(conn)
    result = adopt_via_route(conn)
    session_id = int(result["planning_session_id"])
    # 把无活动期限拨到过去（模拟超期；真实过期由周期任务或懒过期触发）
    conn.execute(
        "UPDATE planning_session SET expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
        (session_id,),
    )
    conn.commit()

    with pytest.raises(HTTPException) as expired:
        main.post_plan_chat(
            PlanChatIn(planning_session_id=session_id, message="还活着吗"), conn
        )
    assert expired.value.status_code == 409
    session = session_row(conn, session_id)
    assert session["status"] == "expired"
    assert session["closed_at"] is not None
    # 过期不是否决：候选仍是 accepted，会话只读保留
    assert conn.execute(
        "SELECT status FROM candidate WHERE id = ?", (int(result["id"]),)
    ).fetchone()["status"] == "accepted"


def test_terminated_session_can_be_reopened_for_a_new_planning_round(conn, fake_llm):
    """F3：会话过期 / 放弃 / 转正之后，候选不该从此进不了规划——
    「重新开始规划」沿用原来的落点开一条新会话，旧会话只读保留。"""
    add_profile(conn)
    plan_id = ledger.create_active(conn, "plan", {"goal": "落点计划"}, actor="user")
    result = adopt_via_route(conn, plan_id=plan_id)
    candidate_id = int(result["id"])
    old_session_id = int(result["planning_session_id"])

    # 让它过期（懒过期：下一次动作时落终态）
    conn.execute(
        "UPDATE planning_session SET expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
        (old_session_id,),
    )
    conn.commit()
    with pytest.raises(HTTPException):
        main.post_plan_chat(
            PlanChatIn(planning_session_id=old_session_id, message="还活着吗"), conn
        )
    assert session_row(conn, old_session_id)["status"] == "expired"

    # 终态会话的 legacy 形态被明确挡下并指向重新开始规划（不再悄悄另开线程）
    with pytest.raises(HTTPException) as legacy_blocked:
        main.post_plan_chat(
            PlanChatIn(candidate_id=candidate_id, message="旧的形态"), conn
        )
    assert legacy_blocked.value.status_code == 409
    assert "重新开始规划" in str(legacy_blocked.value.detail)

    # 重新开始规划：沿用候选的落点，开一段新会话
    reopened = main.post_candidate_planning(candidate_id, conn=conn)
    new_session_id = int(reopened["planning_session_id"])
    assert reopened["created"] is True and new_session_id != old_session_id
    assert reopened["landing_plan_id"] == plan_id
    assert reopened["planning_status"] == "needs_blueprint"
    assert session_row(conn, new_session_id)["status"] == "active"
    # 旧会话只读保留，没有被重开
    assert session_row(conn, old_session_id)["status"] == "expired"

    # 幂等：已有活会话时再调返回同一段，不重复建
    again = main.post_candidate_planning(candidate_id, conn=conn)
    assert again["created"] is False and int(again["planning_session_id"]) == new_session_id

    # 新会话可以正常聊（老会话的只读不挡路）
    fake_llm(chat_reply([], ready=True))
    said = main.post_plan_chat(
        PlanChatIn(planning_session_id=new_session_id, message="重新开始"), conn
    )
    assert said["can_generate"] is True

    # 有了活会话之后，legacy 形态重新回到「按会话走」（填的 candidate_id 被认出来）
    fake_llm(chat_reply([], ready=True))
    legacy_said = main.post_plan_chat(
        PlanChatIn(candidate_id=candidate_id, message="接着聊"), conn
    )
    assert legacy_said["planning_session_id"] == new_session_id


def test_rejecting_a_stale_blueprint_does_not_resurrect_an_expired_session(conn, fake_llm):
    """终态会话上的过期待批稿可以驳回（留痕），但不把会话复活成 active。"""
    add_profile(conn)
    result = adopt_via_route(conn)
    session_id = int(result["planning_session_id"])
    fake_llm(chat_reply([], ready=True), blueprint_json(stage("学 HTTP")))
    main.post_plan_chat(PlanChatIn(planning_session_id=session_id, message="聊聊"), conn)
    created = main.post_plan_blueprint(PlanBlueprintIn(planning_session_id=session_id), conn)

    conn.execute(
        "UPDATE planning_session SET expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
        (session_id,),
    )
    conn.commit()
    with pytest.raises(HTTPException):
        main.post_plan_chat(PlanChatIn(planning_session_id=session_id, message="还在吗"), conn)
    assert session_row(conn, session_id)["status"] == "expired"

    rejected = main.post_proposal_decide(
        created["proposal_id"], ProposalDecideIn(approved=False, reason="旧稿不要了"), conn
    )
    assert rejected["status"] == "rejected"
    assert session_row(conn, session_id)["status"] == "expired"  # 没被复活


def test_reopen_refuses_when_the_old_landing_plan_is_gone_or_closed(conn):
    """F3：落点计划不在了 / 已收尾时如实报冲突，不悄悄把落点改成「新方向」。"""
    add_profile(conn)
    plan_id = ledger.create_active(conn, "plan", {"goal": "会被收尾"}, actor="user")
    result = adopt_via_route(conn, plan_id=plan_id)
    candidate_id = int(result["id"])
    session_id = int(result["planning_session_id"])
    advisor.set_session_status(conn, session_id, "abandoned", reason="测试：放弃这段规划")
    plan.close_plan(conn, plan_id)

    with pytest.raises(HTTPException) as conflict:
        main.post_candidate_planning(candidate_id, conn=conn)
    assert conflict.value.status_code == 409
    assert "已不是进行中" in str(conflict.value.detail)
    # 没有新会话被建出来
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM planning_session WHERE candidate_id = ? AND status = 'active'",
        (candidate_id,),
    ).fetchone()["n"] == 0


def test_reopen_refuses_a_candidate_that_was_never_adopted(conn):
    """F3：没采纳 / 不存在的候选不走这个入口（先采纳，再谈重新开始规划）。"""
    request_id = advisor.record_request(conn, "search", "问过")
    candidate_id = ledger.create_active(
        conn,
        "candidate",
        {"request_id": request_id, "title": "还没定", "why": "w", "depth_target": "够用", "rank": 1},
        actor="agent",
        reason="测试用",
    )
    with pytest.raises(HTTPException) as not_adopted:
        main.post_candidate_planning(candidate_id, conn=conn)
    assert not_adopted.value.status_code == 409
    with pytest.raises(HTTPException) as missing:
        main.post_candidate_planning(9999, conn=conn)
    assert missing.value.status_code == 404


# ---------- ⑥ 旧数据兼容：没有会话的旧候选与旧消息行 ----------

def test_legacy_accepted_candidate_without_session_still_chats(conn):
    """旧流程（采纳即建阶段时代）留下的 accepted 候选没有会话——legacy 对话路径照常可用。"""
    add_profile(conn)
    plan_id = ledger.create_active(conn, "plan", {"goal": "老计划"}, actor="user")
    request_id = advisor.record_request(conn, "search", "老一轮", plan_id)
    candidate_id = ledger.create_active(
        conn,
        "candidate",
        {"request_id": request_id, "title": "学 HTTP", "why": "w", "depth_target": "够用", "rank": 1},
        actor="agent",
        reason="测试用",
    )
    # 直接走台账标 accepted（老库事实：没有 planning_session，landing 可能为空）
    ledger.set_status(conn, "candidate", candidate_id, "accepted", actor="user", reason="老库事实")

    class FakeOperation:
        def __init__(self, _conn, _task, limit=None, transport=None):
            self.used = 0

        def chat(self, messages, provider_id=None, model=None):
            self.used += 1
            return chat_reply(["每周几小时？"])

    original = llm.Operation
    llm.Operation = FakeOperation
    try:
        said = blueprint.say(conn, candidate_id, "我想先把 HTTP 弄明白")
    finally:
        llm.Operation = original
    assert said["planning_session_id"] is None
    assert said["plan_id"] == plan_id
    rows = blueprint.thread(conn, candidate_id, plan_id)
    assert [row["role"] for row in rows] == ["user", "assistant"]
    stored = conn.execute(
        "SELECT planning_session_id FROM plan_chat WHERE candidate_id = ? AND plan_id = ?",
        (candidate_id, plan_id),
    ).fetchall()
    assert all(row["planning_session_id"] is None for row in stored)  # 旧路径的消息不带会话


def test_old_chat_rows_without_session_are_read_only_compatible(conn):
    """旧消息行（planning_session_id 为 NULL）仍按 (候选, 计划) 只读读取，不被会话查询圈走。"""
    plan_id = ledger.create_active(conn, "plan", {"goal": "老计划"}, actor="user")
    request_id = advisor.record_request(conn, "search", "老一轮", plan_id)
    candidate_id = ledger.create_active(
        conn,
        "candidate",
        {"request_id": request_id, "title": "学 HTTP", "why": "w", "depth_target": "够用", "rank": 1},
        actor="agent",
        reason="测试用",
    )
    ledger.set_status(conn, "candidate", candidate_id, "accepted", actor="user", reason="老库事实")
    conn.execute(
        "INSERT INTO plan_chat (plan_id, candidate_id, role, content, created_at)"
        " VALUES (?, ?, 'user', '旧的一句', '2026-09-01T10:00:00+00:00')",
        (plan_id, candidate_id),
    )
    conn.commit()

    legacy = blueprint.thread(conn, candidate_id, plan_id)
    assert [str(row["content"]) for row in legacy] == ["旧的一句"]
    # 会话查询圈不到它：按会话查是空集，thread_plan 也不会把哨兵/空值当成新会话的归属
    assert blueprint.session_thread(conn, 999) == []
    assert blueprint.thread_plan(conn, candidate_id) == plan_id
