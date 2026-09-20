"""计划对话里的「一条可执行建议」（SPEC 决策 39，2026-09-18 用户拍板）。

为什么要有这个模块：决策 37 原先写着「计划对话只说话、不接写入口」——那时的取舍是
节点字段**还没有**写入口（T30 之前），接上去也无从执行。T30 把原地改字段建好之后，
用户拍板接上；但接的方式是**建议 + 当场裁定**，不是让对话直接写库：

- 它每轮最多提**一条**建议（信封里的单个对象或 null，形状上就排除了多条）；
- 一条建议落一条 `kind=plan_change` 的待裁定提案，界面上那条「确认」就是裁定它；
- **批准**才真的动手：改的走 `plan.update_node_fields`（原地改、**id 不变**、台账一条
  流水），加的走 `plan.add_node`（**同一道防重名闸**）；
- **忽略＝当场驳回**，理由由界面固定送「聊天里先不动」——每条建议都有归宿。

**「加」的那两类一次可以加一小批**（2026-09-19 用户走查后放宽）：真实使用里「建一个阶段」
几乎总是连着它下面的几件任务，一次只让加一件会把人逼成连点五次确认，还会让模型拿「规矩」
回嘴。所以一条建议里可以给 `tasks`（最多 `MAX_TASKS` 件）：加阶段＝连着它下面的任务一起建，
加任务＝往已有的那个阶段下一次补几件。**上限仍在**：一轮一条建议、一条建议最多几件任务，
防的是「一次改半个计划」这种没法复核的大动作。

**三类动作，没有第四类**。删节点永远不做：计划里表达「这块不做了」的方式是打勾 /
跳过 / 收尾，删掉会把报告与交付物的痕迹一起断掉。打勾、跳过、交交付物也不归它管——
那些是执行动作，人自己点。

**验收放在落提案之前**（`check`）：一条**批不了**的提案不该落进库。所以在建议成形那一刻
就把「节点存不存在、属不属于这个计划、字段能不能改、改前改后是不是真不一样、名字撞不撞
已经开着的那条」全查一遍——不合格＝带原因重试一次，而不是等你点了「确认」才报错。
`check` 是只读的；`resolve` 在批准那一刻**再查一遍**（计划可能已经变了），仍然只读；
`apply` 才是那唯一一次写。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from . import plan

# 落进 proposal.kind 的取值（决策 28 的第四类）
KIND = "plan_change"

# 三类动作。没有「删除」：见模块说明最后一段。
UPDATE_NODE = "update_node"
ADD_TASK = "add_task"
ADD_STAGE = "add_stage"
ACTIONS = (UPDATE_NODE, ADD_TASK, ADD_STAGE)

# 一条建议里最多带几件任务（2026-09-19 放宽「一次一件」时定的上限）。
# 为什么是这个数：一个阶段拆下来通常是三到五件，再多就该先聊清楚而不是一口气排完；
# 而且确认条与待裁定页要一眼看得完，超过就得滚动，复核成本立刻上去。
MAX_TASKS = 5

# 能改的字段与它们的人话名字（与 T30 的三个字段、决策 38 一致）。
FIELD_LABELS: dict[str, str] = {
    "title": "标题",
    "deliverable": "要交的东西",
    "due_date": "截止日",
}


class PlanChangeError(RuntimeError):
    """建议本身站不住：节点不存在、字段不能改、改前＝改后、名字撞车……"""


class PlanChangeNotFound(PlanChangeError):
    """目标计划 / 节点在提案提出之后没了 → 接口层翻成 404。"""


class PlanChangeConflict(PlanChangeError):
    """与现状冲突（计划已不是进行中）→ 接口层翻成 409。"""


class Suggestion(BaseModel):
    """模型提的一条建议。

    **形状宽松、语义严格**：形状错由 Pydantic 报（缺 why、fields 不是对象、node_id
    不是整数……），语义错由 `check` 报（节点不存在、改前＝改后、重名……）。两种都算
    **不合格**——都会带原因重试一次，而不是等你点了「确认」才发现这条批不了。
    """

    action: str = Field(min_length=1)
    # update_node 要改哪个节点 / add_task 挂在哪个阶段下；add_stage 不用
    node_id: int | None = None
    # update_node：字段名 → 新值
    fields: dict[str, Any] = Field(default_factory=dict)
    # add_task：{title, due_date}——**老形状，仍然收**（库里可能还有按它落的待裁定提案）
    task: dict[str, Any] = Field(default_factory=dict)
    # add_task / add_stage：一次要加的一批任务 [{title, due_date}]，最多 MAX_TASKS 件
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    # add_stage：{title, deliverable, why}
    stage: dict[str, Any] = Field(default_factory=dict)
    why: str = Field(min_length=1)


def _wanted_tasks(suggestion: Suggestion) -> tuple[list[dict[str, Any]], str | None]:
    """把这一条建议里的任务收成一个列表：`tasks` 优先，老形状 `task` 兜底。

    为什么两种都收：`task` 是 T31 第一版的形状，库里可能还有按它落的待裁定提案
    （批准时才会走 `resolve`），换个字段名就让那些提案批不了，不值当。
    """
    raw = [item for item in (suggestion.tasks or []) if isinstance(item, dict)]
    if not raw and suggestion.task:
        raw = [suggestion.task]
    return raw, None


# ---------- 验收：一条建议能不能变成提案 ----------

def check(
    conn: sqlite3.Connection, plan_id: int, suggestion: Suggestion
) -> tuple[dict[str, Any] | None, str | None]:
    """验收一条建议，通过就给出**要落库的 payload**（含给界面用的人话 summary）。

    为什么不合格就整体判不合格、而不是「丢掉坏的那部分」：悄悄丢一个字段等于把你以为
    排好的改动吞了；让它带原因重说一次，成本是一句 prompt 的钱。
    """
    why = str(suggestion.why or "").strip()
    if not why:
        return None, "why 是空的——建议必须说清为什么该改"

    row = plan.resolve_plan(conn, plan_id)
    if row is None:
        return None, f"计划 id={plan_id} 不存在"

    if suggestion.action == UPDATE_NODE:
        return _check_update(conn, plan_id, suggestion, why)
    if suggestion.action == ADD_TASK:
        return _check_add_task(conn, plan_id, suggestion, why)
    if suggestion.action == ADD_STAGE:
        return _check_add_stage(conn, plan_id, suggestion, why)
    return None, (
        f"action「{suggestion.action}」不在能提的三类里"
        f"（{' / '.join(ACTIONS)}）——它不能删节点、不能替你打勾/跳过/交交付物"
    )


def _owned_node(conn: sqlite3.Connection, plan_id: int, node_id: Any) -> tuple[sqlite3.Row | None, str | None]:
    """取一个属于这个计划的节点；不存在 / 不属于它都给出中文原因。"""
    if node_id is None:
        return None, "没给出 node_id"
    node = plan.get_node(conn, int(node_id))
    if node is None:
        return None, f"点名的节点 id={node_id} 不存在——照着上面事实里的编号写"
    if int(node["plan_id"]) != int(plan_id):
        return None, f"节点 #{node_id} 不属于计划 #{plan_id}——它只能动这一个计划里的东西"
    return node, None


def _check_update(
    conn: sqlite3.Connection, plan_id: int, suggestion: Suggestion, why: str
) -> tuple[dict[str, Any] | None, str | None]:
    node, problem = _owned_node(conn, plan_id, suggestion.node_id)
    if problem is not None:
        return None, f"update_node 要改哪一个？{problem}"
    if not suggestion.fields:
        return None, "update_node 得给出 fields（改哪个字段、改成什么）"
    unknown = sorted(set(suggestion.fields) - set(FIELD_LABELS))
    if unknown:
        return None, (
            f"fields 里有改不了的字段 {unknown}——只能改 {' / '.join(FIELD_LABELS)}"
        )

    wanted: dict[str, Any] = {}
    for name, raw in suggestion.fields.items():
        if raw is None:
            continue  # 没给 = 不改（同 T30：只认传了的字段）
        text = str(raw).strip()
        if name == "title":
            if not text:
                return None, "标题不能改成空的——想「不要它了」就在计划表里跳过它"
            wanted["title"] = text
        elif name == "deliverable":
            if node["level"] != "stage":
                return None, (
                    f"要交的东西只属于阶段，#{node['id']} 是 {node['level']}——"
                    "任务上的产出用报告说明"
                )
            wanted["deliverable"] = text or None
        else:
            if not text:
                wanted["due_date"] = None  # 空字符串 = 清掉它（同 T30）
            else:
                parsed = plan.parse_date(text)
                if parsed is None:
                    return None, (
                        f"截止日「{text}」不是日期——写成 YYYY-MM-DD，或者给空字符串清掉它"
                    )
                wanted["due_date"] = parsed.isoformat()
    if not wanted:
        return None, "fields 里没有一样真给出来的改动"

    before = {name: node[name] for name in wanted}
    changed = {name: value for name, value in wanted.items() if before[name] != value}
    if not changed:
        return None, (
            "这几项和现在一模一样（改前＝改后）——要么提一个真不一样的改法，要么就别提"
        )

    payload = {
        "plan_id": int(plan_id),
        "action": UPDATE_NODE,
        "node_id": int(node["id"]),
        "node_title": str(node["title"]),
        "level": str(node["level"]),
        "fields": changed,
        "before": {name: before[name] for name in changed},
        "why": why,
    }
    payload["summary"] = summary_of(payload)
    return payload, None


def _clean_tasks(
    conn: sqlite3.Connection,
    plan_id: int,
    raw: list[dict[str, Any]],
    *,
    parent_id: int | None,
    where: str,
    allow_empty: bool = False,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """把一批任务洗成规范形状（每件 `{title, due_date}`），不合格就整体不合格。

    `parent_id` 给了就顺带查「那个阶段下有没有同名的开着」；`None`（往新阶段里放）
    只查这一批内部有没有自己撞自己——新阶段还没建，没有历史节点可撞。

    `allow_empty` 只有加阶段时为真：先建一个还没拆任务的阶段是正当的（他就是想先占个位置），
    而加任务那一类**任务就是它本身**，空的就是没提。
    """
    if not raw:
        if allow_empty:
            return [], None
        return None, f"{where}要给出要加的任务（tasks 里至少写一件）"
    if len(raw) > MAX_TASKS:
        return None, (
            f"{where}一次最多加 {MAX_TASKS} 件任务，这条给了 {len(raw)} 件——"
            "把最要紧的几件先排上，剩下的下一轮再说"
        )

    cleaned: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    for item in raw:
        title = str((item or {}).get("title") or "").strip()
        if not title:
            return None, f"{where}里有一件没写 title（这件任务叫什么）"
        # 同一批里自己撞自己：只比去空白 + 小写（与 plan 那道闸同一个口径）
        key = title.lower()
        if key in seen:
            return None, (
                f"{where}里有两件任务重名（「{seen[key]}」与「{title}」）——"
                "留下一件，或者把其中一件改个说得清区别的名字"
            )
        seen[key] = title
        due, problem = _due_of((item or {}).get("due_date"))
        if problem is not None:
            return None, f"新任务「{title}」的{problem}"
        if parent_id is not None:
            duplicate = plan.find_open_duplicate(
                conn, int(plan_id), "task", title, parent_id=int(parent_id)
            )
            if duplicate is not None:
                return None, (
                    f"这个阶段下已经开着同名任务「{title}」（#{duplicate['id']}）——"
                    "换个名字，或者先在计划表里把那条处理掉"
                )
        cleaned.append({"title": title, "due_date": due})
    return cleaned, None


def _check_add_task(
    conn: sqlite3.Connection, plan_id: int, suggestion: Suggestion, why: str
) -> tuple[dict[str, Any] | None, str | None]:
    stage, problem = _owned_node(conn, plan_id, suggestion.node_id)
    if problem is not None:
        return None, f"add_task 要挂在哪个阶段下？{problem}"
    if stage["level"] != "stage":
        return None, (
            f"任务只能挂在阶段下，#{stage['id']} 是 {stage['level']}——"
            "要加任务就点名它所属的那个阶段"
        )

    raw, _ = _wanted_tasks(suggestion)
    tasks, problem = _clean_tasks(
        conn, plan_id, raw, parent_id=int(stage["id"]), where="add_task"
    )
    if problem is not None:
        return None, problem

    payload = {
        "plan_id": int(plan_id),
        "action": ADD_TASK,
        "stage_id": int(stage["id"]),
        "stage_title": str(stage["title"]),
        "tasks": tasks,
        "why": why,
    }
    payload["summary"] = summary_of(payload)
    return payload, None


def _check_add_stage(
    conn: sqlite3.Connection, plan_id: int, suggestion: Suggestion, why: str
) -> tuple[dict[str, Any] | None, str | None]:
    title = str(suggestion.stage.get("title") or "").strip()
    if not title:
        return None, "add_stage 的 stage 里要有 title（这个阶段叫什么）"
    deliverable = str(suggestion.stage.get("deliverable") or "").strip()
    if not deliverable:
        return None, (
            f"阶段「{title}」没写「要交的东西」——阶段必须带一句能验收的交付物"
            "（例如「写完一个能跑通的 CRUD 接口」）"
        )
    duplicate = plan.find_open_duplicate(conn, int(plan_id), "stage", title)
    if duplicate is not None:
        return None, (
            f"这个计划里已经开着同名阶段「{title}」（#{duplicate['id']}）——"
            "要给它排任务就点名那个阶段，别再加一个同名的"
        )

    raw, _ = _wanted_tasks(suggestion)
    tasks, problem = _clean_tasks(
        conn, plan_id, raw, parent_id=None, where="add_stage", allow_empty=True
    )
    if problem is not None:
        return None, problem

    payload = {
        "plan_id": int(plan_id),
        "action": ADD_STAGE,
        "stage": {
            "title": title,
            "deliverable": deliverable,
            "why": str(suggestion.stage.get("why") or "").strip(),
        },
        "tasks": tasks,
        "why": why,
    }
    payload["summary"] = summary_of(payload)
    return payload, None


def _due_of(raw: Any) -> tuple[str | None, str | None]:
    """新节点的截止日：不给就不给（不带日期不进落后量，决策 31），给了就必须真是日期。"""
    text = str(raw or "").strip()
    if not text:
        return None, None
    parsed = plan.parse_date(text)
    if parsed is None:
        return None, f"截止日「{text}」不是日期——写成 YYYY-MM-DD，拿不准就别给这个字段"
    return parsed.isoformat(), None


# ---------- 给界面看的那一行 ----------

def summary_of(payload: dict[str, Any]) -> str:
    """把 payload 拼成一行人话，界面直接当确认条渲染。

    为什么在后端拼：界面拿原始字段拼中文，等于把「截止日」这类词表再抄一遍，
    改一处漏一处；而且这一行还要进台账理由，必须只有一处说了算。
    """
    action = str(payload.get("action") or "")
    if action == UPDATE_NODE:
        node_title = payload.get("node_title")
        fields = payload.get("fields") or {}
        before = payload.get("before") or {}
        names = [name for name in fields if name in FIELD_LABELS]
        labels = "、".join(FIELD_LABELS[name] for name in names)
        if len(names) == 1:
            name = names[0]
            return (
                f"改「{node_title}」的{labels}："
                f"{_show(before.get(name), name)} → {_show(fields[name], name)}"
            )
        parts = "；".join(
            f"{FIELD_LABELS[name]} {_show(before.get(name), name)} → {_show(fields[name], name)}"
            for name in names
        )
        return f"改「{node_title}」的{labels}：{parts}"
    if action == ADD_TASK:
        tasks = _tasks_in(payload)
        if not tasks:
            return f"加一件任务（挂在「{payload.get('stage_title')}」下）"
        if len(tasks) == 1:
            due = f"｜截止 {tasks[0]['due_date']}" if tasks[0].get("due_date") else ""
            return (
                f"加一件任务：{tasks[0]['title']}"
                f"（挂在「{payload.get('stage_title')}」下）{due}"
            )
        return (
            f"加 {len(tasks)} 件任务（挂在「{payload.get('stage_title')}」下）："
            + "；".join(f"{item['title']}{_due_suffix(item)}" for item in tasks)
        )
    if action == ADD_STAGE:
        stage = payload.get("stage") or {}
        tasks = _tasks_in(payload)
        head = f"加一个阶段：{stage.get('title')}（排最后）"
        if tasks:
            head += f"｜含 {len(tasks)} 件任务：" + "；".join(
                f"{item['title']}{_due_suffix(item)}" for item in tasks
            )
        return f"{head}｜要交的东西：{stage.get('deliverable')}"
    return "计划改动建议"


def _tasks_in(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """payload 里的任务列表：`tasks` 优先，老形状 `task` 兜底（两边都认）。"""
    tasks = [item for item in (payload.get("tasks") or []) if isinstance(item, dict)]
    if not tasks and payload.get("task"):
        tasks = [payload["task"]]
    return tasks


def _due_suffix(task: dict[str, Any]) -> str:
    return f"（截止 {task['due_date']}）" if task.get("due_date") else ""


def _show(value: Any, name: str) -> str:
    text = "" if value is None else str(value)
    if not text:
        return "（空）"
    return text if name == "due_date" else f"「{text}」"


# ---------- 批准那一刻：先只读地查完，再动手 ----------

@dataclass
class PendingNode:
    """批准时要建的一个节点。`tasks` 那批跟着阶段一起排队，先后顺序就是列表顺序。"""

    level: str
    title: str
    parent_id: int | None = None
    deliverable: str | None = None
    due_date: str | None = None


@dataclass
class ChangePlan:
    """一条建议落到计划里的样子。`resolve` 产出它，`apply` 执行它。

    加的这两类不再是「一个节点」，而是**一串要建的节点**：加阶段＝阶段本身 + 它下面
    的任务，加任务＝一批任务。`nodes` 的顺序就是建它们的顺序（阶段必须先建出来，
    下面的任务才挂得上）。
    """

    plan_id: int
    action: str
    why: str
    node_id: int | None = None
    fields: dict[str, Any] = field(default_factory=dict)
    nodes: list[PendingNode] = field(default_factory=list)


def resolve(conn: sqlite3.Connection, payload: dict[str, Any]) -> ChangePlan:
    """只读地把提案解析成「真要做什么」，并把冲突全查完。

    为什么批准前还要再查一遍：提案是之前那一轮落的，中间你完全可能在计划表里把节点
    处理掉了。台账每个操作各自提交、没有请求级事务——验不过就**一条都不写**，
    提案保持 `pending` 可重裁（与蓝图建树同一条纪律）。
    """
    plan_id = int(payload.get("plan_id") or 0)
    row = plan.resolve_plan(conn, plan_id)
    if row is None:
        raise PlanChangeNotFound(f"这条提案说的计划 id={plan_id} 已不存在")
    action = str(payload.get("action") or "")
    why = str(payload.get("why") or "").strip() or "计划对话里聊出的一条建议"

    if action == UPDATE_NODE:
        node_id = int(payload.get("node_id") or 0)
        node = plan.get_node(conn, node_id)
        if node is None:
            raise PlanChangeNotFound(f"要改的节点 id={node_id} 已不存在")
        if int(node["plan_id"]) != plan_id:
            raise PlanChangeConflict(
                f"节点 #{node_id} 现在不属于计划 #{plan_id}——先核对一下"
            )
        fields = {
            name: value
            for name, value in (payload.get("fields") or {}).items()
            if name in FIELD_LABELS
        }
        if not fields:
            raise PlanChangeError("这条提案里没有要改的字段")
        return ChangePlan(
            plan_id=plan_id, action=action, why=why, node_id=node_id, fields=fields
        )

    if action in (ADD_TASK, ADD_STAGE):
        # 往计划里「加东西」要求它是进行中（与蓝图建树同一道门）。改字段那类**不按状态拦**：
        # 改字段不是生命周期事件（决策 38），暂停/收尾的计划里修一句交付物是正当的。
        if str(row["status"]) != "active":
            raise PlanChangeConflict(
                f"计划 #{plan_id} 已不是进行中（{row['status']}）——往计划里加东西要求它先"
                "回到进行中：把它「继续做」，再批准这条"
            )

    if action == ADD_TASK:
        stage_id = int(payload.get("stage_id") or 0)
        stage = plan.get_node(conn, stage_id)
        if stage is None:
            raise PlanChangeNotFound(f"要挂任务的那个阶段 id={stage_id} 已不存在")
        if stage["level"] != "stage":
            raise PlanChangeError(f"节点 #{stage_id} 不是阶段，任务挂不上去")
        if int(stage["plan_id"]) != plan_id:
            raise PlanChangeConflict(f"阶段 #{stage_id} 现在不属于计划 #{plan_id}")
        tasks = _tasks_in(payload)
        if not tasks:
            raise PlanChangeError("这条提案里没有任务名")
        nodes = []
        for item in tasks:
            title = str(item.get("title") or "").strip()
            if not title:
                raise PlanChangeError("这条提案里有一件任务没写名字")
            # 防重名闸在 add_node 里还有一道；这里先查一次，好给出「撞上哪一条」的中文原因
            plan.assert_no_open_duplicate(conn, plan_id, "task", title, parent_id=stage_id)
            nodes.append(
                PendingNode(
                    level="task",
                    title=title,
                    parent_id=stage_id,
                    due_date=item.get("due_date"),
                )
            )
        return ChangePlan(plan_id=plan_id, action=action, why=why, nodes=nodes)

    if action == ADD_STAGE:
        stage = payload.get("stage") or {}
        title = str(stage.get("title") or "").strip()
        deliverable = str(stage.get("deliverable") or "").strip()
        if not title:
            raise PlanChangeError("这条提案里没有阶段名")
        if not deliverable:
            raise PlanChangeError(f"阶段「{title}」没有「要交的东西」，阶段必须带交付物")
        plan.assert_no_open_duplicate(conn, plan_id, "stage", title)
        # 阶段先排队，它的任务跟着（下面的任务挂得上，靠的就是这个先后）
        nodes = [PendingNode(level="stage", title=title, deliverable=deliverable)]
        for item in _tasks_in(payload):
            task_title = str(item.get("title") or "").strip()
            if not task_title:
                raise PlanChangeError("这条提案里有一件任务没写名字")
            nodes.append(
                PendingNode(
                    level="task",
                    title=task_title,
                    due_date=item.get("due_date"),
                )
            )
        return ChangePlan(plan_id=plan_id, action=action, why=why, nodes=nodes)

    raise PlanChangeError(f"这条提案的 action「{action}」看不懂，先驳回它")


def apply(conn: sqlite3.Connection, change: ChangePlan) -> dict[str, Any]:
    """真的动手——这是这条链路上唯一一次写。走的两条都是现成的写入口。"""
    reason = f"计划对话里的一条建议，你点了确认：{change.why}"
    if change.action == UPDATE_NODE:
        return plan.update_node_fields(
            conn,
            int(change.node_id or 0),
            reason=reason,
            actor="user",
            **change.fields,
        )

    # 加的这两类：走建节点，同一道防重名闸；新阶段排最后（决策 39 的默认），
    # 它下面的任务按给定顺序排 1..n。一个节点一次 add_node，逐个提交（台账本就没有
    # 请求级事务）；所以 resolve 必须在动手之前把冲突全查完——验不过就一条都不写。
    next_stage_order = max(
        [int(row["sort_order"]) for row in plan.get_stages(conn, change.plan_id)] + [0]
    ) + 1
    created: list[dict[str, Any]] = []
    task_index = 0
    for node in change.nodes:
        parent_id = node.parent_id
        if node.level == "stage":
            sort_order = next_stage_order
            next_stage_order += 1
        else:
            parent_id = parent_id if parent_id is not None else created[0]["id"]
            task_index += 1
            sort_order = task_index
        node_id = plan.add_node(
            conn,
            change.plan_id,
            node.level,
            node.title,
            parent_id=parent_id,
            deliverable=node.deliverable,
            due_date=node.due_date,
            sort_order=sort_order,
            actor="user",
        )
        created.append(
            {
                "id": node_id,
                "level": node.level,
                "title": node.title,
                "parent_id": parent_id,
            }
        )
    return {"nodes": created}