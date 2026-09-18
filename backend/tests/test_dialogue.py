"""计划级对话的单测（T28：SPEC 决策 37）。

覆盖四组东西：
① 看与聊：历史、句数、计划不存在 404；每轮 1 次调用、**不限轮数**（与候选对话的 6 轮不同）；
② 上下文：阶段、任务与状态、交付物、最近报告、落后量、档案都要进去——这是它给出有用
   回答的前提；历史按 6000 字符从最早截断，最新一句永远留着；
③ 提炼档案提案：没聊过 409、空 items 不产提案、类别非法判不合格并重试、最多 3 条；
④ 端到端：聊 → 提炼 → 批准 → 档案里真多一条（与 T28-1 的裁定分支接得上）。

一律假上游打桩，不打真实接口、不花钱。
"""

from __future__ import annotations

import json

import pytest

from app import advisor, db, dialogue, ledger, llm, plan, proposals


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


def add_profile(conn, category: str = "long_axis", content: str = "通用工程基础") -> int:
    return ledger.create_active(
        conn, "profile_item", {"category": category, "content": content}, actor="user"
    )


def make_plan(conn, goal: str = "把后端写通") -> int:
    plan_id = ledger.create_active(conn, "plan", {"goal": goal}, actor="user")
    stage_id = plan.add_node(conn, plan_id, "stage", "学 HTTP", deliverable="讲清一次请求全流程")
    plan.add_node(conn, plan_id, "task", "读 MDN", parent_id=stage_id, due_date="2026-10-01")
    done_id = plan.add_node(conn, plan_id, "task", "跑通一个路由", parent_id=stage_id)
    plan.check_task(conn, done_id)
    return plan_id


def extraction(*items: dict) -> str:
    return json.dumps({"items": list(items)}, ensure_ascii=False)


def change(category: str = "current_state", content: str = "晚上只剩一小时", why: str = "聊出来的"):
    return {"category": category, "content": content, "why": why}


def context_of(transport: ScriptedTransport, index: int = 0) -> str:
    """那次调用里「事实块」那一条（system 之后、历史之前）。"""
    return transport.seen[index]["payload"]["messages"][1]["content"]


# ---------- 看与聊 ----------

def test_view_of_a_fresh_dialogue_is_empty(conn):
    plan_id = make_plan(conn)

    view = dialogue.view(conn, plan_id)

    assert view["plan_id"] == plan_id
    assert view["messages"] == []
    assert view["turns_used"] == 0
    assert view["can_extract"] is False  # 还没聊过，没东西可提炼
    assert view["char_limit"] == dialogue.DIALOGUE_CHAR_LIMIT


def test_an_unknown_plan_is_a_not_found(conn):
    with pytest.raises(dialogue.DialogueNotFound):
        dialogue.view(conn, 999)
    with pytest.raises(dialogue.DialogueNotFound):
        dialogue.say(conn, 999, "聊一句", transport=ScriptedTransport("回复"))


def test_one_turn_records_both_sides_in_plain_words(conn):
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    transport = ScriptedTransport("先别急着加功能——把「跑通一个路由」交出去了再继续。")

    done = dialogue.say(conn, plan_id, "我卡在部署上了", transport=transport)

    assert done["turns_used"] == 1 and done["calls"] == 1
    assert done["reply"].startswith("先别急着")
    rows = dialogue.messages_of(conn, plan_id)
    assert [row["role"] for row in rows] == ["user", "assistant"]
    assert rows[0]["content"] == "我卡在部署上了"
    assert rows[1]["content"] == "先别急着加功能——把「跑通一个路由」交出去了再继续。"  # 存人话，不是 JSON
    # 系统提示里明确要求「不输出 JSON」：这一段不需要解析它的输出
    assert "不要输出 JSON" in transport.seen[0]["payload"]["messages"][0]["content"]


def test_there_is_no_turn_cap(conn):
    """长期窗口不能「聊六次就锁死」——成本闸是历史字符上限（决策 6/37 修订）。"""
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    transport = ScriptedTransport(*[f"第 {index} 次回复" for index in range(10)])

    for index in range(10):
        dialogue.say(conn, plan_id, f"第 {index} 句", transport=transport)

    assert dialogue.turns_used(conn, plan_id) == 10
    assert len(transport.seen) == 10


