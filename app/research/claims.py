"""來源證據模型（SourceRecord / ClaimRecord）。

鐵律：沒有 source_url、source_title、excerpt 的內容，不得標記 verified。
淡江內部資料是「內部檔案證據」，永遠不能充當外校事實的來源。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from .entities import HOME_ENTITY_ID, HOME_SCHOOL

# 可信度狀態（固定清單，前端狀態標籤照這裡對色）
STATUS_VERIFIED = "verified"                    # 綠：來源直接支持
STATUS_PARTIAL = "partially_verified"           # 黃：只有部分內容有證據
STATUS_INFERRED = "inferred"                    # 藍：AI 根據資料推測
STATUS_CONFLICTED = "conflicted"                # 紅：不同來源互相矛盾
STATUS_STALE = "stale"                          # 灰：資料過舊
STATUS_INSUFFICIENT = "insufficient_evidence"   # 證據不足
STATUS_WRONG_ENTITY = "wrong_entity"            # 紅：來源不是目前研究對象

ALL_STATUSES = (
    STATUS_VERIFIED, STATUS_PARTIAL, STATUS_INFERRED,
    STATUS_CONFLICTED, STATUS_STALE, STATUS_INSUFFICIENT, STATUS_WRONG_ENTITY,
)

STATUS_LABELS = {
    STATUS_VERIFIED: "已驗證",
    STATUS_PARTIAL: "部分驗證",
    STATUS_INFERRED: "AI 推測",
    STATUS_CONFLICTED: "來源衝突",
    STATUS_STALE: "資料過期",
    STATUS_INSUFFICIENT: "證據不足",
    STATUS_WRONG_ENTITY: "研究對象錯誤",
}

# 外校公開資料超過這個天數視為過舊（IG 經營節奏以學期為單位）
STALE_AFTER_DAYS = 240

_DATE = re.compile(r"(20\d{2})[-/年.](\d{1,2})(?:[-/月.](\d{1,2}))?")


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    m = _DATE.search(str(raw))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3) or 1))
    except ValueError:
        return None


@dataclass
class SourceRecord:
    """一份可展示、可點開的來源卡。"""

    source_id: str = ""
    title: str = ""
    url: str = ""
    publisher: str = ""
    published_at: str = ""
    captured_at: str = ""
    excerpt: str = ""
    source_type: str = "internal_archive"
    # internal_curated | internal_playbook | internal_archive | internal_conversation
    # | official_instagram | official_website | user_provided
    entity_id: str = ""
    school: str = ""
    organization: str = ""
    source_scope: str = "internal"       # internal | external
    academic_term: str = ""              # 例：114-1
    authority_level: str = "archive"     # official | curated | archive | user | unknown
    is_current: bool = False
    is_external: bool = False
    status: str = STATUS_INSUFFICIENT

    def finalize(
        self, today: date | None = None, target_entities: list[str] | None = None,
    ) -> "SourceRecord":
        self.status = classify_source(self, today=today, target_entities=target_entities)
        if not self.source_id:
            digest = hashlib.sha1(
                f"{self.title}|{self.url}|{self.excerpt[:80]}".encode("utf-8")
            ).hexdigest()[:10]
            self.source_id = f"src_{digest}"
        return self

    def to_card(self) -> dict:
        return {
            "source_id": self.source_id,
            "title": self.title,
            "url": self.url,
            "publisher": self.publisher,
            "published_at": self.published_at,
            "captured_at": self.captured_at,
            "excerpt": self.excerpt[:280],
            "source_type": self.source_type,
            "entity_id": self.entity_id,
            "school": self.school,
            "organization": self.organization,
            "source_scope": self.source_scope,
            "academic_term": self.academic_term,
            "authority_level": self.authority_level,
            "is_current": self.is_current,
            "is_external": self.is_external,
            "status": self.status,
            "status_label": STATUS_LABELS.get(self.status, self.status),
        }


def classify_source(
    record: SourceRecord, today: date | None = None, target_entities: list[str] | None = None,
) -> str:
    """對單一來源定可信度。外部來源缺 url/title/excerpt 一律不是 verified。

    ``target_entities``：本輪研究對象。外校來源的 entity 不在研究對象裡
    （或根本對不到實體）時標 wrong_entity——來源卡必須誠實顯示
    「這份來源不是你要研究的對象」，不能掛著 verified 混進證據池。
    """
    today = today or datetime.now().date()

    if record.source_scope == "external":
        if target_entities and record.entity_id not in target_entities:
            return STATUS_WRONG_ENTITY
        if not (record.url and record.title and record.excerpt):
            return STATUS_INSUFFICIENT
        captured = _parse_date(record.captured_at) or _parse_date(record.published_at)
        if captured is None:
            return STATUS_PARTIAL          # 有來源但沒有日期，只能算部分驗證
        if (today - captured).days > STALE_AFTER_DAYS:
            return STATUS_STALE
        if record.authority_level == "official":
            return STATUS_VERIFIED
        return STATUS_PARTIAL

    # 內部資料：檔案本身是真實的（verified），但歷史學期要標 stale，
    # 提醒「這是往年資料，不是本學期事實」。
    if not (record.title and record.excerpt):
        return STATUS_INSUFFICIENT
    if record.is_current or record.authority_level in {"curated"}:
        return STATUS_VERIFIED
    return STATUS_STALE


@dataclass
class ClaimRecord:
    """一個重要結論與它的證據。規格書第四節的 claim record。"""

    claim: str
    entity: str = ""
    entity_id: str = ""
    source_title: str = ""
    source_url: str = ""
    publisher: str = ""
    published_at: str = ""
    captured_at: str = ""
    excerpt: str = ""
    source_type: str = ""
    evidence_level: str = "none"     # direct | partial | none
    confidence: str = "low"          # high | medium | low
    status: str = STATUS_INSUFFICIENT
    source_ids: list[str] = field(default_factory=list)

    def finalize(self) -> "ClaimRecord":
        # 沒有 source_url / source_title / excerpt 就不可能是 verified
        if not (self.source_url and self.source_title and self.excerpt):
            if self.status in {STATUS_VERIFIED, STATUS_PARTIAL}:
                self.status = STATUS_INSUFFICIENT
        return self

    def to_dict(self) -> dict:
        return {
            "claim": self.claim,
            "entity": self.entity,
            "entity_id": self.entity_id,
            "source_title": self.source_title,
            "source_url": self.source_url,
            "publisher": self.publisher,
            "published_at": self.published_at,
            "captured_at": self.captured_at,
            "excerpt": self.excerpt[:200],
            "source_type": self.source_type,
            "evidence_level": self.evidence_level,
            "confidence": self.confidence,
            "status": self.status,
            "status_label": STATUS_LABELS.get(self.status, self.status),
            "source_ids": list(self.source_ids),
        }


def source_from_chunk(
    chunk, current_year: str | None = None, today: date | None = None,
    target_entities: list[str] | None = None,
) -> SourceRecord:
    """把一個檢索 chunk 轉成來源卡。metadata 是在 rag.metadata.infer 時附上的。"""
    meta = getattr(chunk, "meta", None)
    scope = getattr(meta, "source_scope", "internal") if meta else "internal"
    entity_id = getattr(meta, "entity_id", "") if meta else ""
    school = getattr(meta, "school", "") if meta else ""
    org = getattr(meta, "organization", "") if meta else ""
    year = getattr(meta, "academic_year", None) if meta else None
    semester = getattr(meta, "semester", None) if meta else None
    term = f"{year}-{semester}" if year and semester else (str(year) if year else "")

    if scope == "external":
        source_type = getattr(meta, "external_source_type", "") or "official_instagram"
        authority = getattr(meta, "authority_level", "") or "official"
    else:
        kind = getattr(meta, "source_type", "archive") if meta else "archive"
        source_type = {
            "curated": "internal_curated",
            "playbook": "internal_playbook",
            "conversation": "internal_conversation",
        }.get(kind, "internal_archive")
        authority = "curated" if kind in {"curated", "playbook"} else "archive"
        school = school or HOME_SCHOOL
        org = org or "淡江大學領袖禪學社"
        entity_id = entity_id or HOME_ENTITY_ID

    record = SourceRecord(
        title=chunk.source,
        url=getattr(meta, "source_url", "") if meta else "",
        publisher=org or school,
        published_at=getattr(meta, "published_at", "") if meta else "",
        captured_at=getattr(meta, "captured_at", "") if meta else "",
        excerpt=chunk.text.strip()[:280],
        source_type=source_type,
        entity_id=entity_id,
        school=school,
        organization=org,
        source_scope=scope,
        academic_term=term,
        authority_level=authority,
        is_current=bool(current_year and year and str(year) == str(current_year)),
        is_external=(scope == "external"),
    )
    return record.finalize(today=today, target_entities=target_entities)
