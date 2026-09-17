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
    second = add_proposal(conn, "profile_change", {"items": []})
    decided = add_proposal(conn, "plan_replan", {"options": []})
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
    proposal_id = add_proposal(conn, "profile_change", {})

    decide(conn, proposal_id, approved=True)

    with pytest.raises(proposals.ProposalConflict):
        decide(conn, proposal_id, approved=False, reason="反悔")
    with pytest.raises(proposals.ProposalNotFound):
        decide(conn, 999, approved=True)


def test_route_translates_domain_errors_to_status_codes(conn):
    """接口层只做翻译：不存在 404、已裁定 409、规则拒绝 400。"""
    from fastapi import HTTPException

    missing = add_proposal(conn, "profile_change", {})
    proposals.decide(conn, missing, approved=True)
    pending = add_proposal(conn, "profile_change", {})

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

def test_approving_a_stage_advance_with_a_next_stage_changes_nothing_structural(conn):
    """「进下一阶段」是算出来的：阶段一收尾，当前阶段自己就往前走了，没有可写的结构。"""
    add_proposal(
        conn,
        "stage_advance",
        {"plan_id": 1, "stage_id": 1, "stage_title": "阶段一", "next_stage_id": 2,
         "next_stage_title": "阶段二", "done": 2, "skipped": 0, "question": "进不进下一阶段？"},
        reason="进不进下一阶段「阶段二」？",
    )
    proposal_id = conn.execute("SELECT MAX(id) AS n FROM proposal").fetchone()["n"]

    result = decide(conn, proposal_id, approved=True)

    assert result["effect"] == "recorded_only"
    assert ledger.history(conn, "proposal", proposal_id)[-1]["reason"] == "批准：进入下一阶段「阶段二」"


def test_approving_the_last_stage_advance_closes_the_plan(conn):
    """「后面没有更多阶段」那种推进提案，批准才是真有动作：计划收尾。"""
    plan_id = ledger.create_active(conn, "plan", {"goal": "测试计划"}, actor="user")
    stage_id = plan.add_node(conn, plan_id, "stage", "收尾阶段")
    checkpoint_id = plan.add_node(conn, plan_id, "checkpoint", "本周检查点", parent_id=stage_id)
    # 唯一的检查点做完 → 阶段收尾 → 自动产出「后面没有更多阶段」的推进提案
    plan.submit_report(conn, checkpoint_id, "done", "做完了")
    row = conn.execute("SELECT id, payload FROM proposal").fetchone()
    assert json.loads(row["payload"])["next_stage_id"] is None  # 确实是最后一段

    result = decide(conn, int(row["id"]), approved=True)

    assert result["effect"] == "plan_closed"
    plan_row = plan.resolve_plan(conn, plan_id)
    assert plan_row["status"] == "closed"
    assert ledger.history(conn, "plan", plan_id)[-1]["reason"] == (
        f"按提案 #{row['id']} 收尾：阶段都收尾了，后面没有更多阶段"
    )


def test_approving_a_replan_requires_one_of_the_offered_options(conn):
    proposal_id = add_proposal(
        conn,
        "plan_replan",
        {"week": "2026-W38", "plan_id": 1, "why": "落后 5 天",
         "options": [
             {"kind": "reduce_scope", "label": "减量", "detail": "缩一缩范围"},
             {"kind": "postpone", "label": "顺延", "detail": "往后推 5 天"},
         ]},
        reason="落后 5 天",
    )

    with pytest.raises(proposals.ProposalError):
        decide(conn, proposal_id, approved=True)  # 没选方向
    with pytest.raises(proposals.ProposalError):
        decide(conn, proposal_id, approved=True, option="swap_deliverable")  # 不在选项里
    assert status_of(conn, proposal_id) == "pending"  # 两次都没动它

    result = decide(conn, proposal_id, approved=True, option="postpone", reason="这周确实挪不动")

    assert result["effect"] == "replan_recorded" and result["option"] == "postpone"
    assert status_of(conn, proposal_id) == "accepted"
    assert ledger.history(conn, "proposal", proposal_id)[-1]["reason"] == "批准：顺延——这周确实挪不动"


def test_closing_an_already_closed_plan_is_a_conflict(conn):
    plan_id = ledger.create_active(conn, "plan", {"goal": "测试计划"}, actor="user")
    ledger.set_status(conn, "plan", plan_id, "closed", actor="user", reason="先前就收尾了")
    proposal_id = add_proposal(
        conn, "stage_advance", {"plan_id": plan_id, "next_stage_id": None, "stage_title": "最后一段"}
    )

    with pytest.raises(proposals.ProposalConflict):
        decide(conn, proposal_id, approved=True)

    assert status_of(conn, proposal_id) == "pending"


def test_replan_payload_from_the_real_producer_is_decidable(conn):
    """真实生产者产的那条重排提案，形状得跟裁定这边对得上。"""
    plan_id = ledger.create_active(conn, "plan", {"goal": "测试计划"}, actor="user")
    stage_id = plan.add_node(conn, plan_id, "stage", "第一段")
    checkpoint_id = plan.add_node(conn, plan_id, "checkpoint", "本周检查点", parent_id=stage_id)
    # 整周没有报告 → 产出重排提案（理由「本周没有报告，看不出这周推到哪了」）
    proposal_id = plan.ensure_weekly_replan_proposal(conn, today=date(2026, 9, 17), plan_id=plan_id)
    assert proposal_id is not None, "没报告的一周应该产出一条重排提案"

    listed = proposals.list_pending(conn, "plan_replan")["proposals"][0]
    options = [item["kind"] for item in listed["payload"]["options"]]

    result = decide(conn, proposal_id, approved=True, option=options[0])

    assert result["effect"] == "replan_recorded"
    assert checkpoint_id is not None  # 计划结构一个字没改
    assert plan.get_node(conn, checkpoint_id)["status"] == "not_started"
