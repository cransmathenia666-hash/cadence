"""知识库安全层（成果闭环重构 P5 / KB-01，方案 docs/ideas/成果闭环重构开发方案.md §7.1、§7.2、§10.3）。

模型不能直接碰硬盘：所有知识库读取都经过这一层。它只认配置过的知识库根目录，
只放行白名单扩展名的文本文件，路径一律相对、解析后必须在根目录之内——
绝对路径只活在后端配置（`Root.path`）里，任何对外返回（根清单、文件清单、
错误消息）都不携带绝对路径、盘符或系统细节。

扫描（本模块下半部分，KB-02 / KB-03）在这层安全检查之上转一个**受限提炼循环**：
系统先列目录、给每个文件取基线哈希，然后逐个文件让模型调 `knowledge_list_files` /
`knowledge_read_file` 读原文，读够了输出结构化的档案候选卡；每个文件最多
`MAX_FILE_MODEL_CALLS` 次模型调用，读取次数与字符数各有扫描级配额。覆盖账只在
「读完整、输出结构合格、摘录逐字对得上、候选全部落库」时才记 `covered`，且单文件的
覆盖行、文件台账、候选提案、扫描计数在**同一个事务**里提交——任何中断、模型失败、
文件中途被改都只留下 partial / failed，绝不假装看完（§7.4）。

原始文件全文只活在模型的这一轮对话里：台账、日志、提案 payload 只存摘录（§7.1 第 6 条）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any, Sequence

from pydantic import BaseModel, Field, ValidationError

from . import advisor, ledger, llm, memory, profile
from .db import atomic, now_iso

# ---- 配置入口（§10.3：根目录由本地配置，不由模型或普通前端请求指定） ----

ROOTS_ENV = "CADENCE_KNOWLEDGE_ROOTS"

# ---- 读取配额（§7.2：默认值，首轮真实走查后再调；扫描流程读这里，不自己另立数字） ----

MAX_READ_CALLS = 12  # 每次扫描最多读多少次文件
MAX_FILE_CHARS = 30000  # 单篇最多返回多少字符
MAX_TOTAL_CHARS = 120000  # 每次扫描总共最多读多少字符

REQUIREMENTS_VERSION_DEFAULT = "profile-v1"
TRIGGERS = ("manual", "weekly")

# ---- 文件白名单 / 黑名单（§7.1 第 3 条、§10.3） ----

# 只允许 Markdown 与纯文本；大小写不敏感（README.TXT 与 readme.txt 同等对待）。
ALLOWED_EXTENSIONS = frozenset({".md", ".markdown", ".txt"})

# 一票否决的文件后缀。白名单（上一行）之外再多一道闸，专门拦「看着像文本、
# 实际会带出秘密或能执行」的文件；命中时错误消息比「扩展名不在白名单」更具体。
DENIED_SUFFIXES = (
    # 凭证与环境
    ".env",
    ".pem", ".key", ".p12", ".pfx", ".htpasswd", ".npmrc", ".netrc",
    # 数据库（含 SQLite 的 wal/shm 伴生文件）
    ".db", ".sqlite", ".sqlite3", ".sqlite-wal", ".sqlite-shm",
    ".sqlite3-wal", ".sqlite3-shm",
    # 可执行 / 脚本
    ".exe", ".dll", ".bat", ".cmd", ".com", ".ps1", ".sh", ".scr", ".msi", ".vbs", ".jar",
)

# 这些**目录**里的文件一律不读也不列（§10.3：仓库 .git 目录）。比较不分大小写。
DENIED_DIRS = frozenset({".git", "node_modules"})

# 「看起来像本机绝对路径」的片段：盘符路径（C:\… / C:/…）与 UNC（\\server\share\…）。
# 落台账 / 回响应前用它兜底脱敏——只替换自己的根挡不住别处的绝对路径。
_ABSOLUTE_PATH_RE = re.compile(
    r"[A-Za-z]:[\\/][^\s\"'，。；、）(\)\]]*|\\\\[^\s\"'，。；、）(\)\]]+"
)


# ---- 异常 ----


class KnowledgeError(RuntimeError):
    """知识库层面的一般错误（读不了、配置无效、参数不对）。

    消息一律是人话，且不含绝对路径——错误会原样到达前端（§10.3：脱敏的错误）。
    """


class KnowledgeDenied(KnowledgeError):
    """路径安全检查拒绝：这条路径不允许读。消息同样不得含绝对路径。"""


class KnowledgeNotFound(KnowledgeError):
    """知识库根或文件不存在。"""


class KnowledgeConflict(KnowledgeError):
    """读取过程中对象发生了变化（如列目录之后文件被改动，§10.2 changed_during_read）。

    安全层暂不主动抛；由后续扫描流程在比对修改时间时使用。
    """


# ---- 根目录：配置解析与对外视图 ----


@dataclass(frozen=True)
class Root:
    """一个知识库根目录。

    `path` 是绝对路径，只留在后端内部；对外一律经 `public_roots()` 脱敏。
    """

    id: int
    alias: str
    path: Path
    enabled: bool
    error: str | None = None


@dataclass(frozen=True)
class ResolvedPath:
    """一次安全检查的结论：归一化相对路径 + 根内的绝对路径。"""

    root: Root
    relative_path: str  # 一律正斜杠
    absolute: Path  # 已 resolve：符号链接已经跟着走完，不会再指向根外


def parse_roots(config: str) -> list[Root]:
    """把环境变量的原始配置解析成根列表。**只标记问题，绝不抛异常**。

    条目格式「别名=绝对路径」，多个条目用分号或换行分隔。编号按配置顺序从 1 起
    （写错的条目也占号，这样对外清单的编号和用户数配置的顺序永远对得上）。
    目录不存在 / 不是目录 / 不是绝对路径的根标 enabled=False 并带一句人话 error；
    别名重复的也停用（别名是核对出处时的查找键，重名会核到另一个根上）。
    """
    roots: list[Root] = []
    seen_aliases: set[str] = set()
    entries = [e.strip() for e in re.split(r"[;\r\n]+", config or "") if e.strip()]
    for index, entry in enumerate(entries, start=1):
        fallback_alias = f"配置条目{index}"
        if "=" not in entry:
            roots.append(Root(
                id=len(roots) + 1, alias=fallback_alias, path=Path(), enabled=False,
                error="配置条目格式不对：应为「别名=绝对路径」，这一条里没有等号",
            ))
            continue
        alias, _, raw_path = entry.partition("=")
        alias = alias.strip().strip("\"'")
        raw_path = raw_path.strip().strip("\"'")
        if not alias or not raw_path:
            roots.append(Root(
                id=len(roots) + 1, alias=alias or fallback_alias, path=Path(), enabled=False,
                error="配置条目格式不对：别名和路径都不能为空",
            ))
            continue
        # 别名是向外认这些根的唯一名字（核对出处时就是按它反查是哪个根），重名会让出处核到
        # 另一个根上——重复的那条直接停用，而不是静默取第一个。
        if alias in seen_aliases:
            roots.append(Root(
                id=len(roots) + 1, alias=alias, path=Path(), enabled=False,
                error="别名和前面的根重复了（别名要唯一），这一条已停用",
            ))
            continue
        seen_aliases.add(alias)
        path = Path(raw_path)
        if not path.is_absolute():
            roots.append(Root(
                id=len(roots) + 1, alias=alias, path=path, enabled=False,
                error="配置的路径必须是绝对路径（从盘符或共享名写起）",
            ))
            continue
        if not path.is_dir():
            roots.append(Root(
                id=len(roots) + 1, alias=alias, path=path, enabled=False,
                error="这个根指向的目录不存在或不是目录，已停用",
            ))
            continue
        # resolve() 规范化路径并把根自己的符号链接走完（§10.3：配置入口做规范化与逃逸校验）
        roots.append(Root(id=len(roots) + 1, alias=alias, path=path.resolve(), enabled=True))

    return roots


def configured_roots() -> list[Root]:
    """从环境变量读当前配置。每次现读，不做缓存——配置改了立即生效。"""
    return parse_roots(os.environ.get(ROOTS_ENV, ""))


def quota_limits() -> dict[str, int]:
    """读取配额的**公开**上限：界面按它画比例。数字只在后端有一份，界面别抄第二份
    （SPEC 第 14 节「组件不内联业务规则」）——后端调了，界面画的比例才不会静默说谎。
    """
    return {
        "max_read_calls": MAX_READ_CALLS,
        "max_file_chars": MAX_FILE_CHARS,
        "max_total_chars": MAX_TOTAL_CHARS,
    }


def public_roots(roots: Sequence[Root] | None = None) -> list[dict[str, object]]:
    """根目录的对外视图：只有 id、别名、可用状态和脱敏过的错误，**绝不含绝对路径**。"""
    resolved = configured_roots() if roots is None else list(roots)
    return [
        {"id": root.id, "alias": root.alias, "enabled": root.enabled, "error": root.error}
        for root in resolved
    ]


def find_root(root_id: int, roots: Sequence[Root] | None = None) -> Root:
    """按编号找根；找不到明确报错，不静默回落到别的根。"""
    resolved = configured_roots() if roots is None else list(roots)
    for root in resolved:
        if root.id == root_id:
            return root
    return _no_such_root(root_id, resolved)


def _no_such_root(root_id: int, roots: Sequence[Root]) -> Root:
    raise KnowledgeNotFound(f"编号 {root_id} 的知识库根不存在（当前配置了 {len(roots)} 个根）")


def _usable_root(root_id: int, roots: Sequence[Root] | None) -> Root:
    """取一个**可用**的根：不存在的报 NotFound，存在但被停用的报它自己的原因。"""
    root = find_root(root_id, roots)
    if not root.enabled:
        raise KnowledgeError(root.error or "这个知识库根当前不可用")
    return root


# ---- 路径安全检查 ----


def _check_shape(raw: str) -> list[str]:
    """校验路径的「形状」并切段：拒绝绝对路径、盘符、UNC 共享与任何「..」段。

    返回清洗后的段列表（去掉空的与「.」段），供后续拼根内路径用。
    """
    text = (raw or "").strip().strip("\"'")
    if not text:
        raise KnowledgeDenied("路径不能为空")
    # 统一按正斜杠切段；反斜杠只是分隔符写法，不当成文件名的一部分
    text = text.replace("\\", "/")
    pure = PureWindowsPath(text)
    if pure.is_absolute() or pure.drive or pure.root:
        # 盘符写法（C:notes.md）不是 is_absolute，前导斜杠（/etc/passwd）没有 drive——
        # 两者都得单独查：前者看 drive，后者看 root
        raise KnowledgeDenied("拒绝绝对路径与盘符：只能给知识库内的相对路径")
    parts = [p for p in text.split("/") if p and p != "."]
    if not parts:
        raise KnowledgeDenied("路径不能为空")
    for part in parts:
        if part == "..":
            raise KnowledgeDenied("路径里不能出现「..」：不允许越过知识库根目录")
    return parts


def _check_name_rules(parts: list[str]) -> None:
    """对最后一段做文件名检查：禁目录、禁后缀、白名单扩展名。"""
    *parents, name = parts
    hit = next((p for p in parents if p.lower() in DENIED_DIRS), None)
    if hit:
        raise KnowledgeDenied(f"「{hit}/」目录里的文件一律不读")
    lowered = name.lower()
    if lowered.endswith(DENIED_SUFFIXES):
        raise KnowledgeDenied(
            f"「{name}」属于不允许读取的文件类型（配置、数据库、可执行或凭证类文件）"
        )
    suffix = os.path.splitext(lowered)[1]
    if suffix not in ALLOWED_EXTENSIONS:
        raise KnowledgeDenied("只允许读取 Markdown 与纯文本文件（.md / .markdown / .txt）")


def _check_resolved_dirs(absolute: Path, base: Path) -> None:
    """按**解析后**的真实路径再查一次目录黑名单。

    只查字面路径段会漏一种情形：根里一个 junction / 符号链接别名指向 .git——junction 不算
    islink，`os.walk(followlinks=False)` 照样往里走，只有 resolve 之后才现形。
    """
    try:
        parts = absolute.relative_to(base).parts
    except ValueError:
        return  # 已经越界，那是另一道闸的事
    hit = next((p for p in parts[:-1] if p.lower() in DENIED_DIRS), None)
    if hit:
        raise KnowledgeDenied(f"「{hit}/」目录里的文件一律不读")


def safe_relative_path(
    root_id: int, rel: str, roots: Sequence[Root] | None = None
) -> ResolvedPath:
    """把「根编号 + 相对路径」解析成根内的绝对路径；任何越界企图抛 KnowledgeDenied。

    检查顺序：形状（绝对路径 / 盘符 / `..`）→ 解析后仍在根内（符号链接一起 resolve，
    逃逸当场现形）→ 目录黑名单 → 后缀黑名单 → 扩展名白名单。
    """
    root = _usable_root(root_id, roots)
    parts = _check_shape(rel)
    base = root.path.resolve()
    candidate = base.joinpath(*parts).resolve()
    if not candidate.is_relative_to(base):
        raise KnowledgeDenied("路径解析后跑到了知识库根目录外面（可能经过符号链接）")
    _check_name_rules(parts)
    _check_resolved_dirs(candidate, base)
    return ResolvedPath(root=root, relative_path="/".join(parts), absolute=candidate)


# ---- 文件清单与读取 ----


def list_files(
    root_id: int, prefix: str = "", roots: Sequence[Root] | None = None
) -> list[dict[str, object]]:
    """列出根内**可读**文件的相对路径清单，按相对路径排序，一律正斜杠。

    只返回能通过安全检查的文件——「看得见的就都读得到」，清单里不会出现
    让模型去读却注定被拒的路径。prefix 按目录前缀过滤（"notes" 匹配 notes/
    整棵子树，不会误伤 notes-extra/）。
    """
    root = _usable_root(root_id, roots)
    prefix_norm = ""
    if (prefix or "").strip():
        prefix_norm = "/".join(_check_shape(prefix))
    results: list[dict[str, object]] = []
    for dirpath, dirnames, filenames in os.walk(root.path, followlinks=False):
        # 黑名单目录就地修剪：既不枚举也不深入（比逐文件拒绝省一次 resolve）
        dirnames[:] = [d for d in dirnames if d.lower() not in DENIED_DIRS]
        rel_dir = Path(dirpath).relative_to(root.path)
        for name in filenames:
            rel = name if str(rel_dir) == "." else (rel_dir / name).as_posix()
            if prefix_norm and not (
                rel == prefix_norm or rel.startswith(prefix_norm + "/")
            ):
                continue
            parts = rel.split("/")
            try:
                base = root.path.resolve()
                absolute = base.joinpath(*parts).resolve()
                if not absolute.is_relative_to(base):
                    continue  # 符号链接逃逸：不列
                _check_name_rules(parts)
                _check_resolved_dirs(absolute, base)
            except KnowledgeError:
                continue  # 不可读的文件不进清单
            try:
                stat = absolute.stat()
            except OSError:
                continue  # 列目录之后文件没了（竞态）：这轮当它不存在
            results.append({
                "relative_path": rel,
                "size": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime)
                .astimezone()
                .isoformat(timespec="seconds"),
            })
    results.sort(key=lambda item: str(item["relative_path"]))
    return results


def read_text(
    root_id: int,
    rel: str,
    offset: int = 0,
    limit: int = MAX_FILE_CHARS,
    roots: Sequence[Root] | None = None,
) -> dict[str, object]:
    """以 UTF-8 读取一个通过安全检查的文本文件，按单篇上限截断。

    offset / limit 都按**字符**计（与扫描层的字符预算同一口径）。truncated=True
    表示这次没有返回整个文件的剩余内容——调用方必须如实转告「没读完」，不得
    当作已看完（§7.1 第 8 条）。content_hash / size 始终是**整个文件**的值，
    不随截断变化，供「文件变没变」的比对。
    """
    resolved = safe_relative_path(root_id, rel, roots)
    if offset < 0:
        raise KnowledgeError("起始位置 offset 不能是负数")
    limit = MAX_FILE_CHARS if limit is None else int(limit)
    if limit <= 0:
        raise KnowledgeError("单篇读取上限 limit 必须是正整数")
    limit = min(limit, MAX_FILE_CHARS)  # 单篇硬顶：调用方给再大也不越线

    path = resolved.absolute
    if not path.exists():
        raise KnowledgeNotFound(f"文件不存在：{resolved.relative_path}")
    if path.is_dir():
        raise KnowledgeError(f"这是一个目录，不是文件：{resolved.relative_path}")
    try:
        data = path.read_bytes()
    except OSError:
        # 不透传系统错误原文——Windows 的报错文本里带绝对路径
        raise KnowledgeError(f"读不了这个文件：{resolved.relative_path}") from None
    try:
        decoded = data.decode("utf-8")
    except UnicodeDecodeError:
        raise KnowledgeError(
            f"这个文件不是有效的 UTF-8 文本，读不了：{resolved.relative_path}"
        ) from None

    remaining = decoded[offset:]
    text = remaining[:limit]
    return {
        "relative_path": resolved.relative_path,
        "content_hash": hash_bytes(data),
        "size": len(data),
        "text": text,
        "truncated": len(remaining) > len(text),
    }


# ---- 哈希 ----


def hash_bytes(content: bytes) -> str:
    """内容哈希：sha256 十六进制。"""
    return hashlib.sha256(content).hexdigest()


def hash_file(path: Path) -> str:
    """文件哈希：sha256 十六进制，分块读，不怕大文件。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ============================================================
