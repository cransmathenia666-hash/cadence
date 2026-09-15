"""LLM 接入：多家 provider 的配置管理 + 调用记账 + 循环保护。

三条纪律来自 SPEC 第 10 节的「LLM 调用纪律」：
1. 只在四处低频环节调用（提交输入、生成候选、拆分阶段、压缩对话）。本模块不管"在哪调"，
   但用 `Operation` 把"一次操作能调几次"卡住。
2. 失败如实报错，不猜（重试一次的策略在 T12 的结构化输出那里，不在本模块）。
3. **不设预算上限**，改为两件事：每次调用写一行 `llm_call` 记账 + 同一操作最多 3 次调用。
   防的是 bug 导致的循环重试，不是限制你正常使用。

密钥铁律（SPEC 第 10 节第 4 条）：**只写不读**——本模块对外一律只回掩码，
永不回传明文，也不把它写进日志或错误信息。
"""

from __future__ import annotations

import json
import sqlite3
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from .db import now_iso
from .plan import parse_date, week_key

# 同一操作内允许的最大模型调用次数。
MAX_CALLS_PER_OPERATION = 3

# 测连通性也走记账，但用单独的 task 名，免得混进业务调用的统计里。
CONNECTIVITY_TASK = "connectivity_test"

# 一次"发 JSON、收 JSON"的传输函数。默认用标准库 urllib（项目刻意不加 HTTP 依赖）；
# 单测传假函数进来打桩，这样永远不打真实接口。
Transport = Callable[[str, dict[str, str], dict[str, Any]], tuple[int, Any]]


class LlmError(RuntimeError):
    """LLM 层面的明确错误：没有可用 provider、调用超限、上游返回异常等。

    与台账、计划同样的态度：明确报错，不静默忽略。
    """


class DuplicateProvider(LlmError):
    """已经有一家同名 provider。

    单独一个类型，是为了让接口层能把它翻成 409（冲突）而不是 400（参数错）——
    与 `plan.DuplicateNode` 同一个理由：请求本身没错，是和现有数据撞了。
    """


# ---------- 密钥掩码：只写不读 ----------

def mask_key(api_key: str | None) -> str | None:
    """把密钥变成可展示的掩码：只留末 4 位。

    为什么留末 4 位：你得能分辨"列表里这两家是不是同一把钥匙"，末 4 位够区分，
    又不足以复原整把（业界常见做法）。短于 8 位的一律全掩，不给尾巴——
    太短的密钥露出 4 位就露掉一半了。
    """
    if not api_key:
        return None  # 没配就是没配，不编一个掩码出来假装配了
    if len(api_key) < 8:
        return "****"
    return f"****{api_key[-4:]}"


def public_provider(row: sqlite3.Row) -> dict[str, Any]:
    """把一行 provider 变成可以给前端的形状——**这里绝不包含明文密钥**。"""
    return {
        "id": row["id"],
        "name": row["name"],
        "base_url": row["base_url"],
        "default_model": row["default_model"],
        "is_default": bool(row["is_default"]),
        "enabled": bool(row["enabled"]),
        "has_api_key": bool(row["api_key"]),
        "api_key_masked": mask_key(row["api_key"]),
        "created_at": row["created_at"],
    }


# ---------- provider 的增删改查 ----------

def get_provider(conn: sqlite3.Connection, provider_id: int) -> sqlite3.Row | None:
    """取一行的**原始**记录（含明文密钥）。只给本模块内部用，不要往外送。"""
    return conn.execute("SELECT * FROM llm_provider WHERE id = ?", (provider_id,)).fetchone()


