"""PR-01：audit log 與角色常數。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config
from app.services import audit, auth, security
from app.services.roles import Role, has_at_least, is_forbidden_autonomous
from app.services.session_store import get_store


def test_mask_secrets_redacts_nvapi_and_bearer():
    raw = "key=nvapi-ABCDEFGHIJKLMNOP and Bearer supersecrettokenvalue"
    masked = audit.mask_secrets(raw)
    assert "ABCDEFGHIJKLMNOP" not in masked or "…" in masked
    assert "supersecrettokenvalue" not in masked or "…" in masked


def test_role_ordering():
    assert has_at_least(Role.ADMIN, Role.USER)
    assert has_at_least(Role.EDITOR, Role.USER)
    assert not has_at_least(Role.USER, Role.ADMIN)
    assert is_forbidden_autonomous("instagram.publish")
    assert not is_forbidden_autonomous("chat.send")


@pytest.fixture()
def token_client(tmp_db, tmp_output_dir, monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "token")
    monkeypatch.setattr(config, "APP_ACCESS_TOKEN", "s3cret-token")
    monkeypatch.setattr(config, "ADMIN_ACCESS_TOKEN", "admin-s3cret")
    from app.main import app

    with TestClient(app, base_url="https://testserver") as c:
        yield c


def test_security_audit_matches_session_store_contract(tmp_db):
    assert security.audit(
        "acl.denied",
        actor_user_id="u_local",
        role="user",
        resource_type="visual_assets",
        resource_id="asset_1",
        success=False,
        detail={"reason": "owner_mismatch"},
    ) is True
    rows = tmp_db.list_audit(limit=5)
    assert rows
    row = rows[0]
    assert row["action"] == "acl.denied"
    assert row["ok"] is False
    assert "visual_assets/asset_1" in row["resource"]
    assert "owner_mismatch" in row["detail"]
    assert "nvapi" not in row["detail"]


def test_login_success_writes_audit(token_client):
    r = token_client.post("/api/auth", json={"token": "s3cret-token"})
    assert r.status_code == 200
    rows = get_store().list_audit(limit=20)
    actions = [row["action"] for row in rows]
    assert "auth.login" in actions
    # 不應把完整 token 寫進 detail
    joined = " ".join(row.get("detail") or "" for row in rows)
    assert "s3cret-token" not in joined


def test_login_failure_writes_audit(token_client):
    r = token_client.post("/api/auth", json={"token": "wrong"})
    assert r.status_code == 401
    rows = get_store().list_audit(limit=20)
    assert any(row["action"] == "auth.login" and not row["ok"] for row in rows)


def test_admin_reindex_audited(token_client, monkeypatch):
    token_client.post("/api/auth", json={"token": "s3cret-token"})
    token_client.post("/api/admin/auth", json={"token": "admin-s3cret"})

    def _fake_index(rebuild=False):
        class S:
            def stats(self):
                return {"chunks": 0}

        return S()

    monkeypatch.setattr("app.main.retrieval.get_index", _fake_index)
    r = token_client.post("/api/reindex")
    assert r.status_code == 200
    rows = get_store().list_audit(limit=30)
    assert any(row["action"] == "admin.reindex" and row["ok"] for row in rows)
