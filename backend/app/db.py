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
    """建表。可重复执行——schema 里全部是 IF NOT EXISTS。"""
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()
    return path


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        print(f"initialized: {init()}")
    else:
        print("usage: python -m app.db init")
