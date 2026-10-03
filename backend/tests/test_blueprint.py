"""对话式规划与蓝图（T26：SPEC 决策 36）。

覆盖四组东西：
① 对话：前提（必须已采纳）、轮数与历史字符两个上限、每轮只调 1 次不重试；
② 蓝图：落成 `pending` 提案、payload 形状、**树 = 版本**（新版把旧版标 superseded）；
③ 批准 = 按勾选建树：只建勾中的、没勾的直接丢弃；同名阶段**复用**（计划里已有的那条）；
④ 建树前的查重：蓝图内任务重名、撞上已有同名任务、下标超界，一律在建之前拦下；
⑤ 生成门槛与预检（2026-09-28 整改 III-01/III-02）：最后一轮必须聊成、待批稿只给
   出它的那条候选看、任务撞现有计划里开着的同名任务在生成期拦下带原因重试。

一律用假上游打桩（同 `test_candidates.py`），不打真实接口、不花钱。
注意（2026-10-01 成果闭环 OC-05）：采纳不再自动建同名阶段——需要「计划里已有同名阶段」
的用例（复用 / 撞车）一律先手工 `plan.add_node` 建出来。
"""

from __future__ import annotations

import json

import pytest

from app import advisor, blueprint, contract, db, ledger, llm, plan, proposals

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


def blueprint_json(*stages: dict, goal: str = "能自己写后端接口", contract: dict | None = None) -> str:
    return json.dumps(
        {
            "goal": goal,
            "contract": contract or contract_json(),
            "stages": [v2_stage(item) for item in stages],
        },
        ensure_ascii=False,
    )


def review_json(*points: dict, summary: str = "计划与已知信息相符") -> str:
    return json.dumps({"summary": summary, "points": list(points)}, ensure_ascii=False)


def agree_point(
    target: str = "第 1 阶段",
    point: str = "起点与档案相符",
    reason: str = "档案显示已有 Python 项目经验",
) -> dict:
    return {"stance": "agree", "target": target, "point": point, "reason": reason}


def disagree_point(
    target: str, point: str, reason: str, adjustment: str, severity: str = "revise"
) -> dict:
    return {
        "stance": "disagree",
        "target": target,
        "point": point,
        "reason": reason,
        "adjustment": adjustment,
        "severity": severity,
    }


def revision_json(*stages: dict, goal: str = "能自己写后端接口", resolutions=None, contract: dict | None = None) -> str:
    return json.dumps(
        {
            "goal": goal,
            "contract": contract or contract_json(),
            "stages": [v2_stage(item) for item in stages],
            "review_resolution": list(resolutions or []),
        },
        ensure_ascii=False,
    )


# 蓝图 v2（OC-06）的契约底稿：oc-1 为必需条件（所有默认阶段都承接它，覆盖检查通过），
# oc-2 非必需；契约校验要求 2–5 条验收条件，所以底稿放两条。
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


def contract_json(**over) -> dict:
    return {**BASE_CONTRACT, **over}


def v2_stage(stage_dict: dict) -> dict:
    """把测试里的阶段底稿补成 v2 形状（缺的字段按能通过校验的默认值补齐）。"""
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

    OC-05 起采纳不再建阶段：只把候选标 accepted、记规划落点并开一条 planning_session。
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


def legacy_adopted_candidate(conn, title: str = "学 HTTP") -> tuple[int, int]:
    """旧流程（OC-05 之前）留下的已采纳候选：**没有 planning_session**。

    「树 = 版本」的计划级取代语义只在这种老数据上还成立——进过规划的候选一律按会话走。
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
    ledger.set_status(conn, "candidate", candidate_id, "accepted", actor="user", reason="旧库事实")
    return candidate_id, plan_id


def ready_thread(conn, title: str = "学 HTTP", *, transport=None) -> tuple[int, int]:
    """一条已经聊过一轮的对话（`generate_blueprint` 的前置条件）。"""
    candidate_id, plan_id = adopted_candidate(conn, title)
    blueprint.say(
        conn, candidate_id, "我想先把 HTTP 弄明白", transport=transport or ScriptedTransport(chat_reply([], ready=True))
    )
    return candidate_id, plan_id


def free_candidate_adopted_into(conn, plan_id: int, title: str = "轻量后端入门") -> int:
    """一条「新方向」的候选（请求没有计划归属）被显式采纳进某个计划。

    落点记在候选自己身上（`candidate.landing_plan_id`，OC-05 起语义是「规划落点」），
    刷新一次页面对话照样定得下来。
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


def open_payload(conn, plan_id: int, candidate_id: int, *stages: dict) -> dict:
    """直接构造一份 **v2** 蓝图 payload（不经模型）——build_tree 直测与提案落库共用。"""
    return {
        "version": 2,
        "plan_id": plan_id,
        "candidate_id": candidate_id,
        "planning_session_id": None,
        "goal": "能自己写后端接口",
        "contract": contract_json(),
        "stages": [v2_stage(item) for item in stages],
    }


def open_proposal(conn, plan_id: int, candidate_id: int, *stages: dict) -> int:
    """直接落一条待裁定的 **v2** 蓝图提案——省掉一轮对话。"""
    payload = open_payload(conn, plan_id, candidate_id, *stages)
    return ledger.create_active(
        conn,
        "proposal",
        {"kind": BLUEPRINT_KIND, "payload": json.dumps(payload, ensure_ascii=False), "reason": "测试"},
        actor="agent",
    )


def open_v1_proposal(conn, plan_id: int, candidate_id: int, *stages: dict) -> int:
    """落一条**旧 v1** 蓝图提案（没有 version/contract）——测批准拒绝与只读兼容。"""
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
        "SELECT title FROM plan_node WHERE parent_id = ? AND level = 'task' ORDER BY sort_order, id",
        (stage_id,),
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

    assert done["turns_used"] == 1 and done["can_generate"] is False
    assert done["reply"]["questions"] == ["你每周能稳定投入几小时？", "先做哪一块？"]
    rows = blueprint.thread(conn, candidate_id, plan_id)
    assert [row["role"] for row in rows] == ["user", "assistant"]
    assert blueprint.view(conn, candidate_id)["can_generate"] is False
    assert rows[0]["content"] == "我想先把 HTTP 弄明白"
    assert json.loads(rows[1]["content"])["ready"] is False  # 助手那侧存 JSON 原文

    # 背景那一段要带上档案、计划目标，并如实说「还没有阶段」（OC-05：采纳不建阶段）
    background = transport.seen[0]["payload"]["messages"][1]["content"]
    assert "通用工程基础" in background and "计划：学 HTTP" in background
    assert "还没有阶段" in background


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
        chat_reply([], ready=True),
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
    # 蓝图建树也落在同一个计划里（v2 批准走原子链路前的建树直测）
    result = blueprint.build_tree(conn, payload)
    assert result["plan_id"] == plan_id


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

    # 采纳时记下的落点（T37）：刷新页面照样定得下来，不必再问用户一遍
    assert blueprint.view(conn, candidate_id)["plan_id"] == plan_id

    blueprint.say(conn, candidate_id, "第一句", plan_id=plan_id, transport=transport)

    assert blueprint.view(conn, candidate_id)["plan_id"] == plan_id  # 之后从对话里也认
    assert blueprint.thread_plan(conn, candidate_id) == plan_id  # 对话里记了一份


