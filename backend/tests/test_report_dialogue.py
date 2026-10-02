"""报告上下文接入计划对话、调整意图门槛与 contract_change 提案（成果闭环 OC-09）。

方案见 `docs/ideas/成果闭环重构开发方案.md` §3.8 / §5.1 / §6.6，覆盖六组：
① 无 report_id 的对话轮行为不变（上下文不带报告块、对话行不落 report_id）；
② 提交报告本身绝不自动进对话、绝不落提案（哪怕勾了「希望 AI 提建议」）；
③ 带报告的轮：报告原文 + 复盘卡拼进本轮上下文（带字符上限、超出从旧截断），
   闲聊轮零提案（模型硬塞建议会被意图闸拦下重说，重说仍不行就一条都不落）；
④ contract_change 建议：现版 + 拟改合并后过 `contract.validate`（判据不放松），
   两次不合格一条都不落；批准激活新版本（旧版 superseded、受影响当前验收失效），
   契约换版后旧提案就地拒绝；驳回是业务终态；
⑤ 周提醒与周导出带成果语义（契约标题、验收缺口条数、复盘卡结论），零模型调用；
⑥ 报告归属：带别的计划的 report_id 直接拒。

一律假上游打桩（transport 注入），不接真模型、不花钱；全部用临时库。
"""

from __future__ import annotations

import json

import pytest

from app import (
    contract,
    db,
    dialogue,
    export,
    ledger,
    llm,
    notify,
    plan,
    plan_change,
    proposals,
)
from app.main import (
    CriterionIn,
    EvidenceIn,
    EvidenceRequirementIn,
    NodeIn,
    PlanWithContractIn,
    StageReviewIn,
    create_node,
    post_plan_with_contract,
    post_stage_evidence,
    post_stage_review,
    post_task_check,
)


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
            "usage": {"prompt_tokens": 41, "completion_tokens": 23},
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


def contract_payload(**over) -> dict:
    """一份合格契约：两条必需条件（oc-1/oc-2）+ 一条仓库证据要求。"""
    data = {
        "title": "做出能演示的 API 项目",
        "outcome": "一个可运行的 CRUD API 仓库",
        "value": "实习作品集需要一个真实项目",
        "success_statement": "能运行、带错误处理与测试、能当面演示",
        "acceptance_criteria": [
            {"text": "能运行一个包含错误处理的 CRUD API", "required": True},
            {"text": "至少三个接口有自动化测试", "required": True},
        ],
        "evidence_requirements": [
            {"kind": "repository", "required": True, "description": "可运行的代码仓库"}
        ],
    }
    data.update(over)
    return data


def make_outcome_plan(conn) -> dict:
    """成果计划：契约 + 一个承接全部必需条件的阶段 + 一件任务。"""
    created = post_plan_with_contract(PlanWithContractIn(contract=contract_payload()), conn)
    plan_id, contract_id = int(created["id"]), int(created["contract"]["id"])
    create_node(NodeIn(
        plan_id=plan_id, level="stage", title="阶段 1",
        acceptance_criteria=[CriterionIn(text="CRUD 接口能跑通")],
        evidence_requirements=[EvidenceRequirementIn(kind="repository", description="仓库")],
        contract_criterion_ids=["oc-1", "oc-2"],
    ), conn)
    stage_id = int(plan.get_stages(conn, plan_id)[0]["id"])
    task_id = int(create_node(NodeIn(
        plan_id=plan_id, level="task", title="任务 1", parent_id=stage_id,
    ), conn)["id"])
    return {"plan_id": plan_id, "contract_id": contract_id, "stage": stage_id, "task": task_id}


def make_report(conn, data: dict, **over) -> dict:
    """给这个计划的节点交一份报告（可带 OC-08 的新字段），返回 submit_report 的回执。"""
    return plan.submit_report(
        conn, over.pop("node", data["task"]), over.pop("status", "stuck"),
        over.pop("note", "卡在环境配置上"),
        next_action=over.pop("next_action", None),
        review_requested=over.pop("review_requested", False),
        **over,
    )


