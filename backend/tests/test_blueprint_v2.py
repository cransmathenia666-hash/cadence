"""蓝图 v2（成果闭环 OC-06）：payload 携带成果契约 + 确定性校验 + 生成/会话流转。

方案见 `docs/ideas/成果闭环重构开发方案.md` §3.5 / §4.2 / §4.3.1 / §6.2，覆盖：

① v2 形状：standard 与 enhanced 输出同一 v2 形状——payload 带 version=2、contract、
   stages（每阶段 purpose / why_now / deliverable / acceptance_criteria /
   evidence_requirements / contract_criterion_ids / tasks）；生成成功才落 pending 提案
   并把会话置为 blueprint_pending；
② 不合格矩阵：契约缺字段、条数越界、重复 id、非法证据类型、阶段标题空白、阶段没有
   验收条件、条件文本空白、证据类型非法、承接了契约里不存在的条件——全部判不合格、
   不落提案、会话保持 active；契约走 `contract.validate` 同一套标准，不另造口径；
③ 契约未覆盖：必需条件没有被任何阶段承接 → 不产出可批准的提案；
④ 稳定 id：服务端补齐（stage-N-cK / stage-N-eK），模型给的重复 id 判不合格；
⑤ 增强模式：两位独立审查员行为（E-71）原样保留——审查失败不落提案、修订稿与初稿
   过同一套 v2 校验且契约原样带回。

一律用假上游打桩、临时库；blueprint_pending 禁普通发言与退回入口的路由级用例在
`test_planning_session.py`，v1 批准拒绝与 v2 批准等待原子批准的用例在 `test_blueprint.py`。
"""

from __future__ import annotations

import json

import pytest

from app import advisor, blueprint, contract, db, ledger, llm, plan


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


def review_json(*points: dict, summary: str = "计划与已知信息相符") -> str:
    return json.dumps({"summary": summary, "points": list(points)}, ensure_ascii=False)


def contract_json(**over) -> dict:
    """一份能通过 contract.validate 的契约底稿（oc-1 必需、oc-2 非必需）。"""
    data = {
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
    data.update(over)
    return data


def stage(title: str, **over) -> dict:
    """一个合格阶段底稿；`over` 覆盖任意字段便于造坏数据。"""
    data = {
        "title": title,
        "purpose": "为最终成果解决基础问题",
        "why_now": "它是后续一切的基础",
        "deliverable": "交一个能跑通的小东西",
        "acceptance_criteria": [{"text": "交出该阶段可验收的结果", "required": True}],
        "evidence_requirements": [{"kind": "repository", "required": False, "description": "该阶段的产出"}],
        "contract_criterion_ids": ["oc-1"],
        "tasks": [{"title": "读 MDN"}],
    }
    data.update(over)
    return data


def blueprint_json(*stages: dict, goal: str = "能自己写后端接口", contract: dict | None = None) -> str:
    return json.dumps(
        {"goal": goal, "contract": contract or contract_json(), "stages": list(stages)},
        ensure_ascii=False,
    )


def adopted_candidate(conn, title: str = "学 HTTP") -> tuple[int, int, int]:
    """已采纳候选 + 活着的规划会话。返回 (candidate_id, plan_id, session_id)。"""
    plan_id = ledger.create_active(conn, "plan", {"goal": f"计划：{title}"}, actor="user")
    request_id = advisor.record_request(conn, "search", "我不知道该学什么", plan_id)
    candidate_id = ledger.create_active(
        conn,
        "candidate",
        {"request_id": request_id, "title": title, "why": "对主线有直接帮助", "depth_target": "够用", "rank": 1},
        actor="agent",
        reason="测试用",
    )
    decided = advisor.decide_candidate(conn, candidate_id, accept=True)
    session_id = int(decided["planning_session_id"])
    return candidate_id, plan_id, session_id


def ready_thread(conn, title: str = "学 HTTP") -> tuple[int, int, int, str]:
    """一条聊成了一轮的会话，返回 (candidate_id, plan_id, session_id, 那句 ready 回话)。"""
    make_provider(conn)
    add_profile(conn)
    candidate_id, plan_id, session_id = adopted_candidate(conn, title)
    reply = chat_reply([], ready=True)
    blueprint.say(
        conn, candidate_id, "我想先把 HTTP 弄明白",
        planning_session_id=session_id, transport=ScriptedTransport(reply),
    )
    return candidate_id, plan_id, session_id, reply


def pending_count(conn) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM proposal WHERE kind = ? AND status = 'pending'",
            (blueprint.BLUEPRINT_KIND,),
        ).fetchone()["n"]
    )


