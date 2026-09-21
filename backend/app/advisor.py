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
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from . import ledger, llm, plan
from .db import now_iso
from .providers import find
from .providers.find import Brief


class AdvisorError(RuntimeError):
    """决策链路上的明确错误：没档案、模型连着两次输出不合格、上游调用失败。

    一律明确报错而不是"返回一段凑合的建议"——这本产品的价值就在判断质量，
    悄悄给个平庸答案比报错更糟。
    """


class CandidateNotFound(AdvisorError):
    """候选不存在 → 接口层翻成 404。"""


class CandidateConflict(AdvisorError):
    """候选已裁定过，或采纳的前提不满足（没有 active 计划 / 有同名未收尾阶段）
    → 接口层翻成 409（与现状冲突，不是参数写错）。"""


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


def record_request(
    conn: sqlite3.Connection, kind: str, raw_text: str, plan_id: int | None = None
) -> int:
    """把你的这一轮输入记一行。

    `plan_id` = 这一轮针对哪个计划；为空表示「新方向（不属于任何计划）」（SPEC 决策 33 ①）。
    候选随请求继承这个归属，采纳时才知道该落进哪个计划。

    这张表是**追加式日志**、不是带状态机业务表，所以不经台账（`ledger` 只管有状态的
    对象）；和 `llm.record_call` 直接插一行记账是同一个道理。
    """
    cursor = conn.execute(
        "INSERT INTO learning_request (kind, raw_text, plan_id, created_at) VALUES (?, ?, ?, ?)",
        (kind, raw_text, plan_id, now_iso()),
    )
    conn.commit()
    return int(cursor.lastrowid)


def record_clarify(conn: sqlite3.Connection, request_id: int, clarify: dict[str, Any]) -> None:
    """把这一轮问出的追问记在请求行上（T36）。

    T25 定的是「追问不落库」，理由是它不该长出自己的表；但 T36 要「问过的不再问」——
    模型得在下一轮看得到「这件事我已经答过了」，而反馈流水是**从库里读**的。所以它落在
    `learning_request.clarify` 这一列上（可空 JSON），不另立表：追问与它所属的那一轮本就
    是同一条记录的一部分。

    只记「问了什么、缺哪类」；`answer` 由 `mark_clarify_answered` 在下一轮补上。
    """
    conn.execute(
        "UPDATE learning_request SET clarify = ? WHERE id = ?",
        (
            json.dumps(
                {
                    "question": str(clarify.get("question") or "").strip(),
                    "missing": str(clarify.get("missing") or "").strip(),
                    "answer": None,
                },
                ensure_ascii=False,
            ),
            request_id,
        ),
    )
    conn.commit()


def _stored_clarify(raw: Any) -> dict[str, Any] | None:
    """解 `learning_request.clarify` 那一列；解不开当没有。"""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or not str(data.get("question") or "").strip():
        return None
    answer = data.get("answer")
    return {
        "question": str(data["question"]).strip(),
        "missing": str(data.get("missing") or "").strip(),
        "answer": None if answer is None else str(answer).strip(),
    }


def pending_clarify(conn: sqlite3.Connection, plan_id: int | None) -> dict[str, Any] | None:
    """同一计划归属下**最近一轮问过、还没答**的追问；没有就返回 None。

    只看同一个计划范围：两段「找」问的是不同的事，串台会让回答落到别的计划上。
    """
    row = conn.execute(
        "SELECT id, clarify FROM learning_request"
        " WHERE kind = 'search' AND plan_id IS ? AND clarify IS NOT NULL"
        " ORDER BY id DESC LIMIT 1",
        (plan_id,),
    ).fetchone()
    if row is None:
        return None
    stored = _stored_clarify(row["clarify"])
    if stored is None or stored["answer"]:
        return None
    return {"request_id": int(row["id"]), **stored}


def mark_clarify_answered(conn: sqlite3.Connection, request_id: int, answer: str) -> None:
    """把某一轮问出的追问标记成「已答」（T36）：`answer` 进库，反馈流水因此看得见。"""
    row = conn.execute(
        "SELECT clarify FROM learning_request WHERE id = ?", (request_id,)
    ).fetchone()
    if row is None:
        return
    stored = _stored_clarify(row["clarify"])
    if stored is None:
        return
    stored["answer"] = str(answer).strip()
    conn.execute(
        "UPDATE learning_request SET clarify = ? WHERE id = ?",
        (json.dumps(stored, ensure_ascii=False), request_id),
    )
    conn.commit()


def plan_context(conn: sqlite3.Connection, plan_id: int | None) -> dict[str, Any] | None:
    """这一轮「找」针对的计划：目标 + 当前阶段 + 还开着的任务。

    带上它，候选才贴得上手头的计划（SPEC 决策 33 ① 的后半句）；为空表示「新方向」。
    """
    if plan_id is None:
        return None
    row = plan.resolve_plan(conn, plan_id)
    if row is None:
        raise AdvisorError(f"计划 id={plan_id} 不存在")
    stage = plan.current_stage(conn, int(row["id"]))
    return {
        "plan_id": int(row["id"]),
        "goal": row["goal"],
        "current_stage": None if stage is None else {
            "title": stage["title"],
            "deliverable": stage["deliverable"],
            "open_tasks": plan.stage_completion(conn, int(stage["id"]))["open_titles"],
        },
    }


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
    data = extract_json(text)
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


