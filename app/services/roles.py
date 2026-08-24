"""應用程式角色定義與權限對照。

Guest / User / Editor / Admin / System。
目前實作：未登入 = Guest；一般 cookie = User；admin cookie = Admin。
Editor 預留給幹部細分（與 User 同等能力，直到產品需要獨立 token）。
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    GUEST = "guest"
    USER = "user"
    EDITOR = "editor"
    ADMIN = "admin"
    SYSTEM = "system"


# 由低到高，便於比較
_ROLE_RANK = {
    Role.GUEST: 0,
    Role.USER: 1,
    Role.EDITOR: 2,
    Role.ADMIN: 3,
    Role.SYSTEM: 4,
}


def rank(role: Role) -> int:
    return _ROLE_RANK[role]


def at_least(role: Role, minimum: Role) -> bool:
    return rank(role) >= rank(minimum)


def resolve_role(*, authenticated: bool, is_admin: bool) -> Role:
    if is_admin:
        return Role.ADMIN
    if authenticated:
        return Role.USER
    return Role.GUEST


# 工具／端點所需最低角色（文件化；執行時仍以 FastAPI Depends 為準）
ENDPOINT_MIN_ROLE: dict[str, Role] = {
    "chat": Role.USER,
    "session": Role.USER,
    "artifacts": Role.USER,
    "download": Role.USER,
    "term.read": Role.USER,
    "term.write": Role.ADMIN,
    "reindex": Role.ADMIN,
    "instagram.publish": Role.ADMIN,
    "admin.health": Role.ADMIN,
}
