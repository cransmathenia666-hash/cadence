"""清库：把业务数据全清掉，只留模型提供商配置（2026-09-28 用户明确授权）。

与 `wipe_plan_data.py` 的区别：那次只清计划类，刻意保留了长期档案；这次用户在
「长期档案 / 模型配置是否一起清」的选择里答的是「模型提供商留着，其他人全删了」——
所以档案、候选、提案、两种对话、运行记录、交付提交、记忆与扫描记录，以及台账流水
和调用记账，全部物理删除。

保留 `llm_provider`：API 密钥只存在这一张表里，删了得让用户重填一遍才能跑模型。

两条纪律（沿用上一次清库）：
1. **先备份**：默认把库复制成 `data/cadence.db.bak-<时间戳>`，备份失败就不动手；
2. **默认空跑**：不加 `--yes` 只打印将要删多少行，加 `--yes` 才真删。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# 直接 `python tools/wipe_all_data.py` 时只把 tools/ 放进搜索路径，这里补上 backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db  # noqa: E402

# 唯一保留的表
KEEP_TABLES = ("llm_provider",)


def target_tables(conn: sqlite3.Connection) -> list[str]:
    """除保留清单外的所有业务表。

    按 sqlite_master 现查而不是写死表名——以后新加的表自动进入清理范围，不会漏。
    """
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return [row["name"] for row in rows if row["name"] not in KEEP_TABLES]


def report(conn: sqlite3.Connection, tables: list[str]) -> dict[str, int]:
    counts = {
        table: conn.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()["n"]
        for table in tables
    }
    counts["** 保留 ** llm_provider"] = conn.execute(
        "SELECT COUNT(*) AS n FROM llm_provider"
    ).fetchone()["n"]
    return counts


def backup(path: Path) -> Path:
    """用 sqlite 的 backup API 复制，避免直接拷文件时正好拷到写了一半的页。"""
    dest = path.with_name(f"{path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
    source = sqlite3.connect(path)
    clone = sqlite3.connect(dest)
    try:
        source.backup(clone)
    finally:
        clone.close()
        source.close()
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(description="清空业务数据（默认空跑，--yes 才执行）")
    parser.add_argument("--yes", action="store_true", help="真的执行删除（默认只打印）")
    parser.add_argument("--db", type=Path, default=None, help="库文件路径（默认 data/cadence.db）")
    args = parser.parse_args()

    path = Path(args.db) if args.db else db.DB_PATH
    if not path.exists():
        print(f"库不存在：{path}")
        return 1

    conn = db.connect(path)
    try:
        tables = target_tables(conn)
        before = report(conn, tables)
    finally:
        conn.close()

    print(f"库：{path}")
    print("清理前：")
    for name, n in sorted(before.items()):
        print(f"  {name}: {n}")

    if not args.yes:
        print(f"\n以上 {len(tables)} 张表将被清空，`llm_provider` 保留。")
        print("空跑结束（没有动任何数据）。确认无误后加 --yes 执行。")
        return 0

    saved = backup(path)
    print(f"\n已备份：{saved}")

    conn = db.connect(path)
    try:
        for table in tables:
            conn.execute(f'DELETE FROM "{table}"')
        # 自增计数一起归零，让新数据的主键从 1 开始（AUTOINCREMENT 的表才有这张表）
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_sequence'"
        ).fetchone():
            for table in tables:
                conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))
        conn.commit()
        try:
            conn.execute("VACUUM")
        except sqlite3.Error as exc:  # 有别的连接正在用库时 VACUUM 会失败，不影响清理结果
            print(f"（VACUUM 跳过：{exc}）")
    finally:
        conn.close()

    conn = db.connect(path)
    try:
        after = report(conn, tables)
    finally:
        conn.close()

    print("清理后：")
    for name, n in sorted(after.items()):
        print(f"  {name}: {n}")
    print(f"\n完成。文件大小：{path.stat().st_size} bytes。模型提供商配置未动。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
