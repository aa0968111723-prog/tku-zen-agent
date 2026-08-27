from __future__ import annotations

from app import config
from app.services import public_sources
from app.tools import all_names


def test_public_sources_are_allowlisted():
    sources = public_sources.list_public_sources()
    assert sources
    assert all(item["url"].startswith("https://") for item in sources)
    assert all(".tku.edu.tw" in item["url"] or "www.tku.edu.tw" in item["url"] for item in sources)


def test_public_source_unknown_id_is_rejected():
    result = public_sources.fetch_public_source("not-allowed")
    assert result["ok"] is False
    assert result["code"] == "NOT_FOUND"


def test_instagram_public_search_is_blocked_without_explicit_enable(monkeypatch):
    monkeypatch.setattr(config, "INSTAGRAM_PUBLIC_SEARCH_ENABLED", False)
    monkeypatch.setattr(config, "INSTAGRAM_ACCESS_TOKEN", "")
    result = public_sources.search_instagram_public_hashtag("tku")
    assert result["ok"] is False
    assert result["code"] == "BLOCKED_BY_EXTERNAL_DEPENDENCY"


def test_public_tools_are_registered():
    names = set(all_names())
    assert "list_tku_public_sources" in names
    assert "search_tku_public_info" in names
    assert "fetch_tku_public_source" in names
    assert "search_instagram_public_hashtag" in names
