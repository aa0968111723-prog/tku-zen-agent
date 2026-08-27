"""Agent tools for approved TKU public sources and Meta Instagram public hashtag search."""

from __future__ import annotations

from typing import Any

from app.services import public_sources


LIST_SCHEMA = {
    "type": "object",
    "properties": {"query": {"type": "string", "description": "可選的來源名稱或 ID 關鍵字"}},
}
SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "要搜尋的公開資訊"},
        "source_ids": {"type": "array", "items": {"type": "string"}},
        "limit": {"type": "integer", "minimum": 1, "maximum": 4},
    },
    "required": ["query"],
}
FETCH_SCHEMA = {
    "type": "object",
    "properties": {
        "source_id": {"type": "string"},
        "query": {"type": "string"},
    },
    "required": ["source_id"],
}
HASHTAG_SCHEMA = {
    "type": "object",
    "properties": {
        "hashtag": {"type": "string", "description": "例如 tku 或 淡江大學"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 25},
    },
    "required": ["hashtag"],
}


def list_tku_public_sources(args: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "sources": public_sources.list_public_sources(str(args.get("query") or ""))}


def search_tku_public_info(args: dict[str, Any]) -> dict[str, Any]:
    source_ids = args.get("source_ids")
    if not isinstance(source_ids, list):
        source_ids = None
    return public_sources.search_public_info(
        str(args.get("query") or ""),
        source_ids=[str(item) for item in source_ids] if source_ids else None,
        limit=int(args.get("limit") or 4),
    )


def fetch_tku_public_source(args: dict[str, Any]) -> dict[str, Any]:
    return public_sources.fetch_public_source(
        str(args.get("source_id") or ""),
        str(args.get("query") or ""),
    )


def search_instagram_public_hashtag(args: dict[str, Any]) -> dict[str, Any]:
    return public_sources.search_instagram_public_hashtag(
        str(args.get("hashtag") or ""),
        int(args.get("limit") or 10),
    )
