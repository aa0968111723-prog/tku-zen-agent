"""Tool registry with schema scoping and dispatch-time authorization."""

from __future__ import annotations

import inspect
import logging
from typing import Any, Callable

from . import activity, artifact, document, examples, gform, knowledge, perplexity, public_sources, slides, social, spreadsheet, term, visual_library

logger = logging.getLogger(__name__)
Permission = str  # general | admin_confirm

_REGISTRY: dict[str, tuple[Callable[..., dict], dict, Permission]] = {
    "search_knowledge": (knowledge.search_knowledge, knowledge.SCHEMA, "general"),
    "search_previous_examples": (examples.search_previous_examples, examples.SCHEMA, "general"),
    "get_current_term": (term.get_current_term, term.SCHEMA, "general"),
    "create_spreadsheet": (spreadsheet.create_spreadsheet, spreadsheet.SCHEMA, "general"),
    "create_document": (document.create_document, document.SCHEMA, "general"),
    "create_slides": (slides.create_slides, slides.SCHEMA, "general"),
    "create_google_form": (gform.create_google_form, gform.SCHEMA, "general"),
    "search_social_references": (social.search_social_references, social.SEARCH_SCHEMA, "general"),
    "compare_social_strategies": (social.compare_social_strategies, social.COMPARE_SCHEMA, "general"),
    "analyze_social_positioning": (social.analyze_social_positioning, social.ANALYZE_SCHEMA, "general"),
    "create_social_post": (social.create_social_post, social.POST_SCHEMA, "general"),
    "create_social_carousel": (social.create_social_carousel, social.CAROUSEL_SCHEMA, "general"),
    "create_social_story": (social.create_social_story, social.STORY_SCHEMA, "general"),
    "create_reels_script": (social.create_reels_script, social.REELS_SCHEMA, "general"),
    "create_social_content_calendar": (social.create_social_content_calendar, social.CALENDAR_SCHEMA, "general"),
    "list_tku_public_sources": (public_sources.list_tku_public_sources, public_sources.LIST_SCHEMA, "general"),
    "search_tku_public_info": (public_sources.search_tku_public_info, public_sources.SEARCH_SCHEMA, "general"),
    "fetch_tku_public_source": (public_sources.fetch_tku_public_source, public_sources.FETCH_SCHEMA, "general"),
    "search_instagram_public_hashtag": (public_sources.search_instagram_public_hashtag, public_sources.HASHTAG_SCHEMA, "general"),
    "search_instagram_public_account": (public_sources.search_instagram_public_account, public_sources.ACCOUNT_SCHEMA, "general"),
    "search_perplexity_web": (perplexity.search_perplexity_web, perplexity.SCHEMA, "general"),
    "create_social_ab_test": (social.create_social_ab_test, social.AB_TEST_SCHEMA, "general"),
    "create_social_image_prompt": (social.create_social_image_prompt, social.IMAGE_SCHEMA, "general"),
    "create_social_video_prompt": (social.create_social_video_prompt, social.VIDEO_SCHEMA, "general"),
    "create_activity": (activity.create_activity, activity.CREATE_SCHEMA, "general"),
    "update_activity": (activity.update_activity, activity.UPDATE_SCHEMA, "general"),
    "add_activity_task": (activity.add_activity_task, activity.ADD_TASK_SCHEMA, "general"),
    "update_activity_task": (activity.update_activity_task, activity.UPDATE_TASK_SCHEMA, "general"),
    "get_activity_status": (activity.get_activity_status, activity.STATUS_SCHEMA, "general"),
    "list_activities": (activity.list_activities, activity.LIST_SCHEMA, "general"),
    "read_artifact": (artifact.read_artifact, artifact.SCHEMA, "general"),
    "search_visual_library": (visual_library.search_visual_library, visual_library.SCHEMA, "general"),
}

def _model_schema(name: str, schema: dict) -> dict:
    """Normalize legacy object schemas to the OpenAI tool envelope.

    Public-source integrations predate the current registry contract and
    expose only the JSON Schema body. Keep their internal definitions intact,
    but ensure every schema offered to a model has ``function.name`` and
    ``function.parameters``.
    """
    if "function" in schema:
        return schema
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": schema.get("description", ""),
            "parameters": schema,
        },
    }


SCHEMAS: list[dict] = [
    _model_schema(name, schema) for name, (_, schema, _) in _REGISTRY.items()
]