# 二、受限扫描（KB-02 / KB-03，方案 §7.2–§7.4、§10.2、§10.4）
#
# 扫描怎么转：系统先列目录、给每个文件取一个**基线内容哈希**（列目录之后再改 =
# changed_during_read，§10.2）；增量模式下这个版本（相对路径+内容哈希+需求版本，
# §7.4）已经处理过的文件直接跳过；剩下的文件**逐个**进受限提炼循环——模型调
# `knowledge_list_files` / `knowledge_read_file` 读原文（root 与扫描编号由系统注入，
# 模型参数里出现它们一律拒），读够了输出最终的 {"cards": [...], "unread_gaps": [...]}。
# 一个文件「读完整 + 输出结构合格 + 摘录逐字对得上 + 候选全部落库」才在**同一个事务**
# 里写覆盖行、文件台账、候选提案与扫描计数；任何一步不成只写 partial / failed，
# 绝不写 covered，也绝不回滚其它已成功的文件（§7.4）。
# ============================================================

# 落进 `proposal.kind` 的取值（知识库候选与聊天提炼同走档案变更提案，§5.2）。
KIND_PROFILE_CHANGE = "profile_change"

# 模型调用的记账任务名（llm_call.task）；换模型不影响覆盖账，账只记调了多少。
SCAN_TASK = "knowledge_scan"

