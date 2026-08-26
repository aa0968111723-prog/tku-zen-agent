"""Deep data-organization contracts backed by real SQLite and image bytes."""

from __future__ import annotations

import shutil
import tempfile
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app import config, main
from app.services import auth, visual_assets
from app.services.data_organization import DataOrganizationService
from app.services.library_context import LibraryContextResolver
from app.services.visual_assets import VisualAssetStore, dumps, new_id, now
from tests.test_visual_asset_database import picture


@pytest.fixture()
def organization_env(tmp_db, monkeypatch):
    root = Path(tempfile.mkdtemp(prefix="tku_organization_"))
    monkeypatch.setattr(config, "VISUAL_ASSET_DIR", root / "assets")
    monkeypatch.setattr(config, "VISUAL_EXPORT_DIR", root / "exports")
    monkeypatch.setattr(config, "OUTPUT_DIR", root / "outputs")
    monkeypatch.setattr(config, "VISUAL_MAX_FILE_BYTES", 2_000_000)
    store = VisualAssetStore(tmp_db.path)
    monkeypatch.setattr(visual_assets, "_visual_store", store)
    service = DataOrganizationService(tmp_db, store)
    monkeypatch.setattr(service, "_knowledge_documents", lambda: [])
    yield tmp_db, store, service
    visual_assets._visual_store = None
    store.close()
    shutil.rmtree(root, ignore_errors=True)


def _observation(store: VisualAssetStore, asset_id: str, kind: str, entity_id: str, label: str, status: str) -> None:
    stamp = now()
    store._conn.execute(
        "INSERT INTO visual_observations(id,asset_id,entity_type,entity_id,label,status,confidence,source,evidence,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (new_id("obs"), asset_id, kind, entity_id, label, status, 0.82, "test_evidence", dumps({"field": "test"}), stamp, stamp),
    )
    store._conn.commit()


def _person(store: VisualAssetStore, person_id: str, school_id: str, club_id: str, name: str, status: str) -> None:
    stamp = now()
    store._conn.execute(
        "INSERT INTO visual_people(id,school_id,club_id,name,status,source,confidence,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (person_id, school_id, club_id, name, status, dumps({"type": "user_input"}), 0.82, stamp, stamp),
    )
    store._conn.commit()


