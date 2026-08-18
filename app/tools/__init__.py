"""工具註冊表與 skill 篩選。

每次請求只把該 skill 需要的工具 schema 送給模型。開源模型的工具選擇
正確率會隨工具數量明顯下降，而且每個 schema 都要佔輸入 token。
"""

from __future__ import annotations

import inspect
import traceback
from typing import Any, Callable

from . import document, examples, gform, knowledge, slides, spreadsheet, term

_REGISTRY: dict[str, tuple[Callable[..., dict], dict]] = {
    "search_knowledge": (knowledge.search_knowledge, knowledge.SCHEMA),
    "search_previous_examples": (examples.search_previous_examples, examples.SCHEMA),
    "get_current_term": (term.get_current_term, term.SCHEMA),
    "create_spreadsheet": (spreadsheet.create_spreadsheet, spreadsheet.SCHEMA),
    "create_document": (document.create_document, document.SCHEMA),
    "create_slides": (slides.create_slides, slides.SCHEMA),
    "create_google_form": (gform.create_google_form, gform.SCHEMA),
}

# 全部工具的 schema。實際送給模型的是 schemas_for() 篩過的子集。
SCHEMAS: list[dict] = [schema for _, schema in _REGISTRY.values()]

LABELS = {
    "search_knowledge": "查詢社團知識庫",
    "search_previous_examples": "找歷年範例",
    "get_current_term": "查本學期資料",
    "create_spreadsheet": "建立試算表",
    "create_document": "建立文件",
    "create_slides": "建立簡報",
    "create_google_form": "產生 Google 表單腳本",
}


def register(name: str, fn: Callable[..., dict], schema: dict, label: str = "") -> None:
    """讓外部（測試、之後的擴充）掛新工具進來。"""
    _REGISTRY[name] = (fn, schema)
    LABELS.setdefault(name, label or name)
    SCHEMAS.clear()
    SCHEMAS.extend(s for _, s in _REGISTRY.values())


def unregister(name: str) -> None:
    _REGISTRY.pop(name, None)
    LABELS.pop(name, None)
    SCHEMAS.clear()
    SCHEMAS.extend(s for _, s in _REGISTRY.values())


def all_names() -> list[str]:
    return list(_REGISTRY)


def schemas_for(names: tuple[str, ...] | list[str]) -> list[dict]:
    """只回傳指定工具的 schema。

    未知名稱直接忽略而不是報錯 —— skill 定義裡列到還沒實作的工具時，
    應該安靜降級，不該讓整個請求掛掉。
    """
    out: list[dict] = []
    for name in names:
        entry = _REGISTRY.get(name)
        if entry:
            out.append(entry[1])
    return out or SCHEMAS


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
