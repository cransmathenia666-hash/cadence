"""LLM 接入的单测（T10）。

一律用**假的上游**（`FakeTransport`）打桩，永远不打真实接口、不花一分钱。
测的是确定性逻辑：掩码、记账、循环保护、CRUD 的边界。
"""

from __future__ import annotations

import json

import pytest

from app import db, llm, plan

# 一把"长"的假密钥：够长才会露出末 4 位
FAKE_KEY = "sk-fake-1234567890abcd"


class FakeTransport:
    """假上游：记下每次收到了什么，按脚本返回。"""

    def __init__(
        self,
        *,
        text: str = "好的",
        status: int = 200,
        prompt_tokens: int | None = 10,
        completion_tokens: int | None = 5,
    ) -> None:
        self.text = text
        self.status = status
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.seen: list[dict] = []

    def __call__(self, url: str, headers: dict, payload: dict):
        self.seen.append({"url": url, "headers": headers, "payload": payload})
        if self.status != 200:
            return self.status, {"__raw__": "上游炸了"}
        return 200, {
            "choices": [{"message": {"content": self.text}}],
            "usage": {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
            },
        }


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "test.db"
    db.init(path)
    connection = db.connect(path)
    yield connection
    connection.close()


def make_provider(conn, **overrides) -> int:
    values = {
        "name": "假提供商",
        "base_url": "http://127.0.0.1:9999/v1",
        "api_key": FAKE_KEY,
        "default_model": "fake-model",
    }
    values.update(overrides)
    return llm.create_provider(conn, **values)


def make_default_provider(conn, **overrides) -> int:
    provider_id = make_provider(conn, **overrides)
    llm.update_provider(conn, provider_id, set_as_default=True)
    return provider_id


# ---------- 掩码：密钥只写不读 ----------

def test_mask_keeps_only_the_tail():
    assert llm.mask_key(FAKE_KEY) == "****abcd"


@pytest.mark.parametrize("value", [None, ""])
def test_mask_of_missing_key_is_none(value):
    """没配就是没配，不编一个掩码出来假装配了。"""
    assert llm.mask_key(value) is None


def test_short_key_is_fully_masked():
    """短密钥露出 4 位等于露出半把，所以一律全掩。"""
    assert llm.mask_key("sk-1234") == "****"


def test_list_never_returns_the_plaintext_key(conn):
    make_provider(conn)

    payload = json.dumps(llm.list_providers(conn), ensure_ascii=False)

    assert FAKE_KEY not in payload
    listed = llm.list_providers(conn)[0]
    assert listed["api_key_masked"] == "****abcd"
    assert listed["has_api_key"] is True
    assert "api_key" not in listed  # 连字段名都不给，免得有人以为里面是明文


# ---------- CRUD 的边界 ----------

def test_duplicate_name_is_a_clear_error(conn):
    make_provider(conn)

    with pytest.raises(llm.LlmError, match="已经有一家"):
        make_provider(conn)


def test_renaming_does_not_wipe_the_key(conn):
    """界面回填不了密钥；改个名字不能顺手把钥匙擦掉。"""
    provider_id = make_provider(conn)

    llm.update_provider(conn, provider_id, name="改了名字")

    assert llm.get_provider(conn, provider_id)["api_key"] == FAKE_KEY
    assert llm.get_provider(conn, provider_id)["name"] == "改了名字"


def test_empty_api_key_means_do_not_change(conn):
    provider_id = make_provider(conn)

    llm.update_provider(conn, provider_id, api_key="")

    assert llm.get_provider(conn, provider_id)["api_key"] == FAKE_KEY


def test_setting_default_is_exclusive(conn):
    first = make_provider(conn, name="甲")
    second = make_provider(conn, name="乙")

    llm.update_provider(conn, first, set_as_default=True)
    llm.update_provider(conn, second, set_as_default=True)

    defaults = [row["name"] for row in llm.list_providers(conn) if row["is_default"]]
    assert defaults == ["乙"]


def test_delete_keeps_the_call_history(conn):
    """账是历史，provider 没了账还得留着——不然这周花了多少就查不出来了。"""
    provider_id = make_default_provider(conn)
    llm.Operation(conn, "judge", transport=FakeTransport()).chat([{"role": "user", "content": "hi"}])

    llm.delete_provider(conn, provider_id)

    assert llm.list_providers(conn) == []
    assert len(llm.list_calls(conn)) == 1


