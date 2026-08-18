"""Baseline：保護既有的 BM25 檢索行為。

這些測試在 v2 改動之前就要綠，改動之後也必須維持綠 ——
它們定義了「不可以退步」的底線。
"""

from __future__ import annotations

import pytest

from app import retrieval

# (查詢, 期望出現在 top-5 來源裡的關鍵字)
HOWTO_CASES = [
    ("招生管道", "招生"),
    ("這學期招生要怎麼規劃", "招生企劃"),
    ("社課當天流程時間表", "社課排程"),
    ("凝聚分享要問什麼問題", "社課排程"),
    ("戶外營隊安全注意事項", "營隊籌備"),
    ("IG 貼文怎麼寫", "文宣"),
    ("幹部要交接什麼東西", "幹部交接"),
    ("社團評鑑要交什麼資料", "社團評鑑"),
    ("報名表要問什麼題目", "表單設計"),
    ("經費核銷注意事項", "經費預算"),
    ("青年領袖十二項特質", "十二項特質"),
    ("社課主題庫", "社課主題庫"),
    ("社團定位 我們是誰", "我們是誰"),
]


@pytest.mark.parametrize("query,expect", HOWTO_CASES)
def test_howto_queries_hit_curated(index, query, expect):
    hits = index.search(query, k=5)
    sources = [c.source for _, c in hits]
    assert any(expect in s for s in sources), f"{query} → {sources}"


def test_index_builds_and_is_not_trivial(index):
    stats = index.stats()
    assert stats["段落數"] > 80
    assert "社團知識庫" in stats["來源"]
    assert "任務劇本" in stats["來源"]


def test_no_empty_chunks(index):
    """純標題段落會因為標題命中而排前面，但點進去沒內容 —— 不該存在。"""
    tiny = [c for c in index.chunks if len(c.text.strip()) < 50]
    assert len(tiny) <= 5, f"過短段落 {len(tiny)} 個"


def test_stopword_bigrams_are_filtered():
    tokens = retrieval.tokenize("社課有哪些主題可以選")
    assert "有哪" not in tokens
    assert "可以" not in tokens
    assert "社課" in tokens
    assert "主題" in tokens


def test_heading_terms_are_weighted(index):
    """〈社課主題庫〉整段都是課程名稱，內文幾乎不出現「社課」——靠標題加權才找得到。"""
    hits = index.search("社課主題", k=3)
    assert any("社課主題庫" in c.source for _, c in hits)


def test_offtopic_query_is_low_confidence(index):
    _hits, confidence = index.search_scored("紅燒牛肉麵的湯頭怎麼熬", k=3)
    assert confidence < index.LOW_CONFIDENCE


def test_ontopic_query_is_confident(index):
    _hits, confidence = index.search_scored("招生管道", k=3)
    assert confidence >= index.LOW_CONFIDENCE


def test_curated_slots_are_reserved(index):
    """匯入歷年檔案後，精選劇本不可以被整個擠出結果。"""
    has_archive = any(c.tier < retrieval.TIER_CURATED for c in index.chunks)
    if not has_archive:
        pytest.skip("沒有匯入歷年檔案，這條不適用")
    for query in ["社課當天流程時間表", "招生要怎麼規劃", "營隊安全", "評鑑要交什麼"]:
        hits = index.search(query, k=5)
        curated = [c for _, c in hits if c.tier >= retrieval.TIER_CURATED]
        assert curated, f"{query} 的前 5 名完全沒有精選內容"


def test_search_is_fast(index):
    import time

    t0 = time.perf_counter()
    for _ in range(20):
        index.search("期初茶會企劃書要寫什麼", k=5)
    per_query = (time.perf_counter() - t0) / 20
    assert per_query < 0.15, f"單次查詢 {per_query*1000:.0f}ms，太慢"
