"""Leaf data types shared by research, RAG and orchestration.

This module intentionally imports only the Python standard library. Keeping
the evidence records here prevents a package initializer or a RAG module from
pulling the higher-level research integration into the import graph.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime

STATUS_VERIFIED = "verified"
STATUS_PARTIAL = "partially_verified"
STATUS_INFERRED = "inferred"
STATUS_CONFLICTED = "conflicted"
STATUS_STALE = "stale"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_WRONG_ENTITY = "wrong_entity"

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

STALE_AFTER_DAYS = 240
_DATE = re.compile(r"(20\d{2})[-/年.](\d{1,2})(?:[-/月.](\d{1,2}))?")


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    match = _DATE.search(str(raw))
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3) or 1))
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
    entity_id: str = ""
    school: str = ""
    organization: str = ""
    source_scope: str = "internal"
    academic_term: str = ""
    authority_level: str = "archive"
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
    """對單一來源定可信度；沒有完整出處就不能是 verified。"""
    today = today or datetime.now().date()

    if record.source_scope == "external":
        if target_entities and record.entity_id not in target_entities:
            return STATUS_WRONG_ENTITY
        if not (record.url and record.title and record.excerpt):
            return STATUS_INSUFFICIENT
        captured = _parse_date(record.captured_at) or _parse_date(record.published_at)
        if captured is None:
            return STATUS_PARTIAL
        if (today - captured).days > STALE_AFTER_DAYS:
            return STATUS_STALE
        if record.authority_level == "official":
            return STATUS_VERIFIED
        return STATUS_PARTIAL

    if not (record.title and record.excerpt):
        return STATUS_INSUFFICIENT
    if record.is_current or record.authority_level in {"curated"}:
        return STATUS_VERIFIED
    return STATUS_STALE


@dataclass
class ClaimRecord:
    """一個重要結論與它的證據。"""

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
    evidence_level: str = "none"
    confidence: str = "low"
    status: str = STATUS_INSUFFICIENT
    source_ids: list[str] = field(default_factory=list)

    def finalize(self) -> "ClaimRecord":
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
