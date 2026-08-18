"""Phase 4：Hybrid RAG 的 regression。

驗收條件（來自規格）：
  · 同類歷史範例 + curated/playbook 要同時出現在 context
  · 不可讓錯誤的歷史文件獨佔 top results
  · 每一段都要標出來源類型與年份
"""

from __future__ import annotations

import pytest

from app import retrieval
from app.rag import metadata, parse
from app.rag.hybrid import hybrid_search

pytestmark = pytest.mark.usefixtures("index")


def _has_archive(index) -> bool:
    return any(c.tier < retrieval.TIER_CURATED for c in index.chunks)


# ── Query rewrite ────────────────────────────────────────────

@pytest.mark.parametrize(
    "text,doc_type,activity,year_pref",
    [
        ("幫我做期初茶會的企劃書", "企劃書", "期初茶會", "any"),
        ("今年的社課排程", "行事曆", "每週社課", "current"),
        ("去年挑戰營的細流", "細流", "挑戰營", "recent"),
        ("113 學年度的績效報告", "績效報告", None, "explicit"),
        ("招生 IG 貼文", "文宣", "招生", "any"),
        ("經費核銷要注意什麼", "財務", None, "any"),
    ],
)
def test_query_rewrite_extracts_structure(text, doc_type, activity, year_pref):
    q = parse(text)
    assert q.year_preference == year_pref
    if doc_type:
        assert q.document_type == doc_type, f"{text} → {q.document_type}"
    if activity:
        assert q.activity == activity, f"{text} → {q.activity}"


def test_explicit_year_is_captured():
    q = parse("113 學年度的績效報告")
    assert q.explicit_year == "113"


def test_purpose_detection():
    assert parse("招生用的 IG 貼文").purpose == "external"
    assert parse("要交給課外組的評鑑資料").purpose == "official"
    assert parse("幹部交接手冊").purpose == "internal"


# ── Metadata ─────────────────────────────────────────────────

def test_metadata_infers_year_and_activity_from_path():
    from pathlib import Path

    m = metadata.infer(
        Path("knowledge/雲端文件/114學年度/114-1 禪學社期初茶會 浮花禪光  企劃書.md"),
        "歷年檔案／114學年度／114-1 禪學社期初茶會 浮花禪光  企劃書",
        "內容",
        "archive",
    )
    assert m.academic_year == "114"
    assert m.semester == "1"
    assert m.activity == "期初茶會"
    assert m.document_type == "企劃書"


def test_metadata_marks_external_audience():
    from pathlib import Path

    m = metadata.infer(
        Path("knowledge/雲端文件/114學年度/1141_社課一_靜定的力量.md"),
        "歷年檔案／114學年度／IG宣傳 1141_社課一",
        "貼文內容",
        "archive",
    )
    assert m.audience == "external"


def test_metadata_flags_structure_only_files():
    from pathlib import Path

    m = metadata.infer(
        Path("x/招生名單.md"),
        "歷年檔案／113學年度／招生名單",
        "> **僅欄位結構** —— 這是名冊類資料，只保留欄位結構，內容未擷取。",
        "archive",
    )
    assert m.contains_sensitive_structure is True


def test_every_indexed_chunk_has_metadata(index):
    missing = [c for c in index.chunks if getattr(c, "meta", None) is None]
    assert not missing, f"{len(missing)} 段沒有 metadata"


def test_source_types_are_assigned(index):
    kinds = {c.meta.source_type for c in index.chunks}
    assert "curated" in kinds
    assert "playbook" in kinds


