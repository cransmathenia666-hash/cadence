"""记忆系统的单测（2026-09-21 落地，方案 `docs/记忆系统.md`）。

这一层验的是**边界**，不是模型好坏：记忆分几层、计划之间看不看得见、什么内容才准进候选、
谁能批量批准、到期的那条还会不会被当依据、扫描的游标会不会漏、删到底删干净没有。

七组东西：
① 三层与隔离：全局 / 计划内各自住在哪、计划 A 的记忆在计划 B 里读不到；
② 经历检索：六类经历都要能被翻到，关键词 / 来源 / 时间三种筛法都对；
③ 候选契约的确定性校验：来源不存在、摘录对不上、完全重复、取代目标已失效、跨计划动记忆
   ——五种都判不合格；近似重复只提示；
④ 批准与批量：单条裁定真的写进记忆、批量只收「新增 + 用户陈述」；
⑤ 复核到期：到期那条进「待复核」、**不进默认依据**，续期只推时间不改正文；
⑥ 扫描：游标只推进处理过的部分、「没有候选」也算成功、模型失败不落候选也不动游标、
   计划收尾只登记待扫描（不在收尾里调模型）；
⑦ 彻底删除：预览只读、执行后正文与来源原句都不在了、清不干净时**拒绝宣称成功**。

一律假上游打桩，不打真实接口、不花钱。
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from app import (
    agent_tools,
    db,
    dialogue,
    ledger,
    llm,
    memory,
    plan,
    proposals,
)

from test_dialogue import (  # noqa: F401 —— 复用同一套桩件，免得两处各写一遍
    ScriptedTransport,
    ask,
    envelope,
    make_plan,
    make_provider,
)


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "test.db"
    db.init(path)
    connection = db.connect(path)
    yield connection
    connection.close()


def other_plan(conn, goal: str = "另一件要做的事") -> int:
    return ledger.create_active(conn, "plan", {"goal": goal}, actor="user")


def say_it(conn, plan_id: int, text: str, role: str = "user") -> int:
    """往计划对话里塞一句——它是最常用的一类来源（「我说过的」）。"""
    return int(
        conn.execute(
            "INSERT INTO plan_dialogue (plan_id, role, content, created_at)"
            " VALUES (?, ?, ?, ?)",
            (plan_id, role, text, db.now_iso()),
        ).lastrowid
    )


def candidate_of(
    conn,
    *,
    action: str = "add",
    scope: str = "global",
    content: str | None = "我晚上十点以后不安排学习",
    source_kind: str = "user_stated",
    category: str | None = "current_state",
    kind: str | None = None,
    plan_id: int | None = None,
    target_id: int | None = None,
    decision: str | None = None,
    evidence: list[dict] | None = None,
    **extra,
) -> dict:
    data: dict = {
        "action": action,
        "scope": scope,
        "source_kind": source_kind,
        "reason": "他说过这件事",
    }
    if content is not None:
        data["content"] = content
    if scope == "global":
        data["category"] = category
    else:
        data["kind"] = kind or "constraint"
        data["plan_id"] = plan_id
    if target_id is not None:
        data["target_id"] = target_id
    if decision is not None:
        data["decision"] = decision
    data["evidence"] = [{"source_type": "plan_dialogue", "source_id": 0, "excerpt": "占位"}] if evidence is None else evidence
    data.update(extra)
    return data


def evidence_from_dialogue(text: str, source_id: int) -> list[dict]:
    return [{"source_type": "plan_dialogue", "source_id": source_id, "excerpt": text}]


def landed(conn) -> list[dict]:
    return memory.list_inbox(conn)["candidates"]


def proposal_rows(conn) -> list[dict]:
    return [dict(row) for row in conn.execute("SELECT * FROM proposal ORDER BY id").fetchall()]


# ============================================================
# ① 三层与隔离
# ============================================================

def test_global_and_plan_memories_live_in_different_homes(conn):
    """全局记忆住在档案表、计划内记忆住在 `plan_memory`；两级各自取得到。"""
    plan_id = make_plan(conn)
    memory.add_memory(conn, scope="global", category="current_state", content="最近精力差")
    memory.add_memory(conn, scope="plan", plan_id=plan_id, kind="constraint", content="这个计划不碰新框架")

    listing = memory.list_memories(conn, plan_id=plan_id)

    assert [item["content"] for item in listing["global"]] == ["最近精力差"]
    assert [item["content"] for item in listing["plan"]] == ["这个计划不碰新框架"]
    assert listing["plan"][0]["kind_label"] == "约束"
    assert listing["global"][0]["category_label"].startswith("当前状态")


def test_a_plan_cannot_see_another_plans_memory(conn):
    """计划内记忆的意义就在隔离：另一个计划读它，读不到。"""
    mine, theirs = make_plan(conn), other_plan(conn)
    memory.add_memory(conn, scope="plan", plan_id=mine, kind="decision", content="先做完后端再碰前端")

    assert [item["content"] for item in memory.list_memories(conn, plan_id=mine)["plan"]] == [
        "先做完后端再碰前端"
    ]
    assert memory.list_memories(conn, plan_id=theirs)["plan"] == []


def test_a_candidate_cannot_reach_into_another_plan(conn):
    """拿着别个计划的行号去「取代」它——判不合格。作用域是计划内记忆的全部意义。"""
    mine, theirs = make_plan(conn), other_plan(conn)
    theirs_memory = memory.add_memory(
        conn, scope="plan", plan_id=theirs, kind="constraint", content="别动这条"
    )["id"]
    source = say_it(conn, mine, "把那条约束改一下")

    payload, problem = memory.validate_candidate(
        conn,
        candidate_of(
            conn,
            action="supersede",
            scope="plan",
            plan_id=mine,
            target_id=theirs_memory,
            content="改成这样",
            evidence=evidence_from_dialogue("把那条约束改一下", source),
        ),
    )

    assert payload is None
    assert "不属于计划" in problem


def test_legacy_profile_rows_are_marked_not_guessed(conn, tmp_path):
    """老档案统一标成「历史手工录入」，**不替它猜来源**；新写的自带来源性质。"""
    conn.execute(
        "INSERT INTO profile_item (category, content, status, valid_from, created_at)"
        " VALUES ('long_axis', '老条目', 'active', ?, ?)",
        (db.now_iso(), db.now_iso()),
    )
    conn.commit()
    db.init(tmp_path / "test.db")  # 再 init 一次：补齐步骤要把它标上

    old = conn.execute("SELECT * FROM profile_item WHERE content = '老条目'").fetchone()
    new_id = memory.add_memory(conn, scope="global", category="life_log", content="新条目")
    fresh = conn.execute("SELECT * FROM profile_item WHERE id = ?", (new_id["id"],)).fetchone()

    assert old["source_kind"] == "legacy_manual"
    assert fresh["source_kind"] == "user_stated"


# ============================================================
# ② 经历检索：六类都要翻得到
# ============================================================

def seed_all_six(conn, plan_id: int) -> dict[str, int]:
    """把六类经历各造一条出来，返回每类的编号。"""
    dialogue_id = say_it(conn, plan_id, "我想把日志先上线")
    chat_id = int(
        conn.execute(
            "INSERT INTO plan_chat (plan_id, candidate_id, role, content, created_at)"
            " VALUES (?, 1, 'user', ?, ?)",
            (plan_id, "先从最简单的做", db.now_iso()),
        ).lastrowid
    )
    stage_id = plan.add_node(conn, plan_id, "stage", "上线日志", deliverable="能访问的日志页")
    report_id = int(
        conn.execute(
            "INSERT INTO report (node_id, status, note, created_at) VALUES (?, 'done', ?, ?)",
            (stage_id, "这周把静态页搭好了", db.now_iso()),
        ).lastrowid
    )
    request_id = int(
        conn.execute(
            "INSERT INTO learning_request (kind, raw_text, plan_id, created_at) VALUES ('search', ?, ?, ?)",
            ("不知道学什么", plan_id, db.now_iso()),
        ).lastrowid
    )
    candidate_id = int(
        conn.execute(
            "INSERT INTO candidate (request_id, title, why, status, reject_reason, valid_from, created_at)"
            " VALUES (?, '学 Kubernetes', '偏离主线', 'rejected', '现在用不上', ?, ?)",
            (request_id, db.now_iso(), db.now_iso()),
        ).lastrowid
    )
    proposal_id = ledger.create_active(
        conn,
        "proposal",
        {"kind": "plan_change", "payload": json.dumps({"plan_id": plan_id, "summary": "加一件任务"}, ensure_ascii=False)},
        actor="user",
    )
    field_change_id = int(
        conn.execute(
            "INSERT INTO ledger_event (entity_type, entity_id, change_type, before_value, after_value,"
            " reason, actor, created_at) VALUES ('plan_node', ?, 'update_fields', ?, ?, ?, 'user', ?)",
            (stage_id, '{"due_date": "2026-10-01"}', '{"due_date": "2026-10-08"}', "那周出差", db.now_iso()),
        ).lastrowid
    )
    return {
        "plan_dialogue": dialogue_id,
        "plan_chat": chat_id,
        "report": report_id,
        "candidate": candidate_id,
        "proposal": proposal_id,
        "field_change": field_change_id,
    }


def test_all_six_kinds_of_experience_are_searchable(conn):
    plan_id = make_plan(conn)
    seed_all_six(conn, plan_id)

    found = memory.search_experiences(conn, plan_id, limit=20)

    kinds = {item["source_type"] for item in found["items"]}
    assert kinds == set(memory.EXPERIENCE_SOURCES)


def test_experience_search_filters_by_keyword_source_and_time(conn):
    plan_id = make_plan(conn)
    ids = seed_all_six(conn, plan_id)

    by_keyword = memory.search_experiences(conn, plan_id, keyword="日志")
    by_source = memory.search_experiences(conn, plan_id, source_type="report")
    by_time = memory.search_experiences(conn, plan_id, since=(date.today() + timedelta(days=1)).isoformat())

    # 「日志」这条线索同时落在对话、报告、字段变更三处（字段变更写的是「上线日志」这个节点名）
    assert {(item["source_type"], item["source_id"]) for item in by_keyword["items"]} == {
        ("plan_dialogue", ids["plan_dialogue"]),
        ("report", ids["report"]),
        ("field_change", ids["field_change"]),
    }
    assert [item["source_id"] for item in by_source["items"]] == [ids["report"]]
    assert by_time["items"] == []


def test_experience_search_is_capped_at_eight(conn):
    """最多 8 条（方案第 5 节），且最新的在前。"""
    plan_id = make_plan(conn)
    for index in range(12):
        say_it(conn, plan_id, f"第 {index} 句话")

    found = memory.search_experiences(conn, plan_id)

    assert found["matched"] == 12
    assert found["returned"] == memory.SEARCH_LIMIT
    assert found["items"][0]["excerpt"].endswith("第 11 句话")


# ============================================================
# ③ 候选契约：四条确定性校验 + 近似重复只提示
# ============================================================

def test_a_candidate_must_quote_its_source_verbatim(conn):
    plan_id = make_plan(conn)
    say_it(conn, plan_id, "我最近晚上只有一小时")

    _, problem = memory.validate_candidate(
        conn,
        candidate_of(
            conn,
            evidence=[{"source_type": "plan_dialogue", "source_id": 1, "excerpt": "我说的是别的"}],
        ),
    )

    assert problem is not None
    assert "摘录在来源里找不到" in problem


def test_a_candidate_must_point_at_a_real_source(conn):
    _, problem = memory.validate_candidate(
        conn, candidate_of(conn, evidence=evidence_from_dialogue("随便一段", 999))
    )

    assert problem is not None
    assert "来源不存在" in problem


def test_a_candidate_without_evidence_is_rejected(conn):
    _, problem = memory.validate_candidate(conn, candidate_of(conn, evidence=[]))

    assert problem is not None
    assert "至少要给一条来源证据" in problem


def test_an_exact_duplicate_is_refused_but_a_near_one_is_only_flagged(conn):
    """一字不差的判不合格；像但不一样的**只提示**，绝不自动合并。"""
    plan_id = make_plan(conn)
    source = say_it(conn, plan_id, "三个月内不碰新框架")
    memory.add_memory(conn, scope="plan", plan_id=plan_id, kind="constraint", content="三个月内不碰新框架")

    same, problem = memory.validate_candidate(
        conn,
        candidate_of(
            conn,
            scope="plan",
            plan_id=plan_id,
            content="三个月内不碰新框架",
            evidence=evidence_from_dialogue("三个月内不碰新框架", source),
        ),
    )
    near, near_problem = memory.validate_candidate(
        conn,
        candidate_of(
            conn,
            scope="plan",
            plan_id=plan_id,
            content="三个月之内不碰新的框架",
            evidence=evidence_from_dialogue("三个月内不碰新框架", source),
        ),
    )

    assert same is None and "已经有这条" in problem
    assert near_problem is None
    assert "疑似与" in near["duplicate_hint"]


def test_superseding_something_already_gone_is_refused(conn):
    """取代的目标必须**仍然有效**——已经作废的那条不能当靶子。"""
    plan_id = make_plan(conn)
    source = say_it(conn, plan_id, "先写后端再碰前端")
    old_id = memory.add_memory(conn, scope="plan", plan_id=plan_id, kind="decision", content="先写后端")["id"]
    memory.void_memory(conn, "plan", old_id, reason="想法变了")

    payload, problem = memory.validate_candidate(
        conn,
        candidate_of(
            conn,
            action="supersede",
            scope="plan",
            plan_id=plan_id,
            target_id=old_id,
            content="先写后端再碰前端",
            evidence=evidence_from_dialogue("先写后端再碰前端", source),
        ),
    )

    assert payload is None
    assert "已不是当前有效值" in problem


def test_the_model_may_not_pass_off_a_guess_as_your_words(conn):
    """推断必须标明是推断：`legacy_manual` 这类取值不许由模型给。"""
    plan_id = make_plan(conn)
    source = say_it(conn, plan_id, "也许我该学点算法")

    _, problem = memory.validate_candidate(
        conn,
        candidate_of(
            conn,
            source_kind="legacy_manual",
            evidence=evidence_from_dialogue("也许我该学点算法", source),
        ),
    )

    assert problem is not None
    assert "agent_inferred" in problem


# ============================================================
# ④ 批准与批量
# ============================================================

def test_approving_a_candidate_writes_the_memory(conn):
    plan_id = make_plan(conn)
    source = say_it(conn, plan_id, "我晚上十点以后不安排学习")
    payload, problem = memory.validate_candidate(
        conn, candidate_of(conn, evidence=evidence_from_dialogue("我晚上十点以后不安排学习", source))
    )
    assert problem is None
    proposal_id = memory.land_candidate(conn, payload, source="手动扫描产出")

    result = proposals.decide(conn, proposal_id, approved=True, reason="记下")

    assert result["effect"] == "memory_added"
    assert result["remembered"]["memory"]["content"] == "我晚上十点以后不安排学习"
    assert result["remembered"]["memory"]["evidence"][0]["source_id"] == source


def test_approving_a_supersede_keeps_the_old_one_in_the_ledger(conn):
    plan_id = make_plan(conn)
    source = say_it(conn, plan_id, "改成下午学两小时")
    old_id = memory.add_memory(conn, scope="plan", plan_id=plan_id, kind="preference", content="晚上学一小时")["id"]
    payload, problem = memory.validate_candidate(
        conn,
        candidate_of(
            conn,
            action="supersede",
            scope="plan",
            plan_id=plan_id,
            target_id=old_id,
            content="下午学两小时",
            evidence=evidence_from_dialogue("改成下午学两小时", source),
        ),
    )
    assert problem is None
    proposal_id = memory.land_candidate(conn, payload, source="手动扫描产出")

    result = proposals.decide(conn, proposal_id, approved=True)

    assert result["effect"] == "memory_superseded"
    assert result["remembered"]["before"] == "晚上学一小时"
    row = conn.execute("SELECT * FROM plan_memory WHERE id = ?", (old_id,)).fetchone()
    assert row["status"] == "superseded"
    assert [item["content"] for item in memory.list_memories(conn, plan_id=plan_id)["plan"]] == ["下午学两小时"]


def test_approving_rechecks_because_the_world_moves(conn):
    """从落候选到批准之间，目标可能已经被作废了——那时给冲突，不照着旧假设写。"""
    plan_id = make_plan(conn)
    source = say_it(conn, plan_id, "改成一周三次")
    old_id = memory.add_memory(conn, scope="plan", plan_id=plan_id, kind="preference", content="每周两次")["id"]
    payload, _ = memory.validate_candidate(
        conn,
        candidate_of(
            conn,
            action="supersede",
            scope="plan",
            plan_id=plan_id,
            target_id=old_id,
            content="一周三次",
            evidence=evidence_from_dialogue("改成一周三次", source),
        ),
    )
    proposal_id = memory.land_candidate(conn, payload, source="手动扫描产出")
    memory.void_memory(conn, "plan", old_id, reason="这条作废了")

    with pytest.raises(proposals.ProposalConflict):
        proposals.decide(conn, proposal_id, approved=True)

    # 提案保持待处理，可以再裁一次
    assert conn.execute("SELECT status FROM proposal WHERE id = ?", (proposal_id,)).fetchone()["status"] == "pending"


def test_batch_only_takes_adds_you_said_yourself(conn):
    """批量批准只收「新增 + 用户陈述」；推断与取代一律逐条。"""
    plan_id = make_plan(conn)
    source = say_it(conn, plan_id, "我周末不排任务")
    sais = memory.land_candidate(
        conn,
        memory.validate_candidate(
            conn,
            candidate_of(
                conn,
                scope="plan",
                plan_id=plan_id,
                content="周末不排任务",
                evidence=evidence_from_dialogue("我周末不排任务", source),
            ),
        )[0],
        source="扫描产出",
    )
    guessed = memory.land_candidate(
        conn,
        memory.validate_candidate(
            conn,
            candidate_of(
                conn,
                scope="plan",
                plan_id=plan_id,
                content="他大概更喜欢上午干活",
                source_kind="agent_inferred",
                evidence=evidence_from_dialogue("我周末不排任务", source),
            ),
        )[0],
        source="扫描产出",
    )

    result = memory.batch_approve(conn, [sais, guessed])

    assert [item["proposal_id"] for item in result["approved"]] == [sais]
    assert [item["proposal_id"] for item in result["skipped"]] == [guessed]
    assert "只能逐条确认" in result["skipped"][0]["why"]


def test_every_candidate_carries_what_it_needs_to_be_judged(conn):
    """一条候选必须带：动作、范围、陈述还是推断、复核时间、理由、至少一条来源。"""
    plan_id = make_plan(conn)
    source = say_it(conn, plan_id, "我周三晚上有课")
    payload, _ = memory.validate_candidate(
        conn,
        candidate_of(
            conn,
            scope="global",
            category="life_log",
            content="周三晚上有课",
            review_at="2026-12-31",
            evidence=evidence_from_dialogue("我周三晚上有课", source),
        ),
    )
    memory.land_candidate(conn, payload, source="扫描产出")

    item = landed(conn)[0]

    assert item["action_label"] == "新增"
    assert item["scope_label"] == "全局"
    assert item["source_kind_label"] == "用户陈述"
    assert item["reason"] and item["review_at"] == "2026-12-31"
    assert item["evidence"][0]["excerpt"] == "我周三晚上有课"
    assert item["batch_eligible"] is True


# ============================================================
# ⑤ 复核到期：不召回、可续期
# ============================================================

def test_a_memory_past_its_review_date_is_not_used_by_default(conn):
    """到复核时间的那条进「待复核」，**不参与回答**（`read_memories` 不放进默认结果）。"""
    plan_id = make_plan(conn)
    stale = memory.add_memory(
        conn,
        scope="plan",
        plan_id=plan_id,
        kind="constraint",
        content="这学期只学一门课",
        review_at=(date.today() - timedelta(days=1)).isoformat(),
    )
    fresh = memory.add_memory(conn, scope="plan", plan_id=plan_id, kind="decision", content="先把后端写完")

    read = memory.read_memories(conn, plan_id)

    assert [item["content"] for item in read["plan"]] == ["先把后端写完"]
    assert [item["content"] for item in read["due"]] == ["这学期只学一门课"]
    assert read["due_count"] == 1
    # 页面那侧仍看得见它（「待复核」标签页）
    assert stale["review_due"] is True
    assert fresh["review_due"] is False


def test_renewing_only_moves_the_date(conn):
    plan_id = make_plan(conn)
    memory_id = memory.add_memory(
        conn,
        scope="plan",
        plan_id=plan_id,
        kind="constraint",
        content="这学期只学一门课",
        review_at=(date.today() - timedelta(days=1)).isoformat(),
    )["id"]

    renewed = memory.renew_memory(conn, "plan", memory_id, reason="还是这样")

    assert renewed["review_due"] is False
    assert renewed["content"] == "这学期只学一门课"
    assert conn.execute("SELECT COUNT(*) AS n FROM plan_memory").fetchone()["n"] == 1  # 不产生新行


def test_an_explicit_review_date_beats_the_default_ninety_days(conn):
    """「还作数」可以指定下次复核的日子（走查整改第 1 条）：给了就照给的，没给才推 90 天。

    用户走查时的反例是「养成一周的习惯不该复核三个月」——界面现在点开就能选日期，
    默认值仍是 90 天（只是不再是唯一的选择）。
    """
    plan_id = make_plan(conn)
    memory_id = memory.add_memory(
        conn,
        scope="plan",
        plan_id=plan_id,
        kind="constraint",
        content="这学期只学一门课",
        review_at=(date.today() - timedelta(days=1)).isoformat(),
    )["id"]

    picked = memory.renew_memory(conn, "plan", memory_id, review_at="2026-10-05", reason="先盯一周")

    assert picked["review_at"] == "2026-10-05"
    assert picked["review_due"] is False
    assert picked["content"] == "这学期只学一门课"

    # 不给日期才是那个默认（默认没变，只是不再是唯一的选择）
    defaulted = memory.renew_memory(conn, "plan", memory_id)
    assert defaulted["review_at"] == (date.today() + timedelta(days=memory.REVIEW_DAYS)).isoformat()

    # 推完仍然不是新行：内容没变，变的只是「什么时候再回头看它一眼」
    assert conn.execute("SELECT COUNT(*) AS n FROM plan_memory").fetchone()["n"] == 1


def test_review_candidate_can_void_it_too(conn):
    plan_id = make_plan(conn)
    source = say_it(conn, plan_id, "那门课这学期不上了")
    memory_id = memory.add_memory(
        conn,
        scope="plan",
        plan_id=plan_id,
        kind="constraint",
        content="这学期只学一门课",
        review_at=(date.today() - timedelta(days=1)).isoformat(),
    )["id"]
    payload, problem = memory.validate_candidate(
        conn,
        candidate_of(
            conn,
            action="review",
            scope="plan",
            plan_id=plan_id,
            target_id=memory_id,
            decision="void",
            content=None,
            evidence=evidence_from_dialogue("那门课这学期不上了", source),
        ),
    )
    assert problem is None
    proposal_id = memory.land_candidate(conn, payload, source="每周扫描产出")

    result = proposals.decide(conn, proposal_id, approved=True)

    assert result["effect"] == "memory_voided"
    assert memory.list_memories(conn, plan_id=plan_id)["plan"] == []


# ============================================================
# ⑥ 扫描
# ============================================================

def scan_output(*items: dict) -> str:
    return json.dumps({"memories": list(items)}, ensure_ascii=False)


def test_a_scan_lands_candidates_and_moves_the_cursor(conn):
    """扫出来的东西进收件箱（**不写记忆**），游标推到处理过的那条。"""
    make_provider(conn)
    plan_id = make_plan(conn)
    source = say_it(conn, plan_id, "我周二晚上固定有课")
    transport = ScriptedTransport(
        scan_output(
            candidate_of(
                conn,
                scope="plan",
                plan_id=plan_id,
                content="周二晚上固定有课",
                evidence=evidence_from_dialogue("我周二晚上固定有课", source),
            )
        )
    )

    report = memory.scan(conn, trigger=memory.TRIGGER_MANUAL, plan_id=plan_id, transport=transport)

    assert report["status"] == "ok"
    assert report["scanned"] == 1 and report["candidates"] == 1
    assert memory.list_memories(conn, plan_id=plan_id)["plan"] == []  # 只产候选，没写记忆
    assert len(landed(conn)) == 1

    again = memory.scan(conn, trigger=memory.TRIGGER_MANUAL, plan_id=plan_id, transport=ScriptedTransport())
    assert again["scanned"] == 0  # 游标之后没有新经历了，不会再花一次调用


def test_no_candidates_is_still_a_success(conn):
    """「没有候选」也是成功：游标照推，下一批不重复扫。"""
    make_provider(conn)
    plan_id = make_plan(conn)
    say_it(conn, plan_id, "今天天气不错")

    report = memory.scan(
        conn, trigger=memory.TRIGGER_MANUAL, plan_id=plan_id, transport=ScriptedTransport(scan_output())
    )
    again = memory.scan(conn, trigger=memory.TRIGGER_MANUAL, plan_id=plan_id, transport=ScriptedTransport())

    assert report["status"] == "ok" and report["candidates"] == 0
    assert again["scanned"] == 0


def test_a_failed_scan_lands_nothing_and_keeps_the_cursor(conn):
    """模型连着两次给不出合格输出：记一行 failed、**一条候选都不落**、游标不动。"""
    make_provider(conn)
    plan_id = make_plan(conn)
    say_it(conn, plan_id, "我周二固定有课")
    transport = ScriptedTransport("这不是 JSON", "还是不是 JSON")

    report = memory.scan(conn, trigger=memory.TRIGGER_MANUAL, plan_id=plan_id, transport=transport)

    assert report["status"] == "failed"
    assert "没有落任何候选" in report["error"]
    assert landed(conn) == []
    rows = conn.execute("SELECT * FROM memory_scan WHERE status = 'failed'").fetchall()
    assert len(rows) == 1 and rows[0]["cursor"] is None
    # 游标没动：下一批还会看到同一条经历，重来一次
    assert memory.latest_cursor(conn, plan_id) == {}


def test_one_unusable_candidate_does_not_take_the_others_down(conn):
    """单条不合格就丢掉它自己，同批里合格的照常进收件箱。"""
    make_provider(conn)
    plan_id = make_plan(conn)
    source = say_it(conn, plan_id, "我周二晚上固定有课")
    good = candidate_of(
        conn, scope="plan", plan_id=plan_id, content="周二晚上固定有课",
        evidence=evidence_from_dialogue("我周二晚上固定有课", source),
    )
    bad = candidate_of(
        conn, scope="plan", plan_id=plan_id, content="凭空编的一条",
        evidence=[{"source_type": "plan_dialogue", "source_id": source, "excerpt": "没说过的话"}],
    )
    transport = ScriptedTransport(scan_output(good, bad))

    report = memory.scan(conn, trigger=memory.TRIGGER_MANUAL, plan_id=plan_id, transport=transport)

    assert report["candidates"] == 1
    assert len(report["dropped"]) == 1 and "摘录在来源里找不到" in report["dropped"][0]["why"]
    assert len(landed(conn)) == 1


def test_closing_a_plan_only_registers_a_scan(conn):
    """计划收尾**不在收尾里调模型**：只登记一条待扫描，之后由周任务或记忆页做掉。"""
    plan_id = make_plan(conn)
    plan.close_plan(conn, plan_id, "做完了")

    registering = memory.register_pending_scan(conn, plan_id)
    pending = memory.pending_scans(conn)

    assert [int(row["id"]) for row in pending] == [registering]
    assert memory.scan_report(conn)[0]["status"] == "pending"


def test_weekly_runs_whats_pending_and_scans_each_live_plan(conn):
    """每周那一批：先把欠着的做掉，再给每个进行中的计划各扫一批。"""
    make_provider(conn)
    live, done = make_plan(conn), other_plan(conn)
    plan.close_plan(conn, done, "做完了")
    memory.register_pending_scan(conn, done)
    say_it(conn, live, "我周二晚上固定有课")

    reports = memory.weekly_scans(conn, transport=ScriptedTransport(scan_output(), scan_output()))

    assert [item["status"] for item in reports] == ["ok", "ok"]
    assert reports[0]["trigger"] == memory.TRIGGER_PLAN_CLOSE
    assert reports[1]["trigger"] == memory.TRIGGER_WEEKLY and reports[1]["plan_id"] == live


# ============================================================
# ⑦ 彻底删除
# ============================================================

def test_purge_takes_the_text_out_of_the_sources_too(conn):
    """彻底删除：正文本、证据摘录、来源里那句原话全都不在，只剩一条不含内容的墓碑。"""
    plan_id = make_plan(conn)
    source = say_it(conn, plan_id, "我周二晚上固定有课")
    memory_id = memory.add_memory(
        conn,
        scope="plan",
        plan_id=plan_id,
        kind="constraint",
        content="我周二晚上固定有课",
    )["id"]
    memory._attach_evidence(
        conn,
        "plan",
        memory_id,
        [{"source_type": "plan_dialogue", "source_id": source, "excerpt": "我周二晚上固定有课"}],
    )

    preview = memory.purge_preview(conn, "plan", memory_id)
    result = memory.purge(conn, "plan", memory_id, reason="这条不该记")

    assert preview["irreversible"] is True and preview["content"] == "我周二晚上固定有课"
    assert result["complete"] is True and result["leftover"] == []
    assert conn.execute("SELECT COUNT(*) AS n FROM plan_memory WHERE id = ?", (memory_id,)).fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM memory_evidence").fetchone()["n"] == 0
    said = conn.execute("SELECT content FROM plan_dialogue WHERE id = ?", (source,)).fetchone()
    assert said["content"] == memory.PURGED_TEXT  # 来源行还在，但那一句被换掉了
    assert conn.execute("SELECT COUNT(*) AS n FROM memory_deletion").fetchone()["n"] == 1


def test_purge_refuses_to_claim_success_when_copies_remain(conn):
    """同一句话还留在别处、而那条关系没建立过证据——**如实说没删干净**，不宣称成功。"""
    plan_id = make_plan(conn)
    source = say_it(conn, plan_id, "我周二晚上固定有课")
    memory_id = memory.add_memory(
        conn, scope="plan", plan_id=plan_id, kind="constraint", content="我周二晚上固定有课"
    )["id"]
    memory._attach_evidence(
        conn,
        "plan",
        memory_id,
        [{"source_type": "plan_dialogue", "source_id": source, "excerpt": "我周二晚上固定有课"}],
    )
    stage_id = plan.add_node(conn, plan_id, "stage", "排课", deliverable="一张课表")
    conn.execute(
        "INSERT INTO report (node_id, status, note, created_at) VALUES (?, 'done', ?, ?)",
        (stage_id, "我周二晚上固定有课，所以那天没排", db.now_iso()),
    )
    conn.commit()

    result = memory.purge(conn, "plan", memory_id, reason="删掉")

    assert result["complete"] is False
    assert {"table": "report", "column": "note", "id": 1} in result["leftover"]
    assert "不敢说删除成功" in result["note"]


def test_purge_also_clears_the_copy_in_the_ledger_flow(conn):
    """台账流水里那份正文副本也要清掉——「取代」那条流水的 `entity_id` 挂的是**旧行**编号，
    照新行 id 去清正好会漏掉它（冒烟第 16 步踩到过这个坑）。"""
    plan_id = make_plan(conn)
    old_id = memory.add_memory(conn, scope="plan", plan_id=plan_id, kind="constraint", content="旧的说法")["id"]
    new_id = memory.supersede_memory(conn, "plan", old_id, content="新的说法", reason="改口了")["id"]

    result = memory.purge(conn, "plan", new_id, reason="彻底删掉")

    assert result["complete"] is True and result["leftover"] == []
    left = conn.execute(
        "SELECT COUNT(*) AS n FROM ledger_event WHERE before_value LIKE '%新的说法%'"
        " OR after_value LIKE '%新的说法%'"
    ).fetchone()["n"]
    assert left == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM ledger_event").fetchone()["n"] > 0  # 流水行还在


def test_a_purged_candidate_keeps_no_text(conn):
    """引用了这条记忆的候选，payload 里的正文也要抹掉。"""
    plan_id = make_plan(conn)
    source = say_it(conn, plan_id, "改成下午学两小时")
    old_id = memory.add_memory(conn, scope="plan", plan_id=plan_id, kind="preference", content="晚上学一小时")["id"]
    payload, _ = memory.validate_candidate(
        conn,
        candidate_of(
            conn, action="supersede", scope="plan", plan_id=plan_id, target_id=old_id,
            content="下午学两小时", evidence=evidence_from_dialogue("改成下午学两小时", source),
        ),
    )
    proposal_id = memory.land_candidate(conn, payload, source="扫描产出")

    memory.purge(conn, "plan", old_id, reason="不记了")

    row = conn.execute("SELECT payload FROM proposal WHERE id = ?", (proposal_id,)).fetchone()
    assert "下午学两小时" not in row["payload"]
    assert json.loads(row["payload"])["purged"] is True


# ============================================================
# ⑧ Agent 那两条只读工具
# ============================================================

def test_the_agent_can_read_memories_and_see_what_is_due(conn):
    plan_id = make_plan(conn)
    memory.add_memory(conn, scope="global", category="current_state", content="最近精力差")
    memory.add_memory(
        conn, scope="plan", plan_id=plan_id, kind="constraint", content="不碰新框架",
        review_at=(date.today() - timedelta(days=1)).isoformat(),
    )

    outcome = agent_tools.execute(conn, plan_id, "read_memories", {})

    assert "最近精力差" in outcome.text
    assert "已到复核时间" in outcome.text and "不碰新框架" in outcome.text
    # 到期的单独一段、明说「先别当依据」
    assert "先别当依据" in outcome.text
    assert "没算作依据" in outcome.summary


def test_the_agent_can_search_experiences_without_logging_the_keyword(conn):
    plan_id = make_plan(conn)
    say_it(conn, plan_id, "我想先做静态页")

    outcome = agent_tools.execute(conn, plan_id, "search_experiences", {"keyword": "静态页"})

    assert "静态页" in outcome.text
    assert "命中 1 条" in outcome.summary
    # 关键词本身是私事，**只记「给了」不记值**
    assert "静态页" not in agent_tools.summarize_call("search_experiences", {"keyword": "静态页"})
    assert "已隐去" in agent_tools.summarize_call("search_experiences", {"keyword": "静态页"})


def test_the_agent_cannot_read_another_plans_experiences(conn):
    """计划编号由系统注入：它只能看这一段对话所属的计划。"""
    mine, theirs = make_plan(conn), other_plan(conn)
    say_it(conn, theirs, "这是另一个计划里的话")

    outcome = agent_tools.execute(conn, mine, "search_experiences", {})

    assert "另一个计划里的话" not in outcome.text


def test_the_agent_cannot_pick_a_plan_of_its_own(conn):
    plan_id = make_plan(conn)
    with pytest.raises(agent_tools.ToolError):
        agent_tools.execute(conn, plan_id, "search_experiences", {"plan_id": 2})


def test_the_dialogue_prompt_lists_the_new_tools(conn):
    """目录由注册表生成，新工具一加就出现在它能读的清单里。"""
    catalog = agent_tools.catalog_text()

    assert "read_memories" in catalog and "search_experiences" in catalog
    assert list(agent_tools.TOOLS) == [
        "read_current_plan",
        "read_recent_reports",
        "read_profile",
        "read_plan_origin",
        "read_memories",
        "search_experiences",
    ]


def test_experience_search_takes_the_chinese_source_name_too(conn):
    """来源类型写中文也认（走查整改第 4 条），读到的是同一批经历。"""
    plan_id = make_plan(conn)
    say_it(conn, plan_id, "我想先做静态页")

    via_chinese = agent_tools.execute(conn, plan_id, "search_experiences", {"source_type": "计划对话"})
    via_token = agent_tools.execute(conn, plan_id, "search_experiences", {"source_type": "plan_dialogue"})

    assert via_chinese.text == via_token.text
    assert "我想先做静态页" in via_chinese.text
    assert "计划对话" in via_chinese.summary


def test_a_bad_source_type_is_refused_in_plain_chinese(conn):
    """来源类型不认时也只列中文名——英文令牌串不再甩进「本轮依据」。"""
    plan_id = make_plan(conn)

    with pytest.raises(agent_tools.ToolError) as error:
        agent_tools.execute(conn, plan_id, "search_experiences", {"source_type": "聊天记录"})

    said = str(error.value)
    assert "计划对话" in said and "你写的是「聊天记录」" in said
    assert "plan_dialogue" not in said


# ============================================================
# ⑧ 记忆变化感知：水印 + 变化清单 + 摘要行（走查整改第 3 条）
# ============================================================

# 把水印按到过去的某个时刻——变化清单只认「水印之后」的，测的就是这一段。
PAST = "2026-01-01T09:00:00+08:00"


def rewind_to_past(conn) -> None:
    """把**到此刻为止已经发生过的事**的时间戳按到 PAST（水印之前）。

    摘要只认水印之后的变化，而库里的时间戳是秒级精度：测试里「新增 → 读记忆 → 改动」三步
    都落在同一秒，不把它们在时间上分开就判不出先后。按到过去是让用例确定，不是绕过判定。
    """
    conn.execute("UPDATE ledger_event SET created_at = ? WHERE created_at > ?", (PAST, PAST))
    conn.execute("UPDATE memory_deletion SET deleted_at = ? WHERE deleted_at > ?", (PAST, PAST))
    conn.execute("UPDATE plan_dialogue SET created_at = ? WHERE created_at > ?", (PAST, PAST))
    conn.commit()


def need_provider(conn) -> None:
    """要跑一轮对话就先备好上游（一家就够，重复调用不再建）。"""
    if conn.execute("SELECT id FROM llm_provider WHERE enabled = 1").fetchone() is None:
        make_provider(conn)


def read_memories_once(conn, plan_id: int, when: str | None = None) -> None:
    """跑一轮**真读了记忆**的对话，那一行运行账就是这段对话的水印。

    `when` 用来把那一行的时间按到某个时刻（同上：秒级精度下同秒的先后判不出来）。
    """
    transport = ScriptedTransport(ask("read_memories"), envelope("记住了。"))
    need_provider(conn)
    dialogue.say(conn, plan_id, "先看看我记下的东西", transport=transport)
    if when is not None:
        conn.execute("UPDATE agent_run SET created_at = ? WHERE plan_id = ?", (when, plan_id))
        conn.commit()


def test_a_changed_memory_shows_up_in_the_digest(conn):
    """水印之后动过记忆 → 摘要行列得清是哪条、动了什么（新增 / 取代 / 作废 / 彻底删除）。"""
    plan_id = make_plan(conn)
    kept = memory.add_memory(conn, scope="global", category="current_state", content="晚上精力差")["id"]
    voided = memory.add_memory(
        conn, scope="plan", plan_id=plan_id, kind="decision", content="先学前端"
    )["id"]
    purged = memory.add_memory(
        conn, scope="plan", plan_id=plan_id, kind="preference", content="喜欢早上做事"
    )["id"]
    rewind_to_past(conn)  # 上面那三条新增算「水印之前」的事
    read_memories_once(conn, plan_id, when=PAST)

    memory.supersede_memory(conn, "global", kept, content="晚上精力反而好", reason="这周改了作息")
    memory.void_memory(conn, "plan", voided, reason="不这么定了")
    memory.purge(conn, "plan", purged, reason="这条不想留了")

    digest = memory.change_digest(conn, plan_id)

    assert digest is not None
    assert f"#{kept} 被取代" in digest
    assert f"#{voided} 作废" in digest
    assert f"#{purged} 被彻底删除" in digest  # 记忆行已经没了，这条只在墓碑里
    assert "变过 3 条" in digest
    assert "read_memories" in digest  # 提醒里要说清「读一次记忆」
    # 新增也是一次变化
    fresh = memory.add_memory(conn, scope="global", category="life_habit", content="周三晚上有课")["id"]
    assert f"#{fresh} 新增" in (memory.change_digest(conn, plan_id) or "")


def test_nothing_changed_means_no_digest_line(conn):
    """没变过就不出现这一行——摘要行是提醒，不是每轮都有的噪音。"""
    plan_id = make_plan(conn)
    memory.add_memory(conn, scope="plan", plan_id=plan_id, kind="decision", content="先学前端")
    read_memories_once(conn, plan_id)  # 水印就是「刚才」：上面那条在它之前，不算变化

    assert memory.change_digest(conn, plan_id) is None


def test_a_dialogue_that_never_read_memories_gets_no_digest(conn):
    """从没读过记忆的对话不出现这一行：那种历史里没有基于记忆的结论，没有旧认知要顶。"""
    plan_id = make_plan(conn)
    memory.add_memory(conn, scope="plan", plan_id=plan_id, kind="decision", content="先学前端")

    assert memory.change_digest(conn, plan_id) is None


def test_another_plans_memory_change_is_not_our_business(conn):
    """计划内记忆的变化只算它那一个计划——别的计划里的变动，这一段对话看不到。"""
    mine, theirs = make_plan(conn), other_plan(conn)
    read_memories_once(conn, mine, when=PAST)
    read_memories_once(conn, theirs, when=PAST)
    away = memory.add_memory(
        conn, scope="plan", plan_id=theirs, kind="decision", content="他们那边的决定"
    )["id"]

    assert memory.change_digest(conn, mine) is None
    theirs_digest = memory.change_digest(conn, theirs)
    assert theirs_digest is not None and f"#{away} 新增" in theirs_digest
    # 全局记忆的变化对所有对话都算（它不是哪个计划的事）
    memory.add_memory(conn, scope="global", category="life_habit", content="我早上效率高")
    assert memory.change_digest(conn, mine) is not None


def test_the_digest_reaches_the_opening_of_the_next_turn(conn):
    """这一行确实发到了下一轮的开头——排在工具目录与额度之后、历史之前。"""
    plan_id = make_plan(conn)
    memory_id = memory.add_memory(
        conn, scope="plan", plan_id=plan_id, kind="decision", content="先学前端"
    )["id"]
    rewind_to_past(conn)
    read_memories_once(conn, plan_id, when=PAST)
    memory.void_memory(conn, "plan", memory_id, reason="不这么定了")

    transport = ScriptedTransport(envelope("知道了。"))
    need_provider(conn)
    dialogue.say(conn, plan_id, "那接着聊", transport=transport)

    opening = transport.seen[0]["payload"]["messages"][1]["content"]
    assert "【记忆库变了】" in opening
    assert f"#{memory_id} 作废" in opening
    assert opening.index("【额度】") < opening.index("【记忆库变了】")