def test_resolve_provider_gives_clear_errors(conn):
    with pytest.raises(llm.LlmError, match="还没有可用的 provider"):
        llm.resolve_provider(conn)

    provider_id = make_default_provider(conn)
    llm.update_provider(conn, provider_id, enabled=False)
    with pytest.raises(llm.LlmError, match="还没有可用的 provider"):
        llm.resolve_provider(conn)

    with pytest.raises(llm.LlmError, match="不存在"):
        llm.resolve_provider(conn, 9999)


def test_disabled_provider_by_explicit_id_is_refused(conn):
    provider_id = make_provider(conn)
    llm.update_provider(conn, provider_id, enabled=False)

    with pytest.raises(llm.LlmError, match="已被停用"):
        llm.resolve_provider(conn, provider_id)


# ---------- 记账 ----------

def test_every_call_lands_in_the_ledger(conn):
    make_default_provider(conn)
    operation = llm.Operation(conn, "candidates", transport=FakeTransport())

    operation.chat([{"role": "user", "content": "一"}])
    operation.chat([{"role": "user", "content": "二"}])

    calls = llm.list_calls(conn)
    assert len(calls) == 2
    assert all(call["ok"] for call in calls)
    assert all(call["task"] == "candidates" for call in calls)
    assert calls[0]["input_tokens"] == 10
    assert calls[0]["output_tokens"] == 5
    assert calls[0]["model"] == "fake-model"
    assert calls[0]["duration_ms"] is not None


def test_failed_call_is_recorded_too(conn):
    """只记成功的账等于没账——失败的调用同样花了时间，也最需要被发现。"""
    make_default_provider(conn)
    operation = llm.Operation(conn, "judge", transport=FakeTransport(status=500))

    with pytest.raises(llm.LlmError, match="调用 provider"):
        operation.chat([{"role": "user", "content": "hi"}])

    calls = llm.list_calls(conn)
    assert len(calls) == 1
    assert calls[0]["ok"] is False
    assert "500" in calls[0]["error"]


def test_error_message_never_leaks_the_key(conn):
    make_default_provider(conn)
    operation = llm.Operation(conn, "judge", transport=FakeTransport(status=401))

    with pytest.raises(llm.LlmError) as caught:
        operation.chat([{"role": "user", "content": "hi"}])

    assert FAKE_KEY not in str(caught.value)
    assert FAKE_KEY not in json.dumps(llm.list_calls(conn), ensure_ascii=False)


# ---------- 循环保护 ----------

def test_the_fourth_call_is_refused(conn):
    """同一操作最多 3 次：第 4 次中止并报错，而不是继续烧。"""
    make_default_provider(conn)
    operation = llm.Operation(conn, "judge", transport=FakeTransport())

    for _ in range(llm.MAX_CALLS_PER_OPERATION):
        operation.chat([{"role": "user", "content": "hi"}])
    assert operation.used == llm.MAX_CALLS_PER_OPERATION

    with pytest.raises(llm.LlmError, match="超过上限"):
        operation.chat([{"role": "user", "content": "再来一次"}])

    assert operation.used == llm.MAX_CALLS_PER_OPERATION
    assert len(llm.list_calls(conn)) == llm.MAX_CALLS_PER_OPERATION  # 被拒的那次没有记账


def test_a_new_operation_gets_its_own_budget(conn):
    """额度是"每次操作"的，不是全局的——否则一天干三件事就再也调不动了。"""
    make_default_provider(conn)
    transport = FakeTransport()

    for _ in range(5):
        llm.Operation(conn, "judge", transport=transport).chat([{"role": "user", "content": "hi"}])

    assert len(llm.list_calls(conn)) == 5


# ---------- 连通性体检 ----------

def test_connectivity_test_reports_success(conn):
    provider_id = make_default_provider(conn)
    transport = FakeTransport(text="pong")

    result = llm.test_provider(conn, provider_id, transport=transport)

    assert result["ok"] is True
    assert "fake-model" in result["detail"]
    assert transport.seen[0]["url"] == "http://127.0.0.1:9999/v1/chat/completions"
    assert llm.list_calls(conn)[0]["task"] == llm.CONNECTIVITY_TASK


def test_connectivity_failure_is_reported_not_raised(conn):
    """配错了 base_url 是常事，体检就该给一句人话而不是抛异常。"""
    provider_id = make_default_provider(conn)

    result = llm.test_provider(conn, provider_id, transport=FakeTransport(status=404))

    assert result["ok"] is False
    assert "404" in result["detail"]
    assert llm.list_calls(conn)[0]["ok"] is False