def test_a_recorded_thread_stops_when_its_plan_is_no_longer_running(conn):
    """对话记着的计划要是之后收尾了，下一句就该拦下——别等到批准那一步才说。

    这是「从对话里认归属」那一档必须自带的核对：认出来之后不看计划状态，就会在「出方案」
    时落一条永远批不了的蓝图提案（批准那一步才拒），白聊一场。
    """
    make_provider(conn)
    add_profile(conn)
    plan_id = ledger.create_active(conn, "plan", {"goal": "A"}, actor="user")
    candidate_id = free_candidate_adopted_into(conn, plan_id)
    transport = ScriptedTransport(chat_reply(["第一问？"]))
    blueprint.say(conn, candidate_id, "第一句", plan_id=plan_id, transport=transport)
    plan.close_plan(conn, plan_id)

    with pytest.raises(blueprint.BlueprintConflict):  # 聊一句
        blueprint.say(conn, candidate_id, "第二句", transport=transport)
    with pytest.raises(blueprint.BlueprintConflict):  # 出方案
        blueprint.generate_blueprint(conn, candidate_id, transport=transport)

    assert conn.execute("SELECT COUNT(*) AS n FROM proposal").fetchone()["n"] == 0
    assert blueprint.turns_used(conn, candidate_id, plan_id) == 1  # 被拦下那句没记进去


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
        chat_reply([], ready=True),
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
    assert payload["mode"] == "standard" and "review" not in payload
    assert created["mode"] == "standard" and created["review"] is None
    assert payload["goal"] == "能自己写后端接口"
    assert [item["title"] for item in payload["stages"]] == ["学 HTTP", "上线"]
    assert payload["stages"][0]["tasks"][0]["due_date"] == "2026-10-01"
    assert created["version"] == 1 and created["superseded_ids"] == []
    # 它就在待裁定列表里（前端按 kind 分流渲染）
    pending = proposals.list_pending(conn, BLUEPRINT_KIND)["proposals"]
    assert [item["id"] for item in pending] == [created["proposal_id"]]


def test_enhanced_blueprint_passes_after_independent_review(conn):
    make_provider(conn)
    add_profile(conn, content="已做过 Python 命令行小项目")
    transport = ScriptedTransport(
        chat_reply([], ready=True),
        blueprint_json(stage("复习 HTTP", tasks=[task("读请求与响应") ])),
        review_json(agree_point(), summary="水平判断有档案依据"),
        review_json(summary="阶段衔接与任务粒度站得住"),
    )
    candidate_id, _ = ready_thread(conn, transport=transport)

    created = blueprint.generate_blueprint(
        conn, candidate_id, mode="enhanced", transport=transport
    )

    row = conn.execute("SELECT payload FROM proposal WHERE id = ?", (created["proposal_id"],)).fetchone()
    payload = json.loads(row["payload"])
    assert created["mode"] == "enhanced" and created["calls"] == 3
    review = payload["review"]
    assert review["mode"] == "enhanced" and review["initial"] is None
    assert [reviewer["key"] for reviewer in review["reviewers"]] == ["level", "structure"]
    assert all(reviewer["stance"] == "agree" for reviewer in review["reviewers"])
    # 赞同也要写清认可了哪一点
    assert review["reviewers"][0]["points"][0]["point"] == "起点与档案相符"
    assert len(transport.seen) == 4  # 含建线程的一次对话调用
    assert "水平核对员" in transport.seen[2]["payload"]["messages"][0]["content"]
    assert "结构审查员" in transport.seen[3]["payload"]["messages"][0]["content"]
    # 两位审查员互相看不到对方的结论：第二位的输入里没有第一位的回答
    assert "水平判断有档案依据" not in transport.seen[3]["payload"]["messages"][-1]["content"]


def test_enhanced_blueprint_revises_once_and_keeps_audit_details(conn):
    make_provider(conn)
    add_profile(conn, content="已做过 Python 命令行小项目")
    revised = stage("用现有项目验证 HTTP", tasks=[task("为项目增加一个 HTTP 请求")])
    transport = ScriptedTransport(
        chat_reply([], ready=True),
        blueprint_json(stage("复习命令行", tasks=[task("从零学习 Python 命令行") ])),
        review_json(agree_point(), summary="水平判断有档案依据"),
        review_json(
            disagree_point(
                target="第 1 阶段 / 任务 1",
                point="把已掌握的命令行基础安排成从零学习",
                reason="档案：已做过 Python 命令行小项目",
                adjustment="删除重复入门任务，直接用现有项目验证 HTTP 基础",
            ),
            summary="发现一处与已有经验重复的任务",
        ),
        revision_json(
            revised,
            resolutions=[
                {
                    "reviewer_key": "structure",
                    "point_index": 0,
                    "resolution": "移除重复入门任务，改为项目练习",
                }
            ],
        ),
    )
    candidate_id, _ = ready_thread(conn, transport=transport)

    created = blueprint.generate_blueprint(
        conn, candidate_id, mode="enhanced", transport=transport
    )

    payload = json.loads(
        conn.execute("SELECT payload FROM proposal WHERE id = ?", (created["proposal_id"],)).fetchone()["payload"]
    )
    review = payload["review"]
    assert created["calls"] == 4 and created["mode"] == "enhanced"
    assert created["stages"][0]["title"] == "用现有项目验证 HTTP"
    assert review["initial"]["stages"][0]["title"] == "复习命令行"
    structure = next(item for item in review["reviewers"] if item["key"] == "structure")
    assert structure["stance"] == "disagree"
    assert structure["points"][0]["target"] == "第 1 阶段 / 任务 1"
    assert structure["points"][0]["adjustment"] == "删除重复入门任务，直接用现有项目验证 HTTP 基础"
    assert review["revision_resolution"] == [
        {"reviewer_key": "structure", "point_index": 0, "resolution": "移除重复入门任务，改为项目练习"}
    ]
    assert "把已掌握的命令行基础" in transport.seen[-1]["payload"]["messages"][-1]["content"]


def test_enhanced_blueprint_repairs_a_known_plan_conflict_even_if_reviewers_miss_it(conn):
    make_provider(conn)
    add_profile(conn)
    candidate_id, plan_id = adopted_candidate(conn)
    existing_stage = plan.add_node(conn, plan_id, "stage", "学 HTTP")  # 撞车的既有阶段
    plan.add_node(conn, plan_id, "task", "读 MDN", parent_id=existing_stage)
    transport = ScriptedTransport(
        chat_reply([], ready=True),
        blueprint_json(stage("学 HTTP", tasks=[task("读 MDN")])),
        review_json(summary="没有明显语义问题"),
        review_json(summary="没有明显结构问题"),
        revision_json(
            stage("学 HTTP", tasks=[task("写一个请求客户端")]),
            resolutions=[
                {"reviewer_key": "system", "point_index": 0, "resolution": "将撞名任务改为实际项目练习"}
            ],
        ),
    )
    blueprint.say(conn, candidate_id, "我想把 HTTP 学明白", transport=transport)

    created = blueprint.generate_blueprint(
        conn, candidate_id, mode="enhanced", transport=transport
    )

    payload = json.loads(
        conn.execute("SELECT payload FROM proposal WHERE id = ?", (created["proposal_id"],)).fetchone()["payload"]
    )
    assert created["calls"] == 4
    assert payload["mode"] == "enhanced"
    assert payload["stages"][0]["tasks"][0]["title"] == "写一个请求客户端"
    reviewers = payload["review"]["reviewers"]
    # 两位模型审查员都漏看了，系统的防冲突检查自己补位成第三个座位
    assert [reviewer["key"] for reviewer in reviewers] == ["level", "structure", "system"]
    assert reviewers[0]["stance"] == "agree"
    assert reviewers[2]["points"][0]["severity"] == "revise"


