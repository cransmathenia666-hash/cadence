"""Agent 的只读工具箱（2026-09-20 落地，SPEC 决策 40）。

为什么要有这个模块：计划对话原先每轮都**预装**全部业务资料——整棵计划树、最近 5 份
报告、全部档案、这条方向的来历与蓝图——不管这一句问的是什么。代价有两层：一是每轮
都在重发同样的几千字（花钱），二是它拿到的「依据」其实是与问题无关的背景噪音。
改成工具箱之后，模型先说要读什么，系统去取，**取来的才算它这一轮的依据**。

四条纪律：

1. **只有读，没有写**。写计划的唯一入口仍是 `plan_change` 提案 + 你当场裁定（决策 39），
   写档案仍走 `profile_change` 提案——这里一个写入口都不开。
2. **计划编号由系统注入**，不交给模型选：它只能读这一段对话所属的那一个计划。参数里
   出现 `plan_id` 之类一律拒——「读哪个计划」不是它能选的，是它待在哪决定的。
3. **不接受 SQL、路径或任意接口地址**：每个工具各自只会碰一张/几张约定的表，参数只允许
   登记过的几个键。
4. **每个工具有自己的字符上限**，超出按下文各自的规矩裁剪（多半是从最旧的截断）。

`TOOLS` 是**唯一入口**：写进 prompt 的工具目录与真正的执行都由它生成，所以「目录里写了、
其实没有」或者「有工具但没告诉它」这两类不一致从结构上不会发生。2026-09-21 的记忆系统就
是照这条往里加了两条：`read_memories`（当前有效的长期记忆，到复核时间的单独标出）与
`search_experiences`（按关键词 / 来源类型 / 时间检索过去的经历）——加工具只加注册表一行，
循环、上限、运行账一行没动。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable

from . import advisor, blueprint as blueprint_mod, memory, plan

# ---------- 各工具自己的字符上限 ----------
#
# 为什么分开给：一段报告摘要和整棵计划树的合理体量差好几倍，用同一个数只会两头不对。
# 数值按「够回答、不至于把上下文塞满」定——最坏一次运行读 6 样，总量在 `agent_runtime`
# 那侧另有总闸（`TOOL_TOTAL_CHAR_LIMIT`）。
PLAN_CHAR_LIMIT = 6000
REPORTS_CHAR_LIMIT = 2000
PROFILE_CHAR_LIMIT = 1500
LINEAGE_CHAR_LIMIT = 1500
BLUEPRINT_CHAR_LIMIT = 2500
MEMORY_CHAR_LIMIT = 2000
EXPERIENCE_CHAR_LIMIT = 2500

# 最近报告取几份。
RECENT_REPORTS = 5

# 读档案时 `category` 收什么：五个约定令牌，**加上它们的中文名**。
# 为什么要认中文：走查里模型把类别写成了「当前状态」，被参数校验挡下，而挡下的原文直接
# 显示在「本轮依据」里，用户看到一串英文令牌只会困惑（2026-09-21 整改第 4 条）。
PROFILE_CATEGORY_ALIASES: dict[str, str] = {
    "生活习惯": "life_habit",
    "生活记录": "life_log",
    "当前状态": "current_state",
    "短期目标+痛点": "short_term_goal",
    "短期目标": "short_term_goal",
    "长期主线": "long_axis",
}

# 报错时用的短名（「生活习惯 / 生活记录 / …」）。与 `advisor.PROFILE_CATEGORIES` 的中文名
# 同源，只是去掉括号里的举例、去掉空格——那些例子是写给用户看的，不是类别名的一部分。
PROFILE_CATEGORY_SHORT_NAMES: dict[str, str] = {
    "life_habit": "生活习惯",
    "life_log": "生活记录",
    "current_state": "当前状态",
    "short_term_goal": "短期目标+痛点",
    "long_axis": "长期主线",
}


def resolve_category(value: Any) -> str | None:
    """类别参数：令牌、中文短名、中文全名（带括号举例）都收。认不出来给 `None`。"""
    text = "".join(str(value or "").split())  # 「短期目标 + 当下痛点」→「短期目标+当下痛点」
    text = text.split("（")[0].split("(")[0]   # 去掉「（精力/时间/压力）」这类举例
    if text in advisor.PROFILE_CATEGORIES:
        return text
    return PROFILE_CATEGORY_ALIASES.get(text)


def category_error(value: Any) -> str:
    """类别不认时的**人话**报错：只列中文名，不甩令牌串（本轮依据是给人看的）。"""
    names = " / ".join(PROFILE_CATEGORY_SHORT_NAMES[name] for name in advisor.PROFILE_CATEGORIES)
    return f"类别只能是 {names} 之一（你写的是「{value}」）"


class ToolError(RuntimeError):
    """这次工具调用本身不成立：名字不认识、参数非法。

    它**不中断这一轮**：错误原样回给模型，让它自己纠正（额度照扣）。反复问不存在的东西
    最终会撞上调用上限——那一刻才中止，并且什么都不落。
    """


@dataclass(frozen=True)
class ToolOutcome:
    """一次工具调用的结果：`text` 回给模型，`summary` 给人看（界面「本轮依据」那一行）。"""

    text: str
    summary: str


@dataclass(frozen=True)
class Tool:
    """一个工具：名字、什么时候该读它（这句直接进 prompt 的目录）、认哪些参数、怎么执行。"""

    name: str
    summary: str
    args: str  # 参数写法的人话说明；没有参数就是空串
    arg_names: tuple[str, ...]
    run: Callable[[sqlite3.Connection, int, dict[str, Any]], ToolOutcome]
    # 参数值本身就要保护的那些键（比如检索用的关键词可能是一句私事）：
    # 它们只进 prompt 与执行，**不进 `agent_run` 的参数摘要**——日志里不留查询原文。
    hidden_args: tuple[str, ...] = field(default=())


# ---------- 四个工具 ----------

def _plan_facts(conn: sqlite3.Connection, plan_id: int) -> ToolOutcome:
    """当前计划长什么样：目标、状态、落后情况、阶段与任务（含括号里的 #编号）。"""
    tree = plan.plan_tree(conn, plan_id)
    plan_row = tree["plan"]
    lines = [
        "【这个计划】",
        f"- 目标：{plan_row['goal']}",
        f"- 状态：{plan_row['status']}",
    ]
    lag = tree["lag"]
    if lag["behind"]:
        lines.append(f"- 落后情况：落后 {lag['lag_days']} 天（最紧的一条是「{lag['worst']}」）")
    else:
        lines.append("- 落后情况：没落后")

    lines.append("")
    lines.append("【阶段与任务】按顺序（[ ] 未开始 / [~] 进行中 / [x] 完成 / [-] 跳过）")
    lines.append("（括号里的 #编号就是它们的编号——要给建议就用这个编号，别自己编）")
    marks = {"not_started": " ", "in_progress": "~", "done": "x", "stuck": "!", "skipped": "-"}
    for stage in tree["stages"]:
        head = "（已完成）" if stage["finished"] else ""
        lines.append(f"- 阶段「{stage['title']}」（#{stage['id']}）{head}")
        if stage["deliverable"]:
            lines.append(f"  要交的东西：{stage['deliverable']}")
        submission = stage["deliverable_submission"]
        if submission is not None:
            lines.append(f"  已提交交付物：{submission['url']}（{submission['note']}）")
        tasks = stage["tasks"]
        if not tasks:
            lines.append("  （这个阶段还没有任务）")
        for task in tasks:
            due = f"｜截止 {task['due_date']}" if task["due_date"] else ""
            late = f"｜落后 {task['lag_days']} 天" if task["lag_days"] else ""
            lines.append(
                f"  [{marks.get(task['status'], '?')}] {task['title']}（#{task['id']}）{due}{late}"
            )

    stage_count = len(tree["stages"])
    task_count = sum(len(stage["tasks"]) for stage in tree["stages"])
    summary = f"读了当前计划：{stage_count} 个阶段、{task_count} 个任务"
    if lag["behind"]:
        summary += f"，落后 {lag['lag_days']} 天"
    return ToolOutcome(_capped(lines, PLAN_CHAR_LIMIT), summary)