# ── 30 組檢索 regression ──────────────────────────────────────
# (查詢, 期望的來源類型組合, 期望在來源字串裡看到的關鍵字)
CASES: list[tuple[str, str]] = [
    ("期初茶會企劃書", "茶會"),
    ("期初演講企劃書", "演講"),
    ("期中演講怎麼辦", "演講"),
    ("期末社大流程", "社大"),
    ("社課當天流程", "社課"),
    ("一學期社課怎麼排", "社課"),
    ("社課主題可以上什麼", "社課"),
    ("凝聚分享要問什麼", "凝聚"),
    ("社評績效報告", "評鑑"),
    ("年度績效報告要寫什麼", "評鑑"),
    ("社團評鑑要交哪些附件", "評鑑"),
    ("招生企劃怎麼寫", "招生"),
    ("招生 IG 文案", "文宣"),
    ("路宣要怎麼排班", "招生"),
    ("個接怎麼做", "招生"),
    ("新生報名表要問什麼", "表單"),
    ("社課回饋問卷", "問卷"),
    ("挑戰營細流", "挑戰營"),
    ("挑戰營企劃書", "挑戰營"),
    ("戶外營隊安全計畫", "營隊"),
    ("禪訓營是什麼", "禪訓營"),
    ("經費預算表", "經費"),
    ("核銷流程", "核銷"),
    ("財務總表", "財務"),
    ("幹部交接要交什麼", "交接"),
    ("幹部職掌", "職"),
    ("幹部訓練怎麼設計", "幹部"),
    ("社團章程", "章程"),
    ("十二項特質", "特質"),
    ("社團簡介給校外單位", "社團"),
]


@pytest.mark.parametrize("query,expect_keyword", CASES)
def test_context_always_contains_guidance(index, query, expect_keyword):
    """每個查詢的 context 都必須有精選內容 —— 不能被歷年檔案獨佔。"""
    bundle = retrieval.build_context([query])
    assert bundle.hits, f"「{query}」完全查不到東西"
    assert bundle.curated_count >= 1, (
        f"「{query}」的 context 全是歷年檔案，沒有規範可依循："
        f"{[h.chunk.source for h in bundle.hits]}"
    )


@pytest.mark.parametrize("query,expect_keyword", CASES)
def test_context_is_relevant(index, query, expect_keyword):
    blob = " ".join(h.chunk.source + " " + h.chunk.text[:200] for h in retrieval.build_context([query]).hits)
    assert expect_keyword in blob, f"「{query}」的結果裡看不到「{expect_keyword}」"


def test_context_mixes_curated_and_archive(index):
    """有歷年檔案時，做事類的查詢應該同時拿到規範與範例。"""
    if not _has_archive(index):
        pytest.skip("沒有匯入歷年檔案")
    both = 0
    probes = [
        "期初茶會企劃書", "社評績效報告", "挑戰營細流",
        "招生 IG 文案", "經費預算表", "社課排程",
    ]
    for q in probes:
        b = retrieval.build_context([q])
        if b.curated_count >= 1 and b.archive_count >= 1:
            both += 1
    assert both >= len(probes) - 1, f"只有 {both}/{len(probes)} 個查詢同時拿到規範與範例"


def test_explicit_year_is_preferred(index):
    if not _has_archive(index):
        pytest.skip("沒有匯入歷年檔案")
    hits = hybrid_search(
        index, "114 學年度 社課", k=8, parsed=parse("114 學年度 社課"), current_year="115"
    )
    years = [h.chunk.meta.academic_year for h in hits if h.chunk.meta and h.chunk.meta.academic_year]
    assert years, "沒有任何帶年份的結果"
    assert years.count("114") >= 1, f"指定 114 學年度卻沒拿到：{years}"


def test_rendered_context_labels_source_and_year(index):
    if not _has_archive(index):
        pytest.skip("沒有匯入歷年檔案")
    rendered = retrieval.build_context(["期初茶會企劃書"]).render()
    assert "【歷年範例】" in rendered or "【社團知識庫】" in rendered or "【任務劇本】" in rendered
    assert "不可以當成今年的事實" in rendered, "context 必須明講歷年資料不是今年事實"


def test_semantic_layer_degrades_gracefully(index, monkeypatch):
    """語意層壞掉時要退回純 BM25，不能讓檢索整個掛掉。"""
    from app.rag import semantic

    monkeypatch.setattr(semantic, "get", lambda: None)
    bundle = retrieval.build_context(["期初茶會企劃書"])
    assert bundle.hits, "語意層停用後就查不到東西了"


def test_bm25_search_api_unchanged(index):
    """hybrid 是疊在 BM25 上，不是取代 —— 舊 API 要維持可用。"""
    hits = index.search("招生管道", k=5)
    assert hits and all(hasattr(c, "text") for _, c in hits)
    _hits, conf = index.search_scored("招生管道", k=5)
    assert conf > 0
