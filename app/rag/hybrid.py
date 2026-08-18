"""BM25 + 語意 的融合檢索與重排。

融合用 Reciprocal Rank Fusion 而不是加權分數相加：兩個檢索器的分數
量綱完全不同（BM25 是無上界的累加，餘弦相似度是 0–1），直接相加等於
讓 BM25 說了算。RRF 只看名次，不需要校準。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import semantic
from .query import ParsedQuery, parse

if TYPE_CHECKING:
    from ..retrieval import Chunk, Index

RRF_K = 60          # RRF 的平滑常數，標準值
BM25_POOL = 40      # 各檢索器先取多少候選進融合
SEMANTIC_POOL = 40


@dataclass
class Scored:
    chunk: "Chunk"
    score: float
    bm25_rank: int | None = None
    semantic_rank: int | None = None
    reasons: list[str] = field(default_factory=list)


def _rrf(rank: int) -> float:
    return 1.0 / (RRF_K + rank)


def _metadata_boost(chunk: "Chunk", q: ParsedQuery, current_year: str | None) -> tuple[float, list[str]]:
    """重排加權。順序照規格：當期 > 精選 > 劇本 > 同類近期歷年範例。"""
    meta = getattr(chunk, "meta", None)
    boost = 1.0
    reasons: list[str] = []

    if meta is None:
        return boost, reasons

    if meta.source_type in {"curated", "playbook"}:
        boost *= 1.30
        reasons.append("精選")

    # 同類型文件（問細流就給細流，不要給企劃書）
    if q.document_type and meta.document_type == q.document_type:
        boost *= 1.25
        reasons.append(f"同類型：{meta.document_type}")

    # 同一個活動
    if q.activity and meta.activity == q.activity:
        boost *= 1.30
        reasons.append(f"同活動：{meta.activity}")

    # 年份偏好
    if meta.academic_year:
        year = int(meta.academic_year)
        if q.year_preference == "explicit" and q.explicit_year:
            boost *= 1.6 if meta.academic_year == q.explicit_year else 0.75
        elif current_year:
            # 越近的歷年範例越有參考價值，但衰減要溫和，
            # 免得 108 學年度那些仍然有用的公版被埋掉
            gap = max(0, int(current_year) - year)
            boost *= max(0.72, 1.08 - 0.06 * gap)
            if gap <= 1:
                reasons.append("近期")

    # 對外/對內
    if q.purpose == "external" and meta.audience == "external":
        boost *= 1.15
        reasons.append("對外素材")

    # 只剩欄位結構的名冊，參考價值低但不是沒有（可以看欄位設計）
    if meta.contains_sensitive_structure:
        boost *= 0.8

    return boost, reasons


def hybrid_search(
    index: "Index",
    query: str,
    *,
    k: int = 8,
    parsed: ParsedQuery | None = None,
    current_year: str | None = None,
    min_curated: int = 2,
    min_archive: int = 1,
) -> list[Scored]:
    """跑完整條 hybrid pipeline，回傳重排後的結果。"""
    from ..retrieval import TIER_CURATED, tokenize

    q = parsed or parse(query)

    # ── BM25 ──────────────────────────────────────────────
    bm25_hits = index.search(q.describe() or query, k=BM25_POOL, min_curated=0)
    if not bm25_hits:
        bm25_hits = index.search(query, k=BM25_POOL, min_curated=0)

    fused: dict[int, Scored] = {}
    for rank, (_score, chunk) in enumerate(bm25_hits):
        key = id(chunk)
        fused[key] = Scored(chunk=chunk, score=_rrf(rank), bm25_rank=rank)

    # ── 語意 ──────────────────────────────────────────────
    sem = semantic.get()
    if sem is not None and sem.n_chunks == len(index.chunks):
        vec = sem.query(q.describe() or query, tokenize)
        if vec is not None:
            import numpy as np

            sims = sem.similarities(vec)
            top = np.argsort(-sims)[:SEMANTIC_POOL]
            for rank, idx in enumerate(top):
                if sims[idx] <= 0.02:      # 幾乎無關就不要硬塞進候選
                    continue
                chunk = index.chunks[int(idx)]
                key = id(chunk)
                if key in fused:
                    fused[key].score += _rrf(rank)
                    fused[key].semantic_rank = rank
                else:
                    fused[key] = Scored(chunk=chunk, score=_rrf(rank), semantic_rank=rank)

    if not fused:
        return []

    # ── 重排 ──────────────────────────────────────────────
    for scored in fused.values():
        boost, reasons = _metadata_boost(scored.chunk, q, current_year)
        scored.score *= boost
        scored.reasons = reasons

    ranked = sorted(fused.values(), key=lambda s: s.score, reverse=True)

    # ── 保證同時有「規範」與「歷年範例」──────────────────
    return _ensure_mix(ranked, k, min_curated, min_archive, TIER_CURATED)


def _ensure_mix(
    ranked: list[Scored], k: int, min_curated: int, min_archive: int, tier_curated: float
) -> list[Scored]:
    """context 必須同時含精選規範與同類歷年範例。

    只靠分數排序做不到這件事：語料裡歷年檔案佔 95%，常見詞的 IDF 被稀釋，
    要嘛全是歷年檔案、要嘛全是劇本。所以直接保留名額。
    """
    def is_curated(s: Scored) -> bool:
        return s.chunk.tier >= tier_curated

    curated_all = [s for s in ranked if is_curated(s)]
    archive_all = [s for s in ranked if not is_curated(s)]

    # 先各自保留最低額度。兩邊都要保 —— 只保 curated 的話，當前 k 名剛好
    # 全是精選內容時，歷年範例會被完全擠掉，那就拿不到「以前怎麼做的」。
    picked: list[Scored] = curated_all[:min_curated] + archive_all[:min_archive]
    picked_ids = {id(s) for s in picked}

    for s in ranked:
        if len(picked) >= k:
            break
        if id(s) not in picked_ids:
            picked.append(s)
            picked_ids.add(id(s))

    picked.sort(key=lambda s: s.score, reverse=True)
    return picked[:k]
