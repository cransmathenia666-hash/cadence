"""受控工具循环与只读工具的单测（2026-09-20 落地，SPEC 决策 40）。

这一层验的是「Agent 凭什么说话」：它只能读约定的四样资料、读不到别的计划、一次读多少
有额度、撞了上限要如实收场、每一轮都留一行可排错的运行账。**不写任何业务数据**这条也在这里。

三组东西：
① 四个工具本身（`agent_tools`）：目录与注册表一致、类别过滤、非法名字与非法参数被拒；
② 循环那几道闸（`agent_runtime`）：先读后答、一次批量读、工具额度、模型调用上限、
   结构错与工具轮共用一个额度、上下文总闸从最旧的丢；
③ 收场与账：失败/撞上限**一条提案都不落**、`agent_run` 三种状态都留痕、上游挂了也留痕。

一律假上游打桩，不打真实接口、不花钱。
"""

from __future__ import annotations

import json

import pytest

from app import (
    agent_runtime,
    agent_tools,
    db,
    dialogue,
    ledger,
    llm,
    plan,
    plan_change,
)

from test_dialogue import (  # noqa: F401 —— 复用同一套桩件，免得两处各写一遍
    ScriptedTransport,
    add_profile,
    ask,
    envelope,
    make_plan,
    make_provider,
    pending_changes,
)


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "test.db"
    db.init(path)
    connection = db.connect(path)
    yield connection
    connection.close()


def runs(conn) -> list[dict]:
    return [dict(row) for row in conn.execute("SELECT * FROM agent_run ORDER BY id").fetchall()]


def facts_seen(transport: ScriptedTransport, index: int = -1) -> str:
    """那次调用里回填给模型的「你读到的资料」。"""
    return transport.seen[index]["payload"]["messages"][-1]["content"]


# ---------- ① 四个工具本身 ----------

def test_the_catalog_and_the_registry_are_the_same_thing(conn):
    """目录由注册表生成——「目录里写了、其实没有」这类不一致从结构上不会发生。"""
    catalog = agent_tools.catalog_text()

    assert list(agent_tools.TOOLS) == [
        "read_current_plan",
        "read_recent_reports",
        "read_profile",
        "read_plan_origin",
    ]
    for name in agent_tools.TOOLS:
        assert name in catalog
    assert "tool_calls" in catalog  # 目录里要教它怎么开口要资料


def test_every_tool_is_read_only(conn):
    """四个工具都只读：跑一遍下来，业务表一行都没多。"""
    add_profile(conn)
    plan_id = make_plan(conn)

    for name in agent_tools.TOOLS:
        agent_tools.execute(conn, plan_id, name, {})

    assert conn.execute("SELECT COUNT(*) AS n FROM plan_node").fetchone()["n"] == 3
    assert conn.execute("SELECT COUNT(*) AS n FROM proposal").fetchone()["n"] == 0
    assert runs(conn) == []  # 工具本身也不写运行账（那是循环的事）


def test_reading_the_plan_brings_status_lag_and_numbers(conn):
    add_profile(conn)
    plan_id = make_plan(conn)
    stage = plan.get_stages(conn, plan_id)[0]
    task = conn.execute(
        "SELECT * FROM plan_node WHERE parent_id = ? AND title = '读 MDN'", (stage["id"],)
    ).fetchone()

    outcome = agent_tools.execute(conn, plan_id, "read_current_plan", {})

    assert "把后端写通" in outcome.text and "落后情况" in outcome.text
    assert f"（#{stage['id']}）" in outcome.text and f"（#{task['id']}）" in outcome.text
    assert "2026-10-01" in outcome.text  # 截止日
    assert "2 个任务" in outcome.summary and "1 个阶段" in outcome.summary


