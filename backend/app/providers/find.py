"""「找」的来源 provider：候选清单从哪来。

SPEC 第 18 节决策 4：把「找」的来源抽成 provider 接口，**甲档起步**——不联网、只给
路线层面；初步测试完成后再考虑乙档（联网核验链接）。今天实现的就是甲档：候选完全由
模型依据长期档案给路线建议，不去网上抓任何东西。

为什么现在就把接口抽出来：将来升乙档，换的只是「候选从哪来」，而输出校验、排序、
落库、去重这些纪律不该跟着重写——它们留在 `advisor` 里，这一层只负责
「给我一份输入，还我一组候选」。

这一层刻意**不 import `advisor`**：词表与判据由 `advisor` 组装成 `Brief` 传进来，
避免循环依赖，也让「换一个来源」只需要实现 `Source` 这一个协议。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Brief:
    """交给来源的全部输入。

    为什么不让来源自己去读库：负责判断的代码（`advisor`）已经知道档案长什么样、
    哪些类别空着、哪些标题是禁区；来源只该关心「怎么把这些说给模型听」。
    这样乙档实现（比如先去网上找资料、再让模型挑）能拿到同样的输入。
    """

    raw_text: str
    # "#2 [long_axis] 新主线：通用工程基础" 这种现成行——来源不必知道怎么拼档案
    profile_lines: list[str]
    # 空着的类别（中文名），用于「靠它们的判断不许编」那段提示
    missing_labels: list[str]
    # 这一轮针对的计划（目标 / 当前阶段 / 还开着的任务），已由 advisor 拼成现成行；
    # 为空 = 「新方向（不属于任何计划）」（SPEC 决策 33 ①）
    plan_context_lines: list[str]
    # 最近几轮「找」的流水（时间 / 计划归属 / 候选标题 / 我的表态与否决理由原文），
    # 已由 advisor 按条数与字符上限截好（SPEC 决策 35 ①）
    feedback_lines: list[str]
    # 每档深度主要看哪几类档案，来自 advisor.JUDGE_SOURCES（与四问共用一套判据）
    depth_guide_lines: list[str]
    # 追问槽位的 missing 允许点名的词（五类各自的中文名与英文 token），来自 advisor.CLARIFY_KEYS
    clarify_keys: tuple[str, ...]
    depth_targets: tuple[str, ...]
    kinds: tuple[str, ...]
    min_candidates: int
    max_candidates: int
    # `path` 形状下步骤的条数区间（2026-09-20 T34）。与候选条数分开两个常量：
    # 「一条路上的几步」和「几个互相竞争的方向」不是同一个量级。
    min_steps: int = 2
    max_steps: int = 8
    # 已否决过的标题（归一化前），prompt 里要明确列出来当禁区
    banned_titles: list[str] = field(default_factory=list)
    # 已经答过的追问（「我问…」→「我答…」），由 advisor 按计划归属取好。2026-09-26 走查
    # 整改：已答清单原来只埋在反馈流水里，会被字符上限从最旧挤掉，也压不过「缺信息就追问」
    # 的主指令——追问重复就是这么来的。现在单独成段、当禁区用，并在验收里拦下重问。
    clarified_lines: list[str] = field(default_factory=list)
    conversation_lines: list[str] = field(default_factory=list)


class Source(Protocol):
    """候选来源。甲档是本文件里的 `RouteOnlySource`，乙档将来另写一个。"""

    name: str

    def build_messages(self, brief: Brief) -> list[dict[str, str]]:
        """把输入变成一次 chat 调用的 messages。"""
        ...


SYSTEM_PROMPT = (
    "你是在给一位具体的人提学习方向建议：说话像懂行又关心他的朋友——直接、对症、"
    "讲人话，可以有关照，不用客套也不用条条框框地罗列。"
    "你的建议全部装进一个 JSON 对象返回，除此之外不输出任何字。"
)


class RouteOnlySource:
    """甲档：不联网，只给路线层面。候选来自模型对档案的理解，不做任何外部核验。

    「不联网」的诚实后果：它给你的是一条学习路线，不是一个可点开的链接。
    报告里若需要资料本身，得你自己去找——这不是省事，是这一档的边界。
    """

    name = "route_only"

    def build_messages(self, brief: Brief) -> list[dict[str, str]]:
        # 提示词按职责分四层（2026-09-26 语气整改）：层一「人设与语气」在 SYSTEM_PROMPT；
        # 这里从层二数据开始。改说话口吻只动层一和语气段；层四输出契约与 advisor 的校验
        # 一一咬合（"一字不差" / "第一硬约束" 这些措辞有测试咬住），语气层不许碰它。
        # ---------- 层二：数据（原话 / 档案 / 计划背景 / 判断标尺） ----------
        lines = [
            "下面给你我的档案和这次的问题。**这次的问题是第一位的**：候选必须全部围绕它——"
            "档案是你判断「这些方向对我有没有用、多深合适」的依据，不是选题范围。",
            "",
            "【这次的问题】每一条候选都必须能直接回应它：",
            brief.raw_text.strip(),
            "",
            "【我的长期档案】方括号里是类别，开头的 #数字 是这条档案的 id（id 只能从这里选）：",
        ]
        lines += brief.profile_lines or ["（一条都没有）"]

        if brief.plan_context_lines:
            lines += [
                "",
                "【这一轮针对的计划】这是这次问题的背景，候选要贴得上它：",
            ]
            lines += [f"- {line}" for line in brief.plan_context_lines]

        if brief.missing_labels:
            lines += [
                "",
                "【注意】这几类档案目前是空的："
                + "、".join(brief.missing_labels)
                + "。凡是要靠它们才能判断的，answer 里请如实说「依据不足」并讲清缺哪类信息，"
                "不要拿常理替我做决定。",
            ]

        lines += ["", "【建议深度分四档，各自主要看哪几类档案（与四问判据同一套）】"]
        lines += [f"- {line}" for line in brief.depth_guide_lines]

        # ---------- 层三：纪律（表态流水 / 已答追问禁区；否决禁区清单在契约之后） ----------
        if brief.conversation_lines:
            lines += ["", "【本线程最近的对话】按时间从早到晚；接住我前面说的话，不要只看最初的问题："]
            lines += brief.conversation_lines

        if brief.feedback_lines:
            lines += [
                "",
                "【我最近几轮的「找」与我的表态】否决理由是我原样留下的，它比「否决」两个字有用得多：",
            ]
            lines += brief.feedback_lines
            lines += [
                "别再推我已经否决或已经过期的那几条；采纳过的那条也别再重复推（要推就推它的下一步）。",
                "行尾写着「我问→我答」的，是我已经答过的事实——**别再问第二遍**。",
            ]

        if brief.clarified_lines:
            lines += [
                "",
                "【已答过的追问——禁区】下面是我已经答过的追问和我的原话回答，它们是已知事实："
                "答案直接拿来用，同一件事**换说法也不许再问第二遍**：",
            ]
            lines += [f"- {line}" for line in brief.clarified_lines]

        # ---------- 层一落地：语气段（把理由说成人话；只管怎么说，不管说什么） ----------
        # V-01（2026-09-28）：原「档案编号收进句尾括号」作废——正文（why / reply）一律不写
        # #数字 编号，依据只通过 profile_item_ids 字段表达，界面按字段单独展示。
        lines += [
            "",
            "【推荐理由怎么写】why 是说给「我」听的，不是填表：",
            "- 每条 3–5 句成段，像懂行的朋友给建议：先说这条路对我哪里有用，再说为什么现在",
            "  正合适，可以有一句对症的关照（比如贴合我的精力、伤情或作息），不要写成条款罗列。",
            "- 正文（why / reply）里不写「#数字」这类档案编号——依据只通过 profile_item_ids"
            " 字段表达，",
            "  界面会按字段单独展示依据；编号混进正文对我是噪音。",
            "- 引用我的原话是为了对题，不是固定开头——别每条都「你原话是……」，换着方式接话。",
            "- 语气像人：可以口语，但别堆「赋能 / 抓手 / 闭环」这类词。",
        ]

        # ---------- 层四：输出契约（与 advisor 校验一一咬合；语气层不许碰这里的措辞） ----------
        # 意图出口（2026-09-28 II-01）：先自报这一轮是给候选、问一句还是只是说话；
        # 旧的两条规则——「每轮给满清单」「追问不能替代清单」——已按决策 44 删除。
        lines += [
            "",
            "【硬性要求】",
            "- 先判断这一轮属于哪种**意图**，写进 `intent`，只能三选一：",
            "  · `candidates`：信息足够、我在请求推荐——包括明确说「重新找 / 再来一版」，"
            "也包括回答上一轮追问时信息已经足够、直接给出最终候选（这不算擅自重做）。"
            "**只有我明确要求推荐或重做时才给候选**：寒暄续问、闲聊接话的那一轮不要自行产候选。"
            "只有这一种意图才给候选，下面关于 shape / candidates / steps / recommended_start"
            " 的规则只在它下面生效。",
            "  · `need_info`：问题是真实的，但档案信息不足以给出负责任的推荐——"
            "这一轮**只问一句**关于我的事实（给 `clarify`，规则见下），`candidates` 与 `steps`"
            " 给空数组，**不为凑数硬推**。",
            "  · `chat`：寒暄 / 问候 / 与找方向无关的闲聊，或你认为有了新事实、"
            "原先的「多方向 / 一路径」形态判断站不住了、要先向我解释——用 `reply` 说人话，"
            "不给候选也不给 clarify。解释形态冲突时可带 `shape_change`："
            '`{"from": "directions 或 path", "to": "directions 或 path", "reason": "为什么"}。',
            "- `intent` 为 `need_info` 或 `chat` 时，`reply` 必须写一句给我的话；"
            "`intent` 为 `candidates` 时 `reply` 给空字符串即可。",
            "- **本次原话是第一硬约束**：每条候选都必须直接回应【这次的问题】，"
            "`why` 里要能看出它怎么回应（用上原话里的词）；"
            "跟这次问题无关的方向，哪怕档案显示它再值，也不许混进清单——"
            "想推别的方向，等我自己开一轮「不知道学什么」再说。",
            "- `intent` 为 `candidates` 时，先自己判断这一轮给的是哪种**形状**，"
            "只能选一种，写进 `shape`：",
            "  · `directions`：几条**互相竞争**的方向，每条都能单独采纳或否决。"
            "我的困惑是「不知道往哪走」时用它。",
            "  · `path`：**只有当这几条明显是同一条路上的先后步骤**（缺了前面那步就走不到后面那步）"
            "时才用它——这时只给**一条伞候选**（标题是这条路本身，例如「从零到部署学通 agent 开发」），"
            "先后步骤放进 `steps`。同一条路上的几步**不是**几个互相竞争的方向：摆成几个方向，"
            "我就得一条条单独采纳，而否决其中一步会被当成永久拉黑。",
            f"- `shape` 为 `directions` 时：给 {brief.min_candidates}–{brief.max_candidates} 条候选，"
            "`steps` 给空数组。",
            f"- `shape` 为 `path` 时：`candidates` 只放 1 条伞候选，"
            f"`steps` 给 {brief.min_steps}–{brief.max_steps} 个先后步骤"
            "（每个 `{title, deliverable, why}`，按先做的顺序；`why` 说清这一步为什么必须排在那个位置）。",
            "- **列表顺序就是你排的优先级**（最有价值的放第一个）。",
            f"- 每条的 `kind` 只能是这些之一：{' / '.join(brief.kinds)}"
            "（概念 / 资料 / 项目 / 课程）。",
            f"- 每条的 `depth_target` 只能是这些之一：{' / '.join(brief.depth_targets)}。",
            "- 伞候选也要写 `why` 与 `depth_target`：为什么这条路对**我**有用。",
            "- 每条候选都要给 `profile_item_ids`，写清依据的是上面哪几条档案（写 # 后面的数字），"
            "只能从上面出现过的 id 里选；编一个不存在的 id 会被判为不合格。",
            "- 某一类方向在我档案里确实找不到依据时，`why` 要明说「依据不足」并说清缺哪类信息，"
            "`profile_item_ids` 给空数组。宁可说依据不足，也不许编造依据。",
            "- `intent` 为 `candidates` 时还要给 `recommended_start`：从上面这些候选里挑一条"
            "作为起点，**一字不差**复制它的 `title`（复制错一个字会被判为不合格）；"
            "并给 `start_reason`：为什么先从这个开始，一句话。",
            "- `clarify`（追问槽位）：`intent` 为 `need_info` 时**必须**给；"
            "`intent` 为 `candidates` 时也可以带（清单照给）。"
            "如果你觉得档案里缺了某类信息、先问一句能让我下一轮答得更好，"
            '就加上 `{"question": "你要问我的那一句", "missing": "缺的是哪一类档案信息"}`。'
            "`missing` 必须点名其中一类（"
            + " / ".join(brief.clarify_keys)
            + "），不许写空泛的话——"
            "像「你想学什么」这种把问题抛回给我的追问会被判为不合格；"
            "已经问过、我也答过的事（见【已答过的追问——禁区】）换说法再问同样被判为不合格，"
            "把我答的内容当已知用。",
            "- **追问只问「关于我的一件事」**：档案里缺的那类事实，一句话就能答完"
            "（例如「你现在每周能稳定投入几小时」）。**不许问「这条路怎么走」**"
            "（先学哪个 / 怎么排顺序 / 用什么节奏 / 想先交出什么）——那是采纳之后的规划对话该问的，"
            "在这里问会被判为不合格。`question` 写一句话，别写成一段、别一次问好几件事。",
            "- 只输出一个 JSON 对象，不要解释、不要 Markdown 代码块。三种意图的形状："
            '{"intent": "candidates", "reply": "", "shape": "directions",'
            ' "candidates": [{"title": "...", "kind": "...", "why": "...",'
            ' "depth_target": "...", "profile_item_ids": [数字, ...]}, ...], "steps": [],'
            ' "recommended_start": "...", "start_reason": "..."'
            '[, "clarify": {"question": "...", "missing": "..."}]}；'
            "`shape` 为 `path` 时是同一个形状，只是 `candidates` 只有 1 条、"
            '`steps` 给 [{"title": "...", "deliverable": "...", "why": "..."}, ...]；'
            '需要追问时是 {"intent": "need_info", "reply": "...",'
            ' "clarify": {"question": "...", "missing": "..."}}；'
            '只是说话时是 {"intent": "chat", "reply": "..."}'
            '（解释形态冲突时再加 "shape_change": {"from": "...", "to": "...", "reason": "..."}）。',
        ]

        if brief.banned_titles:
            lines += [
                "",
                "【禁区】下面这些是我以前否决过的，一个字都不要以同样或近似的说法再推荐：",
            ]
            lines += [f"- {title}" for title in brief.banned_titles]

        lines += [
            "",
            "【最后核对】每条候选都必须能直接回应【这次的问题】——回应不上来的方向，"
            "一条都不要给；宁可少给，不许拿无关的凑数。",
        ]
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(lines)},
        ]


DEFAULT_SOURCE: Source = RouteOnlySource()


def describe(source: Source) -> dict[str, Any]:
    """来源的自述，随结果一起返回——前端/交接文档能一眼看出这批候选是不是联网来的。"""
    return {"name": source.name, "networked": source.name != RouteOnlySource.name}
