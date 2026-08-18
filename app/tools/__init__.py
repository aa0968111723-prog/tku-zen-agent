"""工具註冊表。刻意只留 5 個工具 —— 開源模型的工具選擇正確率，
會隨工具數量增加而明顯下降。"""

from __future__ import annotations

import inspect
import traceback
from typing import Any, Callable

from . import document, gform, knowledge, slides, spreadsheet

_REGISTRY: dict[str, tuple[Callable[..., dict], dict]] = {
    "search_knowledge": (knowledge.search_knowledge, knowledge.SCHEMA),
    "create_spreadsheet": (spreadsheet.create_spreadsheet, spreadsheet.SCHEMA),
    "create_document": (document.create_document, document.SCHEMA),
    "create_slides": (slides.create_slides, slides.SCHEMA),
    "create_google_form": (gform.create_google_form, gform.SCHEMA),
}

SCHEMAS: list[dict] = [schema for _, schema in _REGISTRY.values()]

# 給介面顯示用的中文名稱
LABELS = {
    "search_knowledge": "查詢社團知識庫",
    "create_spreadsheet": "建立試算表",
    "create_document": "建立文件",
    "create_slides": "建立簡報",
    "create_google_form": "產生 Google 表單腳本",
}


def dispatch(name: str, arguments: dict[str, Any]) -> dict:
    """執行工具。任何例外都轉成給模型看的錯誤訊息，讓它有機會自己修正重試。"""
    entry = _REGISTRY.get(name)
    if entry is None:
        return {
            "ok": False,
            "message": f"沒有名為 {name} 的工具。可用的工具是：{', '.join(_REGISTRY)}。",
        }

    fn, _ = entry

    if "__parse_error__" in arguments:
        return {
            "ok": False,
            "message": "你傳的參數不是合法的 JSON，我無法解析。請重新呼叫一次，注意 JSON 格式要正確。",
        }

    # 丟掉模型多給的參數，避免 TypeError
    sig = inspect.signature(fn)
    accepted = {k: v for k, v in arguments.items() if k in sig.parameters}
    missing = [
        p.name
        for p in sig.parameters.values()
        if p.default is inspect.Parameter.empty and p.name not in accepted
    ]
    if missing:
        return {"ok": False, "message": f"缺少必要參數：{', '.join(missing)}。請補上後重新呼叫。"}

    try:
        return fn(**accepted)
    except Exception as exc:  # noqa: BLE001 —— 工具錯誤不該讓整個對話中斷
        return {
            "ok": False,
            "message": f"執行 {name} 的時候出錯：{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(limit=3),
        }
