"""对话式规划与蓝图（T26：SPEC 决策 36）。

覆盖四组东西：
① 对话：前提（必须已采纳）、轮数与历史字符两个上限、每轮只调 1 次不重试；
② 蓝图：落成 `pending` 提案、payload 形状、**树 = 版本**（新版把旧版标 superseded）；
③ 批准 = 按勾选建树：只建勾中的、没勾的直接丢弃；同名阶段**复用**（采纳时自动建的那条）；
④ 建树前的查重：蓝图内任务重名、撞上已有同名任务、下标超界，一律在建之前拦下。

一律用假上游打桩（同 `test_candidates.py`），不打真实接口、不花钱。
"""

from __future__ import annotations

import json

import pytest

from app import advisor, blueprint, db, ledger, llm, plan, proposals

BLUEPRINT_KIND = blueprint.BLUEPRINT_KIND


class ScriptedTransport:
    """按脚本依次返回的假上游；脚本用完了还被调就直接报错（防悄悄多调一次）。"""

    def __init__(self, *texts: str) -> None:
        self._texts = list(texts)
        self.seen: list[dict] = []

    def __call__(self, url: str, headers: dict, payload: dict):
        self.seen.append({"url": url, "headers": headers, "payload": payload})
        if not self._texts:
            raise AssertionError("假上游被多调了一次：脚本里的回答已经用完")
        return 200, {
            "choices": [{"message": {"content": self._texts.pop(0)}}],
            "usage": {"prompt_tokens": 31, "completion_tokens": 17},
        }


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "test.db"
    db.init(path)
    connection = db.connect(path)
    yield connection
    connection.close()


def make_provider(conn) -> int:
    provider_id = llm.create_provider(
        conn,
        name="假提供商",
        base_url="http://127.0.0.1:9999/v1",
        api_key="sk-fake-1234567890abcd",
        default_model="fake-model",
    )
    llm.update_provider(conn, provider_id, set_as_default=True)
    return provider_id


def add_profile(conn, category: str = "long_axis", content: str = "通用工程基础") -> int:
    return ledger.create_active(
        conn, "profile_item", {"category": category, "content": content}, actor="user"
    )


def chat_reply(questions: list[str], *, ready: bool = False, note: str = "先问几句") -> str:
    return json.dumps({"questions": questions, "ready": ready, "note": note}, ensure_ascii=False)


def blueprint_json(*stages: dict, goal: str = "能自己写后端接口") -> str:
    return json.dumps({"goal": goal, "stages": list(stages)}, ensure_ascii=False)


def stage(title: str, *, deliverable: str = "交一个能跑通的小东西", tasks=None, why="先打基础"):
    return {
        "title": title,
        "deliverable": deliverable,
        "why": why,
        "tasks": list(tasks or []),
    }


def task(title: str, due: str | None = None) -> dict:
    return {"title": title} if due is None else {"title": title, "due_date": due}


def adopted_candidate(conn, title: str = "学 HTTP") -> tuple[int, int]:
    """建一个计划 + 一条**走真正采纳路径**的候选，返回 (候选 id, 计划 id)。

    走真路径是因为采纳会顺带建一个同名阶段——那正是蓝图要复用的那条，测试要看到它。
    """
    plan_id = ledger.create_active(conn, "plan", {"goal": f"计划：{title}"}, actor="user")
    request_id = advisor.record_request(conn, "search", "我不知道该学什么", plan_id)
    candidate_id = ledger.create_active(
        conn,
        "candidate",
        {
            "request_id": request_id,
            "title": title,
            "why": "对主线有直接帮助",
            "depth_target": "够用",
            "rank": 1,
        },
        actor="agent",
        reason="测试用",
    )
    advisor.decide_candidate(conn, candidate_id, accept=True)
    return candidate_id, plan_id


def ready_thread(conn, title: str = "学 HTTP", *, transport=None) -> tuple[int, int]:
    """一条已经聊过一轮的对话（`generate_blueprint` 的前置条件）。"""
    candidate_id, plan_id = adopted_candidate(conn, title)
    blueprint.say(
        conn, candidate_id, "我想先把 HTTP 弄明白", transport=transport or ScriptedTransport(chat_reply(["每周几小时？"]))
    )
    return candidate_id, plan_id