def test_enhanced_blueprint_keeps_uncertain_level_as_a_confirmation_note(conn):
    make_provider(conn)
    add_profile(conn)
    transport = ScriptedTransport(
        chat_reply([], ready=True),
        blueprint_json(stage("Python 基础", tasks=[task("写一个小程序") ])),
        review_json(
            disagree_point(
                target="整体",
                point="档案没有说明用户是否熟悉 Python 基础",
                reason="档案中没有可确认 Python 熟练度的经历",
                adjustment="出方案前询问一次；也可先按待确认状态继续",
                severity="confirm",
            ),
            summary="水平依据不足，请用户确认",
        ),
        review_json(summary="没有明显结构问题"),
    )
    candidate_id, _ = ready_thread(conn, transport=transport)

    created = blueprint.generate_blueprint(
        conn, candidate_id, mode="enhanced", transport=transport
    )

    payload = json.loads(
        conn.execute("SELECT payload FROM proposal WHERE id = ?", (created["proposal_id"],)).fetchone()["payload"]
    )
    assert created["calls"] == 3
    level = payload["review"]["reviewers"][0]
    assert level["stance"] == "disagree"
    assert level["points"][0]["severity"] == "confirm"
    # 待确认不触发修订
    assert payload["review"]["initial"] is None
    assert payload["review"]["revision_resolution"] == []


@pytest.mark.parametrize(
    "responses",
    [
        ("not-json", review_json(), review_json()),  # 增强模式草稿无效时不把额度耗在重试上
        (blueprint_json(stage("学 HTTP")), "not-json", "not-json"),  # 第一位审查员重试后仍失效不静默降级
        (blueprint_json(stage("学 HTTP")), review_json(), "not-json", "not-json"),  # 第二位审查员同样不降级
    ],
)
def test_enhanced_blueprint_fails_closed_on_invalid_draft_or_review(conn, responses):
    make_provider(conn)
    add_profile(conn)
    transport = ScriptedTransport(chat_reply([], ready=True), *responses)
    candidate_id, _ = ready_thread(conn, transport=transport)

    with pytest.raises(blueprint.BlueprintError):
        blueprint.generate_blueprint(conn, candidate_id, mode="enhanced", transport=transport)

    assert conn.execute("SELECT COUNT(*) AS n FROM proposal WHERE kind = ?", (BLUEPRINT_KIND,)).fetchone()["n"] == 0


def test_enhanced_review_accepts_stance_and_severity_aliases(conn):
    """审查员把 stance/severity 写成同义词（「赞同」「需确认」）时表层规范化接住，不浪费重试。"""
    make_provider(conn)
    add_profile(conn)
    aliased = json.dumps(
        {
            "summary": "整体站得住",
            "points": [
                {
                    "stance": "赞同",
                    "target": "第 1 阶段",
                    "point": "起点与档案相符",
                    "reason": "档案里有相关经历记录",
                    "severity": "需确认",
                }
            ],
        },
        ensure_ascii=False,
    )
    transport = ScriptedTransport(
        chat_reply([], ready=True),
        blueprint_json(stage("复习 HTTP")),
        aliased,
        review_json(summary="阶段衔接没有问题"),
    )
    candidate_id, _ = ready_thread(conn, transport=transport)

    created = blueprint.generate_blueprint(
        conn, candidate_id, mode="enhanced", transport=transport
    )

    row = conn.execute(
        "SELECT payload FROM proposal WHERE id = ?", (created["proposal_id"],)
    ).fetchone()
    review = json.loads(row["payload"])["review"]
    assert review["reviewers"][0]["stance"] == "agree"
    assert review["reviewers"][0]["points"][0]["severity"] == "confirm"
    assert created["calls"] == 3  # 规范化接住，没有触发重试


def test_enhanced_review_retries_once_when_first_output_is_broken(conn):
    """审查输出结构不合格时带原因重试一次；重试合格就正常落提案。"""
    make_provider(conn)
    add_profile(conn)
    transport = ScriptedTransport(
        chat_reply([], ready=True),
        blueprint_json(stage("复习 HTTP")),
        "not-json",  # 第一位审查员第一次结构不合格
        review_json(summary="重试后的补充审查"),
        review_json(summary="阶段衔接没有问题"),
    )
    candidate_id, _ = ready_thread(conn, transport=transport)

    created = blueprint.generate_blueprint(
        conn, candidate_id, mode="enhanced", transport=transport
    )

    row = conn.execute(
        "SELECT payload FROM proposal WHERE id = ?", (created["proposal_id"],)
    ).fetchone()
    review = json.loads(row["payload"])["review"]
    assert review["reviewers"][0]["summary"] == "重试后的补充审查"
    assert created["calls"] == 4  # 初稿＋第一位审查员两次＋第二位审查员一次
    assert "结构检查" in transport.seen[3]["payload"]["messages"][-1]["content"]


def test_enhanced_disagreement_without_adjustment_downgrades_to_confirm(conn):
    """重试后仍只缺建议调整：降为待确认反对——意见保留、整稿保留、不触发修订。"""
    make_provider(conn)
    add_profile(conn)
    bare = {"stance": "disagree", "target": "第 1 阶段", "point": "拆得太粗", "reason": "没有可验收的交付物"}
    transport = ScriptedTransport(
        chat_reply([], ready=True),
        blueprint_json(stage("学 HTTP")),
        review_json(bare),
        review_json(bare),  # 重试仍缺 adjustment：触发降级而非整稿作废
        review_json(summary="没有问题"),
    )
    candidate_id, _ = ready_thread(conn, transport=transport)

    created = blueprint.generate_blueprint(conn, candidate_id, mode="enhanced", transport=transport)

    row = conn.execute("SELECT payload FROM proposal WHERE id = ?", (created["proposal_id"],)).fetchone()
    payload = json.loads(row["payload"])
    level = payload["review"]["reviewers"][0]
    assert level["points"][0]["stance"] == "disagree"
    assert level["points"][0]["severity"] == "confirm"
    assert payload["review"]["initial"] is None  # 待确认反对不触发修订
    assert created["calls"] == 4


