"""Acceptance coverage for the architecture/security and social phase."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from app import config, retrieval, skills, tools, verification
from app.services import auth


@pytest.fixture()
def secured_client(tmp_db, tmp_output_dir, monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "token")
    monkeypatch.setattr(config, "APP_ACCESS_TOKEN", "app-code")
    monkeypatch.setattr(config, "ADMIN_ACCESS_TOKEN", "admin-code")
    monkeypatch.setattr(config, "ACCESS_CODE_COOKIE_MAX_AGE", 2592000)
    monkeypatch.setattr(config, "ADMIN_COOKIE_MAX_AGE", 43200)
    auth._failures.clear()
    auth._admin_sessions.clear()
    from app.main import app

    with TestClient(app, base_url="https://testserver") as client:
        yield client


def test_anonymous_business_endpoints_are_401(secured_client):
    checks = [
        ("/api/chat", "post", {"message": "hi"}),
        ("/api/session", "post", {}),
        ("/api/sessions", "get", None),
        ("/api/artifacts", "get", None),
        ("/api/download?artifact_id=x", "get", None),
        ("/api/visual/generate", "post", {"prompt": "幫我做一張社團招生視覺稿"}),
        ("/api/term", "get", None),
        ("/api/health", "get", None),
    ]
    for path, method, body in checks:
        response = getattr(secured_client, method)(path, json=body) if body is not None else getattr(secured_client, method)(path)
        assert response.status_code == 401, (path, response.text)
        assert response.json()["detail"] == auth.UNAUTHORIZED_DETAIL


def test_login_does_not_return_identity_and_preserves_identity(secured_client):
    first = secured_client.post("/api/auth", json={"token": "app-code"})
    assert first.status_code == 200
    assert first.json() == {"ok": True}
    user_id = secured_client.cookies.get(auth.COOKIE_NAME)
    second = secured_client.post("/api/auth", json={"token": "app-code"})
    assert second.status_code == 200
    assert secured_client.cookies.get(auth.COOKIE_NAME) == user_id
    assert secured_client.get("/api/auth").json()["authenticated"] is True


def test_logout_and_admin_boundary(secured_client):
    secured_client.post("/api/auth", json={"token": "app-code"})
    assert secured_client.post("/api/reindex").status_code == 403
    assert secured_client.post("/api/term", json={"fields": {}}).status_code == 403
    assert secured_client.get("/api/admin/health").status_code == 403
    assert secured_client.post("/api/instagram/publish", json={"confirm": True}).status_code == 403

    assert secured_client.post("/api/admin/auth", json={"token": "admin-code"}).status_code == 200
    assert secured_client.get("/api/admin/health").status_code == 200
    assert secured_client.post("/api/admin/logout").status_code == 200
    assert secured_client.get("/api/admin/health").status_code == 403

    assert secured_client.post("/api/auth/logout").status_code == 200
    assert secured_client.get("/api/health").status_code == 401


def test_auth_rate_limit(secured_client):
    for _ in range(5):
        assert secured_client.post("/api/auth", json={"token": "wrong"}).status_code == 401
    limited = secured_client.post("/api/auth", json={"token": "wrong"})
    assert limited.status_code == 429
    assert limited.json()["detail"] == auth.RATE_LIMIT_DETAIL
    assert limited.headers["retry-after"]


def test_instagram_status_is_draft_until_connected(secured_client):
    secured_client.post("/api/auth", json={"token": "app-code"})
    data = secured_client.get("/api/instagram/status").json()
    assert data["connected"] is False
    assert data["mode"] == "draft"
    assert data["publish_enabled"] is False


def test_deployment_without_token_fails_fast():
    env = os.environ.copy()
    env.pop("APP_ACCESS_TOKEN", None)
    env["AUTH_MODE"] = ""
    env["PORT"] = "8999"
    result = subprocess.run(
        [sys.executable, "-m", "app"],
        cwd=str(__import__("pathlib").Path(__file__).resolve().parent.parent),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    # 容器平台會不斷重啟，訊息若只進 stderr 很容易被 BackOff 洗掉，
    # 所以 stdout 也要有一份，而且要講清楚該設哪個變數。
    assert "APP_ACCESS_TOKEN" in result.stdout
    assert "APP_ACCESS_TOKEN" in result.stderr


def test_secret_env_values_tolerate_pasted_quotes(monkeypatch):
    monkeypatch.setenv("APP_ACCESS_TOKEN", '"  code-with-quotes  "')
    assert config._secret("APP_ACCESS_TOKEN") == "code-with-quotes"
    monkeypatch.setenv("APP_ACCESS_TOKEN", "'code'")
    assert config._secret("APP_ACCESS_TOKEN") == "code"
    monkeypatch.setenv("APP_ACCESS_TOKEN", "pl41n-c0de")
    assert config._secret("APP_ACCESS_TOKEN") == "pl41n-c0de"
    monkeypatch.delenv("APP_ACCESS_TOKEN")
    assert config._secret("APP_ACCESS_TOKEN") == ""


def test_social_routes_and_tool_permissions():
    assert skills.route("研究其他學校的禪心社社群策略").skill.name == "social_research"
    assert skills.route("幫我做一篇 IG 貼文").skill.name == "social_publicity"
    assert len(tools.schemas_for([])) == 0
    names = set(tools.all_names())
    assert {"search_social_references", "create_social_post", "create_social_video_prompt"} <= names


def test_social_create_writes_downloadable_artifact(secured_client, tmp_output_dir, tmp_db):
    from app.services.context import RequestContext, use

    secured_client.post("/api/auth", json={"token": "app-code"})
    user_id = secured_client.cookies.get(auth.COOKIE_NAME)
    assert user_id
    session_id = tmp_db.create_session(user_id)
    with use(RequestContext(user_id=user_id, session_id=session_id)):
        result = tools.dispatch(
            "create_social_post",
            {"filename": "post.md", "content": "淡江自己的社群草稿"},
        )
    assert result["ok"] is True
    assert result["filename"].endswith(".md")
    response = secured_client.get(f"/api/download?artifact_id={result['artifact_id']}")
    assert response.status_code == 200


def test_external_reference_is_opt_in(index):
    normal = index.search("外校公開參考 北科", k=20)
    assert all(getattr(getattr(chunk, "meta", None), "source_type", "") != "external_reference" for _, chunk in normal)
    social = index.search("外校公開參考 北科", k=20, include_external=True)
    assert any(getattr(getattr(chunk, "meta", None), "source_type", "") == "external_reference" for _, chunk in social)


def test_social_copy_verification_blocks_copy_date_and_external_name(tmp_path):
    path = tmp_path / "social.md"
    path.write_text("北科禪心社這是一段外校公開參考的長句內容，請勿直接複製。2099/1/1", encoding="utf-8")
    report = verification.verify(
        path,
        rules=["verify_social_copy"],
        external_references=["北科禪心社這是一段外校公開參考的長句內容，請勿直接複製。"],
        external_names={"北科禪心社"},
    )
    assert not report.ok
    assert any(issue.rule == "verify_social_copy" for issue in report.errors)
