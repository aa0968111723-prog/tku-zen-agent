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


_ctx: ContextVar[RequestContext | None] = ContextVar("tku_request_context", default=None)


def current() -> RequestContext | None:
    return _ctx.get()


def require_user() -> str:
    ctx = _ctx.get()
    if ctx is None:
        raise RuntimeError("這個操作需要 RequestContext，但目前沒有設定。")
    return ctx.user_id


@contextlib.contextmanager
def use(ctx: RequestContext) -> Iterator[RequestContext]:
    token = _ctx.set(ctx)
    try:
        yield ctx
    finally:
        _ctx.reset(token)