# 质检结论先固定 pending：规则校验跑完即过，人工抽查是后续的事（§5.2）。
QUALITY_PENDING = "pending"

# 单个文件一次扫描最多几次模型调用。工具轮与「输出不合格带原因重说」**共用**它，
# 但重说只给一次——两次仍不合格就放弃这个文件（与 memory.scan 的 1+1 同一口径）。
MAX_FILE_MODEL_CALLS = 3
MAX_LIST_CALLS = 3  # 列目录工具自己的小额度：防它在循环里反复列目录磨模型调用
MAX_CARDS = 20  # 一次提炼最多几张候选卡（§7.3）

# 开场白里「当前档案」清单的字符上限：够它对着给 target_profile_id，不至于撑爆上下文。
PROFILE_BRIEF_LIMIT = 2000

# 模型可用的两个受限工具名（§7.2：同一个受限扫描 Agent 里的两个工具）。
TOOL_LIST = "knowledge_list_files"
TOOL_READ = "knowledge_read_file"

# 卡片的三种动作。`void` 永远不由模型生成——作废只能由用户手动完成（§7.3）。
CARD_ADD = "add"
CARD_SUPERSEDE = "supersede"
CARD_ACTIONS: dict[str, str] = {"add": "新增", "supersede": "取代", "uncertain": "存疑"}

# 覆盖账里算「这个版本已经处理完」的状态；partial / failed 都不算，下次重扫。
COVERAGE_DONE = ("covered", "reviewed_no_change")

# 覆盖行 / 扫描行的状态取值（schema.sql 的注释口径）。
FILE_COVERED = "covered"
FILE_REVIEWED = "reviewed_no_change"
FILE_PARTIAL = "partial"
FILE_FAILED = "failed"

SCAN_SYSTEM_PROMPT = (
    "你在帮用户把知识库笔记里的长期事实提炼成「档案候选」。"
    "你只输出一个 JSON 对象：不要解释、不要客套、不要 Markdown 代码块。"
)


class ToolError(RuntimeError):
    """一次受限工具调用不成立（参数越权、路径出界、额度用完）。

    它**不中断扫描**：错误原文回给模型，让它自己改——改不动自然撞上模型调用上限。
    消息是人话，且不含绝对路径（§10.3）。
    """


class _FileChanged(RuntimeError):
    """读到一半发现文件内容与列目录时的基线哈希对不上：立刻放弃这个文件（§10.2）。"""


# ---------- 提炼输出 schema（§7.3，字段照设计稿） ----------


class CardEvidence(BaseModel):
    """一条出处：哪个文件的哪一段原文。摘录必须能在真实读到的内容里逐字找到。"""

    path: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    line_start: int
    line_end: int


class DistillCard(BaseModel):
    """模型提的一张档案候选卡。字段先按形状收，语义留给 `_validate_card` 判。"""

    action: str = Field(min_length=1)
    category: str | None = None
    content: str | None = None
    why: str | None = None
    source_kind: str = "agent_inferred"
    evidence: list[CardEvidence] = Field(default_factory=list)
    target_profile_id: int | None = None
    uncertainty_reason: str | None = None


class DistillOutput(BaseModel):
    """一次提炼的产出。**允许 0 张卡**——大多数笔记里没有值得进档案的东西才是常态。"""

    cards: list[DistillCard] = Field(default_factory=list, max_length=MAX_CARDS)
    unread_gaps: list[str] = Field(default_factory=list)


# ---------- 扫描现场 ----------


@dataclass
class ScanState:
    """一次扫描的现场：连接、根、读取配额计数与列目录时算好的覆盖备注。"""

    conn: sqlite3.Connection
    root: Root
    scan_id: int
    requirements_version: str
    read_calls: int = 0
    chars_read: int = 0
    list_calls: int = 0
    coverage_notes: dict[str, str] = field(default_factory=dict)


@dataclass
class FileProgress:
    """一个文件在一次扫描里的读取进度：读了哪些段、读了多少、读完没有。"""

    relative_path: str
    baseline_hash: str  # 列目录时的内容哈希：之后每次读都对它
    size: int
    modified_at: str | None
    segments: list[tuple[int, str]] = field(default_factory=list)  # (offset, 本次读到的原文)
    read_calls: int = 0
    chars_read: int = 0
    total_chars: int | None = None  # 某次读到文件末尾时才知道全文多少字符

    @property
    def covered_chars(self) -> int:
        """从第 0 字符起**连续**覆盖到哪（跳读不算，缺口就是缺口）。"""
        end = 0
        for offset, text in sorted(self.segments):
            if offset > end:
                break
            end = max(end, offset + len(text))
        return end

    @property
    def fully_read(self) -> bool:
        """读完整 = 知道全文长度，且从 0 起连续覆盖到末尾（§7.4 覆盖的第一道前提）。"""
        return self.total_chars is not None and self.covered_chars >= self.total_chars

    @property
    def full_text(self) -> str:
        """把读到的段按偏移拼回去。只有读完整时它才等于文件原文——摘录校验用。"""
        parts: list[str] = []
        end = 0
        for offset, text in sorted(self.segments):
            if offset > end:  # 理论上到不了：full_read 已验证连续；保险起见补个换行占位
                parts.append("\n")
                end = offset
            if offset + len(text) > end:
                parts.append(text[end - offset :])
                end = offset + len(text)
        return "".join(parts)

    def has_excerpt(self, excerpt: str) -> bool:
        """摘录逐字校验：在**任何一次真实读到的原文**里找得到就算数。"""
        if any(excerpt in text for _, text in self.segments):
            return True
        return self.fully_read and excerpt in self.full_text


@dataclass
class FileOutcome:
    """一个文件处理完的结果：给扫描报告与收尾判定用。"""

    relative_path: str
    status: str = FILE_FAILED
    error: str | None = None
    candidates: int = 0
    proposal_ids: list[int] = field(default_factory=list)
    dropped: list[dict[str, str]] = field(default_factory=list)
    model_gaps: list[str] = field(default_factory=list)
    abort_scan: bool = False  # 模型调用失败：后面的文件这次不再尝试


# ---------- 受限工具（§7.2） ----------

# 模型参数里出现这些键一律拒：root 与扫描编号由系统注入，「读哪个范围」不是它能选的。
_FORBIDDEN_ARG_KEYS = ("root_id", "scan_id", "scope")


def _check_args(name: str, args: Any, allowed: tuple[str, ...]) -> None:
    """工具参数闸：先拦越权键（root_id / scan_id / scope），再拦没登记过的键。"""
    if not isinstance(args, dict):
        raise ToolError(f"{name} 的参数必须是一个 JSON 对象（没有参数就给 {{}}）")
    forbidden = [str(key) for key in args if str(key) in _FORBIDDEN_ARG_KEYS]
    if forbidden:
        raise ToolError(
            f"{name} 不收参数「{'、'.join(forbidden)}」：知识库根、扫描编号与范围"
            "都由系统注入，你给不了也不用给"
        )
    unknown = [str(key) for key in args if key not in allowed]
    if unknown:
        raise ToolError(
            f"{name} 不收参数「{'、'.join(unknown)}」"
            + (f"；只认 {' / '.join(allowed)}" if allowed else "；它不接参数")
        )


