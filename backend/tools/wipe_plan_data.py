"""一次性清库：把计划相关的数据物理删掉（2026-09-17 用户明确授权）。

为什么物理删而不是走台账作废：用户为「三级结构改造」选了「不做老数据兼容」——
老计划本来就要清，物理删最干净。代价是这部分历史证据一并消失（连台账流水），
这是用户已知并拍板的选择；长期档案与调用记账**不动**。

保留：
- `profile_item`（含它的台账流水）——长期档案是判据底座，用户补了很久；
- `llm_provider`（含真密钥配置）与 `llm_call`（调用记账）——与计划无关。

删除：`plan` / `plan_node` / `report` / `proposal` / `candidate` / `learning_request` /
`notification_log` 的行，以及台账流水里这几类实体的记录。

两条纪律：
1. **先备份**：默认把库复制成 `data/cadence.db.bak-<时间戳>`，备份失败就不动手；
2. **默认空跑**：不加 `--yes` 只打印将要删多少行，加 `--yes` 才真删。
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# 直接 `python tools/wipe_plan_data.py` 时只把 tools/ 放进搜索路径，这里补上 backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db  # noqa: E402

# 要清的实体（表名 = 台账里的 entity_type，两处同名）
WIPE_TABLES = ("plan", "plan_node", "report", "proposal", "candidate", "learning_request", "notification_log")
# 台账流水里同样清掉这些实体的事件；档案与 provider 的流水保留
WIPE_EVENT_TYPES = WIPE_TABLES


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    result = {}
    for table in WIPE_TABLES:
        result[table] = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
    result["ledger_event(计划类)"] = conn.execute(
        f"SELECT COUNT(*) AS n FROM ledger_event WHERE entity_type IN ({','.join('?' * len(WIPE_EVENT_TYPES))})",
        WIPE_EVENT_TYPES,
    ).fetchone()["n"]
    result["profile_item(保留)"] = conn.execute("SELECT COUNT(*) AS n FROM profile_item").fetchone()["n"]
    result["llm_provider(保留)"] = conn.execute("SELECT COUNT(*) AS n FROM llm_provider").fetchone()["n"]
    result["llm_call(保留)"] = conn.execute("SELECT COUNT(*) AS n FROM llm_call").fetchone()["n"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="清空计划相关数据（默认空跑，--yes 才执行）")
    parser.add_argument("--yes", action="store_true", help="真的执行删除（默认只打印）")
    parser.add_argument("--db", type=Path, default=None, help="库文件路径（默认 data/cadence.db）")
    args = parser.parse_args()

    path = Path(args.db) if args.db else db.DB_PATH
    if not path.exists():
        print(f"库不存在：{path}")
        return 1

    before = counts(db.connect(path))
    print(f"库：{path}")
    print("清理前：")
    for name, n in before.items():
        print(f"  {name}: {n}")

    if not args.yes:
        print("\n空跑结束（没有动任何数据）。确认无误后加 --yes 执行。")
        return 0

    backup = path.with_name(f"{path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(path, backup)
    print(f"\n已备份：{backup}")

    conn = db.connect(path)
    try:
        for table in WIPE_TABLES:
            conn.execute(f"DELETE FROM {table}")
        conn.execute(
            f"DELETE FROM ledger_event WHERE entity_type IN ({','.join('?' * len(WIPE_EVENT_TYPES))})",
            WIPE_EVENT_TYPES,
        )
        conn.commit()
    finally:
        conn.close()

    after = counts(db.connect(path))
    print("清理后：")
    for name, n in after.items():
        print(f"  {name}: {n}")
    print("\n完成。档案、provider 配置与调用记账未动。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
