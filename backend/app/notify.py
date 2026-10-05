"""触达通道：每周那一次兜底提醒（U2 档 2 —— 本地定时 + 邮件，不带链接，含开机补发）。

分工：
- **发什么**（三问的排版、没动静时的降频与改口）—— 这个模块；
- **该不该发**（本周问过没、上一周漏没漏）—— 也是这个模块判定，`app/jobs/weekly_checkpoint.py` 负责触发；
- **怎么送到你面前** —— `Notifier` 两个实现：SMTP 邮件与空实现（本地开发用，只记账不发信）；
- **发过的痕迹** —— `notification_log` 一行一条，`plan._weekly_already_asked` 读的也是它。

为什么内容组装单独抽出来：三问的原料（当前阶段 / 本周推到哪 / 建议怎么调）在
`plan.weekly_status` 里已经算好了，这里只负责**排版成人话**——以后升到档 3（云端常驻）
换的只是「跑在哪、邮件里能不能放链接」，这一段一个字都不用改（`docs/U2-触达详解.md` 三）。

为什么开关与收件邮箱落库、SMTP 密钥走环境变量：前者是给你在设置页拧的旋钮（改完即生效），
后者是密钥——按 SPEC 第 16 节只允许放 `.env`，绝不出现在界面回显里。
"""

from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from email.message import EmailMessage
from typing import Any, Protocol

import sqlite3

from . import config, db, plan

# 设置项的两个键（`app_setting` 表）。用「谁.什么」的写法，免得以后加设置时撞名。
SETTING_ENABLED = "notify.enabled"
SETTING_TO = "notify.to"

# SMTP 密钥只从环境变量读（.env 载入），名字固定这四个。缺一个就退回空实现。
ENV_SMTP_HOST = "CADENCE_SMTP_HOST"
ENV_SMTP_PORT = "CADENCE_SMTP_PORT"
ENV_SMTP_USER = "CADENCE_SMTP_USER"
ENV_SMTP_PASSWORD = "CADENCE_SMTP_PASSWORD"

# 每周一次的时点：周一（ISO 周的第一天，和 `plan.week_key` / 导出文件名同一套口径）+ 09:00。
# 做成常量而不是设置项：改它的收益很低，而「定时任务几点跑」将来归 Windows 任务计划管。
WEEKLY_WEEKDAY = 0
WEEKLY_TIME = time(9, 0)

# 没动静的分档（U2 第四节）：连续 2 周无报告 → 第二封改性质；连续 4 周 → 自动降频为每两周一次。
QUIET_WEEKS_REPHRASE = 2
QUIET_WEEKS_DOWNGRADE = 4


class NotifyError(Exception):
    """触达配置层面的明确错误（进了不该进的值），接口层翻成 400。"""


# ---------- 设置项（app_setting 的读写） ----------

def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM app_setting WHERE key = ?", (key,)).fetchone()
    return default if row is None or row["value"] is None else str(row["value"])


