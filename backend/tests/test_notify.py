"""触达通道（T15）：三问的排版、空实现与邮件实现、开关与收件邮箱的校验。

时间一律用显式传入的固定值（`today=`），不读系统时钟——否则测试会随日期漂移。
"""

from __future__ import annotations

from datetime import date

import pytest

from app import db, ledger, notify, plan

TODAY = date(2026, 10, 5)  # 周一，ISO 第 41 周


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "test.db"
    db.init(path)
    connection = db.connect(path)
    yield connection
    connection.close()


def make_plan(conn, *, due_date: str | None = "2026-10-02", with_task: bool = True):
    plan_id = ledger.create_active(conn, "plan", {"goal": "Web 后端最小集"}, actor="user")
    stage_id = ledger.create_active(
        conn, "plan_node",
        {"plan_id": plan_id, "level": "stage", "title": "阶段 2",
         "deliverable": "接口能读写，数据落 SQLite", "sort_order": 0},
        actor="user")
    if with_task:
        ledger.create_active(
            conn, "plan_node",
            {"plan_id": plan_id, "parent_id": stage_id, "level": "task",
             "title": "接上 SQLite 读写", "due_date": due_date, "sort_order": 0},
            actor="user")
    return plan_id, stage_id


# ---------- 三问的组装（T15 的验收：内容组装有单测） ----------

def test_the_three_questions_come_from_the_weekly_status(conn):
    make_plan(conn)
    status = plan.weekly_status(conn, today=TODAY)
    subject, body = notify.compose_weekly(conn, status, today=TODAY)
    assert subject == "cadence · 周检查点（阶段 2）"
    assert "当前阶段：阶段 2" in body
    assert "交付物：接口能读写，数据落 SQLite" in body
    assert "本周还没有报告" in body
    assert "落后 3 天还没做完" in body          # 2026-10-02 到期，TODAY 是 10-05
    assert "建议怎么调" in body
    assert "减量" in body and "顺延" in body and "换交付物" in body
    assert "http://localhost:" in body          # 回电脑上打开哪儿——纯文本，不做可点链接


def test_a_report_this_week_shows_up_in_the_progress_line(conn):
    plan_id, stage_id = make_plan(conn)
    ledger.set_status(conn, "plan_node", stage_id, "in_progress", actor="user", reason="开工")
    conn.execute(
        "INSERT INTO report (node_id, status, note, created_at) VALUES (?, 'done', ?, ?)",
        (stage_id, "把读写的骨架搭起来了", db.now_iso()),
    )
    conn.commit()
    status = plan.weekly_status(conn, today=date.today())
    _, body = notify.compose_weekly(conn, status, today=date.today())
    assert "本周交了 1 份报告" in body
    assert "把读写的骨架搭起来了" in body


def test_a_plan_without_a_stage_still_composes(conn):
    ledger.create_active(conn, "plan", {"goal": "空计划"}, actor="user")
    status = plan.weekly_status(conn, today=TODAY)
    subject, body = notify.compose_weekly(conn, status, today=TODAY)
    assert subject == "cadence · 周检查点"
    assert "还没有进行中的阶段" in body


def test_a_quiet_plan_changes_its_tone_after_two_weeks(conn):
    make_plan(conn)
    status = plan.weekly_status(conn, today=date(2026, 10, 26))
    _, body = notify.compose_weekly(conn, status, today=date(2026, 10, 26))
    assert "连续几周没动静了" in body
    assert "改小或者停掉" in body


# ---------- 空实现与邮件实现 ----------

def test_the_null_channel_logs_instead_of_sending(conn, monkeypatch):
    monkeypatch.delenv(notify.ENV_SMTP_HOST, raising=False)
    monkeypatch.delenv(notify.ENV_SMTP_USER, raising=False)
    monkeypatch.delenv(notify.ENV_SMTP_PASSWORD, raising=False)
    make_plan(conn)
    notify.update_config(conn, to_addr="someone@example.com", enabled=True)

    result = notify.send_weekly(conn, today=TODAY)

    assert result["sent"] is True and result["channel"] == "none"
    rows = notify.recent(conn)
    assert len(rows) == 1
    assert rows[0]["channel"] == "none" and rows[0]["ok"] is True
    assert "没配 SMTP" in rows[0]["error"]
    assert rows[0]["kind"] == "weekly_checkpoint"


def test_smtp_implementation_builds_a_real_message(conn, monkeypatch):
    sent: list = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            self.host, self.port = host, port
            sent.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def login(self, user, password):
            self.credentials = (user, password)

        def send_message(self, message):
            self.message = message

    monkeypatch.setattr(notify.smtplib, "SMTP_SSL", FakeSMTP)
    sender = notify.SmtpNotifier(host="smtp.example.com", port=465, user="me@example.com", password="授权码")

    result = sender.send("cadence · 周检查点", "正文在这里", "someone@example.com")

    assert result.ok is True and result.channel == "email"
    message = sent[0].message
    assert message["To"] == "someone@example.com"
    assert message["Subject"] == "cadence · 周检查点"
    assert "正文在这里" in message.get_content()
    assert sent[0].credentials == ("me@example.com", "授权码")


