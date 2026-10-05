"""知识库受限扫描循环（KB-02 / KB-03）的单测。

全部 tmp_path 临时目录 + 临时库 + 假上游（照 test_memory / test_agent 的 ScriptedTransport
打法），**绝不碰 data/cadence.db，也绝不读真实知识库**。覆盖的是边界与账目，不是模型好坏：

- 受限工具：列目录只回相对路径；root / scan 编号这类系统注入的参数模型给了就被拒；
- 配额真实生效：读调用次数、单篇截断、总字符三者都卡得住，触顶如实留缺口；
- 覆盖账：文件在列目录后被改不写 covered；模型输出坏 JSON 重说一次仍不合格 = 文件与
  扫描都 failed、无假成功；中断 / 模型失败同样不写 covered；
- 确定性校验：摘录逐字找不到整张卡不落；add / supersede / uncertain 三种分流；
  完全重复被拦、近似重复只塞 duplicate_hint；
- 同一版本再扫一遍能产生第二次扫描记录（增量跳过已覆盖，full 真的重扫）。
"""

from __future__ import annotations

import json

import pytest

from app import db, knowledge_base as kb, ledger

from test_dialogue import ScriptedTransport, make_provider


# ---------- 夹具与脚本工具 ----------


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    root = tmp_path / "vault"
    root.mkdir()
    monkeypatch.setenv("CADENCE_KNOWLEDGE_ROOTS", f"笔记={root}")
    return root


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "test.db"
    db.init(path)
    connection = db.connect(path)
    yield connection
    connection.close()