def _recent_reports(conn: sqlite3.Connection, plan_id: int) -> ToolOutcome:
    """这个计划下最近几份报告，按时间正序（最新的在最后）。"""
    rows = list(
        reversed(
            conn.execute(
                """SELECT r.status, r.note, r.created_at, n.title
                   FROM report r JOIN plan_node n ON n.id = r.node_id
                   WHERE n.plan_id = ? ORDER BY r.id DESC LIMIT ?""",
                (plan_id, RECENT_REPORTS),
            ).fetchall()
        )
    )
    if not rows:
        return ToolOutcome(
            "【最近报告】还没有报告——所以「最近执行得怎么样」这件事目前没有事实可依。",
            "读了最近报告：一份都还没有",
        )
    lines = [f"【最近 {len(rows)} 份报告】"]
    lines += [
        f"- {str(row['created_at'])[:10]}「{row['title']}」{row['status']}："
        f"{row['note'] or '（没写说明）'}"
        for row in rows
    ]
    latest = rows[-1]
    return ToolOutcome(
        _capped(lines, REPORTS_CHAR_LIMIT),
        f"读了最近 {len(rows)} 份报告（最新一份：{str(latest['created_at'])[:10]}"
        f"「{latest['title']}」{latest['status']}）",
    )


def _profile(conn: sqlite3.Connection, plan_id: int, args: dict[str, Any]) -> ToolOutcome:
    """当前有效的长期档案；给了 `category` 就只看那一类（中文名也认）。"""
    asked = args.get("category")
    wanted = None
    if asked is not None:
        wanted = resolve_category(asked)
        if wanted is None:
            raise ToolError(category_error(asked))
    read = advisor.read_profile(conn)
    items = [item for item in read["items"] if wanted is None or item["category"] == wanted]
    if not items:
        return ToolOutcome(
            "【我的长期档案】这一类现在一条都没有——要靠它才能定的，直接问我。"
            if wanted is not None
            else "【我的长期档案】一条都没有——所以判据主要来自计划事实。",
            "读了长期档案：一条都没有",
        )
    lines = ["【我的长期档案】方括号里是类别（判断要对着它说）："]
    lines += [f"#{item['id']} [{item['category']}] {item['content']}" for item in items]
    if wanted is None and read["missing_categories"]:
        names = "、".join(
            advisor.PROFILE_CATEGORIES[name] for name in read["missing_categories"]
        )
        lines.append(f"（这几类还空着：{names}——要靠它们才能定的，问我。）")
    return ToolOutcome(
        _capped(lines, PROFILE_CHAR_LIMIT),
        f"读了长期档案：{len(items)} 条"
        + (f"（只看「{advisor.PROFILE_CATEGORIES[str(wanted)]}」）" if wanted is not None else ""),
    )