def test_enhanced_review_with_blank_reason_still_fails_closed(conn):
    """缺调整建议之外的结构问题（空白依据）不降级：重试仍不合格就整稿不落。"""
    make_provider(conn)
    add_profile(conn)
    blank = {"stance": "disagree", "target": "第 1 阶段", "point": "拆得太粗", "reason": " ", "adjustment": "拆成两阶段"}
    transport = ScriptedTransport(
        chat_reply([], ready=True),
        blueprint_json(stage("学 HTTP")),
        review_json(blank),
        review_json(blank),
        review_json(summary="没有问题"),
    )
    candidate_id, _ = ready_thread(conn, transport=transport)

    with pytest.raises(blueprint.BlueprintError):
        blueprint.generate_blueprint(conn, candidate_id, mode="enhanced", transport=transport)

    assert conn.execute("SELECT COUNT(*) AS n FROM proposal WHERE kind = ?", (BLUEPRINT_KIND,)).fetchone()["n"] == 0


def test_enhanced_confirm_points_carry_user_question(conn):
    """待确认条目必须带一句直接问用户的问句：原样进 payload，退回表单拿它当「要补什么」。"""
    make_provider(conn)
    add_profile(conn)
    confirm_point = {
        "stance": "disagree",
        "target": "第 1 阶段",
        "point": "不应把已有可运行代码当作事实前提",
        "reason": "档案只说在学命令行工具，没有可运行代码的证据",
        "severity": "confirm",
        "question": "你现在是否已有可运行的 Python 代码？",
    }
    transport = ScriptedTransport(
        chat_reply([], ready=True),
        blueprint_json(stage("学 HTTP")),
        review_json(confirm_point),
        review_json(summary="没有问题"),
    )
    candidate_id, _ = ready_thread(conn, transport=transport)

    created = blueprint.generate_blueprint(conn, candidate_id, mode="enhanced", transport=transport)

    row = conn.execute("SELECT payload FROM proposal WHERE id = ?", (created["proposal_id"],)).fetchone()
    level = json.loads(row["payload"])["review"]["reviewers"][0]
    assert level["points"][0]["severity"] == "confirm"
    assert level["points"][0]["question"] == "你现在是否已有可运行的 Python 代码？"
    # 提示词里带 question 指令与例句：模型被明确要求写问句，而不是复述结论
    reviewer_call = transport.seen[2]
    assert any("question" in (message.get("content") or "") for message in reviewer_call["payload"]["messages"])


def test_enhanced_revision_must_account_for_every_required_finding(conn):
    make_provider(conn)
    add_profile(conn)
    transport = ScriptedTransport(
        chat_reply([], ready=True),
        blueprint_json(stage("入门", tasks=[task("看资料") ])),
        review_json(summary="没有水平问题"),
        review_json(
            disagree_point(
                target="第 2 阶段",
                point="直接要求部署但前面没有可运行的项目",
                reason="第 1 阶段的交付物只是阅读笔记",
                adjustment="先增加实现可运行项目的任务",
            ),
            summary="需要先补项目实践",
        ),
        # 回执对到了不存在的审查员/意见上：等于没交代，必须整单作废
        revision_json(
            stage("入门", tasks=[task("看资料") ]),
            resolutions=[{"reviewer_key": "level", "point_index": 0, "resolution": "没有改动"}],
        ),
    )
    candidate_id, _ = ready_thread(conn, transport=transport)

    with pytest.raises(blueprint.BlueprintError):
        blueprint.generate_blueprint(conn, candidate_id, mode="enhanced", transport=transport)

    assert conn.execute("SELECT COUNT(*) AS n FROM proposal WHERE kind = ?", (BLUEPRINT_KIND,)).fetchone()["n"] == 0


def test_a_new_version_supersedes_the_old_one_and_leaves_a_trace(conn):
    make_provider(conn)
    add_profile(conn)
    transport = ScriptedTransport(
        chat_reply([], ready=True),
        blueprint_json(stage("学 HTTP", tasks=[task("读 MDN")])),
        blueprint_json(stage("学 HTTP", tasks=[task("读 MDN"), task("写接口")])),
    )
    # 老数据（没有会话）：计划级的「树 = 版本」取代语义照旧
    candidate_id, plan_id = legacy_adopted_candidate(conn, title="学 HTTP")
    blueprint.say(conn, candidate_id, "聊聊这个方向", plan_id=plan_id, transport=transport)

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
        chat_reply([], ready=True),
        blueprint_json(stage("学 HTTP", tasks=[task("读 MDN", due="下周三")])),
        blueprint_json(stage("学 HTTP", tasks=[task("读 MDN", due="2026-10-01")])),
    )
    candidate_id, _ = ready_thread(conn, transport=transport)

    created = blueprint.generate_blueprint(conn, candidate_id, transport=transport)

    assert created["attempts"] == 2
    assert created["stages"][0]["tasks"][0]["due_date"] == "2026-10-01"


# ---------- 建树（批准动作的内核） ----------
#
# OC-06 起 proposals.decide 对蓝图提案只做版本分流（v1 拒批、v2 等原子批准），
# 建树内核的这些行为改由 build_tree 直测；原子批准接上后（OC-07）它们仍是批准的后半段。

def test_build_tree_builds_only_the_ticked_part(conn):
    candidate_id, plan_id = adopted_candidate(conn, title="学 HTTP")
    # 「计划里已有同名阶段」是复用的前提（OC-05 起采纳不建阶段，这里手工建一条）
    plan.add_node(conn, plan_id, "stage", "学 HTTP")
    payload = open_payload(
        conn,
        plan_id,
        candidate_id,
        stage("学 HTTP", tasks=[task("读 MDN"), task("写一个接口"), task("加错误处理")]),
        stage("做一个小服务", tasks=[task("起一个服务")]),
        stage("上线", deliverable="一个能访问的地址"),
    )

    built = blueprint.build_tree(conn, payload, selected=["0.1", "1"])

    assert [item["title"] for item in built["stages"]] == ["做一个小服务"]
    assert [item["title"] for item in built["tasks"]] == ["写一个接口", "起一个服务"]
    assert stage_titles(conn, plan_id) == ["学 HTTP", "做一个小服务"]  # 「上线」整段被丢弃
    stages = plan.get_stages(conn, plan_id)
    assert task_titles(conn, int(stages[0]["id"])) == ["写一个接口"]  # 0.1 之外的没建
    assert task_titles(conn, int(stages[1]["id"])) == ["起一个服务"]
    # 复用「学 HTTP」时它要交的东西按这一版写进去了（T30 的写入口；改前改后进台账）
    reused = plan.get_node(conn, int(stages[0]["id"]))
    assert reused["deliverable"] == "交一个能跑通的小东西"
    assert built["notes"] and "要交的东西" in built["notes"][0]
    events = [
        event
        for event in ledger.history(conn, "plan_node", int(stages[0]["id"]))
        if event["change_type"] == "update_fields"
    ]
    assert len(events) == 1
    # OC-07 起复用阶段会把成果字段一并写入（deliverable + v2 字段，一条 update_fields 流水）
    after = json.loads(events[0]["after_value"])
    assert after["deliverable"] == "交一个能跑通的小东西"
    # （这条是 legacy 计划上的 build_tree 直测：契约承接 id 不绑，成果计划批准时才会绑）
    assert {"purpose", "why_now", "acceptance_criteria", "evidence_requirements"} <= set(after)