def _as_int(value: Any, name: str, *, default: int, minimum: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        try:
            value = int(str(value).strip())
        except (TypeError, ValueError):
            raise ToolError(f"{name} 要是一个整数，收到的是「{value}」") from None
    if value < minimum:
        raise ToolError(f"{name} 不能小于 {minimum}")
    return value


def _wanted_extensions(raw: Any) -> frozenset[str] | None:
    """extensions 参数：归一化成带点的小写集合；白名单之外的直接拒。"""
    if raw in (None, []):
        return None
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ToolError('extensions 要是一个字符串数组，比如 [".md", ".txt"]')
    wanted: set[str] = set()
    for item in raw:
        ext = "".join(str(item).split()).lower()
        if not ext.startswith("."):
            ext = "." + ext
        if ext not in ALLOWED_EXTENSIONS:
            raise ToolError(
                f"扩展名「{ext}」筛不了：清单只认 {' / '.join(sorted(ALLOWED_EXTENSIONS))}"
            )
        wanted.add(ext)
    return frozenset(wanted)


def _tool_list_files(state: ScanState, args: Any) -> str:
    """列目录：只回相对路径 + 大小 + 修改时间 + 覆盖状态（覆盖备注来自列目录时的快照）。"""
    _check_args(TOOL_LIST, args, ("prefix", "extensions"))
    if state.list_calls >= MAX_LIST_CALLS:
        raise ToolError(
            f"列目录的次数额度（{MAX_LIST_CALLS} 次）已经用完——文件清单已经给过你了，该提炼了"
        )
    prefix = args.get("prefix")
    prefix_norm = ""
    if prefix not in (None, ""):
        if not isinstance(prefix, str):
            raise ToolError("prefix 要是一个相对目录前缀字符串，比如 \"notes\"")
        prefix_norm = "/".join(_check_shape(prefix))
    wanted = _wanted_extensions(args.get("extensions"))
    state.list_calls += 1
    entries = list_files(state.root.id, prefix_norm)
    if wanted is not None:
        entries = [
            item
            for item in entries
            if os.path.splitext(str(item["relative_path"]).lower())[1] in wanted
        ]
    lines = [f"【知识库文件清单】共 {len(entries)} 个（只有相对路径；括号里是覆盖状态）"]
    if not entries:
        lines.append("（没有匹配的文件）")
    for item in entries:
        rel = str(item["relative_path"])
        note = state.coverage_notes.get(rel, "未覆盖")
        lines.append(f"- {rel}（{item['size']} 字节，修改于 {item['modified_at']}，{note}）")
    return "\n".join(lines)


def _tool_read_file(state: ScanState, progress: FileProgress, args: Any) -> str:
    """读文件：路径必须在本次允许的范围内，配额（次数、总字符）真实生效，逐次记账。"""
    _check_args(TOOL_READ, args, ("path", "offset", "limit"))
    raw_path = args.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ToolError(f"{TOOL_READ} 需要 path（相对路径，比如 \"{progress.relative_path}\"）")
    try:
        normalized = "/".join(_check_shape(raw_path))
    except KnowledgeError as error:  # 绝对路径 / 「..」：消息本身就是人话
        raise ToolError(str(error)) from None
    if normalized != progress.relative_path:
        raise ToolError(
            f"「{normalized}」不在这次允许读取的范围内：这一轮只处理 {progress.relative_path}，"
            "范围由系统注入，你不能改"
        )
    offset = _as_int(args.get("offset"), "offset", default=0, minimum=0)
    limit = _as_int(args.get("limit"), "limit", default=MAX_FILE_CHARS, minimum=1)
    if state.read_calls >= MAX_READ_CALLS:
        raise ToolError(
            f"这次扫描的读文件次数额度（{MAX_READ_CALLS} 次）已经用完——"
            "把已经读到的内容提炼成 cards 输出，读不完的部分写进 unread_gaps"
        )
    remaining = MAX_TOTAL_CHARS - state.chars_read
    if remaining <= 0:
        raise ToolError(
            f"这次扫描的总字符额度（{MAX_TOTAL_CHARS}）已经用完——"
            "把已经读到的内容提炼成 cards 输出，读不完的部分写进 unread_gaps"
        )

    result = read_text(state.root.id, normalized, offset=offset, limit=min(limit, remaining))
    if str(result["content_hash"]) != progress.baseline_hash:
        # 列目录之后文件被改过：这次读到的已经不是账本记的那个版本，立即放弃（§10.2）。
        raise _FileChanged()
    text = str(result["text"])
    state.read_calls += 1
    state.chars_read += len(text)
    progress.read_calls += 1
    progress.chars_read += len(text)
    progress.segments.append((offset, text))
    if not result["truncated"]:
        progress.total_chars = offset + len(text)
    tail = "未读完：后面还有内容，用 offset 续读" if result["truncated"] else "已到文件末尾"
    return (
        f"【{TOOL_READ}】{normalized}｜从第 {offset} 字符起｜本次 {len(text)} 字符｜{tail}\n"
        f"{text}"
    )


def _execute_tool(state: ScanState, progress: FileProgress, name: Any, args: Any) -> str:
    """跑一次受限工具。安全层的 KnowledgeError 在这里翻成 ToolError（原文回给模型）。"""
    try:
        if name == TOOL_LIST:
            return _tool_list_files(state, args)
        if name == TOOL_READ:
            return _tool_read_file(state, progress, args)
        raise ToolError(f"没有叫「{name}」的工具；只能用 {TOOL_LIST} / {TOOL_READ}")
    except ToolError:
        raise
    except KnowledgeError as error:
        raise ToolError(str(error)) from None


# ---------- 模型输出的两副面孔 ----------


class _ToolCall(BaseModel):
    name: str = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)


class _ToolRequest(BaseModel):
    tool_calls: list[_ToolCall] = Field(min_length=1)


def _as_tool_request(raw: str) -> tuple[_ToolRequest | None, str | None]:
    """这段输出是「要读资料」还是「最终提炼」？判据只有一个：非空的 tool_calls 数组。"""
    data = advisor.extract_json(raw) if str(raw or "").strip() else None
    if data is None or "tool_calls" not in data:
        return None, None
    if isinstance(data["tool_calls"], list) and not data["tool_calls"]:
        return None, None  # 空数组不是「要读」，按最终输出去验
    try:
        return _ToolRequest.model_validate(data), None
    except ValidationError as error:
        return None, f"tool_calls 写得不对（{_details(error)}）"


def _as_distill_output(raw: str) -> tuple[DistillOutput | None, str | None]:
    data = advisor.extract_json(raw) if str(raw or "").strip() else None
    if data is None:
        return None, "输出不是合法的 JSON 对象"
    try:
        return DistillOutput.model_validate(data), None
    except ValidationError as error:
        return None, _details(error)


def _details(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or '(根)'}: {item['msg']}"
        for item in error.errors()
    )


# ---------- 单个文件的提炼循环 ----------