def free_candidate_adopted_into(conn, plan_id: int, title: str = "轻量后端入门") -> int:
    """一条「新方向」的候选（请求没有计划归属）被显式采纳进某个计划。

    这正是前端刷新后的样子：落点只存在于采纳那一刻的响应里，库里候选自己查不出来。
    """
    request_id = advisor.record_request(conn, "search", "我不知道该学什么")  # 没有归属
    candidate_id = ledger.create_active(
        conn,
        "candidate",
        {
            "request_id": request_id,
            "title": title,
            "why": "对主线有直接帮助",
            "depth_target": "够用",
            "rank": 1,
        },
        actor="agent",
        reason="测试用",
    )
    advisor.decide_candidate(conn, candidate_id, accept=True, plan_id=plan_id)
    return candidate_id


def open_proposal(conn, plan_id: int, candidate_id: int, *stages: dict) -> int:
    """直接落一条待裁定蓝图——专测裁定与建树时用它，省掉一轮对话。"""
    payload = {
        "plan_id": plan_id,
        "candidate_id": candidate_id,
        "goal": "能自己写后端接口",
        "stages": list(stages),
    }
    return ledger.create_active(
        conn,
        "proposal",
        {"kind": BLUEPRINT_KIND, "payload": json.dumps(payload, ensure_ascii=False), "reason": "测试"},
        actor="agent",
    )


def task_titles(conn, stage_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT title FROM plan_node WHERE parent_id = ? ORDER BY sort_order, id", (stage_id,)
    ).fetchall()
    return [str(row["title"]) for row in rows]


def stage_titles(conn, plan_id: int) -> list[str]:
    return [str(row["title"]) for row in plan.get_stages(conn, plan_id)]


# ---------- 对话：前提、轮数、一次调用 ----------

def test_chat_needs_an_adopted_candidate(conn):
    add_profile(conn)
    plan_id = ledger.create_active(conn, "plan", {"goal": "某计划"}, actor="user")
    request_id = advisor.record_request(conn, "search", "问过", plan_id)
    proposed = ledger.create_active(
        conn,
        "candidate",
        {"request_id": request_id, "title": "还没采纳", "why": "w", "depth_target": "够用", "rank": 1},
        actor="agent",
        reason="测试用",
    )

    with pytest.raises(blueprint.BlueprintConflict):  # 没采纳就来聊
        blueprint.say(conn, proposed, "我先说说")
    with pytest.raises(blueprint.BlueprintNotFound):
        blueprint.say(conn, 999, "我先说说")
    assert conn.execute("SELECT COUNT(*) AS n FROM plan_chat").fetchone()["n"] == 0


def test_first_view_is_empty_and_cannot_generate_yet(conn):
    candidate_id, plan_id = adopted_candidate(conn)

    view = blueprint.view(conn, candidate_id)

    assert view["plan_id"] == plan_id  # 计划归属取自候选（决策 33 ②）
    assert view["messages"] == []
    assert view["turns_used"] == 0
    assert view["max_turns"] == blueprint.MAX_TURNS
    assert view["can_generate"] is False  # 还没聊过，不许出方案
    assert view["blueprint"] is None


def test_one_turn_records_both_sides_and_carries_the_context(conn):
    make_provider(conn)
    add_profile(conn)
    candidate_id, plan_id = adopted_candidate(conn)
    transport = ScriptedTransport(chat_reply(["你每周能稳定投入几小时？", "先做哪一块？"]))

    done = blueprint.say(conn, candidate_id, "我想先把 HTTP 弄明白", transport=transport)

    assert done["turns_used"] == 1 and done["can_generate"] is True
    assert done["reply"]["questions"] == ["你每周能稳定投入几小时？", "先做哪一块？"]
    rows = blueprint.thread(conn, candidate_id, plan_id)
    assert [row["role"] for row in rows] == ["user", "assistant"]
    assert rows[0]["content"] == "我想先把 HTTP 弄明白"
    assert json.loads(rows[1]["content"])["ready"] is False  # 助手那侧存 JSON 原文

    # 背景那一段要带上档案、计划目标、以及「已有阶段别重复建」的提醒
    background = transport.seen[0]["payload"]["messages"][1]["content"]
    assert "通用工程基础" in background and "计划：学 HTTP" in background
    assert "不要重复建" in background