def test_a_blank_message_is_refused_and_records_nothing(conn):
    make_provider(conn)
    plan_id = make_plan(conn)

    with pytest.raises(dialogue.DialogueError):
        dialogue.say(conn, plan_id, "   ", transport=ScriptedTransport("回复"))

    assert dialogue.messages_of(conn, plan_id) == []


def test_an_empty_reply_costs_the_turn_but_keeps_your_words(conn):
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    transport = ScriptedTransport("   ")  # 一个字都没回

    with pytest.raises(dialogue.DialogueError):
        dialogue.say(conn, plan_id, "我先说说现状", transport=transport)

    assert len(transport.seen) == 1  # 不重试
    rows = dialogue.messages_of(conn, plan_id)
    assert [row["role"] for row in rows] == ["user"]  # 你的话留着
    assert dialogue.turns_used(conn, plan_id) == 1


def test_a_paused_plan_can_still_be_discussed(conn):
    """讨论不该被计划状态挡住：暂停了正是最需要商量的时刻。"""
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    plan.pause_plan(conn, plan_id)
    transport = ScriptedTransport("那就先停两周，把手上这条交出去再回来。")

    done = dialogue.say(conn, plan_id, "我有点想停一下", transport=transport)

    assert done["turns_used"] == 1
    assert "paused" in context_of(transport)  # 上下文里如实写着它现在是暂停


# ---------- 上下文 ----------

def test_the_context_carries_the_execution_facts(conn):
    make_provider(conn)
    add_profile(conn, "current_state", "晚上有两小时")
    plan_id = make_plan(conn)
    stage_id = plan.get_stages(conn, plan_id)[0]["id"]
    plan.submit_deliverable(conn, int(stage_id), url="https://example.com/x", note="写完了")
    done_task = conn.execute(
        "SELECT id FROM plan_node WHERE parent_id = ? AND title = '跑通一个路由'", (stage_id,)
    ).fetchone()["id"]
    plan.submit_report(conn, int(done_task), status="done", note="能跑了")
    transport = ScriptedTransport("嗯")

    dialogue.say(conn, plan_id, "下一步做什么", transport=transport)
    context = context_of(transport)

    assert "把后端写通" in context  # 计划目标
    assert "学 HTTP" in context  # 阶段
    assert "讲清一次请求全流程" in context  # 阶段的交付物
    assert "[x] 跑通一个路由" in context  # 任务与它的状态（打勾了）
    assert "[ ] 读 MDN" in context
    assert "2026-10-01" in context  # 截止日
    assert "能跑了" in context  # 最近报告
    assert "https://example.com/x" in context  # 已提交的交付物
    assert "晚上有两小时" in context  # 长期档案
    assert "落后情况" in context


def test_history_is_truncated_from_the_earliest(conn):
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    transport = ScriptedTransport("嗯", "嗯")

    dialogue.say(conn, plan_id, "AAA" * 2500, transport=transport)  # 一句就超上限
    dialogue.say(conn, plan_id, "BBB" * 50, transport=transport)

    history = transport.seen[1]["payload"]["messages"][2:]
    assert any("BBB" in item["content"] for item in history)
    assert not any("AAA" in item["content"] for item in history)  # 最早的被截掉


# ---------- 提炼档案提案 ----------

def test_extracting_before_any_turn_is_a_conflict(conn):
    make_provider(conn)
    plan_id = make_plan(conn)
    transport = ScriptedTransport(extraction())

    with pytest.raises(dialogue.DialogueConflict):
        dialogue.propose_profile_changes(conn, plan_id, transport=transport)

    assert transport.seen == []


def test_nothing_to_extract_creates_no_proposal(conn):
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    transport = ScriptedTransport("嗯", extraction())  # 提炼回空数组是正常情形

    dialogue.say(conn, plan_id, "就是随便聊聊", transport=transport)
    result = dialogue.propose_profile_changes(conn, plan_id, transport=transport)

    assert result["items"] == []
    assert proposals.list_pending(conn, "profile_change")["proposals"] == []


