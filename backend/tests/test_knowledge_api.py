"""知识库接口（KB-04）的契约单测。

照 `test_api_contract` 的口径：不开 TestClient（那要 httpx，SPEC 第 16 节 Ask first），
直接调 `app.main` 里的路由函数——依赖的连接显式传，请求体用 Pydantic 模型构造，
错误断言抓 `HTTPException`。全部 tmp_path 临时目录 + 临时库 + 假上游
（接口层不收 transport 参数，打桩靠替换 `llm.post_json`），不碰 data/cadence.db、
不读真实知识库。

覆盖：四条路由的响应形状；根不存在 404、被停用 400；绝对路径 / 越界 400 且不留假
扫描行；扫描失败如实返回 failed 而不是伪装成功；摘要带配额与缺口；扫描历史只有账面
字段、不带原文；所有响应（含错误消息）都不出现绝对路径。
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app import db, llm, main

from test_dialogue import ScriptedTransport, make_provider


# ---------- 夹具 ----------


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    """两个根：编号 1 可用（真实目录），编号 2 指向不存在的目录（被停用）。"""
    root = tmp_path / "vault"
    root.mkdir()
    monkeypatch.setenv(
        "CADENCE_KNOWLEDGE_ROOTS", f"笔记={root};空壳={tmp_path / 'ghost'}"
    )
    return root


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "test.db"
    db.init(path)
    connection = db.connect(path)
    yield connection
    connection.close()


@pytest.fixture()
def fake_upstream(monkeypatch):
    """把「真发请求」换成按脚本回答的假上游；接口层不收 transport，只能从这里打桩。"""

    def install(*texts: str) -> ScriptedTransport:
        transport = ScriptedTransport(*texts)
        monkeypatch.setattr(llm, "post_json", transport)
        return transport

    return install


def _write(path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline=""：按原样落盘，别让 Windows 把 \n 翻译成 \r\n（摘录要逐字比对）
    path.write_text(content, encoding="utf-8", newline="")


def _read_call(path: str, **args) -> str:
    return json.dumps(
        {"tool_calls": [{"name": "knowledge_read_file", "args": {"path": path, **args}}]},
        ensure_ascii=False,
    )


def _final(*cards, gaps=None) -> str:
    return json.dumps({"cards": list(cards), "unread_gaps": gaps or []}, ensure_ascii=False)


def _card(*, excerpt: str, content: str = "晚上十点以后不学习") -> dict:
    return {
        "action": "add",
        "category": "current_state",
        "content": content,
        "why": "笔记里反复出现",
        "source_kind": "user_stated",
        "evidence": [{"path": "a.md", "excerpt": excerpt, "line_start": 1, "line_end": 1}],
        "target_profile_id": None,
        "uncertainty_reason": None,
    }


def _scan_row_count(conn) -> int:
    return int(conn.execute("SELECT COUNT(*) AS n FROM knowledge_scan").fetchone()["n"])


# ---------- GET /api/knowledge/roots ----------


def test_roots_shape_and_a_disabled_root_never_leaks_its_path(conn, vault, tmp_path):
    payload = main.get_knowledge_roots(conn)

    assert [root["id"] for root in payload["roots"]] == [1, 2]
    first, second = payload["roots"]
    assert first == {
        "id": 1, "alias": "笔记", "enabled": True, "error": None, "source": "env",
    }
    assert second["enabled"] is False and "不存在" in str(second["error"])
    rendered = json.dumps(payload, ensure_ascii=False)
    assert str(vault) not in rendered
    assert str(tmp_path / "ghost") not in rendered  # 停用的根也不带出它配置里的路径


# ---------- GET /api/knowledge/files ----------


def test_files_route_shape_and_covered_follows_the_coverage_ledger(conn, vault, fake_upstream):
    _write(vault / "a.md", "# 笔记\n晚上十点以后不学习。\n")

    before = main.get_knowledge_files(root_id=1, conn=conn)
    assert before["root"] == {"id": 1, "alias": "笔记"}
    assert len(before["files"]) == 1
    entry = before["files"][0]
    assert set(entry) == {"relative_path", "size", "modified_at", "covered"}
    assert entry["relative_path"] == "a.md" and entry["size"] > 0
    assert entry["covered"] is False

    make_provider(conn)
    fake_upstream(_read_call("a.md"), _final(_card(excerpt="晚上十点以后不学习。")))
    report = main.post_knowledge_scan(main.KnowledgeScanIn(), conn)
    assert report["status"] == "ok" and report["candidates_created"] == 1

    after = main.get_knowledge_files(root_id=1, conn=conn)
    assert after["files"][0]["covered"] is True

    # prefix 按目录前缀过滤：别的目录名不误伤（"a" 只匹配 a.md 自己或 a/ 子树）
    assert main.get_knowledge_files(root_id=1, prefix="nope", conn=conn)["files"] == []


# ---------- 404 / 400：根不存在、被停用、绝对路径、越界 ----------


def test_missing_root_is_404_on_both_read_routes(conn, vault):
    make_provider(conn)
    with pytest.raises(HTTPException) as files_exc:
        main.get_knowledge_files(root_id=9, conn=conn)
    assert files_exc.value.status_code == 404
    assert "9" in str(files_exc.value.detail) and "不存在" in str(files_exc.value.detail)

    with pytest.raises(HTTPException) as scan_exc:
        main.post_knowledge_scan(main.KnowledgeScanIn(root_id=9), conn)
    assert scan_exc.value.status_code == 404
    assert _scan_row_count(conn) == 0  # 参数错在扫描行落库前就被挡下，不留假记录


def test_disabled_root_is_400_with_its_own_reason(conn, vault):
    with pytest.raises(HTTPException) as files_exc:
        main.get_knowledge_files(root_id=2, conn=conn)
    assert files_exc.value.status_code == 400
    assert "停用" in str(files_exc.value.detail)


def test_absolute_path_and_escape_are_400_without_leaking_the_path(conn, vault, fake_upstream):
    _write(vault / "a.md", "内容。\n")
    make_provider(conn)

    for bad_prefix in ("C:/Windows/notes", "../outside"):
        with pytest.raises(HTTPException) as files_exc:
            main.get_knowledge_files(root_id=1, prefix=bad_prefix, conn=conn)
        assert files_exc.value.status_code == 400
        assert str(vault) not in str(files_exc.value.detail)

    with pytest.raises(HTTPException) as scan_exc:
        main.post_knowledge_scan(
            main.KnowledgeScanIn(scope="paths", paths=["..\\escape.md"]), conn
        )
    assert scan_exc.value.status_code == 400
    assert "越" in str(scan_exc.value.detail) or ".." in str(scan_exc.value.detail)
    assert _scan_row_count(conn) == 0  # 越界在列目录那一步就被拒：一条扫描账都不留


# ---------- POST /api/knowledge/scan：失败是业务结果，不是 500 ----------


def test_failed_scan_is_returned_honestly_not_dressed_up(conn, vault, fake_upstream):
    make_provider(conn)
    _write(vault / "a.md", "# 笔记\n晚上十点以后不学习。\n")
    fake_upstream("这不是 JSON", "还是不是 JSON")  # 重说一次仍不合格

    report = main.post_knowledge_scan(main.KnowledgeScanIn(), conn)  # 200，不是异常

    assert report["status"] == "failed"
    assert "没给出合格的提炼输出" in str(report["error"])
    assert report["candidates_created"] == 0 and report["files_considered"] == 1
    row = conn.execute(
        "SELECT status FROM knowledge_scan WHERE id = ?", (report["scan_id"],)
    ).fetchone()
    assert row["status"] == "failed"  # 账本里也是 failed，不是成功


def test_model_failure_also_lands_as_failed_with_quota_fields(conn, vault, fake_upstream):
    make_provider(conn)
    _write(vault / "a.md", "# 笔记\n晚上十点以后不学习。\n")
    fake_upstream()  # 脚本为空：一调就炸，模拟模型通路不通

    report = main.post_knowledge_scan(main.KnowledgeScanIn(), conn)

    assert report["status"] == "failed"
    assert "调用模型失败" in str(report["error"])
    for key in ("scan_id", "read_calls", "chars_read", "files_considered", "candidates_created"):
        assert key in report


# ---------- 摘要带配额与缺口 ----------


def test_scan_summary_carries_quota_and_gaps(conn, vault, fake_upstream):
    make_provider(conn)
    _write(vault / "a.md", "甲乙丙丁戊己庚辛壬癸" * 4)  # 40 个字符
    fake_upstream(_read_call("a.md", limit=10), _final())  # 只读 10 个字符就收工

    report = main.post_knowledge_scan(main.KnowledgeScanIn(), conn)

    assert report["status"] == "partial"
    assert report["read_calls"] == 1 and report["chars_read"] == 10
    assert report["candidates_created"] == 0
    assert report["unread_gaps"] and "没有读完整" in report["unread_gaps"][0]
    assert str(vault) not in json.dumps(report, ensure_ascii=False)


# ---------- 空请求体的默认行为 ----------


def test_empty_body_scans_the_first_usable_root(conn, vault, fake_upstream):
    make_provider(conn)
    _write(vault / "a.md", "# 笔记\n晚上十点以后不学习。\n")
    fake_upstream(_read_call("a.md"), _final(_card(excerpt="晚上十点以后不学习。")))

    report = main.post_knowledge_scan(main.KnowledgeScanIn(), conn)

    assert report["root_id"] == 1 and report["root_alias"] == "笔记"
    assert report["trigger"] == "manual"  # 界面发起就是 manual；weekly 走每周任务


def test_no_roots_configured_is_a_plain_400(conn, monkeypatch):
    monkeypatch.setenv("CADENCE_KNOWLEDGE_ROOTS", "")
    with pytest.raises(HTTPException) as scan_exc:
        main.post_knowledge_scan(main.KnowledgeScanIn(), conn)
    assert scan_exc.value.status_code == 400
    assert "还没有配置可用的知识库根目录" in str(scan_exc.value.detail)

    with pytest.raises(HTTPException) as files_exc:
        main.get_knowledge_files(root_id=1, conn=conn)
    assert files_exc.value.status_code == 404


# ---------- GET /api/knowledge/scans ----------


def test_scan_history_has_only_ledger_fields_and_supports_root_filter(conn, vault, fake_upstream):
    make_provider(conn)
    _write(vault / "a.md", "# 笔记\n晚上十点以后不学习。\n")
    fake_upstream(_read_call("a.md"), _final(_card(excerpt="晚上十点以后不学习。")))
    report = main.post_knowledge_scan(main.KnowledgeScanIn(), conn)

    payload = main.get_knowledge_scans(conn=conn)
    assert [scan["scan_id"] for scan in payload["scans"]] == [report["scan_id"]]
    entry = payload["scans"][0]
    assert set(entry) == {
        "scan_id", "root_id", "root_alias", "trigger", "requirements_version", "status",
        "read_calls", "chars_read", "files_considered", "candidates_created",
        "quality_status", "error", "created_at", "finished_at",
    }  # 账面字段，一张原文卡都没有
    assert entry["root_alias"] == "笔记" and entry["status"] == "ok"
    assert entry["candidates_created"] == 1
    assert str(vault) not in json.dumps(payload, ensure_ascii=False)

    # 只看别的根：它的扫描历史是空的
    assert main.get_knowledge_scans(root_id=2, conn=conn) == {"scans": []}


def test_roots_route_publishes_the_quota_limits(conn, vault):
    """配额上限由后端给，界面不抄数字（SPEC 第 14 节：组件不内联业务规则）。"""
    from app import knowledge_base as kb

    payload = main.get_knowledge_roots(conn)

    assert payload["limits"] == {
        "max_read_calls": kb.MAX_READ_CALLS,
        "max_file_chars": kb.MAX_FILE_CHARS,
        "max_total_chars": kb.MAX_TOTAL_CHARS,
    }
    assert all("limits" not in root for root in payload["roots"])  # 上限不混进每个根


def test_ui_roots_can_be_added_changed_and_removed_through_the_api(conn, tmp_path, vault):
    """界面里配根目录：加 / 改名停用 / 删；环境变量那份仍照旧只读。"""
    folder = tmp_path / "资料库"
    folder.mkdir()

    created = main.post_knowledge_root(
        main.KnowledgeRootIn(alias="资料", path=str(folder)), conn
    )["root"]
    assert created["alias"] == "资料" and created["source"] == "ui"
    assert created["path"] == str(folder.resolve())  # 配置界面能看到自己配的目录

    renamed = main.put_knowledge_root(
        created["id"], main.KnowledgeRootPatch(alias="资料库", enabled=False), conn
    )["root"]
    assert renamed["alias"] == "资料库" and renamed["enabled"] is False

    assert main.delete_knowledge_root(created["id"], conn) == {"deleted": created["id"]}
    # 删完回到环境变量兜底：那两个 env 根还在，界面那份没了
    aliases = [root["alias"] for root in main.get_knowledge_roots(conn)["roots"]]
    assert aliases == ["笔记", "空壳"]


def test_adding_a_bad_root_gives_400_and_a_duplicate_gives_409(conn, tmp_path, vault):
    folder = tmp_path / "资料库"
    folder.mkdir()
    main.post_knowledge_root(main.KnowledgeRootIn(alias="资料", path=str(folder)), conn)

    with pytest.raises(HTTPException) as dup:
        main.post_knowledge_root(main.KnowledgeRootIn(alias="资料", path=str(folder)), conn)
    assert dup.value.status_code == 409

    with pytest.raises(HTTPException) as bad:
        main.post_knowledge_root(
            main.KnowledgeRootIn(alias="别处", path=str(tmp_path / "不存在")), conn
        )
    assert bad.value.status_code == 400


def test_browse_route_lists_directories(conn, tmp_path, vault):
    sub = tmp_path / "父目录"
    sub.mkdir()
    (sub / "子目录").mkdir()

    listing = main.get_knowledge_browse(str(sub))

    assert [item["name"] for item in listing["entries"]] == ["子目录"]
    assert listing["problem"] is None

    with pytest.raises(HTTPException) as bad:
        main.get_knowledge_browse("相对/路径")
    assert bad.value.status_code == 400
