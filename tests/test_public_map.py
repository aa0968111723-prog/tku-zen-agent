from __future__ import annotations

from app.research.public_map import (
    PUBLIC_SOURCE_ENTITIES,
    REQUIRED_CATEGORIES,
    allowlist_hosts,
    entities_matching,
    is_fetchable_url,
    source_record,
)


def test_public_map_has_eight_explicit_categories():
    assert len(PUBLIC_SOURCE_ENTITIES) == 8
    assert tuple(entity.id for entity in PUBLIC_SOURCE_ENTITIES) == REQUIRED_CATEGORIES


def test_public_map_matches_aliases_without_widening_fetch_policy():
    assert {entity.id for entity in entities_matching("淡水美食")}
    assert is_fetchable_url("https://www.tku.edu.tw/news")
    assert not is_fetchable_url("http://tamsui.dils.tku.edu.tw/")
    assert not is_fetchable_url("https://example.com/")
    assert "www.tku.edu.tw" in allowlist_hosts()


def test_public_map_source_record_is_citable_and_not_club_ssot():
    record = source_record("tamsui-local")
    assert record["source_url"].startswith("https://")
    assert record["source_title"] == "淡水地區"
    assert record["is_club_ssot"] is False
    assert "captured_at" in record