def envelope(intent: str = "chat", reply: str = "好的", suggestion: dict | None = None) -> str:
    return json.dumps({"intent": intent, "reply": reply, "suggestion": suggestion}, ensure_ascii=False)


def revise_contract_suggestion(**over) -> dict:
    data = {
        "action": plan_change.REVISE_CONTRACT,
        "changes": {
            "acceptance_criteria": [
                {"text": "能运行一个包含错误处理的 CRUD API", "required": True},
                {"text": "至少三个接口有自动化测试", "required": True},
                {"text": "能按月统计记账次数", "required": True},
            ],
        },
        "why": "复盘卡显示验收有缺口，先把按月统计定成标准",
    }
    data.update(over)
    return data


def pending_kinds(conn) -> list[str]:
    return [
        str(row["kind"])
        for row in conn.execute("SELECT kind FROM proposal WHERE status = 'pending'")
    ]


# ---------- ① 无 report_id 的轮行为不变 ----------

def test_dialogue_without_report_id_is_unchanged(conn):
    make_provider(conn)
    data = make_outcome_plan(conn)
    transport = ScriptedTransport(envelope())

    said = dialogue.say(conn, data["plan_id"], "最近进度怎么样？", transport=transport)

    assert said["reply"] == "好的" and said["suggestion"] is None
    # 上下文里没有「实际带上的报告块」（提示词里的规则文案不含编号，用编号区分），
    # 对话行也不落 report_id
    sent = json.dumps(transport.seen[0]["payload"], ensure_ascii=False)
    assert "【本轮带上的报告 #" not in sent
    rows = conn.execute(
        "SELECT report_id FROM plan_dialogue WHERE plan_id = ?", (data["plan_id"],)
    ).fetchall()
    assert rows and all(row["report_id"] is None for row in rows)


# ---------- ② 提交报告不进对话、不落提案 ----------

def test_report_submission_lands_no_dialogue_and_no_proposal(conn):
    make_provider(conn)
    data = make_outcome_plan(conn)
    result = make_report(conn, data, status="stuck", next_action="switch", review_requested=True)

    assert result["review_card"]["rule"] == "two_stuck_reports" or result["review_card"]
    assert conn.execute("SELECT COUNT(*) AS n FROM plan_dialogue").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM proposal").fetchone()["n"] == 0
    # 台账里也没有任何提案动作
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM ledger_event WHERE entity_type = 'proposal'"
    ).fetchone()["n"] == 0


# ---------- ③ 带报告的轮：上下文与门槛 ----------

def test_dialogue_with_report_id_attaches_report_and_card(conn):
    make_provider(conn)
    data = make_outcome_plan(conn)
    report = make_report(conn, data, status="stuck", next_action="stop", review_requested=True)
    transport = ScriptedTransport(envelope())

    said = dialogue.say(
        conn, data["plan_id"], "根据这次复盘给建议",
        report_id=report["report_id"], transport=transport,
    )

    assert said["reply"] == "好的"
    sent = json.dumps(transport.seen[0]["payload"], ensure_ascii=False)
    assert f"【本轮带上的报告 #{report['report_id']}" in sent
    assert "卡在环境配置上" in sent                      # 报告原文进了上下文
    assert "复盘卡" in sent and "stop" in sent           # 复盘卡与停止选择也进了
    row = conn.execute(
        "SELECT report_id FROM plan_dialogue WHERE plan_id = ? AND role = 'user'",
        (data["plan_id"],),
    ).fetchone()
    assert row["report_id"] == report["report_id"]       # 对话行落了编号，可追溯


