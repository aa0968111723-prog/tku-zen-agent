"""Contracts for safe local-to-InsForge core data synchronization."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import config, main, retrieval
from app.services import auth, visual_assets
from app.services.insforge_adapters import InsForgeAdapters, InsForgeUnavailable
from app.services.insforge_data_sync import InsForgeDataSyncAdapter


class FakeDatabase:
    def __init__(self, fail_once_table: str = "") -> None:
        self.calls: list[tuple[str,list[dict]]] = []
        self.fail_once_table = fail_once_table

    def upsert(self, table, rows, *, on_conflict="id"):
        body = rows if isinstance(rows,list) else [rows]
        self.calls.append((table,body))
        if table == self.fail_once_table:
            self.fail_once_table = ""
            raise InsForgeUnavailable("temporary remote failure")
        return body


class FakeStorage:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def upload_bytes(self,bucket,key,content,mime_type):
        self.calls.append({"bucket":bucket,"key":key,"content":content,"mime_type":mime_type})
        return {"bucket":bucket,"key":key,"size":len(content)}


@pytest.fixture()
def sync_env(tmp_db,tmp_output_dir,tmp_path,monkeypatch):
    asset_dir = tmp_path / "visual-assets"
    export_dir = tmp_path / "visual-exports"
    knowledge_dir = tmp_path / "knowledge"
    (knowledge_dir / "劇本").mkdir(parents=True)
    (knowledge_dir / "雲端文件").mkdir()
    (knowledge_dir / "社群").mkdir()
    (knowledge_dir / "語料").mkdir()
    (knowledge_dir / "00_社團知識庫.md").write_text(
        "# 社團知識庫\n\n## 期初茶會\n\n淡江大學領袖禪學社的期初茶會包含報到、交流、活動介紹與團體合照，所有日期仍應依當期資料確認。",
        encoding="utf-8",
    )
    monkeypatch.setattr(config,"VISUAL_ASSET_DIR",asset_dir)
    monkeypatch.setattr(config,"VISUAL_EXPORT_DIR",export_dir)
    monkeypatch.setattr(config,"KNOWLEDGE_DIR",knowledge_dir)
    monkeypatch.setattr(config,"PLAYBOOK_DIR",knowledge_dir / "劇本")
    monkeypatch.setattr(config,"DRIVE_DOCS_DIR",knowledge_dir / "雲端文件")
    monkeypatch.setattr(config,"EXTERNAL_REFERENCE_DIR",knowledge_dir / "社群")
    monkeypatch.setattr(config,"CORPUS_DIR",knowledge_dir / "語料")
    monkeypatch.setattr(config,"ENABLE_LINE_CORPUS",False)
    monkeypatch.setattr(config,"INSFORGE_OWNER_ID","remote-owner-1")
    monkeypatch.setattr(config,"INSFORGE_TRUSTED",True)
    monkeypatch.setattr(config,"INSFORGE_ALLOW_ARTIFACT_FILE_SYNC",False)
    monkeypatch.setattr(config,"INSFORGE_ALLOW_KNOWLEDGE_SYNC",True)
    monkeypatch.setattr(config,"INSFORGE_ALLOW_CONVERSATION_KNOWLEDGE_SYNC",False)
    if visual_assets._visual_store is not None:
        visual_assets._visual_store.close()
        visual_assets._visual_store = None
    retrieval._index = None
    user_id = tmp_db.ensure_user("u_sync",is_local=True)
    project_id = tmp_db.create_project(user_id,"招生計畫","promotion")
    artifact_path = tmp_output_dir / "招生文案.md"
    artifact_path.write_text("淡江大學領袖禪學社招生文案",encoding="utf-8")
    artifact = tmp_db.record_artifact(
        user_id=user_id,project_id=project_id,filename=artifact_path.name,
        local_path=str(artifact_path),kind="document",meta={"safe":"yes","api_key":"must-not-sync"},
    )
    activity = tmp_db.create_activity(user_id,"期初茶會",project_id=project_id,activity_type="tea_party")
    task = tmp_db.create_activity_task(user_id,activity["id"],"準備報到桌")
    source_id = tmp_db.record_research_sources(project_id,[{
        "title":"淡江活動頁","url":"https://example.test/tku","summary":"茶會資料",
        "school":"淡江大學","verification":"verified",
    }])[0]
    yield {
        "store":tmp_db,"user_id":user_id,"project_id":project_id,"artifact":artifact,
        "activity":activity,"task":task,"source_id":source_id,
    }
    if visual_assets._visual_store is not None:
        visual_assets._visual_store.close()
        visual_assets._visual_store = None
    retrieval._index = None


def service(sync_env,database=None,storage=None):
    db = database or FakeDatabase()
    files = storage or FakeStorage()
    adapters = InsForgeAdapters(db,files,SimpleNamespace(),SimpleNamespace())
    return InsForgeDataSyncAdapter(sync_env["store"],visual_assets.get_visual_store(),adapters),db,files


def test_preview_lists_safe_data_and_never_exposes_local_paths_or_private_runtime_state(sync_env):
    current,_,_ = service(sync_env)
    preview = current.preview(
        sync_env["user_id"],groups={"projects","artifacts","activities","research_sources","knowledge"},
        project_id=sync_env["project_id"],
    )
    assert preview["counts"]["project"] == 1
    assert preview["counts"]["artifact"] == 1
    assert preview["counts"]["activity"] == 1
    assert preview["counts"]["activity_task"] == 1
    assert preview["counts"]["research_source"] == 1
    assert preview["counts"]["knowledge_document"] == 1
    assert preview["counts"]["knowledge_chunk"] >= 1
    assert preview["contains_absolute_paths"] is False
    assert {"messages","working_memory","retrieval_cache","credentials"}.issubset(preview["excluded"])
    assert "local_path" not in json.dumps(preview)


def test_core_sync_is_idempotent_sanitizes_metadata_and_does_not_upload_artifact_by_default(sync_env):
    current,db,files = service(sync_env)
    groups = {"projects","artifacts","activities","research_sources"}
    first = current.run(sync_env["user_id"],groups=groups,project_id=sync_env["project_id"],idempotency_key="core-sync-001")
    call_count = len(db.calls)
    second = current.run(sync_env["user_id"],groups=groups,project_id=sync_env["project_id"],idempotency_key="core-sync-001")
    assert first["status"] == "completed"
    assert second["id"] == first["id"]
    assert len(db.calls) == call_count
    assert files.calls == []
    artifact_payload = next(rows[0] for table,rows in db.calls if table == "artifacts")
    assert artifact_payload["storage_path"] == ""
    assert "api_key" not in artifact_payload["metadata"]
    assert "local_path" not in artifact_payload
    assert current.get_run(first["id"],"different-user") is None


def test_partial_failure_retry_only_replays_failed_resource(sync_env):
    db = FakeDatabase(fail_once_table="artifacts")
    current,_,_ = service(sync_env,database=db)
    groups = {"projects","artifacts","activities","research_sources"}
    first = current.run(sync_env["user_id"],groups=groups,project_id=sync_env["project_id"],idempotency_key="core-sync-partial")
    assert first["status"] == "partial"
    failed = {(item["resource_type"],item["resource_id"]) for item in first["items"] if item["status"] == "failed"}
    assert failed == {("artifact",sync_env["artifact"].id)}
    before = len(db.calls)
    retried = current.retry(first["id"],sync_env["user_id"])
    assert retried and retried["status"] == "completed"
    assert len(db.calls) == before + 1
    assert db.calls[-1][0] == "artifacts"


def test_artifact_file_upload_requires_explicit_opt_in(sync_env,monkeypatch):
    monkeypatch.setattr(config,"INSFORGE_ALLOW_ARTIFACT_FILE_SYNC",True)
    current,db,files = service(sync_env)
    result = current.run(sync_env["user_id"],groups={"projects","artifacts"},project_id=sync_env["project_id"],idempotency_key="core-sync-files")
    assert result["status"] == "completed"
    assert len(files.calls) == 1
    assert files.calls[0]["content"] == "淡江大學領袖禪學社招生文案".encode()
    artifact_payload = next(rows[0] for table,rows in db.calls if table == "artifacts")
    assert artifact_payload["storage_path"].startswith("owners/remote-owner-1/artifacts/")


def test_backend_sync_api_dry_run_uses_authenticated_server_truth(sync_env,monkeypatch):
    monkeypatch.setattr(config,"AUTH_MODE","local")
    monkeypatch.setattr(auth,"LOCAL_USER_ID",sync_env["user_id"])
    with TestClient(main.app) as client:
        response = client.post("/api/backend-sync/run",json={
            "resource_types":["projects","artifacts","activities","research_sources","knowledge"],
            "project_id":sync_env["project_id"],
            "idempotency_key":"api-dry-run-001",
            "dry_run":True,
        })
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["dry_run"] is True
    assert body["counts"]["project"] == 1
    assert body["counts"]["knowledge_document"] == 1
    assert body["contains_absolute_paths"] is False
    assert "messages" in body["excluded"]


def test_backend_sync_api_rejects_unauthenticated_access(sync_env,monkeypatch):
    monkeypatch.setattr(config,"AUTH_MODE","token")
    monkeypatch.setattr(config,"APP_ACCESS_TOKEN","member-token")
    with TestClient(main.app,base_url="https://testserver") as client:
        response = client.post("/api/backend-sync/run",json={
            "resource_types":["projects"],
            "idempotency_key":"api-auth-check-001",
            "dry_run":True,
        })
    assert response.status_code == 401
