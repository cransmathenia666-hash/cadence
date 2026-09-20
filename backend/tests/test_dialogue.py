"""计划级对话的单测（T28：SPEC 决策 37；T31 起含「一条可执行建议」决策 39；
2026-09-20 起走**受控工具循环**，决策 40）。

覆盖五组东西：
① 看与聊：历史、句数、计划不存在 404；**不限轮数**（与候选对话的 6 轮不同）；输出是
   **信封**（人话 + 最多一条建议），形状不合格带原因重说一次；
② 资料**由它自己读**（决策 40）：这一轮不再预装业务事实，模型先要资料、系统取来给它，
   取来的才进上下文；历史仍按 6000 字符从最早截断，最新一句永远留着；**节点编号**在
   读来的资料里，它给建议时照抄；
③ 提炼档案提案：没聊过 409、空 items 不产提案、类别非法判不合格并重试、最多 3 条；
④ **建议落成提案与当场裁定**（T31）：一次最多一条、字段缺一 / 节点不存在 / 不属于本计划 /
   改前＝改后 / 名字撞车一律判不合格且**一条都不落**；批准后节点真改了且**编号不变**、
   加东西真的建了节点、**忽略什么都没写**；
⑤ 端到端：聊 → 提炼 → 批准 → 档案里真多一条（与 T28-1 的裁定分支接得上）。

工具本身与循环那几道闸（上限、拒绝、运行账）的用例在 `test_agent.py`。
一律假上游打桩，不打真实接口、不花钱。
"""

from __future__ import annotations

import json

import pytest