def _plan_origin(conn: sqlite3.Connection, plan_id: int) -> ToolOutcome:
    """这个计划是怎么来的：采纳过哪条方向、出蓝图之前聊了什么、蓝图长什么样。

    为什么要给：不给它就只能看见「现在这棵树」，答不了「当初为什么这么排」——
    而这类问题恰恰是执行期最常问的（用户原话：只要是这个计划里面的，都该让它知道）。
    """
    lineage = _lineage(conn, plan_id)
    blueprint = _blueprints(conn, plan_id)
    if len(lineage) == 1 and len(blueprint) == 1:
        # 两段都说「没有记录」：老计划，不是从采纳候选/蓝图来的。别让它以为读失败了。
        summary = "读了计划来历：没有记录（不是从采纳候选或蓝图来的）"
    else:
        summary = "读了计划来历：采纳的方向、出蓝图前的对话与蓝图全貌"
    lines = [*lineage, "", *blueprint]
    return ToolOutcome(_capped(lines, LINEAGE_CHAR_LIMIT + BLUEPRINT_CHAR_LIMIT), summary)


def _memories(conn: sqlite3.Connection, plan_id: int) -> ToolOutcome:
    """当前有效的长期记忆，分两级列；**到复核时间的摘出去单独标**（方案第 5 节）。

    为什么到期的还列出来：不列，模型会以为那条事实不存在；列成「当前事实」，它会把一句
    可能早就过期的话当成判据。单独标一句「这条已到复核时间，先别当依据」是唯一诚实的做法。
    """
    read = memory.read_memories(conn, plan_id)
    lines = ["【我的长期记忆】方括号里是类别，末尾是来源与事实时间："]
    if read["global"]:
        lines.append("- 全局：")
        lines += [_memory_line(item) for item in read["global"]]
    else:
        lines.append("- 全局：一条都没有。")
    if read["plan"]:
        lines.append("- 这个计划里的：")
        lines += [_memory_line(item) for item in read["plan"]]
    else:
        lines.append("- 这个计划里的：一条都没有。")
    if read["due"]:
        lines.append("")
        lines.append(f"【已到复核时间、这次先别当依据的 {read['due_count']} 条】")
        lines += [
            f"（{item['scope_label']}）「{item['content']}」——到 {str(item['review_at'])[:10]} 该复核了，"
            "要用它先问我一句"
            for item in read["due"]
        ]
    summary = f"读了长期记忆：全局 {len(read['global'])} 条、这个计划 {len(read['plan'])} 条"
    if read["due_count"]:
        summary += f"（另 {read['due_count']} 条已到复核时间，没算作依据）"
    return ToolOutcome(_capped(lines, MEMORY_CHAR_LIMIT), summary)


