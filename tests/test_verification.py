"""Phase 6：產出驗證與自動修正。"""

from __future__ import annotations

import pytest

from app import verification
from app.services import current_term as term
from app.tools import document, spreadsheet


@pytest.fixture()
def term_115(clean_term):
    term.update({"academic_year": "115", "semester": "上學期"})
    return term.load()


def _make_doc(tmp_output_dir, markdown: str, name: str = "測試"):
    r = document.create_document(filename=name, markdown=markdown)
    assert r["ok"], r["message"]
    from pathlib import Path

    return Path(r["local_path"])


def _make_sheet(tmp_output_dir, sheets, name: str = "測試表"):
    r = spreadsheet.create_spreadsheet(filename=name, sheets=sheets)
    assert r["ok"], r["message"]
    from pathlib import Path

    return Path(r["local_path"])


# ── 療效宣稱 ─────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text",
    [
        "禪定可以治療焦慮症。",
        "保證成功考上研究所。",
        "參加社課能改善憂鬱。",
        "打坐可以改運。",
    ],
)
def test_health_claims_are_errors(tmp_output_dir, term_115, text):
    path = _make_doc(tmp_output_dir, f"# 招生文案\n\n{text}\n\n這是一段補充說明讓字數足夠通過長度檢查。")
    report = verification.verify(path, task_type="recruitment", rules=["no_health_claims"])
    assert not report.ok
    assert any(i.rule == "no_health_claims" for i in report.errors)


def test_safe_wording_passes(tmp_output_dir, term_115):
    path = _make_doc(
        tmp_output_dir,
        "# 招生文案\n\n禪定是幫助放鬆、練習專注的方法，可以讓腦袋休息一下，更了解自己。",
    )
    report = verification.verify(path, task_type="", rules=["no_health_claims"])
    assert report.ok, [i.message for i in report.errors]


# ── 宗教招募 ─────────────────────────────────────────────────

def test_religious_framing_in_external_doc_is_error(tmp_output_dir, term_115):
    path = _make_doc(
        tmp_output_dir,
        "# 招生\n\n歡迎皈依我們的法門，一起參加法會與供養，共修佛法功德迴向給家人。",
    )
    report = verification.verify(path, task_type="recruitment", rules=["external_tone"], external=True)
    assert not report.ok
    assert any(i.rule == "not_religious_recruitment" for i in report.errors)


def test_internal_doc_may_use_club_vocabulary(tmp_output_dir, term_115):
    path = _make_doc(
        tmp_output_dir,
        "# 幹部交接\n\n家族長要負責凝聚與護持，帶社員一起發願、參加共修，把光的種子散播出去。",
    )
    report = verification.verify(path, task_type="handover", rules=["not_religious_recruitment"], external=False)
    assert report.ok, [i.message for i in report.errors]


# ── 舊年度冒充今年 ───────────────────────────────────────────

def test_stale_year_claimed_as_current_is_error(tmp_output_dir, term_115):
    path = _make_doc(
        tmp_output_dir,
        "# 期初茶會企劃書\n\n本學期是 113 學年度，我們預計辦理期初茶會，目標六十人出席。",
    )
    report = verification.verify(path, rules=["no_stale_year_as_current"])
    assert not report.ok
    msg = " ".join(i.message for i in report.errors)
    assert "113" in msg


def test_referencing_past_year_as_example_is_only_a_warning(tmp_output_dir, term_115):
    path = _make_doc(
        tmp_output_dir,
        "# 期初茶會企劃書\n\n參考 113 學年度的做法，流程分成報到、體驗禪、手作三段。",
    )
    report = verification.verify(path, rules=["no_stale_year_as_current"])
    assert report.ok
    assert any(i.rule == "no_stale_year_as_current" for i in report.warnings)


def test_current_year_alone_is_clean(tmp_output_dir, term_115):
    path = _make_doc(tmp_output_dir, "# 企劃書\n\n本學期是 115 學年度，我們要辦期初茶會，流程如下所述。")
    report = verification.verify(path, rules=["no_stale_year_as_current"])
    assert report.ok


# ── 未知事實必須留待填 ───────────────────────────────────────

def test_asserting_unknown_president_is_error(tmp_output_dir, term_115):
    path = _make_doc(tmp_output_dir, "# 企劃書\n\n主辦單位：領袖禪學社\n社長：陳大明\n地點：H116")
    report = verification.verify(path, rules=["placeholder_for_unknown"])
    assert not report.ok
    assert any(i.rule == "placeholder_for_unknown" for i in report.errors)


