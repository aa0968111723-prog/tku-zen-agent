"""Read-only, ACL-first visual library search for the agent."""

from __future__ import annotations

from ..services import context as context_service
from ..services.library_context import LibraryContextResolver
from .knowledge import internal_data_guard


def search_visual_library(
    query: str,
    project_id: str = "",
    scene_position: int = 0,
    shot_position: int = 0,
    limit: int = 8,
) -> dict:
    blocked = internal_data_guard("視覺素材庫")
    if blocked is not None:
        return blocked
    ctx = context_service.current()
    if ctx is None:
        return {"ok": False, "code": "missing_request_context", "message": "目前沒有可驗證的使用者情境。"}
    chosen_project = project_id or ctx.project_id or ""
    result = LibraryContextResolver().search(
        ctx.user_id,
        query=(query or "").strip(),
        project_id=chosen_project,
        scene_position=max(0, int(scene_position or 0)),
        shot_position=max(0, int(shot_position or 0)),
        limit=max(1, min(int(limit or 8), 10)),
    )
    compact_items = []
    for item in result["items"]:
        compact_items.append({
            "asset_id": item["asset_id"],
            "filename": item.get("original_filename"),
            "dimensions": f"{item.get('width', 0)}x{item.get('height', 0)}",
            "quality_score": item.get("quality_score"),
            "school": item.get("school_name"),
            "club": item.get("club_name"),
            "source": item.get("source"),
            "verification_status": item.get("review_status"),
            "confidence": item.get("confidence_score"),
            "matched_entities": item.get("matched_entities", []),
            "matched_dates": item.get("matched_dates", []),
            "reasons": item.get("recommendation_reasons", []),
            "thumbnail_url": item.get("thumbnail_url"),
        })
    return {
        "ok": True,
        "acl_first": True,
        "total": result["total"],
        "context": result["context"],
        "items": compact_items,
        "message": "只回傳目前使用者可見且符合任務情境的素材；人物姓名篩選只接受已確認關聯。",
    }


SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_visual_library",
        "description": (
            "從已授權的視覺素材庫尋找圖片，會依序解析目前專案、故事、場景、分鏡與輸出需求。"
            "當使用者說『找照片』『適合第二幕』『加入招生影片』『找海報』時使用。"
            "回傳來源、信心、確認狀態、匹配理由與 asset_id，不會把未確認人物當成真實姓名。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "自然語言素材需求"},
                "project_id": {"type": "string", "description": "已知專案 ID；不確定可留空，由目前任務情境解析"},
                "scene_position": {"type": "integer", "description": "第幾幕／場，未知填 0"},
                "shot_position": {"type": "integer", "description": "第幾鏡，未知填 0"},
                "limit": {"type": "integer", "description": "回傳筆數，預設 8，最多 10"},
            },
            "required": ["query"],
        },
    },
}
