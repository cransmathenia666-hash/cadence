"""提案裁定的单测（T14 的后端部分）。

覆盖四组东西：
① 列表：只取 `pending`、payload 解成对象、按 kind 过滤；
② 裁定的两种归宿（批准 / 驳回）与留痕（业务终态 + `decided_at` + 台账理由）；
③ 三类失败：不存在 404、已裁定 409、规则拒绝 400（驳回没理由、批准重排没选方向 / 选错方向）；
④ 批准到底动什么：只有「后面没有更多阶段」的推进提案会收尾计划，其余都只记账。

假上游、假数据一以贯之：这里全部用手写的提案行与真实生产者各打一遍。
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from app import db, ledger, plan, proposals
from app.main import ProposalDecideIn, post_proposal_decide


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "test.db"
    db.init(path)
    connection = db.connect(path)
    yield connection
    connection.close()


def add_proposal(conn, kind: str, payload: dict, reason: str = "测试用") -> int:
    return ledger.create_active(
        conn,
        "proposal",
        {"kind": kind, "payload": json.dumps(payload, ensure_ascii=False), "reason": reason},
        actor="agent",
    )


def decide(conn, proposal_id: int, **kwargs):
    """领域层裁定。错误路径测这里；状态码的翻译另有 `test_route_*` 一条。"""
    return proposals.decide(conn, proposal_id, **kwargs)


def decide_via_route(conn, proposal_id: int, **kwargs):
    return post_proposal_decide(proposal_id, ProposalDecideIn(**kwargs), conn)


def status_of(conn, proposal_id: int) -> str:
    return conn.execute("SELECT status FROM proposal WHERE id = ?", (proposal_id,)).fetchone()["status"]


# ---------- 列表 ----------

def test_list_returns_pending_with_parsed_payload(conn):
    first = add_proposal(conn, "material_judgment", {"source_text": "要不要学 python"})
    second = add_proposal(conn, "profile_change", {"category": "current_state", "content": "x"})
    decided = add_proposal(conn, "material_judgment", {"source_text": "另一份资料"})
    proposals.decide(conn, decided, approved=False, reason="不适用")  # 裁定过的就不再出现

    listed = proposals.list_pending(conn)

    assert [item["id"] for item in listed["proposals"]] == [first, second]
    assert listed["proposals"][0]["payload"] == {"source_text": "要不要学 python"}
    assert listed["proposals"][0]["reason"] == "测试用"


def test_list_can_filter_by_kind(conn):
    add_proposal(conn, "material_judgment", {})
    kept = add_proposal(conn, "plan_replan", {"options": []})

    listed = proposals.list_pending(conn, "plan_replan")

    assert [item["id"] for item in listed["proposals"]] == [kept]


def test_broken_payload_does_not_break_the_list(conn):
    conn.execute(
        """INSERT INTO proposal (kind, payload, status, valid_from, created_at)
           VALUES ('profile_change', '{不是 JSON', 'pending', '2026-09-17T00:00:00+08:00',
                   '2026-09-17T00:00:00+08:00')"""
    )
    conn.commit()

    listed = proposals.list_pending(conn)

    assert listed["proposals"][0]["payload"] == {}


# ---------- 裁定与留痕 ----------

def test_approve_a_judgment_records_only(conn):
    proposal_id = add_proposal(conn, "material_judgment", {"source_text": "要不要学 python"})

    result = decide(conn, proposal_id, approved=True)

    assert result["status"] == "accepted" and result["effect"] == "recorded_only"
    row = conn.execute("SELECT * FROM proposal WHERE id = ?", (proposal_id,)).fetchone()
    assert row["status"] == "accepted" and row["decided_at"] is not None
    events = ledger.history(conn, "proposal", proposal_id)
    assert [event["change_type"] for event in events] == ["create", "status_change"]
    assert events[-1]["reason"] == "批准：认可这次四问判断"


def test_reject_needs_reason_and_keeps_it_in_the_ledger(conn):
    proposal_id = add_proposal(conn, "material_judgment", {})

    with pytest.raises(proposals.ProposalError):
        decide(conn, proposal_id, approved=False, reason="   ")

    result = decide(conn, proposal_id, approved=False, reason="这个判断的依据太单薄")

    assert result["status"] == "rejected" and result["effect"] == "recorded_only"
    row = conn.execute("SELECT * FROM proposal WHERE id = ?", (proposal_id,)).fetchone()
    assert row["status"] == "rejected" and row["decided_at"] is not None
    assert ledger.history(conn, "proposal", proposal_id)[-1]["reason"] == "驳回——这个判断的依据太单薄"


def test_deciding_twice_conflicts_and_missing_is_404(conn):
    # 用 material_judgment 当样本：它批准只记账，不要求 payload 有什么形状
    proposal_id = add_proposal(conn, "material_judgment", {})

    decide(conn, proposal_id, approved=True)

    with pytest.raises(proposals.ProposalConflict):
        decide(conn, proposal_id, approved=False, reason="反悔")
    with pytest.raises(proposals.ProposalNotFound):
        decide(conn, 999, approved=True)


def test_route_translates_domain_errors_to_status_codes(conn):
    """接口层只做翻译：不存在 404、已裁定 409、规则拒绝 400。"""
    from fastapi import HTTPException

    missing = add_proposal(conn, "material_judgment", {})
    proposals.decide(conn, missing, approved=True)
    pending = add_proposal(conn, "material_judgment", {})

    assert decide_via_route(conn, pending, approved=True)["status"] == "accepted"
    with pytest.raises(HTTPException) as decided:
        decide_via_route(conn, pending, approved=True)
    assert decided.value.status_code == 409
    with pytest.raises(HTTPException) as rejected_without_reason:
        decide_via_route(conn, pending, approved=False)
    assert rejected_without_reason.value.status_code == 409  # 先撞「已裁定过」
    with pytest.raises(HTTPException) as not_found:
        decide_via_route(conn, 999, approved=True)
    assert not_found.value.status_code == 404
    fresh = add_proposal(conn, "plan_replan", {"options": [{"kind": "postpone", "label": "顺延"}]})
    with pytest.raises(HTTPException) as rejected_without_reason:
        decide_via_route(conn, fresh, approved=False, reason="   ")
    assert rejected_without_reason.value.status_code == 400
    with pytest.raises(HTTPException) as bad_option:
        decide_via_route(conn, fresh, approved=True, option="reduce_scope")
    assert bad_option.value.status_code == 400


# ---------- 批准到底动什么 ----------

# ---------- 批准「档案变更」= 真的写进档案（T28 起） ----------
#
# 这一类提案原先没有生产者、批准也只记账；T28 的计划对话成了它的第一个生产者，
# 于是「批准」第一次要真的改档案——所以「验不过就一条都不写」这条纪律在这里最要紧。

def profile_items(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT id, category, content FROM profile_item WHERE status = 'active' ORDER BY id"
    ).fetchall()
    return [dict(row) for row in rows]


def change_proposal(conn, *, category="current_state", content="晚上只剩一小时", why="聊出来的"):
    return add_proposal(
        conn,
        "profile_change",
        {"category": category, "content": content, "why": why, "plan_id": 1},
    )


def test_approving_a_profile_change_really_writes_the_archive(conn):
    proposal_id = change_proposal(conn)
    assert profile_items(conn) == []

    result = decide_via_route(conn, proposal_id, approved=True)

    assert result["effect"] == "profile_written"
    assert result["written"] == {
        "id": result["written"]["id"],
        "category": "current_state",
        "content": "晚上只剩一小时",
    }
    assert profile_items(conn) == [
        {"id": result["written"]["id"], "category": "current_state", "content": "晚上只剩一小时"}
    ]
    # 台账要能回答「这条是怎么进的档案」：理由是提案号 + 聊出的 why
    events = ledger.history(conn, "profile_item", result["written"]["id"])
    assert [event["change_type"] for event in events] == ["create"]
    assert f"提案 #{proposal_id}" in events[0]["reason"]
    assert "聊出来的" in events[0]["reason"]


def test_rejecting_a_profile_change_writes_nothing(conn):
    proposal_id = change_proposal(conn)

    result = decide_via_route(conn, proposal_id, approved=False, reason="这只是暂时的")

    assert result["effect"] == "recorded_only" and result["written"] is None
    assert profile_items(conn) == []
    assert status_of(conn, proposal_id) == "rejected"


def test_a_duplicate_profile_change_is_refused_and_stays_pending(conn):
    """同类别一字不差已经有一条 → 409，且提案不动（可重裁）。"""
    ledger.create_active(
        conn, "profile_item", {"category": "current_state", "content": "晚上只剩一小时"}, actor="user"
    )
    proposal_id = change_proposal(conn)

    with pytest.raises(proposals.ProposalConflict):
        decide(conn, proposal_id, approved=True)

    assert status_of(conn, proposal_id) == "pending"
    assert len(profile_items(conn)) == 1  # 没有第二条被写进去


@pytest.mark.parametrize(
    "payload",
    [
        {"category": "心情", "content": "有点累"},          # 类别不在五个令牌里
        {"category": "current_state", "content": "   "},     # 没有内容
    ],
)
def test_a_malformed_profile_change_is_refused(conn, payload):
    proposal_id = add_proposal(conn, "profile_change", payload)

    with pytest.raises(proposals.ProposalError):
        decide(conn, proposal_id, approved=True)

    assert status_of(conn, proposal_id) == "pending"
    assert profile_items(conn) == []


# ---------- T29：删掉的两类不再可裁 ----------
#
# `stage_advance` 与 `plan_replan` 整类删除（规则不再产、也就没有裁定入口）。
# 库里可能还留着老类型（历史），这里钉住「明确拒绝、且保持 pending 可驳回」。

@pytest.mark.parametrize("kind", ["stage_advance", "plan_replan"])
def test_deleted_proposal_kinds_are_refused(conn, kind):
    proposal_id = add_proposal(conn, kind, {"options": [{"kind": "postpone", "label": "顺延"}]})

    with pytest.raises(proposals.ProposalError):
        decide(conn, proposal_id, approved=True)

    assert status_of(conn, proposal_id) == "pending"
    # 驳回仍走得通：老提案的正当处置是留一条台账记录说它不作数
    assert decide(conn, proposal_id, approved=False, reason="这类已删除")["status"] == "rejected"
