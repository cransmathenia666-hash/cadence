"""P1 闭环冒烟：一条命令走完「建计划 → 建阶段 → 建任务 → 打勾 → 交交付物 → 看落后量 → 看阶段完成 → 改字段」。

为什么要有它：手点 `/docs` 要五六次；复制 PowerShell 又会踩两个坑——
`$` 被终端吃掉、中文按老编码发出去变乱码。这个脚本用标准库 `urllib` 直接发请求，
不经过 shell，那两个坑自然没有了；请求体统一显式编码成 UTF-8 字节。

默认行为：自己找一个空闲端口，用**临时库**起一个自己的服务，跑完把临时库删掉，
**不碰你 `data/cadence.db` 里的真实数据**。
对着已在跑的服务跑也可以（`--base-url`），但那种情况下它会写进那个服务的库。

顺带覆盖 T19（防重复提交）的两半：任务刚建好、还没完成时再建同名 -> 期望 409；
等它完成之后再建同名 -> 期望 201（这是规则的另一半：已收尾的不挡路）。

2026-09-17 起（T23）走三级结构：阶段完成 = 全部任务打勾/跳过 **且** 交付物已提交；
周打卡只做节奏，不参与完成判定。

T29 起这两步（打勾、交交付物）都不再顺产「推进提案」——那整类已删，阶段完成与否只由计划表显示；冒烟跟着改成断言「一条提案都不产」，顺带证明完成判定本身还在。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

BACKEND = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND.parent
TEMP_DB = PROJECT_ROOT / "data" / "_smoke_p1.db"
# 导出步骤的落点：冒烟不往仓库根的 exports/ 里丢文件，跑完连目录一起删。
TEMP_EXPORTS = PROJECT_ROOT / "data" / "_smoke_exports"


class _StubLlmHandler(BaseHTTPRequestHandler):
    """成果闭环冒烟用的假 LLM 上游：按脚本逐个回内容（OpenAI 兼容 /chat/completions）。

    脚本顺序就是本批端到端的调用顺序：找方向（候选清单）→ 规划对话（ready 回话）
    → 出方案（蓝图 v2 JSON）。脚本用完再被调就回 500——冒烟会当场报出来。
    """

    script: list[str] = []

    def do_POST(self):  # noqa: N802 —— BaseHTTPRequestHandler 的命名约定
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if not _StubLlmHandler.script:
            self.send_response(500)
            self.end_headers()
            return
        text = _StubLlmHandler.script.pop(0)
        body = json.dumps(
            {
                "choices": [{"message": {"content": text}}],
                "usage": {"prompt_tokens": 21, "completion_tokens": 13},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 安静：冒烟输出只留步骤
        pass


def _start_stub_llm(script: list[str]) -> tuple[Thread, str]:
    """在本机空闲端口上起假上游（守护线程），返回 (线程, base_url)。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    _StubLlmHandler.script = list(script)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _StubLlmHandler)
    thread = Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return thread, f"http://127.0.0.1:{port}/v1"


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
        env={**os.environ, "CADENCE_EXPORT_DIR": str(TEMP_EXPORTS)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class Checker:
    """记下每一步是否符合预期，最后一起结算——冒烟脚本的价值就在这里。"""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.steps = 0

    def step(self, number: int, title: str, status: int, body: dict) -> None:
        summary = json.dumps(body, ensure_ascii=False)
        self.steps += 1
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
        # 「还作数」挑的日期（走查整改第 1 条）：给了就照给的，不再固定推 90 天。
        picked_review = (date.today() + timedelta(days=7)).isoformat()

        status, plan = request(base, "POST", "/api/plan", {"goal": f"冒烟 {stamp}"})
        checker.step(1, "建计划", status, plan)
        checker.expect("建计划状态码", status, 201)

        status, stage = request(base, "POST", "/api/plan/nodes", {
            "plan_id": plan["id"], "level": "stage",
            "title": f"阶段 A · 冒烟 {stamp}", "deliverable": "一个能访问的地址", "sort_order": 10,
        })
        checker.step(2, "建阶段", status, stage)

        status, task = request(base, "POST", "/api/plan/nodes", {
            "plan_id": plan["id"], "parent_id": stage["id"], "level": "task",
            "title": f"任务 · 冒烟 {stamp}", "due_date": overdue,
        })
        checker.step(3, f"建任务（到期日故意设在 5 天前：{overdue}）", status, task)
        checker.expect("建任务状态码", status, 201)

        # T19 的前一半：任务还开着时，同名应当被挡下
        status, blocked = request(base, "POST", "/api/plan/nodes", {
            "plan_id": plan["id"], "parent_id": stage["id"], "level": "task",
            "title": f"任务 · 冒烟 {stamp}",
        })
        checker.step(4, "趁它还没完成，再建一次同名任务（T19 应挡下）", status, blocked)
        checker.expect("重复创建状态码", status, 409)

        status, tree = request(base, "GET", f"/api/plan?plan_id={plan['id']}")
        checker.step(5, "取计划：应看到落后 5 天（任务带了日期才进落后量）", status, tree["lag"])
        checker.expect("落后天数", tree["lag"]["lag_days"], 5)
        checker.expect("behind", tree["lag"]["behind"], True)

        status, checked = request(base, "POST", f"/api/plan/nodes/{task['id']}/check")
        checker.step(6, "任务打勾", status, checked)
        checker.expect("任务状态", checked["node_status"], "done")
        checker.expect("只打勾时还不产提案（差交付物）", checked["proposal_id"], None)

        # 放回（2026-09-28 补的出口）：打勾是一键动作，手滑要能退回来，理由进台账。
        # 走完这两条再打回去，后面几步看到的连还是一样的（落后量、交付物判定都不受影响）。
        status, reopened = request(base, "POST", f"/api/plan/nodes/{task['id']}/reopen",
                                   {"reason": "冒烟：手滑打勾了"})
        checker.expect("放回后回到进行中", (status, reopened["node_status"]), (200, "in_progress"))
        status, no_reason = request(base, "POST", f"/api/plan/nodes/{task['id']}/reopen",
                                    {"reason": "   "})
        checker.expect("放回缺理由被拒（业务拒绝 400）", status, 400)
        request(base, "POST", f"/api/plan/nodes/{task['id']}/check")  # 打回完成，接着往下走

        status, delivered = request(base, "POST", f"/api/plan/nodes/{stage['id']}/deliverable", {
            "url": "https://example.com/smoke", "note": "冒烟：交付物提交",
        })
        checker.step(7, "提交交付物（阶段因此完成；T29 起不再顺产推进提案）", status, delivered)
        checker.expect("交付物提交状态码", status, 201)
        # T29：这两步过去会顺产「推进提案」，现在一条都不产——阶段完成只由计划表显示
        checker.expect("打勾与交交付物都不再产提案（T29）", delivered["proposal_id"], None)

        status, tree_after = request(base, "GET", f"/api/plan?plan_id={plan['id']}")
        checker.step(8, "再取计划：落后量应回到 0", status, tree_after["lag"])
        checker.expect("落后天数", tree_after["lag"]["lag_days"], 0)
        checker.expect("当前阶段（都收尾了）", tree_after["current_stage"], None)

        # T19 的后一半：同一个标题，但旧的那条已经完成了，应当放行
        status, reuse = request(base, "POST", "/api/plan/nodes", {
            "plan_id": plan["id"], "parent_id": stage["id"], "level": "task",
            "title": f"任务 · 冒烟 {stamp}",
        })
        checker.step(9, "它已完成后再建同名（应当放行）", status, reuse)
        checker.expect("已完成后再建状态码", status, 201)

        status, checkpoint = request(base, "POST", "/api/plan/nodes", {
            "plan_id": plan["id"], "parent_id": stage["id"], "level": "checkpoint",
            "title": f"周打卡 · 冒烟 {stamp}",
        })
        status, weekly = request(base, "POST", "/api/report", {
            "node_id": checkpoint["id"], "status": "done", "note": "冒烟：本周打卡",
        })
        checker.step(10, "建周打卡并提交报告（只管节奏，不参与完成判定）", status, weekly)
        checker.expect("周打卡报告不额外产提案", weekly["proposal_id"], None)
        # T29：阶段完成与否只由计划表显示（这句顺带证明判定还在，只是不再产提案）
        checker.expect("阶段完成判定仍在（finished=True）",
                       [item["finished"] for item in tree_after["stages"]], [True])

        # T30 的写入口：原地改一个已经建好的节点（交付物、截止日、标题），id 不变
        status, edited = request(base, "POST", f"/api/plan/nodes/{stage['id']}/fields", {
            "deliverable": "一份能看的对照表", "reason": "冒烟：把交付物写具体",
        })
        checker.step(11, "改阶段交付物（T30：原地改 + 台账流水）", status, edited)
        checker.expect("改字段回执列出改了哪些", edited["changed"], ["deliverable"])

        status, refused = request(base, "POST", f"/api/plan/nodes/{stage['id']}/fields", {
            "title": "阶段 · 冒烟", "reason": "   ",
        })
        checker.expect("改字段缺理由被拒（T30，理由只有空白 → 业务拒绝 400）", status, 400)

        # 记忆系统（2026-09-21）：这里只走**不调模型**的那几条——手工补记、看、改、复核、
        # 彻底删除。扫描那条要接真模型，不进冒烟（它由单测的假上游覆盖）。
        status, added = request(base, "POST", "/api/memory", {
            "scope": "plan", "plan_id": plan["id"], "kind": "constraint",
            "content": f"冒烟记忆 {stamp}", "review_at": overdue, "reason": "冒烟：补一条计划内记忆",
        })
        checker.step(12, "手工补一条计划内记忆（复核时间故意设在 5 天前）", status, added)
        checker.expect("补记忆状态码", status, 201)

        status, listing = request(base, "GET", f"/api/memory?plan_id={plan['id']}")
        checker.step(13, "看记忆：它应当在「待复核」那一组里", status, listing["counts"])
        checker.expect("到期待复核的条数", listing["counts"]["due"], 1)

        status, renewed = request(base, "POST", f"/api/memory/{added['id']}/review", {
            "scope": "plan", "decision": "renew", "review_at": picked_review,
            "reason": "冒烟：还作数，下次复核挑了个自己填的日子",
        })
        checker.expect("复核续期后不再到期", renewed["memory"]["review_due"], False)
        checker.expect("复核时间就是挑的那天（不是默认 90 天）",
                       renewed["memory"]["review_at"], picked_review)

        status, replaced = request(base, "PUT", f"/api/memory/{added['id']}", {
            "scope": "plan", "content": f"冒烟记忆 {stamp} · 改过", "reason": "冒烟：改内容",
        })
        checker.step(14, "改一条记忆（走台账取代：旧值留痕、id 换新）", status, replaced)
        checker.expect("取代后是另一条记录", replaced["id"] != added["id"], True)

        status, preview = request(base, "POST", f"/api/memory/{replaced['id']}/purge-preview",
                                  {"scope": "plan"})
        checker.step(15, "彻底删除的影响预览（这一步只读）",
                     status, {"copies": preview["copies"], "irreversible": preview["irreversible"]})
        still_there = request(base, "GET", f"/api/memory?plan_id={plan['id']}")[1]
        checker.expect("预览没有动任何数据", still_there["counts"]["plan"], 1)

        status, purged = request(base, "POST", f"/api/memory/{replaced['id']}/purge",
                                 {"scope": "plan", "reason": "冒烟：删干净"})
        checker.step(16, "彻底删除并全库复扫一遍",
                     status, {"complete": purged["complete"], "leftover": purged["leftover"]})
        checker.expect("清干净了（complete=True）", purged["complete"], True)

        status, notify_state = request(base, "GET", "/api/notify")
        checker.step(17, "每周提醒的现状：开关、下次发送、本周会发什么",
                     status, {"enabled": notify_state["enabled"], "to_addr": notify_state["to_addr"],
                              "due": notify_state["due"], "next_send_at": notify_state["next_send_at"],
                              "subject": notify_state["preview"]["subject"]})
        checker.expect("开关默认是关的", notify_state["enabled"], False)
        checker.expect("收件邮箱还没填", notify_state["to_addr"], None)
        checker.expect("三问的主题带上了当前阶段", "周检查点" in notify_state["preview"]["subject"], True)
        checker.expect("导出目录里还什么都没有", notify_state["export"]["files"], [])

        status, saved = request(base, "PUT", "/api/notify",
                                {"to_addr": "smoke@example.com", "enabled": True})
        checker.step(18, "打开每周提醒：收件邮箱 + 开关",
                     status, {"enabled": saved["enabled"], "to_addr": saved["to_addr"], "due": saved["due"]})
        checker.expect("开关打开了", saved["enabled"], True)
        checker.expect("本周还没问过，所以这周有一封要发", saved["due"], True)

        status, exported = request(base, "POST", "/api/notify/export")
        names = sorted(item["name"] for item in exported["files"])
        checker.step(19, "手动导出四个只读文件", status, {"dir": exported["dir"], "files": names})
        checker.expect("四个文件都写了", len(exported["files"]), 4)
        checker.expect("周检查点的文件名带周编号",
                       any(name.startswith("周检查点-") for name in names), True)

        status, again = request(base, "GET", "/api/notify")
        checker.expect("重新取一次：导出的清单读得回来", again["export"]["last_at"] is not None, True)

        status, closed = request(base, "POST", f"/api/plans/{plan['id']}/close",
                                 {"reason": "冒烟：收尾"})
        checker.step(20, "收尾计划：只登记一条待扫描，不在收尾里调模型",
                     status, {"memory_scan_id": closed["memory_scan_id"]})
        checker.expect("收尾登记了待扫描", isinstance(closed["memory_scan_id"], int), True)

        # ========== 成果闭环（OC-07）：P1 legacy 升级 + 新主线端到端 ==========
        #
        # 21–23：老计划的「升级为成果闭环」入口（一次性交齐完整契约 + 每个阶段的处置）。
        # 24–30：新主线「采纳 → 规划对话 → 蓝图 → 批准 → 建树」——模型调用全部打在本脚本
        # 自带的假上游上（脚本项目约定：冒烟不接真模型、不花真钱、不碰真实库）。

        status, plan_b = request(base, "POST", "/api/plan", {"goal": f"冒烟 legacy 升级 {stamp}"})
        checker.expect("建第二个计划（legacy）状态码", status, 201)
        status, stage_b = request(base, "POST", "/api/plan/nodes", {
            "plan_id": plan_b["id"], "level": "stage",
            "title": f"老阶段 · 冒烟 {stamp}", "deliverable": "一份整理好的笔记", "sort_order": 10,
        })
        checker.expect("给老计划建阶段状态码", status, 201)

        status, upgraded = request(base, "POST", f"/api/plans/{plan_b['id']}/upgrade", {
            "reason": "冒烟：补全成果契约",
            "contract": {
                "title": "整理一份能用的入门笔记",
                "outcome": "一份结构完整、能给别人看的入门笔记",
                "value": "冒烟：legacy 计划升级为成果流程",
                "success_statement": "别人读完能照着跑起来",
                "acceptance_criteria": [
                    {"text": "覆盖最小上手路径", "required": True},
                    {"text": "附可运行的示例", "required": False},
                ],
                "evidence_requirements": [
                    {"kind": "document", "required": True, "description": "笔记文档"},
                ],
            },
            "stages": [{
                "stage_id": stage_b["id"],
                "disposition": "include",
                "acceptance_criteria": [{"text": "笔记覆盖最小上手路径", "required": True}],
            }],
        })
        checker.step(21, "老计划一次性补契约 + 阶段处置，升级为成果闭环", status,
                     {"plan_id": plan_b["id"]})
        checker.expect("升级状态码", status, 201)
        status, plan_b_view = request(base, "GET", f"/api/plan?plan_id={plan_b['id']}")
        checker.expect("升级后 completion_mode=outcome", plan_b_view.get("completion_mode"), "outcome")
        checker.expect("升级后有生效契约", plan_b_view.get("contract") is not None, True)

        # —— 假上游脚本：找方向 → 规划对话 → 出方案（顺序 = 服务端的调用顺序）——
        candidates_text = json.dumps({
            "intent": "candidates",
            "shape": "directions",
            "reply": "",
            "candidates": [
                {"title": "做一个能演示的记账小程序", "kind": "project",
                 "why": "依据不足：冒烟", "depth_target": "够用", "profile_item_ids": []},
                {"title": "读一本产品入门书", "kind": "doc",
                 "why": "依据不足：冒烟", "depth_target": "浅尝", "profile_item_ids": []},
                {"title": "研究一个开源项目", "kind": "concept",
                 "why": "依据不足：冒烟", "depth_target": "够用", "profile_item_ids": []},
            ],
            "recommended_start": "做一个能演示的记账小程序",
            "start_reason": "冒烟：能最快产出可验收的外部结果",
        }, ensure_ascii=False)
        chat_text = json.dumps(
            {"questions": [], "ready": True, "note": "冒烟：信息够了，可以出方案"},
            ensure_ascii=False,
        )
        blueprint_text = json.dumps({
            "goal": "做出一个能演示的记账小程序",
            "contract": {
                "title": "一个能演示的记账小程序",
                "outcome": "一个能运行、能记账、能导出记录的小程序",
                "value": "冒烟：验证成果闭环主线",
                "success_statement": "能运行、能记一笔账、能导出",
                "acceptance_criteria": [
                    {"text": "能运行并记一笔账", "required": True},
                    {"text": "能导出记录", "required": False},
                ],
                "evidence_requirements": [
                    {"kind": "repository", "required": True, "description": "可运行的仓库"},
                ],
            },
            "stages": [{
                "title": "搭出最小可用的记账功能",
                "purpose": "跑通核心记账动作",
                "why_now": "其它一切都依赖它",
                "deliverable": "能记一笔账并导出的程序",
                "acceptance_criteria": [{"text": "能记一笔账并导出", "required": True}],
                "evidence_requirements": [{"kind": "repository", "required": False, "description": "代码仓库"}],
                "contract_criterion_ids": ["oc-1"],
                "tasks": [{"title": "写最小记账页"}],
            }],
        }, ensure_ascii=False)
        # OC-09（复盘回流）：带报告的计划对话轮——假上游直接回一条改契约的建议信封
        dialogue_text = json.dumps({
            "intent": "modify",
            "reply": "看了这次报告和复盘卡：任务卡住了、验收还有缺口。建议把成果契约的验收条件补成三条，把按月统计也定成标准。",
            "suggestion": {
                "action": "revise_contract",
                "changes": {
                    "acceptance_criteria": [
                        {"text": "能运行并记一笔账", "required": True},
                        {"text": "能导出记录", "required": False},
                        {"text": "能按月统计记账次数", "required": True},
                    ],
                },
                "why": "复盘卡显示验收有缺口，先把「按月统计」定进契约标准",
            },
        }, ensure_ascii=False)

        stub_thread, stub_base = _start_stub_llm(
            [candidates_text, chat_text, blueprint_text, dialogue_text])
        status, provider = request(base, "POST", "/api/providers", {
            "name": f"冒烟假上游 {stamp}", "base_url": stub_base,
            "api_key": "sk-smoke", "default_model": "smoke-model", "set_as_default": True,
        })
        checker.step(22, "注册冒烟自带的假模型上游", status, {"id": provider.get("id")})
        checker.expect("注册假上游状态码", status, 201)

        status, profile_item = request(base, "POST", "/api/profile", {
            "category": "long_axis", "content": f"冒烟主线 {stamp}",
        })
        checker.expect("补一条长期档案（规划要判据）状态码", status, 201)

        status, found = request(base, "POST", "/api/requests",
                                {"kind": "search", "raw_text": f"冒烟：帮我找方向 {stamp}"})
        checker.step(23, "「找方向」落一版候选清单（假上游出 3 条）", status,
                     {"candidate_ids": found.get("candidate_ids")})
        checker.expect("找方向状态码", status, 201)
        checker.expect("落了 3 条候选", len(found.get("candidate_ids") or []), 3)

        status, verdict = request(base, "POST",
                                  f"/api/candidates/{found['candidate_ids'][0]}/verdict",
                                  {"accept": True})
        checker.step(24, "采纳候选：进入规划（不再直接建阶段）", status,
                     {"node_id": verdict.get("node_id"),
                      "planning_status": verdict.get("planning_status")})
        checker.expect("采纳状态码", status, 200)
        checker.expect("采纳不再建阶段（node_id 为空）", verdict.get("node_id"), None)
        checker.expect("拿到规划会话", isinstance(verdict.get("planning_session_id"), int), True)
        checker.expect("规划状态是 needs_blueprint", verdict.get("planning_status"), "needs_blueprint")
        session_id = verdict["planning_session_id"]

        status, said = request(base, "POST", "/api/plan-chat",
                               {"planning_session_id": session_id, "message": "冒烟：先把成果聊清"})
        checker.step(25, "按规划会话聊一轮（假上游回「信息够了」）", status,
                     {"can_generate": said.get("can_generate")})
        checker.expect("聊成后可以出方案", said.get("can_generate"), True)

        status, blueprint = request(base, "POST", "/api/plan-chat/blueprint",
                                    {"planning_session_id": session_id})
        checker.step(26, "出方案：落一条待裁定的蓝图 v2 提案", status,
                     {"proposal_id": blueprint.get("proposal_id"),
                      "payload_version": blueprint.get("payload_version")})
        checker.expect("出方案状态码", status, 201)
        checker.expect("payload 是蓝图 v2", blueprint.get("payload_version"), 2)

        # 缺确认标记：就地拒绝、提案保持 pending（OC-07 的批准门槛）
        status, no_confirm = request(base, "POST",
                                     f"/api/proposals/{blueprint['proposal_id']}/decide",
                                     {"approved": True, "landing_mode": "new_plan"})
        checker.expect("缺 confirm_contract 被拒（400）", status, 400)

        status, approved = request(base, "POST",
                                   f"/api/proposals/{blueprint['proposal_id']}/decide", {
                                       "approved": True,
                                       "confirm_contract": True,
                                       "landing_mode": "new_plan",
                                   })
        checker.step(27, "确认成果契约并批准：原子建计划 + 激活契约 + 建树", status,
                     {"effect": approved.get("effect"), "plan_id": approved.get("plan_id")})
        checker.expect("批准状态码", status, 200)
        checker.expect("effect 是 blueprint_built", approved.get("effect"), "blueprint_built")

        status, new_tree = request(base, "GET", f"/api/plan?plan_id={approved['plan_id']}")
        checker.step(28, "看新计划：成果契约 + 阶段 + 任务一次落齐", status,
                     {"completion_mode": new_tree.get("completion_mode"),
                      "stages": [item.get("title") for item in new_tree.get("stages") or []]})
        checker.expect("新计划是成果流程", new_tree.get("completion_mode"), "outcome")
        checker.expect("契约已激活", new_tree.get("contract") is not None, True)
        checker.expect("阶段建出来了", [item.get("title") for item in new_tree.get("stages")],
                       ["搭出最小可用的记账功能"])

        status, session_view = request(base, "GET", f"/api/plan-chat?planning_session_id={session_id}")
        checker.step(29, "回看规划会话：批准后转正为 converted（只读）", status,
                     {"planning_status": session_view.get("planning_status")})
        checker.expect("planning_status=blueprint_approved",
                       session_view.get("planning_status"), "blueprint_approved")
        checker.expect("转正落点回填到新建计划（F4）",
                       session_view.get("planning_session", {}).get("landing_plan_id"),
                       approved["plan_id"])

        # ========== 成果闭环（OC-09）：复盘回流端到端 ==========
        # 30–33：带停止建议的报告 → 复盘卡 → 带 report_id 的计划对话产 contract_change 提案
        # → 批准激活新契约版本（旧版 superseded）。模型调用全部打在本脚本自带的假上游上。

        new_plan_id = approved["plan_id"]
        new_task_id = new_tree["stages"][0]["tasks"][0]["id"]
        status, report = request(base, "POST", "/api/report", {
            "node_id": new_task_id, "status": "stuck", "note": f"冒烟：卡住了 {stamp}",
            "next_action": "stop", "review_requested": True,
        })
        checker.step(30, "提交带停止建议的报告（不进对话、不落提案）", status,
                     {"report_id": report.get("report_id"),
                      "card": (report.get("review_card") or {}).get("rule")})
        checker.expect("报告状态码", status, 201)
        checker.expect("复盘卡给停止入口", (report.get("review_card") or {}).get("rule"), "user_stop")

        status, proposals_before = request(base, "GET", "/api/proposals")
        pending_before = {item["id"] for item in proposals_before.get("proposals") or []}

        status, card = request(base, "GET", f"/api/plans/{new_plan_id}/review-card")
        checker.step(31, "只读取当前复盘卡（供工作台与报告页）", status,
                     {"rule": card.get("rule")})
        checker.expect("只读路由也是停止卡", card.get("rule"), "user_stop")

        status, said = request(base, "POST", "/api/plan-dialogue", {
            "plan_id": new_plan_id,
            "message": f"根据这次复盘，把成果契约改一下：验收条件加一条「能按月统计记账次数」（{stamp}）",
            "report_id": report["report_id"],
        })
        proposal_id_c = (said.get("suggestion") or {}).get("proposal_id")
        checker.step(32, "带 report_id 聊一轮（假上游建议改契约）→ 落 contract_change 提案", status,
                     {"proposal_id": proposal_id_c, "kind": (said.get("suggestion") or {}).get("kind")})
        checker.expect("对话状态码", status, 201)
        checker.expect("落的是契约修改提案", (said.get("suggestion") or {}).get("kind"), "contract_change")
        checker.expect("拿到待裁定的提案编号", isinstance(proposal_id_c, int), True)

        old_contract_id = new_tree["contract"]["id"]
        status, decided = request(base, "POST", f"/api/proposals/{proposal_id_c}/decide",
                                  {"approved": True, "reason": f"冒烟：认可这条契约调整 {stamp}"})
        checker.step(33, "批准契约修改：激活新版本（旧版 superseded）", status,
                     {"effect": decided.get("effect"), "contract": decided.get("contract")})
        checker.expect("批准状态码", status, 200)
        checker.expect("effect 是 contract_activated", decided.get("effect"), "contract_activated")
        checker.expect("新契约是第 2 版", (decided.get("contract") or {}).get("version"), 2)
        checker.expect("旧契约被取代", (decided.get("contract") or {}).get("superseded_id"), old_contract_id)

        status, final_tree = request(base, "GET", f"/api/plan?plan_id={new_plan_id}")
        checker.expect("新计划的当前契约已是新版",
                       (final_tree.get("contract") or {}).get("id"),
                       (decided.get("contract") or {}).get("id"))
        status, pending_after = request(base, "GET", "/api/proposals")
        pending_after_ids = {item["id"] for item in pending_after.get("proposals") or []}
        checker.expect("批准正好消化了这一条提案（其余待裁定不受影响）",
                       pending_after_ids == pending_before, True)

        stub_thread.join(timeout=1)
        print()
        if checker.failures:
            print(f"结论：{len(checker.failures)} 项不符合预期")
            for failure in checker.failures:
                print(f"  - {failure}")
            return 1
        print(f"结论：{checker.steps} 步全部符合预期，P1 闭环在这台机器上跑得通")
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
            shutil.rmtree(TEMP_EXPORTS, ignore_errors=True)
            print(f"\n临时库已删除：{TEMP_DB}（导出目录 {TEMP_EXPORTS} 一并清掉）")


if __name__ == "__main__":
    raise SystemExit(main())
