"""计划级对话：蓝图落地之后，接着聊这个计划（SPEC 决策 37、41）。

为什么单独一个模块、单独一张表：这和 `blueprint.py` 里那段「定方向」的对话不是一回事——
那段绑在候选上、有 6 轮上限、终点是出一棵蓝图；这一段跟着计划走、**不限轮数**，
聊的是执行期的事（卡在哪、下一步先做哪个、要不要调整节奏），蓝图建成之后才真正开始用它。
两者生命周期与上限都不同，塞进一张表只会让每条查询先判断「这是哪种对话」。

**资料是它自己读的**（2026-09-20 起，SPEC 决策 40）：原先每一轮都把整棵计划树、最近
5 份报告、全部档案、这条方向的来历与蓝图拼成一大块发给模型——不管这一句问的是什么。
现在换成**受控工具循环**（`agent_runtime.py` + `agent_tools.py`）：它自己说要读什么，
系统去取，取来的才算它这一轮的依据。四个只读工具：当前计划、最近报告、长期档案、
计划来历；计划编号由系统注入，它读不到别的计划。

三条纪律：
- LLM 只产出**提案**：这一段对话里的「我的状态变了」要落成 `profile_change` 提案，
  写档案必须经你裁定（SPEC 第 8 节铁律）。对话本身不碰计划一个字。
- **聊到明确要改时还能附一条可执行建议**（2026-09-18 T31 起，SPEC 决策 39；2026-09-28
  决策 44 加门槛：只有他明确请求修改的 intent=modify 轮才许提）：改一个已有节点、
  或加一件任务 / 一个阶段。建议落成 `kind=plan_change` 的待裁定提案，你当场点「确认」
  才走写入口（`app/plan_change.py` 管这一类）。它自己**一个字段也写不动**。
- 调用卡在 `llm.Operation` 上：**每轮最多 3 次模型调用**（决策 40 起）——工具轮与
  「输出不合格带原因重说一次」共用这个额度，结构错误也计入上限；**最多 6 次只读工具调用**。
- 成本闸不是轮数而是**历史字符上限**（决策 6/37 的修订）：长期窗口不能用「聊六次就锁死」
  去卡，真正要防的是「代码写出死循环」——而这里每一次都由你手点一句才动一次，没有循环风险。

**输出是信封**（T31 起）：`{"reply": "人话", "suggestion": null 或一条建议}`；要读资料时
换成 `{"tool_calls": [{"name": ..., "args": {}}]}`。2026-09-26 起回话信封还可以带
`questions`（结构化追问数组）：需要他拍板或补充事实时，把要问的做成问答卡而不是散文。
2026-09-28 起回话信封多一个**必填的 `intent`**（SPEC 决策 44 的行为分层，双入口整改
IV-01）：chat＝寒暄/普通交流、answer＝询问执行现状、discuss＝讨论技术或方案、
modify＝明确请求修改当前计划——**只有 modify 才许附 suggestion**（`_check_reply` 里的
硬闸：别的意图硬塞建议就带原因重说）。库里仍只存 `reply` 那一段人话（追问另存 `plan_dialogue.questions` 一列）——
历史拼回上下文与 6000 字符截断的口径一行不改。**回话出口另有一道兜底**（2026-09-21 走查
整改第 2 条）：带原因重试一次之后**仍然是纯散文**的，就把那段散文当回话收下、建议记为空
（「要读资料」与「建议」两处仍严格——它们要真去查表、真去落提案）。提示词因此拆成两段：
`CONTRACT_PROMPT`（形状与纪律，视为代码的一部分）+ `STYLE_PROMPT`（语气与篇幅，可调）。

**每轮开头可能多一行记忆提醒**（走查整改第 3 条）：记忆库自这段对话上次读之后变过，就把
「变过哪几条」写在工具目录之后（`memory.change_digest`，没有变化就没有这一行），契约段里的
硬规则要求那一轮先读一次记忆。它读的是**整份当前值**，不是增量——只给增量压不住历史里的旧说法。

**每轮留一行运行账**（`agent_run`，决策 40）：调了几次模型、读了哪几样、为什么停下。
它不是记忆（下一轮不读它），是给人排错与对账用的；界面上的「本轮依据」就是它。
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from . import (
    agent_runtime,
    advisor,
    blueprint as blueprint_mod,
    ledger,
    llm,
    memory,
    plan_change,
    profile,
)
from .db import now_iso

TASK_DIALOGUE = "plan_dialogue"
TASK_EXTRACT = "dialogue_extract"

# 落进 proposal.kind：用它已有的那一个（决策 6 里「档案变更」这一类的形状一直空着，
# T28 的计划对话成了它的第一个生产者）。
PROFILE_CHANGE_KIND = "profile_change"

# 历史累计字符上限：比候选对话（4000）宽一些，因为执行期要说的事实更多。超出从最早截断。
# 这笔账说的是**对话历史**；这一轮读来的资料另有一道闸（`agent_runtime.TOOL_TOTAL_CHAR_LIMIT`）。
DIALOGUE_CHAR_LIMIT = 6000

# 提炼档案提案时最多取几条。定成常量便于按实测调。
MAX_EXTRACTED_ITEMS = 3


class DialogueError(RuntimeError):
    """对话链路上的明确错误：模型输出不合格、没东西可提炼。"""


class DialogueNotFound(DialogueError):
    """计划不存在 → 接口层翻成 404。"""


class DialogueConflict(DialogueError):
    """与现状冲突：还没聊过就要提炼 → 接口层翻成 409。"""


# ---------- 读与写这张表 ----------

def plan_of(conn: sqlite3.Connection, plan_id: int) -> sqlite3.Row:
    """这段对话跟着哪个计划。

    **不按状态拦**：讨论不该被计划状态挡住——暂停了正是最需要商量的时刻，作废的
    也从「历史计划」进得来（回顾「当时为什么没做下去」是正当需求）。这与候选对话那条
    要求「计划进行中」不同，因为那条会往计划里建树，而这里只说话。
    """
    row = conn.execute("SELECT * FROM plan WHERE id = ?", (plan_id,)).fetchone()
    if row is None:
        raise DialogueNotFound(f"计划 id={plan_id} 不存在")
    return row


def messages_of(conn: sqlite3.Connection, plan_id: int) -> list[sqlite3.Row]:
    """这段对话的全部消息。`id` 也要取——建议与提案就是靠它关联的（决策 39）。"""
    return list(
        conn.execute(
            "SELECT id, role, content, questions, created_at FROM plan_dialogue"
            " WHERE plan_id = ? ORDER BY id",
            (plan_id,),
        ).fetchall()
    )


def turns_used(conn: sqlite3.Connection, plan_id: int) -> int:
    """已经聊了几句 = 你发过几句话（不限轮数，这个数只用来显示）。"""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM plan_dialogue WHERE plan_id = ? AND role = 'user'",
        (plan_id,),
    ).fetchone()
    return int(row["n"])


def _record(
    conn: sqlite3.Connection,
    plan_id: int,
    role: str,
    content: str,
    questions: list[dict[str, Any]] | None = None,
) -> int:
    """追加一句，返回它的行号。这张表是追加式日志、不经台账（同 learning_request 的先例）。

    返回行号是因为 `plan_change` 提案要记着「这条建议是从哪一句里冒出来的」
    （payload 里的 `dialogue_id`）——界面据此把确认条挂在那条消息下面。
    助手这轮要是带了结构化追问（2026-09-26），随行存成 JSON——刷新页面之后
    问答卡还要能渲染出来。
    """
    cursor = conn.execute(
        "INSERT INTO plan_dialogue (plan_id, role, content, questions, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (
            plan_id,
            role,
            content,
            None if questions is None else json.dumps(questions, ensure_ascii=False),
            now_iso(),
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def _trim(rows: list[sqlite3.Row]) -> list[dict[str, str]]:
    """历史 → messages，按 `DIALOGUE_CHAR_LIMIT` **从最早截断**。

    最新那一句永远留着：它就是这一轮要回应的东西，截掉就没有对话可言了。
    """
    kept: list[dict[str, str]] = []
    used = 0
    for row in reversed(rows):
        content = str(row["content"])
        if kept and used + len(content) > DIALOGUE_CHAR_LIMIT:
            break
        kept.insert(0, {"role": "assistant" if str(row["role"]) == "assistant" else "user",
                        "content": content})
        used += len(content)
    return kept


# ---------- 提示词：契约与风格分开两块（2026-09-21 走查整改第 2 条） ----------
#
# 为什么拆：原先它们混在同一段 `SYSTEM_PROMPT` 里，于是「改语气」和「改接口」看起来是同一
# 件事。拆开之后**契约段视为代码的一部分**——改它等于改接口（输出形状、校验口径、动作清单、
# 哪些不许做，都在这儿）；风格段是以后调语气、甚至做成设置项的唯一入口。
# 拼起来仍是原来那一段系统提示词，模型那边看不出区别。

# 契约段：输出的形状与纪律。**动它要连带改验收器与测试**。
CONTRACT_PROMPT = (
    "【输出契约】每一轮你只输出一个 JSON 对象，两种形状选一种：\n"
    "① 要读资料：" '{"tool_calls": [{"name": "read_current_plan", "args": {}}]}'
    "——一次可以要好几样，能一次读完的别分几轮读；读不出结论就直说还缺什么，不要猜。\n"
    "② 直接回话："
    '{"intent": "chat / answer / discuss / modify 之一", "reply": "你要说的话", '
    '"suggestion": null 或一条建议, "questions": 可选的问题数组}'
    "——不要解释、不要客套、不要 Markdown 代码块。intent 必填，漏了就是不合格输出。\n"
    "- **【行为分层】先判断他这句话想要什么、挑定 intent，再决定说不说建议**——四类各有边界：\n"
    "  - chat＝寒暄/普通交流：自然接话、顺着聊，不读工具、不提建议；\n"
    "  - answer＝询问执行现状：先读当前计划（必要时报表 / 档案），**用当前计划的事实回答**，"
    "不用别的计划的经历代替；\n"
    "  - discuss＝讨论技术或方案可能性：说明取舍、回答他的问题，可以读资料——"
    "**给利弊不等于授权修改**，这一轮不提建议；\n"
    "  - modify＝明确请求修改当前计划：他说清了要改哪里、改成什么，你**核对对象之后**才提"
    "一条待确认建议；拿不准他是不是真要改，就在 reply 里先问清，这一轮不附建议。\n"
    "- **questions（他要选项拍板时就是必带，不是可选）**：当他要你在几个选项里拍板"
    "（「给我几个选项」「让我选」「哪个好」），或者你自己就要他二选一、排优先级——"
    "这种时候你的选项**必须**做成问答卡带在 questions 里，不许只在 reply 里列甲乙丙："
    '[{"title": "问题一句话", "options": ["选项甲", "选项乙"], "multiple": false, '
    '"allow_custom": true}]。最多 3 问、每问 2–4 个选项；**选项只写短语（15 字内）**，'
    "选项的补充说明、代价对比放 description 字段，不塞进选项文本里；"
    "multiple=true 表示可多选。开放式的「你怎么想」不进 questions、写在 reply 里；"
    "不需要拍板就整个别带。问答卡与 suggestion 互不相干：歧义先问清，questions 照旧用；"
    "intent=modify 而这轮想先澄清，也是先问一句、不是硬凑一条建议。\n"
    "- suggestion：**一轮最多一条**，**只有 intent=modify 的轮才许带**（别的意图硬塞建议，"
    "整轮判不合格重说）；**提建议的前提是他原话里有明确的修改要求**（「改成…」「加一件…」"
    "「把…推迟到…」这类说法）——只凭你自己判断「应该改」不算，他没说改就先问，"
    "他明确说出修改要求的那一轮再提；只在「改哪里、改成什么、为什么」都说得具体时才提；"
    "拿不准就在 reply 里先问一句（要点选的就做成 questions 问答卡）、suggestion 给 null。"
    "只给点了名的节点编号——"
    "编号得是你从读到的资料里看到的 #号，不要自己编：\n"
    '   改节点 {"action": "update_node", "node_id": 12, "fields": {"due_date": "2026-10-08"}, '
    '"why": "为什么该这么改"}——fields 里只能出现 title / deliverable / due_date，只写要改的那几样；'
    "due_date 给空字符串表示清掉它。\n"
    '   加任务 {"action": "add_task", "node_id": 8, "tasks": [{"title": "任务名", '
    '"due_date": "2026-10-08"}, {"title": "第二件"}], "why": "..."}——node_id 给**阶段**的编号；'
    "tasks 里可以一次给几件（**最多 5 件**），他要是说「排一下」「列几条」就把该排的一次排完，"
    "别一件一件挤牙膏；due_date 拿不准就别给这个字段。\n"
    '   加阶段 {"action": "add_stage", "stage": {"title": "阶段名", '
    '"deliverable": "这个阶段结束时能看见、能验收的东西", "why": "为什么先做它"}, '
    '"tasks": [{"title": "第一件"}, {"title": "第二件"}], "why": "..."}'
    "——新阶段排在最后，必须带「要交的东西」；这个阶段拆出来的任务**一并放在 tasks 里**"
    "（最多 5 件），不要建完空阶段再一件一件补。\n"
    "- 你不能：删节点、替他打勾 / 跳过 / 交交付物、碰别的计划。"
    "「这块不做了」在计划里是用打勾 / 跳过 / 收尾表达的，不是你该提的建议。\n"
    "- 一次给一批不等于可以大改：只排你真说得清的那几件，剩下一律别凑数。\n"
    "- 建议只是建议：他点「确认」才会真改，所以别在 reply 里吹你已经改完了。\n"
    "- **出现【记忆库变了】那一行时，本轮必须先调用一次 read_memories 再回答**——"
    "你早先读到的说法可能已经不作数了。\n"
)

# 风格段：你是谁、怎么说。调语气只动这一块，不碰上面的契约。
STYLE_PROMPT = (
    "【你是谁、怎么说】你是这个学习计划的陪跑顾问，正在和计划的主人讨论它的执行情况。"
    "**你不预先拿到他的业务数据**（阶段、任务、报告、档案、这个计划的来历都在系统里，"
    "要什么自己读）——别讲放之四海皆准的话，也别凭空编事实。"
    "reply 里直接说人话，可以用短段落或列表，不要客套开场；默认控制在 200 字以内，"
    "除非他要求展开。"
    "**reply 是给人看的话：不要把档案或节点的 #数字 编号当脚注写进正文**——"
    "确需指代具体对象就用它的名字说；资料里带的编号是给你核对用的，别照抄进 reply。"
)

# 拼起来发出去的那一段（契约在前、风格在后——形状是硬要求，语气是软要求）。
SYSTEM_PROMPT = f"{CONTRACT_PROMPT}\n{STYLE_PROMPT}"

EXTRACT_SYSTEM_PROMPT = (
    "你从一段对话里提炼「需要更新长期档案」的条目。你只输出一个 JSON 对象："
    "不要解释、不要客套、不要 Markdown 代码块。"
)


# ---------- 看 / 聊 ----------

MAX_QUESTIONS = 3
MAX_QUESTION_OPTIONS = 4


class Question(BaseModel):
    """一个要点选（或填写）的问题：界面把它渲染成问答卡。

    `options` 为空就是自由填写题（界面给输入框）；给选项时 2–4 个、可多选由 `multiple` 说。
    放宽到 8 个是为了把「选项给多了」留给 `_clean_questions` 截断，而不是烧一次重说。
    """

    title: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=200)
    options: list[str] = Field(default_factory=list, max_length=8)
    multiple: bool = False
    allow_custom: bool = True


class Reply(BaseModel):
    """这一轮的回话：自报意图 + 人话（存进库里）+ **最多一条**可执行建议（决策 39）。

    `intent`（2026-09-28，SPEC 决策 44 的行为分层）**必填、无默认值**——模型漏报就是
    字段不合格，带原因重说：chat＝寒暄/普通交流，answer＝询问执行现状，discuss＝讨论
    技术或方案可能性，modify＝明确请求修改当前计划。能不能附 `suggestion` 由 intent
    说了算（硬闸在 `_check_reply`）：只有 modify 才许附。`suggestion` 是**单个对象或
    null**、不是数组：「一轮最多一条」从形状上就成立，不用靠字数限制去数。`questions`
    （2026-09-26）：要他在明确选项里**拍板**时带上的选择题（每问必须带选项），最多 3 问；
    开放式提问留在 reply 散文里，不进 questions。
    """

    reply: str = Field(min_length=1)
    intent: Literal["chat", "answer", "discuss", "modify"]
    suggestion: plan_change.Suggestion | None = None
    questions: list[Question] | None = Field(default=None, max_length=MAX_QUESTIONS)


def _details(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or '(根)'}: {item['msg']}"
        for item in error.errors()
    )


def _clean_questions(
    questions: list[Question] | None,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """把模型给的问题数组清成要落库的形状。**能清就清、清完收下**（2026-09-26 真机教训）：
    空白选项去掉、重复选项去重、选项剩一个的降级成自由填写题、空标题的整问丢弃——
    这些都不值得烧一次「带原因重说」的模型调用；真正致命的毛病只剩「题目全空」。
    """
    if not questions:
        return None, None
    cleaned: list[dict[str, Any]] = []
    for question in questions:
        title = question.title.strip()
        if not title:
            continue  # 这问没法显示，整问丢弃
        options: list[str] = []
        for option in question.options:
            text = option.strip()
            if text and text not in options:
                options.append(text)
        if len(options) > MAX_QUESTION_OPTIONS:
            options = options[:MAX_QUESTION_OPTIONS]
        # 只剩一个选项的「选择题」没有选的意义，降级成自由填写
        if len(options) == 1:
            options = []
        cleaned.append(
            {
                "title": title,
                "description": (question.description or "").strip() or None,
                "options": options,
                # 只剩一个选项的「选择题」没有选的意义，降级成自由填写
                "multiple": len(options) >= 2 and question.multiple,
                "allow_custom": question.allow_custom,
            }
        )
    return (cleaned or None), None


# 「他这一句要的是选项拍板」的话术标记（2026-09-26 真机教训：光靠提示词压不住，
# 他明确要选项而模型只给散文甲乙丙时，验收层直接判不合格带原因重说）。
OPTION_REQUEST_MARKS = (
    "给我几个选项", "给我选项", "给我几个方案", "让我选", "我来拍板", "帮我选",
    "出选择题", "做选择题", "二选一", "三选一", "挑一个", "选一个", "选哪条", "选哪个",
)


def _wants_option_card(message: str) -> bool:
    return any(mark in str(message or "") for mark in OPTION_REQUEST_MARKS)


# 「他这一句明确在要求修改计划」的话术标记（2026-09-28，决策 44 的第二道闸）：intent 是
# 模型**自报**的——它把一句纯讨论硬报成 modify 就能绕过「只有 modify 才许带建议」那道闸。
# 所以 suggestion 真要落提案，还得他**原话里说得出修改的说法**。口径刻意收窄：只收明确
# 的修改动词短语，**宁可漏收**（漏了他会再补一句「对，改成…」，顶多多一轮确认），
# **不可滥收**（把「换成 X 好不好」这类商量误判成修改请求，等于回到误产提案的老路）。
MODIFICATION_INTENT_MARKS = (
    # 改既有的东西
    "改一下", "改成", "改为", "帮我改", "改到", "改期", "改截止", "改交付", "改标题", "改名",
    "设为", "设成",
    # 排期与时间
    "推迟", "延到", "延后", "提前到", "挪到", "挪一下", "换个时间",
    # 加东西
    "加一个", "加一件", "加个", "加一条", "加一项", "加一段", "新增",
    "补一个", "补上", "补个",
    # 一次排一批
    "排一下", "排个", "排进", "排上",
    # 调整 / 更新
    "调整一下", "调整成", "更新一下", "更新成",
)


def _wants_modification(message: str) -> bool:
    """Require a direct instruction, not a verb embedded in a question or example."""
    text = str(message or "").strip()
    # Keep quoted node names ("给「学 HTTP」加一件…") but do not treat a
    # quoted *instruction* as an instruction from the user.
    text = re.sub(r"[「『“\"'‘][^」』”\"'’]*[」』”\"'’]", "", text)
    if not text or re.search(
        r"如果|假如|假设|要是|能否|可不可以|会怎样|怎么样|好不好|是否|是不是|要不要|"
        r"先聊|讨论|探讨|只是问|举例|比如|怎么(?:改|加|排|调|挪|推|延|更|设)|"
        r"(?:不要|不用|不必|不想|不是|不打算|不需要|先别).*?(?:改|加|排|调|挪|推|延|更|设)",
        text,
    ):
        return False
    # Substrings anywhere in the utterance are not enough: "修改成会怎样" or
    # "我了解到可以新增…" are descriptions, not a request to edit this plan.
    action = "|".join(re.escape(mark) for mark in MODIFICATION_INTENT_MARKS)
    command = rf"(?:^|[，,。；;！!])\s*(?:对[，,]\s*|顺便)?(?:请|麻烦|帮我|给我|替我)?\s*(?:把|将|给)?[^，,。；;！？?]{{0,60}}?(?:{action})"
    return bool(re.search(command, text) and re.search(
        rf"(?:^|[，,。；;！!])\s*(?:对[，,]\s*)?(?:顺便|请|麻烦|帮我|给我|替我|把|将|给|改|加|新增|补|排|推迟|延|挪|提前|调整|更新|设)",
        text,
    ))


def _confirmed_clarification(
    conn: sqlite3.Connection, plan_id: int, message: str, suggestion: plan_change.Suggestion
) -> bool:
    """A short yes only confirms the immediately preceding, concrete modification question."""
    if not re.fullmatch(r"(?:对|是|好的|好|确认|可以|就这样|按这个来|就按你说的改)[。！!\s]*", message.strip()):
        return False
    rows = messages_of(conn, plan_id)
    # say() has already recorded this user's answer; failed turns and intervening replies break continuity.
    if len(rows) < 3 or rows[-1]["role"] != "user" or rows[-2]["role"] != "assistant":
        return False
    previous = rows[-2]
    questions = _questions_of(previous)
    if not questions:
        return False
    context = str(previous["content"]) + " ".join(
        str(question.get("title") or "") + " " + " ".join(str(option) for option in question.get("options") or [])
        for question in questions
    )
    if not re.search(r"改|加|排|挪|推迟|延后|调整|更新", context):
        return False
    # A bare yes cannot choose between competing modifications (including different dates).
    options = [str(option) for question in questions for option in question.get("options") or []]
    concrete_options = [option for option in options if re.search(r"改|加|排|挪|推迟|延后|调整|更新", option)]
    if len(concrete_options) > 1:
        return False
    action = suggestion.action
    if action == "update_node":
        node = conn.execute("SELECT title FROM plan_node WHERE id = ? AND plan_id = ?", (suggestion.node_id, plan_id)).fetchone()
        if node is None or str(node["title"]) not in context:
            return False
        return bool(suggestion.fields) and all(
            str(value) in context or (isinstance(value, str) and len(value) == 10 and value[5:] in context)
            for value in suggestion.fields.values()
        )
    if action == "add_task":
        node = conn.execute("SELECT title FROM plan_node WHERE id = ? AND plan_id = ?", (suggestion.node_id, plan_id)).fetchone()
        tasks = suggestion.tasks or ([suggestion.task] if suggestion.task else [])
        return bool(node and str(node["title"]) in context and tasks and all(str(task.get("title") or "") in context for task in tasks))
    if action == "add_stage":
        return bool(suggestion.stage and str(suggestion.stage.get("title") or "") in context and str(suggestion.stage.get("deliverable") or "") in context)
    return False


def _check_reply(
    conn: sqlite3.Connection, plan_id: int, text: str, user_message: str = ""
) -> tuple[Any, ...]:
    """验收这一轮。合格 →（人话，要落库的建议 payload 或 None，None，结构化追问或 None，
    自报的 intent）；不合格 →（None, None, 不合格原因）。

    四件事一起判——输出是不是信封、人话有没有、**意图与建议配不配套**、建议（如果有）
    站不站得住。意图硬闸（2026-09-28，SPEC 决策 44）有两道：其一，它自己判了
    chat/answer/discuss 却硬塞一条建议 → 判不合格重说，只有 modify 才许提；其二，modify
    也是它**自报**的——suggestion 非空而他的原话里说不出修改的说法（见
    MODIFICATION_INTENT_MARKS）同样判不合格重说，免得「把讨论硬报成修改」绕过第一道。
    反过来 intent=modify 而没带建议是合法的（先澄清再说，不算毛病）。建议里「点名的节点
    不存在 / 不属于这个计划 / 改前＝改后 / 名字撞车」这类**批不了**的毛病也在这里拦下：
    宁可不提，也不落一条等你点了「确认」才报错的提案（决策 39）。追问（如果有）在这里
    清成落库形状。他这一句明确要选项拍板（见 OPTION_REQUEST_MARKS）而回话没带 questions
    时，同样判不合格重说——这是硬闸，不指望模型自觉。
    """
    data = advisor.extract_json(text)
    if data is None:
        return None, None, "输出不是合法的 JSON 对象"

    try:
        reply = Reply.model_validate(data)
    except ValidationError as error:
        return None, None, f"字段不合格（{_details(error)}）"

    said = reply.reply.strip()
    if not said:
        return None, None, "reply 是空的——这一轮等于一个字都没说"

    questions, question_problem = _clean_questions(reply.questions)
    if question_problem is not None:
        return None, None, f"questions 不合格（{question_problem}）"

    if questions is None and _wants_option_card(user_message):
        return (
            None,
            None,
            "他这句话要的是**在选项里拍板**——把你要给的选项做成 questions 问答卡"
            "（1 问、2–4 个选项、选项写短语），选项细节仍可写在 reply 里展开；"
            "不要只给散文甲乙丙",
            None,
        )

    if reply.suggestion is not None and reply.intent != "modify":
        return (
            None,
            None,
            f"你把这轮判断为 {reply.intent}，就不该附建议——只有他明确要求修改当前计划"
            "（intent=modify）才能提；拿不准就在 reply 里先问清意图",
        )

    # 第二道闸（2026-09-28，决策 44）：intent 是它**自报**的，光凭上面那道拦不住「把普通
    # 讨论硬报成 modify」。建议真要落提案，他原话里得说得出修改的说法
    # （MODIFICATION_INTENT_MARKS）；说不出就是看不出他要求改——判不合格重说，
    # 宁可让它多问一句确认，也不落一条没人要的提案。
    if (
        reply.suggestion is not None
        and reply.intent == "modify"
        and not (_wants_modification(user_message) or _confirmed_clarification(conn, plan_id, user_message, reply.suggestion))
    ):
        return (
            None,
            None,
            "他这句话看不出是在要求修改当前计划——先别提建议：要么按讨论/解答回话"
            "（intent 用 discuss/answer），要么用 questions 问答卡先确认他是不是真想改、"
            "想改哪里；他明确说出修改要求的那一轮再提",
        )

    if reply.suggestion is None:
        return said, None, None, questions, reply.intent  # 没有建议就是纯聊天，什么都不落

    payload, problem = plan_change.check(conn, plan_id, reply.suggestion)
    if problem is not None:
        return None, None, f"建议不合格（{problem}）"
    return said, payload, None, questions, reply.intent


def _prose_reply(text: str) -> str | None:
    """散文兜底（2026-09-21 走查整改第 2 条）：模型 slipped 成纯散文时，那一段当我们的人话收下。

    只在「取不到 JSON 对象、去空白后又非空」时给东西——能解成对象的那些一律不接（形状不对
    就该由 `_check_reply` 判不合格、带原因重说）。`agent_runtime` 也只在**重试过一次之后**
    才问这个验收器，所以它接住的是「它第二次还是没套壳」，不是「它第一次就随便说」。
    """
    raw = str(text or "").strip()
    if not raw or advisor.extract_json(raw) is not None:
        return None
    return raw


def _suggestions(conn: sqlite3.Connection, plan_id: int) -> dict[int, dict[str, Any]]:
    """这段对话里冒出来过的建议：对话行号 → {proposal_id, summary, status}。

    关联靠提案 payload 里的 `dialogue_id`（**不加列、不动表结构**）：一条建议落一条
    `plan_change` 提案，提案里记着它是从对话的哪一行冒出来的。已裁定的也照样带回来——
    界面就不给按钮了，那一条「已确认 / 已忽略」还看得见。
    """
    rows = conn.execute(
        "SELECT * FROM proposal WHERE kind = ? ORDER BY id", (plan_change.KIND,)
    ).fetchall()
    landed: dict[int, dict[str, Any]] = {}
    for row in rows:
        payload = blueprint_mod.payload_of(row)
        if int(payload.get("plan_id") or 0) != int(plan_id):
            continue
        dialogue_id = payload.get("dialogue_id")
        if dialogue_id is None:
            continue
        landed[int(dialogue_id)] = {
            "proposal_id": int(row["id"]),
            "summary": str(payload.get("summary") or ""),
            "status": str(row["status"]),
        }
    return landed


def _runs(conn: sqlite3.Connection, plan_id: int) -> dict[int, dict[str, Any]]:
    """这段对话里每一轮的运行账：对话行号 → 这一轮读了什么、为什么停下。

    挂在哪一行由 `agent_run.dialogue_id` 说：答出来了就挂在助手那条回话上，失败时挂在你
    那一句上（那一轮没有回话）。界面上的「本轮依据」就是拿它渲染的——**刷新页面也还在**，
    不用把结果存在浏览器内存里。
    """
    rows = conn.execute(
        "SELECT * FROM agent_run WHERE plan_id = ? ORDER BY id", (plan_id,)
    ).fetchall()
    landed: dict[int, dict[str, Any]] = {}
    for row in rows:
        dialogue_id = row["dialogue_id"]
        if dialogue_id is None:
            continue
        landed[int(dialogue_id)] = public_run(row)
    return landed


def public_run(row: sqlite3.Row) -> dict[str, Any]:
    """一行 `agent_run` → 给界面看的形状。

    为什么这里还要现算 `tool_names`：库里存的是明细（每条带成败），而界面那行摘要要的是
    「读成了哪几样」——读失败的那条不该算依据。POST 的回执与这里因此永远同一个形状。
    """
    tools = _tools_of(row)
    return {
        "status": str(row["status"]),
        "stop_reason": str(row["stop_reason"]),
        "model_calls": int(row["model_calls"]),
        "tool_calls": int(row["tool_calls"]),
        "tool_names": list(
            dict.fromkeys(str(tool.get("name")) for tool in tools if tool.get("ok"))
        ),
        "tools": tools,
    }


def _tools_of(row: sqlite3.Row) -> list[dict[str, Any]]:
    """把 `agent_run.tools` 那段 JSON 解回来。库里那段坏了也不该让整个页面打不开。"""
    try:
        tools = json.loads(row["tools"] or "[]")
    except (TypeError, ValueError):
        return []
    return [tool for tool in tools if isinstance(tool, dict)] if isinstance(tools, list) else []


def _record_run(
    conn: sqlite3.Connection, plan_id: int, dialogue_id: int, runbook: agent_runtime.Runbook
) -> None:
    """把这一轮的运行账落一行。**成功的、撞上限的、失败的都落**——失败不记就无据可查。"""
    conn.execute(
        "INSERT INTO agent_run"
        " (plan_id, dialogue_id, status, stop_reason, model_calls, tool_calls, tools, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            plan_id,
            dialogue_id,
            runbook.status,
            runbook.stop_reason,
            runbook.model_calls,
            runbook.tool_calls,
            json.dumps([tool.as_dict() for tool in runbook.tools], ensure_ascii=False),
            now_iso(),
        ),
    )
    conn.commit()


def _questions_of(row: sqlite3.Row) -> list[dict[str, Any]] | None:
    """把一行消息里的追问 JSON 解回来。坏了就当没问过——不能让整个页面打不开。"""
    raw = row["questions"] if "questions" in row.keys() else None
    if not raw:
        return None
    try:
        questions = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(questions, list):
        return None
    return [question for question in questions if isinstance(question, dict)] or None


def view(conn: sqlite3.Connection, plan_id: int) -> dict[str, Any]:
    """看这段对话：计划、历史、聊了几句、能不能提炼档案提案。

    每条助手消息另带三个可选字段：`suggestion`（T31：它那一轮提的建议与那条提案，
    `{proposal_id, summary, status}` 或 `None`）、`run`（2026-09-20 决策 40：那一轮的
    运行账——读了哪几样、调了几次模型、为什么停下；界面据此渲染「本轮依据」，
    失败的运行挂在你那一句上）与 `questions`（2026-09-26：它那轮的结构化追问，
    界面渲染成问答卡）。
    """
    plan_of(conn, plan_id)
    rows = messages_of(conn, plan_id)
    used = turns_used(conn, plan_id)
    landed = _suggestions(conn, plan_id)
    runs = _runs(conn, plan_id)
    messages: list[dict[str, Any]] = []
    for row in rows:
        message = dict(row)
        questions = _questions_of(row)
        message.pop("questions", None)
        message["questions"] = questions
        message["suggestion"] = landed.get(int(row["id"]))
        message["run"] = runs.get(int(row["id"]))
        messages.append(message)
    return {
        "plan_id": plan_id,
        "messages": messages,
        "turns_used": used,
        "char_limit": DIALOGUE_CHAR_LIMIT,
        # 至少聊过一句才有东西可提炼（与「蓝图要先聊过一轮」同一条纪律）
        "can_extract": used >= 1,
    }


def say(
    conn: sqlite3.Connection,
    plan_id: int,
    message: str,
    *,
    provider_id: int | None = None,
    model: str | None = None,
    transport: llm.Transport | None = None,
) -> dict[str, Any]:
    """聊一句：记下你的话 → 跑一轮**受控工具循环** → 记下它的回话（人话）+ 落一条建议提案。

    循环的规矩在 `agent_runtime`：**最多 3 次模型调用**（工具轮与「不合格重说一次」共用）、
    **最多 6 次只读工具调用**；资料不预装，它自己读。撞上限或一直不合格就如实报错，
    **没有落任何提案**——而你那句话已经留在对话里了，再说一句就接着走。

    无论成没成，这一轮都往 `agent_run` 落一行（失败挂在你那一句上）：读了什么、为什么停下。

    助手这一侧存的仍是**人话**（信封里的 `reply`），不是 JSON：下一轮拼进上下文的是
    一段像对话的话，不是它自己吐的壳。信封里自报的 `intent`（2026-09-28，决策 44）
    随回执带回（新增键，老键一个没动）；只有明确请求修改的轮（intent=modify）才可能
    带出建议提案。
    """
    plan_of(conn, plan_id)
    text = str(message or "").strip()
    if not text:
        raise DialogueError("总得说点什么")

    # 验收合格那一轮自报的 intent（`_check_reply` 成功时的第 5 位）。`agent_runtime.Outcome`
    # 没有这个字段、循环那侧这轮不动，只好让验收闭包顺手记到一个格子里——最多记一次
    # （合格即返回）。散文兜底那轮没有信封、没有自报，取值见下面回执处。
    declared: list[str] = []

    def check_with_intent(raw: str) -> tuple[Any, ...]:
        result = _check_reply(conn, plan_id, raw, user_message=text)
        if len(result) > 4:
            declared.append(str(result[4]))
        return result

    asked_id = _record(conn, plan_id, "user", text)
    try:
        outcome = agent_runtime.run(
            conn,
            plan_id,
            task=TASK_DIALOGUE,
            system_prompt=SYSTEM_PROMPT,
            history=_trim(messages_of(conn, plan_id)),
            check_final=check_with_intent,
            prose_fallback=_prose_reply,
            # 记忆库自它上次读之后变过 → 这一轮开头多一行提醒（走查整改第 3 条）。
            # 没有变化、或这段对话从没读过记忆，就是 None（不硬塞噪音）。
            notice=memory.change_digest(conn, plan_id),
            provider_id=provider_id,
            model=model,
            transport=transport,
        )
    except agent_runtime.AgentStop as stop:
        # 失败的那一轮也要留账：挂在你的那一句上——这一轮没有助手回话可挂
        _record_run(conn, plan_id, asked_id, stop.runbook)
        raise DialogueError(str(stop)) from None
    except llm.LlmError as error:
        # 上游没通（没配 provider、超时、返回异常）：同样留一行失败账再往上抛
        _record_run(
            conn,
            plan_id,
            asked_id,
            agent_runtime.Runbook(
                status=agent_runtime.STATUS_FAILED,
                stop_reason=f"调用模型失败：{error}",
            ),
        )
        raise

    reply_id = _record(conn, plan_id, "assistant", outcome.reply, questions=outcome.questions)
    suggestion = _land_suggestion(conn, plan_id, reply_id, outcome.suggestion)
    _record_run(conn, plan_id, reply_id, outcome.runbook)
    run = outcome.runbook.as_dict()
    # 散文兜底收下的那段没有信封、没有自报意图——它就是一段没套壳的人话、不带建议，按 chat 记。
    intent = declared[0] if declared else "chat"
    return {
        "plan_id": plan_id,
        "reply": outcome.reply,
        # 2026-09-28（决策 44）：这一轮它自报的意图。新增键，老键一个没动。
        "intent": intent,
        "suggestion": suggestion,
        # 2026-09-26：这一轮带出的结构化追问（界面渲染成问答卡）；没问就是 null
        "questions": outcome.questions,
        "proposal_id": None if suggestion is None else int(suggestion["proposal_id"]),
        "turns_used": turns_used(conn, plan_id),
        "calls": outcome.runbook.model_calls,
        # 2026-09-20 新增（决策 40）：这一轮读了什么、为什么停下。老字段一个没动。
        "run": run,
        "tools_used": run["tool_names"],
        "stop_reason": run["stop_reason"],
    }


def _land_suggestion(
    conn: sqlite3.Connection, plan_id: int, dialogue_id: int, payload: dict[str, Any] | None
) -> dict[str, Any] | None:
    """把验收过的建议落成一条 `kind=plan_change` 的待裁定提案（决策 39）。

    界面上的「确认」就是裁定它：确认 → 批准（改的原地改、加的建节点），忽略 → 驳回
    （台账记「聊天里先不动」）。所以每条建议都有归宿，不会在 `/proposals` 堆着。
    """
    if payload is None:
        return None
    landed = {**payload, "dialogue_id": int(dialogue_id)}
    proposal_id = ledger.create_active(
        conn,
        "proposal",
        {
            "kind": plan_change.KIND,
            "payload": json.dumps(landed, ensure_ascii=False),
            "reason": (
                f"计划 #{plan_id} 的对话里聊出的一条改动建议：{landed.get('summary')}"
                "——等你确认"
            ),
        },
        actor="agent",
    )
    return {
        "proposal_id": proposal_id,
        "summary": str(landed.get("summary") or ""),
        "status": "pending",
    }


# ---------- 提炼档案提案 ----------

class ProfileChange(BaseModel):
    """一条要写进档案的变更建议。`why` 必填：台账要能回答「为什么这么记」。"""

    category: str = Field(min_length=1)
    content: str = Field(min_length=1)
    why: str = Field(min_length=1)


class Extraction(BaseModel):
    """提炼结果。**允许 0 条**——这段对话里没有值得改档案的变化才是常见情形。"""

    items: list[ProfileChange] = Field(default_factory=list, max_length=MAX_EXTRACTED_ITEMS)


def _check_extraction(text: str) -> tuple[Extraction | None, str | None]:
    data = advisor.extract_json(text)
    if data is None:
        return None, "输出不是合法的 JSON 对象"

    try:
        extraction = Extraction.model_validate(data)
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or '(根)'}: {item['msg']}"
            for item in error.errors()
        )
        return None, f"字段不合格（{details}）"

    unknown = sorted({item.category for item in extraction.items if item.category not in profile.PROFILE_TOKENS})
    if unknown:
        return None, (
            f"类别 {unknown} 不在约定的五个令牌里"
            f"（{' / '.join(profile.PROFILE_TOKENS)}）——档案只有这五类"
        )
    return extraction, None


def propose_profile_changes(
    conn: sqlite3.Connection,
    plan_id: int,
    *,
    provider_id: int | None = None,
    model: str | None = None,
    transport: llm.Transport | None = None,
) -> dict[str, Any]:
    """把这段对话里聊出的变化提炼成待裁定的**档案变更**提案。

    刻意做成一个**你点按钮才发生**的动作，而不是每轮自动附带：闲聊不该往
    `/proposals` 里撒提案。1 次调用 + 不合格带原因重试 1 次（结构化输出按老规矩重试）。
    """
    plan_of(conn, plan_id)
    rows = messages_of(conn, plan_id)
    if turns_used(conn, plan_id) < 1:
        raise DialogueConflict("这段对话还没聊过，没有东西可提炼")

    messages = [
        {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
        {"role": "user", "content": _extract_brief(conn, plan_id)},
        *_trim(rows),
        {
            "role": "user",
            "content": (
                "【现在请提炼】从上面这段对话里找出「需要更新长期档案」的条目，"
                "只输出一个 JSON 对象："
                '{"items": [{"category": "当前状态", "content": "一两句提炼结论", "why": "为什么该这么记"}]}。'
                f"- category 只能是这五个之一：{' / '.join(profile.PROFILE_TOKENS)}。"
                f"- 最多 {MAX_EXTRACTED_ITEMS} 条。"
                "- 只提炼对话里**真实出现过的变化**（时间变了、状态变了、目标变了）；"
                '没有就回 {"items": []}——空着是正常的，不许硬凑。'
                "- content 是提炼结论，不是把原话抄一遍。只输出 JSON，不要解释。"
            ),
        },
    ]
    operation = llm.Operation(conn, TASK_EXTRACT, limit=2, transport=transport)
    raw = operation.chat(messages, provider_id=provider_id, model=model)
    extraction, problem = _check_extraction(raw)

    attempts = 1
    if problem is not None:
        attempts = 2
        messages = [
            *messages,
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": f"你上面的输出不合格：{problem}。请只输出合格的 JSON 对象，不要任何解释。",
            },
        ]
        raw = operation.chat(messages, provider_id=provider_id, model=model)
        extraction, problem = _check_extraction(raw)
        if problem is not None:
            raise DialogueError(
                f"模型连着 {attempts} 次都没给出合格的提炼结果（{problem}）；"
                "已按上限中止，**没有落任何提案**"
            )
    if extraction is None:  # 理论上到不了
        raise DialogueError("内部状态异常：验收通过却没有解析出提炼结果")

    created: list[dict[str, Any]] = []
    for item in extraction.items:
        proposal_id = ledger.create_active(
            conn,
            "proposal",
            {
                "kind": PROFILE_CHANGE_KIND,
                "payload": json.dumps(
                    {
                        "category": item.category,
                        "content": item.content,
                        "why": item.why,
                        "plan_id": plan_id,
                    },
                    ensure_ascii=False,
                ),
                "reason": f"计划 #{plan_id} 的对话里聊出的档案变更：{item.why}",
            },
            actor="agent",
        )
        created.append(
            {"proposal_id": proposal_id, "category": item.category, "content": item.content}
        )

    return {
        "plan_id": plan_id,
        "items": created,
        "attempts": attempts,
        "calls": operation.used,
    }


def _extract_brief(conn: sqlite3.Connection, plan_id: int) -> str:
    """提炼那一下的背景：现档案 + 计划目标。

    为什么要给现档案：不给他就分不清「这是新情况」还是「档案里早写着」，
    会提炼出一堆同义重复——而档案的写入那道判重闸只挡一字不差的。
    """
    read = advisor.read_profile(conn)
    goal = str(plan_of(conn, plan_id)["goal"])
    lines = [f"【计划目标】{goal}", "", "【我的长期档案（当前有效值）】"]
    lines += [
        f"#{item['id']} [{item['category']}] {item['content']}" for item in read["items"]
    ] or ["（一条都没有）"]
    return "\n".join(lines)