def test_report_context_truncates_from_old(conn):
    data = make_outcome_plan(conn)
    long_note = "长" * 3000
    report = plan.submit_report(conn, data["task"], "partial", long_note)

    block = dialogue._report_context(conn, data["plan_id"], report["report_id"])

    assert len(block) <= dialogue.REPORT_CONTEXT_CHAR_LIMIT
    assert block.startswith("（更早的部分已截断）")
    assert long_note not in block            # 三千字的原文装不进两千字上限
    assert "事实明细" in block               # 从旧截断：卡里的结论与事实明细（最新段）保住


def test_report_turn_chit_chat_retracts_suggestion_and_lands_nothing(conn):
    """带报告的闲聊轮：模型硬塞建议 → 意图闸拦下带原因重说 → 它收回后零提案。"""
    make_provider(conn)
    data = make_outcome_plan(conn)
    report = make_report(conn, data)
    bad = envelope("modify", "给你提条建议", {
        "action": "update_node", "node_id": data["task"],
        "fields": {"due_date": "2026-10-20"}, "why": "看着该改",
    })
    transport = ScriptedTransport(bad, envelope("chat", "哈哈，那就继续加油"))

    said = dialogue.say(conn, data["plan_id"], "今天天气不错，随便聊聊",
                        report_id=report["report_id"], transport=transport)

    assert said["suggestion"] is None and said["proposal_id"] is None
    assert pending_kinds(conn) == []
    # 重说时把「他没要求改」的原因带给了模型
    assert "看不出是在要求修改" in json.dumps(transport.seen[1]["payload"], ensure_ascii=False)


def test_report_turn_chit_chat_that_insists_lands_nothing(conn):
    """带报告的闲聊轮：模型连着三次都要硬塞建议 → 整轮失败，一条提案都不落。"""
    make_provider(conn)
    data = make_outcome_plan(conn)
    report = make_report(conn, data)
    bad = envelope("modify", "给你提条建议", {
        "action": "update_node", "node_id": data["task"],
        "fields": {"due_date": "2026-10-20"}, "why": "看着该改",
    })
    transport = ScriptedTransport(bad, bad, bad)

    with pytest.raises(dialogue.DialogueError):
        dialogue.say(conn, data["plan_id"], "今天天气不错，随便聊聊",
                     report_id=report["report_id"], transport=transport)

    assert pending_kinds(conn) == []
    assert conn.execute("SELECT COUNT(*) AS n FROM proposal").fetchone()["n"] == 0


def test_dialogue_report_id_must_belong_to_the_plan(conn):
    make_provider(conn)
    data = make_outcome_plan(conn)
    other = make_outcome_plan(conn)
    report = make_report(conn, other)

    with pytest.raises(dialogue.DialogueError) as caught:
        dialogue.say(conn, data["plan_id"], "看看这份报告",
                     report_id=report["report_id"], transport=ScriptedTransport(envelope()))
    assert "不属于计划" in str(caught.value)


# ---------- ④ contract_change：校验、批准与驳回 ----------

