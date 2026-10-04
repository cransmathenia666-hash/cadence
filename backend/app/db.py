"""数据库连接与初始化。

为什么单独一个模块：表结构只有一处来源（sql/schema.sql），
初始化和运行时连接都从这里走，避免表的定义散落在代码各处。
"""

from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import contextmanager
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

# app/db.py -> app -> backend -> 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 默认仍是真实库；CADENCE_DB_PATH 只给演示/录制等隔离场景换库（2026-10-02 黑客松交付）。
DB_PATH = Path(os.environ.get("CADENCE_DB_PATH") or (PROJECT_ROOT / "data" / "cadence.db"))
SCHEMA_PATH = PROJECT_ROOT / "backend" / "sql" / "schema.sql"


def now_iso() -> str:
    """本地时区的 ISO 时间串，秒级精度，直接可读可排序。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Connection(sqlite3.Connection):
    _atomic: bool = False

    def commit(self) -> None:
        if not self._atomic:
            super().commit()


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
    conn = sqlite3.connect(path, check_same_thread=False, factory=Connection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def atomic(conn: sqlite3.Connection) -> Iterator[None]:
    """Serialize a write unit even when ledger helpers commit internally.

    A savepoint alone cannot contain those commits. Defer commit on this connection
    until the outer unit finishes; rollback the whole unit if any operation fails.
    BEGIN IMMEDIATE makes the read/replace decision serial across connections.
    """
    if conn.in_transaction:
        raise RuntimeError("atomic requires a connection without an open transaction")
    conn.execute("BEGIN IMMEDIATE")
    conn._atomic = True
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    else:
        conn._atomic = False
        conn.commit()
    finally:
        conn._atomic = False


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
        # 索引必须在补列之后：老表的新列此刻才存在
        _add_missing_indexes(conn)
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
    # 2026-09-26（工作台问答卡）：计划对话助手这轮带出的结构化追问（JSON 数组）。
    # 可空：不问就不存；历史消息刷新后还要能渲染成问答卡，所以必须落库。
    ("plan_dialogue", "questions", "TEXT"),
    # 2026-09-28（双入口对话整改 I-01）：「找方向」探索线程身份。线程 = 线程头请求的 id：
    # 新线程第一行 thread_id = 自己的 id，同线程后续行都记头请求的 id。可空——加列之前的
    # 旧记录没有线程归属，各自算独立线程，**禁止**靠原话相同猜成同一线程（方案 §6）。
    ("learning_request", "thread_id", "INTEGER"),
    # 2026-09-28（复核整改②）：模型提出的形态切换提案落库（JSON：{from,to,reason}）。
    # 「用户确认切换」不能只凭客户端布尔值放行——后端要能核实这段线程里确有一份待确认的
    # 提案、且确认那轮模型给的形态与提案目标一致。可空：没提过切换就是 NULL。
    ("learning_request", "shape_change", "TEXT"),
    ("learning_request", "utterance", "TEXT"),
    ("learning_request", "intent", "TEXT"),
    ("learning_request", "reply", "TEXT"),
    ("learning_request", "turn_status", "TEXT"),
    # 2026-10-01（成果闭环 P1）：计划的双模式字段。schema.sql 里不建这些列（老表的表
    # 已存在，IF NOT EXISTS 补不了列），默认值与 schema 语义一致：老计划一律 legacy。
    #   flow_version            1 = 旧流程；2 = 成果契约流程（completion_mode=outcome）
    #   completion_mode         legacy / outcome——阶段完成与收尾判定按它分流
    #   contract_review_status  not_required（加列瞬间的占位）→ 老行由 _backfill 标成
    #                           needs_review（该计划尚未补成果契约）；补齐契约后是 ready
    #   closure_kind / closure_reason   closed 状态的解释（completed / stopped）与理由；
    #                           legacy 的旧式收尾两者留空——不把旧 closed 猜成新验收
    ("plan", "flow_version", "INTEGER NOT NULL DEFAULT 1"),
    ("plan", "completion_mode", "TEXT NOT NULL DEFAULT 'legacy'"),
    ("plan", "contract_review_status", "TEXT NOT NULL DEFAULT 'not_required'"),
    ("plan", "closure_kind", "TEXT"),
    ("plan", "closure_reason", "TEXT"),
    # 2026-10-01（成果闭环 P1）：阶段（plan_node）的验收字段。全部可空——老阶段没有
    # 这些信息，也不能替它猜；没有验收条件与契约绑定的阶段不能提交 v2 验收。
    # contract_id 是阶段建立时绑定的契约版本，历史绑定不随契约升级改写；
    # 验收记录自己保存当时的快照（stage_review.criteria_snapshot）。
    ("plan_node", "purpose", "TEXT"),
    ("plan_node", "why_now", "TEXT"),
    ("plan_node", "acceptance_criteria", "TEXT"),
    ("plan_node", "evidence_requirements", "TEXT"),
    ("plan_node", "contract_criterion_ids", "TEXT"),
    ("plan_node", "contract_id", "INTEGER"),
    # 2026-10-01（成果闭环 OC-05）：规划对话消息按**规划会话**归属（planning_session 表）。
    # 可空——老行没有会话归属，仍按 (candidate_id, plan_id) 只读兼容读取。新流程的消息由
    # 服务端从会话解析候选与落点后写入这一列，不信任客户端另传的 plan_id。会话还没有落点
    # 计划（新方向）时消息行的 plan_id 写约定哨兵 0（真实计划 id 从 1 起，这一列在老库上
    # 是 NOT NULL、加列迁不动约束，0 表示「还没有正式计划」）。
    ("plan_chat", "planning_session_id", "INTEGER"),
    # 2026-10-02（成果闭环 OC-08）：报告的复盘回流四列（方案 §5.2 / §6.4）。全部可空——
    # 加列之前的旧报告行没有这些信息，读取按旧报告解释，不替它们猜。review_requested 只
    # 存「用户希望 AI 提调整建议」的意图，不会因此自动调模型；复盘卡始终确定性生成。
    ("report", "stage_node_ids", "TEXT"),
    ("report", "progressed_criteria", "TEXT"),
    ("report", "next_action", "TEXT"),
    ("report", "review_requested", "INTEGER NOT NULL DEFAULT 0"),
    # 2026-10-02（成果闭环 OC-09）：计划对话行的报告上下文关联。可空——没带报告的轮
    # 与加列之前的旧行都是 NULL；落这一列只为「报告上下文可追溯」（方案 §6.4：刷新后
    # 仍能追溯这轮对话是带着哪份报告进的），上下文本体不落库、每轮现拼。
    ("plan_dialogue", "report_id", "INTEGER"),
    # 2026-10-04（退回蓝图的补充信息）：退回时填的补充说明要出现在对话历史、也进模型
    # 上下文，但它不是一轮对话（没触发模型调用），出方案门槛看的是它前面那句助手回话。
    # 可空——加列之前的行与普通聊天都是 NULL。取值见 blueprint.SUPPLEMENT_KIND。
    ("plan_chat", "kind", "TEXT"),
    # 2026-10-04（模型设置）：接入上的模型级设置。全部可空/带默认——老接入一行就是
    # 「没设置」，调用时请求里不带对应字段，行为与加列之前完全一致。
    ("llm_provider", "reasoning_effort", "TEXT"),
    ("llm_provider", "temperature", "REAL"),
    ("llm_provider", "max_output_tokens", "INTEGER"),
    ("llm_provider", "context_window", "INTEGER"),
    ("llm_provider", "supports_web_search", "INTEGER NOT NULL DEFAULT 0"),
    ("llm_provider", "supports_images", "INTEGER NOT NULL DEFAULT 0"),
    ("llm_provider", "extra_body", "TEXT"),
)

# 老表新列上的索引。**必须在 _add_missing_columns 之后建**（列不存在时 CREATE INDEX
# 会直接报错），所以不能进 schema.sql——schema.sql 在 init 里先跑，那时老表还没有这些列。
# 新库两边结果一致：schema.sql 建全表，这里补索引（IF NOT EXISTS，可重复执行）。
_ADDED_INDEXES: tuple[tuple[str, str, str], ...] = (
    # 双模式读取按 completion_mode 分流；计划表就几百行，索引只为让口径有落点可查
    ("idx_plan_completion_mode", "plan", "completion_mode"),
    # 阶段验收要按 contract_id 找「绑定某版契约的阶段」
    ("idx_node_contract", "plan_node", "contract_id"),
    # 规划会话的消息按会话圈线程（OC-05）；老行该列为 NULL，走旧的 (候选, 计划) 索引
    ("idx_plan_chat_session", "plan_chat", "planning_session_id"),
)


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    for table, column, definition in _ADDED_COLUMNS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _add_missing_indexes(conn: sqlite3.Connection) -> None:
    """老表新列上的索引——调用顺序有讲究：必须在 `_add_missing_columns` 之后。"""
    for name, table, expression in _ADDED_INDEXES:
        conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({expression})")


# 数据补齐（不是加列）：加完列之后，老库里那些既有行要有一个人话可读的默认值。
#
# 只做「已有空值填成约定值」这一种，且**可重复执行**：新写入的条目一律自带 `source_kind`
# （`app/profile.py` 的写入口自己填），所以任何时刻 `source_kind` 为空的只可能是
# 记忆系统落地之前那些历史条目——给它们标「历史手工录入」，而不是替它们猜一个来源。
def _backfill(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE profile_item SET source_kind = 'legacy_manual' WHERE source_kind IS NULL"
    )
    # 成果闭环 P1：老计划（以及一切还没补契约的 legacy 计划）标「该计划尚未补成果契约」。
    # 只做标记，**不创建契约行、不猜测验收标准、不把旧交付物或旧 closed 当成新流程的验收**。
    # 可重复执行：补齐契约的行已经是 ready / superseded 场景，不会再被这条 UPDATE 摸到；
    # 'not_required' 只在加列那一刻存在于老行上，新写入的路由都显式给值（legacy 入口给
    # needs_review，成果入口给 ready）。
    conn.execute(
        "UPDATE plan SET contract_review_status = 'needs_review'"
        " WHERE contract_review_status = 'not_required'"
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        print(f"initialized: {init()}")
    else:
        print("usage: python -m app.db init")