def _opening(state: ScanState, progress: FileProgress) -> str:
    """单个文件提炼循环的开场白：任务、工具、额度、输出形状与当前档案。"""
    lines = [
        f"【这一轮提炼哪个文件】{progress.relative_path}（约 {progress.size} 字节）。"
        "这个路径由系统指定：读取与摘录只允许对着它，你不能换、也不能改范围。",
        "",
        "【你能用的工具】要读资料就只输出一个 JSON 对象："
        '{"tool_calls": [{"name": "工具名", "args": {...}}]}，一次可以要好几样。',
        f"- {TOOL_LIST}：列知识库里的文件（只给相对路径）。"
        '可选参数 {"prefix": "目录前缀", "extensions": [".md"]}。',
        f"- {TOOL_READ}：读上面那个文件。参数 {{\"path\": \"{progress.relative_path}\", "
        "\"offset\": 起始字符, \"limit\": 本次最多字符}；文件没读完就用 offset 续读。",
        "",
        f"【额度】这一轮你最多调模型 {MAX_FILE_MODEL_CALLS} 次（输出不合格重说也计入）；"
        f"整个扫描最多读 {MAX_READ_CALLS} 次文件、共 {MAX_TOTAL_CHARS} 字符。",
        "",
        "【读够之后输出什么】只输出一个 JSON 对象（不要解释、不要 Markdown 代码块）：",
        '{"cards": [...], "unread_gaps": ["没读完或没看清的部分，一句一条"]}',
        "每张卡的形状：",
        (
            '{"action": "add|supersede|uncertain", "category": "类别令牌",'
            ' "content": "可写入档案的一句话", "why": "为什么值得进档案",'
            ' "source_kind": "user_stated|agent_inferred",'
            f' "evidence": [{{"path": "{progress.relative_path}",'
            ' "excerpt": "从文件里一字不差抄的一段", "line_start": 1, "line_end": 3}}],'
            ' "target_profile_id": null, "uncertainty_reason": null}'
        ),
        "- add：档案里还没有这条。supersede：要替代下面档案里的某条，target_profile_id 填它的"
        " #编号。uncertain：拿不准就标它，必须写 uncertainty_reason（这类卡单独存疑，不可批准）。",
        f"- category 只能是这五个令牌之一：{' / '.join(advisor.PROFILE_CATEGORIES)}。",
        "- excerpt 必须从你**真实读到**的内容里一字不差地抄；抄不出来就不要提这张卡。",
        "- source_kind 要如实：材料里用户自己写的才标 user_stated，你归纳出来的必须写"
        " agent_inferred——这一栏批准后是档案上给人看的来源标签，不要替用户认领他没说过的话。",
        f"- 最多 {MAX_CARDS} 张卡；这个文件里没有值得进档案的，就回 "
        '{"cards": [], "unread_gaps": []}——空着是正常的。',
    ]
    rows = ledger.fetch_active(state.conn, "profile_item")
    lines.append("")
    if rows:
        lines.append("【当前档案】supersede 要对着它给 #编号；一字不差的已有条目不要再提：")
        used = 0
        kept = 0
        for row in rows:
            line = f"- #{row['id']} [{row['category']}] {row['content']}"
            if kept and used + len(line) > PROFILE_BRIEF_LIMIT:
                lines.append("（档案太长，只列到这里）")
                break
            lines.append(line)
            kept += 1
            used += len(line) + 1
    else:
        lines.append("【当前档案】一条都没有。")
    return "\n".join(lines)


def _distill_file(
    state: ScanState,
    progress: FileProgress,
    *,
    provider_id: int | None = None,
    model: str | None = None,
    transport: llm.Transport | None = None,
) -> tuple[DistillOutput | None, str | None]:
    """跑一个文件的受限提炼循环，返回（合格输出，不合格原因）。

    最多 `MAX_FILE_MODEL_CALLS` 次模型调用：工具轮与「输出不合格带原因重说」共用，
    但重说只给一次——工具轮可以多轮（比如分两段读完一篇长文），输出不合格却在重说
    一次之后就地放弃。`llm.LlmError` 原样上抛，由扫描层记 failed。
    """
    operation = llm.Operation(
        state.conn, SCAN_TASK, limit=MAX_FILE_MODEL_CALLS, transport=transport
    )
    messages = [
        {"role": "system", "content": SCAN_SYSTEM_PROMPT},
        {"role": "user", "content": _opening(state, progress)},
    ]
    problem = "它还没来得及输出"
    retried = False
    for _attempt in range(MAX_FILE_MODEL_CALLS):
        raw = operation.chat(messages, provider_id=provider_id, model=model)
        request, shape_problem = _as_tool_request(raw)
        if request is not None:
            results = []
            for call in request.tool_calls:
                try:
                    results.append(_execute_tool(state, progress, call.name, call.args))
                except ToolError as error:
                    results.append(f"【{call.name} 没读成】{error}")
            quota = (
                f"（整个扫描已读 {state.read_calls}/{MAX_READ_CALLS} 次文件、"
                f"{state.chars_read}/{MAX_TOTAL_CHARS} 字符）"
            )
            messages = [
                *messages,
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": "【你读到的资料】\n"
                    + ("\n\n".join(results) or "（这次什么都没读到）")
                    + "\n\n"
                    + quota
                    + "读够了就输出最终的 JSON。",
                },
            ]
            continue

        output: DistillOutput | None = None
        if shape_problem is None:
            output, problem = _as_distill_output(raw)
        else:
            problem = shape_problem
        if output is not None:
            return output, None
        if retried:
            break  # 重说一次仍不合格：到此为止，这个文件不记覆盖
        retried = True
        messages = [
            *messages,
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": f"你上面的输出不合格：{problem}。"
                "请按上面说的形状只输出一个合格的 JSON 对象，不要任何解释。",
            },
        ]
    return None, (
        f"连着 {operation.used} 次模型调用都没给出合格的提炼输出（{problem}）；"
        "这个文件不记覆盖，一条候选都没落"
    )


# ---------- 卡片的确定性校验（不通过就整张卡不落，§7.3） ----------


def _validate_card(
    state: ScanState, card: DistillCard, progress: FileProgress
) -> tuple[dict[str, Any] | None, str | None, str]:
    """给出（可落库的 payload，不合格原因，不合格类别）。

    不合格类别只有两种：`duplicate`（与现行档案一字不差——拦下它不是提炼错了，
    是这条早就有了）与 `evidence`（其余一切：类别不合法、摘录找不到、目标无效……）。
    只要有一张卡栽在 evidence 上，整个文件的覆盖账就不推进（§7.4：出处校验要全过才算看完）。
    """
    action = str(card.action or "").strip()
    if action not in CARD_ACTIONS:
        return None, f"动作「{card.action}」不认识，只能给：{' / '.join(CARD_ACTIONS)}", "evidence"
    if card.source_kind not in memory.CANDIDATE_SOURCE_KINDS:
        return None, (
            f"source_kind「{card.source_kind}」不认识，只能是 "
            f"{' / '.join(memory.CANDIDATE_SOURCE_KINDS)}"
        ), "evidence"
    if card.category not in advisor.PROFILE_CATEGORIES:
        return None, (
            f"类别「{card.category}」不在约定的五个令牌里：{' / '.join(advisor.PROFILE_CATEGORIES)}"
        ), "evidence"

    content = str(card.content or "").strip()
    why = str(card.why or "").strip()
    if action in (CARD_ADD, CARD_SUPERSEDE):
        if not content:
            return None, f"{CARD_ACTIONS[action]}必须给 content（要写进档案的那句话）", "evidence"
        if not why:
            return None, f"{CARD_ACTIONS[action]}必须给 why（为什么值得进档案）", "evidence"
    if action == "uncertain" and not str(card.uncertainty_reason or "").strip():
        return None, "uncertain 必须给 uncertainty_reason（拿不准的是哪一句、为什么）", "evidence"

    # 出处校验：路径在本次允许范围内 + 摘录在真实读到的原文里逐字找到。
    landed_evidence: list[dict[str, Any]] = []
    if not card.evidence:
        return None, "每张卡至少要给一条出处（path + excerpt + 行号）", "evidence"
    for item in card.evidence:
        try:
            evidence_path = "/".join(_check_shape(item.path))
        except KnowledgeError as error:
            return None, f"出处路径不合格（{error}）", "evidence"
        if evidence_path != progress.relative_path:
            return None, (
                f"出处路径「{evidence_path}」不在这次允许读取的范围内"
                f"（这一轮只处理 {progress.relative_path}）"
            ), "evidence"
        excerpt = str(item.excerpt or "").strip()
        if not excerpt:
            return None, "摘录不能为空", "evidence"
        if not progress.has_excerpt(excerpt):
            return None, (
                f"摘录在这次真实读到的 {progress.relative_path} 内容里找不到——"
                "摘录必须一字不差地从读到的原文里抄"
            ), "evidence"
        landed_evidence.append(
            {
                "root_alias": state.root.alias,
                "relative_path": evidence_path,
                "excerpt": excerpt,
                "line_start": int(item.line_start),
                "line_end": int(item.line_end),
            }
        )

    target: sqlite3.Row | None = None
    if action == CARD_SUPERSEDE:
        if card.target_profile_id is None:
            return None, "supersede 必须给 target_profile_id（要替代档案里的哪一条）", "evidence"
        target = state.conn.execute(
            "SELECT id, category, content FROM profile_item WHERE id = ? AND status = 'active'",
            (int(card.target_profile_id),),
        ).fetchone()
        if target is None:
            return None, (
                f"取代目标 #{card.target_profile_id} 不是当前有效的档案条目"
                "（可能已被取代或作废）"
            ), "evidence"
        if str(target["category"]) != card.category:
            return None, (
                f"取代目标 #{target['id']} 属于「{target['category']}」类，"
                f"跟这张卡的类别「{card.category}」不一致"
            ), "evidence"
        if content == str(target["content"]).strip():
            return None, "取代的新内容和旧内容一模一样，这条改动等于没发生", "evidence"

    # 完全重复（同类别一字不差的现行条目）整张卡丢；近似重复只提示、不合并（§7.3）。
    exclude_id = int(target["id"]) if target is not None else None
    duplicate = profile.find_duplicate(state.conn, str(card.category), content) if content else None
    if duplicate is not None and exclude_id != int(duplicate["id"]):
        return None, (
            f"档案里已有这条：#{duplicate['id']}（{card.category}，一字不差）——完全重复，不用再提"
        ), "duplicate"

    payload: dict[str, Any] = {
        "action": action,
        "category": card.category,
        "content": content or None,
        "why": why or None,
        "source_kind": card.source_kind,
        "evidence": landed_evidence,
        "target_profile_id": exclude_id,
        "uncertainty_reason": str(card.uncertainty_reason or "").strip() or None,
        "duplicate_hint": memory.duplicate_hint(
            state.conn, "global", content=content, plan_id=None, exclude_id=exclude_id
        ),
        "requirements_version": state.requirements_version,
        "scan_id": state.scan_id,
        "knowledge": True,
    }
    if target is not None:
        payload["target_content"] = str(target["content"])
    return payload, None, "ok"