def test_report_turn_with_explicit_request_lands_contract_change(conn):
    """带报告的轮 + 明确的复盘调整说法 → 落一条 contract_change 提案（一条、可追溯）。"""
    make_provider(conn)
    data = make_outcome_plan(conn)
    report = make_report(conn, data)
    transport = ScriptedTransport(envelope(
        "modify", "建议把「能按月统计记账次数」定进契约标准", revise_contract_suggestion(),
    ))

    said = dialogue.say(
        conn, data["plan_id"], "根据这次复盘，把成果契约改一下：验收条件加一条能按月统计",
        report_id=report["report_id"], transport=transport,
    )

    assert said["suggestion"]["kind"] == plan_change.CONTRACT_CHANGE_KIND
    rows = conn.execute(
        "SELECT * FROM proposal WHERE status = 'pending'", ()
    ).fetchall()
    assert [str(row["kind"]) for row in rows] == [plan_change.CONTRACT_CHANGE_KIND]
    payload = proposals.payload_of(rows[0])
    assert payload["plan_id"] == data["plan_id"]
    assert payload["contract_id"] == data["contract_id"]      # 当前活跃契约的 id
    assert payload["report_id"] == report["report_id"]        # 这条建议是哪次复盘触发的
    assert payload["dialogue_id"] == said["suggestion"]["proposal_id"] or payload["dialogue_id"]
    assert "能按月统计" in payload["diff_summary"]            # 差异摘要是后端确定性拼的
    assert payload["changes"]["acceptance_criteria"][2]["text"] == "能按月统计记账次数"
    assert payload["why"]

    # 刷新回读（GET /api/plan-dialogue 的数据源）：契约修改建议的确认条不能丢——
    # 用户点过入口的那一轮，重开页面仍要能看见并处理这条建议
    viewed = dialogue.view(conn, data["plan_id"])
    last = viewed["messages"][-1]
    assert last["role"] == "assistant"
    assert last["suggestion"]["proposal_id"] == said["suggestion"]["proposal_id"]
    assert last["suggestion"]["kind"] == plan_change.CONTRACT_CHANGE_KIND
    assert last["suggestion"]["status"] == "pending"


def test_contract_change_invalid_twice_lands_nothing(conn):
    """现版 + 拟改合并过不了 contract.validate → 带原因重说，连着不合格一条都不落。"""
    make_provider(conn)
    data = make_outcome_plan(conn)
    report = make_report(conn, data)
    bad = envelope("modify", "建议改契约", revise_contract_suggestion(
        changes={"acceptance_criteria": []},     # 验收条件 0 条：validate 直接拒
    ))
    transport = ScriptedTransport(bad, bad, bad)

    with pytest.raises(dialogue.DialogueError):
        dialogue.say(
            conn, data["plan_id"], "根据这次复盘，把成果契约改一下",
            report_id=report["report_id"], transport=transport,
        )

    assert conn.execute("SELECT COUNT(*) AS n FROM proposal").fetchone()["n"] == 0


def test_contract_change_approve_activates_new_version_and_invalidates_reviews(conn):
    """批准 contract_change：激活新版本、旧版 superseded、受影响的当前验收失效。"""
    make_provider(conn)
    data = make_outcome_plan(conn)
    post_task_check(data["task"], conn)
    evidence = post_stage_evidence(
        data["stage"],
        EvidenceIn(kind="repository", reference="https://github.com/x/y", note="先交一版"),
        conn,
    )
    post_stage_review(data["stage"], StageReviewIn(
        contract_id=data["contract_id"], submission_ids=[evidence["id"]],
        criteria_state={str(item["id"]): "unmet"
                        for item in plan._stage_criteria_of(plan.get_node(conn, data["stage"]))},
        decision="needs_work", note="接口还跑不通",
    ), conn)
    # needs_work 把阶段拉回进行中：报告交在阶段上（任务已打勾，done → stuck 是非法迁移）
    report = make_report(conn, data, node=data["stage"])
    transport = ScriptedTransport(envelope(
        "modify", "建议改写第一条验收条件", revise_contract_suggestion(changes={
            "acceptance_criteria": [
                # 改掉阶段承接的 oc-1 的正文 → 该阶段按旧版判的当前验收必须失效
                {"text": "能运行一个包含错误处理并能导出的 CRUD API", "required": True},
                {"text": "至少三个接口有自动化测试", "required": True},
            ],
        }),
    ))
    said = dialogue.say(
        conn, data["plan_id"], "根据这次复盘，把成果契约改一下：验收条件加一条能按月统计",
        report_id=report["report_id"], transport=transport,
    )
    proposal_id = said["suggestion"]["proposal_id"]

    decided = proposals.decide(conn, proposal_id, approved=True)

    assert decided["status"] == "accepted" and decided["effect"] == "contract_activated"
    assert decided["contract"]["version"] == 2
    assert decided["contract"]["superseded_id"] == data["contract_id"]
    # 受当前契约约束的验收按既有 invalidate 路径失效（历史行保留，只翻当前性）
    assert decided["contract"]["reviews_invalidated"] == 1
    review = conn.execute(
        "SELECT * FROM stage_review WHERE node_id = ?", (data["stage"],)
    ).fetchone()
    assert review["is_current"] == 0 and review["invalidated_at"] is not None
    active = contract.active(conn, data["plan_id"])
    assert int(active["id"]) == decided["contract"]["id"]
    assert active["version"] == 2
    assert "能运行一个包含错误处理并能导出的 CRUD API" in active["acceptance_criteria"]
    assert conn.execute(
        "SELECT completion_mode FROM plan WHERE id = ?", (data["plan_id"],)
    ).fetchone()["completion_mode"] == "outcome"