def extract_json(text: str) -> dict[str, Any] | None:
    """从模型输出里取出 JSON 对象。

    为什么容一手代码块：模型很爱把 JSON 包在 ```json ... ``` 里，加了这层壳不代表内容错，
    为这个就判不合格纯属浪费一次调用。取第一个 `{` 到最后一个 `}` 之间的内容。

    公开（去掉前导下划线）是因为 `blueprint.py` 的对话与蓝图要用**同一套**取法：
    这条容错口径不该两个模块各写一遍。
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


# ---------- 「找」：候选清单（T13） ----------
#
# 与四问共用同一套判据（SPEC 第 4 节：「找」与「判」不做两套逻辑），也共用同一条纪律：
# LLM 只产出提案式的结果，落库要经用户裁定；输出先过 schema 校验，不合格带原因重试一次，
# 两次都不合格就如实报错、什么都不落。
#
# 与四问不同的只有两点：
# ① 候选是**一组**而不是一问一答，所以条数（3–5）本身就是校验项；
# ② 有一份**禁区**：已经否决过的候选一个字都不许再出现——这是成功标准 2 的后半句。

TASK_FIND = "find"

# 深度四档，照抄 SPEC 第 4 节第 ② 问的说法
DEPTH_TARGETS: tuple[str, ...] = ("浅尝", "够用", "熟练", "精通")

# 候选形态，与 `candidate.kind` 列的注释一致
CANDIDATE_KINDS: tuple[str, ...] = ("concept", "doc", "project", "course")

# 条数约束（SPEC 第 9 节成功标准 2：3–5 条）
MIN_CANDIDATES = 3
MAX_CANDIDATES = 5

# 形状（2026-09-20 T34，SPEC 决策 41）：
#   directions —— 几条互相竞争的方向（现状），逐条采纳 / 否决
#   path       —— 一条路（一个伞候选 + 它的几个先后步骤），整条裁定一次
# 为什么要分：用户输入一个明确方向时，「找」回过一条路上的五个先后步骤，却按五个互相竞争的
# 方向摆出来——每条独立采纳/否决，而否决＝永久拉黑。步骤的去留该往后放（蓝图勾选 / 计划跳过），
# 方向层只裁定「这条路走不走」。
SHAPES: tuple[str, ...] = ("directions", "path")

# `path` 下步骤的条数区间（2–8）：给 1 个不叫一条路，给 9 个就不是「找」该管的粒度了
MIN_STEPS = 2
MAX_STEPS = 8

# 反馈流水（SPEC 决策 35 ①）：把最近几轮「找」的结果连着你的表态发进下一轮 prompt。
# 条数与字符上限写成常量，便于按实测调整——候选清单本来就慢（23–27 秒 / 约 5000 token），
# 这段是这轮新增的唯一负担，所以卡得比模型上下文紧得多：宁少说，不多烧。
FEEDBACK_ROUNDS = 5
FEEDBACK_CHAR_LIMIT = 1200

# 只有这三种状态的候选进反馈流水：`proposed` 是「你还没表态」，喂回去等于让模型
# 自己给自己打分。过期与否决分开说——过期**不是**否决（决策 34），它只是「上一轮不算数了」。
FEEDBACK_STATUSES: tuple[str, ...] = ("accepted", "rejected", "expired")

_VERDICT_LABELS = {
    "accepted": "已采纳",
    "rejected": "已否决",
    "expired": "已过期（我没表态，不算否决）",
}

# 追问槽位里「缺哪类信息」必须点名的词（SPEC 决策 35 ②）。判据刻意宽松：中文名或英文
# token 都算，只要能看出问的是哪一类档案就行。为什么要卡这一条：不点名就成「你想学什么」
# 那种空泛追问——它把问题原样抛回给你，等于什么都没说。
CLARIFY_KEYS: tuple[str, ...] = tuple(
    name
    for token, label in PROFILE_CATEGORIES.items()
    for name in (label.split("（")[0].strip(), token)
)

# 追问只问「关于我的一件事」（2026-09-20 T36，SPEC 决策 41）：一句话能答完，且**不许问
# 「这条路怎么走」**——那是采纳之后规划对话的活。两条判据都能当场查，所以钉在这里而不是
# 只写在提示词里（提示词是请求，校验才是保证）。
CLARIFY_QUESTION_MAX = 60

# 反馈流水里「这轮问过、我也答过」的行首（T36）。写成一个常量是因为校验与测试都要认它，
# 措辞改一处就够。
CLARIFY_ANSWERED_LABEL = "我问过"

# 命中任一即判为「规划对话才该问的」：先学哪个、按什么顺序、分几步、怎么排……
# 刻意只收**明确的规划说法**，不收「先」「节奏」这类单独的词——「你平时的作息节奏」是
# 关于我的事实，不该被这里拦下。
PLAN_TALK_MARKERS: tuple[str, ...] = (
    "怎么走",
    "先学哪",
    "先做哪",
    "先从哪",
    "从哪开始",
    "从哪儿开始",
    "从何处开始",
    "什么顺序",
    "怎么排",
    "怎么安排",
    "分几步",
    "分几个阶段",
    "学习路线",
    "路线怎么",
    "打算怎么",
    "打算先",
    "优先学",
    "优先做",
    "怎么取舍",
    "怎么平衡",
)


class Candidate(BaseModel):
    """一条候选。`why` 与四问的 `answer` 同一条底线：要么指回档案，要么明说依据不足。"""

    title: str = Field(min_length=1)
    kind: Literal["concept", "doc", "project", "course"]
    why: str = Field(min_length=1)
    depth_target: Literal["浅尝", "够用", "熟练", "精通"]
    profile_item_ids: list[int] = Field(default_factory=list)


class Clarify(BaseModel):
    """追问槽位（SPEC 决策 35 ②）：信息不够时先问一句——但清单照给。

    `missing` 要说清缺的是哪一类档案信息（校验见 `_clarify_problem`）。
    **不落库**：它只在当次响应里出现（同 `start_reason` 的处理），你回答的内容就是
    下一轮的输入，不需要为它建表加列。
    """

    question: str = Field(min_length=1)
    missing: str = Field(min_length=1)


class PathStep(BaseModel):
    """一条路上的一个先后步骤（T34，SPEC 决策 41）。

    形状取蓝图阶段（`BlueprintStage`）的子集：去掉 `tasks`——「这一步下面拆几件活」
    是规划对话与蓝图勾选时的事，「找」只说到「有这一步、它交什么」这一层。
    """

    title: str = Field(min_length=1)
    # 这一步交出什么。可以留空（模型没给），出蓝图那一步会再要一次
    deliverable: str = ""
    # 为什么它必须排在这个位置——路径与方向的区别就在这个「顺序」上
    why: str = Field(min_length=1)

    @field_validator("title", "why")
    @classmethod
    def _require_text(cls, value: str) -> str:
        """只有空白的标题 / 理由不算给了——`min_length=1` 对「  」是放行的。"""
        cleaned = str(value).strip()
        if not cleaned:
            raise ValueError("不能是空白")
        return cleaned

    @field_validator("deliverable")
    @classmethod
    def _clean_deliverable(cls, value: str) -> str:
        return str(value or "").strip()


class FoundList(BaseModel):
    """一次「找」的输出。形状与条数越界都会被 Pydantic 或 `_check_find` 拦住（不补齐、不截断）。

    `shape` 让模型**先自报形状**：它是 `directions`（3–5 条互相竞争的方向）还是
    `path`（1 条伞候选 + 2–8 个先后步骤）。自报的好处是选错了你一眼能看见——界面把
    形状标在卡上，而不是让系统默默替你猜。

    `clarify` 可选：**追问不能替代清单**——带追问的那一轮仍必须给满候选。
    """

    shape: Literal["directions", "path"]
    candidates: list[Candidate] = Field(default_factory=list, max_length=MAX_CANDIDATES)
    steps: list[PathStep] = Field(default_factory=list, max_length=MAX_STEPS)
    recommended_start: str = Field(min_length=1)
    start_reason: str = Field(min_length=1)
    clarify: Clarify | None = None


def _feedback_lines(conn: sqlite3.Connection) -> list[str]:
    """最近几轮「找」的流水，按时间正序（最早的一轮在前）。

    每行一条请求：时间 / 计划归属 / 这一轮每条候选的标题与裁定结果（否决带理由原文），
    外加**这一轮问过、我也答过的追问**（T36：`我问「…」→ 我答：…`）——它的用处是让模型
    别再问第二遍同一件事。只取 `search` 那一类请求——四问（`evaluate`）记录不进这段
    （决策 35 ①）：那问的是「一份资料值不值得学」，和「别给我推什么方向」不是一回事。
    一轮里既没有一句表态、也没有答过的追问，这一轮就没有可说的，跳过。
    """
    rounds = conn.execute(
        "SELECT id, plan_id, created_at, clarify FROM learning_request"
        " WHERE kind = 'search' ORDER BY id DESC LIMIT ?",
        (FEEDBACK_ROUNDS,),
    ).fetchall()

    lines: list[str] = []
    for request in reversed(rounds):  # 时间正序
        rows = conn.execute(
            f"""SELECT title, status, reject_reason FROM candidate
                WHERE request_id = ? AND status IN ({', '.join('?' * len(FEEDBACK_STATUSES))})
                ORDER BY rank, id""",
            (request["id"], *FEEDBACK_STATUSES),
        ).fetchall()
        items: list[str] = []
        for row in rows:
            verdict = _VERDICT_LABELS.get(str(row["status"]), str(row["status"]))
            reason = str(row["reject_reason"] or "").strip()
            if str(row["status"]) == "rejected" and reason:
                verdict = f"{verdict}：{reason}"  # 理由原文——它比「否决」两个字有用得多
            items.append(f"{row['title']}（{verdict}）")
        asked = _stored_clarify(request["clarify"])
        if asked is not None and asked["answer"]:
            # 追问**问过也答过**才算数：只问了没答的，下一轮它该继续问，不该当成已知事实
            items.append(f"{CLARIFY_ANSWERED_LABEL}「{asked['question']}」→「{asked['answer']}」")
        if not items:
            continue
        scope = "新方向（不属于任何计划）" if request["plan_id"] is None else f"计划 #{request['plan_id']}"
        lines.append(f"- {_short_time(str(request['created_at']))}｜{scope}｜" + "；".join(items))
    return lines


def _feedback_block(conn: sqlite3.Connection) -> list[str]:
    """反馈流水那一段，已按 `FEEDBACK_CHAR_LIMIT` **从最旧截断**（决策 35 ①）。

    从最新往回收，收不下就丢掉更旧的——越近的表态越该被记住。单行就超上限时
    仍然留它（一条真实表态好过一片空白），这也是这道闸只保证「通常不超」的原因。
    """
    kept: list[str] = []
    used = 0
    for line in reversed(_feedback_lines(conn)):
        if kept and used + len(line) > FEEDBACK_CHAR_LIMIT:
            break
        kept.insert(0, line)
        used += len(line)
    return kept


def _short_time(iso: str) -> str:
    """把 ISO 时间压成「2026-09-18 10:23」——精确到分钟足够，还省 prompt 字符。"""
    return iso.replace("T", " ")[:16]


def _normalize_title(title: str) -> str:
    """比标题时用的归一化：去首尾空白、去掉中间所有空白、转小写。

    故意不做模糊匹配：判据要能一眼看懂。代价是「学 Python」与「Python 基础」这种
    换了个说法的同一件事仍可能漏过——这条限制写进了交接文档，不在本轮加复杂度。
    """
    return "".join(title.split()).casefold()


def _rejected_titles(conn: sqlite3.Connection) -> list[str]:
    """已经被否决过的候选标题（去重、按 id 序）。

    为什么取 `rejected` 而不是所有历史：候选只有三种终态，`rejected` 才是「你别再推这个」，
    `accepted` 是「这个我要了」——后者不该进禁区（但也不该反复推，交给 prompt 里的档案去说）。
    """
    rows = conn.execute(
        "SELECT DISTINCT title FROM candidate WHERE status = 'rejected' ORDER BY id"
    ).fetchall()
    return [str(row["title"]) for row in rows]


def _brief(
    raw_text: str,
    profile: dict[str, Any],
    banned: list[str],
    feedback: list[str],
    context: dict[str, Any] | None = None,
) -> Brief:
    """把档案与判据组装成来源层要的输入（见 `providers/find.Brief`）。

    组装留在 advisor 而不是来源层：档案长什么样、哪些类别空着、每档深度看哪几类、
    这一轮针对哪个计划、最近表态过什么，这些都是「判」的知识；来源只管怎么把它讲给模型听。
    """
    plan_lines: list[str] = []
    if context is not None:
        plan_lines.append(f"计划目标：{context['goal']}")
        stage = context["current_stage"]
        if stage is None:
            plan_lines.append("这个计划还没有进行中的阶段（都收尾了或还没建）。")
        else:
            plan_lines.append(f"当前阶段：{stage['title']}")
            if stage["deliverable"]:
                plan_lines.append(f"该阶段要交的东西：{stage['deliverable']}")
            if stage["open_tasks"]:
                plan_lines.append("这个阶段还开着的任务：" + "、".join(stage["open_tasks"]))
    return Brief(
        raw_text=raw_text,
        profile_lines=[
            f"#{item['id']} [{item['category']}] {item['content']}" for item in profile["items"]
        ],
        missing_labels=[PROFILE_CATEGORIES[key] for key in profile["missing_categories"]],
        plan_context_lines=plan_lines,
        feedback_lines=feedback,
        depth_guide_lines=[
            f"{QUESTIONS[key]}：主要看 "
            + "、".join(PROFILE_CATEGORIES[name] for name in JUDGE_SOURCES[key])
            for key in ("depth_target", "intensity", "time_budget")
        ],
        depth_targets=DEPTH_TARGETS,
        kinds=CANDIDATE_KINDS,
        clarify_keys=CLARIFY_KEYS,
        min_candidates=MIN_CANDIDATES,
        max_candidates=MAX_CANDIDATES,
        min_steps=MIN_STEPS,
        max_steps=MAX_STEPS,
        banned_titles=banned,
    )


def find_candidates(
    conn: sqlite3.Connection,
    raw_text: str,
    *,
    plan_id: int | None = None,
    provider_id: int | None = None,
    model: str | None = None,
    transport: llm.Transport | None = None,
    source: find.Source | None = None,
) -> dict[str, Any]:
    """跑一次「我不知道该学什么」。**只回结果，不落库**——落库由 `propose_candidates` 负责。

    `plan_id` 传了就带该计划的上下文（目标 / 当前阶段 / 还开着的任务）进 prompt，
    候选更贴手头的计划（SPEC 决策 33 ①）；不传 = 「新方向」。

    与 `judge` 同一条纪律：不合格带原因重试一次，第二次仍不合格就抛 `AdvisorError`，
    绝不把一份凑合的清单一分成三份端上来。
    """
    profile = read_profile(conn)
    if not profile["items"]:
        raise AdvisorError(
            "长期档案一条都没有，「找」没有判据可依——先去 /profile 补档案"
            "（至少「长期主线」与「短期目标」），再来问"
        )

    context = plan_context(conn, plan_id)
    chosen_source = source or find.DEFAULT_SOURCE
    allowed_ids = {item["id"] for item in profile["items"]}
    banned = _rejected_titles(conn)
    feedback = _feedback_block(conn)
    messages = chosen_source.build_messages(
        _brief(raw_text, profile, banned, feedback, context)
    )

    operation = llm.Operation(conn, TASK_FIND, transport=transport)
    text = operation.chat(messages, provider_id=provider_id, model=model)
    found, problem = _check_find(text, allowed_ids, banned)

    attempts = 1
    if problem is not None:
        # 重试一次：把「哪里不合格」原样告诉模型，比让它重新猜有效得多。
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
        found, problem = _check_find(text, allowed_ids, banned)
        if problem is not None:
            raise AdvisorError(
                f"模型连着 {attempts} 次都没给出合格的候选清单（{problem}）；"
                "已按上限中止，**没有落任何候选**"
            )

    if found is None:  # 理论上到不了：上面两条路径都保证 problem 为 None 时它必有值
        raise AdvisorError("内部状态异常：验收通过却没有解析出候选清单")

    return {
        "plan_id": None if context is None else context["plan_id"],
        # 形状（T34）：`directions` = 几条互相竞争的方向，`path` = 一条路
        "shape": found.shape,
        "candidates": [item.model_dump() for item in found.candidates],
        # 步骤草案只在 path 时有内容：它是「这一条路上的先后几步」，随后进伞候选的 payload，
        # 在采纳之后的规划对话里当底稿用（决策 41）
        "steps": [item.model_dump() for item in found.steps],
        "recommended_start": found.recommended_start,
        "start_reason": found.start_reason,
        # 追问只在当次响应里给（决策 35 ②）：不落库、下次提问看不到它，
        # 你回答的那句话本身就是下一轮的输入。
        "clarify": None if found.clarify is None else found.clarify.model_dump(),
        "source": find.describe(chosen_source),
        "profile_basis": {
            "total": len(profile["items"]),
            "missing_categories": profile["missing_categories"],
        },
        "banned_titles": banned,
        "feedback_lines": feedback,
        "attempts": attempts,
        "calls": operation.used,
    }


def _clarify_problem(clarify: Clarify) -> str | None:
    """追问槽位的验收（决策 35 ②，2026-09-20 T36 收紧）。

    三条闸，各堵一种跑偏：
    ① `missing` 要点名哪一类档案信息——堵「你想学什么」这种把问题抛回给我的空泛追问；
    ② **一句话能答**——堵一次问好几件事、把追问写成一段话；
    ③ **不许问「这条路怎么走」**——先学哪个、按什么顺序、怎么排，那是采纳之后规划对话的活，
       在这里问等于让「找」替规划下结论。判据是明确的规划说法（`PLAN_TALK_MARKERS`），
       不是「先」「节奏」这类单独的词——「你平时的作息节奏」仍然是关于我的事实。

    刻意**不**要求被追问的那一类此刻真的空着：档案里有一条不等于它够用，
    模型想问细一点是正当的。
    """
    if not clarify.question.strip() or not clarify.missing.strip():
        return "clarify 的 question 与 missing 缺一不可——缺一个就别给 clarify"
    if not any(name in clarify.missing for name in CLARIFY_KEYS):
        return (
            f"clarify 的 missing「{clarify.missing}」没说清缺哪类档案信息——"
            f"missing 要点名其中一类：{'、'.join(CLARIFY_KEYS)}。"
            "空泛的追问（例如「你想学什么」）不算合格"
        )

    question = clarify.question.strip()
    if len(question) > CLARIFY_QUESTION_MAX or "\n" in question:
        return (
            f"clarify 的 question 太长了（{len(question)} 字，上限 {CLARIFY_QUESTION_MAX}）——"
            "追问是一句话，问一个我能一句话答完的事实，别写成一段、别一次问好几件事"
        )
    if question.count("？") + question.count("?") > 1:
        return (
            "clarify 的 question 里问号不止一个——一次只问一件关于我的事实，"
            "几件事拆成下一轮再问"
        )
    hit = next((marker for marker in PLAN_TALK_MARKERS if marker in question), None)
    if hit is not None:
        return (
            f"clarify 的 question 问的是「这条路怎么走」（命中了「{hit}」）——"
            "那是采纳之后规划对话该问的（意向、节奏、取舍）。"
            "追问只问关于我的一件事：档案里缺的那类事实，一句话能答完"
        )
    return None


def _shape_problem(found: FoundList) -> str | None:
    """形状与它的条数必须对得上（T34，SPEC 决策 41）。

    两种形状各有各的约束，**不许混着写**：
    - `directions`：3–5 条候选，`steps` 必须是空数组（它是 `path` 才用的字段）；
    - `path`：`candidates` 恰好 1 条伞候选，`steps` 给 2–8 个先后步骤。

    为什么 `path` 只允许一条候选：伞候选是「这条路」，步骤是路上的几步——把它们也摆成
    几条候选，否决其中一步就成了永久拉黑，而「这步暂时不做」和「这步永远别给我」完全是
    两件事（步骤的去留在蓝图勾选与计划跳过里）。
    """
    if found.shape == "directions":
        count = len(found.candidates)
        if not MIN_CANDIDATES <= count <= MAX_CANDIDATES:
            return (
                f"shape 是 directions，就要给 {MIN_CANDIDATES}–{MAX_CANDIDATES} 条"
                f"**互相竞争**的方向，现在给了 {count} 条；"
                "如果它们其实是同一条路上的先后步骤，shape 该写 path"
            )
        if found.steps:
            return (
                f"shape 是 directions，steps 必须是空数组（现在给了 {len(found.steps)} 个）——"
                "steps 只在 shape 为 path 时用：那条路只有一条伞候选，步骤放在 steps 里"
            )
        return None

    count = len(found.candidates)
    if count != 1:
        return (
            f"shape 是 path，candidates 只放 **1 条伞候选**（标题是这条路本身），"
            f"现在给了 {count} 条；先后步骤放进 steps，别当成几条互相竞争的方向"
        )
    steps = len(found.steps)
    if not MIN_STEPS <= steps <= MAX_STEPS:
        return (
            f"shape 是 path，steps 要给 {MIN_STEPS}–{MAX_STEPS} 个先后步骤，现在给了 {steps} 个"
        )
    return None


def _check_find(
    text: str, allowed_ids: set[int], banned: list[str]
) -> tuple[FoundList | None, str | None]:
    """验收模型输出：返回（合格的清单, None）或（None, 不合格的原因）。

    同 `_check`，刻意不抛异常——不合格的原因要能喂回模型重试一次。
    """
    data = extract_json(text)
    if data is None:
        return None, "输出不是合法的 JSON 对象"

    try:
        found = FoundList.model_validate(data)
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or '(根)'}: {item['msg']}"
            for item in error.errors()
        )
        return None, f"字段不合格（{details}）"

    problem = _shape_problem(found)
    if problem is not None:
        return None, problem

    if found.clarify is not None:
        problem = _clarify_problem(found.clarify)
        if problem is not None:
            return None, problem

    cited = {item_id for candidate in found.candidates for item_id in candidate.profile_item_ids}
    unknown = sorted(cited - allowed_ids)
    if unknown:
        return None, f"引用了不存在的档案 id：{unknown}（只能用我列给你的那些 id）"

    # 与四问同一条兜底：没给依据的候选，`why` 里必须明说「依据不足」。
    for candidate in found.candidates:
        if not candidate.profile_item_ids and "依据不足" not in candidate.why:
            return None, (
                f"候选「{candidate.title}」的 why 既没给 profile_item_ids，也没说「依据不足」——"
                "没有依据的推荐不许当合格输出"
            )

    titles = [candidate.title for candidate in found.candidates]
    if found.recommended_start not in titles:
        return None, (
            f"recommended_start「{found.recommended_start}」不是候选之一"
            f"（要一字不差复制某条的 title，现有：{titles}）"
        )

    # 去重的硬保证：禁区里的标题一个字都不许再出现。
    # 为什么是「判不合格重试」而不是「悄悄过滤掉」：过滤会让条数掉到 3 条以下、也不告诉
    # 模型它又推了禁过的；判不合格会把禁区再讲一遍，第二次还犯就如实报错。
    banned_set = {_normalize_title(title) for title in banned}
    repeated = [title for title in titles if _normalize_title(title) in banned_set]
    if repeated:
        return None, f"这些是你以前否决过的，不许再出现：{repeated}（换个方向，别再推它们）"

    return found, None


def expire_previous_candidates(
    conn: sqlite3.Connection, *, keep_request_id: int, plan_id: int | None
) -> list[int]:
    """新一轮「找」落库时，把**同一计划**上一轮还没裁定的候选标记为过期（SPEC 决策 34）。

    过期 ≠ 否决：`_rejected_titles` 只取 `rejected`，所以过期的不进禁区，模型以后还能再推。
    不传计划归属的那些轮（「新方向」）自成一组，互相之间也这么办。
    """
    rows = conn.execute(
        """SELECT c.id FROM candidate c
           JOIN learning_request r ON r.id = c.request_id
           WHERE c.status = 'proposed' AND c.request_id != ?
             AND r.plan_id IS ?""",
        (keep_request_id, plan_id),
    ).fetchall()
    expired: list[int] = []
    for row in rows:
        ledger.set_status(
            conn,
            "candidate",
            int(row["id"]),
            "expired",
            actor="agent",
            reason=f"新一轮「找」（请求 #{keep_request_id}）落库，上一轮未裁定的候选自动过期",
        )
        expired.append(int(row["id"]))
    return expired


def propose_candidates(
    conn: sqlite3.Connection, *, request_id: int, result: dict[str, Any]
) -> list[int]:
    """把候选清单落成 `proposed` 候选行，返回候选 id（按优先级顺序）。

    走 `ledger.create_active`（同 `propose`）：候选也是台账里登记过的东西，
    将来 `GET /api/candidates` 与裁定动作都靠这套口径找得到它。
    `rank` 就是列表顺序——顺序即优先级，前端不再自己排。

    `path` 形状（T34）落**一行**伞候选，先后步骤存在它的 `payload` 里（新列，与
    `proposal.payload` 同一用法）——步骤不是候选，不单独裁定、不进禁区。

    落库的最后一步是**让上一轮过期**（SPEC 决策 34）：同一计划下没裁定过的旧候选
    不再挂着等你，但也不进禁区——过期只是「这轮不算数了」。过期按伞候选走，
    步骤随 payload 一起失效（伞候选过期 = 这条路这轮不算数了）。
    """
    is_path = result.get("shape") == "path"
    steps_payload = (
        json.dumps({"shape": "path", "steps": result["steps"]}, ensure_ascii=False)
        if is_path
        else None
    )
    ids: list[int] = []
    for rank, candidate in enumerate(result["candidates"], start=1):
        is_recommended = candidate["title"] == result["recommended_start"]
        reason = f"「找」的第 {rank} 条候选（{result['source']['name']}）"
        if is_path:
            reason = f"「找」给的这一条路（{result['source']['name']}），含 {len(result['steps'])} 个先后步骤"
        if is_recommended:
            reason += f"；建议先从这条开始：{result['start_reason']}"
        ids.append(
            ledger.create_active(
                conn,
                "candidate",
                {
                    "request_id": request_id,
                    "title": candidate["title"],
                    "kind": candidate["kind"],
                    "why": candidate["why"],
                    "depth_target": candidate["depth_target"],
                    "rank": rank,
                    "is_recommended": 1 if is_recommended else 0,
                    "payload": steps_payload,
                },
                actor="agent",
                reason=reason,
            )
        )
    request_row = conn.execute(
        "SELECT plan_id FROM learning_request WHERE id = ?", (request_id,)
    ).fetchone()
    expire_previous_candidates(
        conn,
        keep_request_id=request_id,
        plan_id=None if request_row is None else request_row["plan_id"],
    )
    return ids


def list_candidates(conn: sqlite3.Connection, request_id: int | None = None) -> dict[str, Any]:
    """取某轮「找」的候选清单。不传 `request_id` 就取最近一轮有候选的那次请求。

    返回里**带上状态**（`proposed` / `accepted` / `rejected` / `expired`）：界面要能把
    「你已经否掉过哪些」「哪些被新一轮顶掉了」也显示出来——去重是「不再推荐」，
    不是「假装它没发生过」。同时带上这一轮的计划归属（`plan_id`，为空 = 新方向）。
    """
    if request_id is None:
        latest = conn.execute("SELECT request_id FROM candidate ORDER BY id DESC LIMIT 1").fetchone()
        request_id = int(latest["request_id"]) if latest is not None else None
    if request_id is None:
        return {
            "request_id": None,
            "raw_text": None,
            "plan_id": None,
            "candidates": [],
            "recommended": None,
        }

    request_row = conn.execute(
        "SELECT id, kind, raw_text, plan_id, created_at FROM learning_request WHERE id = ?",
        (request_id,),
    ).fetchone()
    rows = conn.execute(
        "SELECT id, title, kind, why, depth_target, rank, is_recommended, status,"
        " reject_reason, payload, landing_plan_id FROM candidate WHERE request_id = ?"
        " ORDER BY rank, id",
        (request_id,),
    ).fetchall()
    candidates = [dict(row) for row in rows]
    plan_id = None if request_row is None else request_row["plan_id"]
    for item in candidates:
        item["plan_id"] = plan_id  # 候选随请求继承归属（SPEC 决策 33 ①）
        # 形状从 payload 解出来（T34）：老形状的候选 payload 是空的，按 directions 渲染。
        # 原始 JSON 不往外发——界面要的是解好的 steps。
        item["shape"], item["steps"] = _shape_and_steps(item.pop("payload", None))
    recommended = next((item for item in candidates if item["is_recommended"]), None)
    return {
        "request_id": request_id,
        "raw_text": None if request_row is None else request_row["raw_text"],
        "plan_id": plan_id,
        "created_at": None if request_row is None else request_row["created_at"],
        "candidates": candidates,
        "recommended": recommended,
    }


def _shape_and_steps(payload: Any) -> tuple[str, list[dict[str, Any]]]:
    """从候选的 `payload` 那列解出（形状, 步骤草案）。

    解不开、或是老形状的候选（没 payload）一律当 `directions`、步骤为空——真库里躺着
    2026-09-20 之前落的候选，它们本来就都是一条条独立的方向，按 directions 渲染才对。
    公开（去掉前导下划线）是因为 `blueprint.py` 的规划对话区要拿同一份草案给人看。
    """
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return "directions", []
    if not isinstance(data, dict) or data.get("shape") != "path":
        return "directions", []
    steps = data.get("steps")
    return "path", steps if isinstance(steps, list) else []


def candidate_steps(row: sqlite3.Row) -> list[dict[str, Any]]:
    """一条候选自己带的**步骤草案**（T34）——只有 `path` 候选有。

    对话区与规划对话的上下文都用它：草案不是成品，是出蓝图时的底稿（决策 41）。
    """
    return _shape_and_steps(row["payload"])[1]


def decide_candidate(
    conn: sqlite3.Connection,
    candidate_id: int,
    *,
    accept: bool,
    reason: str | None = None,
    plan_id: int | None = None,
) -> dict[str, Any]:
    """采纳或否决一条候选。

    与提案裁定同一条口径（SPEC 第 18 节第 22 条）：候选的「不再算数」由自己的业务终态
    表达（`accepted` / `rejected`），不动台账的生命周期列。否决必须写理由——它同时进
    `reject_reason` 列与台账流水，下次「找」把标题当禁区用。

    采纳落点**显式化**（SPEC 决策 33 ②，2026-09-17）：候选是一条学习方向，采纳时落成
    一个阶段——落进**候选自带的计划归属**；归属为空（「新方向」）时必须由 `plan_id`
    指明进哪个计划，否则报错，绝不「偷偷落最新」。台账每个操作各自提交、没有请求级事务，
    所以「计划存在 / 无同名未收尾阶段」都**预检在改候选状态之前**——失败时候选保持
    proposed 可重试，绝不留下「已采纳却没建阶段」的半截状态。
    """
    row = conn.execute("SELECT * FROM candidate WHERE id = ?", (candidate_id,)).fetchone()
    if row is None:
        raise CandidateNotFound(f"候选 id={candidate_id} 不存在")
    if row["status"] != "proposed":
        raise CandidateConflict(
            f"候选 id={candidate_id} 已经裁定过了（{row['status']}），不能再改"
        )

    target = "accepted" if accept else "rejected"
    if not accept and not str(reason or "").strip():
        raise AdvisorError("否决必须写明理由——它会被当成禁区，下次「找」不再推荐它")

    target_plan_id: int | None = None
    if accept:
        target_plan_id = landing_plan(conn, row, plan_id)
        try:
            # add_node 内部还会再查一次重名；这里提前查是为了把失败挡在改状态之前
            plan.assert_no_open_duplicate(conn, target_plan_id, "stage", str(row["title"]))
        except plan.DuplicateNode as error:
            raise CandidateConflict(str(error)) from error

    ledger.set_status(
        conn,
        "candidate",
        candidate_id,
        target,
        actor="user",
        reason=None if reason is None else reason.strip(),
        # 否决理由与**采纳落点**都并进同一条 UPDATE（T37：落点要留得住，见 `landing_plan`）。
        # 两者互斥：采纳时记落点、否决时记理由，不会同时出现。
        extra=(
            {"reject_reason": reason.strip()} if not accept else {"landing_plan_id": target_plan_id}
        ),
    )

    node_id: int | None = None
    if accept:
        node_id = plan.add_node(conn, target_plan_id, "stage", str(row["title"]), actor="user")

    shape, steps = _shape_and_steps(row["payload"])
    return {
        "id": candidate_id,
        "status": target,
        "reject_reason": None if accept else reason,
        "plan_id": target_plan_id,
        "node_id": node_id,
        # 采纳一条路径候选（T34）时把步骤草案一并回带：界面与规划对话要拿它当底稿。
        # 否决时它是空的——否掉的是那条路，没有「剩下的几步」可言。
        "shape": shape,
        "steps": steps if accept else [],
    }


def landing_plan(
    conn: sqlite3.Connection, candidate: sqlite3.Row, explicit_plan_id: int | None
) -> int:
    """这条候选该落进哪个计划（SPEC 决策 33 ②）。

    顺序：**采纳时已经落定的那个计划**（`landing_plan_id`，T37）> 调用方显式指定 >
    候选自带的归属；都定不下来就报错（不许偷偷落最新）。

    第一档为什么必须在最前（2026-09-21 T37 补的洞）：「新方向」的候选（那一轮不属于任何
    计划）落点是**采纳那一刻现选的**——此前这个事实只活在当刻响应与 `plan_chat` 里，
    刷新一次页面就没了：规划对话会不知道自己在哪个计划里，又让人「先指定注入的计划」，
    直接聊甚至报「这条候选没有计划归属」。可它明明已经落进去了（阶段都建好了）。
    所以落点记在候选自己身上（`candidate.landing_plan_id`），它同时是这段对话的归属依据。

    显式指定与它不一致也报错——与「候选归属 vs 显式指定」同一条口径：落点是一个既定事实，
    不是每次调用都能重新表决的。

    公开（去掉前导下划线）是因为 `blueprint.py` 的对话与蓝图要用**同一套**归属校验：
    采纳落哪个计划、这段对话属于哪个计划，必须是同一个答案，否则蓝图会建到别的计划里。
    """
    landed = candidate["landing_plan_id"]
    if landed is not None:
        if explicit_plan_id is not None and int(landed) != int(explicit_plan_id):
            raise CandidateConflict(
                f"这条候选采纳时已经落进计划 #{landed}，不能改成 #{explicit_plan_id}"
            )
        target = int(landed)
    else:
        request_row = conn.execute(
            "SELECT plan_id FROM learning_request WHERE id = ?", (candidate["request_id"],)
        ).fetchone()
        inherited = None if request_row is None else request_row["plan_id"]
        if inherited is not None and explicit_plan_id is not None and int(inherited) != int(explicit_plan_id):
            raise CandidateConflict(
                f"这条候选属于计划 #{inherited}，不能落到计划 #{explicit_plan_id}"
            )
        chosen = inherited if inherited is not None else explicit_plan_id
        if chosen is None:
            raise CandidateConflict(
                "这条候选没有计划归属（「新方向」）——采纳时要指明进哪个计划，"
                "或先建一个新计划再采纳"
            )
        target = int(chosen)

    # 两条路都要当场核对：计划可能在这条候选落进去之后被收尾或作废了
    plan_row = plan.resolve_plan(conn, target)
    if plan_row is None:
        raise CandidateConflict(f"计划 id={target} 不存在")
    if plan_row["status"] != "active":
        raise CandidateConflict(
            f"计划 id={target} 已不是进行中（{plan_row['status']}），不能往里落阶段"
        )
    return target
