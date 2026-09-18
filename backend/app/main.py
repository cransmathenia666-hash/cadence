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

from . import advisor, blueprint, config, db, dialogue, ledger, llm, plan, profile, proposals

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
    level: Literal["stage", "checkpoint", "task"]
    title: str = Field(min_length=1)
    parent_id: int | None = Field(default=None, description="检查点 / 任务必填：所属阶段")
    deliverable: str | None = Field(default=None, description="阶段用：可验证的交付物")
    due_date: date | None = Field(default=None, description="计划完成日，写 ISO 日期（如 2026-09-30）；不带就不进落后量")
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
    "category": "档案类别",
    "content": "档案内容",
    "reason": "理由",
    "accept": "是否采纳",
    "kind": "类型",
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
    """建一个阶段、检查点（周打卡）或任务。

    三级结构的一致性由后端把关，不信前端：阶段不能有 parent_id；
    检查点与任务必须有 parent_id、且指向同一个计划里的某个阶段。
    重复提交（前端双击、请求重发）由 `plan.add_node` 挡下并返回 409。
    """
    if plan.resolve_plan(conn, payload.plan_id) is None:
        raise HTTPException(status_code=404, detail=f"计划 id={payload.plan_id} 不存在")

    if payload.level in ("checkpoint", "task"):
        kind = "检查点" if payload.level == "checkpoint" else "任务"
        if payload.parent_id is None:
            raise HTTPException(status_code=400, detail=f"{kind}必须指定 parent_id（所属阶段）")
        parent = plan.get_node(conn, payload.parent_id)
        if parent is None or parent["level"] != "stage":
            raise HTTPException(status_code=400, detail="parent_id 必须指向一个阶段节点")
        if int(parent["plan_id"]) != payload.plan_id:
            raise HTTPException(status_code=400, detail=f"{kind}必须和所属阶段在同一个计划里")
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


# ---------- 任务与交付物（三级结构，SPEC 决策 30–32） ----------
#
# 三个动作各认自己的层级：打勾 / 跳过只对任务，提交交付物只对阶段。
# 状态迁移与留痕归 `plan.py`（状态机 + 台账），这里只翻译 404 / 400。

class TaskSkipIn(BaseModel):
    reason: str = Field(min_length=1, description="为什么跳过——进台账，必填")


class DeliverableIn(BaseModel):
    url: str = Field(min_length=1, description="交付物链接：仓库 / URL / 录屏都行")
    note: str = Field(min_length=1, description="一句话说明这份交付物")


def _require_node(conn: sqlite3.Connection, node_id: int) -> None:
    if plan.get_node(conn, node_id) is None:
        raise HTTPException(status_code=404, detail=f"节点 id={node_id} 不存在")


