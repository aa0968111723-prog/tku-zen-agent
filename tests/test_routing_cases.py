"""任務分類的驗收案例。

背景：曾經因為 social_research 的關鍵字裡放了「禪學社」「領袖社」，
「請用一句話介紹淡江大學領袖禪學社」被誤判為外校社群研究，
還去翻了一堆外部參考資料。這裡把誤判案例與正確案例都釘死。
"""

from __future__ import annotations

import pytest

from app.skills import has_external_intent, is_lookup, route

# ── 指定的六個驗收案例 ────────────────────────────────────────

ACCEPTANCE = [
    ("介紹我們社團", "knowledge"),
    ("查本學期活動", "knowledge"),
    ("研究其他學校怎麼招生", "social_research"),
    ("比較台大與淡江 IG", "social_research"),
    ("幫我寫招生貼文", "social_publicity"),
    ("幫我做期初茶會企劃", "event_planning"),
]


@pytest.mark.parametrize("message,expected", ACCEPTANCE)
def test_acceptance_cases(message: str, expected: str):
    assert route(message).skill.name == expected


# ── 誤判回歸：自家名字不可觸發外校研究 ─────────────────────────

OWN_NAME_QUESTIONS = [
    "請用一句話介紹淡江大學領袖禪學社",
    "介紹一下領袖禪學社",
    "禪學社是什麼樣的社團",
    "領袖禪學社在做什麼",
]


@pytest.mark.parametrize("message", OWN_NAME_QUESTIONS)
def test_own_club_name_never_triggers_external_research(message: str):
    routing = route(message)
    assert routing.skill.name != "social_research", (
        f"{message!r} 不可以被判成外校研究（訊息裡只有我們自己的名字）"
    )
    assert routing.skill.name == "knowledge"
    assert routing.produce_artifact is False


def test_own_club_name_has_no_external_intent():
    assert not has_external_intent("請用一句話介紹淡江大學領袖禪學社")
    assert not has_external_intent("介紹我們社團")


def test_explicit_external_phrases_have_intent():
    assert has_external_intent("研究其他學校怎麼招生")
    assert has_external_intent("比較外校的做法")
    assert has_external_intent("找公開 IG 帳號參考")


# ── 查詢類不產檔 ─────────────────────────────────────────────

@pytest.mark.parametrize(
    "message",
    ["查本學期活動", "幫我查社團資料：我們是做什麼的", "查一下社費多少", "社長是誰"],
)
def test_lookup_messages_do_not_produce_artifacts(message: str):
    routing = route(message)
    assert routing.produce_artifact is False
    assert routing.skill.name == "knowledge"
    # 純查詢不暴露任何 create_* 工具，模型不可能手滑產檔
    assert not any(t.startswith("create_") for t in routing.skill.tool_names())


def test_lookup_with_make_verb_still_produces():
    """「幫我寫一份社團介紹文宣」有明確產檔動詞，不算純查詢。"""
    assert not is_lookup("幫我寫一份社團介紹文宣")
    assert route("幫我寫一份社團介紹文宣").produce_artifact is True


# ── 外校研究不會拿到淡江產檔工具 ─────────────────────────────

def test_research_skill_tools_are_reference_only():
    routing = route("研究其他學校怎麼招生")
    for tool in routing.skill.tool_names():
        assert not tool.startswith("create_"), "外校研究不該直接產出文宣檔案"
