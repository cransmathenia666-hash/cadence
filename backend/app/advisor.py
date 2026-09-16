"""四问判断：把「我发现了一个资料，要不要学」交给 LLM，产出**可回溯**的四问答案。

为什么强调"可回溯"：SPEC 第 9 节成功标准 3 要求每条答案能指回长期档案里的**具体字段**，
而不是一段听着有道理的空话。所以这里的合格线很硬——每条答案都要带 `profile_item` 的 id；
指不回去就算不合格：宁可重试、宁可报错，也不放空话进库。

两条边界（和项目对 LLM 的一贯态度一致）：
- LLM 只产出**提案**：结果落进 `proposal` 表等你裁定，绝不直接改档案或计划。
- 调用次数由 `llm.Operation` 卡着（同一操作最多 3 次）；这里的"重试一次"用掉 2 次。

前端入口在 T14；本题（T12）只开后端这条链路。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from . import ledger, llm
from .db import now_iso


class AdvisorError(RuntimeError):
    """决策链路上的明确错误：没档案、模型连着两次输出不合格、上游调用失败。

    一律明确报错而不是"返回一段凑合的建议"——这本产品的价值就在判断质量，
    悄悄给个平庸答案比报错更糟。
    """


# 记在 llm_call.task 里的任务名，和 connectivity_test 之类区分开
TASK = "judge"

# 落进 proposal.kind 的取值。为什么不复用现有三个（profile_change / plan_replan /
# stage_advance）：它们分别是"改档案""重排计划""推进阶段"，都不是"判一个资料"。
# 只多一个字符串取值，不动表结构、不用迁移；T14 的裁定界面靠它分流。
MATERIAL_JUDGMENT_KIND = "material_judgment"

# 长期档案的五类（SPEC 第 5.1 节）。库里 category 是自由文本、没有数据库层面的约束，
# 这张表就是**约定的词表**：目前只有 `long_axis` 与 `life_log` 真实用过，另外三个是按
# 同一命名风格先定下来的。表外的类别不会丢——照样原样传给模型，只是不算进"缺失类别"。
PROFILE_CATEGORIES: dict[str, str] = {
    "life_habit": "生活习惯（睡眠/运动/作息）",
    "life_log": "生活记录（日程/课程/近况）",
    "current_state": "当前状态（精力/时间/压力）",
    "short_term_goal": "短期目标 + 当下痛点",
    "long_axis": "长期主线（职业方向）",
}

# 四问：键名固定、顺序固定。prompt 与校验共用这一份，免得两处各写一遍走偏。
QUESTIONS: dict[str, str] = {
    "worth_learning": "① 值不值得学（对长期主线 / 当下痛点的贡献）",
    "depth_target": "② 学到什么程度（浅尝 / 够用 / 熟练 / 精通）",
    "intensity": "③ 板块分级（深学 还是 过一遍）",
    "time_budget": "④ 时间预算（按当前状态排期）",
}

# 每问的判据来自哪几类档案——照抄 SPEC 第 4 节那张表，不在这里另编一套。
JUDGE_SOURCES: dict[str, tuple[str, ...]] = {
    "worth_learning": ("long_axis", "short_term_goal"),
    "depth_target": ("long_axis", "short_term_goal"),
    "intensity": ("long_axis", "current_state"),
    "time_budget": ("current_state", "life_habit", "life_log"),
}

SYSTEM_PROMPT = (
    "你是学习决策助手。你只输出一个 JSON 对象：不要解释、不要客套、不要 Markdown 代码块。"
)


# ---------- 输出的形状（LLM 的答案先过这里） ----------

class Answer(BaseModel):
    """一问的答案：一句话 + 它依据的档案 id。

    `answer` 不许为空——空答案就是"没回答"，不该被当成合格输出放过去。
    """

    answer: str = Field(min_length=1)
    profile_item_ids: list[int] = Field(default_factory=list)


class Judgment(BaseModel):
    """四问齐全才算合格。少一问会被 Pydantic 直接拦住（不补默认值，不猜）。"""

    worth_learning: Answer
    depth_target: Answer
    intensity: Answer
    time_budget: Answer


# ---------- 读档案 ----------

def read_profile(conn: sqlite3.Connection) -> dict[str, Any]:
    """长期档案的当前有效值（`GET /api/profile` 就返回这个）。

    "当前有效"由台账保证：`superseded` / `void` 的旧值不在这里出现。
    """
    rows = ledger.fetch_active(conn, "profile_item")
    items = [
        {
            "id": int(row["id"]),
            "category": row["category"],
            "content": row["content"],
            "valid_from": row["valid_from"],
        }
        for row in rows
    ]
    present = {item["category"] for item in items}
    return {
        "items": items,
        "missing_categories": [name for name in PROFILE_CATEGORIES if name not in present],
    }


def record_request(conn: sqlite3.Connection, kind: str, raw_text: str) -> int:
    """把你的这一轮输入记一行。

    这张表是**追加式日志**、不是带状态机业务表，所以不经台账（`ledger` 只管有状态的
    对象）；和 `llm.record_call` 直接插一行记账是同一个道理。
    """
    cursor = conn.execute(
        "INSERT INTO learning_request (kind, raw_text, created_at) VALUES (?, ?, ?)",
        (kind, raw_text, now_iso()),
    )
    conn.commit()
    return int(cursor.lastrowid)


# ---------- 主链路 ----------

def judge(
    conn: sqlite3.Connection,
    raw_text: str,
    *,
    provider_id: int | None = None,
    model: str | None = None,
    transport: llm.Transport | None = None,
) -> dict[str, Any]:
    """跑一次四问判断。**只回结果，不落库**——落提案由 `propose` 负责。

    不合格就带上"哪里不合格"重试一次；第二次还不合格就抛 `AdvisorError`，
    绝不把一段凑合的输出当成成功。
    """
    profile = read_profile(conn)
    if not profile["items"]:
        raise AdvisorError(
            "长期档案一条都没有，四问没有判据可依——先把档案补上（至少「长期主线」），再来问"
        )

    allowed_ids = {item["id"] for item in profile["items"]}
    messages: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _build_prompt(raw_text, profile)},
    ]

    operation = llm.Operation(conn, TASK, transport=transport)
    text = operation.chat(messages, provider_id=provider_id, model=model)
    judgment, problem = _check(text, allowed_ids)

    attempts = 1
    if problem is not None:
        # 重试一次：把"哪里不合格"原样告诉模型，比让它重新猜有效得多。
        # 这一下用掉第 2 次调用，仍在 Operation 的 3 次闸以内。
        attempts = 2
        messages = [
            *messages,
            {"role": "assistant", "content": text},
            {
                "role": "user",
                "content": f"你上面的输出不合格：{problem}。请只输出合格的 JSON 对象，不要任何解释。",
            },
        ]
        text = operation.chat(messages, provider_id=provider_id, model=model)
        judgment, problem = _check(text, allowed_ids)
        if problem is not None:
            raise AdvisorError(
                f"模型连着 {attempts} 次都没给出合格的四问输出（{problem}）；"
                f"已按上限中止，**没有落任何提案**"
            )

    if judgment is None:  # 理论上到不了：上面两条路径都保证 problem 为 None 时它必有值
        raise AdvisorError("内部状态异常：验收通过却没有解析出判断结果")

    return {
        "judgment": judgment.model_dump(),
        "profile_basis": {
            "total": len(profile["items"]),
            "missing_categories": profile["missing_categories"],
        },
        "attempts": attempts,
        "calls": operation.used,
    }


def propose(
    conn: sqlite3.Connection,
    *,
    request_id: int,
    raw_text: str,
    result: dict[str, Any],
) -> int:
    """把判断结果落成一条**待裁定**提案，返回提案 id。

    走 `ledger.create_active`（而不是直接 INSERT）：提案也是台账里登记过的东西，
    将来 `GET /api/proposals` 与裁定动作都靠这套口径找得到它。
    """
    payload = {
        "source_text": raw_text,
        "learning_request_id": request_id,
        "judgment": result["judgment"],
        "profile_basis": result["profile_basis"],
    }
    basis = result["profile_basis"]["total"]
    return ledger.create_active(
        conn,
        "proposal",
        {
            "kind": MATERIAL_JUDGMENT_KIND,
            "payload": json.dumps(payload, ensure_ascii=False),
            "reason": f"对「{_shorten(raw_text)}」的四问判断（依据 {basis} 条档案）",
        },
        actor="agent",
    )


# ---------- 内部：组 prompt 与验收输出 ----------

def _shorten(text: str, limit: int = 30) -> str:
    """把长输入截短放进 reason：理由是一句话，不该塞进整段原文。"""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[:limit]}…"


def _build_prompt(raw_text: str, profile: dict[str, Any]) -> str:
    lines = [
        "我要判断一个资料值不值得学，请你按四问回答。",
        "",
        "【我的长期档案】方括号里是类别，开头的 #数字 是这条档案的 id（id 只能从这里选）：",
    ]
    lines += [
        f"#{item['id']} [{item['category']}] {item['content']}" for item in profile["items"]
    ]

    if profile["missing_categories"]:
        names = "、".join(PROFILE_CATEGORIES[name] for name in profile["missing_categories"])
        lines += [
            "",
            f"【注意】这几类档案目前是空的：{names}。凡是要靠它们才能判断的，"
            "answer 里请如实说「依据不足」并讲清缺哪类信息，不要拿常理替我做决定。",
        ]

    lines += ["", "【四问与各自主要看哪几类档案】"]
    lines += [
        f"- {title}（键名 {key}）：主要看 " + "、".join(PROFILE_CATEGORIES[name] for name in JUDGE_SOURCES[key])
        for key, title in QUESTIONS.items()
    ]

    lines += [
        "",
        "【这次要判断的东西】",
        raw_text.strip(),
        "",
        "【硬性要求】",
        "- 四问的每一问都要给 profile_item_ids，写清你依据的是上面哪几条档案（写 # 后面的数字），"
        "只能从上面出现过的 id 里选；编一个不存在的 id 会被判为不合格。",
        "- 某一问在上面档案里确实找不到依据时，answer 要明说「依据不足」并说清缺哪类信息，"
        "profile_item_ids 给空数组。宁可说依据不足，也不许编造依据。",
        "- 只输出一个 JSON 对象，不要解释、不要 Markdown 代码块。键固定为这四个："
        + " / ".join(QUESTIONS)
        + '，每个值是 {"answer": "一句话", "profile_item_ids": [数字, ...]}。',
    ]
    return "\n".join(lines)


def _check(text: str, allowed_ids: set[int]) -> tuple[Judgment | None, str | None]:
    """验收模型输出：返回（合格的判断, None）或（None, 不合格的原因）。

    刻意**不抛异常**：不合格的原因要能被拿回去喂给模型重试一次，抛异常就断了这条路。
    """
    data = _extract_json(text)
    if data is None:
        return None, "输出不是合法的 JSON 对象"

    try:
        judgment = Judgment.model_validate(data)
    except ValidationError as error:
        # 直接把 Pydantic 的字段级说明透传出去，不另编一套中文（同接口层"两张表里没有的
        # 就回退显示原文"的口径）。
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or '(根)'}: {item['msg']}"
            for item in error.errors()
        )
        return None, f"字段不合格（{details}）"

    cited = {item_id for answer in _answers(judgment) for item_id in answer.profile_item_ids}
    unknown = sorted(cited - allowed_ids)
    if unknown:
        return None, f"引用了不存在的档案 id：{unknown}（只能用我列给你的那些 id）"

    # 「每条答案能指回具体字段」的兜底：没给 id 的，必须明说「依据不足」。
    # 不堵这个口子，模型给四个空数组加四句漂亮话就能过验收——那正是要防的"空泛建议"。
    for key, answer in judgment.model_dump().items():
        if not answer["profile_item_ids"] and "依据不足" not in answer["answer"]:
            return None, (
                f"{key} 既没给 profile_item_ids，也没说「依据不足」——"
                "没有依据的答案不许当合格输出"
            )
    return judgment, None


def _answers(judgment: Judgment) -> list[Answer]:
    return [
        judgment.worth_learning,
        judgment.depth_target,
        judgment.intensity,
        judgment.time_budget,
    ]


def _extract_json(text: str) -> dict[str, Any] | None:
    """从模型输出里取出 JSON 对象。

    为什么容一手代码块：模型很爱把 JSON 包在 ```json ... ``` 里，加了这层壳不代表内容错，
    为这个就判不合格纯属浪费一次调用。取第一个 `{` 到最后一个 `}` 之间的内容。
    """
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw[:4].lower() == "json":
            raw = raw[4:]

    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None

    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