def test_turns_are_capped_at_six(conn):
    make_provider(conn)
    add_profile(conn)
    candidate_id, _ = adopted_candidate(conn)
    transport = ScriptedTransport(*[chat_reply([f"第 {index} 问？"]) for index in range(7)])

    for index in range(blueprint.MAX_TURNS):
        blueprint.say(conn, candidate_id, f"第 {index} 句回答", transport=transport)

    with pytest.raises(blueprint.BlueprintConflict):
        blueprint.say(conn, candidate_id, "还想再聊", transport=transport)

    assert len(transport.seen) == blueprint.MAX_TURNS  # 到顶那一下没再调模型


def test_history_is_truncated_from_the_earliest(conn):
    make_provider(conn)
    add_profile(conn)
    candidate_id, _ = adopted_candidate(conn)
    transport = ScriptedTransport(chat_reply(["接着问？"]), chat_reply(["接着问？"]))

    blueprint.say(conn, candidate_id, "AAA" * 1300, transport=transport)  # 一句话就超上限
    blueprint.say(conn, candidate_id, "BBB" * 100, transport=transport)

    history = transport.seen[1]["payload"]["messages"][2:]
    assert any("BBB" in item["content"] for item in history)
    assert not any("AAA" in item["content"] for item in history)  # 最早的被截掉


def test_chat_without_a_profile_never_calls_the_model(conn):
    make_provider(conn)  # 有 provider，但档案是空的
    candidate_id, _ = adopted_candidate(conn)
    transport = ScriptedTransport(chat_reply(["问一句？"]))

    with pytest.raises(blueprint.BlueprintError):
        blueprint.say(conn, candidate_id, "我先说说", transport=transport)

    assert transport.seen == []
    assert conn.execute("SELECT COUNT(*) AS n FROM plan_chat").fetchone()["n"] == 0


# ---------- 计划归属：已有对话记着的也算数（2026-09-18 修的真实 bug） ----------
#
# 起因：一条「新方向」的候选被显式采纳进某个计划后，落点只活在当刻的响应里；页面刷新
# 一次，前端就传不出 plan_id 了。而当时只有 `view` 会去读 `plan_chat` 记着的归属，
# 「聊一句」和「出方案」只看候选自带的——于是「对话聊成了、最后一步说没有计划归属」。

def test_blueprint_without_plan_id_falls_back_to_the_recorded_thread(conn):
    make_provider(conn)
    add_profile(conn)
    plan_id = ledger.create_active(conn, "plan", {"goal": "轻量后端与数据库入门"}, actor="user")
    candidate_id = free_candidate_adopted_into(conn, plan_id)
    transport = ScriptedTransport(
        chat_reply(["每周几小时？"]),
        blueprint_json(stage("轻量后端入门", tasks=[task("读 MDN")])),
    )

    blueprint.say(conn, candidate_id, "每周 6 小时", plan_id=plan_id, transport=transport)
    # ↓ 模拟前端刷新：内存里的落点没了，plan_id 传 null——这时该从对话里认出来
    created = blueprint.generate_blueprint(conn, candidate_id, transport=transport)

    assert created["plan_id"] == plan_id
    payload = json.loads(
        conn.execute("SELECT payload FROM proposal WHERE id = ?", (created["proposal_id"],)).fetchone()["payload"]
    )
    assert payload["plan_id"] == plan_id
    # 蓝图建树也落在同一个计划里
    result = proposals.decide(conn, created["proposal_id"], approved=True)
    assert result["built"]["plan_id"] == plan_id


def test_another_turn_also_falls_back_to_the_recorded_thread(conn):
    make_provider(conn)
    add_profile(conn)
    plan_id = ledger.create_active(conn, "plan", {"goal": "某计划"}, actor="user")
    candidate_id = free_candidate_adopted_into(conn, plan_id)
    transport = ScriptedTransport(chat_reply(["第一问？"]), chat_reply(["第二问？"]))

    blueprint.say(conn, candidate_id, "第一句", plan_id=plan_id, transport=transport)
    done = blueprint.say(conn, candidate_id, "第二句", transport=transport)  # 不带 plan_id

    assert done["plan_id"] == plan_id
    assert blueprint.turns_used(conn, candidate_id, plan_id) == 2


