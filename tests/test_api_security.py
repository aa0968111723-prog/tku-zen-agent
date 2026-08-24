"""Phase 1：API 層的隔離與認證。

用 FastAPI TestClient 打真的端點，不是只測 store。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config
from app.services import auth


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
    from app.main import app

    # 部署模式的身分 cookie 帶 Secure，http 連線存不進去 —— 這是刻意的，
    # 所以測試要用 https base_url 才能反映真實部署行為。
    with TestClient(app, base_url="https://testserver") as c:
        yield c


# ── 本機模式 ─────────────────────────────────────────────────

def test_local_mode_needs_no_login(client):
    assert client.get("/api/health").status_code == 200
    assert client.post("/api/session", json={}).status_code == 200


def test_local_mode_reuses_one_user(client):
    a = client.post("/api/session", json={}).json()["session_id"]
    b = client.post("/api/session", json={"session_id": a}).json()["session_id"]
    assert a == b, "同一個 session_id 應該被沿用"


# ── 部署模式 ─────────────────────────────────────────────────

def test_token_mode_blocks_anonymous(token_client):
    assert token_client.get("/api/health").status_code == 401
    assert token_client.post("/api/session", json={}).status_code == 401


def test_token_mode_rejects_wrong_token(token_client):
    assert token_client.post("/api/auth", json={"token": "wrong"}).status_code == 401


def test_token_mode_allows_after_login(token_client):
    assert token_client.post("/api/auth", json={"token": "s3cret-token"}).status_code == 200
    assert token_client.get("/api/health").status_code == 200


def test_forged_cookie_is_rejected(token_client):
    token_client.cookies.set(auth.COOKIE_NAME, "u_i_made_this_up")
    assert token_client.get("/api/health").status_code == 401


def test_two_users_are_isolated(token_client, tmp_db, tmp_output_dir):
    """規格驗收：A 使用者無法讀/下載 B 使用者 artifact。"""
    from fastapi.testclient import TestClient

    from app.main import app

    with (
        TestClient(app, base_url="https://testserver") as a,
        TestClient(app, base_url="https://testserver") as b,
    ):
        a.post("/api/auth", json={"token": "s3cret-token"})
        b.post("/api/auth", json={"token": "s3cret-token"})

        a_user = a.cookies.get(auth.COOKIE_NAME)
        b_user = b.cookies.get(auth.COOKIE_NAME)
        assert a_user and b_user and a_user != b_user, "兩個瀏覽器要拿到不同身分"

        a_session = a.post("/api/session", json={}).json()["session_id"]

        # B 拿著 A 的 session_id 也讀不到 A 的東西：會被開一個新的
        b_session = b.post("/api/session", json={"session_id": a_session}).json()["session_id"]
        assert b_session != a_session

        # B 想清掉 A 的對話 → 404
        assert b.post("/api/reset", json={"session_id": a_session}).status_code == 404

        # A 的 artifact，B 下載不到
        art = tmp_db.record_artifact(
            user_id=a_user, filename="secret.xlsx", local_path=str(tmp_output_dir / "secret.xlsx")
        )
        (tmp_output_dir / "secret.xlsx").write_text("x", encoding="utf-8")
        assert a.get(f"/api/download?artifact_id={art.id}").status_code == 200
        assert b.get(f"/api/download?artifact_id={art.id}").status_code == 404


# ── 下載端點 ─────────────────────────────────────────────────

def test_download_requires_artifact_id_not_path(client, tmp_output_dir):
    """以前這個端點吃 ?path=，那在多人環境下是任意檔案下載。"""
    r = client.get("/api/download?path=" + str(tmp_output_dir / "anything.xlsx"))
    assert r.status_code == 422, "不該再接受 path 參數"


def test_download_rejects_unknown_artifact(client):
    assert client.get("/api/download?artifact_id=a_nope").status_code == 404


def test_download_rejects_artifact_outside_output_dir(client, tmp_db, tmp_path):
    """artifact 記錄被竄改指向系統檔案時，路徑檢查要擋下來。"""
    uid = client.post("/api/session", json={}).json() and auth.LOCAL_USER_ID
    outside = tmp_path / "passwd"
    outside.write_text("root:x:0:0", encoding="utf-8")
    art = tmp_db.record_artifact(user_id=uid, filename="passwd", local_path=str(outside))
    assert client.get(f"/api/download?artifact_id={art.id}").status_code == 404


def test_artifacts_listing_never_exposes_server_paths(client, tmp_db, tmp_output_dir):
    uid = auth.LOCAL_USER_ID
    f = tmp_output_dir / "a.docx"
    f.write_text("x", encoding="utf-8")
    tmp_db.ensure_user(uid, is_local=True)
    tmp_db.record_artifact(user_id=uid, filename="a.docx", local_path=str(f))
    body = client.get("/api/artifacts").text
    assert "a.docx" in body
    assert str(tmp_output_dir) not in body, "回應裡不該有伺服器絕對路徑"


# ── 輸入驗證 ─────────────────────────────────────────────────

def test_empty_message_is_rejected(client):
    assert client.post("/api/chat", json={"message": ""}).status_code == 422


def test_oversized_message_is_rejected(client):
    assert client.post("/api/chat", json={"message": "x" * 9000}).status_code == 422


def test_image_attachment_rejects_wrong_data_url(client):
    response = client.post(
        "/api/chat",
        json={
            "message": "請看圖片",
            "attachments": [{"name": "x.png", "media_type": "image/png", "data_url": "data:image/png;base64,not-base64"}],
        },
    )
    assert response.status_code == 422
    assert "圖片" in response.text


def test_image_attachment_rejects_more_than_two_images(client):
    image = {"name": "x.png", "media_type": "image/png", "data_url": "data:image/png;base64,AA=="}
    assert client.post("/api/chat", json={"message": "請看圖片", "attachments": [image, image, image]}).status_code == 422