from app import (
    advisor,
    agent_runtime,
    agent_tools,
    db,
    dialogue,
    ledger,
    llm,
    plan,
    plan_change,
    proposals,
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


def envelope(reply: str = "嗯", suggestion: dict | None = None) -> str:
    """模型的输出信封（T31 起）：人话 + 最多一条建议。"""
    return json.dumps({"reply": reply, "suggestion": suggestion}, ensure_ascii=False)


def ask(*names: str, **args) -> str:
    """模型要读资料的那一种输出（决策 40）：一次可以要好几样。"""
    return json.dumps(
        {"tool_calls": [{"name": name, "args": args} for name in names]}, ensure_ascii=False
    )


def node_id_of(conn, title: str) -> int:
    row = conn.execute(
        "SELECT id FROM plan_node WHERE title = ? AND status != 'skipped'", (title,)
    ).fetchone()
    return int(row["id"])


def update_due(node_id: int, due_date: str = "2026-10-08", **overrides) -> dict:
    """一条「改节点」的建议（默认改截止日）。"""
    suggestion = {
        "action": "update_node",
        "node_id": node_id,
        "fields": {"due_date": due_date},
        "why": "那周我出差，挪一周更现实",
    }
    suggestion.update(overrides)
    return suggestion


def pending_changes(conn) -> list[dict]:
    return proposals.list_pending(conn, plan_change.KIND)["proposals"]


def change(category: str = "current_state", content: str = "晚上只剩一小时", why: str = "聊出来的"):
    return {"category": category, "content": content, "why": why}


def opening_of(transport: ScriptedTransport, index: int = 0) -> str:
    """那次调用里「工具目录」那一条（system 之后、历史之前）。"""
    return transport.seen[index]["payload"]["messages"][1]["content"]


def facts_of(transport: ScriptedTransport, index: int = -1) -> str:
    """那次调用里回填给模型的「你读到的资料」（循环里第 index 次调用的最后一条）。"""
    return transport.seen[index]["payload"]["messages"][-1]["content"]


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
        dialogue.say(conn, 999, "聊一句", transport=ScriptedTransport(envelope("回复")))


def test_one_turn_records_both_sides_in_plain_words(conn):
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    transport = ScriptedTransport(
        envelope("先别急着加功能——把「跑通一个路由」交出去了再继续。")
    )

    done = dialogue.say(conn, plan_id, "我卡在部署上了", transport=transport)

    assert done["turns_used"] == 1 and done["calls"] == 1
    assert done["reply"].startswith("先别急着")
    assert done["suggestion"] is None and done["proposal_id"] is None  # 没有建议就是纯聊天
    rows = dialogue.messages_of(conn, plan_id)
    assert [row["role"] for row in rows] == ["user", "assistant"]
    assert rows[0]["content"] == "我卡在部署上了"
    # 库里存的是**人话**（信封里的 reply），不是那个 JSON 壳——历史与截断口径因此没变
    assert rows[1]["content"] == "先别急着加功能——把「跑通一个路由」交出去了再继续。"
    assert "只输出一个 JSON 对象" in transport.seen[0]["payload"]["messages"][0]["content"]
    assert "一轮最多一条" in transport.seen[0]["payload"]["messages"][0]["content"]


def test_there_is_no_turn_cap(conn):
    """长期窗口不能「聊六次就锁死」——成本闸是历史字符上限（决策 6/37 修订）。"""
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    transport = ScriptedTransport(*[envelope(f"第 {index} 次回复") for index in range(10)])

    for index in range(10):
        dialogue.say(conn, plan_id, f"第 {index} 句", transport=transport)

    assert dialogue.turns_used(conn, plan_id) == 10
    assert len(transport.seen) == 10


def test_a_blank_message_is_refused_and_records_nothing(conn):
    make_provider(conn)
    plan_id = make_plan(conn)

    with pytest.raises(dialogue.DialogueError):
        dialogue.say(conn, plan_id, "   ", transport=ScriptedTransport(envelope("回复")))

    assert dialogue.messages_of(conn, plan_id) == []


def test_an_empty_reply_costs_the_turn_but_keeps_your_words(conn):
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    # 三次都不合格：空回复、根本不是信封的纯文本、以及一个不完整的 JSON
    transport = ScriptedTransport("   ", "就是一段没人话的话", "{}")

    with pytest.raises(dialogue.DialogueError):
        dialogue.say(conn, plan_id, "我先说说现状", transport=transport)

    # 形状不合格 → 带原因重说一次；**重说也计入这一轮的模型调用上限**（决策 40）
    assert len(transport.seen) == agent_runtime.MAX_MODEL_CALLS
    rows = dialogue.messages_of(conn, plan_id)
    assert [row["role"] for row in rows] == ["user"]  # 你的话留着
    assert dialogue.turns_used(conn, plan_id) == 1
    assert pending_changes(conn) == []  # 一直不合格 → 一条提案都没落


def test_a_paused_plan_can_still_be_discussed(conn):
    """讨论不该被计划状态挡住：暂停了正是最需要商量的时刻。"""
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    plan.pause_plan(conn, plan_id)
    transport = ScriptedTransport(
        ask("read_current_plan"), envelope("那就先停两周，把手上这条交出去再回来。")
    )

    done = dialogue.say(conn, plan_id, "我有点想停一下", transport=transport)

    assert done["turns_used"] == 1
    assert "paused" in facts_of(transport)  # 它读来的计划事实里如实写着现在是暂停


# ---------- 资料是它自己读的（2026-09-20，决策 40） ----------
#
# 用户拍板的方向：Agent 得**主动判断需要哪些资料**，而不是每轮把整棵计划树、全部档案
# 一起塞给它。所以这一节验两件事：① 不预装——目录里只有「能读什么」，没有任何业务事实；
# ② 读了就真进上下文——它要来的资料原样回填，它据此说话。

def test_the_opening_carries_only_the_catalog(conn):
    """第一轮就预装全部业务事实是不行的——目录只说明「你能读什么」，事实一样没有。"""
    make_provider(conn)
    add_profile(conn, "current_state", "晚上有两小时")
    plan_id = make_plan(conn)
    transport = ScriptedTransport(envelope("嗯"))

    dialogue.say(conn, plan_id, "随便聊聊", transport=transport)
    opening = opening_of(transport)

    for name in agent_tools.TOOLS:
        assert name in opening  # 四个只读工具都在目录里
    assert "把后端写通" not in opening  # 计划目标没预装
    assert "读 MDN" not in opening  # 阶段与任务没预装
    assert "晚上有两小时" not in opening  # 长期档案也没预装
    assert str(dialogue.DIALOGUE_CHAR_LIMIT) not in opening  # 目录里不该有历史上限之类的噪音


def test_reading_more_than_one_thing_in_a_single_call(conn):
    """一次模型调用可以批量要好几样——省得为读两样资料多花一轮模型（决策 40）。"""
    make_provider(conn)
    add_profile(conn, "current_state", "晚上有两小时")
    plan_id = make_plan(conn)
    stage_id = plan.get_stages(conn, plan_id)[0]["id"]
    plan.submit_deliverable(conn, int(stage_id), url="https://example.com/x", note="写完了")
    done_task = conn.execute(
        "SELECT id FROM plan_node WHERE parent_id = ? AND title = '跑通一个路由'", (stage_id,)
    ).fetchone()["id"]
    plan.submit_report(conn, int(done_task), status="done", note="能跑了")
    transport = ScriptedTransport(
        ask("read_current_plan", "read_recent_reports", "read_profile"),
        envelope("嗯"),
    )

    done = dialogue.say(conn, plan_id, "结合我最近的状态，下一步做什么", transport=transport)
    facts = facts_of(transport)  # 读来的资料在下一次调用的最后一条里回填

    assert "把后端写通" in facts  # 计划目标
    assert "学 HTTP" in facts  # 阶段
    assert "讲清一次请求全流程" in facts  # 阶段的交付物
    assert "[x] 跑通一个路由" in facts  # 任务与它的状态（打勾了）
    assert "[ ] 读 MDN" in facts
    assert "2026-10-01" in facts  # 截止日
    assert "能跑了" in facts  # 最近报告
    assert "https://example.com/x" in facts  # 已提交的交付物
    assert "晚上有两小时" in facts  # 长期档案
    assert "落后情况" in facts
    # 节点编号必须在读来的资料里（T31）：建议里要照抄它们，不给编号它就没处指
    assert f"（#{stage_id}）" in facts
    assert f"（#{done_task}）" in facts
    # 这一轮读了什么，回执里说得清清楚楚（界面「本轮依据」就是它）
    assert done["tools_used"] == ["read_current_plan", "read_recent_reports", "read_profile"]
    assert done["run"]["tool_calls"] == 3 and done["run"]["status"] == "ok"
    assert done["stop_reason"].startswith("答完了")


def test_a_turn_that_reads_nothing_still_works(conn):
    """不读也能答（闲聊那种）。那时 `tools_used` 是空的，运行账照样留一行。"""
    make_provider(conn)
    plan_id = make_plan(conn)
    transport = ScriptedTransport(envelope("那就先从最小的一步开始。"))

    done = dialogue.say(conn, plan_id, "随便聊聊", transport=transport)

    assert done["tools_used"] == [] and done["run"]["tool_calls"] == 0
    assert done["calls"] == 1  # 老字段没动：还是「调了几次模型」


def test_the_run_is_recorded_and_comes_back_with_the_view(conn):
    """运行账落在 `agent_run` 里，看对话时随消息带回来——刷新页面「本轮依据」还在。"""
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    transport = ScriptedTransport(ask("read_current_plan"), envelope("嗯"))

    done = dialogue.say(conn, plan_id, "下一步做什么", transport=transport)
    planned = done["tools_used"]

    row = conn.execute("SELECT * FROM agent_run ORDER BY id DESC LIMIT 1").fetchone()
    assert row["plan_id"] == plan_id and row["status"] == "ok"
    assert row["model_calls"] == 2 and row["tool_calls"] == 1
    assert row["dialogue_id"] == dialogue.messages_of(conn, plan_id)[-1]["id"]  # 挂在助手那条上
    carried = dialogue.view(conn, plan_id)["messages"][-1]["run"]
    assert carried["status"] == "ok"
    # GET 的 `run` 与 POST 回执的 `tool_names` 永远同一个形状（前端两处都靠它）
    assert carried["tool_names"] == planned == ["read_current_plan"]
    assert [tool["name"] for tool in carried["tools"]] == ["read_current_plan"]
    assert carried["tools"][0]["ok"] is True
    assert "阶段" in carried["tools"][0]["summary"]  # 人话摘要，不是原始字段
    assert carried["stop_reason"] == done["stop_reason"]
    # 你那一句上没有运行账（助手那条才有）
    assert dialogue.view(conn, plan_id)["messages"][-2]["run"] is None


def test_history_is_truncated_from_the_earliest(conn):
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    transport = ScriptedTransport(envelope("嗯"), envelope("嗯"))

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
    transport = ScriptedTransport(envelope("嗯"), extraction())  # 提炼回空数组是正常情形

    dialogue.say(conn, plan_id, "就是随便聊聊", transport=transport)
    result = dialogue.propose_profile_changes(conn, plan_id, transport=transport)

    assert result["items"] == []
    assert proposals.list_pending(conn, "profile_change")["proposals"] == []


def test_extraction_lands_pending_profile_change_proposals(conn):
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    transport = ScriptedTransport(
        envelope("嗯"),
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
    transport = ScriptedTransport(envelope("嗯"), payload, payload)

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
        envelope("嗯"),
        extraction(change(category="心情")),   # 第一次类别非法
        extraction(change()),                  # 第二次合格
    )

    dialogue.say(conn, plan_id, "我这周只有一小时", transport=transport)
    result = dialogue.propose_profile_changes(conn, plan_id, transport=transport)

    assert result["attempts"] == 2 and len(result["items"]) == 1
    assert "类别" in transport.seen[2]["payload"]["messages"][-1]["content"]  # 带原因重试


# ---------- 建议落成提案与当场裁定（T31：SPEC 决策 39） ----------
#
# 用户的原话：每轮除了人话，最多可以附一条建议——「改哪个东西、从什么改成什么、为什么」。
# 四件必须同时成立：**最多一条**、字段缺一即判不合格（让它带原因重说一次）、**没有建议时
# 就是纯聊天**、建议落成待裁定的提案并由你**当场**裁定（确认＝批准、忽略＝驳回）。

def test_a_suggestion_lands_one_pending_proposal(conn):
    """建议落成一条待裁定提案——你点确认之前，计划里一个字都没改。"""
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    task_id = node_id_of(conn, "读 MDN")
    transport = ScriptedTransport(
        envelope("那周你在出差，我把读 MDN 挪一周更现实。", update_due(task_id))
    )

    done = dialogue.say(conn, plan_id, "10 月第一周我要出差", transport=transport)

    assert done["reply"].startswith("那周你在出差")
    assert done["calls"] == 1  # 合格就一次调用
    assert done["suggestion"]["summary"] == "改「读 MDN」的截止日：2026-10-01 → 2026-10-08"
    landed = pending_changes(conn)
    assert len(landed) == 1
    payload = landed[0]["payload"]
    assert payload["action"] == "update_node" and payload["node_id"] == task_id
    assert payload["node_title"] == "读 MDN"
    assert payload["fields"] == {"due_date": "2026-10-08"}
    assert payload["before"] == {"due_date": "2026-10-01"}
    assert payload["why"] == "那周我出差，挪一周更现实"
    # 建议记着它是从哪一句冒出来的：界面据此把确认条挂在那条消息下面
    assert payload["dialogue_id"] == dialogue.messages_of(conn, plan_id)[-1]["id"]
    assert done["proposal_id"] == landed[0]["id"]
    # 库里存的仍是人话；计划一个字没动（改要等你点确认）
    assert dialogue.messages_of(conn, plan_id)[-1]["content"].startswith("那周你在出差")
    assert plan.get_node(conn, task_id)["due_date"] == "2026-10-01"


def test_the_view_hands_back_each_message_suggestion(conn):
    """看对话时把建议与那条提案一起带回来——刷新页面后确认条还在，不会变成无主的提案。"""
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    task_id = node_id_of(conn, "读 MDN")
    transport = ScriptedTransport(envelope("挪一周吧。", update_due(task_id)))
    done = dialogue.say(conn, plan_id, "我要出差", transport=transport)

    view = dialogue.view(conn, plan_id)
    assert view["messages"][-2]["suggestion"] is None  # 你的那一句没有建议
    carried = view["messages"][-1]["suggestion"]
    assert carried["proposal_id"] == done["proposal_id"]
    assert carried["status"] == "pending"
    assert carried["summary"] == "改「读 MDN」的截止日：2026-10-01 → 2026-10-08"

    proposals.decide(conn, done["proposal_id"], approved=True)
    # 已裁定的也带回来（界面就不给按钮了）——那一条「已确认」还看得见
    assert dialogue.view(conn, plan_id)["messages"][-1]["suggestion"]["status"] == "accepted"


def test_at_most_one_suggestion_by_shape(conn):
    """一次最多一条：信封里是**单个对象或 null**，给数组直接判不合格（形状上就堵死）。"""
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    task_id = node_id_of(conn, "读 MDN")
    two = json.dumps(
        {
            "reply": "我一次提两条",
            "suggestion": [update_due(task_id), update_due(task_id, due_date="2026-10-15")],
        },
        ensure_ascii=False,
    )
    transport = ScriptedTransport(two, two, two)

    with pytest.raises(dialogue.DialogueError):
        dialogue.say(conn, plan_id, "帮我看看", transport=transport)

    assert len(transport.seen) == agent_runtime.MAX_MODEL_CALLS  # 不合格 → 带原因重说
    assert "字段不合格" in transport.seen[-1]["payload"]["messages"][-1]["content"]
    assert pending_changes(conn) == []


def bad_suggestions(stage_id: int, task_id: int) -> list[tuple[dict, str]]:
    """一条条「批不了」的建议，以及它该被判不合格时说的那句话。"""
    return [
        ({"action": "update_node", "node_id": "十二", "why": "理由"}, "字段不合格"),
        ({"action": "update_node", "node_id": task_id, "fields": {"due_date": "2026-10-08"}}, "字段不合格"),
        ({"action": "delete_node", "node_id": task_id, "why": "删掉它"}, "不在能提的三类里"),
        ({"action": "update_node", "node_id": 99999, "fields": {"due_date": "2026-10-08"}, "why": "理由"}, "不存在"),
        ({"action": "update_node", "node_id": stage_id, "fields": {"owner": "我"}, "why": "理由"}, "改不了的字段"),
        ({"action": "update_node", "node_id": task_id, "fields": {"due_date": "2026-10-01"}, "why": "理由"}, "一模一样"),
        ({"action": "update_node", "node_id": task_id, "fields": {"due_date": "下周三"}, "why": "理由"}, "不是日期"),
        ({"action": "update_node", "node_id": task_id, "fields": {"deliverable": "交个东西"}, "why": "理由"}, "只属于阶段"),
        ({"action": "add_task", "node_id": stage_id, "task": {}, "why": "理由"}, "tasks 里至少写一件"),
        ({"action": "add_task", "node_id": task_id, "task": {"title": "新任务"}, "why": "理由"}, "只能挂在阶段下"),
        ({"action": "add_task", "node_id": stage_id, "task": {"title": "读 MDN"}, "why": "理由"}, "已经开着同名任务"),
        (
            {"action": "add_task", "node_id": stage_id, "tasks": [{"title": " "}], "why": "理由"},
            "没写 title",
        ),
        ({"action": "add_stage", "stage": {"title": "新阶段"}, "why": "理由"}, "没写「要交的东西」"),
        ({"action": "add_stage", "stage": {"title": "学 HTTP", "deliverable": "再讲一遍"}, "why": "理由"}, "同名阶段"),
    ]


def test_a_bad_suggestion_is_unqualified_and_lands_nothing(conn):
    """字段缺一、节点不存在、改前＝改后、名字撞车……**一条都不落**。"""
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    stage_id = node_id_of(conn, "学 HTTP")
    task_id = node_id_of(conn, "读 MDN")

    for bad, hint in bad_suggestions(stage_id, task_id):
        payload = envelope("我提一条", bad)
        transport = ScriptedTransport(payload, payload, payload)
        with pytest.raises(dialogue.DialogueError):
            dialogue.say(conn, plan_id, "帮我看看这条", transport=transport)
        assert len(transport.seen) == agent_runtime.MAX_MODEL_CALLS, bad  # 不合格 → 带原因重说
        assert hint in transport.seen[-1]["payload"]["messages"][-1]["content"], bad
        assert pending_changes(conn) == [], bad
    assert plan.get_node(conn, task_id)["due_date"] == "2026-10-01"  # 计划一个字没动


def test_a_bad_suggestion_told_why_can_come_back_right(conn):
    """带原因重说一次——第二次合格就照常落提案（这才是重试存在的意义）。"""
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    task_id = node_id_of(conn, "读 MDN")
    transport = ScriptedTransport(
        envelope("挪一周吧。", update_due(task_id, due_date="下周三")),  # 第一次日期写坏
        envelope("那改成 10-08。", update_due(task_id)),               # 第二次合格
    )

    done = dialogue.say(conn, plan_id, "我要出差", transport=transport)

    assert done["calls"] == 2 and done["reply"] == "那改成 10-08。"
    assert len(pending_changes(conn)) == 1


def test_a_suggestion_cannot_touch_another_plan(conn):
    """它只能动这一段对话所属的计划——别处的节点一律拒（决策 39）。"""
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    other_plan = ledger.create_active(conn, "plan", {"goal": "把英语捡起来"}, actor="user")
    other_stage = plan.add_node(conn, other_plan, "stage", "背单词", deliverable="记住 500 词")
    other_task = plan.add_node(conn, other_plan, "task", "每天二十分钟", parent_id=other_stage)
    payload = envelope("顺手把英语那边也改了", update_due(other_task))
    transport = ScriptedTransport(payload, payload, payload)

    with pytest.raises(dialogue.DialogueError):
        dialogue.say(conn, plan_id, "顺便看看", transport=transport)

    assert "不属于计划" in transport.seen[-1]["payload"]["messages"][-1]["content"]
    assert pending_changes(conn) == []
    assert plan.get_node(conn, other_task)["due_date"] is None


def test_confirming_an_update_keeps_the_id_and_writes_a_ledger_row(conn):
    """确认＝当场裁定：原地改、**编号不变**、台账一条流水——这就是「确认就改」。"""
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    task_id = node_id_of(conn, "读 MDN")
    plan.submit_report(conn, task_id, status="done", note="读完了")  # 先留一条指着它的引用
    transport = ScriptedTransport(envelope("挪一周吧。", update_due(task_id)))
    done = dialogue.say(conn, plan_id, "我要出差", transport=transport)

    decided = proposals.decide(conn, done["proposal_id"], approved=True)

    assert decided["effect"] == "node_updated"
    assert decided["updated"] == {
        "node_id": task_id,
        "changed": ["due_date"],
        "before": {"due_date": "2026-10-01"},
        "after": {"due_date": "2026-10-08"},
    }
    node = plan.get_node(conn, task_id)
    assert node["id"] == task_id  # 编号一个没动
    assert node["due_date"] == "2026-10-08"
    # 引用还指得到它（台账「取代」会把报告与交付物全断掉，原地改不会）
    reports = conn.execute(
        "SELECT node_id FROM report WHERE node_id = ?", (task_id,)
    ).fetchall()
    assert [int(row["node_id"]) for row in reports] == [task_id]
    events = ledger.history(conn, "plan_node", task_id)
    assert events[-1]["change_type"] == "update_fields"
    assert events[-1]["before_value"] == '{"due_date": "2026-10-01"}'
    assert events[-1]["after_value"] == '{"due_date": "2026-10-08"}'
    assert "那周我出差" in events[-1]["reason"]
    assert pending_changes(conn) == []


def test_ignoring_lands_nothing_and_leaves_one_line(conn):
    """忽略＝当场驳回：计划一个字都没改，台账记一句「聊天里先不动」。"""
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    task_id = node_id_of(conn, "读 MDN")
    transport = ScriptedTransport(envelope("挪一周吧。", update_due(task_id)))
    done = dialogue.say(conn, plan_id, "我要出差", transport=transport)

    decided = proposals.decide(
        conn, done["proposal_id"], approved=False, reason="聊天里先不动"
    )

    assert decided["effect"] == "recorded_only" and decided["status"] == "rejected"
    assert plan.get_node(conn, task_id)["due_date"] == "2026-10-01"
    assert len(ledger.history(conn, "plan_node", task_id)) == 1  # 只有建它那一条
    events = ledger.history(conn, "proposal", done["proposal_id"])
    assert "聊天里先不动" in events[-1]["reason"]
    assert pending_changes(conn) == []


def test_confirming_an_add_task_puts_it_under_the_named_stage(conn):
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    stage_id = node_id_of(conn, "学 HTTP")
    suggestion = {
        "action": "add_task",
        "node_id": stage_id,
        "task": {"title": "把错误处理补上", "due_date": "2026-10-15"},
        "why": "上次跑通只是顺路，错误路径根本没试过",
    }
    transport = ScriptedTransport(envelope("建议补一件任务。", suggestion))
    done = dialogue.say(conn, plan_id, "路由跑通了但没试过错路径", transport=transport)

    assert done["suggestion"]["summary"] == (
        "加一件任务：把错误处理补上（挂在「学 HTTP」下）｜截止 2026-10-15"
    )
    decided = proposals.decide(conn, done["proposal_id"], approved=True)

    assert decided["effect"] == "node_added"
    first = decided["added"]["nodes"][0]
    assert first["level"] == "task" and first["parent_id"] == stage_id
    added = plan.get_node(conn, int(first["id"]))
    assert added["title"] == "把错误处理补上"
    assert added["parent_id"] == stage_id and added["due_date"] == "2026-10-15"


def test_one_suggestion_can_carry_several_tasks(conn):
    """2026-09-19 用户走查放宽：往一个已有阶段里补任务，一条建议可以给一批。

    真实使用里「帮我排一下这个阶段的任务」是常态，一次只让加一件会把人逼成连点好几次
    确认，还逼出两组重复任务（他在真库里撞上过）。上限仍在（`MAX_TASKS`）。
    """
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    stage_id = node_id_of(conn, "学 HTTP")
    suggestion = {
        "action": "add_task",
        "node_id": stage_id,
        "tasks": [
            {"title": "把错误处理补上", "due_date": "2026-10-15"},
            {"title": "写一个并发压测脚本"},
            {"title": "给接口加日志"},
        ],
        "why": "他要把这个阶段的任务一次排完",
    }
    transport = ScriptedTransport(envelope("排好了，三条一起给你。", suggestion))
    done = dialogue.say(conn, plan_id, "帮我排一下这个阶段的任务", transport=transport)

    assert done["suggestion"]["summary"] == (
        "加 3 件任务（挂在「学 HTTP」下）：把错误处理补上（截止 2026-10-15）；"
        "写一个并发压测脚本；给接口加日志"
    )
    decided = proposals.decide(conn, done["proposal_id"], approved=True)

    assert decided["effect"] == "node_added"
    nodes = decided["added"]["nodes"]
    assert [node["title"] for node in nodes] == [
        "把错误处理补上",
        "写一个并发压测脚本",
        "给接口加日志",
    ]
    assert all(node["level"] == "task" and node["parent_id"] == stage_id for node in nodes)
    created = plan.get_node(conn, int(nodes[1]["id"]))
    assert created["parent_id"] == stage_id and created["due_date"] is None


def test_a_stage_can_be_added_together_with_its_tasks(conn):
    """加阶段时能一次连它下面的任务一起建——真实使用里最常见的形状。

    为什么要连建：一个阶段拆出来就是几件任务；先建空壳阶段再一件件补，计划表上会出现
    一个「一个任务都没有」的阶段，看着像空壳（他在真库里就撞上过这个观感）。
    """
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    suggestion = {
        "action": "add_stage",
        "stage": {
            "title": "开发日志上线",
            "deliverable": "一个已开好且发过内容的账号，至少 5 条内容上线",
            "why": "手上没有在建的东西，先把现成素材发出去验证节奏",
        },
        "tasks": [
            {"title": "选定平台开号并发出第一条"},
            {"title": "录一条两账号数据互不可见的屏", "due_date": "2026-10-02"},
        ],
        "why": "他要把这一阶段一次建出来，别一件一件挤",
    }
    transport = ScriptedTransport(envelope("阶段和它的任务一起给你。", suggestion))
    done = dialogue.say(conn, plan_id, "加一个阶段，里面放两件任务", transport=transport)

    assert "含 2 件任务" in done["suggestion"]["summary"]
    decided = proposals.decide(conn, done["proposal_id"], approved=True)

    assert decided["effect"] == "node_added"
    nodes = decided["added"]["nodes"]
    assert [node["level"] for node in nodes] == ["stage", "task", "task"]
    stage_node = plan.get_node(conn, int(nodes[0]["id"]))
    assert stage_node["title"] == "开发日志上线"
    assert stage_node["deliverable"] == "一个已开好且发过内容的账号，至少 5 条内容上线"
    assert [row["title"] for row in plan.get_stages(conn, plan_id)] == [
        "学 HTTP",
        "开发日志上线",
    ]  # 新阶段仍排在最后
    children = conn.execute(
        "SELECT * FROM plan_node WHERE parent_id = ? ORDER BY sort_order, id",
        (int(nodes[0]["id"]),),
    ).fetchall()
    assert [row["title"] for row in children] == [
        "选定平台开号并发出第一条",
        "录一条两账号数据互不可见的屏",
    ]
    assert children[1]["due_date"] == "2026-10-02"


def test_too_many_tasks_in_one_suggestion_is_refused(conn):
    """上限仍在：一条建议最多 MAX_TASKS 件——一次改半个计划就没法复核了。"""
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    stage_id = node_id_of(conn, "学 HTTP")
    too_many = [{"title": f"任务 {index}"} for index in range(plan_change.MAX_TASKS + 1)]
    bad = envelope(
        "排好了。",
        {"action": "add_task", "node_id": stage_id, "tasks": too_many, "why": "排一下"},
    )
    # 一直不合格 → 按上限中止，且**一条都不落**
    transport = ScriptedTransport(bad, bad, bad)
    with pytest.raises(dialogue.DialogueError) as error:
        dialogue.say(conn, plan_id, "把这个阶段的任务全排出来", transport=transport)
    assert f"最多加 {plan_change.MAX_TASKS} 件" in str(error.value)
    assert pending_changes(conn) == []


def test_two_tasks_with_the_same_name_in_one_batch_are_refused(conn):
    """批内自己撞自己也要拦：否则一次确认就把同一件事建了两遍。"""
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    stage_id = node_id_of(conn, "学 HTTP")
    bad = envelope(
        "排好了。",
        {
            "action": "add_task",
            "node_id": stage_id,
            "tasks": [{"title": "录演示视频"}, {"title": " 录演示视频 "}],
            "why": "排一下",
        },
    )
    transport = ScriptedTransport(bad, bad, bad)
    with pytest.raises(dialogue.DialogueError) as error:
        dialogue.say(conn, plan_id, "把这个阶段的任务排出来", transport=transport)
    assert "重名" in str(error.value)
    assert pending_changes(conn) == []


def test_confirming_an_add_stage_puts_it_last(conn):
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    suggestion = {
        "action": "add_stage",
        "stage": {
            "title": "做一个小服务",
            "deliverable": "一个能在浏览器里访问到的地址",
            "why": "把学到的用一次",
        },
        "why": "光看文档记不住",
    }
    transport = ScriptedTransport(envelope("建议再加一个阶段。", suggestion))
    done = dialogue.say(conn, plan_id, "学完了但没做过东西", transport=transport)

    assert done["suggestion"]["summary"] == (
        "加一个阶段：做一个小服务（排最后）｜要交的东西：一个能在浏览器里访问到的地址"
    )
    decided = proposals.decide(conn, done["proposal_id"], approved=True)

    assert decided["effect"] == "node_added"
    titles = [row["title"] for row in plan.get_stages(conn, plan_id)]
    assert titles == ["学 HTTP", "做一个小服务"]  # 新阶段排在最后
    assert plan.get_node(conn, int(decided["added"]["nodes"][0]["id"]))["deliverable"] == (
        "一个能在浏览器里访问到的地址"
    )


def test_adding_to_a_plan_that_left_active_is_refused_at_the_end(conn):
    """批准那一刻还要再验一遍：计划已经不是进行中了，加东西就拒——提案留着可重裁。"""
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    stage_id = node_id_of(conn, "学 HTTP")
    suggestion = {
        "action": "add_task",
        "node_id": stage_id,
        "task": {"title": "把错误处理补上"},
        "why": "错路径没试过",
    }
    transport = ScriptedTransport(envelope("建议补一件任务。", suggestion))
    done = dialogue.say(conn, plan_id, "要不要加的", transport=transport)
    plan.pause_plan(conn, plan_id)  # 中间它被暂停了

    with pytest.raises(proposals.ProposalConflict):
        proposals.decide(conn, done["proposal_id"], approved=True)

    assert len(pending_changes(conn)) == 1  # 提案保持可重裁
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM plan_node WHERE title = '把错误处理补上'"
    ).fetchone()["n"] == 0