# ---------- 收尾提交：一个文件一个事务（§7.4） ----------


def redact_paths(text: str) -> str:
    """把任何看起来像本机绝对路径的片段抹掉（§10.3：绝对路径不进台账、不进日志、不回前端）。

    只替换自己的根是不够的：异常原文里可能带别的盘、别的目录（第三方库路径、用户别处
    的文件名），那些同样不该出现在扫描账与接口响应里。
    """
    return _ABSOLUTE_PATH_RE.sub("（本机路径）", str(text or ""))


def _scrub(text: str, root: Root) -> str:
    """落库前把根目录的绝对路径与其余绝对路径一并抹掉（§10.3）。"""
    cleaned = str(text or "")
    for secret in (str(root.path), f"{root.path.drive}{root.path.root}"):
        if secret:
            cleaned = cleaned.replace(secret, "（知识库根目录）")
    return redact_paths(cleaned)


def _file_meta(root_id: int, rel: str) -> dict[str, Any] | None:
    """列目录之后的文件基线：整个文件的内容哈希与大小。读不了（没了/权限）给 None。"""
    try:
        resolved = safe_relative_path(root_id, rel)
        data = resolved.absolute.read_bytes()
        stat = resolved.absolute.stat()
    except (KnowledgeError, OSError):
        return None
    return {
        "relative_path": rel,
        "content_hash": hash_bytes(data),
        "size": len(data),
        "modified_at": datetime.fromtimestamp(stat.st_mtime)
        .astimezone()
        .isoformat(timespec="seconds"),
    }


def upsert_knowledge_file(conn: sqlite3.Connection, root_id: int, rel: str, meta: dict[str, Any]) -> int:
    """知识库文件台账：按（root, 相对路径）登记 / 更新到最新见过的内容哈希。

    两个调用方：扫描收尾（`_commit_file`）与知识库候选的**批准**（`register_evidence`）——
    批准时摘录重新验过，文件台账也跟着记到验过的那个版本。
    """
    row = conn.execute(
        "SELECT id FROM knowledge_file WHERE root_id = ? AND relative_path = ?", (root_id, rel)
    ).fetchone()
    stamp = now_iso()
    if row is None:
        cursor = conn.execute(
            "INSERT INTO knowledge_file"
            " (root_id, relative_path, content_hash, size, modified_at, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (root_id, rel, meta["content_hash"], meta["size"], meta["modified_at"], stamp, stamp),
        )
        return int(cursor.lastrowid)
    conn.execute(
        "UPDATE knowledge_file SET content_hash = ?, size = ?, modified_at = ?, updated_at = ?"
        " WHERE id = ?",
        (meta["content_hash"], meta["size"], meta["modified_at"], stamp, int(row["id"])),
    )
    return int(row["id"])


def _already_covered(
    conn: sqlite3.Connection, root_id: int, rel: str, content_hash: str, requirements_version: str
) -> bool:
    """这个版本（相对路径+内容哈希+需求版本，§7.4）是否已经处理完。换模型不影响它。"""
    row = conn.execute(
        "SELECT c.id FROM knowledge_coverage c JOIN knowledge_file f ON f.id = c.file_id"
        " WHERE f.root_id = ? AND f.relative_path = ? AND c.content_hash = ?"
        f" AND c.requirements_version = ? AND c.status IN ({','.join('?' * len(COVERAGE_DONE))})"
        " LIMIT 1",
        (root_id, rel, content_hash, requirements_version, *COVERAGE_DONE),
    ).fetchone()
    return row is not None


def _commit_file(
    state: ScanState,
    progress: FileProgress,
    *,
    status: str,
    error: str | None = None,
    payloads: list[dict[str, Any]] | None = None,
    meta: dict[str, Any] | None = None,
) -> list[int]:
    """一个文件的收尾提交：覆盖行 + 文件台账 + 候选提案 + 扫描计数，**同一个事务**。

    只有 covered / reviewed_no_change 会带 payloads（候选在这里落库）；partial / failed
    只留一行「这次为什么没成」，什么都不假装。事务失败就整个文件回滚，别的文件不受牵连。
    """
    if meta is None:
        meta = {
            "content_hash": progress.baseline_hash,
            "size": progress.size,
            "modified_at": progress.modified_at,
        }
    error = _scrub(error, state.root) if error else None
    stamp = now_iso()
    proposal_ids: list[int] = []
    with atomic(state.conn):
        file_id = upsert_knowledge_file(state.conn, state.root.id, progress.relative_path, meta)
        state.conn.execute(
            "INSERT INTO knowledge_coverage"
            " (scan_id, file_id, content_hash, requirements_version, status, read_calls,"
            "  chars_read, candidate_count, error, created_at, finished_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                state.scan_id,
                file_id,
                meta["content_hash"],
                state.requirements_version,
                status,
                progress.read_calls,
                progress.chars_read,
                len(payloads or []),
                error,
                stamp,
                stamp,
            ),
        )
        for payload in payloads or []:
            proposal_ids.append(
                ledger.create_active(
                    state.conn,
                    "proposal",
                    {
                        "kind": KIND_PROFILE_CHANGE,
                        "payload": json.dumps(payload, ensure_ascii=False),
                        "reason": (
                            f"知识库扫描（{state.root.alias}）："
                            f"来自 {progress.relative_path} 的档案候选"
                        ),
                    },
                    actor="agent",
                )
            )
        state.conn.execute(
            "UPDATE knowledge_scan SET read_calls = read_calls + ?, chars_read = chars_read + ?,"
            " files_considered = files_considered + 1, candidates_created = candidates_created + ?"
            " WHERE id = ?",
            (progress.read_calls, progress.chars_read, len(payloads or []), state.scan_id),
        )
    return proposal_ids


# ---------- 单个文件的完整处理 ----------

_CHANGED_MESSAGE = (
    "文件在列目录之后被改动（changed_during_read）：这次不推进覆盖账，下次重扫会重新处理"
)