def test_inventory_and_mapping_are_real_idempotent_non_destructive_and_owner_scoped(organization_env, monkeypatch):
    sessions, store, service = organization_env
    user_a = sessions.ensure_user("owner_a", is_local=True)
    user_b = sessions.ensure_user("owner_b")
    asset_a = store.create_asset(user_id=user_a, filename="淡江茶會.png", mime_type="image/png", content=picture(), school="淡江大學", club="領袖禪學社")
    asset_b = store.create_asset(user_id=user_b, filename="政大茶會.png", mime_type="image/png", content=picture(color=(180, 60, 30)), school="政治大學", club="領袖禪學社")
    person_a, person_b = "person_tku", "person_nccu"
    _person(store, person_a, asset_a["school_id"], asset_a["club_id"], "同名人物", "verified")
    _person(store, person_b, asset_b["school_id"], asset_b["club_id"], "同名人物", "verified")
    _observation(store, asset_a["asset_id"], "person", person_a, "同名人物", "verified")
    _observation(store, asset_b["asset_id"], "person", person_b, "同名人物", "verified")
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    loose_path = config.OUTPUT_DIR / "既有素材.png"
    loose_bytes = picture(color=(40, 190, 90))
    loose_path.write_bytes(loose_bytes)

    inventory = service.inventory(user_a, persist=True)
    assert inventory["statistics"]["images"] == 2
    assert inventory["statistics"]["schools"] == 1
    assert inventory["statistics"]["clubs"] == 1
    assert inventory["statistics"]["people"] == 1
    original_path = Path(store._conn.execute("SELECT storage_path FROM visual_assets WHERE id=?", (asset_a["asset_id"],)).fetchone()[0])
    original_bytes = original_path.read_bytes()

    first = service.run(user_a, idempotency_key="organization-owner-a")
    repeated = service.run(user_a, idempotency_key="organization-owner-a")
    assert first["id"] == repeated["id"] and first["status"] == "completed"
    assert original_path.read_bytes() == original_bytes
    imported = store._conn.execute("SELECT storage_path FROM visual_assets WHERE user_id=? AND source='existing_data_import'", (user_a,)).fetchone()
    assert imported and Path(imported[0]).read_bytes() == loose_bytes and loose_path.read_bytes() == loose_bytes
    assert store._conn.execute("SELECT COUNT(*) FROM data_lineage WHERE owner_id=?", (user_a,)).fetchone()[0] >= 4
    mapped = store._rows("SELECT id,name,type FROM entities WHERE owner_id=?", (user_a,))
    assert any(row["name"] == "同名人物" and row["type"] == "person" for row in mapped)
    assert all("政治" not in row["name"] for row in mapped)
    second_owner = service.run(user_b, idempotency_key="organization-owner-b")
    assert second_owner["status"] == "completed"
    mapped_a = store._conn.execute("SELECT id FROM entities WHERE owner_id=? AND type='person' AND name='同名人物'", (user_a,)).fetchone()[0]
    mapped_b = store._conn.execute("SELECT id FROM entities WHERE owner_id=? AND type='person' AND name='同名人物'", (user_b,)).fetchone()[0]
    assert mapped_a != mapped_b

    real_map = service._map_resource
    failed_once = {"value": False}

    def fail_asset_once(owner_id, resource_type, resource_id, row):
        if resource_type == "visual_asset" and not failed_once["value"]:
            failed_once["value"] = True
            raise RuntimeError("synthetic mapping boundary failure")
        return real_map(owner_id, resource_type, resource_id, row)

    monkeypatch.setattr(service, "_map_resource", fail_asset_once)
    partial = service.run(user_a, idempotency_key="organization-partial")
    assert partial["status"] == "partial" and partial["failed_count"] == 1
    monkeypatch.setattr(service, "_map_resource", real_map)
    resumed = service.retry(partial["id"], user_a)
    assert resumed and resumed["status"] == "completed" and resumed["resumed_from"] == partial["id"]


def test_context_resolver_is_acl_first_and_does_not_promote_probable_people(organization_env):
    sessions, store, _service = organization_env
    user_a = sessions.ensure_user("context_a")
    user_b = sessions.ensure_user("context_b")
    project_a = sessions.create_project(user_a, "淡江招生影片", "recruitment")
    project_b = sessions.create_project(user_b, "政大招生影片", "recruitment")
    asset_a = store.create_asset(user_id=user_a, filename="淡江校園.png", mime_type="image/png", content=picture(size=(1600, 900), color=(170, 200, 210)), school="淡江大學", club="領袖禪學社")
    asset_b = store.create_asset(user_id=user_b, filename="政大校園.png", mime_type="image/png", content=picture(size=(1600, 900), color=(170, 200, 210)), school="政治大學", club="領袖禪學社")
    store._conn.execute("UPDATE visual_assets SET quality_score=90,brightness_score=80 WHERE id IN (?,?)", (asset_a["asset_id"], asset_b["asset_id"]))
    _observation(store, asset_a["asset_id"], "scene", "", "校園", "verified")
    _observation(store, asset_b["asset_id"], "scene", "", "校園", "verified")
    probable_person = "person_probable"
    _person(store, probable_person, asset_a["school_id"], asset_a["club_id"], "薰儀", "probable")
    _observation(store, asset_a["asset_id"], "person", probable_person, "可能是薰儀", "probable")

    resolver = LibraryContextResolver(sessions, store)
    with pytest.raises(ValueError, match="不屬於"):
        resolver.resolve(user_a, query="招生影片", project_id=project_b)
    result = resolver.search(user_a, query="幫我找適合淡江招生影片第二幕的照片", project_id=project_a)
    assert result["acl_first"] is True
    assert result["context"]["scene"]["position"] == 2
    assert result["context"]["output_requirements"]["ratio"] == "16:9"
    assert [item["asset_id"] for item in result["items"]] == [asset_a["asset_id"]]
    people_results, total, _ = store.search(user_a, query="薰儀", person="薰儀")
    assert people_results == [] and total == 0
    store._conn.execute("UPDATE visual_people SET status='verified' WHERE id=?", (probable_person,))
    store._conn.execute("UPDATE visual_observations SET status='verified' WHERE asset_id=? AND entity_id=?", (asset_a["asset_id"], probable_person))
    store._conn.commit()
    people_results, total, _ = store.search(user_a, query="薰儀", person="薰儀")
    assert total == 1 and people_results[0]["asset_id"] == asset_a["asset_id"]