def test_build_tree_reuses_a_stage_and_overwrites_an_old_deliverable_with_a_note(conn):
    """复用的阶段本来就有别的交付物：按这一版改写，并在回执里明说是「改」不是「没写」。"""
    candidate_id, plan_id = adopted_candidate(conn, title="学 HTTP")
    # OC-05 起采纳不建阶段：这个「已有同名阶段」是手工建的复用前提
    stage_id = plan.add_node(conn, plan_id, "stage", "学 HTTP", deliverable="先交一份笔记")
    payload = open_payload(
        conn, plan_id, candidate_id, stage("学 HTTP", deliverable="一个过测试的客户端")
    )

    built = blueprint.build_tree(conn, payload)

    assert plan.get_node(conn, stage_id)["deliverable"] == "一个过测试的客户端"
    note = built["notes"][0]
    assert "从「先交一份笔记」改成了" in note
    # 它没在计划里多建一个同名阶段
    assert len(plan.get_stages(conn, plan_id)) == 1

    # 再次批准同一阶段（复用）：打卡已存在就跳过，不撞防重名闸、也不重复建
    blueprint.build_tree(conn, payload)
    checkpoint_rows = conn.execute(
        "SELECT COUNT(*) AS n FROM plan_node WHERE parent_id = ? AND level = 'checkpoint'",
        (stage_id,),
    ).fetchone()
    assert checkpoint_rows["n"] == 1


def test_build_tree_with_no_selection_means_the_whole_blueprint(conn):
    candidate_id, plan_id = adopted_candidate(conn, title="学 HTTP")
    payload = open_payload(
        conn,
        plan_id,
        candidate_id,
        stage("学 HTTP", tasks=[task("读 MDN")]),
        stage("上线", deliverable="一个能访问的地址"),
    )

    built = blueprint.build_tree(conn, payload)

    assert stage_titles(conn, plan_id) == ["学 HTTP", "上线"]
    stages = plan.get_stages(conn, plan_id)
    assert task_titles(conn, int(stages[0]["id"])) == ["读 MDN"]
    assert stages[1]["deliverable"] == "一个能访问的地址"  # 新阶段的交付物真的写进去了
    # 每个建出来的阶段自动配一个周打卡：报告（周报 → 复盘卡）挂在它上面
    assert [item["title"] for item in built["checkpoints"]] == ["学 HTTP·周打卡", "上线·周打卡"]
    for stage_row in stages:
        checkpoint_titles = [
            row["title"]
            for row in conn.execute(
                "SELECT title FROM plan_node WHERE parent_id = ? AND level = 'checkpoint'",
                (int(stage_row["id"]),),
            )
        ]
        assert checkpoint_titles == [f"{stage_row['title']}·周打卡"]


def test_build_tree_a_ticked_stage_keeps_all_of_its_tasks(conn):
    candidate_id, plan_id = adopted_candidate(conn, title="学 HTTP")
    payload = open_payload(
        conn, plan_id, candidate_id, stage("学 HTTP", tasks=[task("A"), task("B")])
    )

    blueprint.build_tree(conn, payload, selected=["0"])

    stages = plan.get_stages(conn, plan_id)
    assert task_titles(conn, int(stages[0]["id"])) == ["A", "B"]


def test_build_tree_nothing_ticked_is_refused_and_changes_nothing(conn):
    candidate_id, plan_id = adopted_candidate(conn, title="学 HTTP")
    payload = open_payload(conn, plan_id, candidate_id, stage("学 HTTP", tasks=[task("A")]))

    with pytest.raises(blueprint.BlueprintError):
        blueprint.build_tree(conn, payload, selected=[""])

    assert stage_titles(conn, plan_id) == []  # OC-05：采纳不再建阶段，计划还是空的


def test_build_tree_out_of_range_selection_is_refused(conn):
    candidate_id, plan_id = adopted_candidate(conn, title="学 HTTP")
    payload = open_payload(conn, plan_id, candidate_id, stage("学 HTTP", tasks=[task("A")]))

    with pytest.raises(blueprint.BlueprintError):
        blueprint.build_tree(conn, payload, selected=["0.9"])
    with pytest.raises(blueprint.BlueprintError):
        blueprint.build_tree(conn, payload, selected=["5"])
    with pytest.raises(blueprint.BlueprintError):
        blueprint.build_tree(conn, payload, selected=["二"])
    assert conn.execute("SELECT COUNT(*) AS n FROM plan_node").fetchone()["n"] == 0  # 一个节点都没建


def test_rejecting_a_blueprint_builds_nothing(conn):
    candidate_id, plan_id = adopted_candidate(conn, title="学 HTTP")
    proposal_id = open_proposal(conn, plan_id, candidate_id, stage("上线"))

    result = proposals.decide(conn, proposal_id, approved=False, reason="阶段拆得不对")

    assert result["effect"] == "recorded_only" and result["built"] is None
    assert stage_titles(conn, plan_id) == []
    assert result["status"] == "rejected"


# ---------- 建树前的查重（一条都不写的预检） ----------

def test_build_tree_duplicate_task_titles_inside_a_stage_are_refused(conn):
    candidate_id, plan_id = adopted_candidate(conn, title="学 HTTP")
    payload = open_payload(
        conn, plan_id, candidate_id, stage("做服务", tasks=[task("起一个服务"), task("起一个服务")])
    )

    with pytest.raises(blueprint.BlueprintError):
        blueprint.build_tree(conn, payload)

    assert stage_titles(conn, plan_id) == []  # 一个节点都没建


def test_build_tree_a_task_clashing_with_an_existing_open_one_is_refused(conn):
    candidate_id, plan_id = adopted_candidate(conn, title="学 HTTP")
    existing_stage = plan.add_node(conn, plan_id, "stage", "学 HTTP")  # 复用/撞车的既有阶段
    plan.add_node(conn, plan_id, "task", "读 MDN", parent_id=existing_stage)
    payload = open_payload(
        conn, plan_id, candidate_id, stage("学 HTTP", tasks=[task("读 MDN")])
    )

    with pytest.raises(blueprint.BlueprintError):
        blueprint.build_tree(conn, payload)


def test_build_tree_into_a_closed_plan_is_refused(conn):
    candidate_id, plan_id = adopted_candidate(conn, title="学 HTTP")
    payload = open_payload(conn, plan_id, candidate_id, stage("上线"))
    plan.close_plan(conn, plan_id)

    with pytest.raises(blueprint.BlueprintConflict):
        blueprint.build_tree(conn, payload)


# ---------- 批准入口的版本分流（OC-06） ----------

def test_approving_a_v1_blueprint_is_refused_with_a_regenerate_hint(conn):
    """旧 v1 蓝图（无契约）只读兼容：批准明确拒绝并提示重新生成，不半猜缺失契约。"""
    candidate_id, plan_id = adopted_candidate(conn, title="学 HTTP")
    proposal_id = open_v1_proposal(conn, plan_id, candidate_id, stage("上线"))

    with pytest.raises(proposals.ProposalError) as refused:
        proposals.decide(conn, proposal_id, approved=True)
    assert "重新生成" in str(refused.value)

    # 只读兼容：提案还在待裁定列表里，驳回（留痕）仍可用
    assert [item["id"] for item in proposals.list_pending(conn, BLUEPRINT_KIND)["proposals"]] == [
        proposal_id
    ]
    decided = proposals.decide(conn, proposal_id, approved=False, reason="旧稿不要了")
    assert decided["status"] == "rejected"