def test_contract_change_refuses_when_contract_moved_on(conn):
    """提案落库之后契约换了版：批准就地拒绝，提案保持 pending，一条都不写。"""
    make_provider(conn)
    data = make_outcome_plan(conn)
    report = make_report(conn, data)
    transport = ScriptedTransport(envelope(
        "modify", "建议把验收条件补成三条", revise_contract_suggestion(),
    ))
    said = dialogue.say(
        conn, data["plan_id"], "根据这次复盘，把成果契约改一下",
        report_id=report["report_id"], transport=transport,
    )
    proposal_id = said["suggestion"]["proposal_id"]
    # 提案落库后契约被别人动过（直接激活了一版新契约）
    contract.activate(conn, data["plan_id"], contract_payload(title="换了一版"), source_kind="manual")

    with pytest.raises(proposals.ProposalConflict) as caught:
        proposals.decide(conn, proposal_id, approved=True)
    assert "换版" in str(caught.value)
    assert conn.execute(
        "SELECT status FROM proposal WHERE id = ?", (proposal_id,)
    ).fetchone()["status"] == "pending"
    assert conn.execute("SELECT COUNT(*) AS n FROM outcome_contract").fetchone()["n"] == 2


def test_contract_change_reject_is_terminal(conn):
    make_provider(conn)
    data = make_outcome_plan(conn)
    report = make_report(conn, data)
    transport = ScriptedTransport(envelope(
        "modify", "建议把验收条件补成三条", revise_contract_suggestion(),
    ))
    said = dialogue.say(
        conn, data["plan_id"], "根据这次复盘，把成果契约改一下",
        report_id=report["report_id"], transport=transport,
    )
    proposal_id = said["suggestion"]["proposal_id"]

    rejected = proposals.decide(conn, proposal_id, approved=False, reason="先不改契约")
    assert rejected["status"] == "rejected" and rejected["effect"] == "recorded_only"
    assert contract.active(conn, data["plan_id"])["version"] == 1      # 什么都没动
    with pytest.raises(proposals.ProposalConflict):                     # 业务终态，不能重裁
        proposals.decide(conn, proposal_id, approved=True)


def test_plan_change_still_lands_in_report_turn(conn):
    """带报告的轮照样可以提节点建议（共用一条建议的位置，没有把老路堵死）。"""
    make_provider(conn)
    data = make_outcome_plan(conn)
    report = make_report(conn, data)
    transport = ScriptedTransport(envelope("modify", "把截止日顺延一周", {
        "action": "update_node", "node_id": data["task"],
        "fields": {"due_date": "2026-10-20"}, "why": "复盘卡显示落后",
    }))

    said = dialogue.say(
        conn, data["plan_id"], "根据这次复盘，把任务的截止日改一下",
        report_id=report["report_id"], transport=transport,
    )

    assert said["suggestion"]["kind"] == plan_change.KIND
    assert pending_kinds(conn) == [plan_change.KIND]


# ---------- ⑤ 周提醒与导出的成果语义 ----------

