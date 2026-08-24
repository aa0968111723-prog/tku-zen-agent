"""安全強化驗收：權限拆分、fail-closed、標頭、CSRF、限流、稽核。

原則：全部打真的 API 端點，不是只測前端顯示。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config
from app.services import ratelimit


@pytest.fixture()
def client(tmp_db, tmp_output_dir, monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "local")
    monkeypatch.setattr(config, "APP_ACCESS_TOKEN", "")
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def token_client(tmp_db, tmp_output_dir, monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "token")
    monkeypatch.setattr(config, "APP_ACCESS_TOKEN", "s3cret-token")
    monkeypatch.setattr(config, "ADMIN_ACCESS_TOKEN", "adm1n-token")
    from app.main import app

    with TestClient(app, base_url="https://testserver") as c:
        yield c


def _login(c, admin=False):
    assert c.post("/api/auth", json={"token": "s3cret-token"}).status_code == 200
    if admin:
        assert c.post("/api/admin/auth", json={"token": "adm1n-token"}).status_code == 200


# ── openapi / docs ───────────────────────────────────────────

def test_openapi_requires_admin_in_token_mode(token_client):
    assert token_client.get("/openapi.json").status_code == 401
    _login(token_client)
    assert token_client.get("/openapi.json").status_code == 403
    _login(token_client, admin=True)
    assert token_client.get("/openapi.json").status_code == 200


def test_openapi_describes_security_schemes(token_client):
    _login(token_client, admin=True)
    schema = token_client.get("/openapi.json").json()
    schemes = schema["components"]["securitySchemes"]
    assert "sessionCookie" in schemes and "adminCookie" in schemes
    assert schema["security"] == [{"sessionCookie": []}]
    # 管理端點要標明需要兩把鑰匙
    reindex = schema["paths"]["/api/reindex"]["post"]
    assert {"sessionCookie": [], "adminCookie": []} in reindex["security"]


def test_docs_pages_are_disabled(client, token_client):
    for c in (client, token_client):
        assert c.get("/docs").status_code == 404
        assert c.get("/redoc").status_code == 404


def test_openapi_open_in_local_mode(client):
    assert client.get("/openapi.json").status_code == 200


# ── 安全標頭 ─────────────────────────────────────────────────

def test_security_headers_present(client):
    resp = client.get("/")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in resp.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]
    assert "Permissions-Policy" in resp.headers
    # 本機模式（http）不該逼瀏覽器記住 HSTS
    assert "Strict-Transport-Security" not in resp.headers


def test_hsts_in_token_mode(token_client):
    resp = token_client.get("/")
    assert "max-age=31536000" in resp.headers["Strict-Transport-Security"]


# ── CSRF Origin 檢查 ─────────────────────────────────────────

def test_cross_origin_post_is_blocked(client):
    resp = client.post(
        "/api/session", json={}, headers={"Origin": "https://evil.example.com"}
    )
    assert resp.status_code == 403
    assert "來源網域不符" in resp.json()["detail"]


def test_same_origin_post_is_allowed(client):
    resp = client.post(
        "/api/session", json={}, headers={"Origin": "http://testserver"}
    )
    assert resp.status_code == 200


# ── 登入 token 驗證 ──────────────────────────────────────────

@pytest.mark.parametrize("payload", [{"token": ""}, {"token": None}, {}, {"token": "   "}])
def test_login_rejects_empty_and_null_tokens(token_client, payload):
    assert token_client.post("/api/auth", json=payload).status_code == 401


def test_login_error_never_echoes_token(token_client):
    resp = token_client.post("/api/auth", json={"token": "super-unique-guess-12345"})
    assert resp.status_code == 401
    assert "super-unique-guess-12345" not in resp.text
    # 正確授權碼也絕不出現在任何回應內容
    ok = token_client.post("/api/auth", json={"token": "s3cret-token"})
    assert "s3cret-token" not in ok.text


def test_frontend_html_contains_no_tokens(token_client):
    html = token_client.get("/").text
    assert "s3cret-token" not in html
    assert "adm1n-token" not in html


# ── 權限拆分（不可只有 isAdmin）─────────────────────────────

def test_auth_status_exposes_permission_split(token_client):
    _login(token_client, admin=True)
    perms = token_client.get("/api/auth").json()["permissions"]
    assert perms["can_view"] is True
    assert perms["can_manage"] is True
    # 預設（未設 EXTERNAL_PUBLISH_ENABLED）連管理者也沒有發佈權
    assert perms["can_approve"] is False
    assert perms["can_spend"] is False


def test_admin_gets_publish_perms_only_with_flag(token_client, monkeypatch):
    monkeypatch.setattr(config, "EXTERNAL_PUBLISH_ENABLED", True)
    _login(token_client, admin=True)
    perms = token_client.get("/api/auth").json()["permissions"]
    assert perms["can_approve"] is True and perms["can_spend"] is True


# ── 管理端點 fail closed ─────────────────────────────────────

ADMIN_POSTS = ["/api/reindex", "/api/term"]


@pytest.mark.parametrize("path", ADMIN_POSTS)
def test_admin_endpoints_fail_closed_anonymous(token_client, path):
    body = {"fields": {}} if path == "/api/term" else {}
    assert token_client.post(path, json=body).status_code == 401


@pytest.mark.parametrize("path", ADMIN_POSTS)
def test_admin_endpoints_fail_closed_for_plain_user(token_client, path):
    _login(token_client)
    body = {"fields": {}} if path == "/api/term" else {}
    assert token_client.post(path, json=body).status_code == 403


def test_admin_health_and_audit_require_admin(token_client):
    assert token_client.get("/api/admin/health").status_code == 401
    assert token_client.get("/api/admin/audit").status_code == 401
    _login(token_client)
    assert token_client.get("/api/admin/health").status_code == 403
    assert token_client.get("/api/admin/audit").status_code == 403
    _login(token_client, admin=True)
    assert token_client.get("/api/admin/health").status_code == 200
    assert token_client.get("/api/admin/audit").status_code == 200


# ── Instagram：草稿優先、fail closed、需要明確確認 ────────────

IG_ENDPOINTS = [
    "/api/instagram/publish",
    "/api/instagram/comments/reply",
    "/api/instagram/messages/send",
]


@pytest.mark.parametrize("path", IG_ENDPOINTS)
def test_instagram_write_requires_login(token_client, path):
    assert token_client.post(path, json={"confirm": True}).status_code == 401


@pytest.mark.parametrize("path", IG_ENDPOINTS)
def test_instagram_write_denied_for_plain_user(token_client, path):
    _login(token_client)
    assert token_client.post(path, json={"confirm": True}).status_code == 403


@pytest.mark.parametrize("path", IG_ENDPOINTS)
def test_instagram_write_denied_for_admin_without_publish_flag(token_client, path):
    """canApprove / canSpend 預設沒開：連管理者也只能停在草稿模式。"""
    _login(token_client, admin=True)
    resp = token_client.post(path, json={"confirm": True})
    assert resp.status_code == 403
    assert "草稿模式" in resp.json()["detail"]


@pytest.mark.parametrize("path", IG_ENDPOINTS)
def test_instagram_write_requires_explicit_confirmation(token_client, monkeypatch, path):
    monkeypatch.setattr(config, "EXTERNAL_PUBLISH_ENABLED", True)
    _login(token_client, admin=True)
    resp = token_client.post(path, json={})
    assert resp.status_code == 428
    assert "確認" in resp.json()["detail"]
    resp = token_client.post(path, json={"confirm": False})
    assert resp.status_code == 428


@pytest.mark.parametrize("path", IG_ENDPOINTS)
def test_instagram_write_never_succeeds_without_api(token_client, monkeypatch, path):
    """所有守門都過了，功能本身仍未啟用 —— 絕不可能自動發佈。"""
    monkeypatch.setattr(config, "EXTERNAL_PUBLISH_ENABLED", True)
    _login(token_client, admin=True)
    resp = token_client.post(path, json={"confirm": True})
    assert resp.status_code == 501
    assert resp.status_code != 200


# ── 限流 ─────────────────────────────────────────────────────

def test_chat_rate_limit(client, monkeypatch):
    monkeypatch.setattr(config, "CHAT_RATE_LIMIT", 2)
    monkeypatch.setattr(config, "CHAT_RATE_WINDOW", 60)
    ratelimit.reset()
    sid = client.post("/api/session", json={}).json()["session_id"]

    from unittest.mock import patch

    async def _noop(*a, **k):
        return
        yield  # pragma: no cover

    with patch("app.orchestrator.run_turn", _noop):
        for _ in range(2):
            resp = client.post("/api/chat", json={"session_id": sid, "message": "hi"})
            assert resp.status_code != 429
        resp = client.post("/api/chat", json={"session_id": sid, "message": "hi"})
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers
        assert "頻繁" in resp.json()["detail"]


# ── 稽核日誌 ─────────────────────────────────────────────────

def test_audit_log_records_security_events(token_client, tmp_db):
    token_client.post("/api/auth", json={"token": "wrong"})
    _login(token_client, admin=True)
    token_client.post("/api/term", json={"fields": {"club_fee": "300"}})
    token_client.post("/api/instagram/publish", json={"confirm": True})
    rows = tmp_db.list_audit(80)
    failed_logins = [e for e in rows if e["action"] == "auth.login" and not e["ok"]]
    ok_logins = [e for e in rows if e["action"] == "auth.login" and e["ok"]]
    assert failed_logins and ok_logins
    assert any(e["action"] == "auth.admin_login" and e["ok"] for e in rows)
    assert any(e["action"] == "instagram.publish" for e in rows)


def test_audit_log_never_contains_tokens(token_client, tmp_db):
    token_client.post("/api/auth", json={"token": "wrong-token-value"})
    _login(token_client, admin=True)
    token_client.post("/api/instagram/publish", json={"confirm": True})
    dump = str(tmp_db.list_audit(80))
    assert "wrong-token-value" not in dump
    assert "s3cret-token" not in dump
    assert "adm1n-token" not in dump
