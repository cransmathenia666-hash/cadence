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

from . import (
    advisor,
    blueprint,
    config,
    db,
    dialogue,
    export,
    ledger,
    llm,
    memory,
    notify,
    plan,
    profile,
    proposals,
)
from .db import atomic

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
    "clarify_answer": "对追问的回答",
    "clarify_request_id": "追问所属轮次",
    "thread_id": "探索线程",
    "allow_shape_switch": "形态切换确认",
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


class NodeReopenIn(BaseModel):
    reason: str = Field(min_length=1, description="为什么放回来——进台账，必填")


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
    """跳过：**阶段与任务**都能跳，跳过算完成的一种，但必须写一句理由（它是裁定，要留痕）。

    2026-09-20（T35，决策 41）起放开到阶段：「建出来之后不要某一步」由此有了出口——
    在方向层否决是永久拉黑，太重；蓝图勾选只是「本版不建」；已经建出来的这一步要收回，
    就是这里。周打卡不给跳（它是节奏节点，报告里本来就有「跳过」这个状态）。
    """
    _require_node(conn, node_id)
    try:
        return plan.skip_node(conn, node_id, payload.reason)
    except plan.PlanError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/plan/nodes/{node_id}/reopen")
def post_node_reopen(
    node_id: int, payload: NodeReopenIn, conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """放回：把**已完成 / 已跳过的阶段或任务**退回「进行中」（2026-09-28 补的出口）。

    为什么要有它：打勾是一键动作（决策 31），手滑一次原来就没有回头路。与 `/skip`
    同一层口径（阶段与任务）、同一道理由闸（理由进台账）。周打卡不给放回。
    """
    _require_node(conn, node_id)
    try:
        return plan.reopen_node(conn, node_id, payload.reason)
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
    """收尾一个计划：做完了，进历史，不再出现在默认列表里。可重复调用。

    收尾顺手**登记一条待扫描**（记忆系统，方案第 6 节）：这个计划的经历该提炼一遍了。
    刻意**不在这里同步调模型**——收尾是一次业务动作，不该被一次模型调用拖住、也不该
    因为模型不通而失败；由每周任务或记忆页之后来把它做掉。
    """
    try:
        result = plan.close_plan(conn, plan_id, payload.reason)
    except plan.PlanError as error:
        raise HTTPException(status_code=404 if "不存在" in str(error) else 400, detail=str(error)) from error
    scan_id = memory.register_pending_scan(conn, plan_id) if result["changed"] else None
    return {**result, "memory_scan_id": scan_id}


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
    clarify_answer: str | None = Field(
        default=None,
        description=(
            "回答上一轮追问槽的那句话（search 用，2026-09-20 T36 新增）。"
            "给了就把它与**原问题**拼成这一轮的输入（不拼上原问题，下一轮模型就丢了前提），"
            "并把这条追问标记成「已答」进反馈流水，后续轮次不再重复问同一件事。"
            "没有待答的追问时，这句话会作为补充缀在 raw_text 后面，不会被丢掉"
        ),
    )
    clarify_request_id: int | None = Field(
        default=None,
        description="回答哪一轮的追问；前端有明确轮次时传入，避免同计划多轮串台",
    )
    thread_id: int | None = Field(
        default=None,
        description=(
            "延续哪段探索线程（线程头请求的 id，search 用，决策 44 ①）。"
            "不传 = 开一段新探索：同句原话的两次提问也是两段独立线程，不会被合并"
        ),
    )
    allow_shape_switch: bool = Field(
        default=False,
        description=(
            "用户已确认切换「多方向 / 一路径」形态的那一轮置 true（search 用，决策 44 ③）："
            "后端会核对这段线程确有待确认的切换提案，且模型给的形态与提案目标一致才放行"
        ),
    )
    redo: bool = Field(
        default=False,
        description=(
            "用户明确要求重做推荐的那一轮置 true（search 用，决策 44 ①）：只有 redo 轮落新候选"
            "才让同线程上一版未裁定候选过期；普通续聊里模型给的候选照常落库、旧版原样保留"
        ),
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


def _search_round(conn: sqlite3.Connection, payload: RequestIn) -> dict:
    """「找方向」一轮的完整编排（决策 44 ①，I-01～I-05 + 复核整改）。

    顺序就是失败契约（V-02）：线程确认（答追问时原子占用）→ 拼输入（不落状态，续聊的
    原题由后端从线程头取，不信任客户端重发）→ 落输入行 → 调模型 → 候选落库 →
    追问状态收尾（置已答 + 记新追问 + 记形态切换提案）。

    - 线程确认失败 / 并发抢占失败（409）：什么都没写（或占用根本没抢到），用户回原线程或稍后重试。
    - 形态切换确认（allow_shape_switch）**不是客户端说了算**：放行前核对本段线程确有
      待确认的切换提案，模型给的形态还要与提案目标一致，否则 409 / 判不合格。
    - 模型失败（400）：**占用释放、输入行已留痕、要答的追问仍未算已答、候选没有落**——
      可原样重试（失败必须放锁，否则重试会被自己的残留占用挡住）。
    - 意图分流（II-01）：chat / need_info 轮不落候选；candidates 轮只有 `redo`（用户明确
      要求重做）才更替同线程旧未裁定候选，普通轮落了候选也不动旧版（复核整改）。
    """
    # 回答要先算出来：resolve_thread 靠它判断「这轮要不要原子占用那条追问」
    answer = str(payload.clarify_answer or "").strip() or None
    try:
        thread = advisor.resolve_thread(
            conn,
            thread_id=payload.thread_id,
            plan_id=payload.plan_id,
            clarify_request_id=payload.clarify_request_id,
            answer=answer,
        )
    except advisor.ThreadConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    pending = thread.get("pending_clarify")
    # 「答追问」这轮从 resolve_thread 起就原子占用了那条追问；以下任何失败路径都要放锁
    claimed = pending is not None and answer is not None

    def _release() -> None:
        if claimed:
            advisor.release_clarify_claim(conn, int(pending["request_id"]), thread.get("claim_token"))

    # 形态切换的放行核验在调模型**之前**：客户端布尔值只算申请，凭据是线程里那份
    # 待确认提案（它还给出了切换的目标形态，模型给的必须一致）。
    thread["request_id"] = None
    thread["allow_shape_switch"] = bool(payload.allow_shape_switch)
    thread["redo"] = bool(payload.redo)
    if payload.allow_shape_switch:
        proposal = advisor.pending_shape_change(conn, thread.get("thread_head"))
        if proposal is None:
            _release()
            raise HTTPException(
                status_code=409,
                detail="这段探索里没有待确认的形态切换——先让模型说明它为什么想换，再决定切不切",
            )
        thread["expected_shape"] = proposal["to"]

    try:
        model_input = advisor.build_round_input(
            conn, utterance=payload.raw_text, answer=answer, thread=thread
        )
        request_id = advisor.record_request(
            conn, "search", model_input, payload.plan_id, thread_id=thread["thread_id"], utterance=payload.raw_text
        )
        # 新线程：身份刚刚定为本次输入行（record_request 里回填）；后续调用用同一份上下文
        thread["request_id"] = request_id
        if thread["thread_id"] is None:
            thread["thread_id"] = thread["thread_head"] = request_id

        found = advisor.find_candidates(
            conn, model_input, plan_id=payload.plan_id, thread=thread
        )
    except advisor.ThreadConflict as error:
        _release()
        if thread["request_id"] is not None:
            advisor.fail_round(conn, thread["request_id"])
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (advisor.AdvisorError, llm.LlmError) as error:
        _release()
        if thread["request_id"] is not None:
            advisor.fail_round(conn, thread["request_id"])
        raise HTTPException(
            status_code=400,
            detail=(
                f"{error}——你的这一轮输入已记录；要答的追问还没算已答、候选没有落库，"
                "可原样重试"
            ),
        ) from error

    # 候选、更替、追问消费和回话同属一轮成功结果。台账 helper 会 commit，
    # 因此在连接级原子单元里统一延迟提交，任何中途异常都不会留半轮结果。
    try:
        with atomic(conn):
            candidate_ids = advisor._propose_candidates_locked(
                conn,
                request_id=request_id,
                result=found,
                thread_id=thread["thread_id"],
                redo=bool(found.get("redo", payload.redo)),
            )
            advisor.commit_round(
                conn,
                request_id=request_id,
                thread=thread,
                answer=answer,
                clarify=found["clarify"],
                shape_change=found.get("shape_change"),
                intent=found["intent"],
                reply=found["reply"],
            )
    except advisor.ThreadConflict as error:
        _release()
        advisor.fail_round(conn, request_id)
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        _release()
        advisor.fail_round(conn, request_id)
        raise
    return {
        "request_id": request_id,
        "kind": "search",
        # 本轮归属的探索线程（新探索这里带回头请求 id，前端记下来后续带着它续问）
        "thread_id": thread["thread_id"],
        # 意图出口（II-01）：candidates=候选清单；chat/need_info=纯回应，看 reply
        "intent": found["intent"],
        # 本轮是否按「明确重做」处理（只有它才更替同线程旧未裁定候选）
        "redo": bool(found.get("redo", payload.redo)),
        "reply": found["reply"],
        "plan_id": found["plan_id"],
        "shape": found["shape"],
        "steps": found["steps"],
        "candidate_ids": candidate_ids,
        "candidates": found["candidates"],
        "recommended_start": found["recommended_start"],
        "start_reason": found["start_reason"],
        "clarify": found["clarify"],
        # 形态切换请求（II-02）：模型认为新事实推翻原判断时的说明，切不切由用户确认
        "shape_change": found["shape_change"],
        "source": found["source"],
        "profile_basis": found["profile_basis"],
        "banned_titles": found["banned_titles"],
        "feedback_lines": found["feedback_lines"],
        "calls": found["calls"],
    }


@app.post("/api/requests", status_code=201)
def post_request(payload: RequestIn, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """提交每轮输入。两种形态按 `kind` 分流，都不写档案、不写计划。

    - `evaluate`（B 入口）：记一行输入 → 跑四问（最多调 2 次模型）→ 落一条 `pending` 提案。
    - `search`（A 入口，T13）：先确认探索线程（`thread_id`，决策 44 ①）→ 记一行输入 →
      模型自报意图（chat / need_info / candidates）→ 只有 candidates 轮落候选
      （同线程版本更替）；need_info 轮的追问在本轮请求行上，**模型成功后才置已答**，
      失败可原样重试。

    `plan_id` 是这一轮针对的计划（SPEC 决策 33 ①）：带上它，「找」会把该计划的当前阶段
    当上下文；不传 = 「新方向（不属于任何计划）」。线程归属锁在它的头请求上——
    换计划续问会被 409 拒绝，不悄悄替你换线。

    `shape` 与 `steps` 是 2026-09-20 T34 新增的（SPEC 决策 41）：这一轮它给的是一条路
    还是几个互相竞争的方向；形态在线程上锁定，模型要改判先说明、由你确认
    （`allow_shape_switch`）后才会重算（决策 44 ③）。

    回答追问走 `clarify_answer`（T36）：原话保留在输入最前，追问与回答缀在后面。
    """
    if payload.kind == "search":
        return _search_round(conn, payload)

    try:
        result = advisor.judge(conn, payload.raw_text)
    except advisor.AdvisorError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except llm.LlmError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    request_id = advisor.record_request(conn, payload.kind, payload.raw_text, payload.plan_id)
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


class KeepShapeIn(BaseModel):
    thread_id: int = Field(description="探索线程头请求的 id")
    shape_change_request_id: int = Field(description="要保持原形态的确切提案轮次 id")


@app.post("/api/find/shape/keep")
def post_keep_shape(payload: KeepShapeIn, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    try:
        advisor.resolve_thread(
            conn, thread_id=payload.thread_id, plan_id=_thread_plan_id(conn, payload.thread_id),
            clarify_request_id=None,
        )
        return advisor.keep_shape_change(conn, payload.thread_id, payload.shape_change_request_id)
    except advisor.ThreadConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


def _thread_plan_id(conn: sqlite3.Connection, thread_id: int) -> int | None:
    row = conn.execute("SELECT plan_id FROM learning_request WHERE id = ?", (thread_id,)).fetchone()
    return None if row is None else row["plan_id"]


@app.get("/api/learning-requests")
def get_learning_requests(
    plan_id: int | None = None,
    limit: int = 50,
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """读取「找方向」历史轮次，供候选页恢复原问题与追问状态。"""
    return {"requests": advisor.list_search_requests(conn, plan_id=plan_id, limit=limit)}


@app.get("/api/candidates")
def get_candidates(
    request_id: int | None = None,
    thread_id: int | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """取候选清单。`request_id` 指定看哪一轮；`thread_id` 指定看哪段探索线程
    （取该线程内最近一条有候选的请求）；都不传就取最近一轮有候选的那次「找」。

    没有候选时返回 `request_id: null` 与空列表——「还没问过」不是错误，前端不必先探一次。
    返回里带每条候选的状态：界面要能看出哪些是自己已经否掉过的（去重是「不再推荐」，
    不是「假装它没发生过」）。
    """
    return advisor.list_candidates(conn, request_id, thread_id)


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
    的字段**（`updated` 里是改前改后，id 不变），`node_added` 是往计划里加了节点——一个阶段
    带它下面的任务、或者一批任务（`added.nodes` 里按建的顺序列出每一条），`recorded_only`
    就是纯记账。驳回只留痕，不改任何
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
# **2026-09-20 起走受控工具循环**（SPEC 决策 40）：业务事实不再每轮预装，改成它自己读
# （`agent_runtime.py` + `agent_tools.py`，四个只读工具）；每轮最多 3 次模型调用、
# 6 次只读工具调用，每一轮往 `agent_run` 留一行运行账（读了什么、为什么停下）。
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

    每条助手消息另带两个可选字段：`suggestion`（T31：它那一轮提的建议与那条提案，界面据此
    渲染确认条）与 `run`（决策 40：那一轮的运行账——读了哪几样、调了几次模型、为什么停下，
    界面据此渲染「本轮依据」）。刷新页面这些也还在。
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

    这一轮跑的是**受控工具循环**（决策 40）：它自己决定读哪几样资料（最多 6 次），
    最多 3 次模型调用（工具轮与「输出不合格重说一次」共用），撞上限或一直不合格就
    400 报错并如实说清读了什么、还缺什么——**一条提案都不落**，你这句话仍留在对话里。
    合格就落一条 `plan_change` 待裁定提案，**确认 / 忽略在计划页**。回执里
    `run` / `tools_used` / `stop_reason` 是这次新加的（老字段一个没动）；
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


# ---------- 记忆系统（2026-09-21，方案 `docs/记忆系统.md`） ----------
#
# 三层记忆：工作记忆在 `agent_runtime` 里（不落表）、经历记忆从既有六类记录现查
# （`GET` 不了全部，只有 Agent 的 `search_experiences` 工具与扫描用得上）、长期记忆就是
# 这里管的这两级——全局沿用档案表、计划内是 `plan_memory`。
#
# 三条口径，与方案一一对应：
# ① **系统只产候选**：扫描出来的东西进记忆收件箱（`proposal` 表的 `memory_change` 一类），
#    批准走既有的 `/api/proposals/{id}/decide`——候选裁定不另开口子。
# ② **批量批准只收「新增 + 用户陈述」**：取代会改掉已成立的事实、Agent 推断不是他说过的话、
#    彻底删除不可恢复，这三样一律逐条确认。
# ③ **彻底删除先预览再确认**：`purge-preview` 只读，`purge` 才动手，动完**全库复扫**一遍，
#    证明不了清干净就如实说「不敢宣称成功」。
#
# 状态码口径同 SPEC 第 11 节：入参不合法 422、对象不存在 404、与现状冲突（已被取代 / 作废 /
# 已经有完全一样的一条）409、业务规则拒绝 400。


class MemoryIn(BaseModel):
    """手工补一条长期记忆。类别按作用域二选一：全局用档案五类、计划内用约束 / 决定 / 偏好。"""

    scope: Literal["global", "plan"] = Field(description="全局长期记忆 / 计划内记忆")
    content: str = Field(min_length=1, description="一两句结论，不是原始资料")
    plan_id: int | None = Field(default=None, description="scope=plan 时必填")
    category: Literal["life_habit", "life_log", "current_state", "short_term_goal", "long_axis"] | None = Field(
        default=None, description="scope=global 时的类别（五个约定令牌之一）"
    )
    kind: Literal["constraint", "decision", "preference"] | None = Field(
        default=None, description="scope=plan 时的类别"
    )
    source_kind: Literal["user_stated", "agent_inferred", "legacy_manual"] = Field(
        default="user_stated", description="这条是谁说的：你说的是 user_stated"
    )
    fact_time: str | None = Field(default=None, description="这条事实说的是什么时候的状态（YYYY-MM-DD）")
    review_at: str | None = Field(default=None, description="到了这个时候进「待复核」（YYYY-MM-DD）")
    reason: str | None = Field(default=None, description="为什么记它——进台账")


class MemoryUpdateIn(BaseModel):
    """改一条记忆：实为台账「取代」，旧值留痕、理由必填。作用域决定改的是哪张表。"""

    scope: Literal["global", "plan"]
    content: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    fact_time: str | None = None
    review_at: str | None = None
    source_kind: Literal["user_stated", "agent_inferred", "legacy_manual"] | None = None


class MemoryScopeIn(BaseModel):
    scope: Literal["global", "plan"]


class MemoryVoidIn(MemoryScopeIn):
    reason: str = Field(min_length=1, description="为什么作废——写进台账")


class MemoryReviewIn(MemoryScopeIn):
    """待复核的两种处置（方案第 3 节）：还作数就往后推（renew），不再作数就作废（void）。"""

    decision: Literal["renew", "void"]
    review_at: str | None = Field(default=None, description="续期到哪一天；不给就按默认周期推")
    reason: str | None = None


class MemoryScanIn(BaseModel):
    plan_id: int | None = Field(default=None, description="扫哪个计划；不给就是全局那一批")
    trigger: Literal["manual", "weekly", "plan_close"] = Field(default="manual")


class MemoryBatchIn(BaseModel):
    proposal_ids: list[int] = Field(min_length=1, description="记忆收件箱里的提案编号")
    reason: str | None = None


def _memory_error(error: Exception) -> HTTPException:
    """把记忆层的三种错翻成状态码（口径同档案那三条路由）。"""
    if isinstance(error, memory.MemoryNotFound):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, memory.MemoryConflict):
        return HTTPException(status_code=409, detail=str(error))
    return HTTPException(status_code=400, detail=str(error))


@app.get("/api/memory")
def get_memory(plan_id: int | None = None, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """取记忆：全局的、这个计划的、以及两边「已到复核时间」的。

    到期的那组**同时列出来**（页面的「待复核」标签页就用它），但它们**默认不参与回答**
    ——Agent 那边由 `read_memories` 工具把到期的摘出去单独标。
    """
    try:
        return memory.list_memories(conn, plan_id=plan_id)
    except memory.MemoryError as error:
        raise _memory_error(error) from error


@app.get("/api/memory/inbox")
def get_memory_inbox(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """记忆收件箱：还没裁定的记忆候选。裁定走 `/api/proposals/{id}/decide`（同一个口子）。"""
    return memory.list_inbox(conn)


@app.get("/api/memory/scans")
def get_memory_scans(limit: int = 20, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """最近的扫描记录：扫了几条、落了几条候选、失败为什么。"""
    return {"scans": memory.scan_report(conn, limit=limit)}


@app.post("/api/memory/scan")
def post_memory_scan(
    payload: MemoryScanIn, conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """扫一批新经历，**只产候选**。

    先把登记下来还没跑的扫描（计划收尾留下的那些）做掉，再做这一次要的那批。
    单批有条数与字符上限，超出的留到下一批（`has_more` 为真）；某一批失败不影响别的批，
    也不落任何不完整的候选。
    """
    try:
        reports = memory.run_pending(conn)
        reports.append(memory.scan(conn, trigger=payload.trigger, plan_id=payload.plan_id))
    except memory.MemoryError as error:
        raise _memory_error(error) from error
    except llm.LlmError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"scans": reports}


@app.post("/api/memory", status_code=201)
def post_memory(payload: MemoryIn, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """手工补一条长期记忆。**你自己敲的这条不需要来源证据**——你就是来源。"""
    try:
        return memory.add_memory(
            conn,
            scope=payload.scope,
            content=payload.content,
            plan_id=payload.plan_id,
            category=payload.category,
            kind=payload.kind,
            source_kind=payload.source_kind,
            fact_time=payload.fact_time,
            review_at=payload.review_at,
            reason=payload.reason or "手工补记",
        )
    except (memory.MemoryError, ledger.LedgerError) as error:
        raise _memory_error(error) from error


@app.put("/api/memory/{memory_id}")
def put_memory(
    memory_id: int, payload: MemoryUpdateIn, conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """改一条记忆：走台账「取代」，旧值不删、改前改后与理由都留在流水里。"""
    try:
        return memory.supersede_memory(
            conn,
            payload.scope,
            memory_id,
            content=payload.content,
            reason=payload.reason,
            fact_time=payload.fact_time,
            review_at=payload.review_at,
            source_kind=payload.source_kind,
        )
    except (memory.MemoryError, ledger.LedgerError) as error:
        raise _memory_error(error) from error


@app.post("/api/memory/{memory_id}/void")
def post_memory_void(
    memory_id: int, payload: MemoryVoidIn, conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """作废一条记忆（普通纠错走这条，留痕）：从此不参与判断，旧值仍在台账里。"""
    try:
        memory.void_memory(conn, payload.scope, memory_id, reason=payload.reason)
    except (memory.MemoryError, ledger.LedgerError) as error:
        raise _memory_error(error) from error
    return {"id": memory_id, "scope": payload.scope, "voided": True}


@app.post("/api/memory/{memory_id}/review")
def post_memory_review(
    memory_id: int, payload: MemoryReviewIn, conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """处理「待复核」：还作数就把复核时间往后推，不再作数就作废。两条都留痕。"""
    try:
        if payload.decision == "renew":
            return {
                "decision": "renew",
                "memory": memory.renew_memory(
                    conn,
                    payload.scope,
                    memory_id,
                    review_at=payload.review_at,
                    reason=payload.reason,
                ),
            }
        memory.void_memory(
            conn,
            payload.scope,
            memory_id,
            reason=str(payload.reason or "").strip() or "复核后确认不再作数",
        )
    except (memory.MemoryError, ledger.LedgerError) as error:
        raise _memory_error(error) from error
    return {"decision": "void", "id": memory_id, "scope": payload.scope, "voided": True}


@app.post("/api/memory/batch-approve")
def post_memory_batch_approve(
    payload: MemoryBatchIn, conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """批量批准记忆候选：**只收「新增 + 用户明确陈述」**。

    其余（取代、Agent 推断、彻底删除）进 `skipped` 并写明为什么——必须逐条确认。
    """
    return memory.batch_approve(conn, payload.proposal_ids, reason=payload.reason)


@app.post("/api/memory/{memory_id}/purge-preview")
def post_memory_purge_preview(
    memory_id: int, payload: MemoryScopeIn, conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """彻底删除的**影响预览**：这一步不改任何数据，只把要动的地方摆出来给你看。"""
    try:
        return memory.purge_preview(conn, payload.scope, memory_id)
    except memory.MemoryError as error:
        raise _memory_error(error) from error


@app.post("/api/memory/{memory_id}/purge")
def post_memory_purge(
    memory_id: int, payload: MemoryVoidIn, conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    """执行彻底删除（不可恢复）。动完**全库复扫**：清不干净就如实说，不宣称成功。"""
    try:
        return memory.purge(conn, payload.scope, memory_id, reason=payload.reason)
    except memory.MemoryError as error:
        raise _memory_error(error) from error


# ---------- 触达：每周提醒与导出（T15–T17，SPEC 决策 15 / 16 / 20） ----------
#
# 这是全产品唯一一个「你不在场」的场景：本地定时 + 邮件三问提醒。手机点不开本地页面，
# 所以邮件只做提醒、不放操作链接（U2 档 2）。三条路由都只做翻译——该不该发、发什么、
# 降频与否全在 `notify.py`，导出的排版在 `export.py`，这里不重复任何判定。

class NotifyIn(BaseModel):
    enabled: bool | None = Field(default=None, description="开不开每周提醒")
    to_addr: str | None = Field(default=None, description="收件邮箱；传空串表示清掉")


@app.get("/api/notify")
def get_notify(plan_id: int | None = None, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """设置页那一节要的全部事实：开关、发到哪、下次什么时候、本周会发什么、发过什么、导出了什么。"""
    return {**notify.status(conn, plan_id=plan_id), "export": export.summary()}


@app.put("/api/notify")
def put_notify(payload: NotifyIn, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """改开关与收件邮箱。开启前必须有收件邮箱——「开着但发不出去」是最坑的一种状态。"""
    try:
        notify.update_config(conn, enabled=payload.enabled, to_addr=payload.to_addr)
    except notify.NotifyError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {**notify.status(conn), "export": export.summary()}


@app.post("/api/notify/export", status_code=201)
def post_notify_export(plan_id: int | None = None, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """手动导出四个只读文件（T17 的按钮）。导出只写文件，库里一行都不动。"""
    return export.export_all(conn, plan_id=plan_id)