def pending_payload(conn) -> dict:
    row = conn.execute(
        "SELECT payload FROM proposal WHERE kind = ? AND status = 'pending' ORDER BY id DESC",
        (blueprint.BLUEPRINT_KIND,),
    ).fetchone()
    assert row is not None
    return json.loads(row["payload"])


# ---------- ① v2 形状：standard 与 enhanced 同形状 ----------

def test_standard_generation_lands_a_v2_payload_and_pends_the_session(conn):
    transport = ScriptedTransport(chat_reply([], ready=True), blueprint_json(stage("学 HTTP")))
    candidate_id, plan_id, session_id, _ = ready_thread(conn)
    _ = plan_id  # 落点由会话解析

    created = blueprint.generate_blueprint(
        conn, candidate_id, planning_session_id=session_id, transport=transport
    )

    assert created["payload_version"] == 2
    payload = pending_payload(conn)
    assert payload["version"] == 2
    assert payload["contract"]["title"] == "做出能演示的后端小项目"
    assert payload["contract"]["acceptance_criteria"][0]["id"] == "oc-1"
    assert payload["planning_session_id"] == session_id
    stage_out = payload["stages"][0]
    for key in (
        "title", "purpose", "why_now", "deliverable", "acceptance_criteria",
        "evidence_requirements", "contract_criterion_ids", "tasks",
    ):
        assert key in stage_out
    assert stage_out["contract_criterion_ids"] == ["oc-1"]
    assert stage_out["acceptance_criteria"]
    # 生成成功才置 blueprint_pending
    row = conn.execute(
        "SELECT status FROM planning_session WHERE id = ?", (session_id,)
    ).fetchone()["status"]
    assert row == "blueprint_pending"


def test_enhanced_mode_outputs_the_same_v2_shape(conn):
    transport = ScriptedTransport(
        blueprint_json(stage("学 HTTP")),
        review_json(summary="水平判断有档案依据"),
        review_json(summary="契约承接与阶段衔接站得住"),
    )
    candidate_id, _, session_id, _ = ready_thread(conn)

    created = blueprint.generate_blueprint(
        conn, candidate_id, planning_session_id=session_id, mode="enhanced", transport=transport
    )

    payload = pending_payload(conn)
    assert payload["version"] == 2 and payload["mode"] == "enhanced"
    # standard 与 enhanced 的 payload 骨架一致：契约 + 阶段树同形状
    for key in ("goal", "contract", "stages", "planning_session_id"):
        assert key in payload
    assert payload["stages"][0]["acceptance_criteria"]
    assert payload["stages"][0]["contract_criterion_ids"] == ["oc-1"]
    assert created["review"]["mode"] == "enhanced"
    assert [reviewer["key"] for reviewer in created["review"]["reviewers"]] == ["level", "structure"]


def test_generation_failure_keeps_the_session_active(conn):
    """模型两次都不合格 → 不落提案，会话保持 active。"""
    bad = json.dumps({"goal": "目标", "stages": []}, ensure_ascii=False)  # 没有契约、没有阶段
    transport = ScriptedTransport(chat_reply([], ready=True), bad, bad)
    candidate_id, _, session_id, _ = ready_thread(conn)

    with pytest.raises(blueprint.BlueprintError):
        blueprint.generate_blueprint(
            conn, candidate_id, planning_session_id=session_id, transport=transport
        )

    assert pending_count(conn) == 0
    row = conn.execute(
        "SELECT status FROM planning_session WHERE id = ?", (session_id,)
    ).fetchone()["status"]
    assert row == "active"


# ---------- ② 不合格矩阵（契约复用 contract.validate，不另造标准） ----------

@pytest.mark.parametrize("bad_contract", [
    contract_json(value=""),                     # 缺价值
    contract_json(success_statement="  "),       # 成功定义是空白
    contract_json(acceptance_criteria=[{"text": "只有一条", "required": True}]),  # 条数越界
    contract_json(acceptance_criteria=[
        {"id": "oc-1", "text": "能跑", "required": True},
        {"id": "oc-1", "text": "能演示", "required": True},
    ]),                                          # 重复 id
    contract_json(evidence_requirements=[{"kind": "bilibili", "required": True}]),  # 非法证据类型
])
def test_invalid_contracts_are_rejected_with_no_proposal(conn, bad_contract):
    transport = ScriptedTransport(
        blueprint_json(stage("学 HTTP"), contract=bad_contract),
        blueprint_json(stage("学 HTTP"), contract=bad_contract),
    )
    candidate_id, _, session_id, _ = ready_thread(conn)

    with pytest.raises(blueprint.BlueprintError):
        blueprint.generate_blueprint(
            conn, candidate_id, planning_session_id=session_id, transport=transport
        )

    assert pending_count(conn) == 0
    assert conn.execute(
        "SELECT status FROM planning_session WHERE id = ?", (session_id,)
    ).fetchone()["status"] == "active"


