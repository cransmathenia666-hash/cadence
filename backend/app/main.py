"""FastAPI 应用入口。

T1 只放一个健康检查：先证明「服务能起来、契约能生成」，
业务接口按 tasks/todo.md 的 P1 逐步往上加。
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(
    title="cadence",
    version="0.1.0",
    description="学习决策与跟进 Agent：方向层 + 计划表 + 跟进",
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
