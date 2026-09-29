"""Markdown 单向导出（T17）：四个文件的落盘、内容对得上库、以及「改了会被覆盖」。

导出目录一律用 `tmp_path`，不碰仓库根的 `exports/`。
"""

from __future__ import annotations

from datetime import date

import pytest

from app import db, export, ledger

TODAY = date(2026, 10, 5)  # 周一，ISO 第 41 周


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "test.db"
    db.init(path)
    connection = db.connect(path)
    yield connection
    connection.close()


def make_plan(conn):
    plan_id = ledger.create_active(conn, "plan", {"goal": "Web 后端最小集"}, actor="user")
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
    ledger.create_active(
        conn, "profile_item",
        {"category": "current_state", "content": "白天在学校，晚上才有整块时间"},
        actor="user")
    return plan_id, stage_id


def read(out_dir, name: str) -> str:
    return (out_dir / name).read_text(encoding="utf-8")


def test_four_files_land_with_the_week_number_in_the_name(conn, tmp_path):
    make_plan(conn)
    result = export.export_all(conn, today=TODAY, out_dir=tmp_path / "exports" / "nested")

    names = {item["name"] for item in result["files"]}
    assert names == {"周检查点-2026-W41.md", "决策台账.md", "档案-当前.md", "计划-当前.md"}
    assert all(item["bytes"] > 0 for item in result["files"])
    assert result["week"] == "2026-W41"


def test_the_plan_file_matches_the_database(conn, tmp_path):
    make_plan(conn)
    out = tmp_path / "exports"
    export.export_all(conn, today=TODAY, out_dir=out)
    text = read(out, "计划-当前.md")
    assert "Web 后端最小集" in text
    assert "## [ ] 阶段 2" in text
    assert "要交的东西：接口能读写，数据落 SQLite" in text
    assert "- [ ] 接上 SQLite 读写（计划完成日 2026-10-02，落后 3 天）" in text
    assert "图例：" in text
    assert "只读快照" in text


def test_the_ledger_file_carries_the_flow_of_changes(conn, tmp_path):
    _, stage_id = make_plan(conn)
    ledger.set_status(conn, "plan_node", stage_id, "in_progress", actor="user", reason="开工了")
    out = tmp_path / "exports"
    export.export_all(conn, today=TODAY, out_dir=out)
    text = read(out, "决策台账.md")
    assert "计划节点 #" in text and "状态变更" in text
    assert "not_started → in_progress" in text
    assert "开工了" in text


def test_the_profile_file_groups_by_category(conn, tmp_path):
    make_plan(conn)
    out = tmp_path / "exports"
    export.export_all(conn, today=TODAY, out_dir=out)
    text = read(out, "档案-当前.md")
    assert "## 当前状态（精力/时间/压力）" in text
    assert "白天在学校，晚上才有整块时间" in text
    assert "（这一类还是空的）" in text          # 没填的类别如实写空，不装作有内容


def test_the_weekly_file_holds_the_three_questions(conn, tmp_path):
    make_plan(conn)
    out = tmp_path / "exports"
    export.export_all(conn, today=TODAY, out_dir=out)
    text = read(out, "周检查点-2026-W41.md")
    assert "## 一、当前阶段" in text and "## 二、本周推到哪" in text and "## 三、建议怎么调" in text
    assert "落后：「接上 SQLite 读写」到期 2026-10-02，落后 3 天还没做完" in text


def test_editing_a_file_by_hand_is_overwritten_on_the_next_export(conn, tmp_path):
    """「单向」的证据：手改之后重新导出，文件回到库里的样子。"""
    make_plan(conn)
    out = tmp_path / "exports"
    export.export_all(conn, today=TODAY, out_dir=out)
    target = out / "档案-当前.md"
    target.write_text("我在这里写了点批注，还删掉了原来的内容", encoding="utf-8")

    export.export_all(conn, today=TODAY, out_dir=out)

    text = target.read_text(encoding="utf-8")
    assert "我在这里写了点批注" not in text
    assert "白天在学校，晚上才有整块时间" in text


def test_an_empty_database_exports_without_blowing_up(conn, tmp_path):
    out = tmp_path / "exports"
    result = export.export_all(conn, today=TODAY, out_dir=out)
    assert len(result["files"]) == 4
    assert "还没有计划" in read(out, "计划-当前.md")
    assert "还没有任何流水" in read(out, "决策台账.md")


def test_summary_lists_what_is_already_there_without_writing(conn, tmp_path, monkeypatch):
    out = tmp_path / "exports"
    monkeypatch.setenv("CADENCE_EXPORT_DIR", str(out))
    empty = export.summary()
    assert empty["files"] == [] and empty["last_at"] is None

    make_plan(conn)
    export.export_all(conn, today=TODAY, out_dir=out)
    filled = export.summary()
    assert len(filled["files"]) == 4
    assert filled["last_at"] == filled["files"][0]["modified"]
    assert {item["name"] for item in filled["files"]} >= {"计划-当前.md", "决策台账.md"}


def test_the_export_directory_can_be_redirected(conn, tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_EXPORT_DIR", str(tmp_path / "somewhere-else"))
    assert export.export_dir() == tmp_path / "somewhere-else"
    make_plan(conn)
    export.export_all(conn, today=TODAY)
    assert (tmp_path / "somewhere-else" / "计划-当前.md").exists()
