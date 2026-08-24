"""安全輔助：密鑰遮罩、audit 寫入便捷函式。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request

from . import roles
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
) -> None:
    """寫入 audit_logs；失敗只打 log，不影響主流程。"""
    role_value = role.value if isinstance(role, roles.Role) else (role or "")
    ip = client_ip(request)
    try:
        get_store().append_audit(
            action=action,
            actor_user_id=actor_user_id,
            role=role_value,
            resource_type=resource_type,
            resource_id=resource_id,
            ip=ip,
            success=success,
            detail=detail,
        )
    except Exception:  # noqa: BLE001
        logger.exception("audit write failed action=%s", action)