def test_data_organization_api_requires_auth(organization_env, monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "token")
    monkeypatch.setattr(config, "APP_ACCESS_TOKEN", "organization-secret")
    with TestClient(main.app, base_url="https://testserver") as client:
        assert client.get("/api/data-organization/inventory").status_code == 401
        assert client.post("/api/library/context-search", json={"query": "淡江校園"}).status_code == 401


def test_data_organization_and_context_api_persist_server_truth(organization_env, monkeypatch):
    sessions, _store, _service = organization_env
    user_id = sessions.ensure_user("api_owner", is_local=True)
    project_id = sessions.create_project(user_id, "淡江招生影片", "recruitment")
    monkeypatch.setattr(config, "AUTH_MODE", "local")
    monkeypatch.setattr(auth, "LOCAL_USER_ID", user_id)
    with TestClient(main.app) as client:
        inventory = client.post("/api/data-organization/inventory", json={"project_id": project_id})
        assert inventory.status_code == 201
        organized = client.post("/api/data-organization/organize", json={"project_id": project_id, "idempotency_key": "api-organize-depth"})
        assert organized.status_code == 202 and organized.json()["status"] == "completed"
        node = client.post("/api/library/context-nodes", json={
            "project_id": project_id, "node_type": "scene", "title": "進入淡江校園",
            "position": 2, "requirements": {"scene": "校園", "ratio": "16:9"},
        })
        assert node.status_code == 201 and node.json()["position"] == 2
        searched = client.post("/api/library/context-search", json={
            "query": "找淡江招生影片第二幕照片", "project_id": project_id, "page": 1, "limit": 10,
        })
        assert searched.status_code == 200 and searched.json()["acl_first"] is True
        persisted = client.get(f"/api/data-organization/runs/{organized.json()['id']}")
        assert persisted.status_code == 200 and persisted.json()["id"] == organized.json()["id"]


def test_existing_xlsx_and_apps_script_are_imported_as_searchable_documents(organization_env):
    sessions, store, service = organization_env
    user_id = sessions.ensure_user("document_owner", is_local=True)
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    workbook.active.append(["活動", "日期"])
    workbook.active.append(["期初茶會", "2026-09-18"])
    stream = io.BytesIO()
    workbook.save(stream)
    (config.OUTPUT_DIR / "活動表.xlsx").write_bytes(stream.getvalue())
    (config.OUTPUT_DIR / "報名通知.gs").write_text("function notifyTeaParty() { return '淡江茶會'; }", encoding="utf-8")

    result = service.run(user_id, idempotency_key="document-format-import")
    assert result["status"] == "completed", result.get("error_log")
    rows = store._rows("SELECT original_filename,mime_type,ocr_text FROM visual_assets WHERE user_id=? ORDER BY original_filename", (user_id,))
    assert {row["original_filename"] for row in rows} == {"活動表.xlsx", "報名通知.gs"}
    assert any("期初茶會" in row["ocr_text"] for row in rows)
    assert any("淡江茶會" in row["ocr_text"] for row in rows)
