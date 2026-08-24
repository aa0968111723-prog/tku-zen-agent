"""Token authentication, cookie continuity, and small-process rate limiting."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from threading import Lock

from fastapi import HTTPException, Request, Response

from .. import config
from .session_store import get_store

COOKIE_NAME = config.ACCESS_CODE_COOKIE_NAME
LEGACY_COOKIE_NAME = "tku_uid"
ADMIN_COOKIE_NAME = config.ADMIN_COOKIE_NAME
LOCAL_USER_ID = "u_local"
UNAUTHORIZED_DETAIL = "請先輸入授權碼"
INVALID_TOKEN_DETAIL = "授權碼不正確"
RATE_LIMIT_DETAIL = "嘗試次數過多，請稍後再試"
LOCK_SECONDS = 15 * 60
MAX_FAILURES = 5


@dataclass
class _FailureState:
    failures: int = 0
    locked_until: float = 0.0


_failure_lock = Lock()
_failures: dict[tuple[str, str], _FailureState] = {}
_admin_sessions: set[str] = set()


def _set_cookie(response: Response, user_id: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        user_id,
        max_age=config.ACCESS_CODE_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=config.auth_mode() == "token",
        path="/",
    )


def _set_admin_cookie(response: Response) -> None:
    value = secrets.token_urlsafe(24)
    with _failure_lock:
        _admin_sessions.add(value)
    response.set_cookie(
        ADMIN_COOKIE_NAME,
        value,
        max_age=config.ADMIN_COOKIE_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="strict",  # 管理 cookie 不需要任何跨站情境，直接用最嚴格的
        path="/",
    )


def check_token(token: str | None) -> bool:
    expected = config.APP_ACCESS_TOKEN
    if not expected or not isinstance(token, str):
        return False
    return hmac.compare_digest(token.strip(), expected)


def check_admin_token(token: str | None) -> bool:
    expected = config.ADMIN_ACCESS_TOKEN
    if not expected or not isinstance(token, str):
        return False
    return hmac.compare_digest(token.strip(), expected)


def _request_dimensions(request: Request, cookie_name: str) -> tuple[tuple[str, str], ...]:
    """鎖定計數的維度。

    沒帶 cookie 的請求**不能**共用一個 "anonymous" 桶——那會讓任何人
    連錯 5 次就把全部新使用者鎖 15 分鐘（稽核不可靠 #30 的 DoS）。
    無 cookie 時只用 IP 維度。
    """
    ip = request.client.host if request.client else "unknown"
    raw_cookie = request.cookies.get(cookie_name, "")
    dims: list[tuple[str, str]] = [("ip", f"{cookie_name}:{ip}")]
    if raw_cookie:
        cookie_key = hashlib.sha256(raw_cookie.encode("utf-8")).hexdigest()
        dims.append(("cookie", f"{cookie_name}:{cookie_key}"))
    return tuple(dims)


def _check_locked(request: Request, cookie_name: str) -> None:
    now = time.monotonic()
    with _failure_lock:
        retry_after = 0
        for key in _request_dimensions(request, cookie_name):
            state = _failures.get(key)
            if state and state.locked_until > now:
                retry_after = max(retry_after, int(state.locked_until - now) + 1)
        if retry_after:
            exc = HTTPException(status_code=429, detail=RATE_LIMIT_DETAIL)
            exc.headers = {"Retry-After": str(retry_after)}
            raise exc


def _record_failure(request: Request, cookie_name: str) -> None:
    now = time.monotonic()
    with _failure_lock:
        for key in _request_dimensions(request, cookie_name):
            state = _failures.setdefault(key, _FailureState())
            if state.locked_until and state.locked_until <= now:
                # 鎖已到期才歸零重算；沒上過鎖不能歸零，否則永遠鎖不住
                state.failures = 0
                state.locked_until = 0.0
            state.failures += 1
            if state.failures >= MAX_FAILURES:
                state.locked_until = now + LOCK_SECONDS


def _clear_failures(request: Request, cookie_name: str) -> None:
    with _failure_lock:
        for key in _request_dimensions(request, cookie_name):
            _failures.pop(key, None)


def _invalid_token(request: Request, cookie_name: str) -> HTTPException:
    _record_failure(request, cookie_name)
    return HTTPException(status_code=401, detail=INVALID_TOKEN_DETAIL)


def login(request: Request, response: Response, token: str | None) -> None:
    _check_locked(request, COOKIE_NAME)
    store = get_store()
    # 空字串、純空白、null 一律視同錯誤授權碼：同樣走 401 + 失敗計數，
    # 不提早 return —— 回應形狀一致才不會洩漏「授權碼是否存在」。
    # 稽核在 main.py 的端點層寫入（auth.login / auth.admin_login）。
    if not check_token(token):
        raise _invalid_token(request, COOKIE_NAME)
    current = request.cookies.get(COOKIE_NAME) or request.cookies.get(LEGACY_COOKIE_NAME)
    user_id = current if current and store.user_exists(current) else store.ensure_user()
    _set_cookie(response, user_id)
    if request.cookies.get(LEGACY_COOKIE_NAME) and not request.cookies.get(COOKIE_NAME):
        response.delete_cookie(LEGACY_COOKIE_NAME, path="/")
    _clear_failures(request, COOKIE_NAME)


def admin_login(request: Request, response: Response, token: str | None) -> None:
    _check_locked(request, ADMIN_COOKIE_NAME)
    if not check_admin_token(token):
        raise _invalid_token(request, ADMIN_COOKIE_NAME)
    _set_admin_cookie(response)
    _clear_failures(request, ADMIN_COOKIE_NAME)


def identify(request: Request, response: Response) -> str:
    store = get_store()
    if config.auth_mode() == "local":
        user_id = store.ensure_user(LOCAL_USER_ID, is_local=True, display_name="本機使用者")
        _set_cookie(response, user_id)
        return user_id
    raw = request.cookies.get(COOKIE_NAME)
    legacy = request.cookies.get(LEGACY_COOKIE_NAME)
    user_id = raw if raw and store.user_exists(raw) else legacy if legacy and store.user_exists(legacy) else None
    if user_id:
        if user_id == legacy and not raw:
            _set_cookie(response, user_id)
        return user_id
    raise HTTPException(status_code=401, detail=UNAUTHORIZED_DETAIL)


def optional_identity(request: Request) -> str | None:
    if config.auth_mode() == "local":
        store = get_store()
        store.ensure_user(LOCAL_USER_ID, is_local=True, display_name="本機使用者")
        return LOCAL_USER_ID if store.user_exists(LOCAL_USER_ID) else None
    raw = request.cookies.get(COOKIE_NAME) or request.cookies.get(LEGACY_COOKIE_NAME)
    return raw if raw and get_store().user_exists(raw) else None


def admin_identity(request: Request) -> bool:
    value = request.cookies.get(ADMIN_COOKIE_NAME)
    if not value:
        return False
    with _failure_lock:
        return value in _admin_sessions


def require_user(request: Request, response: Response) -> str:
    return identify(request, response)


def require_admin(request: Request) -> None:
    if not admin_identity(request):
        if optional_identity(request) is None:
            raise HTTPException(status_code=401, detail=UNAUTHORIZED_DETAIL)
        raise HTTPException(status_code=403, detail="需要管理者授權")


def logout(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")
    response.delete_cookie(LEGACY_COOKIE_NAME, path="/")


def admin_logout(request: Request, response: Response) -> None:
    value = request.cookies.get(ADMIN_COOKIE_NAME)
    if value:
        with _failure_lock:
            _admin_sessions.discard(value)
    response.delete_cookie(ADMIN_COOKIE_NAME, path="/")
