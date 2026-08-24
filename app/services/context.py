"""目前請求的身分情境。

工具（app/tools/*）需要知道「這份產出是誰的、屬於哪個 session/project」
才能登記到 artifact 表，但又不該為此在每個工具簽章都多塞三個參數 ——
那會污染送給模型的 JSON schema，模型還可能亂填。

所以用 contextvars：orchestrator 在跑之前設好，工具讀取。
contextvars 對 async 與 threadpool 都是安全的（asyncio.to_thread 會複製 context）。
"""

from __future__ import annotations

import contextlib
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class RequestContext:
    user_id: str
    session_id: str | None = None
    project_id: str | None = None
    auth_level: str = "general"


_ctx: ContextVar[RequestContext | None] = ContextVar("tku_request_context", default=None)

# 本輪的研究範圍（ResearchScope.to_dict()）。
# 工具（search_knowledge、search_previous_examples）要靠它判斷：
# 外部研究模式下，淡江內部資料不得作為外校證據。
# 存 dict 而不是 dataclass，避免 services 層反向依賴 research 層。
_research_scope: ContextVar[dict | None] = ContextVar("tku_research_scope", default=None)


def current() -> RequestContext | None:
    return _ctx.get()


def set_research_scope(scope: dict | None) -> None:
    """orchestrator 在檢索與工具執行前設定本輪研究範圍。"""
    _research_scope.set(scope)


def research_scope() -> dict | None:
    return _research_scope.get()


def require_user() -> str:
    ctx = _ctx.get()
    if ctx is None:
        raise RuntimeError("這個操作需要 RequestContext，但目前沒有設定。")
    return ctx.user_id


def replace_current(ctx: RequestContext) -> None:
    """在同一個 request scope 內補上後來才建立的 project_id。

    外層 ``use`` 仍負責在請求結束時還原 ContextVar；這裡只更新目前值，
    讓後續 threadpool 工具取得正確的 project。
    """
    _ctx.set(ctx)


@contextlib.contextmanager
def use(ctx: RequestContext) -> Iterator[RequestContext]:
    token = _ctx.set(ctx)
    try:
        yield ctx
    finally:
        _ctx.reset(token)
