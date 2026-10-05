"""知识库档案候选的批准写入侧（KB-03 写入侧，方案 §5.2 末段、§7.5）单测。

覆盖七组行为：
① 老 payload（计划对话提炼的、没有 action 与 evidence 的）批准行为一行不改；
② add 与 supersede 各自落对档案（取代旧值留痕、目标必须仍是当前有效条目）；
③ uncertain 的批准被拒，提案留在待裁定；
④ 带知识库出处的批准：档案写入、memory_evidence（knowledge 列）、提案终态**同一事务**
   ——事务里任何一步失败整体回滚，什么都不留；
⑤ 批准前文件已改 → 409 冲突，提案保持 pending；
⑥ 完全重复拒批、近似重复不拦批准（duplicate_hint 只是提示）；
⑦ 彻底删除清掉知识库证据副本（证据行 + 候选 payload），但原始文件一个字不动；
⑧ 批量批准维持现状：知识库候选混不进去，只能逐条。

假上游（ScriptedTransport）+ tmp_path 临时库，照 test_knowledge_scan 的打法；
真实知识库由用户手工走查，自动化测试绝不碰 data/cadence.db。
"""

from __future__ import annotations

import json

import pytest

from app import db, knowledge_base as kb, ledger, memory, proposals
from app.knowledge_base import hash_bytes

from test_dialogue import ScriptedTransport, make_provider


# ---------- 夹具与脚本工具（同 test_knowledge_scan） ----------


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    root = tmp_path / "vault"
    root.mkdir()
    monkeypatch.setenv("CADENCE_KNOWLEDGE_ROOTS", f"笔记={root}")
    return root


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "test.db"
    db.init(path)
    connection = db.connect(path)
    yield connection
    connection.close()


