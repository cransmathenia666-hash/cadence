"""多计划与严格分开（T24，SPEC 决策 33 + 34）+ 计划生命周期四态（T27）。

覆盖六组：
① 计划列表：默认只给进行中的，暂停 / 收尾 / 作废要 `include_inactive` 才看得到；
② 收尾与作废：收尾是业务终态 `closed`（可重复调用）、作废走台账 `void`（理由必填）；
③ 候选的计划归属：「找」带计划上下文进 prompt、候选随请求继承归属、`GET /api/candidates` 带出来；
④ 候选过期：新一轮落库把**同计划**上一轮未裁定的标为 `expired`，过期 ≠ 否决（不进禁区）；
⑤ 暂停与重开（T27）：`paused` 是可逆的搁置，`void` 是单向门；两条新路由的状态码与回执形状；
⑥ `ended_at` / `ended_reason`：离开进行中那一刻的时间与理由，进行中时为 None。
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app import advisor, db, ledger, llm, main, plan, proposals, providers
from app.providers import find


class ScriptedTransport:
    """按脚本依次返回的假上游（与 test_candidates 里同一套写法）。"""

    def __init__(self, *texts: str) -> None:
        self._texts = list(texts)
        self.seen: list[dict] = []

    def __call__(self, url: str, headers: dict, payload: dict):
        self.seen.append({"url": url, "headers": headers, "payload": payload})
        if not self._texts:
            raise AssertionError("假上游被多调了一次：脚本里的回答已经用完")
        return 200, {
            "choices": [{"message": {"content": self._texts.pop(0)}}],
            "usage": {"prompt_tokens": 21, "completion_tokens": 13},
        }


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "test.db"
    db.init(path)
    connection = db.connect(path)
    yield connection
    connection.close()


def add_profile(conn, category: str, content: str) -> int:
    return ledger.create_active(
        conn, "profile_item", {"category": category, "content": content}, actor="user"
    )


def make_provider(conn) -> int:
    """建一家假 provider 并设为默认——「找」要解析默认 provider 才会走到假上游。"""
    provider_id = llm.create_provider(
        conn,
        name="假提供商",
        base_url="http://127.0.0.1:9999/v1",
        api_key="sk-fake-1234567890abcd",
        default_model="fake-model",
    )
    llm.update_provider(conn, provider_id, set_as_default=True)
    return provider_id


def make_plan(conn, goal: str) -> int:
    return ledger.create_active(conn, "plan", {"goal": goal}, actor="user")


def found_json(*titles: str) -> str:
    """一份合格的「找」输出（依据 id 由调用方保证存在）。"""
    return json.dumps(
        {
            "candidates": [
                {
                    "title": title,
                    "kind": "project",
                    "why": "依据不足：现有档案里没有直接相关的条目",
                    "depth_target": "够用",
                    "profile_item_ids": [],
                }
                for title in titles
            ],
            "recommended_start": titles[0],
            "start_reason": "先做这个",
        },
        ensure_ascii=False,
    )


# ---------- ① 计划列表 ----------

def test_list_plans_defaults_to_active_only(conn):
    first = make_plan(conn, "学英语")
    second = make_plan(conn, "学技术")
    closed = make_plan(conn, "做完了的")
    plan.close_plan(conn, closed)

    active_only = plan.list_plans(conn)
    assert [item["id"] for item in active_only] == [first, second]
    assert active_only[0]["goal"] == "学英语"
    assert active_only[0]["stages"] == 0 and active_only[0]["current_stage"] is None

    everything = plan.list_plans(conn, include_inactive=True)
    assert [item["id"] for item in everything] == [first, second, closed]
    assert everything[-1]["status"] == "closed"


def test_list_plans_carries_stage_progress(conn):
    plan_id = make_plan(conn, "学技术")
    stage_id = plan.add_node(conn, plan_id, "stage", "阶段 1")
    task_id = plan.add_node(conn, plan_id, "task", "任务 A", parent_id=stage_id)
    plan.check_task(conn, task_id)

    listed = plan.list_plans(conn)[0]

    assert listed["current_stage"] == {"id": stage_id, "title": "阶段 1"}
    assert listed["stages"] == 1 and listed["stages_finished"] == 0  # 还差交付物


# ---------- ② 收尾与作废 ----------

def test_close_plan_is_idempotent_and_leaves_trace(conn):
    plan_id = make_plan(conn, "做完了的")

    first = plan.close_plan(conn, plan_id, reason="都做完了")
    second = plan.close_plan(conn, plan_id)

    assert first["changed"] is True and second["changed"] is False
    assert plan.resolve_plan(conn, plan_id)["status"] == "closed"
    assert ledger.history(conn, "plan", plan_id)[-1]["reason"] == "都做完了"
    assert plan.list_plans(conn) == []  # 默认列表里不再出现


def test_void_plan_needs_reason_and_removes_it_from_the_list(conn):
    plan_id = make_plan(conn, "试建的垃圾")

    with pytest.raises(plan.PlanError):
        plan.void_plan(conn, plan_id, "   ")

    plan.void_plan(conn, plan_id, "试建，不要了")

    assert plan.list_plans(conn) == []
    assert plan.list_plans(conn, include_inactive=True)[0]["status"] == "void"
    assert ledger.history(conn, "plan", plan_id)[-1]["reason"] == "试建，不要了"
    assert plan.resolve_plan(conn) is None  # 最新 active 计划里不再有它


def test_plan_routes_map_missing_to_404_and_rules_to_400(conn):
    plan_id = make_plan(conn, "活的")

    with pytest.raises(HTTPException) as not_found:
        main.post_plan_void(999, main.PlanVoidIn(reason="x"), conn)
    assert not_found.value.status_code == 404
    with pytest.raises(HTTPException) as no_reason:
        main.post_plan_void(plan_id, main.PlanVoidIn(reason="  "), conn)
    assert no_reason.value.status_code == 400
    assert main.post_plan_close(plan_id, main.PlanCloseIn(reason=None), conn)["status"] == "closed"
    assert main.get_plans(False, conn) == {"plans": []}


# ---------- ⑤ 暂停与重开（T27：把「作废」拆成 paused 与 void） ----------

def test_pause_plan_hides_it_and_records_the_reason(conn):
    plan_id = make_plan(conn, "暂时不想学的")

    result = plan.pause_plan(conn, plan_id, reason="先把手上的做完")

    assert result == {"plan_id": plan_id, "status": "paused", "changed": True}
    assert plan.list_plans(conn) == []  # 默认列表里不再出现
    listed = plan.list_plans(conn, include_inactive=True)[0]
    assert listed["status"] == "paused"
    assert listed["ended_reason"] == "先把手上的做完"
    assert listed["ended_at"] is not None
    assert ledger.history(conn, "plan", plan_id)[-1]["after_value"] == "paused"


def test_pause_plan_defaults_the_reason(conn):
    plan_id = make_plan(conn, "懒得动它")

    plan.pause_plan(conn, plan_id)

    assert plan.list_plans(conn, include_inactive=True)[0]["ended_reason"] == "暂时不做了"


def test_pause_plan_is_idempotent_and_writes_nothing(conn):
    plan_id = make_plan(conn, "暂停两次")
    plan.pause_plan(conn, plan_id, reason="第一次")
    before = len(ledger.history(conn, "plan", plan_id))

    again = plan.pause_plan(conn, plan_id, reason="第二次")

    assert again["changed"] is False
    assert len(ledger.history(conn, "plan", plan_id)) == before  # 不写噪音流水
    assert plan.list_plans(conn, include_inactive=True)[0]["ended_reason"] == "第一次"


def test_pause_plan_refuses_closed_and_void(conn):
    closed = make_plan(conn, "做完了的")
    plan.close_plan(conn, closed)
    voided = make_plan(conn, "不该做的")
    plan.void_plan(conn, voided, "一开始就不该建")

    with pytest.raises(plan.PlanError):
        plan.pause_plan(conn, closed)
    with pytest.raises(plan.PlanError):
        plan.pause_plan(conn, voided)

    # 失败路径不改数据
    statuses = {row["id"]: row["status"] for row in conn.execute("SELECT id, status FROM plan")}
    assert statuses == {closed: "closed", voided: "void"}


def test_pause_plan_on_a_missing_plan_raises(conn):
    with pytest.raises(plan.PlanError):
        plan.pause_plan(conn, 999)


def test_reopen_plan_puts_paused_and_closed_back_to_active(conn):
    paused = make_plan(conn, "暂停的")
    closed = make_plan(conn, "收尾的")
    plan.pause_plan(conn, paused, reason="先放放")
    plan.close_plan(conn, closed, reason="做完了")

    first = plan.reopen_plan(conn, paused, reason="有空了，继续")
    second = plan.reopen_plan(conn, closed)  # 收尾的走同一条路：重开

    assert first == {"plan_id": paused, "status": "active", "changed": True}
    assert second == {"plan_id": closed, "status": "active", "changed": True}
    assert [item["id"] for item in plan.list_plans(conn)] == [paused, closed]
    # 台账多一条 status_change（paused|closed → active，带理由）
    last = ledger.history(conn, "plan", paused)[-1]
    assert (last["change_type"], last["before_value"], last["after_value"]) == (
        "status_change", "paused", "active",
    )
    assert last["reason"] == "有空了，继续"
    closing = ledger.history(conn, "plan", closed)[-1]
    assert (closing["before_value"], closing["after_value"]) == ("closed", "active")
    assert closing["reason"] == "继续做"  # 默认理由


def test_reopen_plan_is_idempotent_for_an_active_plan(conn):
    plan_id = make_plan(conn, "本来就是活的")

    result = plan.reopen_plan(conn, plan_id)

    assert result["changed"] is False
    assert len(ledger.history(conn, "plan", plan_id)) == 1  # 只有那条 create


def test_reopen_plan_refuses_a_void_plan(conn):
    plan_id = make_plan(conn, "根本不该做")
    plan.void_plan(conn, plan_id, "不该建")

    with pytest.raises(plan.PlanError):
        plan.reopen_plan(conn, plan_id)

    assert plan.resolve_plan(conn, plan_id)["status"] == "void"  # 单向门：纹丝不动


def test_close_plan_accepts_a_paused_plan(conn):
    plan_id = make_plan(conn, "暂停后又被判做完了")
    plan.pause_plan(conn, plan_id, reason="先放放")

    assert plan.close_plan(conn, plan_id, reason="其实做完了")["changed"] is True

    assert plan.resolve_plan(conn, plan_id)["status"] == "closed"


def test_plan_lifecycle_routes_carry_status_and_changed(conn):
    plan_id = make_plan(conn, "路由口径")

    with pytest.raises(HTTPException) as not_found:
        main.post_plan_pause(999, main.PlanPauseIn(), conn)
    assert not_found.value.status_code == 404
    with pytest.raises(HTTPException) as missing_reopen:
        main.post_plan_reopen(999, main.PlanReopenIn(), conn)
    assert missing_reopen.value.status_code == 404

    paused = main.post_plan_pause(plan_id, main.PlanPauseIn(reason="先放放"), conn)
    assert paused == {"plan_id": plan_id, "status": "paused", "changed": True}
    assert main.get_plans(False, conn) == {"plans": []}

    reopened = main.post_plan_reopen(plan_id, main.PlanReopenIn(reason=None), conn)
    assert reopened == {"plan_id": plan_id, "status": "active", "changed": True}

    # 规则拒绝走 400：收尾后不能再暂停，作废后不能再重开
    main.post_plan_close(plan_id, main.PlanCloseIn(reason=None), conn)
    with pytest.raises(HTTPException) as refused:
        main.post_plan_pause(plan_id, main.PlanPauseIn(), conn)
    assert refused.value.status_code == 400
    voided = make_plan(conn, "要作废的")
    main.post_plan_void(voided, main.PlanVoidIn(reason="不该建"), conn)
    with pytest.raises(HTTPException) as one_way:
        main.post_plan_reopen(voided, main.PlanReopenIn(), conn)
    assert one_way.value.status_code == 400


# ---------- ⑥ ended_at / ended_reason ----------

def test_active_plan_has_no_ending_fields(conn):
    make_plan(conn, "活着的")

    listed = plan.list_plans(conn)[0]

    assert listed["ended_at"] is None and listed["ended_reason"] is None


def test_ending_fields_track_the_last_time_it_left_active(conn):
    plan_id = make_plan(conn, "暂停又回来又收尾")
    plan.pause_plan(conn, plan_id, reason="先放放")
    plan.reopen_plan(conn, plan_id, reason="有空了")
    plan.close_plan(conn, plan_id, reason="这回真做完了")

    listed = plan.list_plans(conn, include_inactive=True)[0]

    assert listed["status"] == "closed"
    assert listed["ended_reason"] == "这回真做完了"  # 不是那条「先放放」
    assert listed["ended_at"] == ledger.history(conn, "plan", plan_id)[-1]["created_at"]


def test_void_plan_records_its_ending_too(conn):
    plan_id = make_plan(conn, "试建的垃圾")
    plan.void_plan(conn, plan_id, "试建，不要了")

    listed = plan.list_plans(conn, include_inactive=True)[0]

    assert (listed["status"], listed["ended_reason"]) == ("void", "试建，不要了")


# ---------- ⑦ 暂停 / 作废的计划不能再被批准收尾（proposals 判据同步） ----------

def _stage_advance_proposal(conn, plan_id: int) -> int:
    """造一条「后面没有更多阶段了」的推进提案，返回提案 id。"""
    stage_id = plan.add_node(conn, plan_id, "stage", "唯一阶段")
    task_id = plan.add_node(conn, plan_id, "task", "唯一的任务", parent_id=stage_id)
    plan.check_task(conn, task_id)
    result = plan.submit_deliverable(conn, stage_id, "https://example.com/out", "做完了")
    assert result["proposal_id"] is not None
    return int(result["proposal_id"])


def test_stage_advance_cannot_close_a_paused_plan(conn):
    plan_id = make_plan(conn, "暂停中的计划")
    proposal_id = _stage_advance_proposal(conn, plan_id)
    plan.pause_plan(conn, plan_id, reason="先放放")

    with pytest.raises(proposals.ProposalConflict):
        proposals.decide(conn, proposal_id, approved=True)

    # 提案保持 pending，计划还是 paused——验不过就一条都不写
    assert main.get_proposals(None, conn)["proposals"][0]["id"] == proposal_id
    assert plan.resolve_plan(conn, plan_id)["status"] == "paused"

    plan.reopen_plan(conn, plan_id, reason="回来收尾")

    decided = proposals.decide(conn, proposal_id, approved=True)

    assert decided["effect"] == "plan_closed"
    assert plan.resolve_plan(conn, plan_id)["status"] == "closed"


def test_stage_advance_cannot_close_a_void_plan(conn):
    plan_id = make_plan(conn, "要作废的计划")
    proposal_id = _stage_advance_proposal(conn, plan_id)
    plan.void_plan(conn, plan_id, "整件事不该做")

    with pytest.raises(proposals.ProposalConflict):
        proposals.decide(conn, proposal_id, approved=True)

    assert plan.resolve_plan(conn, plan_id)["status"] == "void"


# ---------- ③ 候选的计划归属 ----------

def test_find_prompt_carries_the_plan_context(conn):
    make_provider(conn)
    add_profile(conn, "long_axis", "走 Web 方向")
    project = make_plan(conn, "学技术")
    stage_id = plan.add_node(conn, project, "stage", "阶段 1")
    plan.add_node(conn, project, "task", "看完第 3 章", parent_id=stage_id)
    transport = ScriptedTransport(found_json("学 FastAPI", "学 SQL", "学 Linux"))

    advisor.find_candidates(conn, "我不知道学什么", plan_id=project, transport=transport)

    prompt = transport.seen[0]["payload"]["messages"][-1]["content"]
    assert "【这一轮针对的计划】" in prompt
    assert "学技术" in prompt and "阶段 1" in prompt and "看完第 3 章" in prompt


def test_find_without_plan_has_no_plan_section(conn):
    make_provider(conn)
    add_profile(conn, "long_axis", "走 Web 方向")
    make_plan(conn, "学技术")
    transport = ScriptedTransport(found_json("学 FastAPI", "学 SQL", "学 Linux"))

    result = advisor.find_candidates(conn, "我不知道学什么", transport=transport)

    prompt = transport.seen[0]["payload"]["messages"][-1]["content"]
    assert "【这一轮针对的计划】" not in prompt
    assert result["plan_id"] is None


def test_candidates_inherit_the_plan_of_their_round(conn):
    make_provider(conn)
    add_profile(conn, "long_axis", "走 Web 方向")
    project = make_plan(conn, "学技术")
    transport = ScriptedTransport(found_json("学 FastAPI", "学 SQL", "学 Linux"))
    found = advisor.find_candidates(conn, "我不知道学什么", plan_id=project, transport=transport)
    request_id = advisor.record_request(conn, "search", "我不知道学什么", project)
    advisor.propose_candidates(conn, request_id=request_id, result=found)

    listed = advisor.list_candidates(conn)

    assert listed["plan_id"] == project
    assert all(item["plan_id"] == project for item in listed["candidates"])


def test_find_rejects_a_plan_that_does_not_exist(conn):
    make_provider(conn)
    add_profile(conn, "long_axis", "走 Web 方向")
    with pytest.raises(advisor.AdvisorError):
        advisor.find_candidates(conn, "问一句", plan_id=999, transport=ScriptedTransport())


# ---------- ④ 候选过期（SPEC 决策 34） ----------

def test_new_round_expires_previous_undecided_in_the_same_plan(conn):
    first_plan = make_plan(conn, "学技术")
    second_plan = make_plan(conn, "学英语")
    first_request = advisor.record_request(conn, "search", "第一轮", first_plan)
    first_ids = advisor.propose_candidates(
        conn,
        request_id=first_request,
        result={
            "candidates": [
                {"title": "旧候选 A", "kind": "project", "why": "w", "depth_target": "够用"},
                {"title": "旧候选 B", "kind": "project", "why": "w", "depth_target": "够用"},
            ],
            "recommended_start": "旧候选 A",
            "start_reason": "先做这个",
            "source": {"name": "route_only", "networked": False},
        },
    )
    advisor.decide_candidate(conn, first_ids[1], accept=False, reason="和主线无关")

    # 另一个计划的轮次不受影响
    other_request = advisor.record_request(conn, "search", "英语那轮", second_plan)
    advisor.propose_candidates(
        conn,
        request_id=other_request,
        result={
            "candidates": [
                {"title": "英语候选", "kind": "course", "why": "w", "depth_target": "够用"}
            ],
            "recommended_start": "英语候选",
            "start_reason": "先做这个",
            "source": {"name": "route_only", "networked": False},
        },
    )

    # 同一计划再来一轮：上一轮未裁定的过期，已驳回的不动，别的计划的不动
    second_request = advisor.record_request(conn, "search", "第二轮", first_plan)
    advisor.propose_candidates(
        conn,
        request_id=second_request,
        result={
            "candidates": [
                {"title": "新候选", "kind": "project", "why": "w", "depth_target": "够用"}
            ],
            "recommended_start": "新候选",
            "start_reason": "先做这个",
            "source": {"name": "route_only", "networked": False},
        },
    )

    statuses = {
        int(row["id"]): row["status"]
        for row in conn.execute("SELECT id, status FROM candidate").fetchall()
    }
    assert statuses[first_ids[0]] == "expired"
    assert statuses[first_ids[1]] == "rejected"  # 裁定过的不动
    row = conn.execute(
        "SELECT c.status FROM candidate c JOIN learning_request r ON r.id = c.request_id WHERE r.plan_id = ?",
        (second_plan,),
    ).fetchone()
    assert row["status"] == "proposed"  # 别的计划不受影响


def test_expired_candidates_do_not_enter_the_forbidden_zone(conn):
    project = make_plan(conn, "学技术")
    first_request = advisor.record_request(conn, "search", "第一轮", project)
    advisor.propose_candidates(
        conn,
        request_id=first_request,
        result={
            "candidates": [
                {"title": "过期过的方向", "kind": "project", "why": "w", "depth_target": "够用"}
            ],
            "recommended_start": "过期过的方向",
            "start_reason": "先做这个",
            "source": {"name": "route_only", "networked": False},
        },
    )
    second_request = advisor.record_request(conn, "search", "第二轮", project)
    advisor.propose_candidates(
        conn,
        request_id=second_request,
        result={
            "candidates": [
                {"title": "新候选", "kind": "project", "why": "w", "depth_target": "够用"}
            ],
            "recommended_start": "新候选",
            "start_reason": "先做这个",
            "source": {"name": "route_only", "networked": False},
        },
    )

    # 过期 ≠ 否决：禁区里只该有 rejected 的标题
    assert "过期过的方向" not in advisor._rejected_titles(conn)


def test_plan_context_lines_are_empty_without_a_plan():
    brief = advisor._brief("问一句", {"items": [], "missing_categories": []}, [], None)
    assert brief.plan_context_lines == []
    assert find.describe(providers.find.DEFAULT_SOURCE)["networked"] is False