def test_a_recorded_thread_refuses_a_different_plan(conn):
    """已经记在计划 A 名下的对话，不能改口说是计划 B——否则蓝图会建到别的计划里。"""
    make_provider(conn)
    add_profile(conn)
    plan_id = ledger.create_active(conn, "plan", {"goal": "A"}, actor="user")
    other = ledger.create_active(conn, "plan", {"goal": "B"}, actor="user")
    candidate_id = free_candidate_adopted_into(conn, plan_id)
    transport = ScriptedTransport(chat_reply(["第一问？"]))

    blueprint.say(conn, candidate_id, "第一句", plan_id=plan_id, transport=transport)
    with pytest.raises(blueprint.BlueprintConflict):
        blueprint.say(conn, candidate_id, "第二句", plan_id=other, transport=transport)
    with pytest.raises(blueprint.BlueprintConflict):
        blueprint.generate_blueprint(conn, candidate_id, plan_id=other, transport=transport)

    assert blueprint.thread_plan(conn, candidate_id) == plan_id  # 记着的没被改口


def test_view_reports_the_plan_recorded_by_the_thread(conn):
    plan_id = ledger.create_active(conn, "plan", {"goal": "某计划"}, actor="user")
    candidate_id = free_candidate_adopted_into(conn, plan_id)
    make_provider(conn)
    add_profile(conn)
    transport = ScriptedTransport(chat_reply(["第一问？"]))

    # 还没聊过：候选自己查不出归属，界面就得先问用户（见 `view` 的 plan_id 为 null）
    assert blueprint.view(conn, candidate_id)["plan_id"] is None

    blueprint.say(conn, candidate_id, "第一句", plan_id=plan_id, transport=transport)

    assert blueprint.view(conn, candidate_id)["plan_id"] == plan_id  # 之后从对话里认出来


def test_invalid_reply_costs_the_turn_but_keeps_your_words(conn):
    """每轮只给 1 次调用（决策 6 修订），所以不合格就报错、不重试——但你的话留着。"""
    make_provider(conn)
    add_profile(conn)
    candidate_id, plan_id = adopted_candidate(conn)
    transport = ScriptedTransport(json.dumps({"questions": [], "ready": False}))  # 什么都没说

    with pytest.raises(blueprint.BlueprintError):
        blueprint.say(conn, candidate_id, "我还是想学这个", transport=transport)

    assert len(transport.seen) == 1
    rows = blueprint.thread(conn, candidate_id, plan_id)
    assert [row["role"] for row in rows] == ["user"]  # 你那句话还在
    assert blueprint.turns_used(conn, candidate_id, plan_id) == 1


# ---------- 蓝图：落库与版本取代 ----------

def test_blueprint_needs_a_turn_first(conn):
    make_provider(conn)
    add_profile(conn)
    candidate_id, _ = adopted_candidate(conn)
    transport = ScriptedTransport(blueprint_json(stage("学 HTTP")))

    with pytest.raises(blueprint.BlueprintConflict):  # 先聊一轮再出方案
        blueprint.generate_blueprint(conn, candidate_id, transport=transport)

    assert transport.seen == []


def test_blueprint_lands_as_a_pending_proposal(conn):
    make_provider(conn)
    add_profile(conn)
    transport = ScriptedTransport(
        chat_reply(["每周几小时？"]),
        blueprint_json(
            stage("学 HTTP", tasks=[task("读 MDN", due="2026-10-01"), task("写一个接口")]),
            stage("上线", deliverable="一个能访问的地址"),
        ),
    )
    candidate_id, plan_id = ready_thread(conn, transport=transport)

    created = blueprint.generate_blueprint(conn, candidate_id, transport=transport)

    row = conn.execute(
        "SELECT * FROM proposal WHERE id = ?", (created["proposal_id"],)
    ).fetchone()
    assert row["kind"] == BLUEPRINT_KIND and row["status"] == "pending"
    payload = json.loads(row["payload"])
    assert payload["plan_id"] == plan_id and payload["candidate_id"] == candidate_id
    assert payload["goal"] == "能自己写后端接口"
    assert [item["title"] for item in payload["stages"]] == ["学 HTTP", "上线"]
    assert payload["stages"][0]["tasks"][0]["due_date"] == "2026-10-01"
    assert created["version"] == 1 and created["superseded_ids"] == []
    # 它就在待裁定列表里（前端按 kind 分流渲染）
    pending = proposals.list_pending(conn, BLUEPRINT_KIND)["proposals"]
    assert [item["id"] for item in pending] == [created["proposal_id"]]