@pytest.mark.parametrize("bad_stage", [
    stage("   "),                                    # 阶段标题空白
    stage("学 HTTP", acceptance_criteria=[]),        # 阶段没有验收条件
    stage("学 HTTP", acceptance_criteria=[{"text": "  ", "required": True}]),  # 条件文本空白
    stage("学 HTTP", evidence_requirements=[{"kind": "torrent", "required": True}]),  # 证据类型非法
    stage("学 HTTP", contract_criterion_ids=["oc-99"]),  # 承接了契约里不存在的条件
    stage("学 HTTP", tasks=[{"title": "读 MDN"}, {"title": "读 MDN"}]),   # 同阶段任务重名
    stage("学 HTTP", tasks=[{"title": "读 MDN", "due_date": "下周三"}]),  # 假日期
])
def test_invalid_stages_are_rejected_with_no_proposal(conn, bad_stage):
    transport = ScriptedTransport(
        blueprint_json(bad_stage),
        blueprint_json(bad_stage),
    )
    candidate_id, _, session_id, _ = ready_thread(conn)

    with pytest.raises(blueprint.BlueprintError):
        blueprint.generate_blueprint(
            conn, candidate_id, planning_session_id=session_id, transport=transport
        )

    assert pending_count(conn) == 0
    assert conn.execute(
        "SELECT status FROM planning_session WHERE id = ?", (session_id,)
    ).fetchone()["status"] == "active"


def test_one_retry_turns_a_bad_draft_good(conn):
    """第一次不合格（必需条件未承接）带原因重试，第二次合格才落提案。"""
    uncovered = stage("学 HTTP", contract_criterion_ids=[])
    good = blueprint_json(stage("学 HTTP"))
    transport = ScriptedTransport(
        blueprint_json(uncovered),
        good,
    )
    candidate_id, _, session_id, _ = ready_thread(conn)

    created = blueprint.generate_blueprint(
        conn, candidate_id, planning_session_id=session_id, transport=transport
    )

    assert created["attempts"] == 2
    assert pending_count(conn) == 1
    # 重试原因点名了未承接的必需条件（seen[0] 是初稿、seen[1] 是带原因的重试）
    hint = transport.seen[1]["payload"]["messages"][-1]["content"]
    assert "oc-1" in hint and "承接" in hint


# ---------- ③ 契约未覆盖：必需条件必须被承接 ----------

def test_uncovered_required_contract_condition_is_rejected(conn):
    """两条必需条件只有一条被承接 → 判不合格，带原因重试一次后仍不合格就不落提案。"""
    strict = contract_json(acceptance_criteria=[
        {"id": "oc-1", "text": "能运行并演示一个含错误处理的接口", "required": True},
        {"id": "oc-2", "text": "有自动化测试", "required": True},
    ])
    transport = ScriptedTransport(
        blueprint_json(stage("学 HTTP", contract_criterion_ids=["oc-1"]), contract=strict),
        blueprint_json(stage("学 HTTP", contract_criterion_ids=["oc-1"]), contract=strict),
    )
    candidate_id, _, session_id, _ = ready_thread(conn)

    with pytest.raises(blueprint.BlueprintError) as failed:
        blueprint.generate_blueprint(
            conn, candidate_id, planning_session_id=session_id, transport=transport
        )
    assert "oc-2" in str(failed.value)

    assert pending_count(conn) == 0


def test_covered_required_condition_passes_and_optional_one_may_be_dropped(conn):
    """必需条件被承接即合格；非必需条件没被承接不拦。"""
    strict = contract_json(acceptance_criteria=[
        {"id": "oc-1", "text": "能运行并演示一个含错误处理的接口", "required": True},
        {"id": "oc-2", "text": "有自动化测试", "required": False},
    ])
    transport = ScriptedTransport(
        blueprint_json(stage("学 HTTP", contract_criterion_ids=["oc-1"]), contract=strict),
    )
    candidate_id, _, session_id, _ = ready_thread(conn)

    created = blueprint.generate_blueprint(
        conn, candidate_id, planning_session_id=session_id, transport=transport
    )

    assert created["attempts"] == 1
    assert pending_count(conn) == 1


# ---------- ④ 稳定 id：服务端补齐，重复 id 判不合格 ----------

