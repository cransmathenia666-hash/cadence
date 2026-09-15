"""接口层入参校验：建节点的 `due_date` 收成真日期（P1 补）。

为什么不走 TestClient：TestClient 依赖 httpx，而 requirements.txt 刻意只留三个包，
加依赖属于 SPEC 第 16 节的 Ask first。这里直接测请求模型——FastAPI 就是用这个模型
校验入参的，等价，且不引入新依赖。将来真要发 HTTP 时再谈 httpx。
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.main import NodeIn


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
