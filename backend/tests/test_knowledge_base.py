"""知识库安全层（KB-01）单测。

全部用 tmp_path 临时目录 + 临时库，**绝不碰 data/cadence.db，也绝不读真实 Obsidian 库**——
真实库由用户手工走查（SPEC 第 10 节口径），自动化测试只验确定性逻辑。
"""

from __future__ import annotations

import os
import subprocess

import pytest

from app import db, knowledge_base as kb


# ---------- 夹具 ----------


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    """一个已配置的知识库根目录（编号 1）。"""
    root = tmp_path / "vault"
    root.mkdir()
    monkeypatch.setenv("CADENCE_KNOWLEDGE_ROOTS", f"笔记={root}")
    return root


def _write(path, content: str = "hello\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline=""：按原样落盘，别让 Windows 把 \n 翻译成 \r\n（读取断言要逐字比对）
    path.write_text(content, encoding="utf-8", newline="")


def _link_dir(link, target) -> bool:
    """建一个指向 target 的目录链接：先试符号链接，退而求 Junction（不需要管理员）。

    都建不了（权限受限的环境）返回 False，调用方跳过该用例。
    """
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except OSError:
        pass
    try:
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", os.fspath(link), os.fspath(target)],
            check=True, capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _link_file(link, target) -> bool:
    """建一个指向 target 的文件符号链接；Junction 只能指目录，失败就返回 False。"""
    try:
        os.symlink(target, link)
        return True
    except OSError:
        return False


# ---------- 路径安全 ----------


def test_parent_escape_is_denied(vault):
    _write(vault / "note.md")

    for rel in ("../outside.md", "a/../../b.md", "notes/../../x.md"):
        with pytest.raises(kb.KnowledgeDenied, match=r"\.\."):
            kb.safe_relative_path(1, rel)


def test_absolute_path_and_drive_are_denied(vault):
    _write(vault / "note.md")

    for rel in ("C:/Windows/a.md", "C:notes.md", "/etc/passwd", "\\\\server\\share\\a.md"):
        with pytest.raises(kb.KnowledgeDenied, match="绝对路径|盘符"):
            kb.safe_relative_path(1, rel)


def test_symlink_escape_is_denied(vault, tmp_path):
    """符号链接 / Junction 指向根外：resolve 之后必须现形并拒绝。"""
    outside = tmp_path / "outside"
    outside.mkdir()
    _write(outside / "secret.md", "根外的秘密")

    if _link_file(vault / "leak.md", outside / "secret.md"):
        with pytest.raises(kb.KnowledgeDenied, match="外面"):
            kb.safe_relative_path(1, "leak.md")
        return
    if _link_dir(vault / "docs", outside):
        with pytest.raises(kb.KnowledgeDenied, match="外面"):
            kb.safe_relative_path(1, "docs/secret.md")
        return
    pytest.skip("当前环境既建不了符号链接也建不了 Junction")


def test_denied_suffixes_are_denied(vault):
    for rel in ("secret.env", ".env", "data.db", "data.sqlite3", "tool.exe", "run.ps1", "run.sh"):
        _write(vault / rel, "x")

    for rel in ("secret.env", ".env", "data.db", "data.sqlite3", "tool.exe", "run.ps1", "run.sh"):
        with pytest.raises(kb.KnowledgeDenied, match="不允许读取"):
            kb.safe_relative_path(1, rel)


def test_denied_extensions_are_denied_even_without_suffix_hit(vault):
    """白名单扩展名是主闸：不在 .md/.markdown/.txt 里的一律拒（如图片、PDF）。"""
    _write(vault / "photo.jpg")
    _write(vault / "paper.pdf")

    for rel in ("photo.jpg", "paper.pdf", "README"):
        with pytest.raises(kb.KnowledgeDenied, match="Markdown"):
            kb.safe_relative_path(1, rel)


def test_git_and_node_modules_files_are_denied(vault):
    _write(vault / ".git" / "config")
    _write(vault / "node_modules" / "pkg" / "README.md")

    with pytest.raises(kb.KnowledgeDenied, match="\\.git"):
        kb.safe_relative_path(1, ".git/config")
    with pytest.raises(kb.KnowledgeDenied, match="node_modules"):
        kb.safe_relative_path(1, "node_modules/pkg/README.md")


def test_readable_markdown_passes_and_normalizes_separators(vault):
    _write(vault / "notes" / "深挖.md", "# 正文")

    resolved = kb.safe_relative_path(1, "notes\\深挖.md")

    assert resolved.relative_path == "notes/深挖.md"  # 一律正斜杠
    assert resolved.absolute == (vault / "notes" / "深挖.md").resolve()


def test_unknown_root_is_not_found(vault):
    with pytest.raises(kb.KnowledgeNotFound):
        kb.safe_relative_path(99, "a.md")


# ---------- 根配置与脱敏 ----------


def test_public_roots_have_no_absolute_paths(tmp_path, monkeypatch):
    """对外清单只有 id/alias/enabled/error，坏根的错误消息也不得漏出路径。"""
    good = tmp_path / "good"
    good.mkdir()
    missing = tmp_path / "no-such-dir"
    malformed = str(tmp_path / "noequals")  # 没有 = 的条目：整段是路径，也不能漏出去
    monkeypatch.setenv(
        "CADENCE_KNOWLEDGE_ROOTS", f"笔记={good};缺失={missing};{malformed}"
    )

    public = kb.public_roots()

    assert [item["id"] for item in public] == [1, 2, 3]  # 编号按配置顺序从 1 起
    assert set(public[0]) == {"id", "alias", "enabled", "error", "source"}
    assert public[0] == {
        "id": 1, "alias": "笔记", "enabled": True, "error": None, "source": "env",
    }  # 环境变量来的根不给路径：那一份不在界面里改
    assert public[1]["enabled"] is False
    assert public[1]["error"]  # 一句人话 error
    assert public[2]["enabled"] is False
    for item in public:
        assert str(tmp_path) not in str(item)  # 路径不出现在任何对外字段里


def test_missing_root_is_disabled_and_operations_refuse(tmp_path, monkeypatch):
    """根目录缺失：enabled=False、不抛解析异常；对它列目录 / 读文件被人话拒绝。"""
    monkeypatch.setenv(
        "CADENCE_KNOWLEDGE_ROOTS", f"笔记={tmp_path / '不存在的库'}"
    )

    roots = kb.configured_roots()
    assert len(roots) == 1 and roots[0].enabled is False and roots[0].error

    public = kb.public_roots()
    assert public[0]["enabled"] is False
    assert str(tmp_path) not in str(public)

    with pytest.raises(kb.KnowledgeError, match="停用"):
        kb.list_files(1)
    with pytest.raises(kb.KnowledgeError, match="停用"):
        kb.read_text(1, "a.md")


def test_empty_config_yields_no_roots(monkeypatch):
    monkeypatch.delenv("CADENCE_KNOWLEDGE_ROOTS", raising=False)
    assert kb.configured_roots() == []
    assert kb.public_roots() == []


def test_duplicate_alias_disables_the_later_root(tmp_path, monkeypatch):
    """别名是核对出处时的查找键：两条同名会让出处核到另一个根上，所以后一条直接停用。"""
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    monkeypatch.setenv("CADENCE_KNOWLEDGE_ROOTS", f"笔记={first};笔记={second}")

    roots = kb.configured_roots()

    assert [root.enabled for root in roots] == [True, False]
    assert roots[1].error and "重复" in roots[1].error
    assert roots[0].path == first.resolve()  # 先配的那条才是这个别名认的根


# ---------- list_files ----------


def test_list_files_filters_by_prefix_and_sorts(vault):
    _write(vault / "a.md")
    _write(vault / "z.md")
    _write(vault / "notes" / "b.md")
    _write(vault / "notes" / "sub" / "c.md")
    _write(vault / "notes" / "old.txt")
    _write(vault / "notes-extra" / "d.md")

    everything = kb.list_files(1)
    assert [item["relative_path"] for item in everything] == [
        "a.md", "notes-extra/d.md", "notes/b.md", "notes/old.txt", "notes/sub/c.md", "z.md",
    ]  # 按相对路径排序（"-" 在 "/" 前，所以 notes-extra 排在 notes/ 之前）
    assert all("\\" not in item["relative_path"] for item in everything)

    notes_only = kb.list_files(1, prefix="notes")
    assert [item["relative_path"] for item in notes_only] == [
        "notes/b.md", "notes/old.txt", "notes/sub/c.md",
    ]  # notes-extra 不属于 notes 前缀；子树整体命中且有序

    for item in everything:
        assert set(item) == {"relative_path", "size", "modified_at"}


def test_list_files_hides_unreadable_files(vault):
    _write(vault / "ok.md")
    _write(vault / "tool.exe")
    _write(vault / ".git" / "config")

    assert [item["relative_path"] for item in kb.list_files(1)] == ["ok.md"]


# ---------- read_text ----------


def test_read_text_returns_full_file_with_hash(vault):
    content = "# 笔记\n\n今天开始学 FastAPI。\n"
    _write(vault / "deep.md", content)

    result = kb.read_text(1, "deep.md")

    assert result["relative_path"] == "deep.md"
    assert result["text"] == content
    assert result["truncated"] is False
    assert result["size"] == len(content.encode("utf-8"))
    assert result["content_hash"] == kb.hash_bytes(content.encode("utf-8"))
    assert result["content_hash"] == kb.hash_file(vault / "deep.md")


def test_read_text_truncation_flag(vault):
    _write(vault / "big.md", "字" * 100)

    cut = kb.read_text(1, "big.md", limit=10)
    assert cut["text"] == "字" * 10
    assert cut["truncated"] is True
    assert cut["size"] == 100 * len("字".encode("utf-8"))  # size 是整个文件的字节数

    tail = kb.read_text(1, "big.md", offset=95)
    assert tail["text"] == "字" * 5
    assert tail["truncated"] is False

    whole = kb.read_text(1, "big.md")
    assert whole["truncated"] is False


def test_read_text_caps_limit_at_single_file_budget(vault):
    """调用方给再大的 limit，也不越过单篇硬顶。"""
    _write(vault / "huge.md", "a" * (kb.MAX_FILE_CHARS + 500))

    result = kb.read_text(1, "huge.md", limit=kb.MAX_FILE_CHARS * 10)

    assert len(result["text"]) == kb.MAX_FILE_CHARS
    assert result["truncated"] is True


def test_read_text_reports_missing_file_without_paths(vault):
    with pytest.raises(kb.KnowledgeNotFound) as info:
        kb.read_text(1, "没有.md")
    assert "没有.md" in str(info.value)
    assert str(vault) not in str(info.value)


def test_read_text_rejects_invalid_utf8_with_plain_message(vault):
    (vault / "broken.md").write_bytes(b"\xff\xfe\x00\x81bad")

    with pytest.raises(kb.KnowledgeError, match="UTF-8") as info:
        kb.read_text(1, "broken.md")
    assert str(vault) not in str(info.value)  # 人话错误，不带绝对路径


# ---------- 哈希 ----------


def test_hash_bytes_and_hash_file_agree(tmp_path):
    payload = "哈希一致性\n".encode("utf-8")
    path = tmp_path / "x.txt"
    path.write_bytes(payload)

    assert kb.hash_file(path) == kb.hash_bytes(payload)
    assert len(kb.hash_bytes(payload)) == 64  # sha256 十六进制


# ---------- schema / 加列迁移 ----------


def test_db_init_creates_knowledge_tables_and_evidence_columns(tmp_path):
    """新库一次建全三张 knowledge 表；老库可重复 init，memory_evidence 加列不重复不报错。"""
    db_path = tmp_path / "test.db"
    db.init(db_path)
    db.init(db_path)  # 重复 init：不删数据、不重复加列

    conn = db.connect(db_path)
    try:
        tables = {
            row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {"knowledge_file", "knowledge_scan", "knowledge_coverage"} <= tables

        columns = {row["name"] for row in conn.execute("PRAGMA table_info(memory_evidence)")}
        assert {
            "knowledge_file_id", "content_hash", "relative_path", "line_start", "line_end",
        } <= columns
    finally:
        conn.close()


# ---------- 脱敏与「根内别名绕过黑名单」（2026-10-05 独立复核整改） ----------


def test_redact_paths_removes_any_absolute_path():
    """异常原文里可能带**别的盘**的路径——只替换自己的根挡不住它（§10.3）。"""
    cleaned = kb.redact_paths("打不开 D:\\private\\obsidian-vault\\secret.md：拒绝访问")

    assert "D:\\private" not in cleaned
    assert "本机路径" in cleaned


def test_junction_alias_into_a_denied_dir_is_denied(vault):
    """根里一个指向 .git 的 junction 别名：字面路径段看不出问题，resolve 之后才现形。"""
    _write(vault / ".git" / "evil.md", "GIT SECRET")
    if not _link_dir(vault / "alias", vault / ".git"):
        pytest.skip("当前环境建不了符号链接 / Junction")

    listed = [item["relative_path"] for item in kb.list_files(1)]
    assert "alias/evil.md" not in listed
    with pytest.raises(kb.KnowledgeDenied, match=r"\.git"):
        kb.safe_relative_path(1, "alias/evil.md")


# ---------- 在界面里配根目录（2026-10-05：不再让人去改 .env） ----------


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "test.db"
    db.init(path)
    connection = db.connect(path)
    yield connection
    connection.close()


def _dir(base, name: str):
    folder = base / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def test_roots_configured_in_the_ui_win_over_the_env(conn, tmp_path, monkeypatch):
    """界面配过就以库为准：环境变量只是「库里一条都没有时」的兜底。"""
    monkeypatch.setenv("CADENCE_KNOWLEDGE_ROOTS", f"旧的={_dir(tmp_path, 'old')}")

    added = kb.add_root(conn, alias="笔记", path=_dir(tmp_path, "vault"))

    roots = kb.configured_roots(conn)
    assert [root.alias for root in roots] == ["笔记"]
    assert roots[0].source == "ui" and roots[0].enabled
    assert added.id == roots[0].id

    public = kb.public_roots(conn)
    assert public[0]["source"] == "ui"
    assert public[0]["path"] == str((tmp_path / "vault").resolve())  # 界面能看到自己配的目录


def test_duplicate_alias_and_bad_paths_are_refused_with_plain_words(conn, tmp_path):
    from pathlib import Path

    kb.add_root(conn, alias="笔记", path=_dir(tmp_path, "a"))

    with pytest.raises(kb.KnowledgeConflict, match="已经有一个叫"):
        kb.add_root(conn, alias="笔记", path=_dir(tmp_path, "b"))
    with pytest.raises(kb.KnowledgeError, match="绝对路径"):
        kb.add_root(conn, alias="相对", path="some/relative/path")
    with pytest.raises(kb.KnowledgeError, match="现成的目录"):
        kb.add_root(conn, alias="没有", path=str(tmp_path / "不存在"))
    _write(tmp_path / "a" / "note.md")
    with pytest.raises(kb.KnowledgeError, match="指向的是文件"):
        kb.add_root(conn, alias="文件", path=str(tmp_path / "a" / "note.md"))
    with pytest.raises(kb.KnowledgeError, match="系统目录"):
        kb.check_root_path("C:/Windows")
    with pytest.raises(kb.KnowledgeError, match="cadence 自己"):
        kb.check_root_path(str(Path(kb.__file__).resolve().parents[2]))
    with pytest.raises(kb.KnowledgeError, match="整盘"):
        kb.check_root_path(tmp_path.anchor)


def test_update_and_delete_a_ui_root(conn, tmp_path):
    root = kb.add_root(conn, alias="笔记", path=_dir(tmp_path, "a"))

    renamed = kb.update_root(conn, root.id, alias="资料", enabled=False)
    assert renamed.alias == "资料" and renamed.enabled is False
    assert kb.configured_roots(conn)[0].enabled is False

    kb.delete_root(conn, root.id)
    assert kb.configured_roots(conn) == []
    with pytest.raises(kb.KnowledgeNotFound):
        kb.delete_root(conn, root.id)


def test_a_ui_root_whose_folder_is_gone_is_disabled_not_fatal(conn, tmp_path):
    folder = _dir(tmp_path, "临时")
    kb.add_root(conn, alias="会消失", path=folder)
    folder.rmdir()

    (only,) = kb.configured_roots(conn)
    assert only.enabled is False and "不存在" in (only.error or "")


def test_import_env_roots_moves_them_into_the_ui(conn, tmp_path, monkeypatch):
    monkeypatch.setenv(
        "CADENCE_KNOWLEDGE_ROOTS", f"笔记={_dir(tmp_path, 'a')};缺的={tmp_path / '没有'}"
    )

    report = kb.import_env_roots(conn)

    assert report["imported"] == ["笔记"]
    assert any("缺的" in str(item) for item in report["skipped"])
    assert [root.alias for root in kb.configured_roots(conn)] == ["笔记"]


def test_browse_lists_subdirectories_and_says_why_unusable(tmp_path):
    parent = _dir(tmp_path, "父目录")
    _dir(parent, "子目录")
    _write(parent / "note.md")  # 文件不列

    listing = kb.browse(str(parent))

    assert [item["name"] for item in listing["entries"]] == ["子目录"]
    assert listing["problem"] is None  # 这个目录可以选
    assert listing["parent"] == str(tmp_path.resolve())

    start = kb.browse(None)
    assert start["path"] is None
    assert start["entries"]
    assert all(item["problem"] for item in start["entries"])  # 盘符与主目录：看得见、选不了