def list_providers(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """列出全部 provider。返回的是 `public_provider`，密钥只有掩码。"""
    rows = conn.execute("SELECT * FROM llm_provider ORDER BY id").fetchall()
    return [public_provider(row) for row in rows]


def create_provider(
    conn: sqlite3.Connection,
    *,
    name: str,
    base_url: str | None = None,
    api_key: str | None = None,
    default_model: str | None = None,
) -> int:
    """新增一家 provider。名称唯一，重名要让调用方看到明确错误而不是 500。"""
    if not str(name or "").strip():
        raise LlmError("provider 名称不能为空")
    try:
        cursor = conn.execute(
            "INSERT INTO llm_provider"
            " (name, base_url, api_key, default_model, is_default, enabled, created_at)"
            " VALUES (?, ?, ?, ?, 0, 1, ?)",
            (str(name).strip(), base_url, api_key, default_model, now_iso()),
        )
    except sqlite3.IntegrityError as error:
        raise DuplicateProvider(f"已经有一家叫「{name}」的 provider 了") from error
    conn.commit()
    return int(cursor.lastrowid)


def update_provider(
    conn: sqlite3.Connection,
    provider_id: int,
    *,
    name: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    default_model: str | None = None,
    enabled: bool | None = None,
    set_as_default: bool = False,
) -> None:
    """改一家 provider。

    `api_key` **只有显式传了非空值才动**：界面回填不了密钥（我们只给它掩码），
    如果"没传就当成清空"，你改个名字就会顺手把钥匙擦掉。
    """
    if get_provider(conn, provider_id) is None:
        raise LlmError(f"provider id={provider_id} 不存在")

    updates: dict[str, Any] = {}
    if name is not None:
        if not str(name).strip():
            raise LlmError("provider 名称不能为空")
        updates["name"] = str(name).strip()
    if base_url is not None:
        updates["base_url"] = base_url
    if default_model is not None:
        updates["default_model"] = default_model
    if api_key:  # 空串 / None 都视为"不改密钥"
        updates["api_key"] = api_key
    if enabled is not None:
        updates["enabled"] = 1 if enabled else 0

    if updates:
        assignments = ", ".join(f"{column} = ?" for column in updates)
        try:
            conn.execute(
                f"UPDATE llm_provider SET {assignments} WHERE id = ?",
                (*updates.values(), provider_id),
            )
        except sqlite3.IntegrityError as error:
            # 改名撞上已有的名字：和创建重名同一种事，翻成同一个异常
            raise DuplicateProvider(f"已经有一家叫「{updates.get('name')}」的 provider 了") from error

    if set_as_default:
        # 默认只能有一家：先把别人的默认标记清掉，再立这一家
        conn.execute("UPDATE llm_provider SET is_default = 0")
        conn.execute("UPDATE llm_provider SET is_default = 1 WHERE id = ?", (provider_id,))

    conn.commit()


def delete_provider(conn: sqlite3.Connection, provider_id: int) -> None:
    """删一家 provider。

    **不删它的调用记账**：账是历史（这周花了多少），provider 没了账还得留着。
    """
    if get_provider(conn, provider_id) is None:
        raise LlmError(f"provider id={provider_id} 不存在")
    conn.execute("DELETE FROM llm_provider WHERE id = ?", (provider_id,))
    conn.commit()


def resolve_provider(conn: sqlite3.Connection, provider_id: int | None = None) -> sqlite3.Row:
    """取要用哪一家：指定了就用指定的，没指定就用默认的那家。"""
    if provider_id is not None:
        row = get_provider(conn, provider_id)
        if row is None:
            raise LlmError(f"provider id={provider_id} 不存在")
        if not row["enabled"]:
            raise LlmError(f"provider「{row['name']}」已被停用")
        return row

    row = conn.execute(
        "SELECT * FROM llm_provider WHERE is_default = 1 AND enabled = 1 ORDER BY id LIMIT 1"
    ).fetchone()
    if row is None:
        raise LlmError("还没有可用的 provider：先新增一家、设为默认并启用")
    return row


# ---------- 调用记账 ----------

def record_call(
    conn: sqlite3.Connection,
    *,
    provider_id: int | None,
    model: str | None,
    task: str,
    input_tokens: int | None,
    output_tokens: int | None,
    duration_ms: int,
    ok: bool,
    error: str | None = None,
) -> int:
    """写一行调用记账。**失败的调用也要记**——只记成功的账等于没账。"""
    cursor = conn.execute(
        "INSERT INTO llm_call"
        " (provider_id, model, task, input_tokens, output_tokens, duration_ms, ok, error, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (provider_id, model, task, input_tokens, output_tokens, duration_ms,
         1 if ok else 0, error, now_iso()),
    )
    conn.commit()
    return int(cursor.lastrowid)