def test_connectivity_test_does_not_eat_the_operation_budget(conn):
    """体检不吃「最多 3 次」的额度——那道闸是给业务操作用的。"""
    provider_id = make_default_provider(conn)
    llm.test_provider(conn, provider_id, transport=FakeTransport())

    operation = llm.Operation(conn, "judge", transport=FakeTransport())
    for _ in range(llm.MAX_CALLS_PER_OPERATION):
        operation.chat([{"role": "user", "content": "hi"}])

    assert operation.used == llm.MAX_CALLS_PER_OPERATION


def test_connectivity_needs_a_default_model(conn):
    provider_id = make_default_provider(conn, default_model=None)

    result = llm.test_provider(conn, provider_id, transport=FakeTransport())

    assert result["ok"] is False
    assert "默认模型" in result["detail"]


# ---------- 按周汇总 ----------

def test_summary_groups_by_week(conn):
    make_default_provider(conn)
    # 直接写库以控制时间：两条在同一周、一条在下一个 ISO 周
    for created_at, ok, tokens in [
        ("2026-09-15T10:00:00+08:00", 1, 10),
        ("2026-09-16T10:00:00+08:00", 0, 0),
        ("2026-09-22T10:00:00+08:00", 1, 30),
    ]:
        conn.execute(
            "INSERT INTO llm_call (provider_id, model, task, input_tokens, output_tokens,"
            " duration_ms, ok, error, created_at) VALUES (1, 'm', 'judge', ?, 0, 5, ?, ?, ?)",
            (tokens, ok, None if ok else "炸了", created_at),
        )
    conn.commit()

    summary = llm.call_summary_by_week(conn)

    # 用 plan 的周定义算期望，确保"周"只有一处定义
    assert [bucket["week"] for bucket in summary] == [
        plan.week_key(plan.parse_date("2026-09-22T10:00:00+08:00")),
        plan.week_key(plan.parse_date("2026-09-15T10:00:00+08:00")),
    ]
    newest, oldest = summary
    assert newest["calls"] == 1 and newest["input_tokens"] == 30
    assert oldest["calls"] == 2 and oldest["ok"] == 1 and oldest["failed"] == 1
    assert oldest["errors"] == ["炸了"]


# ---------- 读超时（2026-09-16 用户走查踩到的那次） ----------


class TimeoutTransport:
    """假上游：直接抛读超时，模拟模型生成太久。"""

    def __call__(self, url: str, headers: dict, payload: dict):
        raise TimeoutError("The read operation timed out")


def test_chat_timeout_is_generous_because_long_output_is_slow():
    """候选清单实测要 23 秒、4350 token；30 秒的默认值就是这么被撞穿的。

    这条不是形式主义：它是防止有人把超时改回一个「看起来更合理」的短值。
    """
    import inspect

    assert llm.CHAT_TIMEOUT_SECONDS >= 120
    assert (
        inspect.signature(llm.post_json).parameters["timeout"].default
        == llm.CHAT_TIMEOUT_SECONDS
    )
    # 体检反过来要快速失败，不能跟着聊天一起放宽
    assert llm.CONNECTIVITY_TIMEOUT_SECONDS < llm.CHAT_TIMEOUT_SECONDS


def test_timeout_message_explains_itself_and_is_recorded(conn):
    make_default_provider(conn)
    operation = llm.Operation(conn, "find", transport=TimeoutTransport())

    with pytest.raises(llm.LlmError) as caught:
        operation.chat([{"role": "user", "content": "我不知道该学什么"}])

    message = str(caught.value)
    assert "重试" in message  # 告诉用户下一步该干嘛，而不是只甩一个 TimeoutError
    assert str(llm.CHAT_TIMEOUT_SECONDS).rstrip("0").rstrip(".") in message  # 等了多久

    calls = llm.list_calls(conn)
    assert len(calls) == 1
    assert calls[0]["ok"] is False
    assert "TimeoutError" in calls[0]["error"]


# ---------- 模型设置（2026-10-04）：思考程度 / 温度 / 输出上限 / 能力标记 ----------

