"""Agent tools for approved TKU public sources and Meta Instagram public hashtag search."""

from __future__ import annotations

from typing import Any

from app.services import public_sources


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required or []},
        },
    }


LIST_SCHEMA = _schema(
    "list_tku_public_sources",
    "列出已核對的淡江官方公開來源入口；只作來源導覽，不代表社團規定。",
    {"query": {"type": "string", "description": "可選的來源名稱或 ID 關鍵字"}},
)
SEARCH_SCHEMA = _schema(
    "search_tku_public_info",
    "搜尋已核對的淡江官方公開頁面，保留來源與擷取時間。",
    {
        "query": {"type": "string", "description": "要搜尋的公開資訊"},
        "source_ids": {"type": "array", "items": {"type": "string"}},
        "limit": {"type": "integer", "minimum": 1, "maximum": 4},
    },
    ["query"],
)
FETCH_SCHEMA = _schema(
    "fetch_tku_public_source",
    "讀取一個已核對的淡江官方公開來源；拒絕非白名單網域。",
    {
        "source_id": {"type": "string"},
        "query": {"type": "string"},
    },
    ["source_id"],
)
ACCOUNT_SCHEMA = _schema(
    "search_instagram_public_account",
    "在 Meta 授權範圍內讀取指定公開專業帳號；未啟用時老實回報不可用。",
    {
        "username": {"type": "string", "description": "指定公開 Instagram 專業帳號，例如 tku"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 25},
    },
    ["username"],
)
HASHTAG_SCHEMA = _schema(
    "search_instagram_public_hashtag",
    "在 Meta 授權範圍內搜尋公開 hashtag；不讀取私人帳號。",
    {
        "hashtag": {"type": "string", "description": "例如 tku 或 淡江大學"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 25},
    },
    ["hashtag"],
)


def list_tku_public_sources(query: str = "") -> dict[str, Any]:
    return {"ok": True, "sources": public_sources.list_public_sources(query or "")}


def search_tku_public_info(query: str, source_ids: list[str] | None = None, limit: int = 4) -> dict[str, Any]:
    if not isinstance(source_ids, list):
        source_ids = None
    return public_sources.search_public_info(
        query or "",
        source_ids=[str(item) for item in source_ids] if source_ids else None,
        limit=int(limit or 4),
    )


def fetch_tku_public_source(source_id: str, query: str = "") -> dict[str, Any]:
    return public_sources.fetch_public_source(
        source_id or "",
        query or "",
    )


def search_instagram_public_account(username: str, limit: int = 10) -> dict[str, Any]:
    return public_sources.search_instagram_public_account(
        username or "",
        int(limit or 10),
    )


def search_instagram_public_hashtag(hashtag: str, limit: int = 10) -> dict[str, Any]:
    return public_sources.search_instagram_public_hashtag(
        hashtag or "",
        int(limit or 10),
    )