def _write(path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline=""：按原样落盘，别让 Windows 把 \n 翻译成 \r\n（摘录要逐字比对）
    path.write_text(content, encoding="utf-8", newline="")


def _read_call(path: str, **args) -> str:
    return json.dumps(
        {"tool_calls": [{"name": "knowledge_read_file", "args": {"path": path, **args}}]},
        ensure_ascii=False,
    )


def _list_call(**args) -> str:
    return json.dumps(
        {"tool_calls": [{"name": "knowledge_list_files", "args": args}]}, ensure_ascii=False
    )


def _final(*cards, gaps=None) -> str:
    return json.dumps({"cards": list(cards), "unread_gaps": gaps or []}, ensure_ascii=False)


def _card(
    *,
    action: str = "add",
    category: str = "current_state",
    content: str = "晚上十点以后不学习",
    why: str | None = "笔记里反复出现",
    source_kind: str = "user_stated",
    path: str = "a.md",
    excerpt: str,
    line_start: int = 1,
    line_end: int = 1,
    **extra,
) -> dict:
    data = {
        "action": action,
        "category": category,
        "content": content,
        "why": why,
        "source_kind": source_kind,
        "evidence": [
            {"path": path, "excerpt": excerpt, "line_start": line_start, "line_end": line_end}
        ],
        "target_profile_id": None,
        "uncertainty_reason": None,
    }
    data.update(extra)
    return data


def _tool_results(seen_item: dict) -> str:
    """某次模型调用收到的 user 消息正文（工具结果都从这里回给它）。"""
    return "\n".join(
        message["content"]
        for message in seen_item["payload"]["messages"]
        if message["role"] == "user"
    )


def _coverage_rows(conn) -> list[dict]:
    return [
        dict(row) for row in conn.execute("SELECT * FROM knowledge_coverage ORDER BY id")
    ]


def _profile_change_payloads(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT payload FROM proposal WHERE kind = 'profile_change' ORDER BY id"
    ).fetchall()
    return [json.loads(row["payload"]) for row in rows]


class ChangingTransport(ScriptedTransport):
    """第一次被调用时先把文件改成新内容——模拟「列目录之后、读取之前文件变了」。"""

    def __init__(self, path, new_content: str, *texts: str) -> None:
        super().__init__(*texts)
        self._path = path
        self._new_content = new_content

    def __call__(self, url: str, headers: dict, payload: dict):
        self._path.write_text(self._new_content, encoding="utf-8", newline="")
        return super().__call__(url, headers, payload)


# ---------- 受限工具 ----------


def test_list_tool_returns_relative_paths_only(conn, vault):
    """列目录只给相对路径：绝对路径、盘符、反斜杠一个都不能漏出去。"""
    make_provider(conn)
    _write(vault / "a.md", "# 笔记\n晚上十点以后不学习。\n")
    _write(vault / "notes" / "b.md", "正文。\n")
    transport = ScriptedTransport(_list_call(), _read_call("a.md"), _final())

    # 只点名 a.md：这一步验的是「列目录工具」的输出形状，不让第二个文件消耗脚本
    report = kb.scan(conn, 1, scope="paths", paths=["a.md"], transport=transport)

    assert report["status"] == "ok"
    tool_result = _tool_results(transport.seen[1])
    assert "a.md" in tool_result and "notes/b.md" in tool_result  # 整个根都列得出来
    assert "\\" not in tool_result
    assert str(vault) not in tool_result
    assert str(vault) not in json.dumps(transport.seen, ensure_ascii=False)


def test_forbidden_args_are_refused_to_the_model(conn, vault):
    """模型参数里出现 root_id / scan_id / scope 一律拒：错误原文回给模型让它改。"""
    make_provider(conn)
    _write(vault / "a.md", "# 笔记\n晚上十点以后不学习。\n")
    bad = json.dumps(
        {"tool_calls": [{"name": "knowledge_read_file", "args": {"path": "a.md", "root_id": 1}}]},
        ensure_ascii=False,
    )
    transport = ScriptedTransport(bad, _read_call("a.md"), _final())

    report = kb.scan(conn, 1, transport=transport)

    assert report["status"] == "ok"  # 拒完之后它改对了，文件照常处理完
    tool_result = _tool_results(transport.seen[1])
    assert "root_id" in tool_result and "系统注入" in tool_result
    # 被拒的那次不算读：整个扫描只有改对之后那一次读
    assert report["read_calls"] == 1


# ---------- 配额真实生效 ----------


def test_read_call_quota_is_enforced(conn, vault):
    """读调用次数上限：一次批量多要一次，最后一次被如实拒绝，账上只有上限次。"""
    make_provider(conn)
    _write(vault / "a.md", "# 笔记\n晚上十点以后不学习。\n")
    cap = kb.MAX_READ_CALLS
    reads = [{"name": "knowledge_read_file", "args": {"path": "a.md"}} for _ in range(cap + 1)]
    transport = ScriptedTransport(json.dumps({"tool_calls": reads}, ensure_ascii=False), _final())

    report = kb.scan(conn, 1, transport=transport)

    assert report["status"] == "ok"  # 第一次读就读完了全文，配额触顶不影响这个文件
    assert report["read_calls"] == cap
    row = conn.execute(
        "SELECT read_calls FROM knowledge_scan WHERE id = ?", (report["scan_id"],)
    ).fetchone()
    assert int(row["read_calls"]) == cap
    tool_result = _tool_results(transport.seen[1])
    assert tool_result.count("【knowledge_read_file】") == cap
    assert "次数额度" in tool_result


def test_single_file_truncation(conn, vault):
    """单篇截断：limit=10 只给 10 个字符，没读完就明说，文件不记 covered。"""
    make_provider(conn)
    _write(vault / "a.md", "甲乙丙丁戊己庚辛壬癸" * 4)  # 40 个字符
    transport = ScriptedTransport(_read_call("a.md", limit=10), _final())

    report = kb.scan(conn, 1, transport=transport)

    assert report["status"] == "partial"
    tool_result = _tool_results(transport.seen[1])
    assert "本次 10 字符" in tool_result and "未读完" in tool_result
    assert "甲乙丙丁戊己庚辛壬癸甲" not in tool_result  # 第 11 个字符没有给出去
    row = _coverage_rows(conn)[0]
    assert row["status"] == "partial" and row["error"]


def test_total_char_quota_is_enforced(conn, vault):
    """总字符额度用完就不再给读：账上正好卡在额度，剩下的如实记成缺口（数目跟着常量走）。"""
    make_provider(conn)
    per = kb.MAX_FILE_CHARS
    segments = kb.MAX_TOTAL_CHARS // per  # 用满额度要几段
    _write(vault / "big.md", "a" * (per * (segments + 2) + 10))
    reads = [
        {"name": "knowledge_read_file", "args": {"path": "big.md", "offset": index * per}}
        for index in range(segments + 2)  # 多要两段：一段把余额切成 0，再一段被拒
    ]
    transport = ScriptedTransport(json.dumps({"tool_calls": reads}, ensure_ascii=False), _final())

    report = kb.scan(conn, 1, transport=transport)

    assert report["chars_read"] == kb.MAX_TOTAL_CHARS
    assert report["status"] == "partial"  # 还有没读到的，不假装看完
    tool_result = _tool_results(transport.seen[1])
    assert "总字符额度" in tool_result
    assert any("big.md" in gap for gap in report["unread_gaps"])


# ---------- 覆盖账 ----------


def test_file_changed_after_listing_is_not_covered(conn, vault):
    """文件在列目录之后被改（changed_during_read）：不写 covered，候选也不落。"""
    make_provider(conn)
    _write(vault / "a.md", "# 旧版\n晚上十点以后不学习。\n")
    transport = ChangingTransport(vault / "a.md", "# 新版\n最近改吃素了。\n", _read_call("a.md"))

    report = kb.scan(conn, 1, transport=transport)

    assert report["status"] == "failed"
    assert "changed_during_read" in report["error"]
    assert conn.execute("SELECT COUNT(*) AS n FROM proposal").fetchone()["n"] == 0
    row = _coverage_rows(conn)[0]
    assert row["status"] == "failed" and "changed_during_read" in str(row["error"])


def test_bad_json_twice_fails_file_and_scan(conn, vault):
    """坏 JSON 重说一次仍不合格：文件不 covered、扫描 failed、一条候选都不落。"""
    make_provider(conn)
    _write(vault / "a.md", "# 笔记\n晚上十点以后不学习。\n")
    transport = ScriptedTransport("这不是 JSON", "还是不是 JSON")

    report = kb.scan(conn, 1, transport=transport)

    assert report["status"] == "failed"
    assert "没给出合格的提炼输出" in report["error"] and "2 次" in report["error"]
    assert report["candidates_created"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM proposal").fetchone()["n"] == 0
    assert all(row["status"] not in kb.COVERAGE_DONE for row in _coverage_rows(conn))


def test_model_failure_fails_scan_and_skips_the_rest(conn, vault):
    """模型调用失败：扫描 failed、不写 covered，后面的文件这次不再尝试（留缺口）。"""
    make_provider(conn)
    _write(vault / "a.md", "# 甲\n内容甲。\n")
    _write(vault / "b.md", "# 乙\n内容乙。\n")

    def boom(url, headers, payload):
        raise TimeoutError("上游挂了")

    report = kb.scan(conn, 1, transport=boom)

    assert report["status"] == "failed"
    assert "调用模型失败" in report["error"]
    assert report["files_considered"] == 1  # b 没有再试：模型这条路已经不通
    assert any("b.md" in gap for gap in report["unread_gaps"])
    assert [row["status"] for row in _coverage_rows(conn)] == ["failed"]


# ---------- 确定性校验与三种动作分流 ----------


def test_excerpt_not_found_drops_the_whole_card(conn, vault):
    """摘录逐字找不到：整张卡不落，扫描结果里记一句原因。"""
    make_provider(conn)
    _write(vault / "a.md", "# 笔记\n晚上十点以后不学习。\n")
    card = _card(excerpt="这句话文件里根本没有")
    transport = ScriptedTransport(_read_call("a.md"), _final(card))

    report = kb.scan(conn, 1, transport=transport)

    assert report["candidates_created"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM proposal").fetchone()["n"] == 0
    dropped = report["files"][0]["dropped"]
    assert dropped and "摘录" in dropped[0]["why"]
    assert report["status"] == "failed"  # 全部卡都栽在出处校验上：覆盖账不推进
    row = _coverage_rows(conn)[0]
    assert row["status"] == "failed" and "出处校验失败" in str(row["error"])


def test_one_fabricated_card_fails_the_whole_file_even_when_others_pass(conn, vault):
    """混合情形：一张卡摘录真实、一张卡摘录编造——出处校验没全过，整份不落、覆盖账不推进。

    §7.4 的逐条条件：只要有一张卡没过出处校验，covered 就不许写；已过校验的卡这次也
    不落（账要么整笔干净、要么整笔重来），否则编造的卡被静默丢弃、文件还照常记「看完」。
    """
    make_provider(conn)
    _write(vault / "a.md", "# 笔记\n晚上十点以后不学习。\n最近精力偏低。\n")
    cards = [
        _card(excerpt="晚上十点以后不学习。"),  # 摘录真实
        _card(content="最近精力偏低", excerpt="这句话文件里没有"),  # 摘录是编造的
    ]
    transport = ScriptedTransport(_read_call("a.md"), _final(*cards))

    report = kb.scan(conn, 1, transport=transport)

    assert report["status"] == "failed"
    assert report["candidates_created"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM proposal").fetchone()["n"] == 0
    row = _coverage_rows(conn)[0]
    assert row["status"] == "failed"
    error = str(row["error"])
    assert "出处校验失败" in error
    assert "2 张卡里有 1 张" in error  # 与「全部失败」不同：说清是部分没过
    assert "1 张这次也不落" in error  # 过了校验的卡一并重来，不留半真半假的版本


def test_add_supersede_and_uncertain_all_land(conn, vault):
    """add / supersede / uncertain 三种分流：都落成 profile_change 提案，payload 带全出处。"""
    make_provider(conn)
    _write(
        vault / "a.md",
        "晚上十点以后不学习，雷打不动。\n最近精力偏低，下午最差。\n好像在准备一场考试。\n",
    )
    target_id = ledger.create_active(
        conn, "profile_item", {"category": "current_state", "content": "最近精力偏低"}, actor="user"
    )
    cards = [
        _card(action="add", content="晚上十点以后不学习", excerpt="晚上十点以后不学习，雷打不动。"),
        _card(
            action="supersede",
            content="最近精力偏低，下午最差",
            why="笔记更新了状态",
            target_profile_id=target_id,
            excerpt="最近精力偏低，下午最差。",
        ),
        _card(
            action="uncertain",
            content="好像在准备一场考试",
            why=None,
            uncertainty_reason="只提了一句，分不清是临时的还是长期状态",
            excerpt="好像在准备一场考试。",
        ),
    ]
    transport = ScriptedTransport(_read_call("a.md"), _final(*cards))

    report = kb.scan(conn, 1, transport=transport)

    assert report["status"] == "ok" and report["candidates_created"] == 3
    payloads = _profile_change_payloads(conn)
    assert [payload["action"] for payload in payloads] == ["add", "supersede", "uncertain"]
    assert payloads[1]["target_profile_id"] == target_id
    assert payloads[1]["target_content"] == "最近精力偏低"
    assert payloads[2]["uncertainty_reason"]
    for payload in payloads:
        assert payload["knowledge"] is True
        assert payload["evidence"][0]["root_alias"] == "笔记"
        assert payload["evidence"][0]["relative_path"] == "a.md"
        assert payload["requirements_version"] == kb.REQUIREMENTS_VERSION_DEFAULT
        assert payload["scan_id"] == report["scan_id"]
    row = _coverage_rows(conn)[0]
    assert row["status"] == "covered" and row["candidate_count"] == 3


def test_exact_duplicate_blocked_and_near_duplicate_hinted(conn, vault):
    """同类别一字不差的现行条目＝完全重复，整张卡丢；近似重复只塞 duplicate_hint。"""
    make_provider(conn)
    _write(vault / "a.md", "我晚上十点以后不安排学习。\n")
    ledger.create_active(
        conn,
        "profile_item",
        {"category": "current_state", "content": "我晚上十点以后不安排学习"},
        actor="user",
    )
    cards = [
        _card(content="我晚上十点以后不安排学习", excerpt="我晚上十点以后不安排学习。"),
        _card(content="我晚上十点以后不安排学习了", excerpt="我晚上十点以后不安排学习。"),
    ]
    transport = ScriptedTransport(_read_call("a.md"), _final(*cards))

    report = kb.scan(conn, 1, transport=transport)

    assert report["status"] == "ok" and report["candidates_created"] == 1
    assert len(report["files"][0]["dropped"]) == 1
    assert "完全重复" in report["files"][0]["dropped"][0]["why"]
    payloads = _profile_change_payloads(conn)
    assert len(payloads) == 1 and payloads[0]["duplicate_hint"]


# ---------- 再扫一遍 ----------


def test_rescanning_same_version_creates_a_second_scan_record(conn, vault):
    """同一版本再扫一遍：增量跳过已覆盖的文件（不调模型）、照样记新的一行扫描账。"""
    make_provider(conn)
    _write(vault / "a.md", "# 笔记\n晚上十点以后不学习。\n")
    first = kb.scan(conn, 1, transport=ScriptedTransport(_read_call("a.md"), _final()))
    assert first["status"] == "ok"

    second = kb.scan(conn, 1, transport=ScriptedTransport())
    assert second["status"] == "ok"
    assert second["files_considered"] == 0
    assert second["skipped_already_covered"] == ["a.md"]

    third = kb.scan(
        conn,
        1,
        mode="full",
        transport=ScriptedTransport(
            _read_call("a.md"),
            _final(_card(content="晚上十点以后不学习，雷打不动", excerpt="晚上十点以后不学习。")),
        ),
    )
    assert third["files_considered"] == 1 and third["candidates_created"] == 1

    ids = [
        int(row["id"])
        for row in conn.execute("SELECT id FROM knowledge_scan ORDER BY id").fetchall()
    ]
    assert ids == [first["scan_id"], second["scan_id"], third["scan_id"]]


def test_unknown_trigger_is_rejected(conn):
    with pytest.raises(kb.KnowledgeError, match="触发"):
        kb.scan(conn, 1, trigger="nope")
