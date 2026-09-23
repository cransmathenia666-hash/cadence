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
    # check_same_thread=False：FastAPI 把**同步依赖**（`main.get_conn`）与**同步接口**分两次
    # 丢进线程池，两次不保证落在同一个工作线程上，而 sqlite3 默认禁止跨线程使用一条连接
    # ——于是同一个请求先在一个线程里建连接、再到另一个线程里查询，就会抛
    # 「SQLite objects created in a thread can only be used in that same thread」。
    # （本机的 Python 3.14 + 现版 anyio 上几乎每次都撞，页面表现为零星的 500 与「连不上后端」。）
    #
    # 关掉这道检查是安全的：连接**每条请求一条**（见 `main.get_conn`），不跨请求共享，
    # 线程之间只是先后使用、不是同时使用；本机 sqlite3 的 threadsafety 是 3（串行模式），
    # 底层自己会加锁。
    conn = sqlite3.connect(path, check_same_thread=False)
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
        _backfill(conn)
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
    # 2026-09-21（T37）：采纳时落进了哪个计划。「新方向」的候选落点是采纳那一刻现选的，
    # 不记下来，刷新一次页面规划对话就不知道自己在哪个计划里。
    ("candidate", "landing_plan_id", "INTEGER"),
    # 2026-09-21（记忆系统）：全局长期记忆的三列元数据（事实时间 / 复核时间 / 来源性质）。
    # 可空——老条目没有这些信息，也不能替它猜。
    ("profile_item", "fact_time", "TEXT"),
    ("profile_item", "review_at", "TEXT"),
    ("profile_item", "source_kind", "TEXT"),
    # 2026-09-21（记忆走查整改第 3 条）：墓碑上的计划归属。彻底删除之后记忆行已经没了，
    # 没有这一列就算不出「这个计划里刚删过一条记忆」——记忆变化摘要要靠它。
    # 可空：加列之前删掉的那些没有归属。
    ("memory_deletion", "plan_id", "INTEGER"),
)


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    for table, column, definition in _ADDED_COLUMNS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


# 数据补齐（不是加列）：加完列之后，老库里那些既有行要有一个人话可读的默认值。
#
# 只做「已有空值填成约定值」这一种，且**可重复执行**：新写入的条目一律自带 `source_kind`
# （`app/profile.py` 的写入口自己填），所以任何时刻 `source_kind` 为空的只可能是
# 记忆系统落地之前那些历史条目——给它们标「历史手工录入」，而不是替它们猜一个来源。
def _backfill(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE profile_item SET source_kind = 'legacy_manual' WHERE source_kind IS NULL"
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        print(f"initialized: {init()}")
    else:
        print("usage: python -m app.db init")
