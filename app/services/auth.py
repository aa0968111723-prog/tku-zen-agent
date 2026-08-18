"""最小可用的身分識別。

兩種模式（由 config.auth_mode() 決定）：

  local —— 本機單人。所有請求都是同一個固定使用者，不需要登入，
           一鍵啟動的體驗不變，而且重開機後歷史還在。

  token —— 部署模式。要先用 APP_ACCESS_TOKEN 換到身分 cookie 才能用。
           每個瀏覽器換到一個獨立的 user_id，所以幹部之間的
           session 與產出彼此隔離。

刻意不做帳號密碼：社團規模用不到，而且自己實作密碼儲存風險比較大。
之後要接 Google 登入的話，換掉這個檔案就好。
"""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, Response

from .. import config
from .session_store import get_store

COOKIE_NAME = "tku_uid"
LOCAL_USER_ID = "u_local"
COOKIE_MAX_AGE = 60 * 60 * 24 * 180  # 180 天


def _set_cookie(response: Response, user_id: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        user_id,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        # 部署一定是 https；本機是 http，設 secure 會讓 cookie 存不進去
        secure=config.auth_mode() == "token",
        path="/",
    )


def check_token(token: str) -> bool:
    expected = config.APP_ACCESS_TOKEN
    if not expected:
        return False
    return hmac.compare_digest(token.strip(), expected)


def login(response: Response, token: str) -> str:
    """token 模式的登入。回傳 user_id。"""
    if not check_token(token):
        raise HTTPException(status_code=401, detail="存取碼不正確。")
    user_id = get_store().ensure_user()
    _set_cookie(response, user_id)
    return user_id


def identify(request: Request, response: Response) -> str:
    """取得目前請求的 user_id，必要時建立。

    token 模式下沒有有效 cookie 就 401，讓前端跳出輸入存取碼的畫面。
    """
    store = get_store()
    mode = config.auth_mode()

    if mode == "local":
        user_id = store.ensure_user(LOCAL_USER_ID, is_local=True, display_name="本機使用者")
        _set_cookie(response, user_id)
        return user_id

    raw = request.cookies.get(COOKIE_NAME)
    # 必須確認這個 id 真的存在。ensure_user 看到沒見過的 id 會直接建帳號，
    # 若在這裡呼叫它，等於隨便偽造一個 cookie 值就自己開了一個帳號。
    if raw and store.user_exists(raw):
        store.ensure_user(raw)
        return raw

    raise HTTPException(status_code=401, detail="需要存取碼。")


def optional_identity(request: Request) -> str | None:
    """不強制登入的端點用（例如首頁）。"""
    if config.auth_mode() == "local":
        return LOCAL_USER_ID
    return request.cookies.get(COOKIE_NAME)
