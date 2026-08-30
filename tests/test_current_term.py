"""Phase 2：本學期真實資料。

最關鍵的一條：**沒設定就是不知道，不准拿歷年資料頂替。**
"""

from __future__ import annotations

from app.services import current_term as term
from app.tools import term as term_tool


def test_missing_file_means_everything_unknown(clean_term):
    state = term.load()
    assert state.exists is False
    assert state.known() == {}
    assert set(state.missing()) == {f.key for f in term.FIELDS}


def test_empty_string_counts_as_unknown(clean_term):
    term.update({"president": "", "academic_year": "115"})
    state = term.load()
    assert state.get("president") is None
    assert state.get("academic_year") == "115"


def test_literal_unknown_counts_as_unknown(clean_term):
    term.update({"president": "unknown"})
    assert term.load().get("president") is None


def test_artifact_value_falls_back_to_placeholder(clean_term):
    term.update({"academic_year": "115"})
    state = term.load()
    assert state.for_artifact("academic_year") == "115"
    assert state.for_artifact("president") == term.PLACEHOLDER


def test_update_round_trips(clean_term):
    term.update(
        {
            "academic_year": "115",
            "semester": "上學期",
            "president": "林小明",
            "officers": "社長：林小明\n總務：陳小美",
        }
    )
    state = term.load()
    assert state.get("president") == "林小明"
    assert "陳小美" in (state.get("officers") or "")
    assert state.updated_at


def test_update_ignores_unknown_keys(clean_term):
    term.update({"academic_year": "115", "惡意欄位": "x", "__proto__": "y"})
    assert "惡意欄位" not in term.load().values


def test_cache_invalidates_on_write(clean_term):
    term.update({"president": "甲"})
    assert term.load().get("president") == "甲"
    term.update({"president": "乙"})
    assert term.load().get("president") == "乙"


def test_prompt_block_warns_when_unset(clean_term):
    block = term.load().prompt_block()
    assert "完全沒有設定" in block
    assert "不可以從歷年檔案推測" in block


def test_prompt_block_lists_missing_fields(clean_term):
    term.update({"academic_year": "115", "semester": "上學期"})
    block = term.load().prompt_block()
    assert "115" in block
    assert "還沒設定" in block
    assert "社長" in block


def test_term_label(clean_term):
    assert term.load().term_label() == "（本學期未設定）"
    term.update({"academic_year": "115", "semester": "上學期"})
    assert term.load().term_label() == "115 學年度上學期"


def test_tool_reports_unknown_without_guessing(clean_term):
    r = term_tool.get_current_term(keys="president")
    assert r["ok"]
    assert "還沒設定" in r["message"]
    assert "不可以從歷年檔案" in r["message"]
    assert r["unknown_keys"] == ["president"]


def test_tool_returns_known_value(clean_term):
    term.update({"president": "林小明"})
    r = term_tool.get_current_term(keys="president")
    assert "林小明" in r["message"]
    assert r["known_keys"] == ["president"]


def test_tool_accepts_chinese_labels(clean_term):
    term.update({"club_fee": "每學期 300 元"})
    r = term_tool.get_current_term(keys="社費")
    assert "300" in r["message"]


def test_tool_defaults_to_all_fields(clean_term):
    term.update({"academic_year": "115"})
    r = term_tool.get_current_term()
    assert len(r["known_keys"]) + len(r["unknown_keys"]) == len(term.FIELDS)


def test_as_form_shape(clean_term):
    term.update({"academic_year": "115"})
    form = term.as_form()
    keys = {f["key"] for f in form["fields"]}
    assert keys == {f.key for f in term.FIELDS}
    assert form["exists"] is True
    year = next(f for f in form["fields"] if f["key"] == "academic_year")
    assert year["known"] is True and year["value"] == "115"


def test_weekly_schedule_is_registered_and_round_trips(clean_term):
    assert "weekly_schedule" in term.FIELD_BY_KEY
    assert term.FIELD_BY_KEY["weekly_schedule"].kind == "longtext"
    table = (
        "W1  2026/9/14–9/20     招生期\n"
        "W3  2026/9/30（三）     期初茶會\n"
        "W4  2026/10/7（三）     期初演講\u300019:00–21:30\u3000講師盧玫竹"
    )
    term.update({"weekly_schedule": table, "recruitment_period": "2026/9/14–9/27"})
    state = term.load()
    assert "期初茶會" in (state.get("weekly_schedule") or "")
    assert "盧玫竹" in (state.get("weekly_schedule") or "")
    form = term.as_form()
    field = next(f for f in form["fields"] if f["key"] == "weekly_schedule")
    assert field["kind"] == "longtext"
    assert field["known"] is True
    block = state.prompt_block()
    assert "本學期週次表" in block
    assert "期初演講" in block
    by_key = term_tool.get_current_term(keys="weekly_schedule")
    assert "期初茶會" in by_key["message"]
    by_label = term_tool.get_current_term(keys="週次")
    assert "盧玫竹" in by_label["message"]
