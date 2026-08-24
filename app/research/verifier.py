"""回答品質閘門（claim verification）與資料污染偵測。

在 AI 回答送給使用者之前跑，規則式、確定性：

  1. 找出回答中與研究對象有關的句子（claims）。
  2. 每個 claim 對照該實體的來源摘錄，算支持度。
  3. 淡江招牌活動出現在外校句子裡 → 資料歸屬錯誤，阻止輸出。
  4. 研究對象沒有任何相符來源卻寫得斬釘截鐵 → 阻止輸出。
  5. 推測沒放在【可能推測】區 → 降級為「部分內容尚未驗證」。

最高原則：沒有證據，不要下結論。對象不明，不要猜。
來源錯誤，不要完成。資料混淆，必須阻止輸出。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .claims import (
    STATUS_INFERRED,
    STATUS_INSUFFICIENT,
    STATUS_PARTIAL,
    STATUS_STALE,
    STATUS_VERIFIED,
    STATUS_WRONG_ENTITY,
    ClaimRecord,
    SourceRecord,
)
from .entities import (
    EntityResolution,
    ResearchMode,
    ResearchScope,
    SCHOOL_ALIASES,
    entity_by_id,
)

# 研究完成狀態（前端狀態晶片照這裡顯示）
RESEARCH_COMPLETE = "complete"                  # 研究完成（全部條件成立）
RESEARCH_PARTIAL = "partially_verified"         # 部分完成
RESEARCH_UNVERIFIED = "unverified"              # 尚未完成驗證
RESEARCH_NO_SOURCE = "no_reliable_source"       # 找不到可靠來源
RESEARCH_NEEDS_USER = "needs_clarification"     # 需要使用者確認
RESEARCH_BLOCKED = "blocked"                    # 已阻止輸出（對象錯誤／資料混淆）
RESEARCH_INTERNAL = "internal"                  # 淡江內部資料模式

RESEARCH_STATUS_LABELS = {
    RESEARCH_COMPLETE: "研究完成",
    RESEARCH_PARTIAL: "部分完成",
    RESEARCH_UNVERIFIED: "尚未完成驗證",
    RESEARCH_NO_SOURCE: "找不到可靠來源",
    RESEARCH_NEEDS_USER: "需要使用者確認",
    RESEARCH_BLOCKED: "已暫停產生結論",
    RESEARCH_INTERNAL: "依淡江內部資料",
}

INTERNAL_ATTRIBUTION = "本回答依淡江內部資料整理。"
PARTIAL_NOTICE = "部分內容尚未驗證。"
BLOCK_NOTICE = "目前檢索結果與研究對象不一致，暫停產生結論。"
NO_SOURCE_NOTICE = "目前沒有足夠公開來源確認此資訊，因此不提供確定結論。"

# 淡江專屬的招牌活動與名稱。出現在「歸給外校」的句子裡就是資料污染。
HOME_SIGNATURE_TERMS: tuple[str, ...] = (
    "浮游花", "浮花禪光", "浮游禪光", "登峰傳心", "金剛勇士", "禪行破浪",
    "孝子山", "皇帝殿", "攀越心峰", "生命靈數", "聽見彼此的心", "感恩星光夜",
    "快樂禪", "紓壓禪", "與自己有約", "領袖禪訓營",
)

_HOME_WORDS = ("淡江", "淡大", "本社", "我們社")
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;\n])")
_DISCLAIMER = re.compile(
    r"(沒有(足夠|公開|可靠)?(的)?(來源|資料|證據)|查不到|找不到|尚待確認|待確認|未確認|"
    r"無法確認|不確定|尚未驗證|請提供|需要你提供|不提供確定結論)"
)
_QUESTION = re.compile(r"[?？]\s*$")
_SPECULATION_HEAD = re.compile(r"【(可能推測|推測|尚待確認)】")
# 這些段落標籤行是版面結構，不是事實主張：【研究對象】點名對象、【來源整理】列來源
_LABEL_LINE = re.compile(r"^【(研究對象|來源整理|尚待確認|已驗證資料|淡江內部資料|淡江可採用建議|兩者差異)】")


@dataclass
class Finding:
    rule: str
    severity: str          # error | warning
    message: str
    sentence: str = ""

    def to_dict(self) -> dict:
        return {"rule": self.rule, "severity": self.severity,
                "message": self.message, "sentence": self.sentence[:120]}


@dataclass
class AnswerReview:
    verdict: str = "allow"                 # allow | degrade | block
    research_status: str = RESEARCH_INTERNAL
    findings: list[Finding] = field(default_factory=list)
    claims: list[ClaimRecord] = field(default_factory=list)
    contamination: dict | None = None
    notices: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    def to_event(self) -> dict:
        return {
            "type": "answer_review",
            "verdict": self.verdict,
            "research_status": self.research_status,
            "research_status_label": RESEARCH_STATUS_LABELS.get(self.research_status, self.research_status),
            "findings": [f.to_dict() for f in self.findings],
            "claims": [c.to_dict() for c in self.claims],
            "notices": list(self.notices),
        }


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def _tokenize(text: str):
    from ..retrieval import tokenize   # 延遲載入，避免循環 import
    return tokenize(text)


def _entity_terms(entity_id: str) -> list[str]:
    entity = entity_by_id(entity_id)
    if entity is None:
        return []
    terms = [entity.name, *entity.aliases]
    if entity.school:
        terms.append(entity.school)
        terms += [a for a, full in SCHOOL_ALIASES.items() if full == entity.school]
    return sorted(set(terms), key=len, reverse=True)


def _school_terms(school: str) -> list[str]:
    terms = [school] + [a for a, full in SCHOOL_ALIASES.items() if full == school]
    return sorted(set(t for t in terms if t), key=len, reverse=True)


def _support(sentence: str, excerpts: list[str], strip_terms: list[str]) -> float:
    """句子有多少內容能在來源摘錄裡找到。0–1。"""
    cleaned = sentence
    for t in strip_terms:
        cleaned = cleaned.replace(t, " ")
    tokens = set(_tokenize(cleaned))
    if not tokens:
        return 1.0
    pool: set[str] = set()
    for ex in excerpts:
        pool.update(_tokenize(ex))
    if not pool:
        return 0.0
    return len(tokens & pool) / len(tokens)


def _speculation_spans(text: str) -> list[tuple[int, int]]:
    """【可能推測】／【尚待確認】區塊的範圍。放在裡面的推測是合法標記。"""
    spans: list[tuple[int, int]] = []
    for m in _SPECULATION_HEAD.finditer(text):
        start = m.end()
        nxt = text.find("【", start)
        spans.append((start, len(text) if nxt == -1 else nxt))
    return spans


_SOURCE_SECTION_HEAD = re.compile(r"【來源整理】")


def _source_section_spans(text: str) -> list[tuple[int, int]]:
    """【來源整理】區塊：列的是來源清單，不是要驗證的事實主張。"""
    spans: list[tuple[int, int]] = []
    for m in _SOURCE_SECTION_HEAD.finditer(text):
        start = m.end()
        nxt = text.find("【", start)
        spans.append((start, len(text) if nxt == -1 else nxt))
    return spans


def _in_spans(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(a <= pos < b for a, b in spans)


def review_answer(
    text: str,
    *,
    scope: ResearchScope,
    resolution: EntityResolution,
    sources: list[SourceRecord],
    strict: bool = True,
) -> AnswerReview:
    """回答送出前的品質閘門。"""
    review = AnswerReview()
    body = text or ""

    external_sources = [s for s in sources if s.is_external]
    spec_spans = _speculation_spans(body)

    if scope.mode == ResearchMode.INTERNAL:
        review.research_status = RESEARCH_INTERNAL
        _check_internal_answer(body, review, sources)
        if INTERNAL_ATTRIBUTION not in body:
            review.notices.append(INTERNAL_ATTRIBUTION)
        if review.errors:
            review.verdict = "block"
            review.research_status = RESEARCH_BLOCKED
        elif any(f.severity == "warning" for f in review.findings):
            review.verdict = "degrade"
        return review

    # ── 外部研究／比較分析 ────────────────────────────────
    targets: list[tuple[str, str, list[str]]] = []   # (entity_id, 顯示名, 比對詞)
    for eid in scope.target_entities:
        entity = entity_by_id(eid)
        if entity is not None:
            targets.append((eid, entity.name, _entity_terms(eid)))
    covered = {s for _eid, _n, terms in targets for s in terms}
    for school in scope.target_schools:
        terms = _school_terms(school)
        if not any(t in covered for t in terms):
            targets.append(("", school, terms))
    if scope.generic_external and not targets:
        for s in external_sources:
            if s.entity_id and all(eid != s.entity_id for eid, _n, _t in targets):
                targets.append((s.entity_id, s.organization or s.school, _entity_terms(s.entity_id)))

    sentences = _sentences(body)

    # 1. 資料污染：淡江招牌內容被歸給外校
    contaminated: list[Finding] = []
    for sent in sentences:
        if any(w in sent for w in _HOME_WORDS):
            continue   # 有提到淡江的比較句不算歸屬錯誤
        ext_hit = next(
            (name for _eid, name, terms in targets if any(t in sent for t in terms)),
            None,
        )
        if not ext_hit:
            continue
        term_hit = next((t for t in HOME_SIGNATURE_TERMS if t in sent), None)
        if term_hit:
            contaminated.append(Finding(
                rule="data_contamination",
                severity="error",
                message=f"淡江內部內容「{term_hit}」被寫成{ext_hit}的做法。",
                sentence=sent,
            ))
    review.findings.extend(contaminated)

    # 2. 逐句 claim 驗證
    unlabeled_inferred = 0
    pos = 0
    source_spans = _source_section_spans(body)
    for sent in sentences:
        pos = body.find(sent, pos)
        in_speculation = _in_spans(max(pos, 0), spec_spans)
        label = _LABEL_LINE.match(sent)
        if label and label.group(1) in {"研究對象", "來源整理", "尚待確認"}:
            continue   # 標籤行：點名對象、列來源、列未確認項，不是對外校的事實主張
        if _in_spans(max(pos, 0), source_spans):
            continue   # 來源清單內容
        for eid, name, terms in targets:
            if not any(t in sent for t in terms):
                continue
            if _QUESTION.search(sent) or _DISCLAIMER.search(sent):
                break
            entity_sources = [s for s in external_sources if eid and s.entity_id == eid]
            excerpts = [s.excerpt for s in entity_sources]
            support = _support(sent, excerpts, terms) if excerpts else 0.0

            claim = ClaimRecord(claim=sent[:160], entity=name, entity_id=eid)
            if entity_sources and support >= 0.55:
                best = entity_sources[0]
                claim.source_title = best.title
                claim.source_url = best.url
                claim.publisher = best.publisher
                claim.published_at = best.published_at
                claim.captured_at = best.captured_at
                claim.excerpt = best.excerpt
                claim.source_type = best.source_type
                claim.evidence_level = "direct"
                claim.confidence = "high"
                claim.status = STATUS_STALE if best.status == STATUS_STALE else STATUS_VERIFIED
                claim.source_ids = [s.source_id for s in entity_sources]
            elif entity_sources and support >= 0.25:
                best = entity_sources[0]
                claim.source_title = best.title
                claim.source_url = best.url
                claim.publisher = best.publisher
                claim.excerpt = best.excerpt
                claim.source_type = best.source_type
                claim.evidence_level = "partial"
                claim.confidence = "medium"
                claim.status = STATUS_PARTIAL
                claim.source_ids = [s.source_id for s in entity_sources]
            elif entity_sources:
                claim.evidence_level = "none"
                claim.status = STATUS_INFERRED
                if not in_speculation:
                    unlabeled_inferred += 1
                    review.findings.append(Finding(
                        rule="speculation_as_fact",
                        severity="warning",
                        message=f"這句對{name}的描述在來源摘錄裡找不到直接依據，應標示為推測。",
                        sentence=sent,
                    ))
            else:
                claim.status = STATUS_WRONG_ENTITY if not eid else STATUS_INSUFFICIENT
                review.findings.append(Finding(
                    rule="claim_without_source",
                    severity="error",
                    message=f"回答對{name}下了結論，但檢索結果裡沒有任何{name}的可驗證來源。",
                    sentence=sent,
                ))
            review.claims.append(claim.finalize())
            break

    # 3. 外校研究但證據全是內部資料
    if targets and not external_sources and review.claims:
        internal_only = [s for s in sources if not s.is_external]
        if internal_only:
            review.findings.append(Finding(
                rule="internal_sources_only",
                severity="error",
                message="研究對象是外校，但這次檢索到的全部是淡江內部資料，不能作為外校證據。",
            ))

    # 4. 過舊來源
    stale = [s for s in external_sources if s.status == STATUS_STALE]
    if stale and external_sources and len(stale) == len(external_sources):
        review.findings.append(Finding(
            rule="stale_sources",
            severity="warning",
            message="所有外校來源的檢索日期都已過舊，結論僅供參考，建議重新查官方帳號。",
        ))

    # ── 裁決 ─────────────────────────────────────────────
    if contaminated:
        review.contamination = {
            "message": "目前內容可能混入淡江內部資料，不能視為外校公開資料。",
            "items": [f.to_dict() for f in contaminated],
        }
    has_claim_error = any(f.severity == "error" for f in review.findings)
    verified_claims = [c for c in review.claims if c.status == STATUS_VERIFIED]

    if has_claim_error:
        review.verdict = "block"
        review.research_status = RESEARCH_BLOCKED
        review.notices.append(BLOCK_NOTICE)
    elif review.claims and not verified_claims and not any(
        c.status in {STATUS_PARTIAL, STATUS_STALE} for c in review.claims
    ):
        # 全部只是推測
        review.verdict = "degrade"
        review.research_status = RESEARCH_UNVERIFIED
        review.notices.append(PARTIAL_NOTICE)
    elif unlabeled_inferred or any(
        c.status in {STATUS_PARTIAL, STATUS_INFERRED, STATUS_STALE, STATUS_INSUFFICIENT}
        for c in review.claims
    ) or any(f.severity == "warning" for f in review.findings):
        review.verdict = "degrade"
        review.research_status = RESEARCH_PARTIAL
        review.notices.append(PARTIAL_NOTICE)
    elif not external_sources and targets:
        review.verdict = "degrade"
        review.research_status = RESEARCH_NO_SOURCE
        review.notices.append(NO_SOURCE_NOTICE)
    else:
        review.verdict = "allow"
        review.research_status = RESEARCH_COMPLETE if targets else RESEARCH_INTERNAL

    if not strict and review.verdict == "block":
        review.verdict = "degrade"
    return review


def _check_internal_answer(body: str, review: AnswerReview, sources: list[SourceRecord]) -> None:
    """內部模式：不得對外校下事實結論。"""
    sentences = _sentences(body)
    for sent in sentences:
        if _QUESTION.search(sent) or _DISCLAIMER.search(sent):
            continue
        for alias in sorted(SCHOOL_ALIASES, key=len, reverse=True):
            school = SCHOOL_ALIASES[alias]
            if school == "淡江大學" or alias not in sent:
                continue
            review.findings.append(Finding(
                rule="external_claim_in_internal_mode",
                severity="error",
                message=f"目前是淡江內部資料模式，但回答對「{school}」下了結論。"
                        "要研究外校請明確指定研究對象與官方來源。",
                sentence=sent,
            ))
            break
