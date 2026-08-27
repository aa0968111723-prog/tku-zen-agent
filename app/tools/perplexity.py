"""Agent tool for Perplexity real-time public web search."""

from __future__ import annotations

from typing import Any

from app.services import perplexity


SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_perplexity_web",
        "description": "以可追溯來源搜尋最新公開網路資訊；結果不是淡江社團 SSOT。",
        "parameters": {
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
        },
    },
}


def search_perplexity_web(query: str, domains: list[str] | None = None, recency: str | None = None, max_results: int = 10) -> dict[str, Any]:
    if not isinstance(domains, list):
        domains = None
    return perplexity.search_web(
        query or "",
        domains=[str(item) for item in domains] if domains else None,
        recency=recency or None,
        max_results=int(max_results or 10),
    )
