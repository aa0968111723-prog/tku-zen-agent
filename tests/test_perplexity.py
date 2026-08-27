from __future__ import annotations

from app import config
from app.services import perplexity
from app.tools import all_names


def test_perplexity_requires_explicit_enable(monkeypatch):
    monkeypatch.setattr(config, "PERPLEXITY_SEARCH_ENABLED", False)
    monkeypatch.setattr(config, "PERPLEXITY_API_KEY", "")
    result = perplexity.search_web("淡江大學 社團")
    assert result["ok"] is False
    assert result["code"] == "BLOCKED_BY_EXTERNAL_DEPENDENCY"


def test_perplexity_rejects_empty_query():
    result = perplexity.search_web("")
    assert result["ok"] is False
    assert result["code"] == "INVALID_QUERY"


def test_perplexity_rejects_invalid_recency(monkeypatch):
    monkeypatch.setattr(config, "PERPLEXITY_SEARCH_ENABLED", True)
    monkeypatch.setattr(config, "PERPLEXITY_API_KEY", "test-only")
    result = perplexity.search_web("淡江", recency="forever")
    assert result["ok"] is False
    assert result["code"] == "INVALID_RECENCY"


def test_perplexity_tool_is_registered():
    assert "search_perplexity_web" in set(all_names())
