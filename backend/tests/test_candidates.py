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

import pytest

from app import advisor, db, ledger, llm, main, plan
from app.main import VerdictIn


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


def list_json(candidates: list[dict], *, start: str | None = None, reason="先把基础补齐") -> str:
    return json.dumps(
        {
            "candidates": candidates,
            "recommended_start": start or candidates[0]["title"],
            "start_reason": reason,
        },
        ensure_ascii=False,
    )


def four(ids: list[int], **overrides) -> str:
    """一份合格的四条候选——最常用的底稿。"""
    return list_json(
        [
            candidate("Python 基础与工程实践", ids, depth="熟练", kind="course", **overrides),
            candidate("HTTP 与后端接口", ids, **overrides),
            candidate("SQLite 与数据持久化", ids, **overrides),
            candidate("部署一个能访问的小项目", ids, depth="够用", kind="project", **overrides),
        ]
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

    # 「新方向」的候选没有计划归属，采纳时要显式指明落点（SPEC 决策 33 ②）
    assert advisor.decide_candidate(conn, candidate_id, accept=True, plan_id=plan_id)["status"] == "accepted"

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


# ---------- 采纳落点（2026-09-17 起：落候选归属计划；SPEC 决策 33 ②） ----------

def test_accept_lands_in_the_attributed_plan(conn):
    attributed = ledger.create_active(conn, "plan", {"goal": "被指定的计划"}, actor="user")
    ledger.create_active(conn, "plan", {"goal": "更新的计划"}, actor="user")  # 更新，但不该落它
    candidate_id = make_candidate(conn, "学 HTTP", plan_id=attributed)

    result = main.post_candidate_verdict(candidate_id, VerdictIn(accept=True), conn)

    assert result["status"] == "accepted"
    assert result["plan_id"] == attributed
    node = plan.get_node(conn, result["node_id"])
    assert node["level"] == "stage" and node["title"] == "学 HTTP"
    assert int(node["plan_id"]) == attributed
    # 节点自己走台账留痕：一条 create 事件，紧随候选的 accepted 之后
    events = ledger.history(conn, "plan_node", result["node_id"])
    assert [event["change_type"] for event in events] == ["create"]


def test_accept_without_attribution_needs_an_explicit_plan(conn):
    plan_id = ledger.create_active(conn, "plan", {"goal": "某计划"}, actor="user")
    candidate_id = make_candidate(conn, "学 HTTP")  # 「新方向」：没有计划归属

    with pytest.raises(advisor.CandidateConflict):
        advisor.decide_candidate(conn, candidate_id, accept=True)  # 不指明就不许采纳

    assert conn.execute(
        "SELECT status FROM candidate WHERE id = ?", (candidate_id,)
    ).fetchone()["status"] == "proposed"  # 没动它，可重试

    assert advisor.decide_candidate(
        conn, candidate_id, accept=True, plan_id=plan_id
    )["plan_id"] == plan_id


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


def test_accept_without_any_active_plan_conflicts_and_keeps_candidate_proposed(conn):
    candidate_id = make_candidate(conn, "学 HTTP")

    with pytest.raises(advisor.CandidateConflict):
        advisor.decide_candidate(conn, candidate_id, accept=True)

    row = conn.execute("SELECT status FROM candidate WHERE id = ?", (candidate_id,)).fetchone()
    assert row["status"] == "proposed"  # 没动它，建完计划可重试


def test_accept_with_same_title_open_stage_conflicts_and_keeps_candidate_proposed(conn):
    plan_id = ledger.create_active(conn, "plan", {"goal": "计划"}, actor="user")
    plan.add_node(conn, plan_id, "stage", "学 HTTP")
    candidate_id = make_candidate(conn, "学 HTTP")

    with pytest.raises(advisor.CandidateConflict):
        advisor.decide_candidate(conn, candidate_id, accept=True)

    row = conn.execute("SELECT status FROM candidate WHERE id = ?", (candidate_id,)).fetchone()
    assert row["status"] == "proposed"
    stages = plan.get_stages(conn, plan_id)
    assert len(stages) == 1  # 没有第二条同名阶段被建出来


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
        "candidates": [],
        "recommended": None,
    }