def test_stage_ids_are_assigned_by_the_server(conn):
    """模型不给条件 id 时由服务端按「stage-N-cK / stage-N-eK」补齐。"""
    no_ids = stage(
        "学 HTTP",
        acceptance_criteria=[{"text": "接口能跑通", "required": True}, {"text": "有错误处理", "required": False}],
        evidence_requirements=[{"kind": "repository", "required": False, "description": "仓库"}],
    )
    transport = ScriptedTransport(chat_reply([], ready=True), blueprint_json(no_ids))
    candidate_id, _, _, _ = ready_thread(conn)

    created = blueprint.generate_blueprint(conn, candidate_id, transport=transport)

    criteria = created["stages"][0]["acceptance_criteria"]
    assert [item["id"] for item in criteria] == ["stage-1-c1", "stage-1-c2"]
    assert created["stages"][0]["evidence_requirements"][0]["id"] == "stage-1-e1"
    # 契约条件 id 保留模型给的（contract.validate 验过唯一性），阶段承接引用合法
    assert created["contract"]["acceptance_criteria"][0]["id"] == "oc-1"


def test_duplicate_stage_criterion_ids_are_rejected(conn):
    duplicated = stage(
        "学 HTTP",
        acceptance_criteria=[
            {"id": "c1", "text": "接口能跑通", "required": True},
            {"id": "c1", "text": "有错误处理", "required": False},
        ],
    )
    transport = ScriptedTransport(
        chat_reply([], ready=True), blueprint_json(duplicated), blueprint_json(duplicated)
    )
    candidate_id, _, _, _ = ready_thread(conn)

    with pytest.raises(blueprint.BlueprintError):
        blueprint.generate_blueprint(conn, candidate_id, transport=transport)

    assert pending_count(conn) == 0


# ---------- ⑤ 增强模式：审查员行为保留，审查失败不落提案 ----------

def test_enhanced_review_failure_degrades_and_still_lands(conn):
    """审查员连重试都没产出可用结论：该席降级弃权、初稿照常落提案（不再整稿不落）。"""
    transport = ScriptedTransport(
        blueprint_json(stage("学 HTTP")),
        "这不是 JSON",  # 水平核对员输出不合格
        "仍然不是 JSON",  # 带原因重试仍不合格 → 弃权，不再 fail-closed
        review_json(summary="阶段衔接没有问题"),  # 结构审查员照常审
    )
    candidate_id, _, session_id, _ = ready_thread(conn)

    created = blueprint.generate_blueprint(
        conn, candidate_id, planning_session_id=session_id, mode="enhanced", transport=transport
    )

    assert pending_count(conn) == 1
    payload = json.loads(
        conn.execute("SELECT payload FROM proposal WHERE id = ?", (created["proposal_id"],)).fetchone()["payload"]
    )
    level = payload["review"]["reviewers"][0]
    assert level["degraded"] is True and level["points"] == []
    # 初稿未经这一席复核，也未经修订：review.initial 保持 None，会话进入 blueprint_pending
    assert payload["review"]["initial"] is None
    assert conn.execute(
        "SELECT status FROM planning_session WHERE id = ?", (session_id,)
    ).fetchone()["status"] == "blueprint_pending"


def test_enhanced_revision_reruns_v2_validation_and_keeps_the_contract(conn):
    """修订稿与初稿过同一套 v2 校验；修订把必需条件的承接删掉 → 判不合格不落提案。"""
    revised_but_uncovered = json.dumps(
        {
            "goal": "能自己写后端接口",
            "contract": contract_json(),
            "stages": [stage("学 HTTP", contract_criterion_ids=[])],  # 修订丢了承接
            "review_resolution": [
                {"reviewer_key": "level", "point_index": 0, "resolution": "改成项目练习"}
            ],
        },
        ensure_ascii=False,
    )
    agree = {"stance": "agree", "target": "第 1 阶段", "point": "起点与档案相符", "reason": "档案有依据"}
    disagree = {
        "stance": "disagree", "target": "第 1 阶段", "point": "任务太粗",
        "reason": "拆分过粗", "adjustment": "把任务拆细", "severity": "revise",
    }
    transport = ScriptedTransport(
        blueprint_json(stage("学 HTTP")),
        review_json(agree, summary="水平判断有档案依据"),
        review_json(disagree, summary="任务粒度需要修订"),
        revised_but_uncovered,
    )
    candidate_id, _, session_id, _ = ready_thread(conn)

    with pytest.raises(blueprint.BlueprintError):
        blueprint.generate_blueprint(
            conn, candidate_id, planning_session_id=session_id, mode="enhanced", transport=transport
        )

    assert pending_count(conn) == 0