@app.post("/api/plan/nodes/{node_id}/check")
def post_task_check(node_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """任务打勾：一步到完成、不写理由（决策 31）。只对任务层有效。"""
    _require_node(conn, node_id)
    try:
        return plan.check_task(conn, node_id)
    except plan.PlanError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/plan/nodes/{node_id}/skip")
def post_task_skip(
    node_id: int, payload: TaskSkipIn, conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """跳过任务：跳过算完成的一种，但必须写一句理由（它是裁定，要留痕）。"""
    _require_node(conn, node_id)
    try:
        return plan.skip_task(conn, node_id, payload.reason)
    except plan.PlanError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/plan/nodes/{node_id}/deliverable", status_code=201)
def post_deliverable(
    node_id: int, payload: DeliverableIn, conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """提交阶段的交付物：独立动作、可重新提交（旧值留痕）。只对阶段有效。"""
    _require_node(conn, node_id)
    try:
        return plan.submit_deliverable(conn, node_id, payload.url, payload.note)
    except plan.PlanError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


class NodeFieldsIn(BaseModel):
    """改一个已经建好的节点的字段（决策 38：原地改 + 台账流水，id 不变）。

    只传要改的字段；**传空字符串表示清空**（交付物 / 截止日可以清，标题不许清）。
    """

    title: str | None = Field(default=None, description="新的标题")
    deliverable: str | None = Field(default=None, description="阶段要交的东西（只对阶段有效）")
    due_date: str | None = Field(default=None, description="YYYY-MM-DD；空字符串 = 清掉日期（清了就不进落后量）")
    reason: str = Field(min_length=1, description="为什么改——进台账，回答「为什么改」")


@app.post("/api/plan/nodes/{node_id}/fields")
def post_node_fields(
    node_id: int, payload: NodeFieldsIn, conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """改一个已经建好的节点（标题 / 交付物 / 截止日）。

    这是 2026-09-18（T30）补上的那条写入口：在此之前只有「建节点」与「改状态」，
    建好之后交付物写不进去、截止日挪不动——T26 的「同名阶段复用但交付物写不进去」
    与 T14 的「重排提案只能记方向」都卡在这里。

    **id 不变、引用不断**：走台账的一条 `update_fields` 流水（改前改后 + 理由），
    不是「取代」。
    """
    _require_node(conn, node_id)
    try:
        return plan.update_node_fields(
            conn,
            node_id,
            reason=payload.reason,
            title=payload.title,
            deliverable=payload.deliverable,
            due_date=payload.due_date,
        )
    except plan.PlanError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


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


# ---------- 计划列表与生命周期（T24 多计划，SPEC 决策 33 ③；T27 拆四态） ----------
#
# 默认只列**进行中**的计划；暂停（paused）、收尾（closed）、作废（void）的进历史、
# 要 `include_inactive` 才看得到。四态的分界线是「能不能回到进行中」：暂停与收尾能
# （`/reopen` 一条路），作废不能——它是台账的单向门。作废理由必填、收尾与暂停可选，
# 四者都让计划从默认列表消失，但历史与理由都留着。

class PlanCloseIn(BaseModel):
    reason: str | None = Field(default=None, description="为什么收尾（可选，进台账）")


class PlanVoidIn(BaseModel):
    reason: str = Field(min_length=1, description="为什么作废——进台账，必填")


class PlanPauseIn(BaseModel):
    reason: str | None = Field(default=None, description="为什么暂停（可选，默认「暂时不做了」）")


class PlanReopenIn(BaseModel):
    reason: str | None = Field(default=None, description="为什么继续 / 重开（可选，默认「继续做」）")


@app.get("/api/plans")
def get_plans(
    include_inactive: bool = False, conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """列出计划（切换器用）。默认只给进行中的；`include_inactive=true` 连收尾/作废的一起给。"""
    return {"plans": plan.list_plans(conn, include_inactive=include_inactive)}


@app.post("/api/plans/{plan_id}/close")
def post_plan_close(
    plan_id: int, payload: PlanCloseIn, conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """收尾一个计划：做完了，进历史，不再出现在默认列表里。可重复调用。"""
    try:
        return plan.close_plan(conn, plan_id, payload.reason)
    except plan.PlanError as error:
        raise HTTPException(status_code=404 if "不存在" in str(error) else 400, detail=str(error)) from error


@app.post("/api/plans/{plan_id}/void")
def post_plan_void(
    plan_id: int, payload: PlanVoidIn, conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """作废一个计划：这件事根本不该做。**单向门**，理由必填、进台账。"""
    try:
        return plan.void_plan(conn, plan_id, payload.reason)
    except plan.PlanError as error:
        raise HTTPException(status_code=404 if "不存在" in str(error) else 400, detail=str(error)) from error
    except ledger.LedgerError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/plans/{plan_id}/pause")
def post_plan_pause(
    plan_id: int, payload: PlanPauseIn, conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """暂停一个计划：暂时不做，进历史但随时能「继续做」回来。理由可选。"""
    try:
        return plan.pause_plan(conn, plan_id, payload.reason)
    except plan.PlanError as error:
        raise HTTPException(status_code=404 if "不存在" in str(error) else 400, detail=str(error)) from error


@app.post("/api/plans/{plan_id}/reopen")
def post_plan_reopen(
    plan_id: int, payload: PlanReopenIn, conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """把一个暂停 / 收尾的计划放回进行中。作废的不给重开（要重新做就新建一个）。"""
    try:
        return plan.reopen_plan(conn, plan_id, payload.reason)
    except plan.PlanError as error:
        raise HTTPException(status_code=404 if "不存在" in str(error) else 400, detail=str(error)) from error


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


# ---------- 决策入口：四问判断（T12） ----------
#
# 这一段的形状：**LLM 只产出提案**。接口负责把你的输入记一行、把判断跑出来、落一条
# `pending` 提案；档案与计划一个字都不改——改动要等你在 T14 的界面里裁定。

class RequestIn(BaseModel):
    kind: Literal["evaluate", "search"] = Field(
        default="evaluate",
        description="evaluate=判断某个资料值不值得学（四问）；search=我不知道该学什么（候选清单）",
    )
    raw_text: str = Field(
        min_length=1, description="你的原话，例如「我看到一个 Rust 异步编程教程」或「我不知道该学什么」"
    )
    plan_id: int | None = Field(
        default=None,
        description="这一轮针对哪个计划（search 用）；不传 = 「新方向（不属于任何计划）」",
    )


class VerdictIn(BaseModel):
    """对一条候选表态。

    否决必须写理由（业务层判，缺理由回 400）：它会同时进 `reject_reason` 列与台账流水，
    并成为下一次「找」的禁区——理由写得越具体，禁区越管得住。

    采纳要能落到一个计划上（SPEC 决策 33 ②）：候选自带的计划归属优先；归属为空
    （「新方向」）时用 `plan_id` 指明进哪个计划，两者都没有就报 400——不偷偷落最新。
    """

    accept: bool = Field(description="true = 采纳，false = 否决")
    reason: str | None = Field(default=None, description="否决必填：进台账、并成为下次的禁区")
    plan_id: int | None = Field(
        default=None, description="采纳时用：候选没有计划归属时，指明进哪个计划"
    )


@app.get("/api/profile")
def get_profile(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """长期档案的当前有效值（五类），外加哪几类还空着。

    为什么要报「哪几类空着」：四问里 ②③④ 主要靠 `current_state` / `short_term_goal` /
    `life_habit` / `life_log`，这几类没记录时答案只能是「依据不足」——与其让你纳闷
    它为什么答得空，不如接口直接把缺口摊开。
    """
    return advisor.read_profile(conn)


@app.post("/api/requests", status_code=201)
def post_request(payload: RequestIn, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """提交每轮输入。两种形态按 `kind` 分流，都不写档案、不写计划。

    - `evaluate`（B 入口）：记一行输入 → 跑四问（最多调 2 次模型）→ 落一条 `pending` 提案。
    - `search`（A 入口，T13）：记一行输入 → 生成 3–5 条带排序的候选（最多调 2 次模型）
      → 落成 `proposed` 候选，等你在界面上采纳 / 否决。

    `plan_id` 是这一轮针对的计划（SPEC 决策 33 ①）：带上它，「找」会把该计划的当前阶段
    当上下文；不传 = 「新方向（不属于任何计划）」。

    `clarify` 是「找」的追问槽位（SPEC 决策 35 ②）：模型觉得档案缺了某类信息时会先问一句。
    它**不落库**——只在这次响应里给你，你回答的那句话就是下一轮的输入。
    """
    request_id = advisor.record_request(conn, payload.kind, payload.raw_text, payload.plan_id)
    try:
        if payload.kind == "search":
            found = advisor.find_candidates(conn, payload.raw_text, plan_id=payload.plan_id)
            candidate_ids = advisor.propose_candidates(
                conn, request_id=request_id, result=found
            )
            return {
                "request_id": request_id,
                "kind": "search",
                "plan_id": found["plan_id"],
                "candidate_ids": candidate_ids,
                "candidates": found["candidates"],
                "recommended_start": found["recommended_start"],
                "start_reason": found["start_reason"],
                "clarify": found["clarify"],
                "source": found["source"],
                "profile_basis": found["profile_basis"],
                "banned_titles": found["banned_titles"],
                "feedback_lines": found["feedback_lines"],
                "calls": found["calls"],
            }
        result = advisor.judge(conn, payload.raw_text)
    except advisor.AdvisorError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except llm.LlmError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    proposal_id = advisor.propose(
        conn, request_id=request_id, raw_text=payload.raw_text, result=result
    )
    return {
        "request_id": request_id,
        "proposal_id": proposal_id,
        "kind": advisor.MATERIAL_JUDGMENT_KIND,
        "judgment": result["judgment"],
        "profile_basis": result["profile_basis"],
        "calls": result["calls"],
    }


@app.get("/api/candidates")
def get_candidates(
    request_id: int | None = None, conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """取候选清单。不传 `request_id` 就取最近一轮有候选的那次「找」。

    没有候选时返回 `request_id: null` 与空列表——「还没问过」不是错误，前端不必先探一次。
    返回里带每条候选的状态：界面要能看出哪些是自己已经否掉过的（去重是「不再推荐」，
    不是「假装它没发生过」）。
    """
    return advisor.list_candidates(conn, request_id)


@app.post("/api/candidates/{candidate_id}/verdict",
          responses={409: {"description": "这条候选已经裁定过了，或采纳落不了阶段（没有可落的计划 / 有同名未收尾阶段）"}})
def post_candidate_verdict(
    candidate_id: int, payload: VerdictIn, conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """采纳 / 否决一条候选。

    否决留痕（`reject_reason` + 台账流水），并让它的标题成为下一次「找」的禁区——
    这是成功标准 2 后半句「已被否决的候选不再出现」的入口。
    采纳落进**候选自带的计划归属**（SPEC 决策 33 ②）；归属为空时用 `plan_id` 指明，
    两者都没有回 409 并保持 proposed 可重试——不偷偷落最新。
    """
    try:
        return advisor.decide_candidate(
            conn,
            candidate_id,
            accept=payload.accept,
            reason=payload.reason,
            plan_id=payload.plan_id,
        )
    except advisor.CandidateNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except advisor.CandidateConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except advisor.AdvisorError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


# ---------- 待裁定提案（T14：提案裁定页的后端） ----------
#
# 提案是「AI 算出结论、但只有你点头才算数」的东西（SPEC 第 8 节铁律）。
# T29 起只剩三类（`material_judgment` / `profile_change` / `plan_blueprint`），
# 规则自动产的两类（`stage_advance` / `plan_replan`）已整类删除；**T31 又加了第四类
# `plan_change`**（计划对话里附带的一条可执行建议，决策 39）。来源与「批准」各自的
# 含义写在 `app/proposals.py` 的模块说明里。这里只收参数、把领域错误翻成状态码：
# 不存在 404、已裁定过 409、规则拒绝 400。


class ProposalDecideIn(BaseModel):
    """裁定一条提案。

    `approved=False` 必须写理由（业务层判，缺理由回 400）。

    T29 起**没有 `option` 了**：唯一需要「选一个方向」的 `plan_replan` 已整类删除。
    """

    approved: bool = Field(description="true = 批准（可能带副作用），false = 驳回")
    reason: str | None = Field(default=None, description="驳回必填；批准时可选，都进台账")
    selected: list[str] | None = Field(
        default=None,
        description=(
            "批准 plan_blueprint 时用：勾中的阶段 / 任务下标，"
            "形如 [\"0\", \"1.2\"]（阶段整段写 \"下标\"，单个任务写 \"阶段下标.任务下标\"，从 0 起）。"
            "没勾的部分直接丢弃；不传 = 整份采纳"
        ),
    )


@app.get("/api/proposals")
def get_proposals(kind: str | None = None, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """待裁定提案，按提出顺序排；`kind` 传了就只取那一类（四类之一）。"""
    return proposals.list_pending(conn, kind)


@app.get("/api/judgments")
def get_judgments(limit: int = 20, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """判资料的**只读历史**：已经裁定过的四问判断，最近的在前（T29 的 `/judge` 页用它）。

    只读是刻意的：裁定环节在 `/proposals` 那边（`material_judgment` 批准只记账），
    这里只负责让人回看「上次那份资料当时怎么判的」。
    """
    return proposals.list_decided(conn, advisor.MATERIAL_JUDGMENT_KIND, limit)


@app.post("/api/proposals/{proposal_id}/decide",
          responses={409: {"description": "这条提案已经裁定过了，或目标计划已收尾"}})
def post_proposal_decide(
    proposal_id: int, payload: ProposalDecideIn, conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """批准 / 驳回一条提案。

    批准不必都改东西：`effect` 字段说明这一次到底动了什么——`blueprint_built` 是
    **按勾选把树建进计划**（阶段 / 任务，`built` 里列出建了哪些），`profile_written` 是
    真往长期档案里写了一条（`written` 是哪一条），`node_updated` 是**原地改了一个已有节点
    的字段**（`updated` 里是改前改后，id 不变），`node_added` 是往计划里加了一件任务 /
    一个阶段（`added` 里是新建的那条），`recorded_only` 就是纯记账。驳回只留痕，不改任何
    业务数据——计划对话里那条「忽略」就走这里，理由固定「聊天里先不动」。
    """
    try:
        return proposals.decide(
            conn,
            proposal_id,
            approved=payload.approved,
            reason=payload.reason,
            selected=payload.selected,
        )
    except proposals.ProposalNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except blueprint.BlueprintNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except proposals.ProposalConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except blueprint.BlueprintConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (proposals.ProposalError, blueprint.BlueprintError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


# ---------- 对话式规划（T26：SPEC 决策 36） ----------
#
# 采纳一条候选之后、生成蓝图之前的那段对话。三条路由：看历史 / 聊一轮 / 出方案。
# 「采纳之后」是硬前提——没采纳就来聊会被回 409（见 blueprint._accepted_candidate）。
#
# 状态码口径同其它链路：不存在 404、与现状冲突 409（没采纳 / 计划定不下来 / 轮数到顶 /
# 计划已收尾）、业务规则拒绝 400（模型输出不合格、档案空着）。


class PlanChatIn(BaseModel):
    """聊一轮：我说一句话，模型回最多 3 个问题。"""

    candidate_id: int = Field(description="聊的是哪条候选（必须是已采纳的）")
    message: str = Field(min_length=1, description="你的这一句回答")
    plan_id: int | None = Field(
        default=None,
        description="这条候选没有计划归属（「新方向」）时用它指明落在哪个计划",
    )


class PlanBlueprintIn(BaseModel):
    """出方案：把上面聊清的意向落成一条 `pending` 蓝图提案。"""

    candidate_id: int
    plan_id: int | None = Field(default=None, description="同 `PlanChatIn.plan_id`")


@app.get("/api/plan-chat")
def get_plan_chat(
    candidate_id: int,
    plan_id: int | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """看这段对话：历史消息 + 聊了几轮 + 能不能出方案 + 有没有待裁定蓝图。

    追问的边界：`can_generate` 为 false 表示还没聊过——决策 36 要求先问清意向再出树。
    """
    try:
        return blueprint.view(conn, candidate_id, plan_id)
    except blueprint.BlueprintNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except blueprint.BlueprintConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except blueprint.BlueprintError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/plan-chat", status_code=201)
def post_plan_chat(payload: PlanChatIn, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """聊一轮：**每轮 1 次调用、最多 6 轮**（决策 6 修订 / 决策 36）。

    输出不合格时**不重试**（那一轮只给 1 次调用）：如实回 400，而你那句话已经留在
    对话里了——再说一句就接着走。
    """
    try:
        return blueprint.say(
            conn, payload.candidate_id, payload.message, plan_id=payload.plan_id
        )
    except blueprint.BlueprintNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except blueprint.BlueprintConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except blueprint.BlueprintError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except llm.LlmError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/plan-chat/blueprint", status_code=201)
def post_plan_blueprint(
    payload: PlanBlueprintIn, conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """出方案：1 次调用（不合格带原因重试 1 次），落成一条 `pending` 蓝图提案。

    **树 = 版本**：同一个计划同时只有一份待裁定蓝图，新的一版落库时把旧的标成
    `superseded`（业务终态，不走台账的取代——决策 22 禁止对提案做生命周期操作）。
    `superseded_ids` 里列出的就是被它顶掉的那一版。
    """
    try:
        return blueprint.generate_blueprint(
            conn, payload.candidate_id, plan_id=payload.plan_id
        )
    except blueprint.BlueprintNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except blueprint.BlueprintConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except blueprint.BlueprintError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except llm.LlmError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


# ---------- 计划级对话（T28：蓝图落地之后接着聊） ----------
#
# 与 `/api/plan-chat`（定方向的那段短程对话）分工不同：这一段跟着**计划**走，
# 不限轮数（成本闸是历史字符上限），聊的是执行期的事。三条路由：看历史 / 聊一句 /
# 把聊出的变化提炼成档案变更提案。**T31 起**聊一句还能附一条可执行建议（决策 39），
# 建议落成 `kind=plan_change` 的待裁定提案——改计划仍要经你当场裁定（在计划页点
# 「确认」），这一段对话自己一个字段也写不动。
#
# 状态码口径同其它链路：计划不存在 404、还没聊过就提炼 409、模型输出不合格 400。


class DialogueIn(BaseModel):
    """聊一句。"""

    plan_id: int = Field(description="聊的是哪个计划（必须存在）")
    message: str = Field(min_length=1, description="你的这一句")


class DialogueExtractIn(BaseModel):
    """把这段对话里聊出的变化提炼成待裁定的档案变更提案。"""

    plan_id: int


@app.get("/api/plan-dialogue")
def get_plan_dialogue(plan_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """看这段对话：历史消息 + 聊了几句 + 能不能提炼档案提案。

    T31 起每条助手消息另带 `suggestion`（它那一轮提的建议与那条提案），界面据此渲染确认条。
    """
    try:
        return dialogue.view(conn, plan_id)
    except dialogue.DialogueNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except dialogue.DialogueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/plan-dialogue", status_code=201)
def post_plan_dialogue(payload: DialogueIn, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """聊一句：人话 + 最多一条可执行建议（决策 39）。

    输出是信封（`{"reply": ..., "suggestion": ...}`），形状不合格带原因重试 1 次
    （每轮最坏 2 次调用）；建议合格就落一条 `plan_change` 待裁定提案，**确认 / 忽略在计划页**。
    助手那侧存进库里的仍是**人话**。
    """
    try:
        return dialogue.say(conn, payload.plan_id, payload.message)
    except dialogue.DialogueNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except dialogue.DialogueConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except dialogue.DialogueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except llm.LlmError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/plan-dialogue/profile-proposals", status_code=201)
def post_dialogue_profile_proposals(
    payload: DialogueExtractIn, conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """把这段对话里聊出的变化提炼成待裁定的档案变更提案（你点按钮才发生，不是每轮自动）。

    批准那条提案才会真的改档案（`proposals.decide` 的 profile_change 分支，T28-1）。
    """
    try:
        return dialogue.propose_profile_changes(conn, payload.plan_id)
    except dialogue.DialogueNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except dialogue.DialogueConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except dialogue.DialogueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except llm.LlmError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


# ---------- 长期档案的写入（档案录入） ----------
#
# 档案此前只有读（GET /api/profile）：这三条写入口补上「档案只有读、没有写」的缺口。
# 三条全走台账（SPEC 第 8 节铁律）：新增落 active、修改是「取代」（旧值留痕、可查为什么改）、
# 删除是「作废」（void，必须写理由）——不存在物理删除，历史永远能回答「当时为什么这么写」。
# category 只收 advisor.PROFILE_CATEGORIES 那五个令牌：库里没有约束，这层边界是
# 「缺失类别」判断还准不准的最后一道闸。
#
# 状态码口径（同 SPEC 第 11 节）：入参不合法 422（Pydantic 拦）、对象不存在 404、
# 与现状冲突（条目已被取代/作废）409、业务规则拒绝 400。


class ProfileItemIn(BaseModel):
    # Literal 写死这五个令牌、不引用 advisor.PROFILE_CATEGORIES 动态生成：
    # 校验错误要能在 OpenAPI 契约里枚举出来，前端与 /docs 才看得见合法取值。
    category: Literal["life_habit", "life_log", "current_state", "short_term_goal", "long_axis"] = Field(
        description="档案类别，五个约定令牌之一（与 advisor.PROFILE_CATEGORIES 一致）"
    )
    content: str = Field(min_length=1, description="一两句话的提炼结论，不是原始资料")


class ProfileItemUpdate(BaseModel):
    """取代一条档案：旧值标记 superseded、新值成为当前有效值。

    只许改 content；想换类别就作废旧条目、另立新条目——类别是档案的坐标，
    「原地换坐标」会让历史流水对不上号。
    """

    content: str = Field(min_length=1, description="新的内容")
    reason: str = Field(min_length=1, description="为什么改——写进台账，回答「为什么改」")


class ProfileItemVoidIn(BaseModel):
    reason: str = Field(min_length=1, description="为什么作废——写进台账")


def _require_active_profile_item(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row:
    """404 与 409 的分流在这里做：不存在是 404，存在但已不是当前有效值是 409。

    判据本身在 `app/profile.py`（T28 抽出去的），这里只把两种错翻成状态码——
    同一道闸还要被「批准档案变更提案」那条路用，规则不该有两份。
    """
    try:
        return profile.require_active(conn, item_id)
    except profile.ProfileNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except profile.ProfileConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/profile", status_code=201,
          responses={409: {"description": "同类别下已有一条一字不差的当前有效条目（多半是重复提交）"}})
def post_profile_item(payload: ProfileItemIn, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """往档案里补一条。同一类别允许多条并存（比如两条短期目标），互相不挤掉。

    但**一字不差的重复**要挡（同 T19 防重复提交的思路）：档案是给 AI 引用的判据，
    重复条目会让它被反复引用、还会在页面上越积越多。判重只看当前有效的条目——
    作废后重新填一样的文字是正当需求（同「已完成节点不挡同名重建」）。
    """
    content = payload.content.strip()
    try:
        item_id = profile.create_item(conn, category=payload.category, content=content, actor="user")
    except profile.ProfileConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (profile.ProfileError, ledger.LedgerError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"id": item_id, "category": payload.category, "content": content}


@app.put("/api/profile/{item_id}")
def put_profile_item(
    item_id: int, payload: ProfileItemUpdate, conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """改一条档案的内容：实为台账「取代」——旧值不删，before/after 与理由都留在流水里。"""
    row = _require_active_profile_item(conn, item_id)  # 404 / 409 在这一步分流
    try:
        new_id = profile.supersede_item(
            conn, item_id, content=payload.content, reason=payload.reason
        )
    except (profile.ProfileError, ledger.LedgerError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {
        "id": new_id,
        "superseded": item_id,
        "category": row["category"],
        "content": payload.content.strip(),
    }


@app.post("/api/profile/{item_id}/void")
def void_profile_item(
    item_id: int, payload: ProfileItemVoidIn, conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """作废一条档案（不物理删除）：条目从此不在 GET /api/profile 里出现，台账留痕。"""
    try:
        profile.void_item(conn, item_id, reason=payload.reason)
    except profile.ProfileNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except profile.ProfileConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (profile.ProfileError, ledger.LedgerError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"id": item_id, "voided": True}