def set_setting(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    """写一个设置项。

    不走台账：台账管的是**业务对象的生命周期**（决策 22 那张清单），而这是「界面上的旋钮」，
    既没有业务终态、也不该在流水里被当成一件事。时间戳记在表里，改了什么直接看得到。
    """
    conn.execute(
        """INSERT INTO app_setting (key, value, updated_at) VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
        (key, value, db.now_iso()),
    )
    conn.commit()


def read_config(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "enabled": get_setting(conn, SETTING_ENABLED, "0") == "1",
        "to_addr": get_setting(conn, SETTING_TO),
    }


def update_config(
    conn: sqlite3.Connection, *, enabled: bool | None = None, to_addr: str | None = None
) -> dict[str, Any]:
    """改设置——只认传进来的字段。开启前必须先有收件邮箱，否则会「开着但发不出去」。

    **先把所有值验完再落库**：验不过就一行都不写，免得出现「邮箱被清掉了、开关还开着」
    这种半截状态（同一句话里的两件事要么都成，要么都不成）。
    """
    cleaned: str | None = None
    if to_addr is not None:
        cleaned = to_addr.strip() or None
        if cleaned and not _looks_like_address(cleaned):
            raise NotifyError(f"收件邮箱「{cleaned}」看起来不是一个邮箱地址")
    if enabled:
        effective = cleaned if to_addr is not None else read_config(conn)["to_addr"]
        if not effective:
            raise NotifyError("先把收件邮箱填上，再打开每周提醒")
    if to_addr is not None:
        set_setting(conn, SETTING_TO, cleaned)
    if enabled is not None:
        set_setting(conn, SETTING_ENABLED, "1" if enabled else "0")
    return read_config(conn)


def _looks_like_address(value: str) -> bool:
    """够用的邮箱形状检查。

    不引 `email-validator`（那是加依赖，SPEC 第 16 节要求先问）：真的有效性只有发出去才知道，
    这里只拦明显不是邮箱的东西（漏了 @ 或域名里没有点、带了空格换行）。
    """
    if len(value) > 254 or any(character.isspace() for character in value):
        return False
    local, _, domain = value.partition("@")
    return bool(local) and "." in domain and not domain.startswith(".") and not domain.endswith(".")


# ---------- 通道：邮件实现 / 空实现 ----------

@dataclass(frozen=True)
class SendResult:
    ok: bool
    channel: str
    detail: str = ""


class Notifier(Protocol):
    channel: str

    def send(self, subject: str, body: str, to_addr: str) -> SendResult: ...


class NullNotifier:
    """空实现（本地开发用）：什么都不发，只回一句「没配通道」。

    为什么仍然算成功：这条路径本身没出错——邮件实现在拿到授权码之前，链路要能照常跑完
    （判定、组装、记账、导出四步都验证得到）。日志里 `channel='none'` 一眼看得出没真发。
    """

    channel = "none"

    def send(self, subject: str, body: str, to_addr: str) -> SendResult:
        return SendResult(ok=True, channel=self.channel, detail="空实现：没配 SMTP，这次只记账没发信")


class SmtpNotifier:
    """邮件实现：`smtplib` 直发，不引入任何第三方依赖。

    端口口径：465 走 SSL（QQ / 163 的常见档），其余端口先连再 STARTTLS，
    服务器不支持 TLS 时按明文继续——本地自建的中继常常没有证书。
    """

    channel = "email"

    def __init__(self, host: str, port: int, user: str, password: str, sender: str | None = None):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.sender = sender or user

    def send(self, subject: str, body: str, to_addr: str) -> SendResult:
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = to_addr
        message["Subject"] = subject
        message.set_content(body)
        try:
            server = (
                smtplib.SMTP_SSL(self.host, self.port, timeout=20)
                if self.port == 465
                else smtplib.SMTP(self.host, self.port, timeout=20)
            )
            with server:
                if self.port != 465:
                    try:
                        server.starttls()
                    except smtplib.SMTPNotSupportedError:
                        pass
                if self.user:
                    server.login(self.user, self.password)
                server.send_message(message)
        except (OSError, smtplib.SMTPException) as error:
            return SendResult(ok=False, channel=self.channel, detail=f"发送失败：{error}")
        return SendResult(ok=True, channel=self.channel, detail=f"已发送到 {to_addr}")


def smtp_status() -> dict[str, Any]:
    """SMTP 配置到什么程度了——只报「哪些环境变量还缺」，**不回显任何值**。"""
    missing = [
        name for name in (ENV_SMTP_HOST, ENV_SMTP_USER, ENV_SMTP_PASSWORD)
        if not os.environ.get(name)
    ]
    return {"configured": not missing, "missing": missing}


def notifier_from_env() -> Notifier:
    """有 SMTP 配置就用邮件，没有就退回空实现——缺授权码不该拦住整条链路。"""
    state = smtp_status()
    if not state["configured"]:
        return NullNotifier()
    try:
        port = int(os.environ.get(ENV_SMTP_PORT) or 465)
    except ValueError:
        port = 465
    return SmtpNotifier(
        host=str(os.environ[ENV_SMTP_HOST]),
        port=port,
        user=str(os.environ[ENV_SMTP_USER]),
        password=str(os.environ[ENV_SMTP_PASSWORD]),
    )


# ---------- 三问的排版（U2 样例） ----------

def _frontend_hint() -> str:
    """回哪儿更新——纯文本写地址，**不做可点链接**（手机点开 127.0.0.1 是空的，U2 档 2）。"""
    return f"回电脑上打开 http://localhost:{config.FRONTEND_PORT} 更新"


def reportless_weeks(
    conn: sqlite3.Connection, plan_id: int, today: date, created: date | None = None, limit: int = 12
) -> int:
    """从上一周往回数：连续多少周一份报告都没有。

    本周不算——它还没过完，拿「至今没有报告」去催你是不公平的。数到有报告的那周或
    计划建立之前为止（`limit` 是防呆上限，免得老计划一路数到天荒地老）。
    """
    count = 0
    cursor = plan.week_bounds(today)[0]
    for _ in range(limit):
        end = cursor - timedelta(days=1)
        start = end - timedelta(days=6)
        if created is not None and end < created:
            break
        if plan.reports_between(conn, plan_id, start, end):
            break
        count += 1
        cursor = start
    return count


def _asked_between(conn: sqlite3.Connection, start: date, end: date) -> bool:
    """这一周有没有发过（正常发与补发都算——补发过就不该再补一次）。"""
    for row in conn.execute("SELECT sent_at FROM notification_log"):
        day = plan.parse_date(row["sent_at"])
        if day is not None and start <= day <= end:
            return True
    return False


def _stage_line(status: dict[str, Any]) -> str:
    stage = status.get("current_stage")
    if not stage:
        return "当前阶段：还没有进行中的阶段"
    deliverable = stage.get("deliverable") or "（这个阶段还没写要交什么）"
    return f"当前阶段：{stage['title']}\n          交付物：{deliverable}"


def _progress_line(status: dict[str, Any]) -> str:
    """本周推到哪：报告与落后量都摆出来，不挑好听的说。"""
    reports = status.get("reports_this_week") or []
    parts: list[str] = []
    if reports:
        latest = reports[-1]
        parts.append(f"本周交了 {len(reports)} 份报告，最新一份：「{latest.get('note') or '没写说明'}」")
    else:
        parts.append("本周还没有报告")
    progress = status.get("stage_progress") or {}
    if progress.get("total"):
        parts.append(f"阶段里的任务 {progress['settled']}/{progress['total']} 已收尾")
    if status.get("behind_reason"):
        parts.append(status["behind_reason"])
    return "本周推到哪：" + "；".join(parts)


def _advice_block(status: dict[str, Any], quiet_weeks: int) -> str:
    """建议怎么调——三个方向是**选项**不是决定，最终由你裁定（SPEC 第 8 节铁律）。"""
    options = status.get("replan_options") or []
    if quiet_weeks >= QUIET_WEEKS_REPHRASE:
        head = "建议怎么调：连续几周没动静了，先看看这个阶段是不是要改小或者停掉——"
    elif not options:
        head = "建议怎么调：这周没什么要调的，照原计划走。"
        return head
    else:
        head = "建议怎么调（三选一，回页面里改）："
    lines = [f"  · {option['label']}：{option['detail']}" for option in options]
    return "\n".join([head, *lines])


def _outcome_line(conn: sqlite3.Connection, status: dict[str, Any]) -> str | None:
    """成果怎么看（OC-09）：契约标题 + 验收缺口条数 + 最新复盘卡结论一句。

    周提醒只是把确定性算好的事实念一遍——不调模型、不做任何判定；
    没有契约的计划照实说「还没有成果契约」，legacy 计划不被打扰成要补契约。
    """
    plan_id = status.get("plan_id")
    if not plan_id:
        return None
    snapshot = plan.outcome_snapshot(conn, int(plan_id))
    if snapshot["has_contract"]:
        head = f"成果契约《{snapshot['contract_title']}》，验收缺口 {snapshot['acceptance_gaps']} 条"
    else:
        head = "还没有成果契约——验收与完成都挂在契约上"
    return f"成果怎么看：{head}；复盘卡：{snapshot['review_card_conclusion']}"


def compose_weekly(
    conn: sqlite3.Connection,
    status: dict[str, Any],
    *,
    today: date | None = None,
    makeup_week: str | None = None,
) -> tuple[str, str]:
    """把 `plan.weekly_status` 的原料排成一封人话邮件，返回 (主题, 正文)。"""
    today = today or date.today()
    plan_id = status.get("plan_id")
    created = _plan_created(conn, plan_id) if plan_id else None
    quiet_weeks = reportless_weeks(conn, plan_id, today, created) if plan_id else 0

    stage = status.get("current_stage")
    stage_label = f"（{stage['title']}）" if stage else ""
    subject = f"cadence · {'补发 · ' if makeup_week else ''}周检查点{stage_label}"

    lines: list[str] = []
    if makeup_week:
        lines.append(f"（补发）上周（{makeup_week}）的检查点漏了一次——那几天电脑没开着，这次一起补上。")
        lines.append("")
    outcome = _outcome_line(conn, status)
    lines += [_stage_line(status), "", _progress_line(status)]
    if outcome:
        lines += ["", outcome]
    lines += ["", _advice_block(status, quiet_weeks)]
    if quiet_weeks >= QUIET_WEEKS_DOWNGRADE:
        lines += ["", f"这个计划已经连续 {quiet_weeks} 周没有更新，提醒先降为每两周一次。", "这个计划可能已经不合适了，要不要重新看一次。"]
    lines += ["", _frontend_hint()]
    return subject, "\n".join(lines)


def _plan_created(conn: sqlite3.Connection, plan_id: int) -> date | None:
    row = conn.execute("SELECT valid_from FROM plan WHERE id = ?", (plan_id,)).fetchone()
    return None if row is None else plan.parse_date(row["valid_from"])


# ---------- 该不该发 ----------

def next_send_at(now: datetime | None = None) -> str:
    """下一次「周一 09:00」是哪一刻（今天就是周一且还没到点 → 就是今天）。"""
    now = now or datetime.now()
    days_ahead = (WEEKLY_WEEKDAY - now.weekday()) % 7
    candidate = datetime.combine((now + timedelta(days=days_ahead)).date(), WEEKLY_TIME)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate.isoformat(sep=" ", timespec="minutes")


def missed_week(
    conn: sqlite3.Connection, plan_id: int, today: date, created: date | None = None
) -> str | None:
    """上一周的检查点漏了没——漏了就返回那一周的编号（`2026-W38`），没漏返回 None。

    为什么必须有这一步：Windows 任务计划只在电脑开机时才跑，那个时点电脑关着就漏了。
    补发不是「多催一次」——检查点本来就是每周一次，漏掉的那次补上才算没断。
    """
    start = plan.week_bounds(today)[0]
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=6)
    # 计划必须是「上一周开始之前就存在」才算漏——上个星期中途才建的计划，前半个星期
    # 本来就不该被问，把它记成漏会让第一封（甚至每封新计划的第一封）都顶着「补发」。
    if created is not None and created > prev_start:
        return None
    if _asked_between(conn, prev_start, prev_end):
        return None
    return plan.week_key(prev_start)


def decide(
    conn: sqlite3.Connection, status: dict[str, Any], today: date, prefs: dict[str, Any] | None = None
) -> dict[str, Any]:
    """这一封发不发、以什么名义发。判定与发信分开，是为了让 `--dry-run` 能问出同一套答案。

    顺序上先看漏没漏、再看本周问没问：一周最多一封——上一周漏了就把它并进这一封，
    标题写「补发」；两周各发一封等于连催两次，那不是跟进，是噪音（U2 第四节的原则）。
    """
    prefs = prefs or read_config(conn)
    if not prefs["enabled"]:
        return {"send": False, "kind": None, "reason": "每周提醒没打开"}
    if not prefs["to_addr"]:
        return {"send": False, "kind": None, "reason": "还没填收件邮箱"}
    plan_id = status.get("plan_id")
    if not plan_id:
        return {"send": False, "kind": None, "reason": "还没有计划，没什么可检查的"}

    created = _plan_created(conn, plan_id)
    quiet = reportless_weeks(conn, plan_id, today, created)
    if quiet >= QUIET_WEEKS_DOWNGRADE and today.isocalendar().week % 2 == 1:
        return {
            "send": False, "kind": None,
            "reason": f"连续 {quiet} 周没更新，已降频为每两周一次（这周不发）",
        }
    # 降频期间不再追究「上周漏没漏」：那一周本来就轮空，不是漏。
    missed = None if quiet >= QUIET_WEEKS_DOWNGRADE else missed_week(conn, plan_id, today, created)
    asked_this_week = _asked_between(conn, *plan.week_bounds(today))
    if asked_this_week and missed is None:
        return {"send": False, "kind": None, "reason": "本周已经问过了"}
    return {
        "send": True,
        "kind": "makeup" if missed else "weekly_checkpoint",
        "missed_week": missed,
        "reason": f"上周（{missed}）的检查点漏了，补发" if missed else "本周还没问过",
    }


def stamp_for(day: date) -> datetime:
    """「当成那一天」的时间戳：日期用给的那天、时刻用当下的时刻。

    为什么需要它：一周只发一封是靠 `notification_log.sent_at` 判的。按指定日期补做时
    若用真实时钟记账，那一行会落到别的周里，「这周问过没」立刻错位。
    """
    return datetime.combine(day, datetime.now().time())


def log_send(
    conn: sqlite3.Connection,
    *,
    kind: str | None,
    subject: str,
    body: str,
    result: SendResult,
    at: datetime | None = None,
) -> int:
    """把这次触达记进 `notification_log`——成功失败都记，失败连原因一起。

    `at` 只在「按指定日期补做」时给（验收与单测要对着固定的一天看结果）：不给就用此刻。
    这一列既是历史，也是「这周问过没」的判据，所以补做时必须和那一天的账对齐。
    """
    stamp = (at or datetime.now()).astimezone().isoformat(timespec="seconds")
    cursor = conn.execute(
        """INSERT INTO notification_log (channel, kind, subject, body, ok, error, sent_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (result.channel, kind, subject, body, 1 if result.ok else 0, result.detail or None, stamp),
    )
    conn.commit()
    return int(cursor.lastrowid or 0)


def deliver(
    conn: sqlite3.Connection,
    *,
    subject: str,
    body: str,
    kind: str | None,
    to_addr: str | None = None,
    notifier: Notifier | None = None,
    at: datetime | None = None,
) -> dict[str, Any]:
    """送到你的邮箱（或空实现），并记一行账。组装好的正文原样进日志，好对账。"""
    target = to_addr or read_config(conn)["to_addr"]
    if not target:
        raise NotifyError("还没有收件邮箱，发不出去")
    sender = notifier or notifier_from_env()
    result = sender.send(subject, body, str(target))
    log_id = log_send(conn, kind=kind, subject=subject, body=body, result=result, at=at)
    return {
        "sent": result.ok,
        "kind": kind,
        "channel": result.channel,
        "detail": result.detail,
        "notification_id": log_id,
    }


def send_weekly(
    conn: sqlite3.Connection,
    *,
    today: date | None = None,
    notifier: Notifier | None = None,
    at: datetime | None = None,
) -> dict[str, Any]:
    """判定 → 组装 → 发送 → 记账，四步一条链。任务脚本与将来的「立刻试发」都走它。"""
    today = today or date.today()
    status = plan.weekly_status(conn, today=today)
    decision = decide(conn, status, today)
    if not decision["send"]:
        return {"sent": False, **decision, "week": status["week"]}
    subject, body = compose_weekly(conn, status, today=today, makeup_week=decision.get("missed_week"))
    return {
        "week": status["week"],
        "missed_week": decision.get("missed_week"),
        "subject": subject,
        "body": body,
        **deliver(
            conn,
            subject=subject,
            body=body,
            kind=decision["kind"],
            notifier=notifier,
            at=at or stamp_for(today),
        ),
    }


def recent(conn: sqlite3.Connection, limit: int = 5) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM notification_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "channel": str(row["channel"]),
            "channel_label": "邮件" if row["channel"] == "email" else "空实现（未配置 SMTP）",
            "kind": row["kind"],
            "kind_label": {"weekly_checkpoint": "每周检查点", "makeup": "补发"}.get(
                str(row["kind"]), str(row["kind"])
            ),
            "subject": row["subject"],
            "ok": bool(row["ok"]),
            "error": row["error"],
            "sent_at": row["sent_at"],
        }
        for row in rows
    ]


