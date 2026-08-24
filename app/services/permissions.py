"""權限模型：把「是不是管理者」拆成四種可個別驗證的權限。

原本整個系統只有 isAdmin 一個開關，這有兩個問題：
  · 「編輯本學期資料」和「對外發佈 Instagram」風險等級完全不同，
    卻共用同一把鑰匙。
  · 對外發佈（會花錢、會公開）沒有獨立的授權層，一旦之後接上
    官方 API，管理者一個手滑就直接發出去了。

四種權限：
  can_view    —— 讀知識庫、對話、自己的產出（登入即有）
  can_manage  —— 編輯本學期資料、重建索引（管理者）
  can_approve —— 核准對外發佈（IG 發文、回覆留言、傳送訊息）
  can_spend   —— 動用會消耗額度或金錢的外部 API

can_approve / can_spend 預設**沒有任何人擁有**（fail closed）：
必須同時（1）是管理者、（2）部署端明確設定 EXTERNAL_PUBLISH_ENABLED=1。
沒設定就永遠是草稿模式，這是刻意的，不是缺陷。

與 roles.py 的關係：roles 定義穩定的角色名稱與「代理不得自主執行」
的動作清單；這裡是端點層實際擋人的檢查。IG 相關動作同時列在
roles.FORBIDDEN_AUTONOMOUS_ACTIONS —— 就算未來有人把工具接給模型，
兩層都會擋。
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request, Response

from .. import config
from . import auth, roles

DRAFT_ONLY_DETAIL = "目前為草稿模式：對外發佈權限尚未開啟，僅能產生草稿"
NEED_APPROVE_DETAIL = "需要具備「核准對外發佈」權限才能執行這個操作"
NEED_SPEND_DETAIL = "需要具備「動用外部額度」權限才能執行這個操作"
NEED_MANAGE_DETAIL = "需要管理者授權"
NEED_CONFIRM_DETAIL = "這是對外發佈操作，必須在確認畫面勾選「我確認要發佈」後才能執行"


@dataclass(frozen=True)
class Permissions:
    """一個請求身分目前擁有的權限。"""

    user_id: str | None = None
    role: roles.Role = roles.Role.GUEST
    can_view: bool = False
    can_manage: bool = False
    can_approve: bool = False
    can_spend: bool = False


def resolve(request: Request) -> Permissions:
    """由請求的 cookie 推導權限。不丟例外——沒登入就是全 False。"""
    user_id = auth.optional_identity(request)
    if user_id is None:
        return Permissions()
    is_admin = auth.admin_identity(request)
    publish_enabled = bool(config.EXTERNAL_PUBLISH_ENABLED)
    return Permissions(
        user_id=user_id,
        role=roles.Role.ADMIN if is_admin else roles.Role.USER,
        can_view=True,
        can_manage=is_admin,
        # 對外發佈：管理者 + 部署端明確開啟，缺一不可（fail closed）
        can_approve=is_admin and publish_enabled,
        can_spend=is_admin and publish_enabled,
    )


def require_view(request: Request, response: Response) -> str:
    """登入即可。回傳 user_id。"""
    return auth.require_user(request, response)


def require_manage(request: Request) -> Permissions:
    perms = resolve(request)
    if perms.user_id is None:
        raise HTTPException(status_code=401, detail=auth.UNAUTHORIZED_DETAIL)
    if not perms.can_manage:
        raise HTTPException(status_code=403, detail=NEED_MANAGE_DETAIL)
    return perms


def require_publish(request: Request) -> Permissions:
    """對外發佈的完整檢查鏈：登入 → 管理 → 核准權 → 花費權。

    每一層分開回報，讓使用者知道卡在哪一關；但不管卡在哪一關，
    結果都一樣：不會發佈（fail closed）。
    """
    perms = resolve(request)
    if perms.user_id is None:
        raise HTTPException(status_code=401, detail=auth.UNAUTHORIZED_DETAIL)
    if not perms.can_manage:
        raise HTTPException(status_code=403, detail=NEED_MANAGE_DETAIL)
    if not perms.can_approve:
        raise HTTPException(status_code=403, detail=DRAFT_ONLY_DETAIL)
    if not perms.can_spend:
        raise HTTPException(status_code=403, detail=NEED_SPEND_DETAIL)
    return perms


def require_confirmation(confirm: bool | None) -> None:
    """對外發佈一定要帶明確的 confirm=true——預設永遠是草稿。"""
    if confirm is not True:
        raise HTTPException(status_code=428, detail=NEED_CONFIRM_DETAIL)