def test_settings_round_trip_through_public_provider(conn):
    """建家时给的设置要在公开关里原样可读（能力标记转成布尔）。"""
    provider_id = make_provider(
        conn,
        settings={
            "reasoning_effort": "high",
            "temperature": 0.3,
            "max_output_tokens": 4096,
            "context_window": 128000,
            "supports_web_search": 1,
            "supports_images": 1,
            "extra_body": '{"thinking": {"type": "enabled"}}',
        },
    )
    row = llm.get_provider(conn, provider_id)
    assert row["reasoning_effort"] == "high"
    assert row["max_output_tokens"] == 4096
    public = llm.public_provider(row)
    assert public["temperature"] == 0.3
    assert public["context_window"] == 128000
    assert public["supports_web_search"] is True
    assert public["supports_images"] is True
    assert json.loads(public["extra_body"]) == {"thinking": {"type": "enabled"}}


def test_new_provider_without_settings_has_all_none(conn):
    """不设置就是「没设置」：老行为一寸不变。"""
    row = llm.get_provider(conn, make_provider(conn))
    for column in ("reasoning_effort", "temperature", "max_output_tokens",
                   "context_window", "extra_body"):
        assert row[column] is None, column
    assert row["supports_web_search"] == 0
    assert row["supports_images"] == 0


def test_settings_replace_whole_block_on_update(conn):
    """更新时传了 settings 就是整块替换：没填的落 None（清空是正当操作）。

    路由层发的是 `ProviderSettings.to_columns()` 出来的**全 7 键**字典，
    所以这里也按真实调用路径构造，而不是手写半个字典。
    """
    from app.main import ProviderSettings

    provider_id = make_provider(
        conn, settings={"temperature": 0.7, "max_output_tokens": 2048}
    )
    llm.update_provider(
        conn, provider_id, settings=ProviderSettings(temperature=0.2).to_columns()
    )
    row = llm.get_provider(conn, provider_id)
    assert row["temperature"] == 0.2
    assert row["max_output_tokens"] is None  # 被整块替换清掉了


def test_update_without_settings_touches_nothing(conn):
    """set_as_default 这类局部更新不带 settings：设置必须原样保留。"""
    provider_id = make_provider(conn, settings={"temperature": 0.5})
    llm.update_provider(conn, provider_id, set_as_default=True)
    assert llm.get_provider(conn, provider_id)["temperature"] == 0.5


def test_invalid_settings_are_rejected_in_plain_language(conn):
    """非法值在入库前就报人话错误，不能等调用那天上游回莫名的 400。"""
    with pytest.raises(llm.LlmError, match="思考程度"):
        llm._validate_settings(reasoning_effort="很猛")
    with pytest.raises(llm.LlmError, match="温度"):
        llm._validate_settings(temperature=5)
    with pytest.raises(llm.LlmError, match="输出上限"):
        llm._validate_settings(max_output_tokens=0)
    with pytest.raises(llm.LlmError, match="上下文窗口"):
        llm._validate_settings(context_window=-1)
    with pytest.raises(llm.LlmError, match="附加请求体"):
        llm._validate_settings(extra_body="{oops")


def test_extra_body_must_be_an_object(conn):
    with pytest.raises(llm.LlmError, match="JSON 对象"):
        llm._validate_settings(extra_body='[1, 2, 3]')


def test_call_payload_carries_the_settings(conn):
    """调用载荷要带上设置：温度/输出上限/思考程度/附加体，没设置的字段不出现。"""
    provider_id = make_default_provider(
        conn,
        settings={
            "reasoning_effort": "low",
            "temperature": 0.1,
            "max_output_tokens": 1024,
            "extra_body": '{"top_p": 0.9}',
        },
    )
    transport = FakeTransport()
    llm.Operation(conn, "judge", transport=transport).chat(
        [{"role": "user", "content": "在吗"}]
    )
    payload = transport.seen[0]["payload"]
    assert payload["temperature"] == 0.1
    assert payload["max_tokens"] == 1024
    assert payload["reasoning_effort"] == "low"
    assert payload["top_p"] == 0.9  # 附加请求体逐字并入
    assert "context_window" not in payload  # 信息性字段不该漏进请求


def test_call_payload_without_settings_stays_minimal(conn):
    """什么都不设置：请求体只有 model + messages，与加这组设置之前一模一样。"""
    make_default_provider(conn)
    transport = FakeTransport()
    llm.Operation(conn, "judge", transport=transport).chat(
        [{"role": "user", "content": "在吗"}]
    )
    assert transport.seen[0]["payload"] == {
        "model": "fake-model",
        "messages": [{"role": "user", "content": "在吗"}],
    }


