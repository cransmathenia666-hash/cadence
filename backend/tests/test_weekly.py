"""每周检查点 job（T16）：干跑不出手、一周一封、漏了的补发、没动静的降频。

时间一律显式传入。建计划时把 `valid_from` 钉在 2026-09-01——「连续几周没动静」是数
计划建立之后的周数，不钉的话测试会随着真实运行日期漂移（今天跑绿、半年后跑红）。
"""

from __future__ import annotations

from datetime import date

import pytest

from app import db, ledger, notify, plan
from app.jobs import weekly_checkpoint as job

MONDAY = date(2026, 10, 5)          # 第 41 周
NEXT_WEEK = date(2026, 10, 12)      # 第 42 周
AFTER_A_GAP = date(2026, 10, 19)    # 第 43 周——上一周（W42）刻意不跑，模拟电脑没开机
ODD_QUIET_WEEK = date(2026, 11, 30)  # 第 49 周（奇数）
EVEN_QUIET_WEEK = date(2026, 12, 7)  # 第 50 周（偶数）

BORN = "2026-09-30T09:00:00+08:00"   # 第 40 周里、周中建的——上一周（W40）不算漏，安静周数从 1 起算


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "test.db"
    db.init(path)
    connection = db.connect(path)
    yield connection
    connection.close()


def make_plan(conn, *, with_address: bool = True, enabled: bool = True):
    plan_id = ledger.create_active(
        conn, "plan", {"goal": "Web 后端最小集", "valid_from": BORN, "created_at": BORN}, actor="user")
    stage_id = ledger.create_active(
        conn, "plan_node",
        {"plan_id": plan_id, "level": "stage", "title": "阶段 2",
         "deliverable": "接口能读写，数据落 SQLite", "sort_order": 0},
        actor="user")
    ledger.create_active(
        conn, "plan_node",
        {"plan_id": plan_id, "parent_id": stage_id, "level": "task",
         "title": "接上 SQLite 读写", "due_date": "2026-10-02", "sort_order": 0},
        actor="user")
    if with_address:
        notify.update_config(conn, to_addr="someone@example.com", enabled=enabled)
    return plan_id, stage_id


def run(conn, today, tmp_path, **kwargs):
    return job.run(conn, today=today, out_dir=tmp_path / "exports", notifier=notify.NullNotifier(), **kwargs)


def test_dry_run_asks_the_three_questions_and_writes_nothing(conn, tmp_path):
    make_plan(conn)
    report = run(conn, MONDAY, tmp_path, dry_run=True)

    assert report["send"] is True and report["kind"] == "weekly_checkpoint"
    assert "当前阶段：阶段 2" in report["body"]
    assert "本周推到哪" in report["body"] and "建议怎么调" in report["body"]
    assert notify.recent(conn) == []                      # 不落通知记录
    assert not (tmp_path / "exports").exists()            # 不导出
    assert report["export"] is None and report["memory"] is None


def test_one_email_a_week_and_it_is_logged(conn, tmp_path):
    make_plan(conn)
    report = run(conn, MONDAY, tmp_path)

    assert report["delivery"]["sent"] is True
    assert report["delivery"]["channel"] == "none"        # 没配 SMTP：空实现
    rows = notify.recent(conn)
    assert len(rows) == 1 and rows[0]["kind"] == "weekly_checkpoint"
    assert rows[0]["ok"] is True
    logged = conn.execute("SELECT body FROM notification_log").fetchone()["body"]
    assert logged == report["body"]                       # 日志里是原样那一封，好对账


def test_a_second_run_in_the_same_week_is_silent(conn, tmp_path):
    make_plan(conn)
    run(conn, MONDAY, tmp_path)
    again = run(conn, date(2026, 10, 7), tmp_path)

    assert again["send"] is False and again["delivery"] is None
    assert again["reason"] == "本周已经问过了"
    assert len(notify.recent(conn)) == 1