def test_approving_a_v2_blueprint_still_needs_explicit_confirmation(conn):
    """v2 蓝图批准（OC-07 起原子）必须显式确认契约；缺 confirm_contract 就地拒绝、提案保持 pending。"""
    candidate_id, plan_id = adopted_candidate(conn, title="学 HTTP")
    # F6 口径：延续/改版只允许已有 active 契约的计划（legacy 计划要先走升级入口）
    contract.activate(
        conn, plan_id, contract_json(), source_kind="manual", reason="测试：落点先升到成果流程"
    )
    proposal_id = open_proposal(conn, plan_id, candidate_id, stage("学 HTTP", tasks=[task("读 MDN")]))

    with pytest.raises(proposals.ProposalError) as refused:
        proposals.decide(conn, proposal_id, approved=True, confirm_contract=False)
    assert "确认" in str(refused.value) or "confirm" in str(refused.value)
    row = conn.execute("SELECT status FROM proposal WHERE id = ?", (proposal_id,)).fetchone()
    assert row["status"] == "pending"

    # 确认了就走原子批准：契约激活 + 建树一次完成（这条 v2 提案没有会话归属，跳过会话关闭）
    result = proposals.decide(conn, proposal_id, approved=True, confirm_contract=True)
    assert result["effect"] == "blueprint_built" and result["plan_id"] == plan_id
    # 第一版是测试预置的、第二版是这次批准激活的
    assert conn.execute("SELECT COUNT(*) AS n FROM outcome_contract").fetchone()["n"] == 2
    assert contract.active(conn, plan_id)["version"] == 2
    assert [str(row["title"]) for row in plan.get_stages(conn, plan_id)] == ["学 HTTP"]
    plan_row = conn.execute("SELECT * FROM plan WHERE id = ?", (plan_id,)).fetchone()
    assert plan_row["completion_mode"] == "outcome"


# ---------- 路径形状的步骤草案进对话上下文（T34：SPEC 决策 41） ----------
#
# 采纳一条路径候选时，那几步自动建的同名阶段只有一条（伞候选那一条）。步骤是**草案**：
# 它进规划对话的上下文当底稿，模型在它上面增减，而不是从零再问六轮。

def adopted_path_candidate(conn, steps: list[dict], title: str = "从零到部署学通 agent 开发"):
    """一条**带步骤草案**的已采纳候选（走真路径；OC-05 起采纳不建伞阶段，草案只进对话）。"""
    plan_id = ledger.create_active(conn, "plan", {"goal": f"计划：{title}"}, actor="user")
    request_id = advisor.record_request(conn, "search", "学 agent 开发怎么学", plan_id)
    candidate_id = ledger.create_active(
        conn,
        "candidate",
        {
            "request_id": request_id,
            "title": title,
            "why": "对主线有直接帮助",
            "depth_target": "够用",
            "rank": 1,
            "payload": json.dumps({"shape": "path", "steps": steps}, ensure_ascii=False),
        },
        actor="agent",
        reason="测试用",
    )
    advisor.decide_candidate(conn, candidate_id, accept=True)
    return candidate_id, plan_id


def test_path_steps_ride_into_the_chat_context(conn):
    make_provider(conn)
    add_profile(conn)
    steps = [
        {"title": "先把异步基础打牢", "deliverable": "一个会跑的 async 小 demo", "why": "不然后面看不懂调度"},
        {"title": "写一个能跑通的循环", "deliverable": "一个能跑通的脚本", "why": "前面的基础在这儿用上"},
    ]
    candidate_id, plan_id = adopted_path_candidate(conn, steps)

    # 对话区（刷新页面也在）：草案从 candidate.payload 解出来，不靠当刻响应
    assert [item["title"] for item in blueprint.view(conn, candidate_id, plan_id)["steps"]] == [
        "先把异步基础打牢",
        "写一个能跑通的循环",
    ]

    transport = ScriptedTransport(chat_reply(["每周能投入几小时？"]))
    blueprint.say(conn, candidate_id, "我想先把基础弄明白", transport=transport)

    prompt = transport.seen[0]["payload"]["messages"][1]["content"]
    assert "【这条路它给的先后步骤（草案，不是成品）】" in prompt
    assert "1. 先把异步基础打牢" in prompt
    assert "一个会跑的 async 小 demo" in prompt
    assert "不然后面看不懂调度" in prompt
    assert "以这份草案为底稿" in prompt


def test_direction_candidate_has_no_steps_section(conn):
    """老形状（directions）的候选没有草案——那一段整个不出现，不留空标题。"""
    make_provider(conn)
    add_profile(conn)
    candidate_id, plan_id = adopted_candidate(conn, title="学 HTTP")

    assert blueprint.view(conn, candidate_id, plan_id)["steps"] == []

    transport = ScriptedTransport(chat_reply(["每周几小时？"]))
    blueprint.say(conn, candidate_id, "先聊两句", transport=transport)
    assert "步骤（草案" not in transport.seen[0]["payload"]["messages"][1]["content"]


def test_a_new_direction_candidate_remembers_where_it_landed(conn):
    """走查踩到的缺口（T37）：采纳后刷新页面，规划对话不该再问「注入哪个计划」。

    「新方向」的候选（那一轮不属于任何计划）落点是采纳那一刻现选的——不记在候选自己身上，
    刷新一次就只剩当刻响应里那一下，对话便不知道自己在哪个计划里。
    """
    make_provider(conn)
    add_profile(conn)
    plan_id = ledger.create_active(conn, "plan", {"goal": "Python 后端"}, actor="user")
    request_id = advisor.record_request(conn, "search", "从 Python 底座到后端上线")
    candidate_id = ledger.create_active(
        conn,
        "candidate",
        {
            "request_id": request_id,
            "title": "从 Python 工程底座到 FastAPI 后端上线",
            "why": "对主线有直接帮助",
            "depth_target": "熟练",
            "rank": 1,
        },
        actor="agent",
        reason="测试用",
    )
    advisor.decide_candidate(conn, candidate_id, accept=True, plan_id=plan_id)

    # 模拟页面刷新：不带当刻响应里的任何东西再问一遍
    assert blueprint.view(conn, candidate_id, None)["plan_id"] == plan_id

    transport = ScriptedTransport(chat_reply(["每周几小时？"]))
    assert blueprint.say(conn, candidate_id, "先打基础", transport=transport)["plan_id"] == plan_id


# ---------- 归属闸 · 有效轮门槛 · 生成期校验前移（2026-09-28 整改 III-01 / III-02） ----------

def test_view_refuses_a_plan_id_that_disagrees_with_the_recorded_thread(conn):
    """view 传的 plan_id 也要过 say/generate 那道闸：与已记归属对不上就 409——
    不然待批蓝图会错挂到同计划另一条候选的对话里展示。"""
    make_provider(conn)
    add_profile(conn)
    plan_id = ledger.create_active(conn, "plan", {"goal": "A"}, actor="user")
    other = ledger.create_active(conn, "plan", {"goal": "B"}, actor="user")
    candidate_id = free_candidate_adopted_into(conn, plan_id)
    transport = ScriptedTransport(chat_reply(["第一问？"]))
    blueprint.say(conn, candidate_id, "第一句", plan_id=plan_id, transport=transport)

    with pytest.raises(blueprint.BlueprintConflict):
        blueprint.view(conn, candidate_id, other)

    seen = blueprint.view(conn, candidate_id, plan_id)  # 传对的照常看
    assert seen["plan_id"] == plan_id and seen["turns_used"] == 1