def _write(path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline=""：按原样落盘，别让 Windows 把 \n 翻译成 \r\n（摘录要逐字比对）
    path.write_text(content, encoding="utf-8", newline="")


def _read_call(path: str, **args) -> str:
    return json.dumps(
        {"tool_calls": [{"name": "knowledge_read_file", "args": {"path": path, **args}}]},
        ensure_ascii=False,
    )


def _final(*cards) -> str:
    return json.dumps({"cards": list(cards), "unread_gaps": []}, ensure_ascii=False)


def _card(
    *,
    action: str = "add",
    category: str = "current_state",
    content: str = "晚上十点以后不学习",
    why: str | None = "笔记里反复出现",
    source_kind: str = "agent_inferred",
    path: str = "a.md",
    excerpt: str = "晚上十点以后不学习。",
    line_start: int = 2,
    line_end: int = 2,
    **extra,
) -> dict:
    data = {
        "action": action,
        "category": category,
        "content": content,
        "why": why,
        "source_kind": source_kind,
        "evidence": [
            {"path": path, "excerpt": excerpt, "line_start": line_start, "line_end": line_end}
        ],
        "target_profile_id": None,
        "uncertainty_reason": None,
    }
    data.update(extra)
    return data


NOTE = "# 笔记\n晚上十点以后不学习。\n"
EXCERPT = "晚上十点以后不学习。"


def _scan_lands_card(conn, vault, card: dict, *, filename: str = "a.md", content: str = NOTE) -> int:
    """跑一次真扫描（假上游）让一张卡落成待裁定提案，返回提案编号。"""
    make_provider(conn)
    _write(vault / filename, content)
    report = kb.scan(conn, 1, transport=ScriptedTransport(_read_call(filename), _final(card)))
    assert report["status"] == "ok" and report["candidates_created"] == 1, report
    rows = conn.execute("SELECT id FROM proposal WHERE kind = 'profile_change'").fetchall()
    assert len(rows) == 1
    return int(rows[0]["id"])


def _knowledge_proposal(conn, payload: dict) -> int:
    """手工落一条知识库形状的档案变更提案（不走扫描，测批准侧自己的闸用）。"""
    return ledger.create_active(
        conn,
        "proposal",
        {
            "kind": "profile_change",
            "payload": json.dumps(payload, ensure_ascii=False),
            "reason": "知识库扫描（笔记）：来自 a.md 的档案候选",
        },
        actor="agent",
    )


def _payload_of(conn, proposal_id: int) -> dict:
    row = conn.execute("SELECT payload FROM proposal WHERE id = ?", (proposal_id,)).fetchone()
    return json.loads(row["payload"])


def _evidence_rows(conn) -> list[dict]:
    return [dict(row) for row in conn.execute("SELECT * FROM memory_evidence").fetchall()]


def _status_of(conn, proposal_id: int) -> str:
    return conn.execute("SELECT status FROM proposal WHERE id = ?", (proposal_id,)).fetchone()["status"]


def _profile_contents(conn) -> list[str]:
    return [
        str(row["content"])
        for row in conn.execute("SELECT content FROM profile_item WHERE status = 'active'")
    ]


# ---------- ① 老 payload 批准行为一行不改 ----------


def test_old_payload_without_action_still_approves_as_before(conn):
    """没有 action / evidence 的老提案：照旧新增一条档案，不写任何证据行。"""
    proposal_id = _knowledge_proposal(
        conn,
        {"category": "current_state", "content": "每天能投入两小时", "why": "计划对话里说的", "plan_id": None},
    )

    result = proposals.decide(conn, proposal_id, approved=True)

    assert result["effect"] == "profile_written" and result["status"] == "accepted"
    assert result["written"] == {"id": result["written"]["id"], "category": "current_state", "content": "每天能投入两小时"}
    assert _profile_contents(conn) == ["每天能投入两小时"]
    assert _evidence_rows(conn) == []
    assert conn.execute("SELECT COUNT(*) AS n FROM knowledge_file").fetchone()["n"] == 0


def test_old_payload_rejection_and_duplicate_gate_unchanged(conn):
    """老提案的驳回与判重闸也照旧：驳回留痕、一字不差的重复给 409 且提案保持 pending。"""
    first = _knowledge_proposal(conn, {"category": "long_axis", "content": "以产出为导向学习"})
    proposals.decide(conn, first, approved=True)

    duplicate = _knowledge_proposal(conn, {"category": "long_axis", "content": "以产出为导向学习"})
    with pytest.raises(proposals.ProposalConflict):
        proposals.decide(conn, duplicate, approved=True)
    assert _status_of(conn, duplicate) == "pending"

    rejected = _knowledge_proposal(conn, {"category": "long_axis", "content": "另一条主线"})
    result = proposals.decide(conn, rejected, approved=False, reason="还没想清楚")
    assert result["status"] == "rejected" and _profile_contents(conn) == ["以产出为导向学习"]


# ---------- ② add 与 supersede 各自落对档案 ----------


def test_add_card_writes_archive_with_knowledge_provenance(conn, vault):
    """add 卡批准：档案落 active、出处行带全套 knowledge 列、文件台账登记。"""
    proposal_id = _scan_lands_card(conn, vault, _card())

    result = proposals.decide(conn, proposal_id, approved=True)

    assert result["effect"] == "profile_written" and result["status"] == "accepted"
    item_id = result["written"]["id"]
    assert _profile_contents(conn) == ["晚上十点以后不学习"]

    rows = _evidence_rows(conn)
    assert len(rows) == 1
    evidence = rows[0]
    assert evidence["scope"] == "global" and evidence["memory_id"] == item_id
    assert evidence["source_type"] == "knowledge_file"
    assert evidence["excerpt"] == EXCERPT
    assert evidence["relative_path"] == "a.md"
    assert evidence["content_hash"] == hash_bytes((vault / "a.md").read_bytes())
    assert (evidence["line_start"], evidence["line_end"]) == (2, 2)  # 摘录在第 2 行
    assert evidence["knowledge_file_id"] == evidence["source_id"]  # source_id 挂文件台账

    file_row = conn.execute("SELECT * FROM knowledge_file").fetchone()
    assert file_row is not None
    assert file_row["id"] == evidence["knowledge_file_id"]
    assert file_row["relative_path"] == "a.md"

    shown = memory.evidence_of(conn, "global", item_id)[0]
    assert shown["source_label"] == "知识库文件" and shown["relative_path"] == "a.md"

    # 台账回答得出「这条是谁按哪个提案写进去的」
    event = conn.execute(
        "SELECT * FROM ledger_event WHERE entity_type = 'profile_item' AND entity_id = ? AND change_type = 'create'",
        (item_id,),
    ).fetchone()
    assert f"按提案 #{proposal_id}" in event["reason"]


def test_supersede_card_replaces_target_and_keeps_old_value(conn, vault):
    """supersede 卡批准：新条目 active 带新出处，旧条目 superseded 留痕、自己的出处不动。"""
    old_id = ledger.create_active(
        conn, "profile_item", {"category": "current_state", "content": "最近精力偏低"}, actor="user"
    )
    proposal_id = _scan_lands_card(
        conn, vault, _card(action="supersede", content="最近精力偏低，下午最差", target_profile_id=old_id)
    )

    result = proposals.decide(conn, proposal_id, approved=True)

    new_id = result["written"]["id"]
    assert new_id != old_id
    old_row = conn.execute("SELECT * FROM profile_item WHERE id = ?", (old_id,)).fetchone()
    new_row = conn.execute("SELECT * FROM profile_item WHERE id = ?", (new_id,)).fetchone()
    assert old_row["status"] == "superseded" and old_row["superseded_by"] == new_id
    assert new_row["status"] == "active" and new_row["content"] == "最近精力偏低，下午最差"

    # 取代出来的新条目挂新证据；台账留「为什么改」
    rows = _evidence_rows(conn)
    assert [row["memory_id"] for row in rows] == [new_id]
    assert rows[0]["excerpt"] == EXCERPT
    event = conn.execute(
        "SELECT * FROM ledger_event WHERE entity_type = 'profile_item' AND change_type = 'supersede'"
    ).fetchone()
    assert event["entity_id"] == old_id and "下午最差" in event["after_value"]


def test_supersede_target_must_still_be_active(conn, vault):
    """目标条目已被取代 / 作废之后批准取代卡：409，提案留在待裁定。"""
    old_id = ledger.create_active(
        conn, "profile_item", {"category": "current_state", "content": "最近精力偏低"}, actor="user"
    )
    ledger.void(conn, "profile_item", old_id, reason="记错了", actor="user")
    payload = {
        "action": "supersede",
        "category": "current_state",
        "content": "最近精力偏低，下午最差",
        "why": "笔记里写了",
        "source_kind": "agent_inferred",
        "evidence": [
            {"root_alias": "笔记", "relative_path": "a.md", "excerpt": EXCERPT, "line_start": 2, "line_end": 2}
        ],
        "target_profile_id": old_id,
        "knowledge": True,
    }
    proposal_id = _knowledge_proposal(conn, payload)
    _write(vault / "a.md", NOTE)  # 出处本身对得上，拦它的是目标已不有效

    with pytest.raises(proposals.ProposalConflict):
        proposals.decide(conn, proposal_id, approved=True)
    assert _status_of(conn, proposal_id) == "pending"
    assert _evidence_rows(conn) == []


def test_supersede_with_same_content_or_cross_category_is_refused(conn, vault):
    """取代的新旧内容一模一样＝没发生；跨类别取代＝对错了条。都在批准前拦下。"""
    old_id = ledger.create_active(
        conn, "profile_item", {"category": "current_state", "content": "最近精力偏低"}, actor="user"
    )
    same = _knowledge_proposal(
        conn,
        {
            "action": "supersede", "category": "current_state", "content": "最近精力偏低",
            "why": "x", "source_kind": "agent_inferred",
            "evidence": [{"root_alias": "笔记", "relative_path": "a.md", "excerpt": EXCERPT}],
            "target_profile_id": old_id, "knowledge": True,
        },
    )
    with pytest.raises(proposals.ProposalError, match="一模一样"):
        proposals.decide(conn, same, approved=True)

    cross = _knowledge_proposal(
        conn,
        {
            "action": "supersede", "category": "long_axis", "content": "换成另一条主线",
            "why": "x", "source_kind": "agent_inferred",
            "evidence": [{"root_alias": "笔记", "relative_path": "a.md", "excerpt": EXCERPT}],
            "target_profile_id": old_id, "knowledge": True,
        },
    )
    with pytest.raises(proposals.ProposalError, match="类别"):
        proposals.decide(conn, cross, approved=True)
    # 两次都只在预检拦下：目标条目原封不动
    assert conn.execute("SELECT status FROM profile_item WHERE id = ?", (old_id,)).fetchone()["status"] == "active"


# ---------- ③ uncertain 批准被拒 ----------


def test_uncertain_card_is_refused_with_a_plain_answer(conn, vault):
    """存疑卡不能批准：给人话错误，提案留在待裁定，什么都不写。"""
    proposal_id = _scan_lands_card(
        conn,
        vault,
        _card(
            action="uncertain",
            content="好像在准备一场考试",
            why=None,
            uncertainty_reason="只提了一句，分不清是临时的还是长期状态",
        ),
    )

    with pytest.raises(proposals.ProposalError) as caught:
        proposals.decide(conn, proposal_id, approved=True)
    assert "存疑" in str(caught.value)

    assert _status_of(conn, proposal_id) == "pending"
    assert _profile_contents(conn) == []
    assert _evidence_rows(conn) == []


# ---------- ④ 档案、证据、提案终态同一事务 ----------


def test_approval_rolls_back_as_a_whole_when_any_step_fails(conn, vault, monkeypatch):
    """事务里任何一步失败（这里让提案终态那步炸）＝整体回滚：档案、证据、文件台账都不留。

    手工落提案（不走扫描）：文件台账在批准前是空的，回滚后仍为 0——
    证明 `register_evidence` 的登记也随事务一起回了滚。
    """
    _write(vault / "a.md", NOTE)
    proposal_id = _knowledge_proposal(
        conn,
        {
            "action": "add", "category": "current_state", "content": "晚上十点以后不学习",
            "why": "笔记里反复出现", "source_kind": "agent_inferred",
            "evidence": [{"root_alias": "笔记", "relative_path": "a.md", "excerpt": EXCERPT}],
            "target_profile_id": None, "knowledge": True,
        },
    )

    def boom(*args, **kwargs):
        raise ledger.LedgerError("炸在这一步")

    monkeypatch.setattr(ledger, "set_status", boom)
    with pytest.raises(proposals.ProposalError, match="整体回滚"):
        proposals.decide(conn, proposal_id, approved=True)

    assert _status_of(conn, proposal_id) == "pending"  # 提案留在待裁定，可以再来一次
    assert _profile_contents(conn) == []
    assert _evidence_rows(conn) == []
    assert conn.execute("SELECT COUNT(*) AS n FROM knowledge_file").fetchone()["n"] == 0


def test_approval_succeeds_after_a_rolled_back_attempt(conn, vault, monkeypatch):
    """回滚不留残局：同一张卡重裁一次能干净落库（档案 + 证据 + 终态齐全）。"""
    _write(vault / "a.md", NOTE)
    proposal_id = _knowledge_proposal(
        conn,
        {
            "action": "add", "category": "current_state", "content": "晚上十点以后不学习",
            "why": "笔记里反复出现", "source_kind": "agent_inferred",
            "evidence": [{"root_alias": "笔记", "relative_path": "a.md", "excerpt": EXCERPT}],
            "target_profile_id": None, "knowledge": True,
        },
    )

    real_set_status = ledger.set_status
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ledger.LedgerError("第一次炸")
        return real_set_status(*args, **kwargs)

    monkeypatch.setattr(ledger, "set_status", flaky)
    with pytest.raises(proposals.ProposalError):
        proposals.decide(conn, proposal_id, approved=True)

    result = proposals.decide(conn, proposal_id, approved=True)  # 第二次不再炸
    assert result["status"] == "accepted"
    assert len(_evidence_rows(conn)) == 1
    assert _status_of(conn, proposal_id) == "accepted"


# ---------- ⑤ 批准前文件已改 → 409 ----------


def test_changed_file_conflicts_and_writes_nothing(conn, vault):
    """批准前再验一次：摘录在当前文件里找不到了就 409，绝不按扫描时的旧假设写。"""
    proposal_id = _scan_lands_card(conn, vault, _card())
    _write(vault / "a.md", "# 笔记（已改）\n现在改成晚上十一点睡。\n")

    with pytest.raises(proposals.ProposalConflict, match="找不到了|改过"):
        proposals.decide(conn, proposal_id, approved=True)

    assert _status_of(conn, proposal_id) == "pending"
    assert _profile_contents(conn) == []
    assert _evidence_rows(conn) == []
    # 扫描自己登记的文件台账行还在（那是扫描的账）；批准侧没有新写入
    assert conn.execute("SELECT COUNT(*) AS n FROM knowledge_file").fetchone()["n"] == 1


def test_missing_root_conflicts_instead_of_guessing(conn, vault, monkeypatch):
    """根目录被移出配置：无从核对摘录就当场拒绝，不静默落库。"""
    proposal_id = _scan_lands_card(conn, vault, _card())
    monkeypatch.setenv("CADENCE_KNOWLEDGE_ROOTS", "别的库=" + str(vault))

    with pytest.raises(proposals.ProposalConflict, match="根目录"):
        proposals.decide(conn, proposal_id, approved=True)
    assert _status_of(conn, proposal_id) == "pending"


# ---------- ⑥ 完全重复与近似重复 ----------


def test_exact_duplicate_cannot_be_approved(conn, vault):
    """档案里已有一字不差的当前条目：批准给 409（同判重闸），提案保持 pending。

    （完全重复的卡在扫描层就整张丢了，落不成提案——这里手工落一条来测批准侧自己的闸。）
    """
    ledger.create_active(
        conn, "profile_item", {"category": "current_state", "content": "晚上十点以后不学习"}, actor="user"
    )
    _write(vault / "a.md", NOTE)
    proposal_id = _knowledge_proposal(
        conn,
        {
            "action": "add", "category": "current_state", "content": "晚上十点以后不学习",
            "why": "笔记里反复出现", "source_kind": "agent_inferred",
            "evidence": [{"root_alias": "笔记", "relative_path": "a.md", "excerpt": EXCERPT}],
            "target_profile_id": None, "knowledge": True,
        },
    )

    with pytest.raises(proposals.ProposalConflict, match="已有这条"):
        proposals.decide(conn, proposal_id, approved=True)
    assert _status_of(conn, proposal_id) == "pending"
    assert _evidence_rows(conn) == []


def test_near_duplicate_hint_does_not_block_approval(conn, vault):
    """近似重复只塞 duplicate_hint 提示、不自动合并——人看了提示仍可批准。"""
    ledger.create_active(
        conn, "profile_item", {"category": "current_state", "content": "我晚上十点以后不安排学习"}, actor="user"
    )
    proposal_id = _scan_lands_card(
        conn, vault, _card(content="我晚上十点以后不安排学习了")  # 像（相似度高）但不等于
    )
    assert _payload_of(conn, proposal_id).get("duplicate_hint")

    result = proposals.decide(conn, proposal_id, approved=True)
    assert result["status"] == "accepted"
    assert sorted(_profile_contents(conn)) == ["我晚上十点以后不安排学习", "我晚上十点以后不安排学习了"]


# ---------- ⑦ 彻底删除：清知识库副本，不碰原文件 ----------


def test_purge_clears_knowledge_copies_but_keeps_the_original_file(conn, vault):
    """purge：证据行（含路径/哈希副本）与候选 payload 里的正文、摘录一起清；原文件原样。"""
    proposal_id = _scan_lands_card(conn, vault, _card())
    result = proposals.decide(conn, proposal_id, approved=True)
    item_id = result["written"]["id"]
    original = (vault / "a.md").read_text(encoding="utf-8")

    preview = memory.purge_preview(conn, "global", item_id)
    assert proposal_id in preview["candidate_proposals"]
    assert any(item["source_type"] == "knowledge_file" for item in preview["evidence"])

    purged = memory.purge(conn, "global", item_id, reason="这条不该记")

    assert purged["complete"] is True and purged["leftover"] == []
    assert _evidence_rows(conn) == []
    payload = _payload_of(conn, proposal_id)
    assert payload["purged"] is True
    assert "晚上十点以后不学习" not in json.dumps(payload, ensure_ascii=False)  # 正文与摘录副本都没了
    # 原始文件一个字不动，文件台账行也还在（§7.5：只保留「原文件仍在」的状态信息）
    assert (vault / "a.md").read_text(encoding="utf-8") == original
    assert conn.execute("SELECT COUNT(*) AS n FROM knowledge_file").fetchone()["n"] == 1
    # 台账流水行还在（审计事实），但里面那份正文副本被换掉了
    assert conn.execute("SELECT COUNT(*) AS n FROM ledger_event").fetchone()["n"] > 0


def test_purge_reports_original_file_text_as_none_of_cadences_business(conn, vault):
    """摘录的原句还留在原文件里＝天经地义，不算 cadence 内残留（complete 不因此变假）。"""
    proposal_id = _scan_lands_card(conn, vault, _card())
    result = proposals.decide(conn, proposal_id, approved=True)

    purged = memory.purge(conn, "global", result["written"]["id"], reason="删")

    assert purged["complete"] is True  # 原文件里有同样的句子也照删不误——它不在 cadence 的账上
    assert (vault / "a.md").exists()


def test_purge_only_scrubs_fragments_of_a_near_duplicate_candidate(conn, vault):
    """近似重复的待裁定候选只是「文字里包含」被删正文，不是它的副本——按片段清：
    提案行、状态、动作与其余字段全部保留，只把含被删正文的片段换成「已按用户要求删除」。
    整条 payload 打成空壳会把一条还能看、还能拒的提案变成无法理解的废纸。"""
    _write(vault / "a.md", NOTE)
    make_provider(conn)
    report = kb.scan(
        conn,
        1,
        transport=ScriptedTransport(
            _read_call("a.md"),
            _final(
                _card(),  # 将被批准、随后被彻底删除的那条
                _card(content="晚上十点以后不学习了"),  # 近似重复：内容是被删正文的超集
            ),
        ),
    )
    assert report["status"] == "ok" and report["candidates_created"] == 2
    approved_id, held_id = [
        int(row["id"])
        for row in conn.execute(
            "SELECT id FROM proposal WHERE kind = 'profile_change' ORDER BY id"
        ).fetchall()
    ]
    item_id = proposals.decide(conn, approved_id, approved=True)["written"]["id"]

    purged = memory.purge(conn, "global", item_id, reason="删")

    assert purged["complete"] is True and purged["leftover"] == []
    # 近似重复候选活着：仍是待裁定，骨架完整，只有含被删正文的片段被换掉
    assert _status_of(conn, held_id) == "pending"
    payload = _payload_of(conn, held_id)
    assert payload["action"] == "add" and payload["why"] == "笔记里反复出现"
    assert payload["category"] == "current_state"
    assert payload["content"] == f"{memory.PURGED_TEXT}了"
    assert payload["evidence"][0]["excerpt"] == f"{memory.PURGED_TEXT}。"
    assert payload["purged"] is True


# ---------- ⑧ 批量批准维持现状 ----------


def test_batch_approve_still_only_takes_plain_user_adds(conn, vault):
    """知识库候选（profile_change）混不进批量批准——批量只收「新增 + 用户陈述」的记忆候选。"""
    proposal_id = _scan_lands_card(conn, vault, _card(source_kind="agent_inferred"))

    report = memory.batch_approve(conn, [proposal_id])

    assert report["approved"] == []
    assert report["skipped"][0]["proposal_id"] == proposal_id
    assert _status_of(conn, proposal_id) == "pending"  # 只能逐条


# ---------- ⑨ 出处保留与清理口径（2026-10-05 独立复核整改） ----------


def test_editing_a_profile_item_keeps_its_provenance(conn):
    """在档案页改一句（取代）不该把知识库出处整条丢掉（§5.2 来源可考）。"""
    from app import profile

    item_id = profile.create_item(conn, category="current_state", content="晚上十点以后不学习")
    memory.attach_evidence(
        conn,
        "global",
        item_id,
        [
            {
                "source_type": "knowledge_file",
                "source_id": 0,
                "excerpt": "晚上十点以后不学习。",
                "relative_path": "10-生活/作息.md",
                "content_hash": "abc123",
                "line_start": 3,
                "line_end": 3,
            }
        ],
    )

    new_id = profile.supersede_item(
        conn, item_id, content="晚上十一点以后不学习", reason="作息往后挪了一小时"
    )

    kept = memory.evidence_of(conn, "global", new_id)
    assert len(kept) == 1
    assert kept[0]["relative_path"] == "10-生活/作息.md"
    assert kept[0]["content_hash"] == "abc123"
    assert kept[0]["source_type"] == "knowledge_file"


def test_purge_clears_multiline_excerpts_from_the_ledger_too(conn):
    """多行摘录在台账 JSON 里是 \\n 两个字符：只按原文找会漏，complete 就成了假话。"""
    from app import profile

    excerpt = "第一条\n第二条"
    item_id = profile.create_item(conn, category="current_state", content="多行摘录的档案条目")
    memory.attach_evidence(
        conn,
        "global",
        item_id,
        [
            {
                "source_type": "knowledge_file",
                "source_id": 0,
                "excerpt": excerpt,
                "relative_path": "a.md",
                "content_hash": "h",
                "line_start": 1,
                "line_end": 2,
            }
        ],
    )
    # 同一段摘录经台账写进一条提案：`create` 的 after_value 是整段 payload JSON，副本在里面
    ledger.create_active(
        conn,
        "proposal",
        {
            "kind": "profile_change",
            "payload": json.dumps(
                {
                    "action": "add",
                    "category": "current_state",
                    "content": "另一条",
                    "evidence": [{"excerpt": excerpt}],
                },
                ensure_ascii=False,
            ),
            "reason": "测试用",
        },
        actor="agent",
    )

    report = memory.purge(conn, "global", item_id, reason="删")

    assert report["complete"] is True
    assert report["leftover"] == []
    blob = json.dumps(
        [row["after_value"] for row in conn.execute("SELECT after_value FROM ledger_event")],
        ensure_ascii=False,
    )
    assert "第二条" not in blob


def test_supersede_with_a_non_numeric_target_is_a_business_error(conn):
    """手改 / 损坏的 payload 给了非数字编号：给 400 人话，不是没接住的 ValueError（500）。"""
    proposal_id = ledger.create_active(
        conn,
        "proposal",
        {
            "kind": "profile_change",
            "payload": json.dumps(
                {
                    "action": "supersede",
                    "category": "current_state",
                    "content": "新句子",
                    "why": "x",
                    "target_profile_id": "not-an-int",
                },
                ensure_ascii=False,
            ),
            "reason": "测试用",
        },
        actor="agent",
    )

    with pytest.raises(proposals.ProposalError):
        proposals.decide(conn, proposal_id, approved=True)
