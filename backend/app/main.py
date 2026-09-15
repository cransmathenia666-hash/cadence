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
from fastapi.responses import JSONResponse, Response
from fastapi.utils import is_body_allowed_for_status_code
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db, ledger, plan

app = FastAPI(
    title="cadence",
    version="0.1.0",
    description="学习决策与跟进 Agent：方向层 + 计划表 + 跟进",
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