def test_a_failing_smtp_reports_the_reason_instead_of_raising(conn, monkeypatch):
    class BrokenSMTP:
        def __init__(self, *args, **kwargs):
            raise OSError("连接被拒绝")

    monkeypatch.setattr(notify.smtplib, "SMTP_SSL", BrokenSMTP)
    sender = notify.SmtpNotifier(host="smtp.example.com", port=465, user="me@example.com", password="x")

    result = sender.send("主题", "正文", "someone@example.com")

    assert result.ok is False
    assert "连接被拒绝" in result.detail


def test_env_decides_which_channel_is_used(conn, monkeypatch):
    monkeypatch.delenv(notify.ENV_SMTP_HOST, raising=False)
    monkeypatch.delenv(notify.ENV_SMTP_USER, raising=False)
    monkeypatch.delenv(notify.ENV_SMTP_PASSWORD, raising=False)
    assert notify.notifier_from_env().channel == "none"
    assert notify.smtp_status() == {
        "configured": False,
        "missing": [notify.ENV_SMTP_HOST, notify.ENV_SMTP_USER, notify.ENV_SMTP_PASSWORD],
    }

    monkeypatch.setenv(notify.ENV_SMTP_HOST, "smtp.example.com")
    monkeypatch.setenv(notify.ENV_SMTP_USER, "me@example.com")
    monkeypatch.setenv(notify.ENV_SMTP_PASSWORD, "授权码")

    sender = notify.notifier_from_env()
    assert sender.channel == "email" and sender.port == 465


# ---------- 开关与收件邮箱 ----------

def test_enabling_without_an_address_is_refused(conn):
    with pytest.raises(notify.NotifyError):
        notify.update_config(conn, enabled=True)
    assert notify.read_config(conn) == {"enabled": False, "to_addr": None}


def test_a_typo_in_the_address_is_refused(conn):
    for bad in ("someone", "someone@", "@example.com", "someone@example", "a b@example.com"):
        with pytest.raises(notify.NotifyError):
            notify.update_config(conn, to_addr=bad)
    assert notify.update_config(conn, to_addr=" someone@example.com ")["to_addr"] == "someone@example.com"


def test_turning_it_off_keeps_the_address(conn):
    notify.update_config(conn, to_addr="someone@example.com", enabled=True)
    notify.update_config(conn, enabled=False)
    prefs = notify.read_config(conn)
    assert prefs == {"enabled": False, "to_addr": "someone@example.com"}


def test_clearing_the_address_is_allowed(conn):
    notify.update_config(conn, to_addr="someone@example.com")
    assert notify.update_config(conn, to_addr="")["to_addr"] is None


# ---------- 该不该发 ----------

def test_nothing_is_sent_before_the_switch_is_on(conn):
    make_plan(conn)
    assert notify.decide(conn, plan.weekly_status(conn, today=TODAY), TODAY)["reason"] == "每周提醒没打开"


def test_nothing_is_sent_without_a_plan(conn):
    notify.update_config(conn, to_addr="someone@example.com", enabled=True)
    decision = notify.decide(conn, plan.weekly_status(conn, today=TODAY), TODAY)
    assert decision["send"] is False and "还没有计划" in decision["reason"]


def test_the_second_run_in_the_same_week_is_quiet(conn):
    make_plan(conn)
    notify.update_config(conn, to_addr="someone@example.com", enabled=True)
    notify.send_weekly(conn, today=TODAY, notifier=notify.NullNotifier())
    decision = notify.decide(conn, plan.weekly_status(conn, today=date(2026, 10, 6)), date(2026, 10, 6))
    assert decision == {"send": False, "kind": None, "reason": "本周已经问过了"}


def test_status_reports_what_the_settings_page_needs(conn, tmp_path, monkeypatch):
    make_plan(conn)
    monkeypatch.setenv("CADENCE_EXPORT_DIR", str(tmp_path / "exports"))
    notify.update_config(conn, to_addr="someone@example.com", enabled=True)
    from datetime import datetime

    state = notify.status(conn, today=TODAY, now=datetime(2026, 10, 5, 8, 0))
    assert state["enabled"] is True and state["to_addr"] == "someone@example.com"
    assert state["due"] is True and state["decision_reason"] == "本周还没问过"
    assert state["week"] == "2026-W41"
    assert state["next_send_at"].startswith("2026-10-05 09:00")   # 当天还没到点，就是当天
    assert "阶段 2" in state["preview"]["subject"]
    assert state["notice"] is None or "SMTP" in state["notice"]


def test_next_send_moves_to_next_week_after_the_moment(conn):
    from datetime import datetime
    assert notify.next_send_at(datetime(2026, 10, 5, 8, 0)).startswith("2026-10-05 09:00")
    assert notify.next_send_at(datetime(2026, 10, 5, 10, 0)).startswith("2026-10-12 09:00")
    assert notify.next_send_at(datetime(2026, 10, 8, 10, 0)).startswith("2026-10-12 09:00")
