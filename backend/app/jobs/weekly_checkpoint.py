"""每周检查点 job（T16）：一周一次的兜底——把三问发到你邮箱，顺手导出四个只读文件，
再做记忆周扫描与知识库增量扫描（KB-06）。

怎么用（在 `backend/` 下）：

    python -m app.jobs.weekly_checkpoint --dry-run     # 只打印：这周会不会发、发什么、会扫哪些根
    python -m app.jobs.weekly_checkpoint               # 真跑：发信（没配 SMTP 时空实现只记账）→ 导出 → 记忆周扫描 → 知识库增量扫描

接 Windows 任务计划（U2 档 2）：每周一 09:00 跑一次第二条命令即可。电脑关机错过的那次
由**补发**补上——补发判定在 `notify.decide` 里，不指望任务计划本身永远可靠。

为什么 `--today` 是命令行参数而不是读系统时钟：验收与单测要对着固定的一天看到确定的
三问（周二绿、周五红的测试等于没测）；真实运行不带它，就用当天。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date

from .. import db, export, knowledge_base, memory, notify, plan


def run(
    conn: sqlite3.Connection,
    *,
    today: date | None = None,
    dry_run: bool = False,
    plan_id: int | None = None,
    with_memory: bool = True,
    with_knowledge: bool = True,
    out_dir: str | None = None,
    notifier: notify.Notifier | None = None,
) -> dict:
    """跑一轮：判定 →（真跑时）发送 → 导出 → 记忆周扫描 → 知识库增量扫描。返回这一轮的账。"""
    explicit_day = today is not None
    today = today or date.today()
    status = plan.weekly_status(conn, today=today, plan_id=plan_id)
    decision = notify.decide(conn, status, today)
    subject, body = notify.compose_weekly(
        conn, status, today=today, makeup_week=decision.get("missed_week")
    )
    report: dict = {
        "dry_run": dry_run,
        "today": today.isoformat(),
        "week": status["week"],
        "plan_id": status["plan_id"],
        "send": bool(decision["send"]),
        "kind": decision["kind"],
        "missed_week": decision.get("missed_week"),
        "reason": decision["reason"],
        "subject": subject,
        "body": body,
        "delivery": None,
        "export": None,
        "memory": None,
        "knowledge": None,
    }
    if dry_run:
        # 干跑一行都不写：不落通知记录、不导出、不扫记忆、不碰知识库——
        # 知识库这步只预告会对哪些根跑，一个模型都不调、不写覆盖账。
        if with_knowledge:
            report["knowledge"] = {"planned_roots": [root.alias for root in _knowledge_targets()]}
        return report

    if decision["send"]:
        report["delivery"] = notify.deliver(
            conn,
            subject=subject,
            body=body,
            kind=decision["kind"],
            notifier=notifier,
            # 按指定日期补做时，记账时间也对齐到那一天——否则「这周问过没」的判据会错位。
            at=notify.stamp_for(today) if explicit_day else None,
        )

    # 导出跟着这一趟走（T17：与每周兜底推送同一时刻自动导出）；步骤幂等，重复跑只是覆盖。
    report["export"] = export.export_all(conn, plan_id=plan_id, today=today, out_dir=out_dir)

    if with_memory:
        report["memory"] = _run_memory_scans(conn)
    if with_knowledge:
        report["knowledge"] = _run_knowledge_scans(conn)
    return report


def _knowledge_targets() -> list[knowledge_base.Root]:
    """每周知识库这一步的目标根：当前配置里**可用**的那些（dry-run 预告也用它）。"""
    return [root for root in knowledge_base.configured_roots() if root.enabled]


def _run_knowledge_scans(conn: sqlite3.Connection) -> dict:
    """知识库的每周增量扫描（KB-06）：对每个可用根跑一次（trigger=weekly）。

    失败不该带倒已经做完的事（邮件与导出已经落地了），也不该带倒**别的根**：
    单个根的异常收成一行 failed，让报告如实说「这个根这轮没扫成」。
    没配可用的根不算失败，只是如实说一声跳过了。
    """
    targets = _knowledge_targets()
    if not targets:
        return {
            "roots": 0,
            "scans": 0,
            "candidates": 0,
            "failed": 0,
            "failed_reasons": [],
            "note": "没有配置可用的知识库根目录，这步跳过",
        }
    scans: list[dict] = []
    for root in targets:
        try:
            scans.append(knowledge_base.scan(conn, root.id, trigger="weekly"))
        except Exception as error:  # noqa: BLE001 —— 单个根出问题不连累其它根与别的步骤
            scans.append(
                {
                    "root_id": root.id,
                    "root_alias": root.alias,
                    "status": "failed",
                    # 异常原文可能带本机绝对路径：报告与终端输出都要先脱敏（§10.3）。
                    "error": knowledge_base.redact_paths(
                        f"知识库根「{root.alias}」这轮没扫成：{error}"
                    ),
                }
            )
    failed = [scan for scan in scans if str(scan.get("status")) == "failed"]
    # partial（读取配额触顶 / 有文件没读成）不能算成「没失败就没事」：它是「没看完」，
    # 得跟 failed 一样进报告，否则触顶被抹平成「失败 0 个根」（契约 §10.4）。
    partial = [scan for scan in scans if str(scan.get("status")) == "partial"]
    return {
        "roots": len(targets),
        "scans": len(scans),
        "candidates": sum(int(scan.get("candidates_created") or 0) for scan in scans),
        "failed": len(failed),
        "partial": len(partial),
        "failed_reasons": [scan.get("error") for scan in failed if scan.get("error")],
        "gaps": [
            knowledge_base.redact_paths(str(gap))
            for scan in scans
            for gap in (scan.get("unread_gaps") or [])
        ],
    }


def _run_memory_scans(conn: sqlite3.Connection) -> dict:
    """记忆的每周那一批（T40）：先把欠着的待扫描做掉，再按进行中的计划各扫一批。

    失败不该带倒已经做完的事（邮件与导出已经落地了）：这里把异常收成一句话放进报告，
    让 job 的输出如实说「记忆这步没跑成」，而不是整条命令崩掉。
    """
    try:
        scans = memory.weekly_scans(conn)
    except Exception as error:  # noqa: BLE001 —— 上游没配 provider / 网络不通都在这里落地
        return {"error": f"记忆周扫描没跑成：{error}"}
    failed = [scan for scan in scans if str(scan.get("status")) == "failed"]
    return {
        "scans": len(scans),
        "candidates": sum(int(scan.get("candidates") or 0) for scan in scans),
        "failed": len(failed),
        "failed_reasons": [scan.get("error") for scan in failed if scan.get("error")],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="每周检查点：三问提醒 + 导出四个只读文件")
    parser.add_argument("--dry-run", action="store_true", help="只打印这周会发什么，不写任何记录")
    parser.add_argument("--today", help="按哪一天算（YYYY-MM-DD）；不给就用今天")
    parser.add_argument("--plan-id", type=int, help="指定计划；不给就用最新的进行中计划")
    parser.add_argument("--no-memory", action="store_true", help="跳过记忆的每周扫描（不想这批调模型时用）")
    parser.add_argument("--no-knowledge", action="store_true", help="跳过知识库的每周扫描（不想这批调模型时用）")
    parser.add_argument("--export-dir", help="导出目录；默认为仓库根的 exports/")
    args = parser.parse_args(argv)

    today = plan.parse_date(args.today) if args.today else date.today()
    if args.today and today is None:
        print(f"--today 得是 YYYY-MM-DD 这种写法，收到的是「{args.today}」", file=sys.stderr)
        return 2

    conn = db.connect()
    try:
        report = run(
            conn,
            today=today,
            dry_run=args.dry_run,
            plan_id=args.plan_id,
            with_memory=not args.no_memory,
            with_knowledge=not args.no_knowledge,
            out_dir=args.export_dir,
        )
    finally:
        conn.close()

    print(f"[{report['week']}] 计划 #{report['plan_id']}｜{report['reason']}")
    print("-" * 60)
    print(f"主题：{report['subject']}")
    print()
    print(report["body"])
    print("-" * 60)
    if report["dry_run"]:
        # 干跑预告：知识库这步只说要扫哪些根，一个模型都不调、不写覆盖账
        planned = (report["knowledge"] or {}).get("planned_roots") or []
        if planned:
            print(f"知识库扫描：将对 {'、'.join(planned)} 各跑一次增量扫描")
        else:
            print("知识库扫描：没有配置可用的知识库根目录，这一步会跳过")
        print("（--dry-run：没有发信、没有导出、没有写任何记录）")
        return 0
    delivery = report["delivery"]
    if delivery is None:
        print("这一轮没发信。")
    elif delivery["sent"]:
        print(f"已记录：{delivery['detail']}（channel={delivery['channel']}，通知 #{delivery['notification_id']}）")
    else:
        print(f"发送失败：{delivery['detail']}（通知 #{delivery['notification_id']} 记了这一笔）")
    export_report = report["export"]
    print(f"导出 {len(export_report['files'])} 个文件 → {export_report['dir']}")
    for item in export_report["files"]:
        print(f"  · {item['name']}（{item['bytes']} 字节）")
    if report["memory"] is not None:
        memory_report = report["memory"]
        if memory_report.get("error"):
            print(memory_report["error"])
        else:
            print(
                f"记忆周扫描：{memory_report['scans']} 批，产出候选 {memory_report['candidates']} 条，"
                f"失败 {memory_report['failed']} 批"
            )
    if report["knowledge"] is not None:
        knowledge_report = report["knowledge"]
        if knowledge_report.get("error"):
            print(knowledge_report["error"])
        elif knowledge_report.get("note"):
            print(f"知识库扫描：{knowledge_report['note']}")
        else:
            print(
                f"知识库扫描：{knowledge_report['roots']} 个根，产出候选 "
                f"{knowledge_report['candidates']} 条，失败 {knowledge_report['failed']} 个根，"
                f"没看完 {knowledge_report.get('partial', 0)} 个根"
            )
            for reason in knowledge_report.get("failed_reasons", []):
                print(f"  · {reason}")
            for gap in knowledge_report.get("gaps", []):
                print(f"  · 缺口：{gap}")
    if delivery is not None and not delivery["sent"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
