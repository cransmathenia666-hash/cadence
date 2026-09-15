"""FastAPI 应用入口。

接口清单与前后端契约见 `docs/SPEC.md` 第 11 节，契约源就是这里的 `/openapi.json`。
P1 补上闭环那一段：建计划 → 建节点 → 提交报告 → 看状态与落后量。

业务规则不在这里：状态机、落后量、阶段判定都在 `app/plan.py`，
接口只负责收参数、把领域错误翻译成 HTTP 状态码。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import date
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.utils import is_body_allowed_for_status_code
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config, db, ledger, llm, plan

app = FastAPI(
    title="cadence",
    version="0.1.0",
    description="学习决策与跟进 Agent：方向层 + 计划表 + 跟进",
)

# 跨源：开发期浏览器里的前端（Next.js，另一个端口）要能调这个后端。
# 只放行 config.FRONTEND_ORIGINS 里那几个本机来源，不用 ["*"]——来源一旦放开，
# 任何网页都能读这个后端的数据。
#
# 为什么 allow_headers 用 ["*"]：前端发 JSON 必须带 Content-Type，浏览器会先发
# 预检请求；将来加 token 也会多一个头。来源已经收紧了，头放开没有额外风险。
# 方法按 SPEC 第 11 节契约里真实用到的四种列出来，不用通配符——契约加方法时
# 这里会提醒你回来改。
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(config.FRONTEND_ORIGINS),
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)


def get_conn() -> Iterator[sqlite3.Connection]:
    """一个请求一条连接。

    为什么不共用一条全局连接：FastAPI 把同步接口放在线程池里跑，
    而 sqlite3 的连接默认不允许跨线程使用，共用会踩到线程错误。
    """
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


class PlanIn(BaseModel):
    goal: str = Field(min_length=1, description="这个计划要达成的目标")


class NodeIn(BaseModel):
    plan_id: int
    level: Literal["stage", "checkpoint"]
    title: str = Field(min_length=1)
    parent_id: int | None = Field(default=None, description="检查点必填：所属阶段")
    deliverable: str | None = Field(default=None, description="阶段用：可验证的交付物")
    due_date: date | None = Field(default=None, description="计划完成日，写 ISO 日期（如 2026-09-30）")
    sort_order: int = 0


class ReportIn(BaseModel):
    node_id: int
    status: Literal["done", "partial", "stuck", "skipped"]
    note: str = Field(min_length=1, description="一句话说明，必填")
    artifact_url: str | None = None
    material_feedback: str | None = None


# ---------- 错误响应：所有出口一个形状（方案 C，SPEC 第 11 节） ----------
#
# 为什么需要这一节：FastAPI 有两条互不相干的报错路径。请求没进门就被校验拦下时，
# 框架自己的处理器把 `detail` 固定塞成「错误对象数组」（msg 还是英文）；而进门之后
# 我们自己 raise 的，框架只是把 `detail` 原样照抄（所以我们塞的是中文字符串）。
# 同一个键两种类型，前端 alert 会显示成 [object Object]。
# 这里把两条路径都拍成同一个形状：detail 给人看（中文），errors 给程序用（字段级明细）。

# 字段名 → 中文标签。没登记的字段回退用原始字段名，不做猜测。
_FIELD_LABELS: dict[str, str] = {
    "goal": "目标",
    "plan_id": "计划 id",
    "level": "层级",
    "title": "标题",
    "parent_id": "所属阶段",
    "deliverable": "交付物",
    "due_date": "到期日",
    "sort_order": "排序号",
    "node_id": "节点 id",
    "status": "状态",
    "note": "一句话说明",
    "artifact_url": "产物链接",
    "material_feedback": "资料评价",
}

# Pydantic 的错误类型 → 中文说明。没登记的类型回退用原始的英文 msg——
# 宁可露出英文，也不要吞掉信息或编一个可能不对的说法。
_TYPE_MESSAGES: dict[str, str] = {
    "missing": "是必填的",
    "date_from_datetime_parsing": "不是有效日期（要 YYYY-MM-DD，如 2026-09-30）",
    "date_parsing": "不是有效日期（要 YYYY-MM-DD，如 2026-09-30）",
    "date_from_datetime_inexact": "不是有效日期（要 YYYY-MM-DD，如 2026-09-30）",
    "literal_error": "的取值不在允许范围内",
    "string_too_short": "不能为空",
    "int_parsing": "必须是整数",
    "int_type": "必须是整数",
    "string_type": "必须是文本",
    "json_invalid": "请求体不是合法的 JSON",
}


def human_readable_errors(errors: list[dict]) -> str:
    """把 Pydantic 的字段级错误拼成一句中文，给用户看。"""
    parts = []
    for error in errors:
        # loc 形如 ["body", "due_date"]；去掉 "body" 只留字段路径
        location = [str(item) for item in error.get("loc", ()) if item != "body"]
        field = _FIELD_LABELS.get(location[-1], location[-1]) if location else "请求体"
        reason = _TYPE_MESSAGES.get(str(error.get("type")), str(error.get("msg", "不合法")))
        parts.append(f"{field}{reason}")
    return "参数不合法：" + "；".join(parts) if parts else "参数不合法"


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """入参不合法的出口（请求还没进业务函数就被拦下的那种）。"""
    errors = list(exc.errors())
    return JSONResponse(
        status_code=422,
        content={"detail": human_readable_errors(errors), "errors": jsonable_encoder(errors)},
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException) -> Response:
    """所有 HTTPException 的出口：我们自己 raise 的 400 / 409，以及框架生成的 404 / 405。

    `detail` 一律拍成字符串。正常路径下它本来就是字符串，这里的兜底是为了
    「万一有人塞了别的类型」也不会漏出第二种形状。
    """
    headers = getattr(exc, "headers", None)
    if not is_body_allowed_for_status_code(exc.status_code):
        return Response(status_code=exc.status_code, headers=headers)
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return JSONResponse(
        status_code=exc.status_code, content={"detail": detail, "errors": []}, headers=headers
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/plan", status_code=201)
def create_plan(payload: PlanIn, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """建一个计划。目标是唯一必填项。"""
    goal = payload.goal.strip()
    try:
        plan_id = ledger.create_active(conn, "plan", {"goal": goal}, actor="user")
    except ledger.LedgerError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"id": plan_id, "goal": goal}


@app.post("/api/plan/nodes", status_code=201,
          responses={409: {"description": "同一层级下已有未收尾的同名节点（多半是重复提交）"}})
def create_node(payload: NodeIn, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """建一个阶段或检查点。

    两级结构的一致性由后端把关，不信前端：阶段不能有 parent_id，
    检查点必须有 parent_id、且指向同一个计划里的某个阶段。
    重复提交（前端双击、请求重发）由 `plan.add_node` 挡下并返回 409。
    """
    if plan.resolve_plan(conn, payload.plan_id) is None:
        raise HTTPException(status_code=404, detail=f"计划 id={payload.plan_id} 不存在")

    if payload.level == "checkpoint":
        if payload.parent_id is None:
            raise HTTPException(status_code=400, detail="检查点必须指定 parent_id（所属阶段）")
        parent = plan.get_node(conn, payload.parent_id)
        if parent is None or parent["level"] != "stage":
            raise HTTPException(status_code=400, detail="parent_id 必须指向一个阶段节点")
        if int(parent["plan_id"]) != payload.plan_id:
            raise HTTPException(status_code=400, detail="检查点必须和所属阶段在同一个计划里")
    elif payload.parent_id is not None:
        raise HTTPException(status_code=400, detail="阶段节点不能有 parent_id")

    try:
        node_id = plan.add_node(
            conn,
            plan_id=payload.plan_id,
            level=payload.level,
            title=payload.title,
            parent_id=payload.parent_id,
            deliverable=payload.deliverable,
            # 边界处转成 ISO 文本再往下走：库里 due_date 是 TEXT，而 Python 3.12 起
            # sqlite3 不再自带 date 适配器，直接绑 date 对象会触发废弃警告。
            # 校验归边界（这里是 date 类型），存储归文本，内部照旧只用 parse_date 解析。
            due_date=None if payload.due_date is None else payload.due_date.isoformat(),
            sort_order=payload.sort_order,
        )
    except plan.DuplicateNode as error:
        # 409：请求本身没错，是和现有状态冲突——重复提交走这一条
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ledger.LedgerError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"id": node_id, "level": payload.level, "title": payload.title.strip()}


@app.post("/api/report", status_code=201)
def post_report(payload: ReportIn, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """提交一条报告：状态四选一 + 一句话（必填），产物与资料评价可选。"""
    if plan.get_node(conn, payload.node_id) is None:
        raise HTTPException(status_code=404, detail=f"节点 id={payload.node_id} 不存在")
    try:
        return plan.submit_report(
            conn,
            payload.node_id,
            payload.status,
            payload.note,
            artifact_url=payload.artifact_url,
            material_feedback=payload.material_feedback,
        )
    except plan.PlanError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/api/plan")
def get_plan(plan_id: int | None = None, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """当前计划、节点树、当前阶段、落后量。不传 plan_id 就取最新的 active 计划。"""
    tree = plan.plan_tree(conn, plan_id=plan_id)
    if plan_id is not None and tree["plan"] is None:
        raise HTTPException(status_code=404, detail=f"计划 id={plan_id} 不存在")
    return tree


# ---------- LLM 提供商与调用记账（T10） ----------
#
# 铁律（SPEC 第 10 节第 4 条）：密钥**只写不读**——进来的 api_key 只往库里落，
# 出去的任何响应里只有掩码。所以下面所有返回值都走 `llm.public_provider`，
# 不要把 `llm.get_provider` 拿到的原始行直接返回。

class ProviderIn(BaseModel):
    name: str = Field(min_length=1, description="给这家起的名字，全库唯一")
    base_url: str | None = Field(default=None, description="形如 https://api.example.com/v1")
    api_key: str | None = Field(default=None, description="只写不读：接口永不回传明文")
    default_model: str | None = None
    set_as_default: bool = False


class ProviderPatch(BaseModel):
    """改一家 provider。字段都可选，只改传了的。

    `api_key` 不传（或传空串）表示**不改密钥**——界面只拿得到掩码，回填不了明文，
    若把"没传"当成"清空"，改个名字就会顺手把钥匙擦掉。
    """

    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    default_model: str | None = None
    enabled: bool | None = None
    set_as_default: bool = False


def _require_provider(conn: sqlite3.Connection, provider_id: int) -> None:
    if llm.get_provider(conn, provider_id) is None:
        raise HTTPException(status_code=404, detail=f"provider id={provider_id} 不存在")


@app.get("/api/providers")
def get_providers(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """列出已配置的提供商。**只回掩码**，明文密钥永不出库。"""
    return {"providers": llm.list_providers(conn)}


@app.post("/api/providers", status_code=201,
          responses={409: {"description": "已经有一家同名 provider"}})
def post_provider(payload: ProviderIn, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """新增一家提供商。密钥只往库里写，响应里只有掩码。"""
    try:
        provider_id = llm.create_provider(
            conn,
            name=payload.name,
            base_url=payload.base_url,
            api_key=payload.api_key,
            default_model=payload.default_model,
        )
        if payload.set_as_default:
            llm.update_provider(conn, provider_id, set_as_default=True)
    except llm.DuplicateProvider as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except llm.LlmError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return llm.public_provider(llm.get_provider(conn, provider_id))  # type: ignore[arg-type]


@app.put("/api/providers/{provider_id}")
def put_provider(
    provider_id: int, payload: ProviderPatch, conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """改一家提供商：改字段、启用停用、设为默认。"""
    _require_provider(conn, provider_id)
    try:
        llm.update_provider(
            conn,
            provider_id,
            name=payload.name,
            base_url=payload.base_url,
            api_key=payload.api_key,
            default_model=payload.default_model,
            enabled=payload.enabled,
            set_as_default=payload.set_as_default,
        )
    except llm.DuplicateProvider as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except llm.LlmError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return llm.public_provider(llm.get_provider(conn, provider_id))  # type: ignore[arg-type]


@app.delete("/api/providers/{provider_id}")
def delete_provider(provider_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """删一家提供商。**它的调用记账不删**——账是历史，得留着。"""
    _require_provider(conn, provider_id)
    llm.delete_provider(conn, provider_id)
    return {"id": provider_id, "deleted": True}


@app.post("/api/providers/{provider_id}/test")
def test_provider(provider_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """发一个最小请求测连通性。

    **失败也返回 200**：「通不通」是它的返回值，不是 HTTP 层错误。配错 base_url
    或密钥失效是常事，前端要的是「结果 + 原因」，而不是一个异常。
    """
    _require_provider(conn, provider_id)
    return llm.test_provider(conn, provider_id)


@app.get("/api/llm-calls")
def get_llm_calls(limit: int = 50, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """调用记账：最近流水 + 按周汇总。

    为什么不设预算上限就必须有这个：不设上限的前提是「看得见花了多少」。
    """
    return {"calls": llm.list_calls(conn, limit=limit), "by_week": llm.call_summary_by_week(conn)}
