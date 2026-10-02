"""「找」：候选清单生成与去重的单测（T13）。

同 `test_advisor.py` 的态度：一律用**假的上游**打桩，不打真实接口、不花钱。
覆盖四组东西：
① 条数约束（3–5，少一条多一条都不收）；
② 排序与「建议先从哪条开始」落库后的样子；
③ 去重——已否决的候选一个字都不许再出现（成功标准 2 的后半句）；
④ 裁定（采纳 / 否决）与它的留痕。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app import advisor, db, ledger, llm, main, plan
from app.main import RequestIn, VerdictIn


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
            "usage": {"prompt_tokens": 21, "completion_tokens": 13},
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


def add_profile(conn, category: str, content: str) -> int:
    return ledger.create_active(
        conn, "profile_item", {"category": category, "content": content}, actor="user"
    )


def candidate(title: str, ids: list[int], *, why: str | None = None, kind="concept", depth="够用"):
    return {
        "title": title,
        "kind": kind,
        "why": why or f"对主线有直接帮助：{title}",
        "depth_target": depth,
        "profile_item_ids": ids,
    }


def list_json(
    candidates: list[dict],
    *,
    start: str | None = None,
    reason="先把基础补齐",
    clarify: dict | None = None,
    shape: str = "directions",
    steps: list[dict] | None = None,
) -> str:
    """一份「找」的输出。`intent` 必填（2026-09-28 双入口整改），候选轮默认 candidates；
    `shape` 必填（T34），默认是「几条互相竞争的方向」。"""
    payload: dict = {
        "intent": "candidates",
        "shape": shape,
        "candidates": candidates,
        "recommended_start": start or candidates[0]["title"],
        "start_reason": reason,
    }
    if steps is not None:
        payload["steps"] = steps
    if clarify is not None:
        payload["clarify"] = clarify
    return json.dumps(payload, ensure_ascii=False)


def chat_json(reply: str = "你好呀，我在呢——想聊点什么？", **extra) -> str:
    """一份 intent=chat 的输出：只有 reply，不落候选不落追问。"""
    return json.dumps({"intent": "chat", "reply": reply, **extra}, ensure_ascii=False)


def need_info_json(question: str, missing: str, *, reply: str = "先问你一句", **extra) -> str:
    """一份 intent=need_info 的输出：一句 reply + 一条追问，无候选。"""
    return json.dumps(
        {
            "intent": "need_info",
            "reply": reply,
            "clarify": {"question": question, "missing": missing},
            **extra,
        },
        ensure_ascii=False,
    )


def four(ids: list[int], *, clarify: dict | None = None, **overrides) -> str:
    """一份合格的四条候选——最常用的底稿。"""
    return list_json(
        [
            candidate("Python 基础与工程实践", ids, depth="熟练", kind="course", **overrides),
            candidate("HTTP 与后端接口", ids, **overrides),
            candidate("SQLite 与数据持久化", ids, **overrides),
            candidate("部署一个能访问的小项目", ids, depth="够用", kind="project", **overrides),
        ],
        clarify=clarify,
    )


# ---------- 条数与排序 ----------

def test_valid_list_lands_candidates_in_priority_order(conn):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "通用工程基础 + 能上线的项目")
    transport = ScriptedTransport(four([axis]))

    result = advisor.find_candidates(conn, "我不知道该学什么", transport=transport)
    request_id = advisor.record_request(conn, "search", "我不知道该学什么")
    ids = advisor.propose_candidates(conn, request_id=request_id, result=result)

    assert result["recommended_start"] == "Python 基础与工程实践"
    assert result["attempts"] == 1
    rows = conn.execute("SELECT * FROM candidate ORDER BY rank").fetchall()
    assert len(rows) == 4
    # rank 就是列表顺序（顺序即优先级），前端不再自己排
    assert [row["rank"] for row in rows] == [1, 2, 3, 4]
    assert [row["title"] for row in rows][0] == "Python 基础与工程实践"
    assert [row["is_recommended"] for row in rows] == [1, 0, 0, 0]
    assert all(row["status"] == "proposed" for row in rows)
    assert all(row["request_id"] == request_id for row in rows)
    assert ids == [row["id"] for row in rows]
    # 来源要能自证是甲档（不联网）
    assert result["source"] == {"name": "route_only", "networked": False}


def test_two_candidates_are_too_few_and_get_one_retry(conn):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    too_few = list_json([candidate("只有一条", [axis]), candidate("只有两条", [axis])])
    transport = ScriptedTransport(too_few, four([axis]))

    result = advisor.find_candidates(conn, "我不知道该学什么", transport=transport)

    assert result["attempts"] == 2
    assert len(result["candidates"]) == 4
    # 重试时把「哪里不合格」讲给模型听，而不是让它重新猜
    retry_prompt = transport.seen[1]["payload"]["messages"][-1]["content"]
    assert "不合格" in retry_prompt


def test_six_candidates_are_too_many(conn):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    six = list_json([candidate(f"候选 {index}", [axis]) for index in range(6)])
    transport = ScriptedTransport(six, four([axis]))

    result = advisor.find_candidates(conn, "我不知道该学什么", transport=transport)

    assert result["attempts"] == 2
    assert len(result["candidates"]) == 4


def test_two_bad_attempts_raise_and_land_nothing(conn):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    too_few = list_json([candidate("一条", [axis])])
    transport = ScriptedTransport(too_few, too_few)

    with pytest.raises(advisor.AdvisorError):
        advisor.find_candidates(conn, "我不知道该学什么", transport=transport)

    assert transport._texts == []  # 正好两次，没有第三次
    assert conn.execute("SELECT COUNT(*) AS n FROM candidate").fetchone()["n"] == 0


# ---------- 与四问同一条验收底线 ----------

def test_basis_is_required_or_say_insufficient(conn):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    vague = list_json(
        [candidate(f"候选 {index}", [], why="学它很有前途") for index in range(3)]
    )
    transport = ScriptedTransport(vague, four([axis]))

    result = advisor.find_candidates(conn, "我不知道该学什么", transport=transport)

    assert result["attempts"] == 2  # 第一次被拦下


def test_insufficient_basis_is_accepted_when_spelled_out(conn):
    """确实没依据时如实说「依据不足」加空数组是允许的——同四问的 T12 口径。"""
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    honest = list_json(
        [
            candidate(
                f"候选 {index}",
                [],
                why="依据不足：当前状态与生活习惯都空着，排不出投入量",
            )
            for index in range(3)
        ]
    )
    transport = ScriptedTransport(honest)

    result = advisor.find_candidates(conn, "我不知道该学什么", transport=transport)

    assert result["attempts"] == 1
    assert len(result["candidates"]) == 3


def test_invented_profile_id_is_invalid(conn):
    make_provider(conn)
    add_profile(conn, "long_axis", "主线")
    invented = list_json([candidate(f"候选 {index}", [9999]) for index in range(3)])
    transport = ScriptedTransport(invented, four([1]))

    result = advisor.find_candidates(conn, "我不知道该学什么", transport=transport)

    assert result["attempts"] == 2


def test_recommended_start_must_match_a_title(conn):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    mismatch = list_json(
        [candidate(f"候选 {index}", [axis]) for index in range(3)], start="不存在的标题"
    )
    transport = ScriptedTransport(mismatch, four([axis]))

    result = advisor.find_candidates(conn, "我不知道该学什么", transport=transport)

    assert result["attempts"] == 2


def test_no_profile_means_not_a_single_model_call(conn):
    """档案空着就不该花钱：一次模型都不调，直接报错让你先补档案。"""
    transport = ScriptedTransport()  # 一旦被调就 AssertionError

    with pytest.raises(advisor.AdvisorError):
        advisor.find_candidates(conn, "我不知道该学什么", transport=transport)

    assert transport.seen == []


# ---------- 去重：否决过的不再出现 ----------

def _reject(conn, request_id: int, title: str, reason: str = "不想学这个") -> int:
    candidate_id = ledger.create_active(
        conn,
        "candidate",
        {"request_id": request_id, "title": title, "why": "w", "depth_target": "够用", "rank": 1},
        actor="agent",
        reason="测试用",
    )
    advisor.decide_candidate(conn, candidate_id, accept=False, reason=reason)
    return candidate_id


def test_rejected_title_is_listed_as_forbidden_and_blocks_the_output(conn):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    request_id = advisor.record_request(conn, "search", "上一轮")
    _reject(conn, request_id, "学 Rust")

    repeated = list_json(
        [
            candidate("学 Rust", [axis]),
            candidate("候选 B", [axis]),
            candidate("候选 C", [axis]),
        ]
    )
    transport = ScriptedTransport(repeated, four([axis]))

    result = advisor.find_candidates(conn, "我不知道该学什么", transport=transport)

    assert result["attempts"] == 2  # 又推了禁过的 → 判不合格
    assert result["banned_titles"] == ["学 Rust"]
    assert "学 Rust" in transport.seen[0]["payload"]["messages"][-1]["content"]  # 第一轮就写进禁区
    assert "学 Rust" not in [item["title"] for item in result["candidates"]]


@pytest.mark.parametrize("variant", ["学Rust", "学 rust", " 学 Rust "])
def test_forbidden_match_ignores_spacing_and_case(conn, variant):
    """「学Rust」「学 rust」「 学 Rust 」是同一件事——归一化后一律算禁区。"""
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    request_id = advisor.record_request(conn, "search", "上一轮")
    _reject(conn, request_id, "学 Rust")

    sneaky = list_json(
        [candidate(variant, [axis]), candidate("候选 B", [axis]), candidate("候选 C", [axis])]
    )
    transport = ScriptedTransport(sneaky, four([axis]))

    assert advisor.find_candidates(conn, "我不知道该学什么", transport=transport)["attempts"] == 2


def test_two_bad_attempts_with_forbidden_title_land_nothing(conn):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    request_id = advisor.record_request(conn, "search", "上一轮")
    _reject(conn, request_id, "学 Rust")
    repeated = list_json(
        [candidate("学 Rust", [axis]), candidate("B", [axis]), candidate("C", [axis])]
    )
    transport = ScriptedTransport(repeated, repeated)

    with pytest.raises(advisor.AdvisorError):
        advisor.find_candidates(conn, "我不知道该学什么", transport=transport)

    assert conn.execute("SELECT COUNT(*) AS n FROM candidate").fetchone()["n"] == 1  # 只有那条旧的


# ---------- 裁定与留痕 ----------

def test_reject_requires_reason_and_leaves_trace(conn):
    request_id = advisor.record_request(conn, "search", "问过")
    candidate_id = ledger.create_active(
        conn,
        "candidate",
        {"request_id": request_id, "title": "学 Rust", "why": "w", "depth_target": "够用", "rank": 1},
        actor="agent",
        reason="测试用",
    )

    with pytest.raises(advisor.AdvisorError):
        advisor.decide_candidate(conn, candidate_id, accept=False, reason="  ")

    result = main.post_candidate_verdict(
        candidate_id, VerdictIn(accept=False, reason="和主线无关"), conn
    )

    assert result["status"] == "rejected"
    assert result["plan_id"] is None and result["node_id"] is None  # 否决不建任何节点
    row = conn.execute("SELECT * FROM candidate WHERE id = ?", (candidate_id,)).fetchone()
    assert row["status"] == "rejected"
    assert row["reject_reason"] == "和主线无关"
    events = ledger.history(conn, "candidate", candidate_id)
    assert [event["change_type"] for event in events] == ["create", "status_change"]
    assert events[-1]["reason"] == "和主线无关"


def test_accept_and_the_two_refusals(conn):
    plan_id = ledger.create_active(conn, "plan", {"goal": "测试计划"}, actor="user")
    request_id = advisor.record_request(conn, "search", "问过")
    candidate_id = ledger.create_active(
        conn,
        "candidate",
        {"request_id": request_id, "title": "学 HTTP", "why": "w", "depth_target": "够用", "rank": 1},
        actor="agent",
        reason="测试用",
    )

    # 「新方向」的候选没有计划归属，采纳时可用 plan_id 指明延续哪个已有计划（规划落点）
    result = advisor.decide_candidate(conn, candidate_id, accept=True, plan_id=plan_id)
    assert result["status"] == "accepted"
    # OC-05：采纳进入规划——创建 planning_session 且不建阶段
    assert result["planning_session_id"] is not None
    assert result["created_planning_session"] is True
    assert result["planning_status"] == "needs_blueprint"
    assert result["node_id"] is None
    session = advisor.get_planning_session(conn, result["planning_session_id"])
    assert session["status"] == "active"
    assert int(session["landing_plan_id"]) == plan_id
    assert int(session["candidate_id"]) == candidate_id

    # 已经裁定过的不能再改
    with pytest.raises(advisor.CandidateConflict):
        advisor.decide_candidate(conn, candidate_id, accept=False, reason="反悔")
    # 不存在的候选
    with pytest.raises(advisor.CandidateNotFound):
        advisor.decide_candidate(conn, 999, accept=True)


def make_candidate(conn, title: str, plan_id: int | None = None) -> int:
    request_id = advisor.record_request(conn, "search", "问过", plan_id)
    return ledger.create_active(
        conn,
        "candidate",
        {"request_id": request_id, "title": title, "why": "w", "depth_target": "够用", "rank": 1},
        actor="agent",
        reason="测试用",
    )


# ---------- 采纳落点（2026-10-01 OC-05 起：采纳只记规划落点 + 开规划会话，不再建阶段） ----------

def test_accept_lands_in_the_attributed_plan_without_building_a_stage(conn):
    attributed = ledger.create_active(conn, "plan", {"goal": "被指定的计划"}, actor="user")
    ledger.create_active(conn, "plan", {"goal": "更新的计划"}, actor="user")  # 更新，但不该落它
    candidate_id = make_candidate(conn, "学 HTTP", plan_id=attributed)

    result = main.post_candidate_verdict(candidate_id, VerdictIn(accept=True), conn)

    assert result["status"] == "accepted"
    assert result["plan_id"] == attributed  # 兼容字段：语义是「规划落点」
    assert result["landing_plan_id"] == attributed
    assert result["node_id"] is None  # 采纳不再建阶段
    # 计划结构一个没动：没有新阶段、没有新节点
    assert plan.get_stages(conn, attributed) == []
    assert conn.execute("SELECT COUNT(*) AS n FROM plan_node").fetchone()["n"] == 0
    # 落点与规划会话都留住了
    row = conn.execute("SELECT * FROM candidate WHERE id = ?", (candidate_id,)).fetchone()
    assert row["landing_plan_id"] == attributed
    session = advisor.get_planning_session(conn, result["planning_session_id"])
    assert session["status"] == "active"
    events = ledger.history(conn, "planning_session", result["planning_session_id"])
    assert [event["change_type"] for event in events] == ["create"]


def test_accept_without_attribution_adopts_as_a_new_direction(conn):
    """OC-05：「新方向」的候选没有现存 active 计划也能采纳——只开规划会话，不建正式计划。"""
    candidate_id = make_candidate(conn, "学 HTTP")  # 「新方向」：没有计划归属

    result = advisor.decide_candidate(conn, candidate_id, accept=True)

    assert result["status"] == "accepted"
    assert result["plan_id"] is None and result["landing_plan_id"] is None
    assert result["node_id"] is None
    # 不创建正式计划、更不会出现 plan.status=draft 之类的中间状态
    assert conn.execute("SELECT COUNT(*) AS n FROM plan").fetchone()["n"] == 0
    session = advisor.get_planning_session(conn, result["planning_session_id"])
    assert session["status"] == "active"
    assert session["landing_plan_id"] is None  # 新方向：转换（蓝图批准）前没有落点

    # 指明延续某个已有计划的采纳照旧记录落点
    plan_id = ledger.create_active(conn, "plan", {"goal": "某计划"}, actor="user")
    other = make_candidate(conn, "学后端")
    assert advisor.decide_candidate(conn, other, accept=True, plan_id=plan_id)["plan_id"] == plan_id


def test_accept_refuses_a_plan_that_contradicts_the_attribution(conn):
    attributed = ledger.create_active(conn, "plan", {"goal": "A"}, actor="user")
    other = ledger.create_active(conn, "plan", {"goal": "B"}, actor="user")
    candidate_id = make_candidate(conn, "学 HTTP", plan_id=attributed)

    with pytest.raises(advisor.CandidateConflict):
        advisor.decide_candidate(conn, candidate_id, accept=True, plan_id=other)


def test_accept_refuses_a_closed_plan(conn):
    plan_id = ledger.create_active(conn, "plan", {"goal": "已收尾"}, actor="user")
    plan.close_plan(conn, plan_id)
    candidate_id = make_candidate(conn, "学 HTTP", plan_id=plan_id)

    with pytest.raises(advisor.CandidateConflict):
        advisor.decide_candidate(conn, candidate_id, accept=True)


def test_accept_with_same_title_open_stage_keeps_the_plan_untouched(conn):
    """OC-05：同名开着的阶段不再是采纳的阻碍——采纳只记归属，计划结构一个不碰。"""
    plan_id = ledger.create_active(conn, "plan", {"goal": "计划"}, actor="user")
    plan.add_node(conn, plan_id, "stage", "学 HTTP")
    candidate_id = make_candidate(conn, "学 HTTP")

    result = advisor.decide_candidate(conn, candidate_id, accept=True)

    assert result["status"] == "accepted"
    stages = plan.get_stages(conn, plan_id)
    assert len(stages) == 1  # 没有第二条同名阶段被建出来
    assert conn.execute("SELECT COUNT(*) AS n FROM plan_node").fetchone()["n"] == 1


def test_list_candidates_defaults_to_the_latest_search(conn):
    first_request = advisor.record_request(conn, "search", "第一轮")
    second_request = advisor.record_request(conn, "search", "第二轮")
    ledger.create_active(
        conn,
        "candidate",
        {"request_id": first_request, "title": "旧候选", "why": "w", "depth_target": "够用", "rank": 1},
        actor="agent",
        reason="测试用",
    )
    ledger.create_active(
        conn,
        "candidate",
        {
            "request_id": second_request,
            "title": "新候选",
            "why": "w",
            "depth_target": "熟练",
            "rank": 1,
            "is_recommended": 1,
        },
        actor="agent",
        reason="测试用",
    )

    latest = advisor.list_candidates(conn)
    assert latest["request_id"] == second_request
    assert latest["raw_text"] == "第二轮"
    assert [item["title"] for item in latest["candidates"]] == ["新候选"]
    assert latest["recommended"]["title"] == "新候选"
    # 显式指定就是那一轮
    assert list(advisor.list_candidates(conn, first_request)["candidates"])[0]["title"] == "旧候选"


def test_list_candidates_without_any_search_is_empty_not_an_error(conn):
    assert advisor.list_candidates(conn) == {
        "request_id": None,
        "raw_text": None,
        "plan_id": None,
        "thread_id": None,
        "candidates": [],
        "recommended": None,
    }


# ---------- 反馈流水（T25：SPEC 决策 35 ①） ----------
#
# 这一段回答的是「我上次为什么不要那条」——光有禁区（标题不许重复）不足以让模型知道
# 我的偏好，所以把最近几轮的表态连理由原文一起发过去。

def settle(
    conn, request_id: int, title: str, status: str, reason: str | None = None
) -> int:
    """造一条指定状态的候选——不裁定就落不了反馈流水，这里直接把它推到终态。"""
    candidate_id = ledger.create_active(
        conn,
        "candidate",
        {"request_id": request_id, "title": title, "why": "w", "depth_target": "够用", "rank": 1},
        actor="agent",
        reason="测试用",
    )
    if status == "rejected":
        advisor.decide_candidate(conn, candidate_id, accept=False, reason=reason or "不想学")
    elif status == "accepted":
        # 采纳要落进候选自带的归属计划（决策 33 ②）；归属为空就现建一个
        inherited = conn.execute(
            "SELECT plan_id FROM learning_request WHERE id = ?", (request_id,)
        ).fetchone()["plan_id"]
        plan_id = inherited or ledger.create_active(
            conn, "plan", {"goal": f"为「{title}」建的计划"}, actor="user"
        )
        advisor.decide_candidate(conn, candidate_id, accept=True, plan_id=plan_id)
    elif status == "expired":
        ledger.set_status(conn, "candidate", candidate_id, "expired", actor="agent", reason="过期")
    return candidate_id


def prompt_of(transport: ScriptedTransport) -> str:
    return transport.seen[0]["payload"]["messages"][-1]["content"]


def test_feedback_carries_verdicts_reasons_and_skips_the_undecided(conn):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    plan_id = ledger.create_active(conn, "plan", {"goal": "学英语"}, actor="user")
    attributed = advisor.record_request(conn, "search", "上一轮", plan_id)
    settle(conn, attributed, "已否决的方向", "rejected", "和主线无关")
    settle(conn, attributed, "已采纳的方向", "accepted")
    settle(conn, attributed, "过期的方向", "expired")
    settle(conn, attributed, "还没表态的方向", "proposed")
    free = advisor.record_request(conn, "search", "更早那轮")
    settle(conn, free, "新方向里的候选", "rejected", "太贵")

    transport = ScriptedTransport(four([axis]))
    result = advisor.find_candidates(conn, "我不知道该学什么", transport=transport)
    prompt = prompt_of(transport)

    assert "已否决的方向（已否决：和主线无关）" in prompt  # 理由原文，不是只有「否决」两个字
    assert "已采纳的方向（已采纳）" in prompt
    assert "过期的方向（已过期（我没表态，不算否决））" in prompt
    assert "还没表态的方向" not in prompt  # 你还没表态的，不当反馈喂回去
    assert f"计划 #{plan_id}" in prompt  # 每轮带计划归属
    assert "新方向（不属于任何计划）" in prompt
    # 时间正序：先提的那轮在前，后提的那轮在后
    lines = result["feedback_lines"]
    assert "已否决的方向" in lines[0] and f"计划 #{plan_id}" in lines[0]
    assert "新方向里的候选" in lines[1]


def test_feedback_keeps_only_the_last_five_rounds(conn):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    for index in range(1, 7):  # 六轮，只该记住后五轮
        request_id = advisor.record_request(conn, "search", f"第 {index} 轮")
        settle(conn, request_id, f"方向{index}", "rejected", f"理由{index}")

    transport = ScriptedTransport(four([axis]))
    advisor.find_candidates(conn, "我不知道该学什么", transport=transport)
    prompt = prompt_of(transport)

    assert "方向1（" not in prompt  # 最老那一轮被挤出窗口
    assert all(f"方向{index}（" in prompt for index in range(2, 7))


def test_feedback_is_truncated_from_the_oldest_by_char_limit(conn):
    """整段超过 1200 字符时从最旧截断——越近的表态越该被记住。"""
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    long_reason = "理由" * 100  # 一条理由就 200 字，五行必定超上限
    for index in range(1, 7):
        request_id = advisor.record_request(conn, "search", f"第 {index} 轮")
        settle(conn, request_id, f"方向{index}", "rejected", long_reason)

    transport = ScriptedTransport(four([axis]))
    result = advisor.find_candidates(conn, "我不知道该学什么", transport=transport)
    lines = result["feedback_lines"]

    assert len(lines) < 6  # 真的截了
    assert "方向6（" in lines[-1] and "方向1（" not in "".join(lines)  # 留最新、丢最旧
    assert sum(len(line) for line in lines) <= advisor.FEEDBACK_CHAR_LIMIT


def test_feedback_ignores_four_question_rounds(conn):
    """四问（evaluate）记录不进这段——它回答的是「这份资料值不值得学」，不是方向偏好。

    注意它的标题仍会进**禁区**（`_rejected_titles` 只看候选状态、不看请求类别）：
    禁区的口径是「我否决过这个」，与它从哪个入口来无关。这里钉的是反馈流水那一段。
    """
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    evaluate = advisor.record_request(conn, "evaluate", "我看了一个教程")
    settle(conn, evaluate, "四问那一轮的候选", "rejected", "不要")

    transport = ScriptedTransport(four([axis]))
    result = advisor.find_candidates(conn, "我不知道该学什么", transport=transport)

    assert result["feedback_lines"] == []


# ---------- 追问槽位（T25：SPEC 决策 35 ②） ----------

def test_clarify_rides_along_without_replacing_the_list(conn):
    """candidates 轮可带追问（清单照给），它记在本轮请求行上、清单照常落库。"""
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    asked = {"question": "你现在每周能稳定投入几小时？", "missing": "当前状态"}
    transport = ScriptedTransport(four([axis], clarify=asked))

    result = advisor.find_candidates(conn, "我不知道该学什么", transport=transport)

    assert result["attempts"] == 1
    assert len(result["candidates"]) == 4
    assert result["clarify"] == asked
    assert "clarify" in prompt_of(transport)  # 这个槽位真写进了 prompt

    request_id = advisor.record_request(conn, "search", "问过")
    advisor.propose_candidates(conn, request_id=request_id, result=result)
    assert "clarify" not in advisor.list_candidates(conn)  # 只活在当次响应里
    row = conn.execute("SELECT * FROM candidate LIMIT 1").fetchone()
    assert "clarify" not in row.keys()  # 没为它建列


@pytest.mark.parametrize(
    "clarify",
    [
        {"question": "你想学什么？", "missing": "还不清楚"},  # 空泛追问
        {"question": "你想学什么？", "missing": "很多方面"},  # 没点名任何一类档案
    ],
)
def test_vague_clarify_is_unqualified_and_gets_one_retry(conn, clarify):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    transport = ScriptedTransport(four([axis], clarify=clarify), four([axis]))

    result = advisor.find_candidates(conn, "我不知道该学什么", transport=transport)

    assert result["attempts"] == 2
    assert result["clarify"] is None  # 第二次没追问，就干净地没有
    assert "没说清缺哪类档案信息" in transport.seen[1]["payload"]["messages"][-1]["content"]


@pytest.mark.parametrize(
    "clarify",
    [
        {"question": "   ", "missing": "当前状态"},
        {"question": "想问你一件事", "missing": "  "},
    ],
)
def test_clarify_needs_both_fields(conn, clarify):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    transport = ScriptedTransport(four([axis], clarify=clarify), four([axis]))

    assert advisor.find_candidates(conn, "我不知道该学什么", transport=transport)["attempts"] == 2


def test_clarify_accepts_the_english_token_too(conn):
    """中文名或英文 token 都算点名——判据宽松的那一面也钉住。"""
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    transport = ScriptedTransport(
        four([axis], clarify={"question": "你的作息是怎样的？", "missing": "life_habit"})
    )

    result = advisor.find_candidates(conn, "我不知道该学什么", transport=transport)

    assert result["attempts"] == 1
    assert result["clarify"]["missing"] == "life_habit"


# ---------- 追问只问「关于我的一件事」（T36：SPEC 决策 41） ----------
#
# 追问与规划对话各管一段：追问问的是**关于你的事实**（档案里缺的那类，一句话能答），
# 规划对话问的是**这条路怎么走**（意向、节奏、取舍）。下面的闸堵的就是后者越界过来。

@pytest.mark.parametrize(
    "question",
    [
        "你打算先学哪一块？",           # 先学哪个
        "这两块你打算怎么安排顺序？",     # 怎么排
        "你想分几步走？",               # 分几步
        "这一步的学习路线怎么定？",       # 学习路线
        "每周 5 小时和 10 小时你怎么取舍？",  # 取舍
    ],
)
def test_planning_question_is_unqualified_and_gets_one_retry(conn, question):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    transport = ScriptedTransport(
        four([axis], clarify={"question": question, "missing": "当前状态"}), four([axis])
    )

    result = advisor.find_candidates(conn, "我不知道该学什么", transport=transport)

    assert result["attempts"] == 2
    assert result["clarify"] is None
    assert "规划对话该问的" in transport.seen[1]["payload"]["messages"][-1]["content"]


@pytest.mark.parametrize(
    "question",
    [
        "你现在每周能稳定投入几小时？还是说只有周末有时间？",  # 一次问了好几件事
        "关于你的情况" + "我" * 80 + "，你觉得呢？",            # 写成了一长段
        "你的作息是怎样的？\n第二行还写了别的",                 # 换行 = 一段话
    ],
)
def test_clarify_must_be_one_short_question(conn, question):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    transport = ScriptedTransport(
        four([axis], clarify={"question": question, "missing": "当前状态"}), four([axis])
    )

    assert advisor.find_candidates(conn, "我不知道该学什么", transport=transport)["attempts"] == 2


def test_clarify_about_a_fact_is_still_fine(conn):
    """堵的是规划类追问，不是追问本身：「你每周能投入几小时」照旧合格。"""
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    transport = ScriptedTransport(
        four([axis], clarify={"question": "你现在每周能稳定投入几小时？", "missing": "当前状态"})
    )

    result = advisor.find_candidates(conn, "我不知道该学什么", transport=transport)

    assert result["attempts"] == 1
    assert result["clarify"]["question"] == "你现在每周能稳定投入几小时？"


# ---------- 已回答的追问进反馈流水（T36） ----------

def test_answered_clarify_is_composed_and_remembered(conn):
    """回答追问：① 拼成「原问题 + 我的回答」的下一轮输入 ② 那一轮进反馈流水 ③ 不再重复问。"""
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    asked = {"question": "你现在每周能稳定投入几小时？", "missing": "当前状态"}

    first_id = advisor.record_request(conn, "search", "学 agent 开发怎么学")
    advisor.record_clarify(conn, first_id, asked)

    answer = "每周大概 5 小时"
    asked_row = advisor.pending_clarify(conn, None)
    assert asked_row is not None and asked_row["question"] == asked["question"]
    advisor.mark_clarify_answered(conn, asked_row["request_id"], answer)
    composed = f"{asked_row['question']}\n我的回答：{answer}"

    assert composed.startswith(asked["question"]) and answer in composed  # 前提没丢
    assert advisor.pending_clarify(conn, None) is None  # 答过就不再是「待答」

    second_id = advisor.record_request(conn, "search", composed)
    assert second_id != first_id
    transport = ScriptedTransport(four([axis]))
    result = advisor.find_candidates(conn, composed, transport=transport)

    line = next(line for line in result["feedback_lines"] if "我问过" in line)
    assert asked["question"] in line and answer in line
    assert "别再问第二遍" in prompt_of(transport)


# ---------- 已答追问的禁区与重问拦截（2026-09-26 走查整改） ----------
#
# 走查抓到的问题：答过「手大概多久恢复」，下一轮它换个说法又绕回手。根因是已答清单只
# 埋在反馈流水里、没进输出契约。改后：已答清单单独成段当禁区，重问（逐字 / 近逐字）在校验里硬拦。


def seed_answered_clarify(conn, question: str, answer: str, plan_id: int | None = None) -> None:
    """造一轮「问过且已答」的追问，作为下一轮的背景。"""
    request_id = advisor.record_request(conn, "search", "随便一轮「找」", plan_id)
    advisor.record_clarify(conn, request_id, {"question": question, "missing": "当前状态"})
    advisor.mark_clarify_answered(conn, request_id, answer)


def test_answered_clarify_gets_its_own_forbidden_zone(conn):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    seed_answered_clarify(conn, "你的手大概多久能恢复？", "医生说两周左右")
    transport = ScriptedTransport(four([axis]))

    advisor.find_candidates(conn, "手受伤恢复期间能学点什么", transport=transport)

    prompt = prompt_of(transport)
    assert "已答过的追问" in prompt and "医生说两周左右" in prompt
    # 这次的问题提到最前：档案是依据，不是选题范围
    assert prompt.index("【这次的问题】") < prompt.index("【我的长期档案】")
    assert "无关" in prompt and "第一硬约束" in prompt
    assert "与它无关但更值得做的事" not in prompt  # 旧的「无关也可以给」口子已收


def test_reasking_an_answered_clarify_is_rejected(conn):
    """逐字 / 近逐字的重问被判不合格，重试理由里带上我的原话回答。"""
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    seed_answered_clarify(conn, "你现在每周能稳定投入几小时？", "每周大概 5 小时")
    transport = ScriptedTransport(
        four([axis], clarify={"question": "你现在每周能稳定投入几小时呢？", "missing": "当前状态"}),
        four([axis]),
    )

    result = advisor.find_candidates(conn, "我不知道该学什么", transport=transport)

    assert result["attempts"] == 2 and result["clarify"] is None
    retry = transport.seen[1]["payload"]["messages"][-1]["content"]
    assert "我已经答过" in retry and "每周大概 5 小时" in retry


def test_a_new_clarify_on_another_fact_is_still_fine(conn):
    """禁区拦的是重问，不是追问本身：换一件关于我的事实照常合格。"""
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    seed_answered_clarify(conn, "你现在每周能稳定投入几小时？", "每周大概 5 小时")
    transport = ScriptedTransport(
        four([axis], clarify={"question": "你现在用什么设备写代码？", "missing": "生活记录"})
    )

    result = advisor.find_candidates(conn, "我不知道该学什么", transport=transport)

    assert result["attempts"] == 1
    assert result["clarify"]["question"] == "你现在用什么设备写代码？"


def test_answered_clarifies_stay_in_their_own_plan_scope(conn):
    """禁区按计划归属取（与 pending_clarify 同一把尺）：只问没答的不算，别计划的也不串台。"""
    seed_answered_clarify(conn, "新方向这边答过的事", "答了")
    seed_answered_clarify(conn, "计划这边没答的事", "")  # 只问没答 → 不进禁区
    seed_answered_clarify(conn, "计划这边答过的事", "答了", plan_id=7)

    assert advisor._answered_clarifies(conn, None) == [("新方向这边答过的事", "答了")]
    assert advisor._answered_clarifies(conn, 7) == [("计划这边答过的事", "答了")]


def test_answer_without_a_pending_clarify_is_kept_as_supplement(conn, monkeypatch):
    """没有待答的追问（页面刷新过）时不吞掉那句话——缀在 raw_text 后面照常发出去。"""
    make_provider(conn)
    add_profile(conn, "long_axis", "主线")
    seen: dict = {}

    def fake_find(conn_, raw_text, **kwargs):
        seen["raw_text"] = raw_text
        raise advisor.AdvisorError("到这儿就够了")

    monkeypatch.setattr(advisor, "find_candidates", fake_find)
    with pytest.raises(HTTPException):
        main.post_request(
            RequestIn(kind="search", raw_text="原来那句困惑", clarify_answer="每周 5 小时"), conn
        )

    assert seen["raw_text"] == "原来那句困惑\n补充：每周 5 小时"


def test_clarify_answer_keeps_the_original_direction(conn, monkeypatch):
    """回答追问后的输入必须仍带着最初那句原话（2026-09-26 走查整改）。

    原来只拼「追问问题 + 我的回答」，原话从第二轮追问起就从输入里消失，模型只能拿档案
    旧主线当方向，产出一堆无关候选。现在原话打头，追问与回答缀在后面。
    """
    make_provider(conn)
    add_profile(conn, "long_axis", "主线")
    original = "我主要提高口语和阅读能力，英语零基础"
    first_id = advisor.record_request(conn, "search", original)
    advisor.record_clarify(
        conn,
        first_id,
        {"question": "医生预计你的手多久能恢复到可以敲键盘？", "missing": "生活记录"},
    )
    seen: dict = {}

    def fake_find(conn_, raw_text, **kwargs):
        seen["raw_text"] = raw_text
        raise advisor.AdvisorError("到这儿就够了")

    monkeypatch.setattr(advisor, "find_candidates", fake_find)
    with pytest.raises(HTTPException):
        main.post_request(
            RequestIn(kind="search", raw_text=original, clarify_answer="现在已经正常",
                      clarify_request_id=first_id),
            conn,
        )

    assert seen["raw_text"].startswith(original)  # 原话在场，模型才有得直接回应
    assert "医生预计" in seen["raw_text"] and "现在已经正常" in seen["raw_text"]
    # 失败的轮次不消费回答（I-03 原子化）：追问保持待答，可原样重试
    assert advisor.pending_clarify(conn, None) is not None


def test_prompt_layers_keep_contract_and_voice_separate(conn):
    """提示词四层拆分（2026-09-26 语气整改）：契约与纪律的关键句都在，语气段也在。

    层一（人设语气）/ 层二（数据）/ 层三（纪律）/ 层四（输出契约）——语气以后随便改，
    这几句契约措辞（与 advisor 校验咬合）不许被顺手动掉。
    """
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    seed_answered_clarify(conn, "你现在每周能稳定投入几小时？", "每周大概 5 小时")
    transport = ScriptedTransport(four([axis]))

    advisor.find_candidates(conn, "学英语口语和阅读", transport=transport)

    prompt = prompt_of(transport)
    # 层四契约：这几句与校验咬合，措辞冻结
    assert "第一硬约束" in prompt
    assert "只输出一个 JSON 对象" in prompt
    assert "一字不差" in prompt
    assert "clarify" in prompt
    # 层三纪律：追问禁区与原话回应
    assert "已答过的追问" in prompt and "依据不足" in prompt
    # 层一人设与语气段：人设句在 system 消息里（prompt_of 只取用户消息，所以单独看）
    system_msg = transport.seen[0]["payload"]["messages"][0]["content"]
    assert "懂行又关心他的朋友" in system_msg
    assert "推荐理由怎么写" in prompt and "3–5 句" in prompt


# ---------- 路径形状（T34：SPEC 决策 41） ----------
#
# 用户说清一个方向时，「找」给的应该是一条路（一个方向 + 它的几个先后步骤），而不是五个方向：
# 五个方向各自采纳/否决，而否决＝永久拉黑——「这步暂时不用」被误读成「这步永远别给我」。

def step(title: str, *, deliverable: str = "交一个能跑通的小东西", why: str = "不先做它后面那步做不了"):
    return {"title": title, "deliverable": deliverable, "why": why}


def path_json(ids: list[int], *, steps: list[dict] | None = None, **overrides) -> str:
    """一份 `path` 形状的输出：1 条伞候选 + 2–8 个步骤。"""
    return list_json(
        [candidate("从零到部署学通 agent 开发", ids, **overrides)],
        shape="path",
        steps=steps if steps is not None else [step("先把异步基础打牢"), step("写一个能跑通的循环"), step("部署上线并写复盘")],
    )


def test_path_lands_one_umbrella_candidate_carrying_its_steps(conn):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "通用工程基础 + 能上线的项目")
    steps = [step("先把异步基础打牢"), step("写一个能跑通的循环"), step("部署上线并写复盘")]
    transport = ScriptedTransport(path_json([axis], steps=steps))

    result = advisor.find_candidates(conn, "学 agent 开发怎么学", transport=transport)
    request_id = advisor.record_request(conn, "search", "学 agent 开发怎么学")
    ids = advisor.propose_candidates(conn, request_id=request_id, result=result)

    assert result["shape"] == "path"
    assert [item["title"] for item in result["candidates"]] == ["从零到部署学通 agent 开发"]
    assert [item["title"] for item in result["steps"]] == [item["title"] for item in steps]
    assert len(ids) == 1  # 只落一行伞候选——步骤不是候选

    listed = advisor.list_candidates(conn)
    assert listed["candidates"][0]["shape"] == "path"
    assert [item["title"] for item in listed["candidates"][0]["steps"]] == [
        item["title"] for item in steps
    ]
    assert "payload" not in listed["candidates"][0]  # 原始 JSON 不往外发


def test_path_steps_never_enter_the_forbidden_zone(conn):
    """否决的是**那条路**（永久禁区），路上的步骤一个都不进——这是 T34 的核心诉求。"""
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    request_id = advisor.record_request(conn, "search", "上一轮")
    steps = [step("先把异步基础打牢"), step("写一个能跑通的循环"), step("部署上线并写复盘")]
    umbrella_id = ledger.create_active(
        conn,
        "candidate",
        {
            "request_id": request_id,
            "title": "从零到部署学通 agent 开发",
            "why": "w",
            "depth_target": "够用",
            "rank": 1,
            "payload": json.dumps({"shape": "path", "steps": steps}, ensure_ascii=False),
        },
        actor="agent",
        reason="测试用",
    )
    advisor.decide_candidate(conn, umbrella_id, accept=False, reason="这条路我暂时不走")

    assert advisor._rejected_titles(conn) == ["从零到部署学通 agent 开发"]

    # 下一轮换一条路，其中一步的名字与上轮某一步一样——不该被当成禁区
    other = list_json(
        [candidate("把 agent 开发用于自己的项目", [axis])],
        shape="path",
        steps=[step("先把异步基础打牢"), step("换一个场景做小工具")],
    )
    transport = ScriptedTransport(other)
    result = advisor.find_candidates(conn, "我不知道该学什么", transport=transport)

    assert result["attempts"] == 1
    assert [item["title"] for item in result["steps"]][0] == "先把异步基础打牢"


@pytest.mark.parametrize(
    ("kwargs", "why"),
    [
        ({"steps": [step("只有一步")]}, "步骤少于 2"),
        ({"steps": [step(f"第 {index} 步") for index in range(9)]}, "步骤多于 8"),
    ],
)
def test_path_step_count_out_of_range_is_unqualified(conn, kwargs, why):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    transport = ScriptedTransport(path_json([axis], **kwargs), four([axis]))

    result = advisor.find_candidates(conn, "我不知道该学什么", transport=transport)

    assert result["attempts"] == 2, why


def test_path_with_more_than_one_candidate_is_unqualified(conn):
    """步骤被摆成几条互相竞争的方向——正是要防的那种输出。"""
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    bad = list_json(
        [candidate(f"第 {index} 步", [axis]) for index in range(3)],
        shape="path",
        steps=[step("第一步"), step("第二步")],
    )
    transport = ScriptedTransport(bad, four([axis]))

    result = advisor.find_candidates(conn, "我不知道该学什么", transport=transport)

    assert result["attempts"] == 2
    assert "只放 **1 条伞候选**" in transport.seen[1]["payload"]["messages"][-1]["content"]


@pytest.mark.parametrize("bad_step", [{"title": "", "why": "因为"}, {"title": "第一步", "why": "  "}])
def test_path_step_must_have_title_and_why(conn, bad_step):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    transport = ScriptedTransport(path_json([axis], steps=[bad_step, step("第二步")]), four([axis]))

    assert advisor.find_candidates(conn, "我不知道该学什么", transport=transport)["attempts"] == 2


def test_directions_with_steps_is_unqualified(conn):
    """两种形状不许混着写：directions 就没有步骤这回事。"""
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    mixed = list_json(
        [candidate(f"方向 {index}", [axis]) for index in range(3)],
        shape="directions",
        steps=[step("第一步"), step("第二步")],
    )
    transport = ScriptedTransport(mixed, four([axis]))

    assert advisor.find_candidates(conn, "我不知道该学什么", transport=transport)["attempts"] == 2


def test_missing_shape_is_unqualified(conn):
    """形状是模型先自报的——不自报就没法保证「一条路」按一条路处理。"""
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    no_shape = json.dumps(
        {
            "intent": "candidates",
            "candidates": [candidate(f"方向 {index}", [axis]) for index in range(3)],
            "recommended_start": "方向 0",
            "start_reason": "先做这个",
        },
        ensure_ascii=False,
    )
    transport = ScriptedTransport(no_shape, four([axis]))

    assert advisor.find_candidates(conn, "我不知道该学什么", transport=transport)["attempts"] == 2


def test_accepting_a_path_candidate_returns_steps_without_building_stages(conn):
    plan_id = ledger.create_active(conn, "plan", {"goal": "学 agent"}, actor="user")
    request_id = advisor.record_request(conn, "search", "问过", plan_id)
    steps = [step("先把异步基础打牢"), step("写一个能跑通的循环")]
    candidate_id = ledger.create_active(
        conn,
        "candidate",
        {
            "request_id": request_id,
            "title": "从零到部署学通 agent 开发",
            "why": "w",
            "depth_target": "够用",
            "rank": 1,
            "payload": json.dumps({"shape": "path", "steps": steps}, ensure_ascii=False),
        },
        actor="agent",
        reason="测试用",
    )

    result = main.post_candidate_verdict(candidate_id, VerdictIn(accept=True), conn)

    assert result["shape"] == "path"
    assert [item["title"] for item in result["steps"]] == ["先把异步基础打牢", "写一个能跑通的循环"]
    # OC-05：步骤是规划对话的底稿，采纳不建伞阶段
    assert result["node_id"] is None
    assert plan.get_stages(conn, plan_id) == []
    assert result["planning_session_id"] is not None


def test_rejecting_a_path_candidate_returns_no_steps(conn):
    """否掉的是那条路——没有「剩下的几步」可言，回执里不给。"""
    request_id = advisor.record_request(conn, "search", "问过")
    candidate_id = ledger.create_active(
        conn,
        "candidate",
        {
            "request_id": request_id,
            "title": "某条路",
            "why": "w",
            "depth_target": "够用",
            "rank": 1,
            "payload": json.dumps({"shape": "path", "steps": [step("a"), step("b")]}, ensure_ascii=False),
        },
        actor="agent",
        reason="测试用",
    )

    result = advisor.decide_candidate(conn, candidate_id, accept=False, reason="这条路不走")

    assert result["status"] == "rejected"
    assert result["steps"] == []


def test_path_expiry_follows_the_umbrella_candidate(conn):
    """决策 34 的过期按伞候选走：整条路一起失效，步骤不单独留一行状态。"""
    plan_id = ledger.create_active(conn, "plan", {"goal": "计划"}, actor="user")
    first = advisor.record_request(conn, "search", "上一轮", plan_id)
    umbrella_id = ledger.create_active(
        conn,
        "candidate",
        {
            "request_id": first,
            "title": "某条路",
            "why": "w",
            "depth_target": "够用",
            "rank": 1,
            "payload": json.dumps({"shape": "path", "steps": [step("a"), step("b")]}, ensure_ascii=False),
        },
        actor="agent",
        reason="测试用",
    )

    second = advisor.record_request(conn, "search", "这一轮", plan_id)
    advisor.propose_candidates(
        conn,
        request_id=second,
        result={
            "shape": "directions",
            "candidates": [{"title": "新方向", "kind": "concept", "why": "w", "depth_target": "够用"}],
            "steps": [],
            "recommended_start": "新方向",
            "start_reason": "先做这个",
            "source": {"name": "route_only", "networked": False},
        },
    )

    assert conn.execute(
        "SELECT status FROM candidate WHERE id = ?", (umbrella_id,)
    ).fetchone()["status"] == "expired"


def test_old_candidates_without_payload_render_as_directions(conn):
    """真库里躺着 2026-09-20 之前落的候选——它们没有 payload，按 directions 渲染。"""
    request_id = advisor.record_request(conn, "search", "老库那一轮")
    ledger.create_active(
        conn,
        "candidate",
        {"request_id": request_id, "title": "老候选", "why": "w", "depth_target": "够用", "rank": 1},
        actor="agent",
        reason="测试用",
    )

    item = advisor.list_candidates(conn)["candidates"][0]

    assert item["shape"] == "directions"
    assert item["steps"] == []


# ---------- 采纳落点要留得住（T37） ----------
#
# 走查发现的缺口：「新方向」的候选（那一轮不属于任何计划）落点是采纳那一刻现选的，此前
# 这个事实只活在当刻响应与 `plan_chat` 里——刷新一次页面，规划对话就不知道自己在哪个计划里，
# 又让人「先指定注入的计划」，直接聊甚至报「这条候选没有计划归属」。可它明明已经落进去了。

def fresh_row(conn, candidate_id: int):
    """重新从库里取一行（模拟页面刷新后不再持有当刻响应里的任何东西）。"""
    return conn.execute("SELECT * FROM candidate WHERE id = ?", (candidate_id,)).fetchone()


def test_accepting_remembers_the_landing_plan(conn):
    plan_id = ledger.create_active(conn, "plan", {"goal": "Python 后端"}, actor="user")
    candidate_id = make_candidate(conn, "从 Python 底座到后端上线")  # 「新方向」，没有归属

    advisor.decide_candidate(conn, candidate_id, accept=True, plan_id=plan_id)

    row = fresh_row(conn, candidate_id)
    assert row["landing_plan_id"] == plan_id
    # 刷新之后照样定得下来——不必再问一次「进哪个计划」
    assert advisor.landing_plan(conn, row, None) == plan_id
    # 界面拿得到它（规划对话据此知道自己在哪个计划里）
    assert advisor.list_candidates(conn)["candidates"][0]["landing_plan_id"] == plan_id


def test_landing_plan_refuses_a_different_plan(conn):
    """落点是既定事实，不是每次调用都能重新表决的。"""
    landed = ledger.create_active(conn, "plan", {"goal": "A"}, actor="user")
    other = ledger.create_active(conn, "plan", {"goal": "B"}, actor="user")
    candidate_id = make_candidate(conn, "某条路")
    advisor.decide_candidate(conn, candidate_id, accept=True, plan_id=landed)

    with pytest.raises(advisor.CandidateConflict):
        advisor.landing_plan(conn, fresh_row(conn, candidate_id), other)


def test_landing_plan_of_a_plan_closed_afterwards_is_reported(conn):
    """落进去之后计划被收尾了：报「已不是进行中」，而不是含糊的「没有计划归属」。"""
    plan_id = ledger.create_active(conn, "plan", {"goal": "会被收尾"}, actor="user")
    candidate_id = make_candidate(conn, "某条路")
    advisor.decide_candidate(conn, candidate_id, accept=True, plan_id=plan_id)
    plan.close_plan(conn, plan_id)

    with pytest.raises(advisor.CandidateConflict) as error:
        advisor.landing_plan(conn, fresh_row(conn, candidate_id), None)

    assert "已不是进行中" in str(error.value)


def test_rejecting_leaves_no_landing_plan(conn):
    candidate_id = make_candidate(conn, "不要的方向")

    advisor.decide_candidate(conn, candidate_id, accept=False, reason="和主线无关")

    assert fresh_row(conn, candidate_id)["landing_plan_id"] is None


def test_attributed_candidate_still_lands_in_its_round_plan(conn):
    """回归：有归属的候选落点与归属一致，两列说的是同一件事。"""
    plan_id = ledger.create_active(conn, "plan", {"goal": "学英语"}, actor="user")
    candidate_id = make_candidate(conn, "背单词", plan_id=plan_id)

    advisor.decide_candidate(conn, candidate_id, accept=True)

    assert fresh_row(conn, candidate_id)["landing_plan_id"] == plan_id


def test_old_accepted_candidate_without_landing_still_resolves(conn):
    """真库里 2026-09-21 之前采纳的老候选落点是 NULL——有归属的仍按归属定，不受影响。"""
    plan_id = ledger.create_active(conn, "plan", {"goal": "老计划"}, actor="user")
    candidate_id = make_candidate(conn, "老候选", plan_id=plan_id)
    ledger.set_status(conn, "candidate", candidate_id, "accepted", actor="user", reason="老库直接改的")

    row = fresh_row(conn, candidate_id)
    assert row["landing_plan_id"] is None  # 老数据就是没有
    assert advisor.landing_plan(conn, row, None) == plan_id


# 流程闭环合并：明确轮次不得串到同计划的另一条追问。
def test_explicit_clarify_request_targets_only_that_round(conn, monkeypatch):
    first = advisor.record_request(conn, "search", "线性代数")
    second = advisor.record_request(conn, "search", "高等数学")
    advisor.record_clarify(conn, first, {"question": "线代基础如何？", "missing": "基础"})
    advisor.record_clarify(conn, second, {"question": "高数基础如何？", "missing": "基础"})
    seen = {}

    def fake_find(conn_, raw_text, **kwargs):
        seen["text"] = raw_text
        raise advisor.AdvisorError("停止于假上游")

    monkeypatch.setattr(advisor, "find_candidates", fake_find)
    with pytest.raises(HTTPException):
        main.post_request(RequestIn(kind="search", raw_text="线性代数", clarify_answer="零基础", clarify_request_id=first), conn)
    assert "线代基础如何？" in seen["text"]
    assert "高数基础如何？" not in seen["text"]
    assert advisor.pending_clarify(conn, None)["request_id"] == second
    # 失败的轮次不消费回答（I-03 原子化）：first 的追问仍待答，回答没有落库
    requests = main.get_learning_requests(conn=conn)["requests"]
    assert next(row for row in requests if row["request_id"] == first)["clarify"]["answer"] is None


@pytest.mark.parametrize("target", ["missing", "other_plan", "answered"])
def test_explicit_invalid_clarify_never_consumes_latest(conn, target):
    first = advisor.record_request(conn, "search", "旧问题", plan_id=7 if target == "other_plan" else None)
    advisor.record_clarify(conn, first, {"question": "旧追问？", "missing": "基础"})
    if target == "answered":
        advisor.mark_clarify_answered(conn, first, "已答")
    latest = advisor.record_request(conn, "search", "新问题")
    advisor.record_clarify(conn, latest, {"question": "新追问？", "missing": "基础"})
    text = advisor.compose_clarify_round(conn, "原问题", "补充内容", None, 99999 if target == "missing" else first)
    assert text.splitlines() == ["原问题", "补充：补充内容"]
    assert advisor.pending_clarify(conn, None)["request_id"] == latest


def test_search_history_contract_filters_orders_and_bounds(conn):
    first = advisor.record_request(conn, "search", "新方向")
    own = advisor.record_request(conn, "search", "计划方向", plan_id=7)
    advisor.record_request(conn, "evaluate", "不是找方向", plan_id=7)
    advisor.record_clarify(conn, own, {"question": "基础如何？", "missing": "基础"})
    history = main.get_learning_requests(conn=conn)["requests"]
    assert [row["request_id"] for row in history] == [own, first]
    assert set(history[0]) == {"request_id", "raw_text", "utterance", "intent", "reply", "status", "plan_id", "thread_id", "created_at", "clarify", "shape_change", "candidate_count", "has_candidates"}
    assert history[0]["candidate_count"] == 0 and history[0]["has_candidates"] is False
    assert [row["request_id"] for row in main.get_learning_requests(plan_id=7, conn=conn)["requests"]] == [own]
    assert len(advisor.list_search_requests(conn, limit=0)) == 1


# ---------- 探索线程与意图出口（2026-09-28 双入口整改，SPEC 决策 44） ----------
#
# 这一段用**手工驱动的新链路**打整条线：resolve_thread → build_round_input → record_request
# → find_candidates(thread=…) → propose_candidates(thread_id=…) → commit_round——正是集成
# 负责人要在 main.py 里接的那条线。假上游的输出一律带 intent。


def drive_round(
    conn,
    raw_text: str,
    transport,
    *,
    thread_id: int | None = None,
    clarify_request_id: int | None = None,
    plan_id: int | None = None,
    answer: str | None = None,
    allow_shape_switch: bool = False,
    redo: bool = False,
    expected_shape: str | None = None,
):
    """按新链路跑一轮，返回 (thread, request_id, result)。

    新线程（resolve 返回 thread_id=None）在 record_request 回填后把身份补进 thread dict，
    这也是集成接线时 main.py 要做的事：落库后线程 id = 本行 id。
    集成契约（2026-09-28 复核整改）：resolve_thread 要拿到 answer（答追问轮原子占用）；
    失败路径必须 release_clarify_claim 放锁；expected_shape 由调用方查
    pending_shape_change 后塞入（形态切换的放行凭据，不是客户端布尔值）；
    propose_candidates 的 redo 来自 find 结果的透传。
    """
    thread = advisor.resolve_thread(
        conn,
        thread_id=thread_id,
        plan_id=plan_id,
        clarify_request_id=clarify_request_id,
        answer=answer,
    )
    text_in = advisor.build_round_input(conn, utterance=raw_text, answer=answer, thread=thread)
    request_id = advisor.record_request(conn, "search", text_in, plan_id, thread["thread_id"])
    thread["request_id"] = request_id
    thread["allow_shape_switch"] = allow_shape_switch
    thread["redo"] = redo
    if expected_shape is not None:
        thread["expected_shape"] = expected_shape
    if thread["thread_id"] is None:
        thread["thread_id"] = thread["thread_head"] = request_id
    try:
        result = advisor.find_candidates(
            conn, text_in, plan_id=plan_id, thread=thread, transport=transport
        )
    except Exception:
        # 与 main.py 同一契约：模型失败必须放锁，否则原样重试会被自己的残留占用挡住
        pending = thread.get("pending_clarify")
        if pending is not None and answer:
            advisor.release_clarify_claim(conn, int(pending["request_id"]), thread.get("claim_token"))
        raise
    if result["intent"] == "candidates" and result["candidates"]:
        advisor.propose_candidates(
            conn,
            request_id=request_id,
            result=result,
            thread_id=thread["thread_id"],
            redo=result["redo"],
        )
    advisor.commit_round(
        conn,
        request_id=request_id,
        thread=thread,
        answer=answer,
        clarify=result["clarify"],
        shape_change=result["shape_change"],
    )
    return thread, request_id, result


def candidate_ids_of(conn, request_id: int) -> list[int]:
    return [
        int(row["id"])
        for row in conn.execute(
            "SELECT id FROM candidate WHERE request_id = ? ORDER BY rank", (request_id,)
        ).fetchall()
    ]


def statuses_of(conn, ids: list[int]) -> dict[int, str]:
    placeholders = ",".join("?" * len(ids))
    return {
        int(row["id"]): row["status"]
        for row in conn.execute(
            f"SELECT id, status FROM candidate WHERE id IN ({placeholders})", ids
        ).fetchall()
    }


# ---------- ① 意图出口：chat / need_info 不落候选 ----------

def test_chat_greeting_lands_no_candidate_and_returns_reply(conn):
    make_provider(conn)
    add_profile(conn, "long_axis", "主线")
    transport = ScriptedTransport(chat_json("你好呀！今天想聊点什么？"))

    thread, request_id, result = drive_round(conn, "hi", transport)

    assert result["intent"] == "chat"
    assert result["reply"] == "你好呀！今天想聊点什么？"
    assert result["candidates"] == [] and result["clarify"] is None
    assert conn.execute("SELECT COUNT(*) AS n FROM candidate").fetchone()["n"] == 0
    row = conn.execute(
        "SELECT thread_id, clarify FROM learning_request WHERE id = ?", (request_id,)
    ).fetchone()
    assert row["thread_id"] == request_id  # 新线程：身份 = 自己
    assert row["clarify"] is None  # chat 轮不落追问


@pytest.mark.parametrize(
    ("extra", "fragment"),
    [
        ({"shape": "directions", "candidates": None}, "不该给候选"),
        ({"clarify": {"question": "每周几小时？", "missing": "当前状态"}}, "不要给 clarify"),
    ],
)
def test_chat_with_candidates_or_clarify_is_unqualified(conn, extra, fragment):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    extra = dict(extra)
    if extra.get("candidates") is None and "candidates" in extra:
        extra["candidates"] = [candidate(f"方向 {i}", [axis]) for i in range(3)]
    chatty = chat_json("在呢", **extra)
    transport = ScriptedTransport(chatty, chat_json("在呢"))

    result = advisor.find_candidates(conn, "hi", transport=transport)

    assert result["attempts"] == 2  # chat 轮夹带候选 / 追问都被拦下重试
    assert result["intent"] == "chat" and result["candidates"] == []
    assert fragment in transport.seen[1]["payload"]["messages"][-1]["content"]


def test_need_info_asks_then_the_answer_marks_it_answered(conn):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    question = "你现在每周能稳定投入几小时？"
    transport = ScriptedTransport(need_info_json(question, "当前状态"), four([axis]))

    thread, first_id, first = drive_round(conn, "想学点东西提升一下", transport)

    assert first["intent"] == "need_info"
    assert first["clarify"]["question"] == question
    assert first["candidates"] == []
    assert conn.execute("SELECT COUNT(*) AS n FROM candidate").fetchone()["n"] == 0  # 追问轮不落候选
    stored = json.loads(
        conn.execute("SELECT clarify FROM learning_request WHERE id = ?", (first_id,)).fetchone()["clarify"]
    )
    assert stored["answer"] is None  # 问题记下了，还没答

    # 回答该追问：同一线程续问，commit_round 之后才标已答
    _, second_id, second = drive_round(
        conn,
        "想学点东西提升一下",
        transport,
        thread_id=thread["thread_id"],
        clarify_request_id=first_id,
        answer="每周 5 小时左右",
    )

    assert second["intent"] == "candidates"
    sent = transport.seen[1]["payload"]["messages"][-1]["content"]
    # 原话打头（拼在【这次的问题】段首），追问与回答缀在后面
    assert "想学点东西提升一下\n【追问】" in sent
    assert question in sent and "【我的回答】每周 5 小时左右" in sent
    stored = json.loads(
        conn.execute("SELECT clarify FROM learning_request WHERE id = ?", (first_id,)).fetchone()["clarify"]
    )
    assert stored["answer"] == "每周 5 小时左右"
    assert (
        advisor.resolve_thread(
            conn, thread_id=thread["thread_id"], plan_id=None, clarify_request_id=None
        )["pending_clarify"]
        is None
    )
    assert {
        int(row["request_id"]) for row in conn.execute("SELECT request_id FROM candidate")
    } == {second_id}


# ---------- ③ 原子性：失败的轮不消费回答，但请求行留痕 ----------

def test_failed_round_keeps_clarify_unanswered_but_records_the_request(conn):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    transport1 = ScriptedTransport(need_info_json("你现在每周能稳定投入几小时？", "当前状态"))
    thread, first_id, _ = drive_round(conn, "想学点东西", transport1)

    too_few = list_json([candidate("只有一条", [axis])])
    transport2 = ScriptedTransport(too_few, too_few)
    with pytest.raises(advisor.AdvisorError):
        drive_round(
            conn,
            "想学点东西",
            transport2,
            thread_id=thread["thread_id"],
            clarify_request_id=first_id,
            answer="每周 5 小时",
        )

    stored = json.loads(
        conn.execute("SELECT clarify FROM learning_request WHERE id = ?", (first_id,)).fetchone()["clarify"]
    )
    assert stored["answer"] is None  # 失败不提前消费回答——可原样重试
    assert "claimed_at" not in stored  # 失败路径放锁（F4）：重试不会被自己的残留占用挡住
    retry_row = conn.execute(
        "SELECT thread_id, clarify FROM learning_request WHERE id = ?", (first_id + 1,)
    ).fetchone()
    assert retry_row["thread_id"] == thread["thread_id"]  # 本轮已留痕、归属正确
    assert retry_row["clarify"] is None  # 没落新追问
    assert conn.execute("SELECT COUNT(*) AS n FROM candidate").fetchone()["n"] == 0


# ---------- ④ 同句新问题 = 两条独立线程，不合并 ----------

def test_same_text_twice_without_thread_are_two_independent_threads(conn):
    first = advisor.record_request(conn, "search", "我不知道该学什么")
    advisor.record_clarify(conn, first, {"question": "第一段问的事？", "missing": "当前状态"})
    second = advisor.record_request(conn, "search", "我不知道该学什么")

    r1 = conn.execute("SELECT thread_id FROM learning_request WHERE id = ?", (first,)).fetchone()
    r2 = conn.execute("SELECT thread_id FROM learning_request WHERE id = ?", (second,)).fetchone()
    assert r1["thread_id"] == first and r2["thread_id"] == second and first != second

    # 互不合并：第二段的线程看不到第一段的未答追问，也不靠原话相同认亲
    t2 = advisor.resolve_thread(conn, thread_id=second, plan_id=None, clarify_request_id=None)
    assert t2["thread_head"] == second and t2["pending_clarify"] is None
    t1 = advisor.resolve_thread(conn, thread_id=first, plan_id=None, clarify_request_id=None)
    assert t1["pending_clarify"]["request_id"] == first


# ---------- ⑤ 反馈流水与否决禁区的作用域（I-04） ----------

def test_feedback_and_banned_stay_in_their_own_thread(conn):
    """F2（复核整改）：同计划两个线程，A 线程的否决/表态不出现在 B 线程的反馈流水与
    禁区；同线程续问才可见。旧口径「否决在同计划内生效」废止——计划归属只是行内展示
    信息，不再参与圈选；这是有意的行为变更，测试钉住。"""
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    plan_a = ledger.create_active(conn, "plan", {"goal": "计划 A"}, actor="user")
    plan_b = ledger.create_active(conn, "plan", {"goal": "计划 B"}, actor="user")

    # 线程 TA（计划 A）：否决一条 + 答过一条追问
    transport_ta = ScriptedTransport(four([axis]))
    ta, ta_round, _ = drive_round(conn, "A 计划想学什么", transport_ta, plan_id=plan_a)
    ta_ids = candidate_ids_of(conn, ta_round)
    advisor.decide_candidate(conn, ta_ids[0], accept=False, reason="A 线程里否掉的")
    advisor.record_clarify(conn, ta_round, {"question": "A 线程问过的事？", "missing": "当前状态"})
    advisor.mark_clarify_answered(conn, ta_round, "答了")

    # 计划 B 的新线程：TA 的否决、TA 线程的已答，一样都不过来
    transport_b = ScriptedTransport(four([axis]))
    _, _, result_b = drive_round(conn, "B 计划想学什么", transport_b, plan_id=plan_b)
    prompt_b = transport_b.seen[0]["payload"]["messages"][-1]["content"]
    assert result_b["banned_titles"] == [] and result_b["feedback_lines"] == []
    assert "A 线程里否掉的" not in prompt_b and "A 线程问过的事" not in prompt_b

    # 同计划 A 的另一条线程：TA 的表态同样不夹带（复核拍板的有意变更）
    other = list_json([candidate(f"换思路方向 {i}", [axis]) for i in range(3)])
    transport_a2 = ScriptedTransport(other)
    _, _, result_a2 = drive_round(conn, "A 计划换个思路再问", transport_a2, plan_id=plan_a)
    assert result_a2["banned_titles"] == []
    assert "A 线程里否掉的" not in transport_a2.seen[0]["payload"]["messages"][-1]["content"]

    # 同线程续问：自己的否决与已答可见（禁区 + 反馈流水 + 已答追问段）
    transport_same = ScriptedTransport(other)
    _, _, result_same = drive_round(
        conn, "A 线程接着聊", transport_same, thread_id=ta["thread_id"], plan_id=plan_a
    )
    prompt_same = transport_same.seen[0]["payload"]["messages"][-1]["content"]
    assert "Python 基础与工程实践" in result_same["banned_titles"]  # TA 否掉的那条
    assert "A 线程里否掉的" in prompt_same and "A 线程问过的事" in prompt_same

    # 无计划的新方向线程：别的线程的否决不追过来（全局禁区退场，既有口径保持）
    transport_free = ScriptedTransport(four([axis]))
    _, _, result_free = drive_round(conn, "新方向随便看看", transport_free)
    assert result_free["banned_titles"] == [] and result_free["feedback_lines"] == []


# ---------- ⑥ 形态锁（II-02） ----------

def test_shape_lock_holds_path_until_the_user_confirms_a_switch(conn):
    """F5 全流程：形态锁拦下漂移 → 模型按规矩 chat 说明冲突（提案落库）→ 用户确认那轮
    凭 pending_shape_change 的目标放行 → 新形态落库、锁随之移动、提案随之失效。"""
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    transport = ScriptedTransport(path_json([axis]))
    thread, _, first = drive_round(conn, "学 agent 开发怎么学", transport)

    assert first["shape"] == "path"
    assert advisor._thread_shape(conn, thread["thread_head"]) == "path"

    # 下一轮模型硬给 directions：判不合格，重试理由指路 chat + shape_change
    wrong = list_json([candidate(f"方向 {i}", [axis]) for i in range(3)])
    transport2 = ScriptedTransport(wrong, wrong)
    with pytest.raises(advisor.AdvisorError) as err:
        drive_round(conn, "我每周大概有 10 小时", transport2, thread_id=thread["thread_id"])
    assert "形态已定为 path" in str(err.value) and 'intent="chat"' in str(err.value)
    retry = transport2.seen[1]["payload"]["messages"][-1]["content"]
    assert "形态已定为 path" in retry and "shape_change" in retry

    # 这一次模型按规矩说明冲突（chat + shape_change）：提案落在本轮请求行上，等用户确认
    explanation = chat_json(
        "你补充的情况让它更像几个互不依赖的方向",
        shape_change={"from": "path", "to": "directions", "reason": "几个方向没有先后依赖"},
    )
    _, _, second = drive_round(
        conn, "其实我想要几个方向挑挑", ScriptedTransport(explanation), thread_id=thread["thread_id"]
    )
    assert second["intent"] == "chat"
    proposal = advisor.pending_shape_change(conn, thread["thread_head"])
    assert proposal is not None
    assert proposal["to"] == "directions" and proposal["from"] == "path"

    # 用户确认切换（allow_shape_switch + 后端查出的提案目标）：放行，锁随之移动
    transport3 = ScriptedTransport(four([axis]))
    _, _, third = drive_round(
        conn,
        "好，就按几个方向来",
        transport3,
        thread_id=thread["thread_id"],
        allow_shape_switch=True,
        expected_shape="directions",
    )

    assert third["shape"] == "directions"
    assert advisor._thread_shape(conn, thread["thread_head"]) == "directions"
    # 出了新形态的候选，提案就有了答案：再查就该是 None（陈旧凭据不回收）
    assert advisor.pending_shape_change(conn, thread["thread_head"]) is None


def test_shape_switch_without_a_pending_proposal_is_rejected(conn):
    """F5：没有待确认提案却要求放行切换——请求本身错了，而且一次模型都不该调。"""
    make_provider(conn)
    add_profile(conn, "long_axis", "主线")
    transport = ScriptedTransport(four([1]))  # 一旦被调就说明没拦住

    with pytest.raises(advisor.ThreadConflict) as err:
        drive_round(conn, "给我换个形态", transport, allow_shape_switch=True)

    assert "没有待确认的形态切换" in str(err.value)
    assert transport.seen == []  # 在调模型之前就拦下


def test_shape_switch_proposal_expires_when_the_old_shape_keeps_winning(conn):
    """F5：提案之后用户保持旧形态又出了一版候选——提案失效（「有候选出，提案就算被回答」）。"""
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    explanation = chat_json(
        "你说的更像一条固定的路",
        shape_change={"from": "directions", "to": "path", "reason": "这几步有先后依赖"},
    )
    thread, chat_id, _ = drive_round(conn, "其实我想走一条固定的路", ScriptedTransport(explanation))

    proposal = advisor.pending_shape_change(conn, thread["thread_head"])
    assert proposal is not None and proposal["to"] == "path"
    stored = conn.execute(
        "SELECT shape_change FROM learning_request WHERE id = ?", (chat_id,)
    ).fetchone()
    assert json.loads(stored["shape_change"]) == {
        "from": "directions",
        "to": "path",
        "reason": "这几步有先后依赖",
        "decision": "pending",
    }

    # 用户没点头，按旧形态（线程尚无形态，这轮首次定形 directions）继续出了候选
    _, _, result = drive_round(
        conn, "先按几个方向来吧", ScriptedTransport(four([axis])), thread_id=thread["thread_id"]
    )
    assert result["shape"] == "directions"
    assert advisor.pending_shape_change(conn, thread["thread_head"]) is None


# ---------- ⑦ 过期改同线程版本更替（C4④） ----------

def test_expiry_replaces_only_the_same_thread(conn):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    plan_id = ledger.create_active(conn, "plan", {"goal": "计划"}, actor="user")

    transport1 = ScriptedTransport(four([axis]))
    t1, r1, _ = drive_round(conn, "第一版", transport1, plan_id=plan_id)
    ids1 = candidate_ids_of(conn, r1)
    advisor.decide_candidate(conn, ids1[0], accept=True)  # 采纳一条（落进计划归属）

    transport2 = ScriptedTransport(four([axis]))
    t2, r2, _ = drive_round(conn, "同计划的另一条线程", transport2, plan_id=plan_id)
    ids2 = candidate_ids_of(conn, r2)

    # 同线程**明确重做**（redo=True）：第一版未裁定的过期，已采纳的不动
    transport3 = ScriptedTransport(four([axis]))
    _, r3, _ = drive_round(
        conn, "同线程再来一版", transport3, thread_id=t1["thread_id"], plan_id=plan_id, redo=True
    )
    ids3 = candidate_ids_of(conn, r3)

    statuses = statuses_of(conn, ids1 + ids2 + ids3)
    assert statuses[ids1[0]] == "accepted"  # 已采纳不受新版影响
    assert all(statuses[cid] == "expired" for cid in ids1[1:])
    assert all(statuses[cid] == "proposed" for cid in ids2)  # 跨线程（哪怕同计划）不自动过期
    assert all(statuses[cid] == "proposed" for cid in ids3)

    # 空手的轮（chat / need_info）不触发过期：propose 拿到空清单就该原样返回
    before = statuses_of(conn, ids2 + ids3)
    assert advisor.propose_candidates(
        conn,
        request_id=r3,
        result={"intent": "chat", "reply": "在呢", "candidates": []},
        thread_id=t1["thread_id"],
    ) == []
    assert statuses_of(conn, ids2 + ids3) == before


def test_list_candidates_by_thread_finds_that_thread_not_the_global_latest(conn):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    t1, r1, _ = drive_round(conn, "第一段", ScriptedTransport(four([axis])))
    t2, r2, _ = drive_round(conn, "第二段", ScriptedTransport(four([axis])))  # 全局最新的候选在第二段

    listed = advisor.list_candidates(conn, thread_id=t1["thread_id"])
    assert listed["request_id"] == r1 and listed["thread_id"] == t1["thread_id"]
    assert listed["candidates"][0]["title"] == "Python 基础与工程实践"

    # 一条从没出过候选的独立线程（chat 轮开的新线程）：按线程查拿不到清单，也不许捞别的线程的
    chat_transport = ScriptedTransport(chat_json("好呀"))
    chat_thread, chat_request, _ = drive_round(conn, "嗨", chat_transport)
    empty = advisor.list_candidates(conn, thread_id=chat_thread["thread_id"])
    assert empty["request_id"] is None and empty["candidates"] == []
    assert chat_thread["thread_id"] == chat_request


# ---------- ⑧ 反馈窗口：先筛后限（I-05） ----------

def test_feedback_window_keeps_answered_rounds_and_skips_empty_ones(conn):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    head = advisor.record_request(conn, "search", "线程头")
    # 线程头那轮：问过且答过（它后面跟着一串只问没答的轮——旧行为会把它挤出窗口）
    advisor.record_clarify(conn, head, {"question": "第一批答的事？", "missing": "当前状态"})
    advisor.mark_clarify_answered(conn, head, "答了")
    for index in range(1, 5):  # 连续四轮只问没答
        only_asked = advisor.record_request(conn, "search", f"只问没答 {index}", None, head)
        advisor.record_clarify(
            conn, only_asked, {"question": f"没答的问题 {index}？", "missing": "当前状态"}
        )
    verdict_round = advisor.record_request(conn, "search", "有表态的一轮", None, head)
    settle(conn, verdict_round, "方向五", "rejected", "第五轮的理由")

    transport = ScriptedTransport(four([axis]))
    _, _, result = drive_round(conn, "这一轮的问题", transport, thread_id=head)

    assert len(result["feedback_lines"]) == 2
    assert "我问过「第一批答的事？」" in result["feedback_lines"][0]  # 已答没被未答挤出窗口
    assert "方向五（已否决：第五轮的理由）" in result["feedback_lines"][1]
    prompt = prompt_of(transport)
    assert all(f"没答的问题 {index}" not in prompt for index in range(1, 5))  # 只问没答的不算反馈


# ---------- ⑩ resolve_thread 的冲突分支与旧记录兜底 ----------

def test_resolve_thread_rejects_conflicts_and_falls_back_for_legacy_rows(conn):
    plan_a = ledger.create_active(conn, "plan", {"goal": "A"}, actor="user")
    plan_b = ledger.create_active(conn, "plan", {"goal": "B"}, actor="user")

    head = advisor.record_request(conn, "search", "线程头", plan_a)

    # 线程不存在
    with pytest.raises(advisor.ThreadConflict):
        advisor.resolve_thread(conn, thread_id=999, plan_id=None, clarify_request_id=None)
    # 换计划续问（两个方向都拦）
    with pytest.raises(advisor.ThreadConflict) as err:
        advisor.resolve_thread(conn, thread_id=head, plan_id=plan_b, clarify_request_id=None)
    assert f"这段探索属于计划 #{plan_a}" in str(err.value)
    assert "回到原线程" in str(err.value)
    free_head = advisor.record_request(conn, "search", "新方向线程")
    with pytest.raises(advisor.ThreadConflict) as err:
        advisor.resolve_thread(conn, thread_id=free_head, plan_id=plan_a, clarify_request_id=None)
    assert "新方向" in str(err.value)

    # 追问不属于该线程
    stranger = advisor.record_request(conn, "search", "别的线程的一轮")
    with pytest.raises(advisor.ThreadConflict):
        advisor.resolve_thread(conn, thread_id=head, plan_id=plan_a, clarify_request_id=stranger)

    # 未答追问：点名哪条拿哪条；已答：不算错，pending 记 None（回答按「补充」拼）
    advisor.record_clarify(conn, head, {"question": "线程头问的？", "missing": "当前状态"})
    pending = advisor.resolve_thread(conn, thread_id=head, plan_id=plan_a, clarify_request_id=head)
    assert pending["pending_clarify"]["request_id"] == head
    advisor.mark_clarify_answered(conn, head, "答了")
    answered = advisor.resolve_thread(conn, thread_id=head, plan_id=plan_a, clarify_request_id=head)
    assert answered["pending_clarify"] is None

    # 同线程续问（thread_id = 头）也能取到线程内最近未答
    follower = advisor.record_request(conn, "search", "续问一轮", plan_a, head)
    advisor.record_clarify(conn, follower, {"question": "续问问的？", "missing": "生活记录"})
    by_head = advisor.resolve_thread(conn, thread_id=head, plan_id=plan_a, clarify_request_id=None)
    assert by_head["pending_clarify"]["request_id"] == follower

    # 旧客户端兜底：只带 clarify_request_id → 线程从那一轮反推（新契约：计划归属也要对上）
    legacy = advisor.resolve_thread(conn, thread_id=None, plan_id=plan_a, clarify_request_id=follower)
    assert legacy["thread_head"] == head and legacy["pending_clarify"]["request_id"] == follower

    # 兼容路径换计划同样要拒（F3：反推出的头行计划归属也要与本轮核对）
    with pytest.raises(advisor.ThreadConflict) as err:
        advisor.resolve_thread(conn, thread_id=None, plan_id=plan_b, clarify_request_id=follower)
    assert f"这段探索属于计划 #{plan_a}" in str(err.value)

    # 旧行（thread_id NULL，这里手工把列清空模拟）：独立成段，线程就是它自己
    conn.execute("UPDATE learning_request SET thread_id = NULL WHERE id = ?", (follower,))
    conn.commit()
    legacy_old = advisor.resolve_thread(conn, thread_id=None, plan_id=plan_a, clarify_request_id=follower)
    assert legacy_old["thread_head"] == follower
    assert legacy_old["pending_clarify"]["request_id"] == follower

    # 全新线程：三个键都是 None，等 record_request 回填
    fresh = advisor.resolve_thread(conn, thread_id=None, plan_id=None, clarify_request_id=None)
    assert fresh == {"thread_id": None, "thread_head": None, "pending_clarify": None}


# ---------- build_round_input 的「补充」分支（无待答追问时不吞话） ----------

def test_build_round_input_appends_supplement_without_touching_state(conn):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    transport = ScriptedTransport(need_info_json("你现在每周能稳定投入几小时？", "当前状态"))
    thread, first_id, _ = drive_round(conn, "想学点东西", transport)

    # 无待答追问的场景：回答按「补充」缀在原话后面，且不写任何状态
    thread["pending_clarify"] = None
    composed = advisor.build_round_input(
        conn, utterance="想学点东西", answer="顺便说一句我周末有空", thread=thread
    )
    assert composed == "想学点东西\n补充：顺便说一句我周末有空"
    stored = json.loads(
        conn.execute("SELECT clarify FROM learning_request WHERE id = ?", (first_id,)).fetchone()["clarify"]
    )
    assert stored["answer"] is None  # build 不落状态——「已答」只能由 commit_round 写


# ---------- 形态切换请求（II-02）：shape_change 用 "from" 键解析、按 alias 回传 ----------

def test_shape_change_parses_the_from_key_and_returns_it_back(conn):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    explanation = json.dumps(
        {
            "intent": "chat",
            "reply": "你说的这个新方向更像一条固定的路，我想把它换成一条路来推。",
            "shape_change": {"from": "directions", "to": "path", "reason": "这几步有先后依赖"},
        },
        ensure_ascii=False,
    )
    transport = ScriptedTransport(explanation)

    result = advisor.find_candidates(conn, "其实我想走一条固定的路", transport=transport)

    assert result["intent"] == "chat" and result["candidates"] == []
    # 回传给界面时还原成模型用的 "from" 键
    assert result["shape_change"] == {
        "from": "directions",
        "to": "path",
        "reason": "这几步有先后依赖",
    }
    # 非法取值照常拦下：from 不是两种形态之一 → 不合格重试
    bad = json.dumps(
        {"intent": "chat", "reply": "换", "shape_change": {"from": "别的", "to": "path", "reason": "想换"}},
        ensure_ascii=False,
    )
    transport2 = ScriptedTransport(bad, chat_json("那我们聊聊"))
    assert advisor.find_candidates(conn, "换个形态", transport=transport2)["attempts"] == 2



# ---------- ⑬ 路由编排（main.post_request）：线程接线、意图分流与失败回执 ----------

def fake_find_result(**overrides) -> dict:
    """一份「已经过了 advisor 验收」的 find_candidates 返回（路由只负责编排与透传）。"""
    base = {
        "intent": "chat",
        "reply": "你好呀！想聊点什么方向，还是随便说说话？",
        "plan_id": None,
        "shape": None,
        "steps": [],
        "candidates": [],
        "recommended_start": None,
        "start_reason": None,
        "clarify": None,
        "shape_change": None,
        "source": {"name": "route_only", "networked": False},
        "profile_basis": {"total": 1, "missing_categories": []},
        "banned_titles": [],
        "feedback_lines": [],
        "attempts": 1,
        "calls": 1,
    }
    base.update(overrides)
    return base


def test_route_search_returns_thread_identity_and_intent(conn, monkeypatch):
    """路由把线程身份与意图带回：新探索带回头请求 id；chat 轮不落候选、不记追问。"""
    add_profile(conn, "long_axis", "主线")
    seen: dict = {}

    def fake_find(conn_, raw_text, **kwargs):
        seen["raw_text"] = raw_text
        assert kwargs["thread"]["request_id"] > 0
        return fake_find_result()

    monkeypatch.setattr(advisor, "find_candidates", fake_find)
    response = main.post_request(RequestIn(kind="search", raw_text="hi"), conn)

    assert response["thread_id"] == response["request_id"]  # 新线程身份 = 本行 id
    assert response["intent"] == "chat" and response["reply"]
    assert response["candidate_ids"] == []
    assert conn.execute("SELECT COUNT(*) AS n FROM candidate").fetchone()["n"] == 0


def test_route_persists_reply_and_replays_only_same_thread_success(conn, monkeypatch):
    add_profile(conn, "long_axis", "主线")
    monkeypatch.setattr(advisor, "find_candidates", lambda *a, **k: fake_find_result(reply="前一轮助手回复"))
    first = main.post_request(RequestIn(kind="search", raw_text="第一问"), conn)
    other = main.post_request(RequestIn(kind="search", raw_text="另一段"), conn)
    history = main.get_learning_requests(conn=conn)["requests"]
    assert history[1]["utterance"] == "第一问"
    assert history[1]["intent"] == "chat"
    assert history[1]["reply"] == "前一轮助手回复"
    assert history[1]["status"] == "success"
    assert other["thread_id"] != first["thread_id"]
    conversation = advisor._thread_conversation(conn, first["thread_id"], None)
    assert conversation == ["我：第一问", "助手（chat）：前一轮助手回复"]


def test_keep_shape_route_exact_target_and_refresh_decision(conn):
    thread = advisor.record_request(conn, "search", "一段探索")
    proposal_id = advisor.record_request(conn, "search", "解释形态", thread_id=thread)
    advisor.commit_round(
        conn, request_id=proposal_id, thread={"thread_head": thread}, answer=None,
        clarify=None, intent="chat", reply="建议切换",
        shape_change={"from": "directions", "to": "path", "reason": "有先后关系"},
    )
    with pytest.raises(HTTPException) as wrong:
        main.post_keep_shape(main.KeepShapeIn(thread_id=thread, shape_change_request_id=thread), conn)
    assert wrong.value.status_code == 409
    result = main.post_keep_shape(main.KeepShapeIn(thread_id=thread, shape_change_request_id=proposal_id), conn)
    assert result["decision"] == "keep" and result["shape"] == "directions"
    assert advisor.list_search_requests(conn)[0]["shape_change"]["decision"] == "keep"
    with pytest.raises(HTTPException) as repeated:
        main.post_keep_shape(main.KeepShapeIn(thread_id=thread, shape_change_request_id=proposal_id), conn)
    assert repeated.value.status_code == 409


def test_route_thread_plan_mismatch_is_409(conn):
    """线程归属锁在头请求上：换计划续问要 409，不悄悄换线（I-02）。"""
    add_profile(conn, "long_axis", "主线")
    plan_a = ledger.create_active(conn, "plan", {"goal": "学 A"}, actor="user")
    plan_b = ledger.create_active(conn, "plan", {"goal": "学 B"}, actor="user")
    head = advisor.record_request(conn, "search", "关于 A 的问题", plan_a)

    with pytest.raises(HTTPException) as excinfo:
        main.post_request(
            RequestIn(kind="search", raw_text="接着聊", plan_id=plan_b, thread_id=head), conn
        )
    assert excinfo.value.status_code == 409


def test_route_failure_reports_input_saved_and_retry(conn, monkeypatch):
    """模型失败的回执契约（V-02）：400、输入已记录、追问未消费、可原样重试。"""
    add_profile(conn, "long_axis", "主线")
    head = advisor.record_request(conn, "search", "学什么好")
    advisor.record_clarify(conn, head, {"question": "每周几小时？", "missing": "生活习惯"})

    monkeypatch.setattr(
        advisor, "find_candidates", lambda *a, **k: (_ for _ in ()).throw(advisor.AdvisorError("两次不合格"))
    )
    with pytest.raises(HTTPException) as excinfo:
        main.post_request(
            RequestIn(kind="search", raw_text="学什么好", clarify_answer="每周 5 小时",
                      clarify_request_id=head, thread_id=head),
            conn,
        )
    assert excinfo.value.status_code == 400
    assert "已记录" in excinfo.value.detail and "重试" in excinfo.value.detail
    row = conn.execute("SELECT clarify FROM learning_request WHERE id = ?", (head,)).fetchone()
    assert advisor._stored_clarify(row["clarify"])["answer"] is None  # 回答没被消费


def test_route_success_closes_the_clarify_after_candidates(conn, monkeypatch):
    """成功路径的收尾顺序：候选落库之后才把追问置为已答（I-03 原子化的路由侧证明）。"""
    add_profile(conn, "long_axis", "主线")
    head = advisor.record_request(conn, "search", "学什么好")
    advisor.record_clarify(conn, head, {"question": "每周几小时？", "missing": "生活习惯"})

    monkeypatch.setattr(
        advisor, "find_candidates",
        lambda *a, **k: fake_find_result(intent="chat", reply="明白了，那我按这个来。"),
    )
    response = main.post_request(
        RequestIn(kind="search", raw_text="学什么好", clarify_answer="每周 5 小时",
                  clarify_request_id=head, thread_id=head),
        conn,
    )
    assert response["thread_id"] == head
    row = conn.execute("SELECT clarify FROM learning_request WHERE id = ?", (head,)).fetchone()
    assert advisor._stored_clarify(row["clarify"])["answer"] == "每周 5 小时"


def test_same_batch_duplicate_titles_are_unqualified(conn):
    """同批重复（II-04）：一份清单里两条同一件事＝拿重复凑条数，判不合格带原因重试。"""
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    first = list_json(
        [candidate("学 FastAPI", [axis]), candidate("学 SQL", [axis]), candidate("学 Linux", [axis])]
    )
    dup = list_json(
        [candidate("学 FastAPI", [axis]), candidate("学  FastAPI", [axis]), candidate("学 SQL", [axis])]
    )
    transport = ScriptedTransport(dup, first)

    result = advisor.find_candidates(conn, "学什么", transport=transport)

    assert result["attempts"] == 2  # 第一次同批重复被判不合格、带原因重说后通过


# ---------- 复核整改回归（F1–F6，2026-09-28） ----------
#
# 上一轮 E-61 落了线程/意图/形态锁，独立复核发现六处行为缺口；这一段把它们逐条钉住。

# ---------- F1：续聊保留原题（不信任客户端） ----------

def test_continuation_input_keeps_the_head_utterance_without_the_client(conn):
    """F1：续聊的原题由后端从线程头取——客户端只发新话时原题不丢；重发原题时不重复拼。"""
    make_provider(conn)
    add_profile(conn, "long_axis", "主线")
    original = "我想在入冬前养成跑步的习惯"
    transport1 = ScriptedTransport(need_info_json("你现在每周能稳定投入几次？", "生活习惯"))
    thread, first_id, _ = drive_round(conn, original, transport1)

    # 客户端续聊只发新话（没带原题）：基底仍是线程头原题，新话以【这轮要说】缀在后面
    transport2 = ScriptedTransport(chat_json("好，记下了"))
    _, second_id, _ = drive_round(
        conn, "另外我还想练核心力量", transport2, thread_id=thread["thread_id"]
    )
    stored = conn.execute(
        "SELECT raw_text FROM learning_request WHERE id = ?", (second_id,)
    ).fetchone()
    assert stored["raw_text"] == f"{original}\n【这轮要说】另外我还想练核心力量"
    prompt = transport2.seen[0]["payload"]["messages"][-1]["content"]
    assert original in prompt and "另外我还想练核心力量" in prompt

    # 客户端把原题原样重发一遍（无新话无回答）：不重复拼
    transport3 = ScriptedTransport(chat_json("嗯嗯"))
    _, third_id, _ = drive_round(conn, original, transport3, thread_id=thread["thread_id"])
    stored = conn.execute(
        "SELECT raw_text FROM learning_request WHERE id = ?", (third_id,)
    ).fetchone()
    assert stored["raw_text"] == original

    # 答追问轮：基底仍是原题，追问与回答缀在后面（原话永远在场）
    transport4 = ScriptedTransport(four([1]))
    _, fourth_id, fourth = drive_round(
        conn,
        original,
        transport4,
        thread_id=thread["thread_id"],
        clarify_request_id=first_id,
        answer="每周 3 次",
    )
    assert fourth["intent"] == "candidates"
    stored = conn.execute(
        "SELECT raw_text FROM learning_request WHERE id = ?", (fourth_id,)
    ).fetchone()
    assert stored["raw_text"] == (
        f"{original}\n【追问】你现在每周能稳定投入几次？\n【我的回答】每周 3 次"
    )


# ---------- F3：线程头严核 ----------

def test_thread_head_must_be_a_search_head_row(conn):
    """F3：后续轮 id / 四问 id 冒充线程号、兼容路径换计划——一律 ThreadConflict。"""
    plan_a = ledger.create_active(conn, "plan", {"goal": "A"}, actor="user")
    plan_b = ledger.create_active(conn, "plan", {"goal": "B"}, actor="user")
    head = advisor.record_request(conn, "search", "线程头", plan_a)
    follower = advisor.record_request(conn, "search", "后续一轮", plan_a, head)
    evaluate_id = advisor.record_request(conn, "evaluate", "这是一次四问")

    # 后续轮的 id 不是线程头（它的 thread_id 指向头，不等于自己）
    with pytest.raises(advisor.ThreadConflict) as err:
        advisor.resolve_thread(conn, thread_id=follower, plan_id=plan_a, clarify_request_id=None)
    assert "不是一段探索的开头" in str(err.value)
    # 四问行的 id 更不能当线程号
    with pytest.raises(advisor.ThreadConflict):
        advisor.resolve_thread(conn, thread_id=evaluate_id, plan_id=None, clarify_request_id=None)
    # 兼容路径：R 是四问行 → 拒；反推出的头行换计划 → 拒（F3 补的校验）
    with pytest.raises(advisor.ThreadConflict):
        advisor.resolve_thread(conn, thread_id=None, plan_id=None, clarify_request_id=evaluate_id)
    with pytest.raises(advisor.ThreadConflict) as err:
        advisor.resolve_thread(conn, thread_id=None, plan_id=plan_b, clarify_request_id=follower)
    assert f"这段探索属于计划 #{plan_a}" in str(err.value)
    # 正路仍通：头号 + 同计划
    ok = advisor.resolve_thread(conn, thread_id=head, plan_id=plan_a, clarify_request_id=None)
    assert ok["thread_head"] == head
    legacy_ok = advisor.resolve_thread(
        conn, thread_id=None, plan_id=plan_a, clarify_request_id=follower
    )
    assert legacy_ok["thread_head"] == head


# ---------- F4：并发回答的原子占用 ----------

def test_concurrent_answers_claim_the_clarify_atomically(conn):
    """F4：同一追问被两路并发回答——后到的被挡下，回答不许被重复消费；放锁后可重试。"""
    head = advisor.record_request(conn, "search", "学什么好")
    advisor.record_clarify(conn, head, {"question": "每周几小时？", "missing": "生活习惯"})

    first = advisor.resolve_thread(
        conn, thread_id=head, plan_id=None, clarify_request_id=head, answer="每周 5 小时"
    )
    assert first["pending_clarify"]["request_id"] == head
    stored = json.loads(
        conn.execute("SELECT clarify FROM learning_request WHERE id = ?", (head,)).fetchone()["clarify"]
    )
    assert stored["claimed_at"]  # 占用已记下

    with pytest.raises(advisor.ThreadConflict) as err:
        advisor.resolve_thread(
            conn, thread_id=head, plan_id=None, clarify_request_id=head, answer="每周 5 小时"
        )
    assert "另一轮回答接管" in str(err.value)

    # 失败路径放锁之后，原样重试能接回同一条追问
    advisor.release_clarify_claim(conn, head, first["claim_token"])
    again = advisor.resolve_thread(
        conn, thread_id=head, plan_id=None, clarify_request_id=head, answer="每周 5 小时"
    )
    assert again["pending_clarify"]["request_id"] == head


def test_stale_claim_from_a_crashed_round_can_be_taken_over(conn):
    """F4：占用带时间戳——超过保鲜期的残留占用（进程崩溃留下的）可重新接管，不会永久锁死。"""
    head = advisor.record_request(conn, "search", "学什么好")
    advisor.record_clarify(conn, head, {"question": "每周几小时？", "missing": "生活习惯"})
    stale = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    raw = json.dumps(
        {"question": "每周几小时？", "missing": "生活习惯", "answer": None, "claimed_at": stale},
        ensure_ascii=False,
    )
    conn.execute("UPDATE learning_request SET clarify = ? WHERE id = ?", (raw, head))
    conn.commit()

    thread = advisor.resolve_thread(
        conn, thread_id=head, plan_id=None, clarify_request_id=head, answer="现在 5 小时"
    )
    assert thread["pending_clarify"]["request_id"] == head
    stored = json.loads(
        conn.execute("SELECT clarify FROM learning_request WHERE id = ?", (head,)).fetchone()["clarify"]
    )
    assert stored["claimed_at"] != stale  # 旧占用被刷新成现在


def test_success_round_replaces_the_claim_with_the_answer(conn):
    """F4：成功收尾把占用换成「已答」——JSON 里既有 answer 又没有 claimed_at。"""
    make_provider(conn)
    add_profile(conn, "long_axis", "主线")
    head = advisor.record_request(conn, "search", "学什么好")
    advisor.record_clarify(conn, head, {"question": "每周几小时？", "missing": "生活习惯"})

    drive_round(
        conn,
        "学什么好",
        ScriptedTransport(four([1])),
        thread_id=head,
        clarify_request_id=head,
        answer="每周 5 小时",
    )

    stored = json.loads(
        conn.execute("SELECT clarify FROM learning_request WHERE id = ?", (head,)).fetchone()["clarify"]
    )
    assert stored["answer"] == "每周 5 小时"
    assert "claimed_at" not in stored  # 「已答」取代「作答中」
    assert advisor.resolve_thread(
        conn, thread_id=head, plan_id=None, clarify_request_id=None
    )["pending_clarify"] is None


# ---------- F6：redo 才更替旧候选 ----------

def test_non_redo_candidate_round_keeps_the_previous_version_decidable(conn):
    """F6：普通候选轮不动旧未裁定候选——两版并存，旧的仍可裁定；已采纳的永不过期。"""
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    t1, r1, _ = drive_round(conn, "第一版", ScriptedTransport(four([axis])))
    ids1 = candidate_ids_of(conn, r1)
    _, r2, _ = drive_round(
        conn, "续聊里模型又给了清单", ScriptedTransport(four([axis])), thread_id=t1["thread_id"]
    )
    ids2 = candidate_ids_of(conn, r2)

    statuses = statuses_of(conn, ids1 + ids2)
    assert all(status == "proposed" for status in statuses.values())  # 两版并存，谁也没动谁
    # 旧版仍可裁定（「新方向」的候选采纳要指明落点计划，SPEC 决策 33 ②）
    landing = ledger.create_active(conn, "plan", {"goal": "承接采纳"}, actor="user")
    advisor.decide_candidate(conn, ids1[0], accept=True, plan_id=landing)
    statuses = statuses_of(conn, ids1 + ids2)
    assert statuses[ids1[0]] == "accepted"

    # 明确重做（redo=True）才更替：未裁定的全过期，已采纳的永不过期
    _, r3, _ = drive_round(
        conn, "明确重做一版", ScriptedTransport(four([axis])),
        thread_id=t1["thread_id"], redo=True,
    )
    ids3 = candidate_ids_of(conn, r3)
    statuses = statuses_of(conn, ids1 + ids2 + ids3)
    assert statuses[ids1[0]] == "accepted"
    assert all(statuses[cid] == "expired" for cid in ids1[1:] + ids2)
    assert all(statuses[cid] == "proposed" for cid in ids3)


def test_explicit_redo_replaces_the_same_thread_only(conn):
    """F6：redo=True 只更替本线程旧候选；跨线程（哪怕同计划）依旧不动。"""
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")
    plan_id = ledger.create_active(conn, "plan", {"goal": "计划"}, actor="user")
    t1, r1, _ = drive_round(conn, "第一版", ScriptedTransport(four([axis])), plan_id=plan_id)
    ids1 = candidate_ids_of(conn, r1)
    t2, r2, _ = drive_round(conn, "另一条线程", ScriptedTransport(four([axis])), plan_id=plan_id)
    ids2 = candidate_ids_of(conn, r2)

    _, r3, _ = drive_round(
        conn, "重做第一版", ScriptedTransport(four([axis])),
        thread_id=t1["thread_id"], plan_id=plan_id, redo=True,
    )
    ids3 = candidate_ids_of(conn, r3)
    statuses = statuses_of(conn, ids1 + ids2 + ids3)
    assert all(statuses[cid] == "expired" for cid in ids1)
    assert all(statuses[cid] == "proposed" for cid in ids2)  # 跨线程不自动过期
    assert all(statuses[cid] == "proposed" for cid in ids3)


# ---------- 模型错误自报的完整失败链路 ----------

def test_model_self_report_failures_get_one_reason_carrying_retry_then_error(conn):
    """模型错误自报走全链路（不许只测理想输出）：判不合格 → 带原因重试 → 第二次仍错就
    报错、不落任何候选。三个场景：该给 chat 的轮硬给 candidates、path 线程硬给
    directions、确认切形态时给错目标。"""
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线")

    # ① 寒暄轮硬给 candidates（自报 chat 却塞了清单）
    chatty = chat_json("在呢", candidates=[candidate(f"方向 {i}", [axis]) for i in range(3)])
    transport1 = ScriptedTransport(chatty, chatty)
    with pytest.raises(advisor.AdvisorError) as err1:
        advisor.find_candidates(conn, "hi", transport=transport1)
    assert "不该给候选" in transport1.seen[1]["payload"]["messages"][-1]["content"]
    assert "连着 2 次" in str(err1.value)
    assert conn.execute("SELECT COUNT(*) AS n FROM candidate").fetchone()["n"] == 0

    # ② path 线程硬给 directions（形态锁拦下，重试理由指路 chat + shape_change）
    thread, _, _ = drive_round(conn, "学 agent 开发怎么学", ScriptedTransport(path_json([axis])))
    directions = list_json([candidate(f"方向 {i}", [axis]) for i in range(3)])
    transport2 = ScriptedTransport(directions, directions)
    with pytest.raises(advisor.AdvisorError):
        drive_round(conn, "接着聊", transport2, thread_id=thread["thread_id"])
    assert "形态已定为 path" in transport2.seen[1]["payload"]["messages"][-1]["content"]
    before = conn.execute("SELECT COUNT(*) AS n FROM candidate").fetchone()["n"]

    # ③ 确认切形态却给错目标（要 directions，模型咬死 path）——两次都错就报错
    explanation = chat_json(
        "你想挑着学", shape_change={"from": "path", "to": "directions", "reason": "没有先后依赖"}
    )
    drive_round(
        conn, "给我几个方向挑挑", ScriptedTransport(explanation), thread_id=thread["thread_id"]
    )
    stubborn = path_json([axis])
    transport3 = ScriptedTransport(stubborn, stubborn)
    with pytest.raises(advisor.AdvisorError) as err3:
        drive_round(
            conn, "确认切换", transport3, thread_id=thread["thread_id"],
            allow_shape_switch=True, expected_shape="directions",
        )
    assert "切换到 directions" in transport3.seen[1]["payload"]["messages"][-1]["content"]
    assert "没有落任何候选" in str(err3.value)
    # 三轮失败都没落新候选：库里还是 path 那一轮的 1 条伞候选
    assert conn.execute("SELECT COUNT(*) AS n FROM candidate").fetchone()["n"] == before