def test_reading_the_profile_can_be_narrowed_to_one_category(conn):
    add_profile(conn, "current_state", "晚上有两小时")
    add_profile(conn, "long_axis", "长期做工程")
    plan_id = make_plan(conn)

    all_of_it = agent_tools.execute(conn, plan_id, "read_profile", {})
    one_of_it = agent_tools.execute(conn, plan_id, "read_profile", {"category": "current_state"})

    assert "晚上有两小时" in all_of_it.text and "长期做工程" in all_of_it.text
    assert "晚上有两小时" in one_of_it.text and "长期做工程" not in one_of_it.text
    assert "1 条" in one_of_it.summary


def test_an_unknown_category_is_refused(conn):
    add_profile(conn)
    plan_id = make_plan(conn)

    with pytest.raises(agent_tools.ToolError) as error:
        agent_tools.execute(conn, plan_id, "read_profile", {"category": "心情"})

    assert "不是约定的类别" in str(error.value)


def test_an_unknown_tool_name_is_refused(conn):
    plan_id = make_plan(conn)

    with pytest.raises(agent_tools.ToolError) as error:
        agent_tools.execute(conn, plan_id, "read_anything", {})

    assert "没有叫「read_anything」的工具" in str(error.value)


def test_a_plan_id_argument_is_refused(conn):
    """「读哪个计划」不是模型能选的：它只能读这一段对话所属的那一个。"""
    add_profile(conn)
    plan_id = make_plan(conn)
    other = ledger.create_active(conn, "plan", {"goal": "把英语捡起来"}, actor="user")

    with pytest.raises(agent_tools.ToolError) as error:
        agent_tools.execute(conn, plan_id, "read_current_plan", {"plan_id": other})

    assert "计划编号由系统指定" in str(error.value)


def test_empty_reads_say_so_in_plain_words(conn):
    """没有档案时说「一条都没有」，而不是留一片空白让人以为读失败了。"""
    plan_id = make_plan(conn)

    assert "一条都没有" in agent_tools.execute(conn, plan_id, "read_profile", {}).text
    assert "还没有报告" in agent_tools.execute(conn, plan_id, "read_recent_reports", {}).text


# ---------- ② 循环那几道闸 ----------

def test_it_reads_the_plan_then_answers(conn):
    """用户走查里最典型的一轮：问「我现在先做什么」→ 它自己读计划 → 据此回答。"""
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    transport = ScriptedTransport(ask("read_current_plan"), envelope("先做读 MDN，它截止最早。"))

    done = dialogue.say(conn, plan_id, "根据目前进度，我下一步先做什么", transport=transport)

    assert len(transport.seen) == 2  # 一次读、一次答
    assert done["reply"].startswith("先做读 MDN")
    assert done["tools_used"] == ["read_current_plan"]
    assert done["run"]["model_calls"] == 2 and done["run"]["status"] == "ok"
    assert "读 MDN" in facts_seen(transport)  # 资料是真回填进去了


def test_a_bad_output_and_a_tool_round_share_the_same_budget(conn):
    """结构错也计入上限（决策 6 的老规矩）：读一轮 + 两次不合格 = 3 次，到此为止。"""
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    transport = ScriptedTransport(ask("read_current_plan"), "不是信封", "还不是信封")

    with pytest.raises(dialogue.DialogueError) as error:
        dialogue.say(conn, plan_id, "看看计划", transport=transport)

    assert len(transport.seen) == agent_runtime.MAX_MODEL_CALLS
    assert "连着 3 次都没给出合格的输出" in str(error.value)
    assert pending_changes(conn) == []


def test_hitting_the_model_call_cap_says_what_is_missing(conn):
    """一直读、不回答：撞上限时如实说「读了什么、还没读什么」，**不猜一个答案**。"""
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    transport = ScriptedTransport(
        ask("read_current_plan"),
        ask("read_recent_reports"),
        ask("read_profile"),
    )

    with pytest.raises(dialogue.DialogueError) as error:
        dialogue.say(conn, plan_id, "随便看看", transport=transport)

    said = str(error.value)
    assert "按上限中止" in said
    assert "读过：read_current_plan、read_recent_reports、read_profile" in said
    assert "还没读：read_plan_origin" in said
    assert pending_changes(conn) == []  # 撞上限时什么都不落
    recorded = runs(conn)[-1]
    assert recorded["status"] == agent_runtime.STATUS_LIMIT
    assert recorded["model_calls"] == 3 and recorded["tool_calls"] == 3
    assert [tool["name"] for tool in json.loads(recorded["tools"])] == [
        "read_current_plan",
        "read_recent_reports",
        "read_profile",
    ]