# ---------- 端到端：聊 → 提炼 → 批准 → 档案真变了 ----------

def test_from_chat_to_archive_in_three_steps(conn):
    make_provider(conn)
    add_profile(conn, "current_state", "晚上有两小时")
    plan_id = make_plan(conn)
    transport = ScriptedTransport(envelope("那我们把节奏改成每周两小时。"), extraction(change()))

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

# ---------- 「这个计划是怎么来的」（2026-09-18 用户要求；2026-09-20 起改为按需读） ----------
#
# 他的原话：只要是这个计划里面的，都该让它知道——包括当时没勾的部分与每个阶段的理由。
# 决策 40 之后这一整块不再预装，改成 `read_plan_origin` 这一个工具：问「当初为什么这么排」
# 时它自己去读，读完进上下文。

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


def test_reading_the_origin_brings_how_the_direction_came_about(conn):
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    add_lineage(conn, plan_id)
    transport = ScriptedTransport(ask("read_plan_origin"), envelope("嗯"))

    dialogue.say(conn, plan_id, "当初为什么先排学 HTTP", transport=transport)
    facts = facts_of(transport)

    assert "这条方向的来历" in facts
    assert "学 HTTP" in facts and "对主线有帮助" in facts  # 采纳的是哪条、当时给的理由
    assert "我每周大概能投入 6 小时" in facts  # 出蓝图之前聊过什么
    assert "信息够了" in facts  # 助手那侧存的是 JSON，喂回去时渲染成人话


