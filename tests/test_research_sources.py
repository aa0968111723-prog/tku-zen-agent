"""外校研究的來源可信度。

紅線：不可以輸出「公開網路搜尋」這種空來源、不可以有沒網址的學校案例、
不可以把內部參考庫偽裝成即時研究。查不到就要老實說查不到。
"""

from __future__ import annotations

import re

from app.tools import social

REQUIRED_FIELDS = (
    "source_title",
    "source_url",
    "publisher",
    "captured_at",
    "source_type",
    "supporting_excerpt",
    "access_status",
)


# ── 結構化欄位 ───────────────────────────────────────────────

def test_every_reference_has_all_structured_fields(index):
    result = social.search_social_references("外校禪學社招生怎麼做")
    refs = result.get("references") or []
    assert refs, "參考庫有資料時就該引用得出來"
    for src in refs:
        for field in REQUIRED_FIELDS:
            assert field in src, f"缺少欄位 {field}"


def test_verified_references_have_url_and_date(index):
    refs = social.search_social_references("外校社群定位").get("references") or []
    verified = [r for r in refs if r.get("verified")]
    assert verified, "應該有通過驗證的來源"
    for src in verified:
        assert re.match(r"^https?://", str(src["source_url"])), src["source_url"]
        assert re.match(r"^\d{4}-\d{2}-\d{2}", str(src["captured_at"])), src["captured_at"]
        assert src["publisher"], "來源必須標明發布者"


def test_access_status_declares_reference_library_not_live_search(index):
    """不能把內部知識庫內容偽裝成外部即時研究。"""
    result = social.search_social_references("外校社群定位")
    for src in result.get("references") or []:
        assert "參考庫" in src["access_status"]
    assert "非本次即時網路搜尋" in result["message"]


def test_no_vague_source_labels(index):
    result = social.search_social_references("外校招生")
    blob = result["message"]
    for banned in ("公開網路搜尋", "網路搜尋結果", "根據搜尋"):
        assert banned not in blob, f"不可出現空泛來源說法：{banned}"


def test_message_lists_source_urls_and_dates(index):
    message = social.search_social_references("外校招生")["message"]
    assert "https://" in message, "來源網址要直接出現在輸出裡"
    assert "檢索日期" in message


# ── 查不到來源時的誠實聲明 ───────────────────────────────────

def test_disclaimer_when_no_verifiable_source(monkeypatch):
    monkeypatch.setattr(social, "_references", lambda *a, **k: [])
    result = social.compare_social_strategies("完全查不到的主題")
    assert result["no_verified_source"] is True
    assert result["disclaimer"] == (
        "本次無法驗證公開來源，以下僅為一般策略推測，不得視為該校現況。"
    )
    assert result["disclaimer"] in result["message"]
    assert result["references"] == []


def test_no_sources_means_no_invented_findings(monkeypatch):
    monkeypatch.setattr(social, "_references", lambda *a, **k: [])
    result = social.analyze_social_positioning("某個主題")
    # 查不到來源時不可以有「他校怎麼做」的內容區塊
    assert result["sections"] == {}
    assert "沒有" in result["message"]


# ── 提示規範 ─────────────────────────────────────────────────

def test_skill_guidance_forbids_pretending_live_search():
    from app.skills import SKILL_BY_NAME

    guidance = SKILL_BY_NAME["social_research"].extra_guidance
    assert "不是即時網路搜尋" in guidance
    assert "來源" in guidance