def test_extraction_lands_pending_profile_change_proposals(conn):
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    transport = ScriptedTransport(
        "嗯",
        extraction(change(), change("short_term_goal", "先把记账跑通", "目标收窄了")),
    )

    dialogue.say(conn, plan_id, "我这周只有一小时", transport=transport)
    result = dialogue.propose_profile_changes(conn, plan_id, transport=transport)

    assert len(result["items"]) == 2
    pending = proposals.list_pending(conn, "profile_change")["proposals"]
    assert [item["payload"]["content"] for item in pending] == ["晚上只剩一小时", "先把记账跑通"]
    assert pending[0]["payload"]["plan_id"] == plan_id
    assert "为什么这么记" not in pending[0]["payload"]  # 存的是 why，不是提示语
    assert pending[0]["payload"]["why"] == "聊出来的"
    # 提炼这一下要把「现档案」摆出来，免得提炼出一堆同义重复
    assert "通用工程基础" in transport.seen[1]["payload"]["messages"][1]["content"]


@pytest.mark.parametrize(
    "payload",
    [
        extraction(change(category="心情")),                      # 类别不在五个令牌里
        extraction(*[change(content=f"第 {i} 条") for i in range(4)]),  # 超过 3 条
    ],
)
def test_a_malformed_extraction_gets_one_retry_then_fails(conn, payload):
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    transport = ScriptedTransport("嗯", payload, payload)

    dialogue.say(conn, plan_id, "聊一句", transport=transport)
    with pytest.raises(dialogue.DialogueError):
        dialogue.propose_profile_changes(conn, plan_id, transport=transport)

    assert len(transport.seen) == 3  # 对话 1 次 + 提炼 2 次
    assert proposals.list_pending(conn, "profile_change")["proposals"] == []


def test_a_retried_extraction_can_succeed(conn):
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    transport = ScriptedTransport(
        "嗯",
        extraction(change(category="心情")),   # 第一次类别非法
        extraction(change()),                  # 第二次合格
    )

    dialogue.say(conn, plan_id, "我这周只有一小时", transport=transport)
    result = dialogue.propose_profile_changes(conn, plan_id, transport=transport)

    assert result["attempts"] == 2 and len(result["items"]) == 1
    assert "类别" in transport.seen[2]["payload"]["messages"][-1]["content"]  # 带原因重试


# ---------- 端到端：聊 → 提炼 → 批准 → 档案真变了 ----------

def test_from_chat_to_archive_in_three_steps(conn):
    make_provider(conn)
    add_profile(conn, "current_state", "晚上有两小时")
    plan_id = make_plan(conn)
    transport = ScriptedTransport("那我们把节奏改成每周两小时。", extraction(change()))

    dialogue.say(conn, plan_id, "我最近只有一小时了", transport=transport)
    extracted = dialogue.propose_profile_changes(conn, plan_id, transport=transport)
    decided = proposals.decide(conn, extracted["items"][0]["proposal_id"], approved=True)

    assert decided["effect"] == "profile_written"
    items = conn.execute(
        "SELECT category, content FROM profile_item WHERE status = 'active' ORDER BY id"
    ).fetchall()
    assert [dict(row) for row in items] == [
        {"category": "current_state", "content": "晚上有两小时"},
        {"category": "current_state", "content": "晚上只剩一小时"},
    ]
    # 旧的没被顶掉：这是**新增**一条，不是取代（要取代得先有「哪一条该被取代」的信息）
    assert dialogue.view(conn, plan_id)["turns_used"] == 1

# ---------- 上下文里「这个计划是怎么来的」（2026-09-18 用户要求） ----------
#
# 他的原话：只要是这个计划里面的，都该让它知道——包括当时没勾的部分与每个阶段的理由。
# 所以在「计划现在长什么样」之外，上下文还带上这条方向的来历与蓝图全貌。

def add_lineage(conn, plan_id: int, *, title: str = "学 HTTP", why: str = "对主线有帮助"):
    """造一段「定方向」的历史：一条被采纳的候选 + 它那段对话。"""
    request_id = advisor.record_request(conn, "search", "我不知道该学什么", plan_id)
    candidate_id = ledger.create_active(
        conn,
        "candidate",
        {"request_id": request_id, "title": title, "why": why, "depth_target": "够用", "rank": 1},
        actor="agent",
        reason="测试用",
    )
    for role, content in [
        ("user", "我每周大概能投入 6 小时"),
        ("assistant", json.dumps({"questions": [], "ready": True, "note": "信息够了"})),
    ]:
        conn.execute(
            "INSERT INTO plan_chat (plan_id, candidate_id, role, content, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (plan_id, candidate_id, role, content, "2026-09-18T10:00:00+08:00"),
        )
    conn.commit()
    return candidate_id


