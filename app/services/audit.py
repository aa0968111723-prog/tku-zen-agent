"""Append-only audit log 與敏感字串遮罩。

設計目標：
  · 登入、管理操作、發布嘗試可追溯
  · 不把完整 token／授權碼寫進資料庫或 log
  · 失敗不得拖垮主流程（寫入失敗只記 logger）
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import Request

from .session_store import get_store

logger = logging.getLogger(__name__)

# 常見密鑰形態：nvapi-、長 hex、Bearer、明顯 token 參數
_SECRET_PATTERNS = [
    re.compile(r"(nvapi-[A-Za-z0-9_-]{8,})"),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{8,}", re.I),
    re.compile(r"([A-Za-z0-9_-]*(?:token|secret|password|passwd|api[_-]?key)[A-Za-z0-9_-]*\s*[=:]\s*)([^\s,;]{6,})", re.I),
]


def mask_secrets(text: str | None, keep: int = 4) -> str:
    """遮罩字串中的疑似密鑰，保留前後極短片段以便除錯。"""
    if not text:
        return ""
    out = str(text)

    def _mask_match(m: re.Match[str]) -> str:
        g = m.group(0)
        if len(g) <= keep * 2:
            return "***"
        return f"{g[:keep]}…{g[-keep:]}"

    for pat in _SECRET_PATTERNS:
        out = pat.sub(_mask_match, out)
    return out[:2000]


def _client_ip(request: Request | None) -> str:
    if request is None:
        return ""
    # 部署在反向代理後可再讀 X-Forwarded-For；此處先用直接 peer
    return request.client.host if request.client else ""


def write_audit(
    *,
    action: str,
    actor_user_id: str | None = None,
    resource: str = "",
    detail: str | dict[str, Any] | None = None,
    request: Request | None = None,
    ok: bool = True,
) -> None:
    """寫入一筆 audit。任何例外只記 logger，不向外拋。"""
    try:
        if isinstance(detail, dict):
            detail_s = mask_secrets(str(detail))
        else:
            detail_s = mask_secrets(detail)
        get_store().append_audit(
            actor_user_id=actor_user_id or "",
            action=action[:120],
            resource=(resource or "")[:240],
            detail=detail_s,
            ip=_client_ip(request),
            ok=ok,
        )
    except TypeError:
        # Contract mismatch must not be swallowed; callers and tests see it.
        raise
    except Exception:  # noqa: BLE001
        logger.exception("audit write failed action=%s", action)