def _memory_line(item: dict[str, Any]) -> str:
    label = item["category_label"] or item["kind_label"] or ""
    stamp = str(item["fact_time"])[:10] if item["fact_time"] else "时间不明"
    return f"#{item['id']} [{label}] {item['content']}｜{item['source_kind_label']}｜{stamp}"


def _experiences(
    conn: sqlite3.Connection, plan_id: int, args: dict[str, Any]
) -> ToolOutcome:
    """检索这个计划过去的经历：按关键词、来源类型、起始时间筛，最多 8 条（最新在前）。"""
    keyword = args.get("keyword")
    source_type = args.get("source_type")
    since = args.get("since")
    try:
        found = memory.search_experiences(
            conn, plan_id, keyword=keyword, source_type=source_type, since=since
        )
    except memory.MemoryError as error:
        raise ToolError(str(error)) from None

    filters: list[str] = []
    if keyword:
        filters.append("带关键词")
    if found["source_type"]:
        filters.append(memory.EXPERIENCE_SOURCES[str(found["source_type"])])
    if since:
        filters.append(f"{str(since)[:10]} 之后")
    label = "、".join(filters) if filters else "全部"
    if not found["items"]:
        return ToolOutcome(
            f"【这个计划过去的经历】{label}：一条都没找到。",
            f"检索了经历（{label}）：一条都没有",
        )
    lines = [f"【这个计划过去的经历】{label}，最新的在前（每行末尾是来源编号）："]
    lines += [
        f"- {item['date']} [{item['source_label']}#{item['source_id']}] {item['excerpt']}"
        for item in found["items"]
    ]
    if found["matched"] > found["returned"]:
        lines.append(
            f"（还有 {found['matched'] - found['returned']} 条没列出来，"
            "说一句「更早的那些」我就接着往上翻。）"
        )
    return ToolOutcome(
        _capped(lines, EXPERIENCE_CHAR_LIMIT),
        f"检索了经历（{label}）：命中 {found['matched']} 条，列了 {found['returned']} 条",
    )


TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        Tool(
            name="read_current_plan",
            summary="这个计划的目标、状态、落后情况，以及全部阶段与任务（含 #编号与截止日）。"
            "回答「下一步先做什么」「卡在哪」这类问题之前先读它。",
            args="",
            arg_names=(),
            run=lambda conn, plan_id, args: _plan_facts(conn, plan_id),
        ),
        Tool(
            name="read_recent_reports",
            summary="这个计划最近的报告（最多 5 份）：时间、任务、状态与说明。"
            "要谈「最近执行得怎么样」「为什么没推进」时读它。",
            args="",
            arg_names=(),
            run=lambda conn, plan_id, args: _recent_reports(conn, plan_id),
        ),
        Tool(
            name="read_profile",
            summary="我的长期档案（当前有效值）：生活习惯、生活记录、当前状态、短期目标、长期主线。"
            "判断要对着改档案、或要结合我的精力与时间说话时读它。",
            args='可选 {"category": "当前状态"}——只看某一类时给，写中文名即可'
            f"（{' / '.join(PROFILE_CATEGORY_SHORT_NAMES.values())}）；不给就是全部。",
            arg_names=("category",),
            run=_profile,
        ),
        Tool(
            name="read_plan_origin",
            summary="这个计划的来历：采纳过哪条候选、出蓝图之前聊过什么、蓝图里每个阶段的理由、"
            "以及当时哪些没被勾中所以没建。回答「当初为什么这么排」时读它。",
            args="",
            arg_names=(),
            run=lambda conn, plan_id, args: _plan_origin(conn, plan_id),
        ),
        Tool(
            name="read_memories",
            summary="已经记住的长期记忆（全局的 + 这个计划里的；到复核时间的会单独标出来、"
            "不算依据）。他说「我不是说过吗」「按我的习惯来」这类话之前先读它。",
            args="",
            arg_names=(),
            run=lambda conn, plan_id, args: _memories(conn, plan_id),
        ),
        Tool(
            name="search_experiences",
            summary="翻这个计划过去的经历：我们聊过什么、报告里写过什么、哪些方向被采纳过或"
            "否决过、提案怎么裁的、节点字段为什么改过。回答「以前为什么这么定」时读它。",
            args='可选 {"keyword": "关键词", "source_type": "计划对话", "since": "2026-09-01"}'
            "——三个都可省；source_type 只认 " + " / ".join(memory.EXPERIENCE_SOURCES.values()),
            arg_names=("keyword", "source_type", "since"),
            hidden_args=("keyword",),
            run=_experiences,
        ),
    )
}


