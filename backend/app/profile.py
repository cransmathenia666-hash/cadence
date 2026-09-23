"""长期档案的写入规则：新增 / 取代 / 作废。

为什么单独一个模块：判重那条规则（同类别下**一字不差的当前有效条目**不许重复）原先
写在接口层，只有 `POST /api/profile` 用得上；从 T28 起「批准一条档案变更提案」也要
走同一道闸——同一件事两处各写一遍，迟早一边改了另一边没改。放到这里之后，
接口层只剩「把领域错误翻成状态码」。

三条写路径全走台账（SPEC 第 8 节铁律）：新增落 active、修改是「取代」（旧值留痕）、
删除是「作废」（`void`，必须写理由）——不存在物理删除，历史永远能回答「当时为什么这么写」。
类别只收 `advisor.PROFILE_CATEGORIES` 那五个约定令牌：库里没有约束，这一层是
「缺失类别」判断还准不准的最后一道闸。
"""

from __future__ import annotations

import sqlite3

from . import ledger
from .advisor import PROFILE_CATEGORIES


class ProfileError(RuntimeError):
    """档案写入链路上的明确错误。"""


class ProfileConflict(ProfileError):
    """与现状冲突：同类别下一字不差的当前有效条目已存在（多半是重复提交）。"""


class ProfileNotFound(ProfileError):
    """条目不存在 → 接口层翻成 404。"""


def require_active(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row:
    """取一条**当前有效**的档案条目。

    不存在与「存在但已不是当前有效值」是两种错：前者 404、后者 409——这条分界在
    接口层靠异常类型区分，所以这里只负责把两种情形分开抛。
    """
    row = conn.execute("SELECT * FROM profile_item WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise ProfileNotFound(f"档案条目 id={item_id} 不存在")
    if row["status"] != "active":
        raise ProfileConflict(
            f"档案条目 id={item_id} 已不是当前有效值（{row['status']}），只能改动当前有效的条目"
        )
    return row


def find_duplicate(
    conn: sqlite3.Connection, category: str, content: str
) -> sqlite3.Row | None:
    """同类别下有没有一字不差的**当前有效**条目。

    为什么只看当前有效的：作废之后重新填一样的文字是正当需求（同「已完成节点不挡同名
    重建」）。为什么这条闸必须存在：档案是给 AI 引用的判据，重复条目会被反复引用、
    还会在页面上越积越多——同 T19 防重复提交的思路。
    """
    return conn.execute(
        "SELECT * FROM profile_item WHERE status = 'active' AND category = ? AND content = ?",
        (category, content.strip()),
    ).fetchone()


def assert_no_duplicate(conn: sqlite3.Connection, category: str, content: str) -> None:
    duplicate = find_duplicate(conn, category, content)
    if duplicate is not None:
        raise ProfileConflict(
            f"档案里已有这条：#{duplicate['id']}（{category}，一字不差）。"
            "不用再补；想改它就用「改」，不想让它算数就「作废」"
        )


def create_item(
    conn: sqlite3.Connection,
    *,
    category: str,
    content: str,
    reason: str | None = None,
    actor: str = "user",
    source_kind: str = "user_stated",
    fact_time: str | None = None,
    review_at: str | None = None,
) -> int:
    """补一条档案：先判重，再落 active。

    `reason` 留给「提案批准」那条路：台账里要能看出这条是谁按哪个提案写进去的。
    `source_kind` 从记忆系统（2026-09-21）起必填默认值：档案多了一列「来源性质」，
    手工敲进来的就是**用户陈述**——留空会让 `db.init` 的补齐步骤把它误标成历史条目。
    """
    if category not in PROFILE_TOKENS:
        raise ProfileError(
            f"类别「{category}」不在约定的五个令牌里：{' / '.join(PROFILE_TOKENS)}"
        )
    cleaned = str(content or "").strip()
    if not cleaned:
        raise ProfileError("档案内容不能为空")
    assert_no_duplicate(conn, category, cleaned)
    return ledger.create_active(
        conn,
        "profile_item",
        {
            "category": category,
            "content": cleaned,
            "source_kind": source_kind,
            "fact_time": fact_time,
            "review_at": review_at,
        },
        actor=actor,
        reason=reason,
    )


def supersede_item(
    conn: sqlite3.Connection, item_id: int, *, content: str, reason: str, actor: str = "user"
) -> int:
    """改一条档案的内容：走台账「取代」，旧值不删、理由留痕。"""
    require_active(conn, item_id)
    if not str(reason or "").strip():
        raise ProfileError("取代必须写明理由，否则台账回答不了「为什么改」")
    return ledger.supersede(
        conn, "profile_item", item_id, {"content": content.strip()}, reason=reason.strip(), actor=actor
    )


def void_item(
    conn: sqlite3.Connection, item_id: int, *, reason: str, actor: str = "user"
) -> None:
    """作废一条档案：从此不在 `GET /api/profile` 里出现，台账留痕。"""
    require_active(conn, item_id)
    if not str(reason or "").strip():
        raise ProfileError("作废必须写明理由")
    ledger.void(conn, "profile_item", item_id, reason=reason.strip(), actor=actor)


# 五个约定令牌，直接从 `advisor.PROFILE_CATEGORIES` 取键——不另抄一份。
# 为什么可以直接引用它：`advisor` 是读档案的那一侧，它不 import 本模块，不会绕成环。
PROFILE_TOKENS: tuple[str, ...] = tuple(PROFILE_CATEGORIES)