def test_a_new_version_supersedes_the_old_one_and_leaves_a_trace(conn):
    make_provider(conn)
    add_profile(conn)
    transport = ScriptedTransport(
        chat_reply(["每周几小时？"]),
        blueprint_json(stage("学 HTTP", tasks=[task("读 MDN")])),
        blueprint_json(stage("学 HTTP", tasks=[task("读 MDN"), task("写接口")])),
    )
    candidate_id, plan_id = ready_thread(conn, transport=transport)

    first = blueprint.generate_blueprint(conn, candidate_id, transport=transport)
    second = blueprint.generate_blueprint(conn, candidate_id, transport=transport)

    assert second["version"] == 2
    assert second["superseded_ids"] == [first["proposal_id"]]
    old = conn.execute("SELECT * FROM proposal WHERE id = ?", (first["proposal_id"],)).fetchone()
    assert old["status"] == "superseded"  # 业务终态，不是台账的取代
    events = ledger.history(conn, "proposal", first["proposal_id"])
    assert [event["change_type"] for event in events] == ["create", "status_change"]
    assert events[-1]["after_value"] == "superseded"
    # 同一计划同时只有一份待裁定蓝图
    assert [item["id"] for item in proposals.list_pending(conn, BLUEPRINT_KIND)["proposals"]] == [
        second["proposal_id"]
    ]
    with pytest.raises(proposals.ProposalConflict):  # 被取代的不能再裁
        proposals.decide(conn, first["proposal_id"], approved=True)
    assert blueprint.view(conn, candidate_id)["blueprint"]["id"] == second["proposal_id"]


def test_invalid_due_date_gets_one_retry(conn):
    make_provider(conn)
    add_profile(conn)
    transport = ScriptedTransport(
        chat_reply(["每周几小时？"]),
        blueprint_json(stage("学 HTTP", tasks=[task("读 MDN", due="下周三")])),
        blueprint_json(stage("学 HTTP", tasks=[task("读 MDN", due="2026-10-01")])),
    )
    candidate_id, _ = ready_thread(conn, transport=transport)

    created = blueprint.generate_blueprint(conn, candidate_id, transport=transport)

    assert created["attempts"] == 2
    assert created["stages"][0]["tasks"][0]["due_date"] == "2026-10-01"


# ---------- 批准 = 按勾选建树 ----------

def test_approval_builds_only_the_ticked_part(conn):
    candidate_id, plan_id = adopted_candidate(conn, title="学 HTTP")
    proposal_id = open_proposal(
        conn,
        plan_id,
        candidate_id,
        stage("学 HTTP", tasks=[task("读 MDN"), task("写一个接口"), task("加错误处理")]),
        stage("做一个小服务", tasks=[task("起一个服务")]),
        stage("上线", deliverable="一个能访问的地址"),
    )

    result = proposals.decide(conn, proposal_id, approved=True, selected=["0.1", "1"])

    assert result["effect"] == "blueprint_built"
    assert [item["title"] for item in result["built"]["stages"]] == ["做一个小服务"]
    assert [item["title"] for item in result["built"]["tasks"]] == ["写一个接口", "起一个服务"]
    assert stage_titles(conn, plan_id) == ["学 HTTP", "做一个小服务"]  # 「上线」整段被丢弃
    stages = plan.get_stages(conn, plan_id)
    assert task_titles(conn, int(stages[0]["id"])) == ["写一个接口"]  # 0.1 之外的没建
    assert task_titles(conn, int(stages[1]["id"])) == ["起一个服务"]
    # 复用「学 HTTP」时它要交的东西写不进去——这条限制要明说，不能悄悄吞掉
    assert result["built"]["notes"] and "没能写进去" in result["built"]["notes"][0]


def test_no_selection_means_the_whole_blueprint(conn):
    candidate_id, plan_id = adopted_candidate(conn, title="学 HTTP")
    proposal_id = open_proposal(
        conn,
        plan_id,
        candidate_id,
        stage("学 HTTP", tasks=[task("读 MDN")]),
        stage("上线", deliverable="一个能访问的地址"),
    )

    result = proposals.decide(conn, proposal_id, approved=True)

    assert stage_titles(conn, plan_id) == ["学 HTTP", "上线"]
    stages = plan.get_stages(conn, plan_id)
    assert task_titles(conn, int(stages[0]["id"])) == ["读 MDN"]
    assert stages[1]["deliverable"] == "一个能访问的地址"  # 新阶段的交付物真的写进去了