# ---------- 目录与执行 ----------

def catalog_text() -> str:
    """写进 prompt 的工具目录（由 `TOOLS` 生成，和执行永远一致）。"""
    lines = ["【你能读的资料】你不预先拿到任何业务数据，需要什么就自己读——一次可以要好几样："]
    for tool in TOOLS.values():
        args = f"　参数：{tool.args}" if tool.args else ""
        lines.append(f"- {tool.name}：{tool.summary}{args}")
    lines.append(
        "读的时候只输出 "
        '{"tool_calls": [{"name": "read_current_plan", "args": {}}]}'
        "；要一次读好几样就在数组里给好几条。"
    )
    return "\n".join(lines)


def execute(
    conn: sqlite3.Connection, plan_id: int, name: Any, args: Any
) -> ToolOutcome:
    """执行一次工具调用。名字不认识、参数非法一律抛 `ToolError`（原文回给模型纠正）。"""
    tool = TOOLS.get(str(name))
    if tool is None:
        raise ToolError(
            f"没有叫「{name}」的工具；只能读这几个：{' / '.join(TOOLS)}"
        )
    if not isinstance(args, dict):
        raise ToolError(f"{tool.name} 的参数必须是一个 JSON 对象（没有参数就给 {{}}）")
    unknown = [key for key in args if key not in tool.arg_names]
    if unknown:
        raise ToolError(
            f"{tool.name} 不收参数「{'、'.join(str(key) for key in unknown)}」"
            + ("；它不接参数" if not tool.arg_names else f"；它只认 {' / '.join(tool.arg_names)}")
            + "。计划编号由系统指定，不用你给"
        )
    return tool.run(conn, plan_id, args)


# ---------- 内部：裁剪与两段来历 ----------

def _capped(lines: list[str], limit: int) -> str:
    """按字符上限拼装：从最旧的往下砍，砍掉了就明说一句（不假装资料只有这么多）。"""
    kept: list[str] = []
    used = 0
    dropped = False
    for line in lines:
        if kept and used + len(line) > limit:
            dropped = True
            break
        kept.append(line)
        used += len(line) + 1
    if dropped:
        kept.append("（这段资料太长，只列到这里——要更早的那些就说一声。）")
    return "\n".join(kept)


def _lineage(conn: sqlite3.Connection, plan_id: int) -> list[str]:
    """这个计划是怎么来的：采纳过哪条方向、出蓝图之前聊了什么。

    数据在 `plan_chat` 里（定方向那段对话）。
    """
    rows = conn.execute(
        "SELECT DISTINCT candidate_id FROM plan_chat WHERE plan_id = ? ORDER BY candidate_id",
        (plan_id,),
    ).fetchall()
    if not rows:
        return ["【这条方向的来历】没有记录（这个计划不是从采纳一条候选来的，或是更早建的）"]

    lines = ["【这条方向的来历】"]
    for row in rows:
        candidate = conn.execute(
            "SELECT id, title, why, depth_target, status FROM candidate WHERE id = ?",
            (row["candidate_id"],),
        ).fetchone()
        if candidate is None:
            continue
        lines.append(
            f"- 采纳的方向：{candidate['title']}（当时给的理由：{candidate['why']}；"
            f"建议深度 {candidate['depth_target']}）"
        )
        said: list[str] = []
        for message in conn.execute(
            "SELECT role, content FROM plan_chat WHERE plan_id = ? AND candidate_id = ? ORDER BY id",
            (plan_id, row["candidate_id"]),
        ):
            text_of = (
                blueprint_mod.render_reply(str(message["content"]))
                if str(message["role"]) == "assistant"
                else str(message["content"])
            )
            said.append(f"  {'它' if message['role'] == 'assistant' else '我'}：{text_of}")
        kept, used = [], 0
        for line in reversed(said):  # 从最新往回收，收不下就丢更早的
            if kept and used + len(line) > LINEAGE_CHAR_LIMIT:
                break
            kept.insert(0, line)
            used += len(line)
        lines += kept or ["  （那段对话没有记录）"]
    return lines


