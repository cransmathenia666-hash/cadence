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
    """一份「找」的输出。`shape` 必填（T34），默认是「几条互相竞争的方向」。"""
    payload: dict = {
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
    """追问不能替代清单：带追问的那一轮照样给满候选，且它**不落库**。"""
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
            "candidates": [candidate(f"方向 {index}", [axis]) for index in range(3)],
            "recommended_start": "方向 0",
            "start_reason": "先做这个",
        },
        ensure_ascii=False,
    )
    transport = ScriptedTransport(no_shape, four([axis]))

    assert advisor.find_candidates(conn, "我不知道该学什么", transport=transport)["attempts"] == 2


def test_accepting_a_path_candidate_builds_the_umbrella_stage_and_returns_steps(conn):
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
    node = plan.get_node(conn, result["node_id"])
    assert node["level"] == "stage" and node["title"] == "从零到部署学通 agent 开发"


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
