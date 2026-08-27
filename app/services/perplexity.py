"""Perplexity Search API integration for current public web research."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from app import config

_ALLOWED_RECENCY = {"hour", "day", "week", "month", "year"}
_DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "tku-zen-agent-perplexity-search/1.0",
}


def _normalise_domains(domains: list[str] | None) -> list[str]:
    result: list[str] = []
    for raw in domains or []:
        value = str(raw).strip()
        if not value or value.startswith("-") or len(value) > 253 or any(ch.isspace() for ch in value):
            continue
        parsed = urlparse(value if "://" in value else f"https://{value}")
        if parsed.hostname and parsed.hostname not in result:
            result.append(value)
        if len(result) >= 20:
            break
    return result


def search_web(
    query: str,
    *,
    domains: list[str] | None = None,
    recency: str | None = None,
    max_results: int = 10,
) -> dict[str, Any]:
    query = query.strip()
    if not query:
        return {"ok": False, "code": "INVALID_QUERY", "message": "搜尋字串不可為空。"}
    if recency and recency not in _ALLOWED_RECENCY:
        return {"ok": False, "code": "INVALID_RECENCY", "message": "日期範圍只能是 hour、day、week、month 或 year。"}
    if not config.PERPLEXITY_SEARCH_ENABLED or not config.PERPLEXITY_API_KEY:
        return {
            "ok": False,
            "code": "BLOCKED_BY_EXTERNAL_DEPENDENCY",
            "message": "Perplexity 搜尋尚未啟用；請設定 server-only API key 並開啟 PERPLEXITY_SEARCH_ENABLED。",
        }

    payload: dict[str, Any] = {
        "query": query,
        "country": "TW",
        "search_language_filter": ["zh"],
        "max_results": max(1, min(20, max_results)),
        "search_context_size": "high",
    }
    clean_domains = _normalise_domains(domains)
    if clean_domains:
        payload["search_domain_filter"] = clean_domains
    if recency:
        payload["search_recency_filter"] = recency

    last_error: Exception | None = None
    for attempt in range(config.PERPLEXITY_SEARCH_ATTEMPTS):
        try:
            with httpx.Client(
                base_url=config.PERPLEXITY_BASE_URL,
                timeout=config.PERPLEXITY_SEARCH_TIMEOUT_SECONDS,
                headers={
                    **_DEFAULT_HEADERS,
                    "Authorization": f"Bearer {config.PERPLEXITY_API_KEY}",
                },
            ) as client:
                response = client.post("/search", json=payload)
                response.raise_for_status()
                data = response.json()
            results = data.get("results", [])
            if not isinstance(results, list):
                results = []
            return {
                "ok": True,
                "provider": "perplexity",
                "query": query,
                "filters": {
                    "domains": clean_domains,
                    "recency": recency,
                    "country": "TW",
                    "language": "zh",
                },
                "results": results[: payload["max_results"]],
                "search_id": data.get("id"),
                "server_time": data.get("server_time"),
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "external_reference_warning": "搜尋結果是公開網路參考，需查看原始來源後才能列為已確認事實。",
            }
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            last_error = exc
            if attempt + 1 < config.PERPLEXITY_SEARCH_ATTEMPTS:
                continue

    return {
        "ok": False,
        "code": "BLOCKED_BY_EXTERNAL_DEPENDENCY",
        "message": f"Perplexity 搜尋目前無法使用：{type(last_error).__name__ if last_error else 'UnknownError'}。",
    }