def test_a_missed_week_comes_back_as_a_makeup(conn, tmp_path):
    make_plan(conn)
    run(conn, MONDAY, tmp_path)
    report = run(conn, AFTER_A_GAP, tmp_path)             # W42 整周没跑

    assert report["kind"] == "makeup"
    assert report["missed_week"] == "2026-W42"
    assert report["body"].startswith("（补发）上周（2026-W42）的检查点漏了一次")
    assert notify.recent(conn)[0]["kind_label"] == "补发"


def test_four_quiet_weeks_downgrade_to_every_other_week(conn, tmp_path):
    make_plan(conn)
    skipped = run(conn, ODD_QUIET_WEEK, tmp_path)
    assert skipped["send"] is False
    assert "降频为每两周一次" in skipped["reason"]

    sent = run(conn, EVEN_QUIET_WEEK, tmp_path)
    assert sent["send"] is True
    assert "连续" in sent["body"] and "每两周一次" in sent["body"]
    assert "这个计划可能已经不合适了" in sent["body"]


def test_the_memory_batch_reports_what_it_did(conn, tmp_path):
    """记忆的每周那一批跟着跑，结果记在报告里（这个临时库没有经历，扫了也是 0 条候选）。"""
    make_plan(conn)
    report = run(conn, MONDAY, tmp_path)

    assert report["delivery"]["sent"] is True
    assert len(report["export"]["files"]) == 4
    assert report["memory"]["scans"] >= 1


def test_a_broken_memory_batch_does_not_undo_the_email(conn, tmp_path, monkeypatch):
    """记忆那批炸了也不该带倒已经发完的信与已经导出的文件——只如实写一句。"""
    def boom(_conn):
        raise RuntimeError("模型服务连不上")

    monkeypatch.setattr(job.memory, "weekly_scans", boom)
    make_plan(conn)
    report = run(conn, MONDAY, tmp_path)

    assert report["delivery"]["sent"] is True
    assert len(report["export"]["files"]) == 4
    assert "模型服务连不上" in report["memory"]["error"]


def test_memory_can_be_skipped_on_purpose(conn, tmp_path):
    make_plan(conn)
    report = run(conn, MONDAY, tmp_path, with_memory=False)
    assert report["memory"] is None


def test_nothing_is_sent_while_the_reminder_is_off(conn, tmp_path):
    make_plan(conn, enabled=False)
    report = run(conn, MONDAY, tmp_path)
    assert report["send"] is False and report["reason"] == "每周提醒没打开"
    assert report["export"] is not None                   # 导出与开关无关：那四个文件照写


def test_the_cli_dry_run_prints_the_questions(tmp_path, monkeypatch, capsys):
    path = tmp_path / "cli.db"
    db.init(path)
    with db.connect(path) as setup:
        make_plan(setup)
    # `main` 自己开关连接（跑完就关），所以这里给它一条每次新开的连接，
    # 断言另开一条读——不能复用 fixture 里那条，它会被 main 关掉。
    real_connect = db.connect
    monkeypatch.setattr(job.db, "connect", lambda *args, **kwargs: real_connect(path))
    code = job.main(["--dry-run", "--today", "2026-10-05", "--export-dir", str(tmp_path / "exports")])
    output = capsys.readouterr().out

    assert code == 0
    assert "[2026-W41]" in output
    assert "当前阶段：阶段 2" in output
    assert "（--dry-run：没有发信、没有导出、没有写任何记录）" in output
    with db.connect(path) as check:
        assert notify.recent(check) == []


def test_the_cli_refuses_a_malformed_date(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(job.db, "connect", lambda: db.connect(tmp_path / "unused.db"))
    code = job.main(["--dry-run", "--today", "2026/10/05"])
    assert code == 2
    assert "YYYY-MM-DD" in capsys.readouterr().err


def test_the_week_key_matches_the_export_file_name(conn, tmp_path):
    """周编号三处同源：判定（week_key）、导出文件名、通知记录所属的周。"""
    make_plan(conn)
    report = run(conn, MONDAY, tmp_path)
    names = [item["name"] for item in report["export"]["files"]]
    assert f"周检查点-{plan.week_key(MONDAY)}.md" in names
    assert report["week"] == plan.week_key(MONDAY) == "2026-W41"