def _blueprints(conn: sqlite3.Connection, plan_id: int) -> list[str]:
    """这个计划的蓝图：最近的待裁定那版，与已经建进计划的那版。

    除了阶段与任务本身，还标出**哪些当时没被勾中、因此没建**——那是「为什么计划里
    没有这一块」的答案。更早的版本不铺开（都被顶掉了），只报个数。
    """
    rows = conn.execute(
        "SELECT * FROM proposal WHERE kind = ? ORDER BY id DESC",
        (blueprint_mod.BLUEPRINT_KIND,),
    ).fetchall()
    mine = [
        row for row in rows if int(blueprint_mod.payload_of(row).get("plan_id") or 0) == int(plan_id)
    ]
    if not mine:
        return ["【蓝图】没有记录（这个计划不是从蓝图建起来的，或是更早建的）"]

    taken = {
        str(stage["title"]): {str(task["title"]) for task in stage["tasks"]}
        for stage in plan.plan_tree(conn, plan_id)["stages"]
    }
    wanted: list[str] = []
    for row in mine:
        status = str(row["status"])
        if status == "superseded":
            continue  # 被新版顶掉的版本不值一提，下面只报个数
        if status == "pending":
            label = "待你勾选的那一版"
        elif status == "accepted":
            label = "已经建进计划的这一版"
        else:
            label = f"（{status}）"
        payload = blueprint_mod.payload_of(row)
        block = [f"- {label}：目标「{payload.get('goal')}」"]
        for stage in payload.get("stages") or []:
            title = str(stage.get("title"))
            built = title in taken
            block.append(f"  · 阶段「{title}」{'（已建）' if built else '（当时没勾，没建）'}")
            block.append(f"    要交的东西：{stage.get('deliverable')}")
            if stage.get("why"):
                block.append(f"    为什么先做它：{stage['why']}")
            for task in stage.get("tasks") or []:
                task_title = str(task.get("title"))
                done = task_title in taken.get(title, set())
                due = f"｜截止 {task['due_date']}" if task.get("due_date") else ""
                block.append(f"    - {task_title}{due}{'' if done else '（当时没勾，没建）'}")
                if len(chr(10).join(block)) > BLUEPRINT_CHAR_LIMIT:
                    break
        wanted += block
        if len(chr(10).join(wanted)) > BLUEPRINT_CHAR_LIMIT:
            wanted.append("  （这版太长，只列到这里）")
            break
    dropped = [row for row in mine if str(row["status"]) == "superseded"]
    head = ["【蓝图】"]
    if dropped:
        head.append(f"（另有 {len(dropped)} 版更早的已被新版顶掉，不再列出）")
    return head + wanted


def summarize_args(args: Any, hidden: tuple[str, ...] = ()) -> str:
    """参数摘要——进 `agent_run.tools`，给人看它这一轮问了什么。

    `hidden` 里的键**只记「给了」不记值**：检索经历的关键词可能是一句私事，
    而运行账是给人翻的日志，不该成为第二个存它的地方。
    """
    if not args:
        return ""
    safe = (
        {key: ("（已隐去）" if key in hidden else value) for key, value in args.items()}
        if hidden
        else args
    )
    try:
        return json.dumps(safe, ensure_ascii=False)[:120]
    except (TypeError, ValueError):  # 理论上到不了：args 来自解析过的 JSON
        return str(safe)[:120]


def summarize_call(name: Any, args: Any) -> str:
    """按工具名取它的隐藏参数清单再摘要——调用方（`agent_runtime`）不必知道哪个键要隐去。"""
    tool = TOOLS.get(str(name))
    return summarize_args(args, tool.hidden_args if tool else ())