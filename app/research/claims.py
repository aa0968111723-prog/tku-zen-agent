"""Convert retrieval chunks into evidence records.

The records themselves live in :mod:`app.research.types`, a dependency-free
leaf module. This module is limited to mapping retrieval metadata to records.
"""

from __future__ import annotations

from .entities import HOME_ENTITY_ID, HOME_SCHOOL
from .types import (
    ALL_STATUSES,
    STATUS_CONFLICTED,
    STATUS_INFERRED,
    STATUS_INSUFFICIENT,
    STATUS_LABELS,
    STATUS_PARTIAL,
    STATUS_STALE,
    STATUS_VERIFIED,
    STATUS_WRONG_ENTITY,
    ClaimRecord,
    SourceRecord,
    classify_source,
)

__all__ = [
    "ALL_STATUSES",
    "STATUS_CONFLICTED",
    "STATUS_INFERRED",
    "STATUS_INSUFFICIENT",
    "STATUS_LABELS",
    "STATUS_PARTIAL",
    "STATUS_STALE",
    "STATUS_VERIFIED",
    "STATUS_WRONG_ENTITY",
    "ClaimRecord",
    "SourceRecord",
    "classify_source",
    "source_from_chunk",
]


def source_from_chunk(
    chunk, current_year: str | None = None, today=None,
    target_entities: list[str] | None = None,
) -> SourceRecord:
    """把一個檢索 chunk 轉成來源卡。"""
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