def test_the_tool_budget_is_six_and_the_rest_is_refused(conn):
    """一次可以批量要好几样，但一轮最多 6 次；要超了，超出的那些如实说不读。"""
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    transport = ScriptedTransport(
        ask(*(["read_current_plan"] * 7)),  # 一口气要 7 样
        envelope("我读了 6 次，够了。"),
    )

    done = dialogue.say(conn, plan_id, "把这些都读一遍", transport=transport)

    assert done["run"]["tool_calls"] == agent_runtime.MAX_TOOL_CALLS
    refused = facts_seen(transport, 1)  # 回填的那一段（第 7 条被如实拒了）
    assert "额度" in refused and "已经用完了" in refused
    assert len(done["run"]["tools"]) == agent_runtime.MAX_TOOL_CALLS  # 没读成的那条不记账


def test_an_invalid_tool_call_does_not_kill_the_turn(conn):
    """问了自己编的工具：**原样回给它**让它换一个，而不是把这一轮判死。"""
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    transport = ScriptedTransport(
        ask("read_everything_about_me"),
        ask("read_current_plan"),
        envelope("重新读到了，我先说结论。"),
    )

    done = dialogue.say(conn, plan_id, "你看着办", transport=transport)

    assert done["reply"].startswith("重新读到了")
    failed = done["run"]["tools"][0]
    assert failed["name"] == "read_everything_about_me" and failed["ok"] is False
    assert "没有叫" in failed["summary"]
    assert done["tools_used"] == ["read_current_plan"]  # 只有真读成的那条算依据


def test_a_plan_id_the_model_makes_up_is_refused_and_told_why(conn):
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    other = ledger.create_active(conn, "plan", {"goal": "把英语捡起来"}, actor="user")
    transport = ScriptedTransport(
        json.dumps(
            {"tool_calls": [{"name": "read_current_plan", "args": {"plan_id": other}}]},
            ensure_ascii=False,
        ),
        envelope("那我只读手头这个计划。"),
    )

    done = dialogue.say(conn, plan_id, "顺便看看英语那边", transport=transport)

    assert "计划编号由系统指定" in done["run"]["tools"][0]["summary"]
    assert "把英语捡起来" not in facts_seen(transport)  # 别的计划一个字都没读到


def test_older_reads_are_dropped_when_the_context_gets_too_big(conn):
    """本轮资料总量超闸：**从最旧的那一轮往下丢**，并留一句实话。"""
    make_provider(conn)
    for index in range(10):
        add_profile(conn, "current_state", f"第 {index} 条现状：" + "长" * 200)
    plan_id = make_plan(conn)
    stage_id = plan.get_stages(conn, plan_id)[0]["id"]
    for index in range(80):  # 把计划撑到 6000 字的上限，两次读取叠起来才够撞总闸
        plan.add_node(conn, plan_id, "task", f"第 {index} 件事：" + "做" * 80, parent_id=stage_id)
    transport = ScriptedTransport(
        ask("read_current_plan", *(["read_profile"] * 3)),  # 第一轮就读到满
        ask("read_current_plan"),                           # 第二轮仍在读
        envelope("读完了。"),
    )

    dialogue.say(conn, plan_id, "全读一遍再回答", transport=transport)

    # 第三轮那次的 messages 里：第一轮读来的档案已经被丢掉，并说清了为什么
    joined = "\n".join(item["content"] for item in transport.seen[2]["payload"]["messages"])
    assert "已经省略" in joined
    assert "第 0 条现状" not in joined  # 最旧那一轮确实没了
    assert "【阶段与任务】" in joined  # 最新那次读取留着


# ---------- ③ 收场与运行账 ----------