def test_view_checks_the_callers_plan_against_the_landing_before_any_chat(conn):
    """还没聊过也一样：调用方说明与候选落点冲突，view 不再直接照单全收。"""
    plan_id = ledger.create_active(conn, "plan", {"goal": "A"}, actor="user")
    other = ledger.create_active(conn, "plan", {"goal": "B"}, actor="user")
    candidate_id = free_candidate_adopted_into(conn, plan_id)

    with pytest.raises(blueprint.BlueprintConflict):
        blueprint.view(conn, candidate_id, other)

    assert blueprint.view(conn, candidate_id, plan_id)["plan_id"] == plan_id
    assert blueprint.view(conn, candidate_id)["plan_id"] == plan_id  # 不带 plan_id 也认得


def test_a_failed_reply_does_not_count_as_a_valid_turn(conn):
    """III-01：模型没接住的那轮不算「聊成」——不能拿一句留下的失败消息凑数出蓝图。"""
    make_provider(conn)
    add_profile(conn)
    candidate_id, plan_id = adopted_candidate(conn)
    transport = ScriptedTransport(
        json.dumps({"questions": [], "ready": False}),  # 这轮回话不合格 → say 报错
        chat_reply([], ready=True),
        blueprint_json(stage("学 HTTP", tasks=[task("读 MDN")])),
    )

    with pytest.raises(blueprint.BlueprintError):
        blueprint.say(conn, candidate_id, "我还是想学这个", transport=transport)

    seen = blueprint.view(conn, candidate_id)
    assert seen["turns_used"] == 1  # 界面显示：你发过一句
    assert seen["valid_turns_used"] == 0  # 但没聊成
    assert seen["can_generate"] is False
    with pytest.raises(blueprint.BlueprintConflict):  # 出方案被拦下
        blueprint.generate_blueprint(conn, candidate_id, transport=transport)
    assert len(transport.seen) == 1  # 蓝图生成一次模型都没调

    blueprint.say(conn, candidate_id, "每周 6 小时", transport=transport)  # 聊成一轮
    created = blueprint.generate_blueprint(conn, candidate_id, transport=transport)
    assert created["plan_id"] == plan_id
    seen = blueprint.view(conn, candidate_id)
    assert seen["valid_turns_used"] == 1
    # 提案已落在会话上：会话转 blueprint_pending，等裁定（不再允许就地再出一版）
    assert seen["can_generate"] is False
    assert seen["planning_status"] == "blueprint_pending"


def test_pending_blueprint_summary_carries_its_candidate(conn):
    """III-02：待批稿只给**出它的那条候选**看——摘要带 candidate_id，同计划别的候选不外显。"""
    make_provider(conn)
    add_profile(conn)
    transport = ScriptedTransport(
        chat_reply([], ready=True),
        blueprint_json(stage("学 HTTP", tasks=[task("读 MDN")])),
    )
    candidate_id, plan_id = ready_thread(conn, transport=transport)
    created = blueprint.generate_blueprint(conn, candidate_id, transport=transport)

    summary = blueprint.view(conn, candidate_id)["blueprint"]

    assert summary["id"] == created["proposal_id"]
    assert summary["candidate_id"] == candidate_id

    # 同计划另一条候选的对话里**看不到**这份待批稿（查看与候选绑定）——免得那边把它当
    # 自己的稿子批了；A 自己再看，那份还在
    other_id = free_candidate_adopted_into(conn, plan_id)
    assert blueprint.view(conn, other_id)["blueprint"] is None
    assert blueprint.view(conn, candidate_id)["blueprint"]["id"] == created["proposal_id"]
    # 计划级口径（不传候选）照旧能用：版本更替（_supersede_previous）那半边走的就是它
    assert blueprint.pending_blueprint(conn, plan_id)["id"] == created["proposal_id"]


def test_generation_retries_when_a_stage_title_is_blank(conn):
    """III-02 前移：纯空白标题 Pydantic 的 min_length 挡不住——生成时就拦下带原因重试。"""
    make_provider(conn)
    add_profile(conn)
    transport = ScriptedTransport(
        chat_reply([], ready=True),
        blueprint_json(stage("   ")),  # 阶段标题全是空白
        blueprint_json(stage("学 HTTP", tasks=[task("读 MDN")])),
    )
    candidate_id, _ = ready_thread(conn, transport=transport)

    created = blueprint.generate_blueprint(conn, candidate_id, transport=transport)

    assert created["attempts"] == 2
    assert [item["title"] for item in created["stages"]] == ["学 HTTP"]


def test_generation_retries_when_a_task_title_is_blank(conn):
    make_provider(conn)
    add_profile(conn)
    transport = ScriptedTransport(
        chat_reply([], ready=True),
        blueprint_json(stage("学 HTTP", tasks=[task("   ")])),  # 任务标题全是空白
        blueprint_json(stage("学 HTTP", tasks=[task("读 MDN")])),
    )
    candidate_id, _ = ready_thread(conn, transport=transport)

    created = blueprint.generate_blueprint(conn, candidate_id, transport=transport)

    assert created["attempts"] == 2
    assert created["stages"][0]["tasks"][0]["title"] == "读 MDN"


def test_generation_retries_when_one_stage_has_duplicate_task_titles(conn):
    make_provider(conn)
    add_profile(conn)
    transport = ScriptedTransport(
        chat_reply([], ready=True),
        blueprint_json(stage("学 HTTP", tasks=[task("读 MDN"), task("读 MDN")])),
        blueprint_json(stage("学 HTTP", tasks=[task("读 MDN")])),
    )
    candidate_id, _ = ready_thread(conn, transport=transport)

    created = blueprint.generate_blueprint(conn, candidate_id, transport=transport)

    assert created["attempts"] == 2  # 同一阶段里重名 → 判不合格重试


def test_same_task_title_in_different_stages_is_allowed(conn):
    """跨阶段同名任务合法——不同父节点在计划树里本来就互不冲突。"""
    make_provider(conn)
    add_profile(conn)
    transport = ScriptedTransport(
        chat_reply([], ready=True),
        blueprint_json(
            stage("学 HTTP", tasks=[task("读 MDN")]),
            stage("上线", tasks=[task("读 MDN")]),
        ),
    )
    candidate_id, _ = ready_thread(conn, transport=transport)

    created = blueprint.generate_blueprint(conn, candidate_id, transport=transport)

    assert created["attempts"] == 1
    assert [item["tasks"][0]["title"] for item in created["stages"]] == ["读 MDN", "读 MDN"]