def _process_file(
    state: ScanState,
    entry: dict[str, Any],
    *,
    provider_id: int | None = None,
    model: str | None = None,
    transport: llm.Transport | None = None,
) -> FileOutcome:
    """处理一个文件：提炼循环 → 确定性校验 → 原子提交。每一步失败都只留诚实的一行。"""
    progress = FileProgress(
        relative_path=str(entry["relative_path"]),
        baseline_hash=str(entry["content_hash"]),
        size=int(entry["size"]),
        modified_at=entry.get("modified_at"),
    )
    outcome = FileOutcome(relative_path=progress.relative_path)

    try:
        output, problem = _distill_file(
            state, progress, provider_id=provider_id, model=model, transport=transport
        )
    except llm.LlmError as error:
        outcome.error = _scrub(f"调用模型失败：{error}", state.root)
        outcome.abort_scan = True  # 模型这条通路出了问题，后面的文件这次也做不了
        _commit_file(state, progress, status=FILE_FAILED, error=outcome.error)
        return outcome
    except _FileChanged:
        outcome.error = _CHANGED_MESSAGE
        _commit_file(state, progress, status=FILE_FAILED, error=outcome.error)
        return outcome

    if output is None:  # 结构不合格（重说一次仍不行）
        outcome.error = problem
        _commit_file(state, progress, status=FILE_FAILED, error=problem)
        return outcome

    outcome.model_gaps = list(output.unread_gaps)

    # 提交前再核一次内容哈希：列目录之后文件被改（含读到一半才改）都不推进覆盖账。
    fresh = _file_meta(state.root.id, progress.relative_path)
    if fresh is None or str(fresh["content_hash"]) != progress.baseline_hash:
        outcome.error = _CHANGED_MESSAGE
        _commit_file(
            state, progress, status=FILE_FAILED, error=outcome.error, meta=fresh or None
        )
        return outcome

    if not progress.fully_read:
        outcome.status = FILE_PARTIAL
        total = progress.total_chars if progress.total_chars is not None else "未知"
        outcome.error = (
            f"没有读完整（连续读到第 {progress.covered_chars} 字符，全文 {total} 字符）："
            "不落候选、不记覆盖，下次重扫"
        )
        _commit_file(state, progress, status=FILE_PARTIAL, error=outcome.error, meta=fresh)
        return outcome

    landed: list[dict[str, Any]] = []
    evidence_failures = 0
    first_evidence_failure: str | None = None
    for card in output.cards:
        payload, why, kind = _validate_card(state, card, progress)
        if payload is None:
            outcome.dropped.append({"why": str(why)})
            if kind != "duplicate":  # 完全重复被拦不算提炼错；其余的算出处校验失败
                evidence_failures += 1
                if first_evidence_failure is None:
                    first_evidence_failure = str(why)
            continue
        landed.append(payload)

    if evidence_failures:
        # §7.4：出处校验只要有一张卡没过，覆盖账就不推进——哪怕其余的卡都过了。已过
        # 校验的卡这次也不落：账要么整笔干净、要么整笔重来，不留「半真半假」的版本，
        # 也免得下次重扫把同样的卡再落一遍、收件箱里出现两条一模一样的提案。
        outcome.status = FILE_FAILED
        outcome.error = (
            f"提炼出的 {len(output.cards)} 张卡里有 {evidence_failures} 张没有通过确定性校验"
            f"（{first_evidence_failure}）；出处校验失败，覆盖账不推进"
            + (
                f"，已通过校验的 {len(landed)} 张这次也不落，下次重扫一并重新提炼"
                if landed
                else ""
            )
        )
        _commit_file(state, progress, status=FILE_FAILED, error=outcome.error, meta=fresh)
        return outcome

    # 出处校验全过：有卡落库＝covered；没提卡、或提的卡全被「完全重复」拦下＝
    # 提炼本身成立，结论是「这个版本没有新东西」。
    status = FILE_COVERED if landed else FILE_REVIEWED

    proposal_ids = _commit_file(
        state, progress, status=status, payloads=landed, meta=fresh
    )
    outcome.status = status
    outcome.candidates = len(proposal_ids)
    outcome.proposal_ids = proposal_ids
    return outcome


# ---------- 扫描入口 ----------