def list_calls(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    """最近的调用流水，新的在前。"""
    rows = conn.execute(
        "SELECT * FROM llm_call ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [
        {
            "id": row["id"],
            "provider_id": row["provider_id"],
            "model": row["model"],
            "task": row["task"],
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "duration_ms": row["duration_ms"],
            "ok": bool(row["ok"]),
            "error": row["error"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def call_summary_by_week(conn: sqlite3.Connection, limit_weeks: int = 8) -> list[dict[str, Any]]:
    """按周汇总调用：条数、成功失败、tokens 合计、耗时合计。

    为什么不设预算上限就要有这个：不设上限的前提是"看得见花了多少"，
    看不见就没法判断要不要收敛。周的起止沿用 `plan.week_key`——
    周的定义只留一处（与导出的周文件名同一套写法），免得两处对不上账。
    """
    buckets: dict[str, dict[str, Any]] = {}
    for row in conn.execute("SELECT * FROM llm_call ORDER BY created_at"):
        day = parse_date(row["created_at"])
        key = week_key(day) if day is not None else "未知"
        bucket = buckets.setdefault(
            key,
            {"week": key, "calls": 0, "ok": 0, "failed": 0,
             "input_tokens": 0, "output_tokens": 0, "duration_ms": 0, "errors": []},
        )
        bucket["calls"] += 1
        if row["ok"]:
            bucket["ok"] += 1
        else:
            bucket["failed"] += 1
            if row["error"]:
                bucket["errors"].append(str(row["error"])[:120])
        bucket["input_tokens"] += row["input_tokens"] or 0
        bucket["output_tokens"] += row["output_tokens"] or 0
        bucket["duration_ms"] += row["duration_ms"] or 0
    return sorted(buckets.values(), key=lambda item: item["week"], reverse=True)[:limit_weeks]


# ---------- 真正发请求 ----------

def post_json(
    url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float = 30.0
) -> tuple[int, Any]:
    """发一个 JSON POST。用标准库 urllib——项目刻意不加 HTTP 依赖（见 SPEC 第 10 节）。

    连不上、超时这类异常**不在这里吞掉**：交给调用方记账并如实报错。
    """
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        return error.code, {"__raw__": body}


def _chat_payload(model: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    """OpenAI 兼容的请求体。多家 provider 都提供这个形状的接口，所以只写一种。"""
    return {"model": model, "messages": messages}


def _endpoint(provider: sqlite3.Row) -> str:
    base = str(provider["base_url"] or "").rstrip("/")
    if not base:
        raise LlmError(f"provider「{provider['name']}」没有配 base_url")
    return f"{base}/chat/completions"


def _parse_reply(status: int, body: Any) -> tuple[str, int | None, int | None]:
    """从 OpenAI 兼容的回复里取出正文与 token 用量。"""
    if status != 200:
        raise LlmError(f"上游返回 {status}：{str(body)[:200]}")
    if not isinstance(body, dict):
        raise LlmError(f"上游返回的不是 JSON 对象：{str(body)[:200]}")
    choices = body.get("choices") or []
    if not choices:
        raise LlmError(f"上游返回里没有 choices：{str(body)[:200]}")
    text = ((choices[0].get("message") or {}).get("content")) or ""
    usage = body.get("usage") or {}
    return text, usage.get("prompt_tokens"), usage.get("completion_tokens")


class Operation:
    """一次操作（比如"生成候选"）的上下文：卡住调用次数，逐次记账。

    为什么要有这个类：调用不设预算上限，代价是"万一代码写错进了循环"就会一直烧。
    把它做成结构上的约束——第 4 次直接抛错并中止——而不是靠写代码的人自觉。
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        task: str,
        *,
        limit: int = MAX_CALLS_PER_OPERATION,
        transport: Transport | None = None,
    ) -> None:
        self._conn = conn
        self.task = task
        self._limit = limit
        self._transport = transport or post_json
        self._used = 0

    @property
    def used(self) -> int:
        """这次操作已经调了几次。"""
        return self._used

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        provider_id: int | None = None,
        model: str | None = None,
    ) -> str:
        """调一次模型，返回正文。超限、上游出错、没配 provider 都抛 `LlmError`。"""
        if self._used >= self._limit:
            raise LlmError(
                f"这次操作已经调了 {self._used} 次模型，超过上限 {self._limit}；"
                "中止，不做第 4 次——这道闸防的是代码里的循环重试"
            )

        provider = resolve_provider(self._conn, provider_id)
        chosen_model = model or provider["default_model"]
        if not chosen_model:
            raise LlmError(f"provider「{provider['name']}」既没配默认模型，调用时也没指定模型")

        self._used += 1
        started = time.perf_counter()
        input_tokens: int | None = None
        output_tokens: int | None = None
        error_text: str | None = None
        text = ""
        try:
            status, body = self._transport(
                _endpoint(provider),
                {"Authorization": f"Bearer {provider['api_key'] or ''}"},
                _chat_payload(chosen_model, messages),
            )
            text, input_tokens, output_tokens = _parse_reply(status, body)
        except Exception as cause:  # 上游怎么错都要记账，不能"失败了就不记"
            error_text = f"{type(cause).__name__}: {cause}"
        finally:
            record_call(
                self._conn,
                provider_id=int(provider["id"]),
                model=chosen_model,
                task=self.task,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_ms=int((time.perf_counter() - started) * 1000),
                ok=error_text is None,
                error=error_text,
            )

        if error_text is not None:
            raise LlmError(f"调用 provider「{provider['name']}」失败：{error_text}")
        return text


def test_provider(
    conn: sqlite3.Connection, provider_id: int, *, transport: Transport | None = None
) -> dict[str, Any]:
    """体检：发一个最小请求看通不通。

    为什么单独一条：配 provider 最容易错的就是 base_url 写错、密钥失效，
    这两类错误必须在"真正拿它干活之前"暴露出来。

    它**不占**「同一操作最多 3 次」的额度——那道闸是给业务操作用的，不是给体检用的；
    但它照样写一行记账（task 为 `connectivity_test`），因为那也是真花了钱。
    """
    provider = get_provider(conn, provider_id)
    if provider is None:
        raise LlmError(f"provider id={provider_id} 不存在")

    send = transport or post_json
    model = provider["default_model"]
    started = time.perf_counter()
    input_tokens: int | None = None
    output_tokens: int | None = None
    error_text: str | None = None
    try:
        if not model:
            raise LlmError("没有配默认模型，测不了连通性")
        status, body = send(
            _endpoint(provider),
            {"Authorization": f"Bearer {provider['api_key'] or ''}"},
            {**_chat_payload(model, [{"role": "user", "content": "ping"}]), "max_tokens": 1},
        )
        _, input_tokens, output_tokens = _parse_reply(status, body)
    except Exception as cause:
        error_text = f"{type(cause).__name__}: {cause}"
    finally:
        duration_ms = int((time.perf_counter() - started) * 1000)
        record_call(
            conn,
            provider_id=provider_id,
            model=model,
            task=CONNECTIVITY_TASK,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=duration_ms,
            ok=error_text is None,
            error=error_text,
        )

    if error_text is not None:
        return {"ok": False, "detail": error_text}
    return {"ok": True, "detail": f"连通（模型 {model}，{duration_ms} 毫秒）"}