# 使用者看得到的工具名稱一律繁體中文 —— 內部英文工具名（create_social_carousel
# 之類）絕不能直接呈現在介面上。tests/test_ui_language.py 會掃這份表。
LABELS = {
    "search_knowledge": "搜尋社團知識庫",
    "search_previous_examples": "搜尋歷年範例",
    "get_current_term": "查本學期資料",
    "create_spreadsheet": "建立試算表",
    "create_document": "建立文件",
    "create_slides": "建立簡報",
    "create_google_form": "建立 Google 表單",
    "create_activity": "建立活動資料",
    "update_activity": "更新活動資料",
    "add_activity_task": "新增活動待辦",
    "update_activity_task": "更新活動待辦",
    "get_activity_status": "檢查活動進度",
    "list_activities": "列出活動",
    "read_artifact": "讀取上一份產出",
    "search_visual_library": "搜尋視覺素材庫",
    "search_social_references": "搜尋外校公開參考",
    "compare_social_strategies": "比較外校社群策略",
    "analyze_social_positioning": "分析社群定位",
    "create_social_post": "建立貼文草稿",
    "create_social_carousel": "建立輪播草稿",
    "create_social_story": "建立限動草稿",
    "create_reels_script": "建立 Reels 腳本",
    "create_social_content_calendar": "建立內容月曆",
    "list_tku_public_sources": "列出淡江公開來源",
    "search_tku_public_info": "搜尋淡江公開資訊",
    "fetch_tku_public_source": "讀取淡江官方來源",
    "search_instagram_public_hashtag": "搜尋 Instagram 公開標籤",
    "search_instagram_public_account": "讀取 Instagram 公開帳號",
    "search_perplexity_web": "搜尋最新公開網路資訊",
    "create_social_ab_test": "建立 A/B 測試草稿",
    "create_social_image_prompt": "建立圖像提示詞",
    "create_social_video_prompt": "建立影片提示詞",
}
# 保險：漏掛中文名的工具顯示通用中文字樣，不顯示英文內部名
for _name in _REGISTRY:
    LABELS.setdefault(_name, "執行工具")


def register(
    name: str,
    fn: Callable[..., dict],
    schema: dict,
    label: str = "",
    permission: Permission = "general",
) -> None:
    if permission not in {"general", "admin_confirm"}:
        raise ValueError("permission must be general or admin_confirm")
    _REGISTRY[name] = (fn, schema, permission)
    # fallback 不用英文內部名——那會原封不動出現在前端進度列（稽核不可靠 #42）
    LABELS[name] = label or "執行工具"
    SCHEMAS.clear()
    SCHEMAS.extend(_model_schema(n, s) for n, (_, s, _) in _REGISTRY.items())


def unregister(name: str) -> None:
    _REGISTRY.pop(name, None)
    LABELS.pop(name, None)
    SCHEMAS.clear()
    SCHEMAS.extend(_model_schema(n, s) for n, (_, s, _) in _REGISTRY.items())


def all_names() -> list[str]:
    return list(_REGISTRY)


def permission_for(name: str) -> Permission | None:
    entry = _REGISTRY.get(name)
    return entry[2] if entry else None


def schemas_for(names: tuple[str, ...] | list[str]) -> list[dict]:
    """Return only explicitly requested known schemas; empty means none."""
    return [_model_schema(name, _REGISTRY[name][1]) for name in names if name in _REGISTRY]


def _auth_level() -> str:
    from ..services import context as ctx_mod

    ctx = ctx_mod.current()
    return getattr(ctx, "auth_level", "general") if ctx else "general"


def dispatch(name: str, arguments: dict[str, Any], *, auth_level: str | None = None) -> dict:
    entry = _REGISTRY.get(name)
    if entry is None:
        return {"ok": False, "code": "unknown_tool", "message": "找不到這個工具"}

    fn, _schema, permission = entry
    level = auth_level or _auth_level()
    if permission == "admin_confirm" and level != "admin":
        return {
            "ok": False,
            "code": "insufficient_permission",
            "message": "這個工具需要管理者授權與人工確認",
        }

    if "__parse_error__" in arguments:
        return {"ok": False, "code": "invalid_arguments", "message": "工具參數不是合法的 JSON，請重新呼叫"}

    sig = inspect.signature(fn)
    accepted = {k: v for k, v in arguments.items() if k in sig.parameters}
    missing = [
        p.name
        for p in sig.parameters.values()
        if p.default is inspect.Parameter.empty and p.name not in accepted
    ]
    if missing:
        return {"ok": False, "code": "missing_arguments", "message": f"工具缺少必要參數：{', '.join(missing)}，請重新呼叫"}

    try:
        return fn(**accepted)
    except Exception:  # noqa: BLE001
        logger.exception("tool execution failed: %s", name)
        return {"ok": False, "code": "tool_failed", "message": "工具執行失敗，請稍後再試"}
