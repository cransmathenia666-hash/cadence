"""数据库连接与初始化。

为什么单独一个模块：表结构只有一处来源（sql/schema.sql），
初始化和运行时连接都从这里走，避免表的定义散落在代码各处。
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# app/db.py -> app -> backend -> 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "data" / "cadence.db"
SCHEMA_PATH = PROJECT_ROOT / "backend" / "sql" / "schema.sql"


def now_iso() -> str:
    """本地时区的 ISO 时间串，秒级精度，直接可读可排序。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init(db_path: Path | str | None = None) -> Path:
    """建表 + 补齐后续加上的列。可重复执行。

    表结构只有一处来源（schema.sql），但 `CREATE TABLE IF NOT EXISTS` 不会给**已存在**的
    表补列——所以从 2026-09-17 起，往老表加列要走下面的 `_ADDED_COLUMNS`：缺了才补，
    不删不改。新库由 schema.sql 直接建全，老库靠这一步追平，两边结果一致。
    """
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _add_missing_columns(conn)
        conn.commit()
    finally:
        conn.close()
    return path


# (表, 列, 列定义)：往老表补列的清单。只允许「加列」，不做改名与删除。
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    # 2026-09-17（T24）：一轮「找」属于哪个计划——候选随请求继承归属
    ("learning_request", "plan_id", "INTEGER"),
    # 2026-09-20（T34）：路径形状的步骤草案。`candidate` 加一列，与 `proposal.payload` 同一用法
    ("candidate", "payload", "TEXT"),
    # 2026-09-20（T36）：这一轮问出的追问（JSON：{question, missing, answer}）。
    # 「问过的不再问」要求下一轮读得到，而反馈流水是从这张表拼的——所以它必须落库。
    ("learning_request", "clarify", "TEXT"),
)


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    for table, column, definition in _ADDED_COLUMNS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        print(f"initialized: {init()}")
    else:
        print("usage: python -m app.db init")