def test_placeholder_for_president_is_accepted(tmp_output_dir, term_115):
    path = _make_doc(tmp_output_dir, "# 企劃書\n\n主辦單位：領袖禪學社\n社長：待填\n地點：待填")
    report = verification.verify(path, rules=["placeholder_for_unknown"])
    assert report.ok


def test_known_president_may_be_stated(tmp_output_dir, clean_term):
    term.update({"academic_year": "115", "president": "陳大明"})
    path = _make_doc(tmp_output_dir, "# 企劃書\n\n社長：陳大明\n本學期活動規劃如下所述，內容包含流程與分工。")
    report = verification.verify(path, rules=["placeholder_for_unknown"])
    assert report.ok


# ── 必要章節 ─────────────────────────────────────────────────

def test_missing_required_sections(tmp_output_dir, term_115):
    path = _make_doc(tmp_output_dir, "# 企劃書\n\n這份文件只有一段話，沒有任何結構化章節在裡面存在。")
    report = verification.verify(path, task_type="event_planning", rules=["required_sections"])
    assert any(i.rule == "required_sections" for i in report.issues)


def test_complete_sections_pass(tmp_output_dir, term_115):
    path = _make_doc(
        tmp_output_dir,
        "# 期初茶會企劃書\n\n## 目標\n\n六十人出席。\n\n## 流程\n\n報到、體驗禪、手作、社團介紹。",
    )
    report = verification.verify(path, task_type="event_planning", rules=["required_sections"])
    assert report.ok, [i.message for i in report.errors]


# ── 試算表 ───────────────────────────────────────────────────

def test_finance_sheet_without_formula_is_error(tmp_output_dir, term_115):
    path = _make_sheet(
        tmp_output_dir,
        [{"name": "預算", "csv": "項目,單價,數量,小計\n海報,120,10,1200\n總計,,,1200"}],
    )
    report = verification.verify(path, task_type="finance")
    assert not report.ok
    assert any(i.rule == "formulas_present" for i in report.errors)


def test_finance_sheet_with_formula_passes(tmp_output_dir, term_115):
    path = _make_sheet(
        tmp_output_dir,
        [{"name": "預算", "csv": "項目,單價,數量,小計\n海報,120,10,=B2*C2\n總計,,,=SUM(D2:D2)"}],
    )
    report = verification.verify(path, task_type="finance")
    assert report.ok, [i.message for i in report.errors]


def test_sheet_without_header_is_flagged(tmp_output_dir, term_115):
    path = _make_sheet(tmp_output_dir, [{"name": "資料", "csv": "單一欄\n值1\n值2"}])
    report = verification.verify(path, task_type="")
    assert any(i.rule == "has_header" for i in report.issues)


def test_empty_sheet_is_warned(tmp_output_dir, term_115):
    path = _make_sheet(tmp_output_dir, [{"name": "空的", "csv": "欄1,欄2"}])
    report = verification.verify(path, task_type="")
    assert any(i.rule == "no_empty_sheet" for i in report.warnings)


# ── 其他 ─────────────────────────────────────────────────────

def test_missing_file_is_error(tmp_path):
    report = verification.verify(tmp_path / "nope.docx")
    assert not report.ok


def test_near_empty_document_is_error(tmp_output_dir, term_115):
    path = _make_doc(tmp_output_dir, "短", name="幾乎空白")
    report = verification.verify(path)
    assert not report.ok


def test_repair_instruction_is_actionable(tmp_output_dir, term_115):
    path = _make_doc(tmp_output_dir, "# 文案\n\n禪定可以治療失眠，保證成功。這是補充說明讓長度足夠。")
    report = verification.verify(path, rules=["no_health_claims"])
    instruction = report.repair_instruction()
    assert "用同一個工具重做一次" in instruction
    assert "治療" in instruction or "保證成功" in instruction
    assert "只修這些問題" in instruction


def test_clean_report_has_no_repair_instruction(tmp_output_dir, term_115):
    path = _make_doc(tmp_output_dir, "# 文件\n\n這是一份完全正常的文件內容，沒有任何違規用語出現在裡面。")
    report = verification.verify(path, rules=["no_health_claims"])
    assert report.repair_instruction() == ""