def test_reading_the_origin_brings_the_blueprint_and_what_was_not_ticked(conn):
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
    transport = ScriptedTransport(ask("read_plan_origin"), envelope("嗯"))

    dialogue.say(conn, plan_id, "下一步做什么", transport=transport)
    facts = facts_of(transport)

    assert "蓝图" in facts
    assert "它是后面所有接口的地基" in facts  # 每个阶段的理由
    assert "做一个小服务" in facts and "当时没勾，没建" in facts  # 没勾的那部分
    assert "写个 demo" in facts and "读 MDN" in facts
    # 已建的那些要标成已建，别让它以为整棵树都没建
    assert "学 HTTP」（已建）" in facts


def test_a_superseded_blueprint_version_is_only_counted(conn):
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    stages = [{"title": "学 HTTP", "deliverable": "讲清流程", "why": "地基", "tasks": []}]
    add_blueprint(conn, plan_id, stages=stages, status="superseded")
    add_blueprint(conn, plan_id, stages=stages, status="pending")
    transport = ScriptedTransport(ask("read_plan_origin"), envelope("嗯"))

    dialogue.say(conn, plan_id, "看看", transport=transport)
    facts = facts_of(transport)

    assert "1 版更早的已被新版顶掉" in facts  # 旧版不铺开，只报个数
    assert "待你勾选的那一版" in facts


def test_reading_the_origin_says_so_when_there_is_nothing_to_tell(conn):
    """不是从候选/蓝图来的老计划：如实说没有记录，而不是留一片空白。"""
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    transport = ScriptedTransport(ask("read_plan_origin"), envelope("嗯"))

    done = dialogue.say(conn, plan_id, "开始吧", transport=transport)
    facts = facts_of(transport)

    assert facts.count("没有记录") == 2  # 来历与蓝图各一句
    # 摘要里也说清「没有记录」，免得把这句读成「读失败了」
    assert "没有记录" in done["run"]["tools"][0]["summary"]


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
    transport = ScriptedTransport(ask("read_plan_origin"), envelope("嗯"))

    dialogue.say(conn, plan_id, "接着聊", transport=transport)
    facts = facts_of(transport)

    assert "AAA" in facts  # 最新那一大段留着（它才是「我刚说的」）
    assert "我每周大概能投入 6 小时" not in facts  # 更早的那些被截掉
