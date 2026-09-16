"""档案写入（录入 / 取代 / 作废）的单测。

同 test_api_contract.py 的态度：不起 HTTP 服务，直接调路由函数与请求模型——
FastAPI 的校验就是 Pydantic，路由函数就是普通函数，等价且不引入新依赖。

台账语义在这里是主角：取代必须留旧值与理由、作废必须写理由、
历史行不许再被动——这些是 SPEC 第 8 节铁律在本接口的落点。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import advisor, db, ledger, main
from app.main import ProfileItemIn, ProfileItemUpdate, ProfileItemVoidIn


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "test.db"
    db.init(path)
    connection = db.connect(path)
    yield connection
    connection.close()


def create(conn, category="long_axis", content="测试主线", **_):
    return main.post_profile_item(ProfileItemIn(category=category, content=content), conn)


# ---------- 入参校验（边界在 Pydantic，422 那一层） ----------


@pytest.mark.parametrize(
    "category", ["life_habit", "life_log", "current_state", "short_term_goal", "long_axis"]
)
def test_category_accepts_the_five_tokens(category, conn):
    """五个约定令牌都收——这五个就是 advisor.PROFILE_CATEGORIES 的键。"""
    assert create(conn, category=category)["category"] == category


def test_category_outside_vocabulary_is_rejected():
    """表外类别会让四问的「缺失类别」判断失真，边界处就拒掉。"""
    with pytest.raises(ValidationError):
        ProfileItemIn(category="别的什么", content="x")


def test_empty_content_and_reason_are_rejected():
    """content / reason 不许为空：空内容没意义，空理由让台账回答不了「为什么」。"""
    with pytest.raises(ValidationError):
        ProfileItemIn(category="long_axis", content="")
    with pytest.raises(ValidationError):
        ProfileItemUpdate(content="", reason="x")
    with pytest.raises(ValidationError):
        ProfileItemUpdate(content="x", reason="")
    with pytest.raises(ValidationError):
        ProfileItemVoidIn(reason="")


# ---------- 新增 ----------


def test_create_lands_active_and_visible_in_profile_view(conn):
    created = create(conn, category="current_state", content="每天能投入 2 小时")

    view = advisor.read_profile(conn)
    landed = next(item for item in view["items"] if item["id"] == created["id"])
    assert (landed["category"], landed["content"]) == ("current_state", "每天能投入 2 小时")
    assert "current_state" not in view["missing_categories"]


def test_same_category_allows_multiple_active_items(conn):
    """档案不是键值对：两条短期目标可以并存，互不挤掉（挤掉是「取代」的事）。"""
    first = create(conn, category="short_term_goal", content="目标一")
    second = create(conn, category="short_term_goal", content="目标二")

    assert first["id"] != second["id"]
    categories = [item["id"] for item in advisor.read_profile(conn)["items"]]
    assert first["id"] in categories and second["id"] in categories


def test_whitespace_only_content_is_rejected_as_400(conn):
    """Pydantic 只挡空串，纯空白串漏到台账层——create_active 的非空校验兜住，报 400。"""
    with pytest.raises(HTTPException) as caught:
        create(conn, content="   ")
    assert caught.value.status_code == 400


# ---------- 防一字不差的重复（同 T19 的思路，但只挡当前有效条目） ----------


def test_exact_duplicate_is_409_and_names_the_existing_id(conn):
    first = create(conn, category="short_term_goal", content="三周内上线第一个项目")

    with pytest.raises(HTTPException) as caught:
        create(conn, category="short_term_goal", content="三周内上线第一个项目")
    assert caught.value.status_code == 409
    assert str(first["id"]) in caught.value.detail


def test_duplicate_check_ignores_leading_and_trailing_whitespace(conn):
    create(conn, content="每天投入 2 小时")

    with pytest.raises(HTTPException) as caught:
        create(conn, content="  每天投入 2 小时  ")
    assert caught.value.status_code == 409


def test_same_content_in_different_category_is_allowed(conn):
    """格位不同不算重复：同样的字放在「短期目标」和「生活记录」是两条不同的判据。"""
    create(conn, category="short_term_goal", content="每周跑步三次")
    create(conn, category="life_log", content="每周跑步三次")  # 不抛即通过


def test_refill_after_void_is_allowed(conn):
    """作废后重新补一样的文字是正当需求——判重只看当前有效的条目。"""
    item_id = create(conn, content="旧目标，先作废")["id"]
    main.void_profile_item(item_id, ProfileItemVoidIn(reason="过期了"), conn)

    recreated = create(conn, content="旧目标，先作废")
    assert recreated["id"] != item_id


# ---------- 取代 ----------


def test_update_supersedes_and_keeps_history(conn):
    item_id = create(conn, content="旧主线：先把 Python 学完")["id"]

    result = main.put_profile_item(
        item_id, ProfileItemUpdate(content="新主线：通用工程基础", reason="方向变了"), conn
    )

    # 新条目生效、旧条目留痕但不再出现
    assert result["superseded"] == item_id
    items = {item["id"]: item["content"] for item in advisor.read_profile(conn)["items"]}
    assert items == {result["id"]: "新主线：通用工程基础"}

    # 台账流水能回答「为什么改」：before / after / 理由俱全
    events = ledger.history(conn, "profile_item", item_id)
    supersede_events = [event for event in events if event["change_type"] == "supersede"]
    assert len(supersede_events) == 1
    assert supersede_events[0]["before_value"] == "旧主线：先把 Python 学完"
    assert supersede_events[0]["after_value"] == "新主线：通用工程基础"
    assert supersede_events[0]["reason"] == "方向变了"


def test_update_inherits_category_without_touching_it(conn):
    """取代只改 content：category 从旧行继承——这是台账 supersede 的合并语义。"""
    item_id = create(conn, category="life_habit", content="晚上学")["id"]

    result = main.put_profile_item(item_id, ProfileItemUpdate(content="早上学", reason="作息调整"), conn)

    assert result["category"] == "life_habit"


def test_update_missing_item_is_404(conn):
    with pytest.raises(HTTPException) as caught:
        main.put_profile_item(999, ProfileItemUpdate(content="x", reason="y"), conn)
    assert caught.value.status_code == 404


def test_update_superseded_item_is_409(conn):
    """历史行不许再动：要先改，就改当前生效的那条（result["id"]）。"""
    old_id = create(conn, content="v1")["id"]
    result = main.put_profile_item(old_id, ProfileItemUpdate(content="v2", reason="改"), conn)

    with pytest.raises(HTTPException) as caught:
        main.put_profile_item(old_id, ProfileItemUpdate(content="v3", reason="再改"), conn)
    assert caught.value.status_code == 409


# ---------- 作废 ----------


def test_void_hides_item_and_requires_active(conn):
    item_id = create(conn, content="过期了")["id"]

    assert main.void_profile_item(item_id, ProfileItemVoidIn(reason="信息过期"), conn) == {
        "id": item_id,
        "voided": True,
    }
    assert advisor.read_profile(conn)["items"] == []

    # 已作废的再作废一次：与现状冲突，409
    with pytest.raises(HTTPException) as caught:
        main.void_profile_item(item_id, ProfileItemVoidIn(reason="再作废"), conn)
    assert caught.value.status_code == 409


def test_void_missing_item_is_404_and_blank_reason_is_400(conn):
    with pytest.raises(HTTPException) as not_found:
        main.void_profile_item(999, ProfileItemVoidIn(reason="x"), conn)
    assert not_found.value.status_code == 404

    item_id = create(conn)["id"]
    # 纯空白理由剥完是空串，台账拒收——「作废必须写明理由」不是摆设
    with pytest.raises(HTTPException) as bad_request:
        main.void_profile_item(item_id, ProfileItemVoidIn(reason="   "), conn)
    assert bad_request.value.status_code == 400


def test_void_leaves_ledger_trace(conn):
    item_id = create(conn, content="会被作废的一条")["id"]

    main.void_profile_item(item_id, ProfileItemVoidIn(reason="测试作废"), conn)

    events = ledger.history(conn, "profile_item", item_id)
    void_events = [event for event in events if event["change_type"] == "void"]
    assert len(void_events) == 1
    assert void_events[0]["before_value"] == "会被作废的一条"
    assert void_events[0]["reason"] == "测试作废"