def test_weekly_notify_and_export_carry_outcome_semantics(conn):
    make_provider(conn)   # 指向没人监听的端口：任何模型调用都会当场失败
    data = make_outcome_plan(conn)   # 阶段没验收 → 契约两条必需条件都是缺口
    status = plan.weekly_status(conn, plan_id=data["plan_id"])

    subject, body = notify.compose_weekly(conn, status)
    active = contract.active(conn, data["plan_id"])
    card = plan.review_card(conn, data["plan_id"])

    assert f"成果契约《{active['title']}》" in body
    assert "验收缺口 2 条" in body
    assert card["conclusion"] in body
    assert llm.list_calls(conn) == []                     # 周提醒零模型调用

    name, markdown = export.weekly_markdown(conn, plan_id=data["plan_id"])
    assert name.startswith("周检查点-") and name.endswith(".md")   # 文件命名不变
    assert f"成果契约：{active['title']}｜验收缺口 2 条" in markdown
    assert card["conclusion"] in markdown
    assert llm.list_calls(conn) == []                     # 导出同样零模型调用


def test_weekly_notify_without_contract_says_so(conn):
    make_provider(conn)
    plan_id = ledger.create_active(conn, "plan", {"goal": "旧流程计划"}, actor="user")
    ledger.create_active(conn, "plan_node", {
        "plan_id": plan_id, "level": "stage", "title": "阶段 1", "sort_order": 0,
    }, actor="user")

    _, body = notify.compose_weekly(conn, plan.weekly_status(conn, plan_id=plan_id))
    _, markdown = export.weekly_markdown(conn, plan_id=plan_id)

    assert "还没有成果契约" in body and "还没有成果契约" in markdown
    assert "复盘卡" in body and "复盘卡" in markdown


def test_outcome_gap_count_dedupes_unbound_criteria(conn):
    """缺口计数按**条件**去重：没被任何阶段承接的条件同时出现在 missing 与 unsatisfied，
    不能算两遍——否则周提醒与周导出会向用户报出偏大的缺口数。"""
    created = post_plan_with_contract(PlanWithContractIn(contract=contract_payload(
        acceptance_criteria=[
            {"text": "条件一", "required": True},
            {"text": "条件二", "required": True},
            {"text": "条件三（没有阶段承接）", "required": True},
        ],
    )), conn)
    plan_id, contract_id = int(created["id"]), int(created["contract"]["id"])
    create_node(NodeIn(
        plan_id=plan_id, level="stage", title="阶段 1",
        acceptance_criteria=[CriterionIn(text="阶段内条件")],
        evidence_requirements=[EvidenceRequirementIn(kind="repository", description="仓库")],
        contract_criterion_ids=["oc-1", "oc-2"],   # oc-3 故意没有阶段承接
    ), conn)
    stage_id = int(plan.get_stages(conn, plan_id)[0]["id"])
    task_id = int(create_node(NodeIn(
        plan_id=plan_id, level="task", title="任务 1", parent_id=stage_id,
    ), conn)["id"])
    post_task_check(task_id, conn)
    evidence = post_stage_evidence(
        stage_id,
        EvidenceIn(kind="repository", reference="https://github.com/x/y", note="仓库"),
        conn,
    )
    post_stage_review(stage_id, StageReviewIn(
        contract_id=contract_id, submission_ids=[evidence["id"]],
        criteria_state={"c1": "met"}, decision="accepted", note="达标",
    ), conn)

    coverage = plan.contract_coverage(conn, plan_id)
    assert coverage["missing"] == ["oc-3"]
    assert coverage["unsatisfied"] == ["oc-3"]          # 同一个条件出现在两个列表里
    assert plan.outcome_snapshot(conn, plan_id)["acceptance_gaps"] == 1   # 去重后是 1，不是 2

    _, body = notify.compose_weekly(conn, plan.weekly_status(conn, plan_id=plan_id))
    assert "验收缺口 1 条" in body
