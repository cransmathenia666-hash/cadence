"""四问判断链路的单测（T12）。

一律用**假的上游**打桩，不打真实接口、不花一分钱（同 `test_llm.py` 的态度）。
覆盖三条主路径：合法输出 → 落提案；非法输出 → 重试一次；两次都不合格 → 如实报错且不留痕。

真实模型的端到端走查在 `tools/` 的冒烟脚本与你手工那一步，不放进这套单测——
单测要的是确定、免费、随时可跑。
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import advisor, db, ledger, llm, main


class ScriptedTransport:
    """按脚本依次返回的假上游。

    脚本用完还要被调，就直接报错——免得测试悄悄多调了一次模型（那正是要盯住的东西）。
    """

    def __init__(self, *texts: str) -> None:
        self._texts = list(texts)
        self.seen: list[dict] = []

    def __call__(self, url: str, headers: dict, payload: dict):
        self.seen.append({"url": url, "headers": headers, "payload": payload})
        if not self._texts:
            raise AssertionError("假上游被多调了一次：脚本里的回答已经用完")
        return 200, {
            "choices": [{"message": {"content": self._texts.pop(0)}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        }


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "test.db"
    db.init(path)
    connection = db.connect(path)
    yield connection
    connection.close()


def make_provider(conn) -> int:
    """一家能用的假 provider——`llm.Operation` 得 resolve 到它才会发请求。"""
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


def valid_json(ids: list[int]) -> str:
    """一份合格的四问输出。

    `time_budget` 故意留空 id 并写明「依据不足」——锁住这条被允许的路径：
    真的没依据时如实说，比编一条强。
    """
    anchor = ids[:1]
    return json.dumps(
        {
            "worth_learning": {"answer": "值得学，和长期主线一致", "profile_item_ids": anchor},
            "depth_target": {"answer": "够用就行", "profile_item_ids": anchor},
            "intensity": {"answer": "过一遍", "profile_item_ids": anchor},
            "time_budget": {
                "answer": "依据不足：当前状态与生活记录都空着，排不出期",
                "profile_item_ids": [],
            },
        },
        ensure_ascii=False,
    )


def invented_json() -> str:
    """引用了档案里根本没有的 id——这是"指回具体字段"最容易被糊弄过去的地方。"""
    return json.dumps(
        {
            "worth_learning": {"answer": "值得", "profile_item_ids": [9999]},
            "depth_target": {"answer": "够用", "profile_item_ids": [9999]},
            "intensity": {"answer": "过一遍", "profile_item_ids": [9999]},
            "time_budget": {"answer": "依据不足", "profile_item_ids": []},
        },
        ensure_ascii=False,
    )


def blank_answers_json() -> str:
    """四个空数组 + 四句漂亮话：正是要防的"空泛建议"。"""
    return json.dumps(
        {
            "worth_learning": {"answer": "值得学，很有前途", "profile_item_ids": []},
            "depth_target": {"answer": "够用", "profile_item_ids": []},
            "intensity": {"answer": "过一遍", "profile_item_ids": []},
            "time_budget": {"answer": "每天两小时", "profile_item_ids": []},
        },
        ensure_ascii=False,
    )


# ---------- 合法输出：落提案，但只是提案 ----------

def test_valid_output_lands_a_pending_proposal(conn):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线是后端 + 工程能力")
    add_profile(conn, "life_log", "这学期选了数据库课")
    transport = ScriptedTransport(valid_json([axis]))

    result = advisor.judge(conn, "我看到一个 Rust 异步编程教程", transport=transport)
    request_id = advisor.record_request(conn, "evaluate", "我看到一个 Rust 异步编程教程")
    proposal_id = advisor.propose(
        conn,
        request_id=request_id,
        raw_text="我看到一个 Rust 异步编程教程",
        result=result,
    )

    row = conn.execute("SELECT * FROM proposal WHERE id = ?", (proposal_id,)).fetchone()
    payload = json.loads(row["payload"])
    assert row["kind"] == advisor.MATERIAL_JUDGMENT_KIND
    assert row["status"] == "pending"  # 待裁定，不是「已生效」
    assert payload["learning_request_id"] == request_id
    assert payload["judgment"]["worth_learning"]["profile_item_ids"] == [axis]
    assert payload["profile_basis"]["total"] == 2
    assert result["calls"] == 1

    # 台账留痕（提案也是台账里登记过的东西），每次调用落一行记账
    assert ledger.history(conn, "proposal", proposal_id)[0]["change_type"] == "create"
    assert [call["task"] for call in llm.list_calls(conn)] == [advisor.TASK]

    # 只提案不动手：计划和档案一个字都没改
    assert conn.execute("SELECT COUNT(*) FROM plan").fetchone()[0] == 0
    assert conn.execute("SELECT status FROM profile_item WHERE id = ?", (axis,)).fetchone()[
        "status"
    ] == "active"


# ---------- 非法输出：重试一次 ----------

def test_bad_output_is_retried_once_and_can_succeed(conn):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线是后端")
    # 第一次少三问，第二次合格
    transport = ScriptedTransport(
        json.dumps(
            {"worth_learning": {"answer": "值得", "profile_item_ids": [axis]}}, ensure_ascii=False
        ),
        valid_json([axis]),
    )

    result = advisor.judge(conn, "某个教程", transport=transport)

    assert result["attempts"] == 2
    assert result["calls"] == 2
    # 重试时要把「哪里不合格」告诉模型，而不是让它重新猜
    assert "不合格" in transport.seen[1]["payload"]["messages"][-1]["content"]
    assert len(llm.list_calls(conn)) == 2


def test_two_bad_outputs_raise_and_leave_no_trace(conn):
    make_provider(conn)
    add_profile(conn, "long_axis", "主线是后端")
    transport = ScriptedTransport("我觉得挺好的，建议学一下", "还是不行")

    with pytest.raises(advisor.AdvisorError, match="连着 2 次"):
        advisor.judge(conn, "某个教程", transport=transport)

    assert len(transport.seen) == 2  # 只重试一次，不无限重试
    assert conn.execute("SELECT COUNT(*) FROM proposal").fetchone()[0] == 0


def test_invented_profile_id_is_rejected(conn):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线是后端")
    transport = ScriptedTransport(invented_json(), valid_json([axis]))

    result = advisor.judge(conn, "某个教程", transport=transport)

    assert result["attempts"] == 2
    assert "不存在的档案 id" in transport.seen[1]["payload"]["messages"][-1]["content"]


def test_blank_answers_without_ids_are_rejected(conn):
    """光说漂亮话、指不回档案的，一律不合格——这是本产品最该守住的一条。"""
    make_provider(conn)
    add_profile(conn, "long_axis", "主线是后端")
    transport = ScriptedTransport(blank_answers_json(), blank_answers_json())

    with pytest.raises(advisor.AdvisorError, match="依据不足"):
        advisor.judge(conn, "某个教程", transport=transport)


def test_empty_answer_text_is_rejected(conn):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线是后端")
    blank = json.dumps(
        {
            "worth_learning": {"answer": "", "profile_item_ids": [axis]},
            "depth_target": {"answer": "够用", "profile_item_ids": [axis]},
            "intensity": {"answer": "过一遍", "profile_item_ids": [axis]},
            "time_budget": {"answer": "依据不足", "profile_item_ids": []},
        },
        ensure_ascii=False,
    )
    transport = ScriptedTransport(blank, valid_json([axis]))

    result = advisor.judge(conn, "某个教程", transport=transport)

    assert result["attempts"] == 2


def test_markdown_code_fence_is_tolerated(conn):
    """模型爱把 JSON 包在 ```json 里——加壳不代表内容错，不该为此浪费一次重试。"""
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线是后端")

    result = advisor.judge(
        conn, "某个教程", transport=ScriptedTransport(f"```json\n{valid_json([axis])}\n```")
    )

    assert result["calls"] == 1


# ---------- 没档案就别问 ----------

def test_no_profile_means_no_judgement_at_all(conn):
    make_provider(conn)
    transport = ScriptedTransport(valid_json([1]))

    with pytest.raises(advisor.AdvisorError, match="档案"):
        advisor.judge(conn, "某个教程", transport=transport)

    assert transport.seen == []  # 一次都不该调：没判据就别花钱
    assert llm.list_calls(conn) == []


def test_prompt_declares_which_categories_are_missing(conn):
    make_provider(conn)
    add_profile(conn, "long_axis", "主线是后端")
    transport = ScriptedTransport(valid_json([1]))

    profile = advisor.read_profile(conn)
    assert [item["content"] for item in profile["items"]] == ["主线是后端"]
    assert set(profile["missing_categories"]) == {
        "life_habit",
        "life_log",
        "current_state",
        "short_term_goal",
    }

    advisor.judge(conn, "某个教程", transport=transport)
    prompt = transport.seen[0]["payload"]["messages"][-1]["content"]
    assert "#1 [long_axis] 主线是后端" in prompt
    assert "生活习惯" in prompt  # 缺失的类用中文名摊开讲
    assert "依据不足" in prompt  # 并要求"没依据就直说"


# ---------- 接口层 ----------

def test_requests_endpoint_records_input_and_proposes(conn, monkeypatch):
    make_provider(conn)
    axis = add_profile(conn, "long_axis", "主线是后端")
    monkeypatch.setattr(
        llm,
        "post_json",
        lambda url, headers, payload, timeout=30.0: (
            200,
            {
                "choices": [{"message": {"content": valid_json([axis])}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4},
            },
        ),
    )

    payload = main.post_request(main.RequestIn(raw_text="我看到一个 Rust 教程"), conn=conn)

    assert payload["request_id"] == 1
    assert payload["kind"] == advisor.MATERIAL_JUDGMENT_KIND
    assert payload["judgment"]["depth_target"]["answer"] == "够用就行"
    assert payload["profile_basis"]["total"] == 1

    recorded = conn.execute(
        "SELECT kind, raw_text FROM learning_request WHERE id = ?", (payload["request_id"],)
    ).fetchone()
    assert recorded["kind"] == "evaluate"
    assert recorded["raw_text"] == "我看到一个 Rust 教程"

    landed = conn.execute(
        "SELECT status FROM proposal WHERE id = ?", (payload["proposal_id"],)
    ).fetchone()
    assert landed["status"] == "pending"


def test_requests_endpoint_maps_advisor_error_to_400(conn):
    make_provider(conn)  # provider 有，档案没有

    with pytest.raises(HTTPException) as caught:
        main.post_request(main.RequestIn(raw_text="某个教程"), conn=conn)

    assert caught.value.status_code == 400
    assert "档案" in caught.value.detail


def test_request_text_cannot_be_blank():
    with pytest.raises(ValidationError):
        main.RequestIn(raw_text="")


def test_profile_endpoint_exposes_items_and_gaps(conn):
    add_profile(conn, "long_axis", "主线是后端")
    add_profile(conn, "life_log", "这学期选了数据库课")

    payload = main.get_profile(conn=conn)

    assert [item["category"] for item in payload["items"]] == ["long_axis", "life_log"]
    assert "short_term_goal" in payload["missing_categories"]
