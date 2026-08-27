"""Agent tool for Perplexity real-time public web search."""

from __future__ import annotations

from typing import Any

from app.services import perplexity


SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "要研究的公開網路問題"},
        "domains": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 20,
            "description": "可選網域白名單，例如 instagram.com、tku.edu.tw",
        },
        "recency": {
            "type": "string",
            "enum": ["hour", "day", "week", "month", "year"],
            "description": "可選的最新時間範圍",
        },
        "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
    },
    "required": ["query"],
}


def search_perplexity_web(args: dict[str, Any]) -> dict[str, Any]:
    domains = args.get("domains")
    if not isinstance(domains, list):
        domains = None
    return perplexity.search_web(
        str(args.get("query") or ""),
        domains=[str(item) for item in domains] if domains else None,
        recency=str(args.get("recency") or "") or None,
        max_results=int(args.get("max_results") or 10),
    )
