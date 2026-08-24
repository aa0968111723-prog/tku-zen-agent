"""Instagram 草稿優先的安全性：任何路徑都不可能自動發佈。

覆蓋三層：
  1. 工具層 —— 模型手上根本沒有能發佈的工具
  2. 產出層 —— 網宣工具只寫本機草稿檔
  3. 設定層 —— 憑證不外流、預設草稿模式
（端點層的權限鏈在 tests/test_security_hardening.py。）
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from app import config, tools
from app.services import roles
from app.tools import social

ROOT = Path(__file__).resolve().parent.parent


# ── 工具層 ───────────────────────────────────────────────────

def test_no_publishing_tool_is_registered():
    """工具註冊表裡不可以有任何會對外發佈的工具。"""
    banned = re.compile(r"(publish|post_to|send_message|reply_comment|upload_to_ig)", re.IGNORECASE)
    for name in tools.all_names():
        assert not banned.search(name), f"註冊了可能對外發佈的工具：{name}"


def test_publish_actions_are_forbidden_for_agents():
    """roles 層也把發佈列為代理不得自主執行的動作。"""
    for action in ("instagram.publish", "instagram.comments.reply", "instagram.messages.send"):
        assert roles.is_forbidden_autonomous(action)


def test_social_tools_only_write_local_drafts():
    """每個 create_social_* 都只走本機 markdown 產出。"""
    for name in tools.all_names():
        if not name.startswith("create_social") and name != "create_reels_script":
            continue
        fn = tools._REGISTRY[name][0]
        src = inspect.getsource(fn)
        assert "_write_markdown" in src, f"{name} 沒有走草稿產出路徑"


def test_social_module_makes_no_network_calls():
    src = (ROOT / "app" / "tools" / "social.py").read_text(encoding="utf-8")
    for banned in ("httpx.", "requests.", "urllib.request", "graph.facebook.com", "instagram.com/api"):
        assert banned not in src, f"社群工具不該有網路呼叫：{banned}"


def test_draft_output_carries_external_warning(tmp_output_dir, clean_term):
    result = social.create_social_post(filename="測試貼文", content="測試內容")
    text = Path(result["local_path"]).read_text(encoding="utf-8")
    assert social.EXTERNAL_WARNING in text
    assert result["filename"].endswith(".md"), "草稿只產 Markdown，不會直接發佈"


# ── 設定層 ───────────────────────────────────────────────────

def test_publish_disabled_by_default():
    """未設定 EXTERNAL_PUBLISH_ENABLED 時，發佈總開關必須是關的。"""
    assert config.EXTERNAL_PUBLISH_ENABLED is False


def test_instagram_credentials_never_reach_the_model():
    """憑證不可以出現在任何工具 schema 或 system prompt 組裝路徑。"""
    schema_blob = str(tools.SCHEMAS)
    for banned in ("INSTAGRAM_ACCESS_TOKEN", "access_token", "INSTAGRAM_BUSINESS_ACCOUNT_ID"):
        assert banned not in schema_blob

    prompt_src = (ROOT / "app" / "orchestrator" / "prompt.py").read_text(encoding="utf-8")
    assert "INSTAGRAM" not in prompt_src


def test_status_endpoint_exposes_no_credentials(tmp_db, tmp_output_dir, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(config, "AUTH_MODE", "local")
    monkeypatch.setattr(config, "APP_ACCESS_TOKEN", "")
    monkeypatch.setattr(config, "INSTAGRAM_ACCESS_TOKEN", "ig-secret-token")
    monkeypatch.setattr(config, "INSTAGRAM_BUSINESS_ACCOUNT_ID", "12345")
    from app.main import app

    with TestClient(app) as c:
        body = c.get("/api/instagram/status").text
        assert "ig-secret-token" not in body
        assert "12345" not in body
        data = c.get("/api/instagram/status").json()
        # 有憑證但沒開發佈開關 → 仍然是草稿模式
        assert data["mode"] == "draft"
        assert data["publish_enabled"] is False


def test_connected_plus_flag_still_needs_permissions(tmp_db, tmp_output_dir, monkeypatch):
    """就算憑證齊、開關開，寫入端點仍要走完整權限鏈，永遠不會 200。"""
    from fastapi.testclient import TestClient

    monkeypatch.setattr(config, "AUTH_MODE", "token")
    monkeypatch.setattr(config, "APP_ACCESS_TOKEN", "tok")
    monkeypatch.setattr(config, "ADMIN_ACCESS_TOKEN", "adm")
    monkeypatch.setattr(config, "INSTAGRAM_ACCESS_TOKEN", "ig-secret")
    monkeypatch.setattr(config, "INSTAGRAM_BUSINESS_ACCOUNT_ID", "999")
    monkeypatch.setattr(config, "EXTERNAL_PUBLISH_ENABLED", True)
    from app.main import app

    with TestClient(app, base_url="https://testserver") as c:
        c.post("/api/auth", json={"token": "tok"})
        assert c.post("/api/instagram/publish", json={"confirm": True}).status_code == 403
        c.post("/api/admin/auth", json={"token": "adm"})
        assert c.post("/api/instagram/publish", json={}).status_code == 428
        assert c.post("/api/instagram/publish", json={"confirm": True}).status_code == 501


# ── 前端 ─────────────────────────────────────────────────────

def test_frontend_shows_draft_banner_and_never_calls_publish():
    js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    assert "目前為草稿模式——不會自動發布任何內容" in html
    for banned in ("/api/instagram/publish", "/api/instagram/messages/send",
                   "/api/instagram/comments/reply"):
        assert banned not in js, f"前端不該有自動發佈路徑：{banned}"


def test_publicity_prompt_says_draft_only():
    js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "僅草稿，不要發布" in js