def add_blueprint(conn, plan_id: int, *, stages: list[dict], status: str = "pending") -> int:
    payload = {"plan_id": plan_id, "candidate_id": 1, "goal": "把后端写通", "stages": stages}
    proposal_id = ledger.create_active(
        conn,
        "proposal",
        {"kind": "plan_blueprint", "payload": json.dumps(payload, ensure_ascii=False), "reason": "测试用"},
        actor="agent",
    )
    if status != "pending":
        ledger.set_status(conn, "proposal", proposal_id, status, actor="agent", reason="测试用")
    return proposal_id


def test_context_carries_how_the_direction_came_about(conn):
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    add_lineage(conn, plan_id)
    transport = ScriptedTransport("嗯")

    dialogue.say(conn, plan_id, "开始吧", transport=transport)
    context = context_of(transport)

    assert "这条方向的来历" in context
    assert "学 HTTP" in context and "对主线有帮助" in context  # 采纳的是哪条、当时给的理由
    assert "我每周大概能投入 6 小时" in context  # 出蓝图之前聊过什么
    assert "信息够了" in context  # 助手那侧存的是 JSON，喂回去时渲染成人话


def test_context_carries_the_blueprint_and_what_was_not_ticked(conn):
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    # make_plan 已经建了阶段「学 HTTP」与任务「读 MDN」——它们就是「当时勾了的那部分」
    add_blueprint(
        conn,
        plan_id,
        stages=[
            {
                "title": "学 HTTP",
                "deliverable": "讲清一次请求全流程",
                "why": "它是后面所有接口的地基",
                "tasks": [{"title": "读 MDN", "due_date": None}, {"title": "写个 demo", "due_date": None}],
            },
            {"title": "做一个小服务", "deliverable": "一个能访问的地址", "why": "把学的用起来", "tasks": []},
        ],
        status="accepted",
    )
    transport = ScriptedTransport("嗯")

    dialogue.say(conn, plan_id, "下一步做什么", transport=transport)
    context = context_of(transport)

    assert "蓝图" in context
    assert "它是后面所有接口的地基" in context  # 每个阶段的理由
    assert "做一个小服务" in context and "当时没勾，没建" in context  # 没勾的那部分
    assert "写个 demo" in context and "读 MDN" in context
    # 已建的那些要标成已建，别让它以为整棵树都没建
    assert "学 HTTP」（已建）" in context


def test_a_superseded_blueprint_version_is_only_counted(conn):
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    stages = [{"title": "学 HTTP", "deliverable": "讲清流程", "why": "地基", "tasks": []}]
    add_blueprint(conn, plan_id, stages=stages, status="superseded")
    add_blueprint(conn, plan_id, stages=stages, status="pending")
    transport = ScriptedTransport("嗯")

    dialogue.say(conn, plan_id, "看看", transport=transport)
    context = context_of(transport)

    assert "1 版更早的已被新版顶掉" in context  # 旧版不铺开，只报个数
    assert "待你勾选的那一版" in context


def test_context_says_so_when_there_is_nothing_to_tell(conn):
    """不是从候选/蓝图来的老计划：如实说没有记录，而不是留一片空白。"""
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    transport = ScriptedTransport("嗯")

    dialogue.say(conn, plan_id, "开始吧", transport=transport)
    context = context_of(transport)

    assert context.count("没有记录") == 2  # 来历与蓝图各一句


def test_the_lineage_is_truncated_from_the_earliest(conn):
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    candidate_id = add_lineage(conn, plan_id)
    conn.execute(
        "INSERT INTO plan_chat (plan_id, candidate_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
        (plan_id, candidate_id, "user", "AAA" * 900, "2026-09-18T11:00:00+08:00"),
    )
    conn.commit()
    transport = ScriptedTransport("嗯")

    dialogue.say(conn, plan_id, "接着聊", transport=transport)
    context = context_of(transport)

    assert "AAA" in context  # 最新那一大段留着（它才是「我刚说的」）
    assert "我每周大概能投入 6 小时" not in context  # 更早的那些被截掉
