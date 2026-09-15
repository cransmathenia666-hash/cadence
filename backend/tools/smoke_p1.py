"""P1 闭环冒烟：一条命令走完「建计划 → 建节点 → 提交报告 → 看落后量 → 产出提案」。

为什么要有它：手点 `/docs` 要五六次；复制 PowerShell 又会踩两个坑——
`$` 被终端吃掉、中文按老编码发出去变乱码。这个脚本用标准库 `urllib` 直接发请求，
不经过 shell，那两个坑自然没有了；请求体统一显式编码成 UTF-8 字节。

默认行为：自己找一个空闲端口，用**临时库**起一个自己的服务，跑完把临时库删掉，
**不碰你 `data/cadence.db` 里的真实数据**。
对着已在跑的服务跑也可以（`--base-url`），但那种情况下它会写进那个服务的库。

顺带覆盖 T19（防重复提交）的两半：检查点刚建好、还没收尾时再建同名 -> 期望 409；
等它被报成 done（收尾）之后再建同名 -> 期望 201（这是规则的另一半：已收尾的不挡路）。
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND.parent
TEMP_DB = PROJECT_ROOT / "data" / "_smoke_p1.db"


def free_port() -> int:
    """让操作系统分配一个当前空闲的端口，避免和别的东西撞（本项目就撞过一次）。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request(base: str, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    """发一个请求，返回 (状态码, 解析后的 body)。4xx/5xx 也照样取回 body，方便看原因。"""
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        try:
            return error.code, json.loads(body)
        except json.JSONDecodeError:
            return error.code, {"detail": body}


def wait_ready(base: str, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if request(base, "GET", "/api/health")[0] == 200:
                return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.3)
    raise RuntimeError(f"服务在 {timeout} 秒内没起来：{base}")


def start_temp_server(port: int) -> subprocess.Popen:
    """起一个只服务临时库的 uvicorn。输出丢掉，不给终端添噪音。"""
    launcher = (
        "import pathlib, uvicorn; from app import db; "
        f"db.DB_PATH = pathlib.Path(r'{TEMP_DB}'); db.init(); "
        "from app.main import app; "
        f"uvicorn.run(app, host='127.0.0.1', port={port})"
    )
    return subprocess.Popen(
        [sys.executable, "-c", launcher],
        cwd=str(BACKEND),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class Checker:
    """记下每一步是否符合预期，最后一起结算——冒烟脚本的价值就在这里。"""

    def __init__(self) -> None:
        self.failures: list[str] = []

    def step(self, number: int, title: str, status: int, body: dict) -> None:
        summary = json.dumps(body, ensure_ascii=False)
        print(f"{number}) {title}\n   HTTP {status}  {summary[:160]}{'…' if len(summary) > 160 else ''}")

    def expect(self, label: str, actual: object, wanted: object) -> None:
        ok = actual == wanted
        print(f"   {'符合预期' if ok else '不符合预期'}：{label} = {actual!r}" + ("" if ok else f"（期望 {wanted!r}）"))
        if not ok:
            self.failures.append(f"{label}：实际 {actual!r}，期望 {wanted!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description="P1 闭环冒烟")
    parser.add_argument("--base-url", help="对着已在跑的服务跑（会写进它的库）；不传就自己起临时库")
    args = parser.parse_args()

    checker = Checker()
    stamp = time.strftime("%m%d-%H%M%S")
    server: subprocess.Popen | None = None
    base = args.base_url

    if base is None:
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        TEMP_DB.unlink(missing_ok=True)
        server = start_temp_server(port)
        print(f"临时库：{TEMP_DB}\n服务：{base}（PID {server.pid}）\n")

    try:
        wait_ready(base)
        overdue = (date.today() - timedelta(days=5)).isoformat()

        status, plan = request(base, "POST", "/api/plan", {"goal": f"冒烟 {stamp}"})
        checker.step(1, "建计划", status, plan)
        checker.expect("建计划状态码", status, 201)

        status, stage = request(base, "POST", "/api/plan/nodes", {
            "plan_id": plan["id"], "level": "stage",
            "title": f"阶段 A · 冒烟 {stamp}", "deliverable": "一个能访问的地址", "sort_order": 10,
        })
        checker.step(2, "建阶段", status, stage)

        status, checkpoint = request(base, "POST", "/api/plan/nodes", {
            "plan_id": plan["id"], "parent_id": stage["id"], "level": "checkpoint",
            "title": f"检查点 · 冒烟 {stamp}", "due_date": overdue,
        })
        checker.step(3, f"建检查点（到期日故意设在 5 天前：{overdue}）", status, checkpoint)
        checker.expect("建检查点状态码", status, 201)

        # T19 的前一半：节点还开着时，同名应当被挡下
        status, blocked = request(base, "POST", "/api/plan/nodes", {
            "plan_id": plan["id"], "parent_id": stage["id"], "level": "checkpoint",
            "title": f"检查点 · 冒烟 {stamp}",
        })
        checker.step(4, "趁它还没收尾，再建一次同名检查点（T19 应挡下）", status, blocked)
        checker.expect("重复创建状态码", status, 409)

        status, tree = request(base, "GET", f"/api/plan?plan_id={plan['id']}")
        checker.step(5, "取计划：应看到落后 5 天", status, tree["lag"])
        checker.expect("落后天数", tree["lag"]["lag_days"], 5)
        checker.expect("behind", tree["lag"]["behind"], True)

        status, first = request(base, "POST", "/api/report", {
            "node_id": checkpoint["id"], "status": "partial", "note": "冒烟：做了一半",
        })
        checker.step(6, "提交「部分完成」报告", status, first)
        checker.expect("节点状态", first["node_status"], "in_progress")

        status, second = request(base, "POST", "/api/report", {
            "node_id": checkpoint["id"], "status": "done", "note": "冒烟：做完了",
            "artifact_url": "https://example.com/smoke",
        })
        checker.step(7, "提交「完成」报告（阶段因此收尾）", status, second)
        checker.expect("节点状态", second["node_status"], "done")
        checker.expect("是否产出推进提案", second["proposal_id"] is not None, True)

        status, tree_after = request(base, "GET", f"/api/plan?plan_id={plan['id']}")
        checker.step(8, "再取计划：落后量应回到 0", status, tree_after["lag"])
        checker.expect("落后天数", tree_after["lag"]["lag_days"], 0)
        checker.expect("当前阶段（都收尾了）", tree_after["current_stage"], None)

        # T19 的后一半：同一个标题，但旧的那条已经收尾了，应当放行
        status, reuse = request(base, "POST", "/api/plan/nodes", {
            "plan_id": plan["id"], "parent_id": stage["id"], "level": "checkpoint",
            "title": f"检查点 · 冒烟 {stamp}",
        })
        checker.step(9, "它已收尾后再建同名（应当放行）", status, reuse)
        checker.expect("已收尾后重建状态码", status, 201)

        print()
        if checker.failures:
            print(f"结论：{len(checker.failures)} 项不符合预期")
            for failure in checker.failures:
                print(f"  - {failure}")
            return 1
        print("结论：9 步全部符合预期，P1 闭环在这台机器上跑得通")
        return 0
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
            TEMP_DB.unlink(missing_ok=True)
            print(f"\n临时库已删除：{TEMP_DB}")


if __name__ == "__main__":
    raise SystemExit(main())
