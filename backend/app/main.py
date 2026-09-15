"""FastAPI 应用入口。

接口清单与前后端契约见 `docs/SPEC.md` 第 11 节，契约源就是这里的 `/openapi.json`。
P1 补上闭环那一段：建计划 → 建节点 → 提交报告 → 看状态与落后量。

业务规则不在这里：状态机、落后量、阶段判定都在 `app/plan.py`，
接口只负责收参数、把领域错误翻译成 HTTP 状态码。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

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
    due_date: str | None = None
    sort_order: int = 0


class ReportIn(BaseModel):
    node_id: int
    status: Literal["done", "partial", "stuck", "skipped"]
    note: str = Field(min_length=1, description="一句话说明，必填")
    artifact_url: str | None = None
    material_feedback: str | None = None


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


@app.post("/api/plan/nodes", status_code=201)
def create_node(payload: NodeIn, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """建一个阶段或检查点。

    两级结构的一致性由后端把关，不信前端：阶段不能有 parent_id，
    检查点必须有 parent_id、且指向同一个计划里的某个阶段。
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
        node_id = ledger.create_active(
            conn,
            "plan_node",
            {
                "plan_id": payload.plan_id,
                "parent_id": payload.parent_id,
                "level": payload.level,
                "title": payload.title.strip(),
                "deliverable": payload.deliverable,
                "due_date": payload.due_date,
                "sort_order": payload.sort_order,
            },
            actor="user",
        )
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