def status(
    conn: sqlite3.Connection,
    plan_id: int | None = None,
    today: date | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """设置页那一节要的全部事实：开关、发到哪、下次什么时候、本周会发什么、发过什么。

    `now` 与 `today` 一样可注入：`next_send_at` 原来直接读墙上时钟，用例只能写死当天那一刻，
    一过点就红——把时刻收成一个可选参数，测试就能在任何日子跑。给了 `now` 就用它的日期：
    两个时钟只留一个来源，免得 `next_send_at` 与 `due`/`week` 出自不同的时钟、互相矛盾。
    """
    today = now.date() if now is not None else (today or date.today())
    prefs = read_config(conn)
    channel = smtp_status()
    weekly = plan.weekly_status(conn, today=today, plan_id=plan_id)
    subject, body = compose_weekly(conn, weekly, today=today)
    decision = decide(conn, weekly, today, prefs)
    notice = None
    if not prefs["enabled"]:
        notice = "每周提醒没打开——现在是「你不来，它不响」。"
    elif not prefs["to_addr"]:
        notice = "还没填收件邮箱，打开提醒前先填上。"
    elif not channel["configured"]:
        notice = "SMTP 还没配好，现在只会留一条记录、不会真发信（授权码填进后端环境变量后自动生效）。"
    return {
        "enabled": prefs["enabled"],
        "to_addr": prefs["to_addr"],
        "channel": "email" if channel["configured"] else "none",
        "smtp_configured": channel["configured"],
        "smtp_missing_env": channel["missing"],
        "week": weekly["week"],
        "plan_id": weekly["plan_id"],
        "due": decision["send"],
        "decision_reason": decision["reason"],
        "missed_week": decision.get("missed_week"),
        "next_send_at": next_send_at(now),
        "preview": {"subject": subject, "body": body},
        "notice": notice,
        "recent": recent(conn),
    }