def test_every_turn_leaves_one_run_row(conn):
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    transport = ScriptedTransport(
        ask("read_current_plan"), envelope("嗯"), envelope("还是嗯")
    )

    dialogue.say(conn, plan_id, "第一句", transport=transport)
    dialogue.say(conn, plan_id, "第二句", transport=transport)

    rows = runs(conn)
    assert [row["status"] for row in rows] == ["ok", "ok"]
    assert all(row["plan_id"] == plan_id for row in rows)
    # 挂在助手那条回话上：界面据此在消息下面显示「本轮依据」
    messages = dialogue.messages_of(conn, plan_id)
    assert [row["dialogue_id"] for row in rows] == [int(messages[1]["id"]), int(messages[3]["id"])]


def test_a_failed_run_leaves_a_row_on_your_own_message(conn):
    """失败时那一轮没有助手回话，运行账就挂在你那一句上（不然这一轮谁都查不到）。"""
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)
    transport = ScriptedTransport(ask("read_current_plan"), ask("read_profile"), ask("read_profile"))

    with pytest.raises(dialogue.DialogueError):
        dialogue.say(conn, plan_id, "读不出结论就别答", transport=transport)

    row = runs(conn)[-1]
    assert row["status"] == agent_runtime.STATUS_LIMIT
    assert row["dialogue_id"] == int(dialogue.messages_of(conn, plan_id)[0]["id"])
    carried = dialogue.view(conn, plan_id)["messages"][0]["run"]  # 挂在你那一句上，界面也看得到
    assert carried["status"] == agent_runtime.STATUS_LIMIT
    assert "还没读" in carried["stop_reason"]


def test_an_upstream_failure_still_leaves_a_row(conn):
    """上游挂了（网络、超时、没配 provider）：那一轮同样留痕，而且如实往上抛。"""
    make_provider(conn)
    add_profile(conn)
    plan_id = make_plan(conn)

    def broken(url: str, headers: dict, payload: dict):
        raise TimeoutError("等了 180 秒还没读完")

    with pytest.raises(llm.LlmError):
        dialogue.say(conn, plan_id, "在吗", transport=broken)

    row = runs(conn)[-1]
    assert row["status"] == agent_runtime.STATUS_FAILED
    assert "调用模型失败" in row["stop_reason"]
    assert row["model_calls"] == 0
    assert dialogue.messages_of(conn, plan_id)[0]["content"] == "在吗"  # 你的话留着


def test_it_still_cannot_write_anything(conn):
    """跑一整轮下来，业务数据一个字没动——要改必须你点「确认」（决策 39）。"""
    make_provider(conn)
    add_profile(conn, "current_state", "晚上有两小时")
    plan_id = make_plan(conn)
    stage_id = plan.get_stages(conn, plan_id)[0]["id"]
    transport = ScriptedTransport(
        ask("read_current_plan"),
        envelope(
            "我建议把读 MDN 挪一周。",
            {
                "action": "update_node",
                "node_id": int(
                    conn.execute(
                        "SELECT id FROM plan_node WHERE title = '读 MDN'"
                    ).fetchone()["id"]
                ),
                "fields": {"due_date": "2026-10-08"},
                "why": "那周我出差",
            },
        ),
    )

    done = dialogue.say(conn, plan_id, "下周我要出差", transport=transport)

    landed = pending_changes(conn)
    assert done["suggestion"] is not None and len(landed) == 1
    assert landed[0]["kind"] == plan_change.KIND and landed[0]["decided_at"] is None
    assert conn.execute(
        "SELECT due_date FROM plan_node WHERE title = '读 MDN'"
    ).fetchone()["due_date"] == "2026-10-01"  # 改要等你点「确认」
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM plan_node WHERE parent_id = ?", (stage_id,)
    ).fetchone()["n"] == 2  # 一件都没多建
    # 档案一个字没动（要写档案走另一条提案，不在这条路上）
    assert conn.execute("SELECT COUNT(*) AS n FROM profile_item").fetchone()["n"] == 1