"""接口层契约：入参校验与错误响应形状（P1 补 + 方案 C）。

为什么不走 TestClient：TestClient 依赖 httpx，而 requirements.txt 刻意只留三个包，
加依赖属于 SPEC 第 16 节的 Ask first。这里直接测请求模型与错误处理器——FastAPI 用的
就是这两样东西，等价，且不引入新依赖。将来真要发 HTTP 时再谈 httpx。
"""

from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from app import config
from app.main import (
    NodeIn,
    app,
    http_error_handler,
    human_readable_errors,
    validation_error_handler,
)


# ---------- 建节点的 due_date 收成真日期 ----------

def test_due_date_accepts_iso_string_and_becomes_date():
    node = NodeIn(plan_id=1, level="checkpoint", title="接上 SQLite 读写", due_date="2026-09-30")

    assert node.due_date == date(2026, 9, 30)


def test_due_date_is_optional():
    """不填就是「没定计划完成日」，落后量按无从判断处理，不能强制必填。"""
    assert NodeIn(plan_id=1, level="stage", title="阶段 1").due_date is None


@pytest.mark.parametrize("bad", ["2026-13-01", "2026/09/30", "2026-9-3", "下周三", "20260930"])
def test_impossible_due_date_is_rejected(bad):
    """以前这些会原样入库，让落后量永远算不出来也报不出错；现在在边界就挡住。

    注意这里只认零填充的 ISO 日期——P2 前端表单必须送 `2026-09-30` 这种写法。
    """
    with pytest.raises(ValidationError):
        NodeIn(plan_id=1, level="checkpoint", title="x", due_date=bad)


def test_level_still_only_accepts_two_values():
    with pytest.raises(ValidationError):
        NodeIn(plan_id=1, level="milestone", title="x")


# ---------- 错误响应：所有出口一个形状（方案 C） ----------

def run(handler, exc):
    """直接调错误处理器。它们是 async，但不需要真起 HTTP 服务。"""
    return asyncio.run(handler(None, exc))


def body_of(response) -> dict:
    return json.loads(response.body)


def real_errors(**payload) -> list[dict]:
    """拿 Pydantic 真正吐出来的错误，而不是我手编的形状。"""
    try:
        NodeIn(**payload)
    except ValidationError as exc:
        return exc.errors()
    raise AssertionError("预期这里会校验失败")


def as_request_body(errors: list[dict]) -> list[dict]:
    """FastAPI 在请求体校验失败时会给 loc 加上 "body" 前缀，这里照样模拟。"""
    return [dict(error, loc=("body", *error["loc"])) for error in errors]


def test_date_error_turns_into_chinese():
    errors = as_request_body(real_errors(
        plan_id=1, level="checkpoint", title="x", due_date="2026-14-15"))

    assert human_readable_errors(errors) == "参数不合法：到期日不是有效日期（要 YYYY-MM-DD，如 2026-09-30）"


def test_missing_field_turns_into_chinese():
    errors = as_request_body(real_errors(plan_id=1, level="stage"))

    assert human_readable_errors(errors) == "参数不合法：标题是必填的"


def test_enum_error_turns_into_chinese():
    errors = as_request_body(real_errors(plan_id=1, level="milestone", title="x"))

    assert human_readable_errors(errors) == "参数不合法：层级的取值不在允许范围内"


def test_unknown_error_type_falls_back_to_original_message():
    """没登记的类型宁可露出英文，也不吞掉信息或编一个可能不对的说法。"""
    message = human_readable_errors([{
        "type": "something_new_from_pydantic", "loc": ("body", "title"),
        "msg": "Some brand new complaint",
    }])

    assert message == "参数不合法：标题Some brand new complaint"


def test_field_without_label_falls_back_to_raw_name():
    message = human_readable_errors([{
        "type": "missing", "loc": ("body", "brand_new_field"), "msg": "Field required",
    }])

    assert message == "参数不合法：brand_new_field是必填的"


def test_both_error_paths_share_one_shape():
    """方案 C 的全部意义：前端只看一种形状，detail 永远是字符串、errors 永远是数组。"""
    validation = body_of(run(validation_error_handler, RequestValidationError(
        [{"type": "missing", "loc": ("body", "title"), "msg": "Field required"}])))
    business = body_of(run(http_error_handler, HTTPException(status_code=409, detail="同名检查点")))
    not_found = body_of(run(http_error_handler, HTTPException(status_code=404, detail="计划不存在")))

    for payload in (validation, business, not_found):
        assert set(payload) == {"detail", "errors"}
        assert isinstance(payload["detail"], str)
        assert isinstance(payload["errors"], list)

    assert validation["errors"][0]["loc"] == ["body", "title"]  # 前端靠它定位到具体输入框
    assert business["errors"] == []


def test_validation_path_still_returns_422():
    response = run(validation_error_handler, RequestValidationError(
        [{"type": "missing", "loc": ("body", "title"), "msg": "Field required"}]))

    assert response.status_code == 422


@pytest.mark.parametrize("status", [400, 404, 405, 409])
def test_http_error_path_keeps_its_status_code(status):
    response = run(http_error_handler, HTTPException(status_code=status, detail="说明"))

    assert response.status_code == status
    assert body_of(response)["errors"] == []


def test_non_string_detail_is_stringified():
    """兜底：万一有人往 detail 里塞了别的类型，也不能漏出第二种形状。"""
    response = run(http_error_handler, HTTPException(status_code=400, detail={"weird": "shape"}))

    assert isinstance(body_of(response)["detail"], str)


# ---------- 跨源（CORS）：只放行本机前端（见 tasks/plan.md 风险表） ----------

def cors_kwargs() -> dict:
    """取出已注册的 CORS 中间件配置。

    断言「恰好一个」是为了防有人把中间件删掉——删了这些测试会红，
    而不是悄悄变成「不校验」。
    """
    middlewares = [item for item in app.user_middleware if item.cls is CORSMiddleware]
    assert len(middlewares) == 1, f"期望恰好一个 CORSMiddleware，实际 {len(middlewares)} 个"
    return middlewares[0].kwargs


def test_cors_allows_the_configured_dev_origins():
    assert cors_kwargs()["allow_origins"] == list(config.FRONTEND_ORIGINS)


def test_cors_never_opens_a_wildcard_origin():
    """来源一放开，等于任何网页都能读这个后端的数据；本地开发也没理由放通配符。"""
    origins = cors_kwargs()["allow_origins"]

    assert "*" not in origins
    # 不带端口的 localhost 会放行该主机的任意端口，比需要的宽
    assert "http://localhost" not in origins
    assert all(origin.startswith(("http://localhost:", "http://127.0.0.1:")) for origin in origins)


def test_frontend_port_is_defined_in_one_place():
    """端口集中配置：两个来源都从 config.FRONTEND_PORT 拼出来，不各写一遍。"""
    assert config.FRONTEND_PORT == 3000
    assert all(str(config.FRONTEND_PORT) in origin for origin in config.FRONTEND_ORIGINS)


def test_cors_methods_are_a_whitelist_matching_the_contract():
    """方法是白名单不是通配符——契约加方法时这条会红，提醒回来改。"""
    assert set(cors_kwargs()["allow_methods"]) == {"GET", "POST", "PUT", "DELETE"}
