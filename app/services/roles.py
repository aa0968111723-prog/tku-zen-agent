"""角色常數與權限輔助。

PR-01 只建立穩定的角色名稱與檢查函式，不改變既有 cookie 登入流程。
後續 HITL／工具暴露可依 Role 擴充。
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    GUEST = "guest"
    USER = "user"
    EDITOR = "editor"
    ADMIN = "admin"
    SYSTEM = "system"


# 由低到高：數字越大權限越高（僅供比較，正式授權仍看 cookie／token）
ROLE_RANK = {
    Role.GUEST: 0,
    Role.USER: 10,
    Role.EDITOR: 20,
    Role.ADMIN: 30,
    Role.SYSTEM: 40,
}


def rank(role: Role | str) -> int:
    if isinstance(role, str):
        try:
            role = Role(role)
        except ValueError:
            return -1
    return ROLE_RANK.get(role, -1)


def has_at_least(actual: Role | str, required: Role | str) -> bool:
    return rank(actual) >= rank(required)


# 高風險動作：代理不得自行完成，須人工或 admin
FORBIDDEN_AUTONOMOUS_ACTIONS = frozenset(
    {
        "instagram.publish",
        "instagram.comments.reply",
        "instagram.messages.send",
        "data.bulk_export",
        "data.delete_user",
        "term.overwrite_without_admin",
    }
)


def is_forbidden_autonomous(action: str) -> bool:
    return action in FORBIDDEN_AUTONOMOUS_ACTIONS