def test_two_invalid_blueprints_land_nothing(conn):
    """两次都不合格：报错不落——一条明知批不了的提案都不该留在库里。"""
    make_provider(conn)
    add_profile(conn)
    transport = ScriptedTransport(
        chat_reply([], ready=True),
        blueprint_json(stage("   ")),  # 第一次：阶段标题空白
        blueprint_json(stage("学 HTTP", tasks=[task("读 MDN"), task("读 MDN")])),  # 第二次：重名
    )
    candidate_id, _ = ready_thread(conn, transport=transport)

    with pytest.raises(blueprint.BlueprintError):
        blueprint.generate_blueprint(conn, candidate_id, transport=transport)

    assert conn.execute(
        "SELECT COUNT(*) AS n FROM proposal WHERE kind = ?", (BLUEPRINT_KIND,)
    ).fetchone()["n"] == 0


@pytest.mark.parametrize("reply", [
    chat_reply(["每周几小时？"]),
    chat_reply(["还要确认哪一步？"], ready=True),
])
def test_generation_requires_latest_ready_reply_without_questions(conn, reply):
    make_provider(conn)
    add_profile(conn)
    candidate_id, plan_id = adopted_candidate(conn)
    transport = ScriptedTransport(reply)
    said = blueprint.say(conn, candidate_id, "先聊聊", transport=transport)
    assert said["can_generate"] is False
    assert blueprint.view(conn, candidate_id)["can_generate"] is False
    with pytest.raises(blueprint.BlueprintConflict):
        blueprint.generate_blueprint(conn, candidate_id, transport=transport)
    assert len(transport.seen) == 1
    assert blueprint.valid_turns_used(conn, candidate_id, plan_id) == 1


def test_later_unready_reply_revokes_previous_readiness(conn):
    make_provider(conn)
    add_profile(conn)
    candidate_id, _ = adopted_candidate(conn)
    transport = ScriptedTransport(chat_reply([], ready=True), chat_reply(["再确认一下？"]))
    assert blueprint.say(conn, candidate_id, "足够了", transport=transport)["can_generate"] is True
    assert blueprint.say(conn, candidate_id, "还有个疑问", transport=transport)["can_generate"] is False
    assert blueprint.view(conn, candidate_id)["can_generate"] is False
    with pytest.raises(blueprint.BlueprintConflict):
        blueprint.generate_blueprint(conn, candidate_id, transport=transport)
    assert len(transport.seen) == 2


@pytest.mark.parametrize("bad", [
    blueprint_json(stage("学 HTTP"), goal="   "),
    blueprint_json(stage("学 HTTP", deliverable="  ")),
])
def test_generation_retries_blank_goal_or_deliverable_and_lands_nothing(conn, bad):
    make_provider(conn)
    add_profile(conn)
    transport = ScriptedTransport(chat_reply([], ready=True), bad, bad)
    candidate_id, _ = ready_thread(conn, transport=transport)
    with pytest.raises(blueprint.BlueprintError):
        blueprint.generate_blueprint(conn, candidate_id, transport=transport)
    assert len(transport.seen) == 3
    assert conn.execute("SELECT COUNT(*) AS n FROM proposal").fetchone()["n"] == 0


# ---------- 生成门槛 · 与现有计划的冲突预检（2026-09-28 整改 III-01 / III-02） ----------
#
# 生成要过三道：聊成的轮数至少一轮（既有）、**最后一轮必须是它答的那句**（新）、
# 蓝图任务不撞现有计划里开着的同名任务（新——这道原先要等批准按钮才炸，留下明知
# 批不了的稿子；现在生成期就拦下、带原因重试一次）。

def test_generation_retries_when_a_task_clashes_with_an_open_one(conn):
    """蓝图给同名开着的阶段排了撞名任务 → 生成期判不合格重试；第二次改掉才落提案。"""
    make_provider(conn)
    add_profile(conn)
    candidate_id, plan_id = adopted_candidate(conn)
    existing_stage = plan.add_node(conn, plan_id, "stage", "学 HTTP")  # 撞车的既有阶段
    plan.add_node(conn, plan_id, "task", "读 MDN", parent_id=existing_stage)
    transport = ScriptedTransport(
        chat_reply([], ready=True),
        blueprint_json(stage("学 HTTP", tasks=[task("读 MDN")])),  # 撞上开着的同名任务
        blueprint_json(stage("学 HTTP", tasks=[task("写一个请求客户端")])),  # 第二次改掉
    )
    blueprint.say(conn, candidate_id, "我想先把 HTTP 弄明白", transport=transport)

    created = blueprint.generate_blueprint(conn, candidate_id, transport=transport)

    assert created["attempts"] == 2
    assert created["stages"][0]["tasks"][0]["title"] == "写一个请求客户端"
    # 重试原因点名了撞车的阶段与任务，并说清后果
    hint = transport.seen[2]["payload"]["messages"][-1]["content"]
    assert "学 HTTP" in hint and "读 MDN" in hint and "防重名闸" in hint


def test_generation_lands_nothing_when_both_attempts_clash(conn):
    """两次都撞：报错不落——一条明知批不了的提案都不该留在库里。"""
    make_provider(conn)
    add_profile(conn)
    candidate_id, plan_id = adopted_candidate(conn)
    existing_stage = plan.add_node(conn, plan_id, "stage", "学 HTTP")
    plan.add_node(conn, plan_id, "task", "读 MDN", parent_id=existing_stage)
    transport = ScriptedTransport(
        chat_reply([], ready=True),
        blueprint_json(stage("学 HTTP", tasks=[task("读 MDN")])),
        blueprint_json(stage("学 HTTP", tasks=[task("读 MDN"), task("写个 demo")])),
    )
    blueprint.say(conn, candidate_id, "我想先把 HTTP 弄明白", transport=transport)

    with pytest.raises(blueprint.BlueprintError):
        blueprint.generate_blueprint(conn, candidate_id, transport=transport)

    assert conn.execute(
        "SELECT COUNT(*) AS n FROM proposal WHERE kind = ?", (BLUEPRINT_KIND,)
    ).fetchone()["n"] == 0


def test_generation_is_blocked_while_the_last_turn_is_unanswered(conn):
    """最后一轮只留下你的话（它的回话失败或还没回）→ 409 拦下；补聊成一轮后才可出方案。"""
    make_provider(conn)
    add_profile(conn)
    candidate_id, plan_id = adopted_candidate(conn)
    transport = ScriptedTransport(
        chat_reply([], ready=True),                    # 第一轮聊成
        json.dumps({"questions": [], "ready": False}),   # 第二轮它没答成 → say 报错
        chat_reply([], ready=True),                     # 补聊的那一轮
        blueprint_json(stage("学 HTTP", tasks=[task("读 MDN")])),
    )
    blueprint.say(conn, candidate_id, "我想先把 HTTP 弄明白", transport=transport)
    with pytest.raises(blueprint.BlueprintError):  # 回话失败，只留下你的话
        blueprint.say(conn, candidate_id, "每周 6 小时，先抓基础", transport=transport)

    with pytest.raises(blueprint.BlueprintConflict) as conflict:
        blueprint.generate_blueprint(conn, candidate_id, transport=transport)
    assert "上一轮还没聊成" in str(conflict.value)
    assert len(transport.seen) == 2  # 被拦下时生成一次模型都没调

    blueprint.say(conn, candidate_id, "每周 6 小时，先抓基础", transport=transport)  # 补聊成
    created = blueprint.generate_blueprint(conn, candidate_id, transport=transport)
    assert created["plan_id"] == plan_id
