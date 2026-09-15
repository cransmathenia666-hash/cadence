"""只读打印 cadence 库里的内容：计划树、报告、台账流水、待裁定提案。

为什么留这么一把固定的工具：这台机器上没有 `sqlite3` 命令行，
而"库里现在到底是什么状态"是最常要回答的问题——之前每次都得临时写脚本导出 JSON。
有了它，任何人（包括不写代码的你）都能一条命令看到全貌。

两条纪律写死在代码里：
1. 只用 SELECT，**数据库连接本身以只读模式打开**（`mode=ro`），所以它想写也写不进去；
2. 业务判断不在这里重算，一律调用 `app/plan.py` 的函数——落后量、当前阶段的口径只有一处。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# 直接 `python tools/show_db.py` 时，Python 只把 tools/ 放进搜索路径，
# 所以这里手动把 backend/ 加进去，才能 import app.*
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db, ledger, plan  # noqa: E402

STATUS_LABELS = {
    "not_started": "未开始",
    "in_progress": "进行中",
    "done": "完成",
    "stuck": "卡住",
    "skipped": "跳过",
    "active": "生效中",
    "superseded": "已被取代",
    "void": "已作废",
    "closed": "已收尾",
    "pending": "待裁定",
    "accepted": "已采纳",
    "rejected": "已否决",
}


def label(status: str) -> str:
    return f"{status}={STATUS_LABELS[status]}" if status in STATUS_LABELS else status


def short(text: object, width: int = 70) -> str:
    """把长文本压成一行，方便在终端里看。"""
    value = " ".join(str(text or "").split())
    return value if len(value) <= width else value[: width - 1] + "…"


def open_readonly(path: Path) -> sqlite3.Connection:
    """以只读方式打开库。写操作会在 SQLite 层面直接失败，不靠"我记得别写"。"""
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def show_plan_tree(conn: sqlite3.Connection, plan_row: sqlite3.Row) -> None:
    tree = plan.plan_tree(conn, plan_id=int(plan_row["id"]))
    lag = tree["lag"]
    stage = tree["current_stage"]
    print(f"计划 {plan_row['id']} · {plan_row['goal']}  [{label(plan_row['status'])}]  建于 {plan_row['valid_from']}")
    worst = f"（最堵的是「{lag['worst']['title']}」，到期 {lag['worst']['due_date']}）" if lag["worst"] else ""
    print(f"  落后量：{lag['lag_days']} 天{worst}")
    print(f"  当前阶段：{stage['title'] if stage else '无（所有阶段都已收尾）'}")

    if not tree["stages"]:
        print("  （还没有阶段与检查点）")
    for stage_row in tree["stages"]:
        progress = stage_row["progress"]
        print(
            f"  └─ 阶段 {stage_row['id']} · {stage_row['title']}  [{label(stage_row['status'])}]"
            f"  进度 {progress['settled']}/{progress['total']} 收尾"
            f"（完成 {progress['done']} / 跳过 {progress['skipped']}）"
        )
        if stage_row["deliverable"]:
            print(f"       交付物：{stage_row['deliverable']}")
        for checkpoint in stage_row["checkpoints"]:
            lag_text = "—" if checkpoint["lag_days"] is None else f"{checkpoint['lag_days']} 天"
            print(
                f"       ├─ 检查点 {checkpoint['id']} · {checkpoint['title']}"
                f"  [{label(checkpoint['status'])}]"
                f"  到期 {checkpoint['due_date'] or '未定'}  落后 {lag_text}"
            )


def show_reports(conn: sqlite3.Connection, limit: int) -> None:
    rows = conn.execute(
        """SELECT id, node_id, status, note, artifact_url, created_at
           FROM report ORDER BY id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    print(f"\n最近 {len(rows)} 条报告")
    if not rows:
        print("  （还没有报告）")
    for row in reversed(rows):
        artifact = f"  产物：{row['artifact_url']}" if row["artifact_url"] else ""
        print(f"  #{row['id']} 节点 {row['node_id']} [{row['status']}] {short(row['note'], 40)}"
              f"  {row['created_at']}{artifact}")


def show_ledger(conn: sqlite3.Connection, limit: int) -> None:
    rows = conn.execute(
        """SELECT id, entity_type, entity_id, change_type, before_value, after_value, reason, actor
           FROM ledger_event ORDER BY id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    print(f"\n最近 {len(rows)} 条台账流水（时间正序）")
    if not rows:
        print("  （台账还是空的）")
    for row in reversed(rows):
        line = (
            f"  #{row['id']} {row['entity_type']} #{row['entity_id']} {row['change_type']}"
            f"：{short(row['before_value'], 24)} → {short(row['after_value'], 40)}"
            f"  （{row['actor']}）"
        )
        if row["reason"]:
            line += f"\n        理由：{short(row['reason'])}"
        print(line)


def show_proposals(conn: sqlite3.Connection) -> None:
    rows = ledger.fetch_active(conn, "proposal")
    print(f"\n待裁定提案：{len(rows)} 条")
    if not rows:
        print("  （没有待裁定的提案）")
    for row in rows:
        print(f"  #{row['id']} [{row['kind']}] {short(row['reason'])}")


def main() -> int:
    parser = argparse.ArgumentParser(description="只读打印 cadence 库内容")
    parser.add_argument("--db", help="库文件路径，默认用 app.db 里的 data/cadence.db")
    parser.add_argument("--limit", type=int, default=10, help="报告与台账各显示多少条，默认 10")
    args = parser.parse_args()

    path = Path(args.db) if args.db else db.DB_PATH
    if not path.exists():
        print(f"库文件不存在：{path}")
        print("先建库：.venv\\Scripts\\python.exe -m app.db init")
        return 1

    conn = open_readonly(path)
    try:
        print(f"cadence 库（只读模式）：{path}\n")
        plans = conn.execute("SELECT * FROM plan ORDER BY id").fetchall()
        print(f"计划共 {len(plans)} 个")
        for plan_row in plans:
            print()
            show_plan_tree(conn, plan_row)
        show_reports(conn, args.limit)
        show_ledger(conn, args.limit)
        show_proposals(conn)
        print("\n状态对照：" + "，".join(f"{key}={value}" for key, value in STATUS_LABELS.items()))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