def _plan_files(
    state: ScanState, *, mode: str, scope: str, paths: list[str] | None
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """列目录 + 取每个文件的基线哈希；增量模式下跳过这个版本已经处理完的文件。

    返回（待处理文件, 已跳过, 缺口）。基线哈希在这里取：之后文件再变就是
    changed_during_read。scope=paths 点名的文件读不了要明确报错——那是显式指派。
    """
    conn, root = state.conn, state.root
    if scope == "paths":
        wanted: list[str] = []
        for raw in paths or []:
            resolved = safe_relative_path(root.id, str(raw))  # 越界 / 绝对路径：当场报错
            if resolved.relative_path not in wanted:
                wanted.append(resolved.relative_path)
        wanted.sort()
    else:
        wanted = [str(item["relative_path"]) for item in list_files(root.id)]

    entries: list[dict[str, Any]] = []
    skipped: list[str] = []
    gaps: list[str] = []
    for rel in wanted:
        meta = _file_meta(root.id, rel)
        if meta is None:
            if scope == "paths":
                raise KnowledgeNotFound(f"点名的文件不存在或读不了：{rel}")
            gaps.append(f"{rel}：这次读不了（可能刚被移动、删除或改成不支持的编码）")
            continue
        prior = conn.execute(
            "SELECT content_hash FROM knowledge_file WHERE root_id = ? AND relative_path = ?",
            (root.id, rel),
        ).fetchone()
        if _already_covered(conn, root.id, rel, meta["content_hash"], state.requirements_version):
            note = "已覆盖"
        elif prior is not None and str(prior["content_hash"]) != meta["content_hash"]:
            note = "内容有更新"
        else:
            note = "未覆盖"
        state.coverage_notes[rel] = note
        if note == "已覆盖" and mode == "incremental":
            skipped.append(rel)
            continue
        entries.append(meta)
    return entries, skipped, gaps


def scan(
    conn: sqlite3.Connection,
    root_id: int,
    *,
    trigger: str = "manual",
    mode: str = "incremental",
    scope: str = "all",
    paths: list[str] | None = None,
    requirements_version: str = REQUIREMENTS_VERSION_DEFAULT,
    provider_id: int | None = None,
    model: str | None = None,
    transport: llm.Transport | None = None,
) -> dict[str, Any]:
    """扫一个知识库根：列目录 → 逐文件受限提炼 → 覆盖账与候选（只产候选，不写档案）。

    状态口径（§7.4、§10.4）：全部处理完 = ok；读取配额触顶或有文件没做成（没读完、
    跳过）但有文件成功 = partial；任何文件失败（模型失败、输出不合格、文件被改、
    出处校验失败）= failed——已成功的文件照常保留覆盖，绝不回滚。
    `llm.LlmError` 在这里消化成 failed，不往上抛（接口层不用见它）。
    """
    if trigger not in TRIGGERS:
        raise KnowledgeError(f"扫描触发「{trigger}」不认识，只能给：{' / '.join(TRIGGERS)}")
    # 名字以授权契约（§6.7）为准：mode = incremental / rescan、scope = all / changed / selected。
    # 旧写法 full / paths 继续收，省得已经写下的调用方跟着改；changed 在默认增量模式下与 all
    # 等价（覆盖账本就按「相对路径+内容哈希+需求版本」跳过没变的文件），保留这个名字只为对齐契约。
    mode = {"rescan": "full"}.get(str(mode), str(mode))
    if mode not in ("incremental", "full"):
        raise KnowledgeError(
            f"扫描模式「{mode}」不认识：incremental（跳过已覆盖）或 rescan / full（全部重扫）"
        )
    scope = {"selected": "paths", "changed": "all"}.get(str(scope), str(scope))
    if scope not in ("all", "paths"):
        raise KnowledgeError("扫描范围 scope 只能给 all / changed（整个根）或 selected / paths（点名文件）")
    if scope == "paths":
        named = sorted({str(item).strip() for item in (paths or []) if str(item).strip()})
        if not named:
            raise KnowledgeError("scope=paths 要点名文件：给 paths（相对路径列表）")
    else:
        named = None
    version = str(requirements_version or "").strip() or REQUIREMENTS_VERSION_DEFAULT
    root = _usable_root(root_id, None)
    state = ScanState(conn=conn, root=root, scan_id=0, requirements_version=version)

    # 列目录与点名文件的校验放在扫描行落库**之前**：参数错误当场报，不留一条假 running。
    entries, skipped, gaps = _plan_files(state, mode=mode, scope=scope, paths=named)

    scan_id = int(
        conn.execute(
            "INSERT INTO knowledge_scan"
            " (root_id, trigger, requirements_version, status, quality_status, created_at)"
            " VALUES (?, ?, ?, 'running', ?, ?)",
            (root.id, trigger, version, QUALITY_PENDING, now_iso()),
        ).lastrowid
    )
    conn.commit()
    state.scan_id = scan_id

    results: list[FileOutcome] = []
    abort = False
    fatal: str | None = None
    try:
        for entry in entries:
            rel = str(entry["relative_path"])
            if abort:
                gaps.append(f"{rel}：模型调用失败，这次没有处理")
                continue
            if state.read_calls >= MAX_READ_CALLS or state.chars_read >= MAX_TOTAL_CHARS:
                gaps.append(
                    f"{rel}：读取配额（{MAX_READ_CALLS} 次文件 / {MAX_TOTAL_CHARS} 字符）已用完，"
                    "这次没有处理"
                )
                continue
            outcome = _process_file(
                state, entry, provider_id=provider_id, model=model, transport=transport
            )
            results.append(outcome)
            # 没有处理完的文件就是缺口：partial / failed 的原因如实写进 unread_gaps（§7.1 第 8 条）。
            if outcome.status not in COVERAGE_DONE and outcome.error:
                gaps.append(f"{rel}：{outcome.error}")
            gaps.extend(f"{rel}：{gap}" for gap in outcome.model_gaps)
            abort = outcome.abort_scan
    except Exception as error:  # 兜底：扫描行绝不能停在 running，也不该让接口变成 500
        # `_process_file` 只接 LlmError 与 _FileChanged；台账 / 事务层没预料到的错误原来会
        # 直接穿出去，把这一行永远留在 running。这里如实记 failed，按业务失败返回。
        fatal = _scrub(f"扫描中断：{type(error).__name__}: {error}", root)
        gaps.append(f"扫描中断：{fatal}")

    if fatal is not None:
        status = "failed"
        error = fatal
    else:
        failed = [item for item in results if item.status == FILE_FAILED]
        if failed:
            status = "failed"
        elif [item for item in results if item.status == FILE_PARTIAL] or gaps:
            status = "partial"
        else:
            status = "ok"
        error = "；".join(item.error for item in failed if item.error) or None

    conn.execute(
        "UPDATE knowledge_scan SET status = ?, error = ?, finished_at = ? WHERE id = ?",
        (status, None if error is None else _scrub(error, root), now_iso(), scan_id),
    )
    conn.commit()

    return {
        "scan_id": scan_id,
        "root_id": root.id,
        "root_alias": root.alias,
        "trigger": trigger,
        "requirements_version": version,
        "status": status,
        "quality_status": QUALITY_PENDING,
        "read_calls": state.read_calls,
        "chars_read": state.chars_read,
        "files_considered": len(results),
        "candidates_created": sum(item.candidates for item in results),
        "files": [
            {
                "relative_path": item.relative_path,
                "status": item.status,
                "error": item.error,
                "candidates": item.candidates,
                "dropped": item.dropped,
                "model_gaps": item.model_gaps,
            }
            for item in results
        ],
        "skipped_already_covered": skipped,
        "unread_gaps": gaps,
        "error": error,
    }


def list_scans(
    conn: sqlite3.Connection, limit: int = 20, root_id: int | None = None
) -> list[dict[str, Any]]:
    """扫描历史，新的在前；`root_id` 给了就只看那个根的。

    根别名现查现配——根被移出配置后历史仍可读，别名留空。
    只有账面字段（读了多少、出了几条候选、为什么失败），不含任何原文（§6.7）。
    """
    aliases = {item.id: item.alias for item in configured_roots()}
    if root_id is None:
        rows = conn.execute(
            "SELECT * FROM knowledge_scan ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM knowledge_scan WHERE root_id = ? ORDER BY id DESC LIMIT ?",
            (root_id, limit),
        ).fetchall()
    return [
        {
            "scan_id": int(row["id"]),
            "root_id": int(row["root_id"]),
            "root_alias": aliases.get(int(row["root_id"])),
            "trigger": str(row["trigger"]),
            "requirements_version": str(row["requirements_version"]),
            "status": str(row["status"]),
            "read_calls": int(row["read_calls"]),
            "chars_read": int(row["chars_read"]),
            "files_considered": int(row["files_considered"]),
            "candidates_created": int(row["candidates_created"]),
            "quality_status": row["quality_status"],
            "error": row["error"],
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
        }
        for row in rows
    ]


def file_listing(
    conn: sqlite3.Connection,
    root_id: int,
    prefix: str = "",
    requirements_version: str = REQUIREMENTS_VERSION_DEFAULT,
    roots: Sequence[Root] | None = None,
) -> dict[str, Any]:
    """一个根的文件清单**对外视图**（KB-04，方案 §6.7）：根只有编号与别名，文件只有
    相对路径、大小、修改时间与覆盖状态——绝对路径不出现在任何字段里。

    `covered` 按**当前最新覆盖账**算：文件的当前内容（现算哈希）在这个需求口径版本下
    已有 covered / reviewed_no_change 的覆盖行才算数——文件改过就不算，重扫会重新处理它。
    """
    root = _usable_root(root_id, roots)
    files: list[dict[str, Any]] = []
    for item in list_files(root_id, prefix, roots):
        rel = str(item["relative_path"])
        try:
            current_hash = hash_file(safe_relative_path(root_id, rel, roots).absolute)
        except (KnowledgeError, OSError):
            current_hash = ""  # 列目录之后读不了了：按没覆盖算，重扫见分晓
        files.append(
            {
                "relative_path": rel,
                "size": item["size"],
                "modified_at": item["modified_at"],
                "covered": bool(current_hash)
                and _already_covered(conn, root.id, rel, current_hash, requirements_version),
            }
        )
    return {"root": {"id": root.id, "alias": root.alias}, "files": files}


# ============================================================
# 三、批准侧（KB-03 写入侧，方案 §5.2 末段）
# ============================================================
# 扫描只产候选（kind=profile_change 的提案，出处是 root_alias + relative_path + 摘录）；
# 批准时由 `proposals.decide` 走这里：先**再验一次**摘录仍在当前文件里（文件改了就 409，
# 绝不静默套用扫描时的旧假设），再在批准事务里把文件台账与 memory_evidence 落列——
# 档案写入、证据写入、提案终态三件事同一事务完成。原文件本身只读，永不改写。


def verify_evidence(item: dict[str, Any]) -> dict[str, Any]:
    """批准前再验一条知识库出处（**纯只读**，不碰库）：文件还在、摘录还能逐字找到。

    从落候选到批准之间文件完全可能已经改过——摘录找不到了就抛 `KnowledgeConflict`，
    调用方翻成 409 让提案留在待裁定，而不是照着旧假设把档案写了。返回落
    memory_evidence 需要的元数据（路径 / 重算的行号 / 当前版本哈希）。
    """
    relative_path = str(item.get("relative_path") or "").strip()
    excerpt = str(item.get("excerpt") or "").strip()
    if not relative_path or not excerpt:
        raise KnowledgeConflict("这条出处缺了相对路径或摘录，没法核对原文")
    root_alias = str(item.get("root_alias") or "").strip()
    roots = configured_roots()
    root = next((r for r in roots if r.enabled and r.alias == root_alias), None)
    if root is None:
        raise KnowledgeConflict(
            f"知识库根目录「{root_alias}」不在当前配置里（或已停用），摘录无从核对——"
            "先把根目录配回来，或重新扫描产出新候选"
        )
    resolved = safe_relative_path(root.id, relative_path, roots)
    path = resolved.absolute
    if not path.exists():
        raise KnowledgeConflict(f"文件已经不在了：{resolved.relative_path}")
    try:
        data = path.read_bytes()
    except OSError:
        raise KnowledgeConflict(f"文件现在读不了：{resolved.relative_path}") from None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise KnowledgeConflict(f"文件现在不是有效的 UTF-8 文本：{resolved.relative_path}") from None
    index = text.find(excerpt)
    if index < 0:
        raise KnowledgeConflict(
            f"摘录在 {resolved.relative_path} 的当前内容里已经找不到了——"
            "文件在批准前被改过。重新扫描让它产出对得上原文的新候选，再决定写不写"
        )
    line_start = text.count("\n", 0, index) + 1
    try:
        modified_at = (
            datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")
        )
    except OSError:
        modified_at = None
    return {
        "root_id": root.id,
        "root_alias": root.alias,
        "relative_path": resolved.relative_path,
        "excerpt": excerpt,
        # 行号按**当前**文件重算：文件即使改了（摘录还在），证据记的行号也要对得上现在
        "line_start": line_start,
        "line_end": line_start + excerpt.count("\n"),
        "content_hash": hash_bytes(data),
        "size": len(data),
        "modified_at": modified_at,
    }


def register_evidence(
    conn: sqlite3.Connection, verified: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """把验过的出处登记进文件台账，并拼出 memory_evidence 的落库字段。

    必须在**批准事务里**调用：`knowledge_file` 台账行（登记 / 更新到验过的版本）与
    档案、证据、提案终态落在同一个事务。`source_id` 挂文件台账编号——证据能按编号
    反查到它是哪个根下的哪个文件。
    """
    rows: list[dict[str, Any]] = []
    for item in verified:
        file_id = upsert_knowledge_file(
            conn,
            int(item["root_id"]),
            str(item["relative_path"]),
            {
                "content_hash": item["content_hash"],
                "size": item["size"],
                "modified_at": item["modified_at"],
            },
        )
        rows.append(
            {
                "source_type": memory.KNOWLEDGE_SOURCE_TYPE,
                "source_id": file_id,
                "excerpt": str(item["excerpt"]),
                "source_time": str(item["modified_at"])[:10] if item["modified_at"] else None,
                "knowledge_file_id": file_id,
                "content_hash": item["content_hash"],
                "relative_path": str(item["relative_path"]),
                "line_start": int(item["line_start"]),
                "line_end": int(item["line_end"]),
            }
        )
    return rows

