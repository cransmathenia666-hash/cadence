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
