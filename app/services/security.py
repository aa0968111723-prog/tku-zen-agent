"""安全輔助：密鑰遮罩、audit 寫入便捷函式。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request

from . import roles
from .audit import mask_secrets
from .session_store import get_store

logger = logging.getLogger(__name__)


def mask_secret(value: str | None, *, keep: int = 4) -> str:
    """日誌用遮罩：保留前後少數字元。"""
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "***"
    return f"{value[:keep]}…{value[-keep:]}"


def client_ip(request: Request | None) -> str:
    if request is None or request.client is None:
        return "unknown"
    return request.client.host or "unknown"


def audit(
    action: str,
    *,
    request: Request | None = None,
    actor_user_id: str | None = None,
    role: str | roles.Role | None = None,
    resource_type: str = "",
    resource_id: str = "",
    success: bool = True,
    detail: dict[str, Any] | None = None,
) -> bool:
    """Write one audit row using SessionStore.append_audit's real signature.

    Strategy: user-facing operations stay available if audit I/O fails, but
    the failure is logged and callers receive False. Never swallow TypeError
    from a mismatched contract.
    """
    role_value = role.value if isinstance(role, roles.Role) else (role or "")
    resource = "/".join(part for part in (resource_type, resource_id) if part)[:240]
    payload = dict(detail or {})
    if role_value:
        payload.setdefault("role", role_value)
    try:
        get_store().append_audit(
            actor_user_id=actor_user_id or "",
            action=action[:120],
            resource=resource,
            detail=mask_secrets(str(payload))[:2000],
            ip=client_ip(request),
            ok=bool(success),
        )
        return True
    except TypeError:
        raise
    except Exception:
        logger.exception("audit write failed action=%s", action)
        return False