def test_a_ticked_stage_keeps_all_of_its_tasks(conn):
    candidate_id, plan_id = adopted_candidate(conn, title="学 HTTP")
    proposal_id = open_proposal(
        conn, plan_id, candidate_id, stage("学 HTTP", tasks=[task("A"), task("B")])
    )

    proposals.decide(conn, proposal_id, approved=True, selected=["0"])

    stages = plan.get_stages(conn, plan_id)
    assert task_titles(conn, int(stages[0]["id"])) == ["A", "B"]


def test_nothing_ticked_is_refused_and_changes_nothing(conn):
    candidate_id, plan_id = adopted_candidate(conn, title="学 HTTP")
    proposal_id = open_proposal(conn, plan_id, candidate_id, stage("学 HTTP", tasks=[task("A")]))

    with pytest.raises(blueprint.BlueprintError):
        proposals.decide(conn, proposal_id, approved=True, selected=[""])

    assert stage_titles(conn, plan_id) == ["学 HTTP"]  # 还是采纳时那一条
    row = conn.execute("SELECT status FROM proposal WHERE id = ?", (proposal_id,)).fetchone()
    assert row["status"] == "pending"  # 提案没被改成 accepted，可重裁


def test_out_of_range_selection_is_refused(conn):
    candidate_id, plan_id = adopted_candidate(conn, title="学 HTTP")
    proposal_id = open_proposal(conn, plan_id, candidate_id, stage("学 HTTP", tasks=[task("A")]))

    with pytest.raises(blueprint.BlueprintError):
        proposals.decide(conn, proposal_id, approved=True, selected=["0.9"])
    with pytest.raises(blueprint.BlueprintError):
        proposals.decide(conn, proposal_id, approved=True, selected=["5"])
    with pytest.raises(blueprint.BlueprintError):
        proposals.decide(conn, proposal_id, approved=True, selected=["二"])
    assert conn.execute("SELECT COUNT(*) AS n FROM plan_node").fetchone()["n"] == 1  # 只有采纳那条阶段


def test_rejecting_a_blueprint_builds_nothing(conn):
    candidate_id, plan_id = adopted_candidate(conn, title="学 HTTP")
    proposal_id = open_proposal(conn, plan_id, candidate_id, stage("上线"))

    result = proposals.decide(conn, proposal_id, approved=False, reason="阶段拆得不对")

    assert result["effect"] == "recorded_only" and result["built"] is None
    assert stage_titles(conn, plan_id) == ["学 HTTP"]
    assert result["status"] == "rejected"


# ---------- 建树前的查重（一条都不写的预检） ----------

def test_duplicate_task_titles_inside_a_stage_are_refused(conn):
    candidate_id, plan_id = adopted_candidate(conn, title="学 HTTP")
    proposal_id = open_proposal(
        conn, plan_id, candidate_id, stage("做服务", tasks=[task("起一个服务"), task("起一个服务")])
    )

    with pytest.raises(blueprint.BlueprintError):
        proposals.decide(conn, proposal_id, approved=True)

    assert stage_titles(conn, plan_id) == ["学 HTTP"]  # 一个节点都没建


def test_a_task_clashing_with_an_existing_open_one_is_refused(conn):
    candidate_id, plan_id = adopted_candidate(conn, title="学 HTTP")
    adopted = plan.get_stages(conn, plan_id)[0]
    plan.add_node(conn, plan_id, "task", "读 MDN", parent_id=int(adopted["id"]))
    proposal_id = open_proposal(
        conn, plan_id, candidate_id, stage("学 HTTP", tasks=[task("读 MDN")])
    )

    with pytest.raises(blueprint.BlueprintError):
        proposals.decide(conn, proposal_id, approved=True)


def test_building_into_a_closed_plan_is_refused(conn):
    candidate_id, plan_id = adopted_candidate(conn, title="学 HTTP")
    proposal_id = open_proposal(conn, plan_id, candidate_id, stage("上线"))
    plan.close_plan(conn, plan_id)

    with pytest.raises(blueprint.BlueprintConflict):
        proposals.decide(conn, proposal_id, approved=True)

    row = conn.execute("SELECT status FROM proposal WHERE id = ?", (proposal_id,)).fetchone()
    assert row["status"] == "pending"