def test_broken_extra_body_in_db_fails_the_call_clearly(conn):
    """库里躺着一个坏 JSON（比如手工改库改坏）：调用要报明确错误而不是裸异常。"""
    provider_id = make_default_provider(conn)
    conn.execute("UPDATE llm_provider SET extra_body = '{bad' WHERE id = ?", (provider_id,))
    conn.commit()
    with pytest.raises(llm.LlmError, match="附加请求体"):
        llm.Operation(conn, "judge", transport=FakeTransport()).chat(
            [{"role": "user", "content": "在吗"}]
        )


def test_parse_reply_prefers_tool_call_arguments():
    """结构化输出（工具调用）取回的是工具参数那串 JSON；没有工具调用照旧取正文。"""
    body = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [{"function": {"arguments": '{"summary": "ok", "points": []}'}}],
                }
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2},
    }
    text, prompt_tokens, completion_tokens = llm._parse_reply(200, body)
    assert text == '{"summary": "ok", "points": []}'
    assert (prompt_tokens, completion_tokens) == (1, 2)

    plain = {"choices": [{"message": {"content": "你好"}}], "usage": {}}
    assert llm._parse_reply(200, plain)[0] == "你好"


# ---------- 拉远端模型列表（添加接入时的自动补全） ----------

def test_fetch_models_lists_ids_sorted(monkeypatch):
    """哑列表（只有 id）也照常工作：排序返回，不带任何编造的参数。"""
    import app.llm as llm_module

    def fake_get(url, headers, timeout=20.0):
        assert url == "http://127.0.0.1:9999/v1/models"
        assert headers["Authorization"] == f"Bearer {FAKE_KEY}"
        return {"data": [{"id": "zeta"}, {"id": "alpha"}, {"nope": 1}, "junk"]}

    monkeypatch.setattr(llm_module, "_get_json", fake_get)
    assert llm.fetch_models("http://127.0.0.1:9999/v1/", FAKE_KEY) == [
        {"id": "alpha"}, {"id": "zeta"},
    ]


def test_fetch_models_extracts_upstream_metadata(monkeypatch):
    """上游带了参数就原样提取：窗口 / 输出上限 / 图片模态 / 推理支持。"""
    import app.llm as llm_module

    def fake_get(url, headers, timeout=20.0):
        return {"data": [{
            "id": "grand/model",
            "context_length": 204800,
            "max_completion_tokens": 8192,
            "architecture": {"input_modalities": ["text", "image"]},
            "supported_parameters": ["temperature", "reasoning_effort"],
        }]}

    monkeypatch.setattr(llm_module, "_get_json", fake_get)
    assert llm.fetch_models("http://127.0.0.1:9999/v1") == [{
        "id": "grand/model",
        "context_window": 204800,
        "max_output_tokens": 8192,
        "supports_images": True,
        "supports_reasoning": True,
    }]


def test_fetch_models_without_key_sends_no_auth_header(monkeypatch):
    import app.llm as llm_module

    seen = {}

    def fake_get(url, headers, timeout=20.0):
        seen.update(headers)
        return {"data": []}

    monkeypatch.setattr(llm_module, "_get_json", fake_get)
    assert llm.fetch_models("http://127.0.0.1:9999/v1") == []
    assert "Authorization" not in seen


def test_fetch_models_reports_upstream_errors_in_plain_language(monkeypatch):
    import app.llm as llm_module

    def fake_get(url, headers, timeout=20.0):
        raise llm.LlmError("上游返回 401：bad key")

    monkeypatch.setattr(llm_module, "_get_json", fake_get)
    with pytest.raises(llm.LlmError, match="401"):
        llm.fetch_models("http://127.0.0.1:9999/v1")


def test_fetch_models_rejects_unrecognized_shape(monkeypatch):
    import app.llm as llm_module

    monkeypatch.setattr(llm_module, "_get_json", lambda url, headers, timeout=20.0: {"oops": 1})
    with pytest.raises(llm.LlmError, match="模型列表"):
        llm.fetch_models("http://127.0.0.1:9999/v1")


def test_fetch_models_needs_a_base_url():
    with pytest.raises(llm.LlmError, match="接口地址"):
        llm.fetch_models("  ")


def test_fetch_models_reports_text_only_models_explicitly(monkeypatch):
    """上游明确给了模态列表却没有 image：如实回 False，界面才能把图片勾摘下来。"""
    import app.llm as llm_module

    monkeypatch.setattr(
        llm_module, "_get_json",
        lambda url, headers, timeout=20.0: {"data": [{
            "id": "text/only",
            "architecture": {"input_modalities": ["text"]},
        }]},
    )
    assert llm.fetch_models("http://127.0.0.1:9999/v1") == [
        {"id": "text/only", "supports_images": False},
    ]
