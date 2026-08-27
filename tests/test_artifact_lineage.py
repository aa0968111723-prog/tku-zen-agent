"""產出版本管理：檔名穩定、歷史保留、最近產出只顯示最終版。

背景：一次網宣任務曾經留下「原始檔 / _2.md / _2_2.md」三份，
使用者分不出哪一份是最終版。現在的設計：
  · 使用者看到的檔名**永遠相同**（不再有 _2、_2_2 或任何後綴）
  · 舊版移到 outputs/<日期>/.versions/ 底下保留（lineage）
  · 資料庫版本號遞增、parent_id 串起版本鏈
  · 「最近產出」每個檔名只顯示最新一版
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config
from app.tools.base import unique_path


@pytest.fixture()
def client(tmp_db, tmp_output_dir, monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "local")
    monkeypatch.setattr(config, "APP_ACCESS_TOKEN", "")
    from app.main import app

    with TestClient(app) as c:
        yield c


# ── 檔名 ─────────────────────────────────────────────────────

def test_repeat_writes_keep_stable_filename(tmp_output_dir):
    """同名重寫時，使用者看到的檔名不變；舊版進 .versions/。"""
    d = tmp_output_dir
    first = unique_path(d, "115-1-期初茶會-網宣草稿.md")
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_text("v1", encoding="utf-8")
    second = unique_path(d, "115-1-期初茶會-網宣草稿.md")
    second.parent.mkdir(parents=True, exist_ok=True)
    second.write_text("v2", encoding="utf-8")

    assert first.name == "115-1-期初茶會-網宣草稿.md"
    assert second.name == "115-1-期初茶會-網宣草稿.md"
    assert ".versions" in str(second.parent), "新版寫到版本槽，正式位置的檔名不變"


def test_no_underscore_version_suffixes_ever(tmp_output_dir):
    """任何一次寫檔都不可以產生 _2、_2_2 這種檔名。"""
    d = tmp_output_dir
    for _ in range(4):
        p = unique_path(d, "期初茶會.md")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    produced = [p.name for p in d.rglob("期初茶會*.md")]
    assert produced, "應該有寫出檔案"
    for name in produced:
        assert name == "期初茶會.md", f"出現版本污染檔名：{name}"


# ── 資料庫 lineage ───────────────────────────────────────────

def test_same_filename_increments_version_and_links_parent(tmp_db):
    uid = tmp_db.ensure_user()
    v1 = tmp_db.record_artifact(user_id=uid, filename="網宣草稿.md", local_path="/x/1.md")
    v2 = tmp_db.record_artifact(user_id=uid, filename="網宣草稿.md", local_path="/x/2.md")
    assert (v1.version, v2.version) == (1, 2)
    assert v2.parent_id == v1.id, "新版要連回上一版（lineage）"


def test_recent_artifacts_show_only_latest_version(tmp_db):
    uid = tmp_db.ensure_user()
    tmp_db.record_artifact(user_id=uid, filename="網宣草稿.md", local_path="/x/1.md")
    tmp_db.record_artifact(user_id=uid, filename="網宣草稿.md", local_path="/x/2.md")
    latest = tmp_db.record_artifact(user_id=uid, filename="網宣草稿.md", local_path="/x/3.md")

    visible = tmp_db.list_artifacts(uid)
    assert len(visible) == 1
    assert visible[0].id == latest.id and visible[0].version == 3

    history = tmp_db.list_artifacts(uid, include_history=True)
    assert len(history) == 3, "舊版仍保留在 lineage"


def test_lineage_walkable_via_parent_ids(tmp_db):
    uid = tmp_db.ensure_user()
    tmp_db.record_artifact(user_id=uid, filename="企劃.md", local_path="/x/1.md")
    tmp_db.record_artifact(user_id=uid, filename="企劃.md", local_path="/x/2.md")
    latest = tmp_db.record_artifact(user_id=uid, filename="企劃.md", local_path="/x/3.md")

    chain = []
    cur = latest
    while cur:
        chain.append(cur.version)
        cur = tmp_db.get_artifact(cur.parent_id, uid) if cur.parent_id else None
    assert chain == [3, 2, 1]


def test_different_filenames_are_independent(tmp_db):
    uid = tmp_db.ensure_user()
    a = tmp_db.record_artifact(user_id=uid, filename="期初茶會.md", local_path="/x/1.md")
    b = tmp_db.record_artifact(user_id=uid, filename="期初茶會.xlsx", local_path="/x/1.xlsx")
    assert a.version == 1 and b.version == 1
    assert len(tmp_db.list_artifacts(uid)) == 2


# ── API ──────────────────────────────────────────────────────

def test_api_lists_only_latest_versions(client, tmp_db):
    uid = "u_local"
    tmp_db.ensure_user(uid, is_local=True)
    tmp_db.record_artifact(user_id=uid, filename="網宣.md", local_path="/x/1.md")
    tmp_db.record_artifact(user_id=uid, filename="網宣.md", local_path="/x/2.md")

    arts = client.get("/api/artifacts").json()["artifacts"]
    versions = [a["version"] for a in arts if a["filename"] == "網宣.md"]
    assert versions == [2], "最近產出只顯示最終版本"


def test_artifact_download_checks_ownership(tmp_db, tmp_output_dir, monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "token")
    monkeypatch.setattr(config, "APP_ACCESS_TOKEN", "tok")
    from app.main import app

    with (
        TestClient(app, base_url="https://testserver") as a,
        TestClient(app, base_url="https://testserver") as b,
    ):
        a.post("/api/auth", json={"token": "tok"})
        b.post("/api/auth", json={"token": "tok"})
        a.post("/api/session", json={})
        uid_rows = tmp_db._conn.execute("SELECT id FROM users ORDER BY created_at").fetchall()
        owner = uid_rows[0]["id"]
        art = tmp_db.record_artifact(user_id=owner, filename="秘密.md", local_path=str(tmp_output_dir / "秘密.md"))
        assert b.get(f"/api/download?artifact_id={art.id}").status_code == 404


# ── 「還沒驗證完就說完成」的措辭 ─────────────────────────────

def test_tool_result_does_not_claim_completion_before_verification():
    from pathlib import Path

    from app.tools.base import Artifact

    msg = Artifact(filename="網宣.md", local_path=Path("/x/1.md")).to_result()["message"]
    assert "尚待檢查" in msg
    assert not msg.startswith("已完成")
