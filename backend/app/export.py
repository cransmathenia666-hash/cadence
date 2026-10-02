"""Markdown 单向导出（T17）：把库里的事实拍成四个只读文件。

四个文件（SPEC 决策 15）：
- `计划-当前.md`：计划树——阶段要交什么、任务与周打卡到哪了、落后多少
- `决策台账.md`：每一次状态变更的流水（改前改后、谁改的、为什么）
- `档案-当前.md`：长期档案的当前有效条目，按五类分组
- `周检查点-YYYY-WW.md`：这一周的三问与本周报告

**单向**是它的规矩（T17 的验收就盯这一条）：只写文件、永不读回。你在 md 上做的批注，
下次导出会被覆盖——这正是要的效果，免得出现「文件说 A、系统说 B」两个真相源。
所以这里没有任何解析 Markdown 的代码，四个写入函数都是「读库 → 排版 → 落盘」。

为什么单独存一个目录：`exports/` 已在 .gitignore 里，私人内容不会跟着仓库走。
定时任务与环境测试都允许用 `CADENCE_EXPORT_DIR` 改落点（冒烟脚本要往临时目录写）。
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import advisor, db, ledger, plan

# 导出目录：默认仓库根的 `exports/`，可用环境变量改（测试与冒烟写临时目录）。
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 台账流水一次最多导多少条：这是给人读的快照，不是归档系统；超了在文件里如实写一句。
LEDGER_LIMIT = 300

# 节点状态 → 复选标记。这是导出这一处的排版，不是业务词表（业务词表在 plan.NODE_STATUSES）。
_MARKS = {
    "done": "[x]",
    "skipped": "[-]",
    "in_progress": "[~]",
    "stuck": "[!]",
    "not_started": "[ ]",
}

_LEDGER_ENTITY_LABELS = {
    "plan": "计划",
    "plan_node": "计划节点",
    "candidate": "候选",
    "profile_item": "档案",
    "proposal": "提案",
    "plan_memory": "计划内记忆",
}

_LEDGER_CHANGE_LABELS = {
    "create": "建立",
    "supersede": "取代",
    "void": "作废",
    "status_change": "状态变更",
    "update_fields": "改字段",
    "arbitrate": "裁定",
}

_LEGEND = "图例：[x] 完成 · [-] 跳过 · [~] 进行中 · [!] 卡住 · [ ] 未开始"


def export_dir(out_dir: Path | str | None = None) -> Path:
    if out_dir is not None:
        return Path(out_dir)
    return Path(os.environ.get("CADENCE_EXPORT_DIR") or (PROJECT_ROOT / "exports"))


def _header(title: str, extra: str | None = None) -> list[str]:
    """每个文件都带的那两行：它是谁写的、改了会怎样——省得半年后当成手写笔记去编辑。"""
    lines = [
        f"# {title}",
        "",
        "> 由 cadence 自动导出 · 只读快照 · 在这个文件上做的修改，下次导出会被覆盖",
        f"> 导出时间：{datetime.now().astimezone().isoformat(sep=' ', timespec='seconds')}",
    ]
    if extra:
        lines.append(f"> {extra}")
    lines.append("")
    return lines


def _cell(value: Any) -> str:
    """表格单元格：竖线与换行会把 markdown 表格拆散，统一换成安全字符。"""
    text = "" if value is None else str(value)
    return text.replace("|", "／").replace("\n", " ").strip()


def plan_markdown(conn: sqlite3.Connection, plan_id: int | None = None, today: date | None = None) -> str:
    tree = plan.plan_tree(conn, plan_id=plan_id, today=today)
    if tree["plan"] is None:
        return "\n".join(_header("计划-当前", "当前没有进行中的计划") + ["还没有计划。", ""])
    row = tree["plan"]
    lag = tree["lag"]
    extra = f"计划 #{row['id']}「{row['goal']}」 · 状态 {row['status']}"
    if lag["behind"]:
        extra += f" · 落后 {lag['lag_days']} 天（「{lag['worst']['title']}」）"
    lines = _header("计划-当前", extra)
    lines += [_LEGEND, ""]

    if not tree["stages"]:
        lines += ["（这个计划还没有阶段）", ""]

    for stage in tree["stages"]:
        mark = _MARKS.get(str(stage["status"]), "[ ]")
        lines.append(f"## {mark} {stage['title']}")
        if stage["deliverable"]:
            lines.append(f"要交的东西：{stage['deliverable']}")
        progress = stage["progress"]
        progress_note = f"任务 {progress['settled']}/{progress['total']} 已收尾"
        if stage["lag_days"]:
            progress_note += f" · 落后 {stage['lag_days']} 天"
        lines.append(progress_note)
        submission = stage["deliverable_submission"]
        if submission:
            lines.append(
                f"交付物已提交（{submission['created_at'][:10]}）：{submission['url'] or '（没留链接）'}"
                f" — {submission['note'] or '（没写说明）'}"
            )
        elif progress["all_tasks_settled"] and not stage["finished"]:
            lines.append("交付物还没提交——任务都收尾了，交了这一步才算阶段完成。")
        if stage["tasks"]:
            lines += ["", "### 任务"]
            lines += [_task_line(task) for task in stage["tasks"]]
        if stage["checkpoints"]:
            lines += ["", "### 周打卡"]
            lines += [_task_line(item) for item in stage["checkpoints"]]
        lines.append("")
    return "\n".join(lines)


def _task_line(node: dict[str, Any]) -> str:
    mark = _MARKS.get(str(node["status"]), "[ ]")
    line = f"- {mark} {node['title']}"
    if node["due_date"]:
        line += f"（计划完成日 {node['due_date']}"
        line += f"，落后 {node['lag_days']} 天）" if node["lag_days"] else "）"
    return line


def ledger_markdown(conn: sqlite3.Connection) -> str:
    total = int(conn.execute("SELECT COUNT(*) AS n FROM ledger_event").fetchone()["n"])
    rows = conn.execute(
        "SELECT * FROM ledger_event ORDER BY id DESC LIMIT ?", (LEDGER_LIMIT,)
    ).fetchall()
    extra = f"共 {total} 条流水，这里按时间正序列出最近 {len(rows)} 条"
    lines = _header("决策台账", extra)
    if not rows:
        lines += ["还没有任何流水。", ""]
        return "\n".join(lines)
    lines += [
        "| 时间 | 对象 | 动作 | 之前 → 之后 | 理由 | 经手人 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in reversed(rows):
        entity = _LEDGER_ENTITY_LABELS.get(str(row["entity_type"]), str(row["entity_type"]))
        change = _LEDGER_CHANGE_LABELS.get(str(row["change_type"]), str(row["change_type"]))
        before, after = row["before_value"], row["after_value"]
        transition = "—" if before is None and after is None else f"{_cell(before)} → {_cell(after)}"
        lines.append(
            f"| {_cell(row['created_at'][:19].replace('T', ' '))} | {entity} #{row['entity_id']} "
            f"| {change} | {transition} | {_cell(row['reason']) or '—'} | {_cell(row['actor'])} |"
        )
    lines.append("")
    return "\n".join(lines)


def profile_markdown(conn: sqlite3.Connection) -> str:
    items = ledger.fetch_active(conn, "profile_item")
    extra = f"当前有效条目 {len(items)} 条"
    lines = _header("档案-当前", extra)
    by_category: dict[str, list[sqlite3.Row]] = {}
    for item in items:
        by_category.setdefault(str(item["category"]), []).append(item)

    for token, label in advisor.PROFILE_CATEGORIES.items():
        lines.append(f"## {label}")
        rows = by_category.get(token) or []
        if not rows:
            lines += ["（这一类还是空的）", ""]
            continue
        for item in sorted(rows, key=lambda row: int(row["id"])):
            lines.append(f"- {item['content']}")
            notes: list[str] = []
            if item["source_kind"]:
                notes.append(f"来源：{item['source_kind']}")
            if item["fact_time"]:
                notes.append(f"事实时间：{item['fact_time']}")
            if item["review_at"]:
                notes.append(f"下次复核：{item['review_at']}")
            if notes:
                lines.append(f"  - {' · '.join(notes)}")
        lines.append("")
    unknown = [token for token in by_category if token not in advisor.PROFILE_CATEGORIES]
    if unknown:
        lines += ["## 类别不在五类里的条目", ""]
        lines += [f"- {item['content']}" for token in unknown for item in by_category[token]]
        lines.append("")
    return "\n".join(lines)


def weekly_markdown(
    conn: sqlite3.Connection, plan_id: int | None = None, today: date | None = None
) -> tuple[str, str]:
    """返回 (文件名, 正文)。文件名里的周编号与 `plan.week_key` 同一套写法。"""
    today = today or date.today()
    status = plan.weekly_status(conn, today=today, plan_id=plan_id)
    week = status["week"]
    stage = status.get("current_stage")
    extra = f"本周（{status['week_start']} ~ {status['week_end']}）"
    if stage:
        extra += f" · 当前阶段：{stage['title']}"
    lines = _header("周检查点 " + week, extra)

    lines += ["## 一、当前阶段", ""]
    if stage:
        lines.append(f"{stage['title']}｜要交的东西：{stage['deliverable'] or '（还没写）'}")
        progress = status.get("stage_progress") or {}
        if progress.get("total"):
            lines.append(f"任务 {progress['settled']}/{progress['total']} 已收尾")
    else:
        lines.append("还没有进行中的阶段。")

    lines += ["", "## 二、本周推到哪", ""]
    reports = status.get("reports_this_week") or []
    if reports:
        for report in reports:
            lines.append(f"- {report['created_at'][:16].replace('T', ' ')}｜{report['status']}｜{report['note'] or '（没写说明）'}")
            if report["artifact_url"]:
                lines.append(f"  - 产物：{report['artifact_url']}")
            if report["material_feedback"]:
                lines.append(f"  - 资料评价：{report['material_feedback']}")
    else:
        lines.append("本周还没有报告。")
    if status.get("behind_reason"):
        lines += ["", f"落后：{status['behind_reason']}"]

    lines += ["", "## 三、建议怎么调", ""]
    options = status.get("replan_options") or []
    if options:
        lines += [f"- {option['label']}：{option['detail']}" for option in options]
        lines += ["", "（三个方向只是建议，改不改、怎么改由你定。）"]
    else:
        lines.append("这周没什么要调的。")

    # 成果与复盘（OC-09）：契约标题、验收缺口条数、最新复盘卡结论——与周提醒同一份
    # 确定性快照（plan.outcome_snapshot），照实记账；文件命名与只读性质不变。
    lines += ["", "## 四、成果与复盘", ""]
    plan_id = status.get("plan_id")
    if not plan_id:
        lines.append("还没有进行中的计划。")
    else:
        snapshot = plan.outcome_snapshot(conn, int(plan_id))
        if snapshot["has_contract"]:
            lines.append(
                f"- 成果契约：{snapshot['contract_title']}｜验收缺口 {snapshot['acceptance_gaps']} 条"
            )
        else:
            lines.append("- 还没有成果契约（验收与完成都挂在契约上）。")
        lines.append(f"- 复盘卡：{snapshot['review_card_conclusion']}")
    lines.append("")
    return f"周检查点-{week}.md", "\n".join(lines)


def summary(out_dir: Path | str | None = None) -> dict[str, Any]:
    """看一眼导出目录里现在有什么（**不写文件**）——设置页显示「最近导出」用它。"""
    target = export_dir(out_dir)
    files: list[dict[str, Any]] = []
    if target.is_dir():
        for path in sorted(target.glob("*.md")):
            stat = path.stat()
            files.append({
                "name": path.name,
                "path": str(path),
                "bytes": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
            })
    files.sort(key=lambda item: item["modified"], reverse=True)
    return {
        "dir": str(target),
        "last_at": files[0]["modified"] if files else None,
        "files": files,
    }


def export_all(
    conn: sqlite3.Connection,
    *,
    plan_id: int | None = None,
    today: date | None = None,
    out_dir: Path | str | None = None,
) -> dict[str, Any]:
    """写四个文件，返回它们的清单。只写不读——这是「单向」的落点。"""
    today = today or date.today()
    target = export_dir(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    weekly_name, weekly_text = weekly_markdown(conn, plan_id=plan_id, today=today)
    payloads = {
        "计划-当前.md": plan_markdown(conn, plan_id=plan_id, today=today),
        "决策台账.md": ledger_markdown(conn),
        "档案-当前.md": profile_markdown(conn),
        weekly_name: weekly_text,
    }
    files: list[dict[str, Any]] = []
    for name, text in payloads.items():
        path = target / name
        path.write_text(text, encoding="utf-8")
        stat = path.stat()
        files.append({
            "name": name,
            "path": str(path),
            "bytes": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
        })
    return {
        "dir": str(target),
        "exported_at": db.now_iso(),
        "week": plan.week_key(today),
        "files": files,
    }
