"""Phase 1 media-library contracts; all PASS assertions exercise real local code."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, main
from app.services import visual_assets
from tests.test_visual_asset_database import picture, upload


@pytest.fixture()
def visual_env(tmp_db,monkeypatch):
    root = Path(tempfile.mkdtemp(prefix="tku_visual_phase1_"))
    monkeypatch.setattr(config,"VISUAL_ASSET_DIR",root / "assets")
    monkeypatch.setattr(config,"VISUAL_EXPORT_DIR",root / "exports")
    monkeypatch.setattr(config,"VISUAL_MAX_FILE_BYTES",2_000_000)
    monkeypatch.setattr(config,"VISUAL_MAX_BATCH",30)
    monkeypatch.setattr(config,"VISUAL_SEARCH_LIMIT",60)
    if visual_assets._visual_store is not None:
        visual_assets._visual_store.close()
        visual_assets._visual_store = None
    yield root
    if visual_assets._visual_store is not None:
        visual_assets._visual_store.close()
        visual_assets._visual_store = None
    shutil.rmtree(root,ignore_errors=True)


def folder_payload(entries: list[dict], files: list[tuple], *, key: str, resync: bool = False, project_id: str = ""):
    data = {
        "manifest": json.dumps({"total_count": len(entries), "items": entries}, ensure_ascii=False),
        "idempotency_key": key, "root_name": "社團相簿", "school": "淡江大學",
        "club": "領袖禪學社", "auto_analyze": "false", "resync": str(resync).lower(),
    }
    if project_id:
        data["project_id"] = project_id
    return {"files": [("files", item) for item in files], "data": data}


def test_migration_ledger_and_document_media_are_persistent(visual_env):
    with TestClient(main.app) as client:
        response = client.post(
            "/api/visual-assets/upload",
            files=[("files",("活動說明.txt","淡江大學 領袖禪學社 期初茶會 2026-09-18".encode(),"text/plain"))],
            data={"auto_analyze":"false","school":"淡江大學","club":"領袖禪學社"},
        )
        assert response.status_code == 201
        item = response.json()["items"][0]
        assert item["asset_type"] == "document"
        assert "期初茶會" in item["ocr_text"]
        store = visual_assets.get_visual_store()
        versions = {row[0] for row in store._conn.execute("SELECT version FROM visual_schema_migrations")}
        assert versions == {"0001_initial_visual_schema","0002_phase1_media_import","0003_insforge_visual_backend","0004_insforge_core_data_sync","0005_data_organization_depth","0006_audit_job_and_project_scope","0007_import_project_and_cancel"}
        assert store._conn.execute("SELECT length(storage_path)>0 FROM visual_assets WHERE id=?",(item["asset_id"],)).fetchone()[0] == 1
        assert store._conn.execute("SELECT typeof(storage_path) FROM visual_assets WHERE id=?",(item["asset_id"],)).fetchone()[0] == "text"


def test_direct_upload_idempotency_and_video_external_blocker(visual_env):
    with TestClient(main.app) as client:
        kwargs = {"files":[("files",("same.png",picture(),"image/png"))],"data":{"auto_analyze":"false","idempotency_key":"direct-phase1-idempotent"}}
        first = client.post("/api/visual-assets/upload",**kwargs)
        second = client.post("/api/visual-assets/upload",**kwargs)
        assert first.status_code == second.status_code == 201
        assert first.json()["items"][0]["asset_id"] == second.json()["items"][0]["asset_id"]
        assert second.json()["created"] == 0 and second.json()["skipped"] == 1

        video = client.post(
            "/api/visual-assets/upload",
            files=[("files",("clip.mp4",b"phase1-video-original","video/mp4"))],
            data={"auto_analyze":"false"},
        ).json()["items"][0]
        assert video["asset_type"] == "video" and video["suitability"]["video"] is True
        blocked = client.post(f"/api/visual-assets/{video['asset_id']}/analyze")
        assert blocked.status_code == 503
        assert blocked.json()["detail"]["code"] == "BLOCKED_BY_EXTERNAL_DEPENDENCY"


def test_direct_upload_catalogues_audio_and_camera_raw_without_decoding(visual_env):
    with TestClient(main.app) as client:
        response = client.post(
            "/api/visual-assets/upload",
            files=[
                ("files", ("field-recording.mp3", b"audio-original", "application/octet-stream")),
                ("files", ("camera-shot.cr2", b"raw-original", "application/octet-stream")),
            ],
            data={"auto_analyze": "false"},
        )
        assert response.status_code == 201, response.text
        by_name = {item["original_filename"]: item for item in response.json()["items"]}
        assert by_name["field-recording.mp3"]["asset_type"] == "audio"
        assert by_name["camera-shot.cr2"]["asset_type"] == "image"
        assert by_name["field-recording.mp3"]["source_metadata"]["inspection_mode"] == "generic"
        assert by_name["camera-shot.cr2"]["source_metadata"]["inspection_mode"] == "generic"


def test_folder_import_partial_failure_resume_idempotency_and_resync(visual_env):
    entries = [
        {"client_key":"a","relative_path":"茶會/第一張.png","last_modified":"2026-09-18"},
        {"client_key":"b","relative_path":"茶會/第二張.png","last_modified":"2026-09-18"},
    ]
    key = "folder-phase1-resume"
    with TestClient(main.app) as client:
        first = client.post("/api/visual-assets/import", **folder_payload(
            entries,
            [("第一張.png",picture(color=(20,90,180)),"image/png"),("第二張.png",b"broken","image/png")],
            key=key,
        ))
        assert first.status_code == 201
        body = first.json()
        assert body["partial_failure"] is True
        assert body["items"][0]["relative_path"] == "茶會/第一張.png"
        assert body["errors"][0]["code"] == "invalid_image"
        import_id = body["import"]["id"]
        assert body["import"]["completed_count"] == 1 and body["import"]["failed_count"] == 1

        resumed = client.post("/api/visual-assets/import", **folder_payload(
            [entries[1]], [("第二張.png",picture(color=(80,160,60)),"image/png")], key=key,
        ))
        assert resumed.status_code == 201
        state = client.get(f"/api/visual-imports/{import_id}").json()
        assert state["completed_count"] == 2 and state["failed_count"] == 0 and state["status"] == "verified"

        repeated = client.post("/api/visual-assets/import", **folder_payload(
            [entries[1]], [("第二張.png",picture(color=(80,160,60)),"image/png")], key=key,
        ))
        assert len(repeated.json()["skipped"]) == 1

        changed = client.post("/api/visual-assets/import", **folder_payload(
            [entries[1]], [("第二張.png",picture(color=(170,40,50)),"image/png")], key=key, resync=True,
        ))
        replacement = changed.json()["items"][0]
        assert replacement["supersedes_asset_id"]
        assert replacement["relative_path"] == "茶會/第二張.png"
        previous_id = replacement["supersedes_asset_id"]
        auto_version = client.post("/api/visual-assets/import", **folder_payload(
            [entries[1]], [("第二張.png",picture(color=(10,10,10)),"image/png")], key=key,
        ))
        assert auto_version.status_code == 201
        newest = auto_version.json()["items"][0]
        assert newest["asset_id"] != previous_id
        assert newest["supersedes_asset_id"]
        assert client.get(f"/api/visual-assets/{previous_id}").status_code == 200


def test_folder_import_writes_validated_project_id(visual_env, tmp_db):
    user_id = tmp_db.ensure_user("u_local", is_local=True)
    project_id = tmp_db.create_project(user_id, "招生影片", "promotion")
    entries = [{"client_key": "p1", "relative_path": "校園/入口.png"}]
    with TestClient(main.app) as client:
        denied = client.post(
            "/api/visual-assets/import",
            **folder_payload(entries, [("入口.png", picture(), "image/png")], key="folder-project-denied", project_id="p_not_owned"),
        )
        assert denied.status_code == 422
        ok = client.post(
            "/api/visual-assets/import",
            **folder_payload(entries, [("入口.png", picture(), "image/png")], key="folder-project-ok", project_id=project_id),
        )
        assert ok.status_code == 201
        item = ok.json()["items"][0]
        assert item["project_id"] == project_id


def test_exact_phase1_routes_review_confirm_retry_and_external_marker(visual_env,monkeypatch):
    from app.services import visual_analysis
    from app.services.fal import FalError

    async def unavailable(_url):
        raise FalError("offline",code="vision_network")

    monkeypatch.setattr(visual_analysis.fal,"analyze_visual_asset",unavailable)
    with TestClient(main.app) as client:
        asset = upload(client,picture(),auto=False).json()["items"][0]
        queue = client.get("/api/visual-assets/review-queue")
        assert queue.status_code == 200 and queue.json()["total"] == 1
        blocked = client.post(f"/api/visual-assets/{asset['asset_id']}/analyze")
        assert blocked.status_code == 503
        assert blocked.json()["detail"]["code"] == "BLOCKED_BY_EXTERNAL_DEPENDENCY"
        detail = client.get(f"/api/visual-assets/{asset['asset_id']}").json()
        assert detail["analysis_job"]["error_code"] == "BLOCKED_BY_EXTERNAL_DEPENDENCY"
        retry = client.post(f"/api/visual-assets/{asset['asset_id']}/retry")
        assert retry.status_code == 503
        confirmed = client.post(f"/api/visual-assets/{asset['asset_id']}/confirm",json={"reason":"人工確認本機資料"})
        assert confirmed.status_code == 200 and confirmed.json()["review_status"] == "verified"


def test_ocr_empty_result_is_preserved_without_fabricated_text(visual_env,monkeypatch):
    from app.services import visual_analysis

    async def empty_ocr(_url):
        return {"summary":"無可讀文字","ocr_text":"","dates":[],"scenes":[],"event":{},"clubs":[],"people":{"count":0,"descriptions":[]},"objects":[],"logos":[],"quality_notes":["OCR 無結果"],"is_poster":False}

    async def no_people(_target,_refs):
        return []

    monkeypatch.setattr(visual_analysis.fal,"analyze_visual_asset",empty_ocr)
    monkeypatch.setattr(visual_analysis.fal,"compare_confirmed_people",no_people)
    with TestClient(main.app) as client:
        asset = upload(client,picture(),auto=False).json()["items"][0]
        analyzed = client.post(f"/api/visual-assets/{asset['asset_id']}/analyze")
        assert analyzed.status_code == 200
        assert analyzed.json()["ocr_text"] == ""
        assert analyzed.json()["analysis"]["quality_notes"] == ["OCR 無結果"]


def test_school_hard_boundary_and_different_user_isolation(visual_env,monkeypatch):
    monkeypatch.setattr(config,"AUTH_MODE","token")
    monkeypatch.setattr(config,"APP_ACCESS_TOKEN","phase1-secret")
    with TestClient(main.app,base_url="https://testserver") as first, TestClient(main.app,base_url="https://testserver") as second:
        assert first.post("/api/auth",json={"token":"phase1-secret"}).status_code == 200
        assert second.post("/api/auth",json={"token":"phase1-secret"}).status_code == 200
        tku = upload(first,picture(color=(10,90,180)),name="淡江期初茶會.png",school="淡江大學").json()["items"][0]
        nccu = upload(first,picture(color=(170,30,70)),name="政大期初茶會.png",school="政治大學",club="領袖社").json()["items"][0]
        assert first.post("/api/entities/confirm",json={"asset_id":tku["asset_id"],"entity_type":"person","action":"correct","candidate_label":"私人測試人物","values":{"school":"淡江大學"}}).status_code == 200
        found = first.get("/api/visual-assets/search",params={"q":"找淡江茶會照片"}).json()
        assert [item["asset_id"] for item in found["items"]] == [tku["asset_id"]]
        assert nccu["asset_id"] not in {item["asset_id"] for item in found["items"]}
        assert second.get(f"/api/visual-assets/{tku['asset_id']}").status_code == 404
        assert second.get("/api/visual-assets/search").json()["total"] == 0
        assert second.get("/api/people?q=私人測試人物").json()["total"] == 0
        assert second.get("/api/clubs?q=領袖禪學社").json()["total"] == 0


def test_insforge_backend_status_is_explicitly_blocked_when_unconfigured(visual_env,monkeypatch):
    from app.services.insforge_adapters import reset_insforge_adapters
    monkeypatch.setattr(config,"INSFORGE_BASE_URL","")
    monkeypatch.setattr(config,"INSFORGE_SERVICE_KEY","")
    monkeypatch.setattr(config,"INSFORGE_ANON_KEY","")
    reset_insforge_adapters()
    with TestClient(main.app) as client:
        response = client.get("/api/visual-backend/status")
    assert response.status_code == 200
    payload = response.json()["status"]
    assert payload["available"] is False
    assert payload["code"] == "BLOCKED_BY_EXTERNAL_DEPENDENCY"


def test_insforge_sync_is_durable_and_does_not_send_private_asset_without_explicit_consent(visual_env,monkeypatch):
    from app.services import visual_assets
    from app.services.insforge_adapters import reset_insforge_adapters
    monkeypatch.setattr(config,"INSFORGE_BASE_URL","")
    monkeypatch.setattr(config,"INSFORGE_SERVICE_KEY","")
    monkeypatch.setattr(config,"INSFORGE_ANON_KEY","")
    monkeypatch.setattr(config,"INSFORGE_OWNER_ID","")
    reset_insforge_adapters()
    with TestClient(main.app) as client:
        response = upload(client,picture(),auto=False)
        asset_id = response.json()["items"][0]["asset_id"]
        sync = client.post("/api/visual-sync/run",json={"asset_ids":[asset_id],"idempotency_key":"sync-test-001"})
        assert sync.status_code == 202
        body = sync.json()
        assert body["status"] == "blocked"
        assert body["items"][0]["error_code"] == "BLOCKED_BY_EXTERNAL_DEPENDENCY"
        again = client.post("/api/visual-sync/run",json={"asset_ids":[asset_id],"idempotency_key":"sync-test-001"})
        assert again.json()["id"] == body["id"]
        rollback = client.post(f"/api/visual-sync/{body['id']}/rollback")
        assert rollback.status_code == 200
        assert rollback.json()["rollback_status"] == "complete"
    store = visual_assets.get_visual_store()
    assert store._rows("SELECT status FROM visual_asset_backend_refs WHERE asset_id=?",(asset_id,))[0]["status"] == "rolled_back_local"


def test_local_asset_file_cannot_escape_visual_dir(visual_env):
    with TestClient(main.app) as client:
        item = upload(client, picture(), auto=False).json()["items"][0]
        asset_id = item["asset_id"]
        original = client.get(item["original_url"])
        assert original.status_code == 200
        store = visual_assets.get_visual_store()
        bait = visual_env / "secret.txt"
        bait.write_text("should-not-be-served", encoding="utf-8")
        store._conn.execute("UPDATE visual_assets SET storage_path=? WHERE id=?", (str(bait), asset_id))
        store._conn.commit()
        escaped = store.asset_file(asset_id, store._rows("SELECT user_id FROM visual_assets WHERE id=?", (asset_id,))[0]["user_id"], "original")
        assert escaped is None
        assert client.get(item["original_url"]).status_code == 404
        assert bait.read_text(encoding="utf-8") == "should-not-be-served"


def test_upload_records_project_id_when_provided(visual_env, tmp_db):
    project_id = tmp_db.create_project(tmp_db.ensure_user("u_local", is_local=True), "招生計畫", "promotion")
    with TestClient(main.app) as client:
        response = client.post(
            "/api/visual-assets/upload",
            files=[("files", ("tea.png", picture(), "image/png"))],
            data={"auto_analyze": "false", "school": "淡江大學", "club": "領袖禪學社", "project_id": project_id},
        )
        assert response.status_code == 201
        item = response.json()["items"][0]
        assert item["project_id"] == project_id
        store = visual_assets.get_visual_store()
        row = store._rows("SELECT project_id,owner_id FROM visual_assets WHERE id=?", (item["asset_id"],))[0]
        assert row["project_id"] == project_id
        assert row["owner_id"]


def test_process_restart_does_not_downgrade_verified_review_status(visual_env):
    with TestClient(main.app) as client:
        item = upload(client, picture(), auto=False).json()["items"][0]
        asset_id = item["asset_id"]
    store = visual_assets.get_visual_store()
    store.create_job(asset_id)
    with store._lock:
        store._conn.execute("UPDATE visual_assets SET review_status='verified' WHERE id=?", (asset_id,))
        store._conn.commit()
    store.close()
    visual_assets._visual_store = None
    reopened = visual_assets.get_visual_store()
    row = reopened._rows("SELECT review_status,processing_state FROM visual_assets WHERE id=?", (asset_id,))[0]
    assert row["review_status"] == "verified"
    assert row["processing_state"] == "failed"
    job = reopened._rows(
        "SELECT status,error_code FROM visual_analysis_jobs WHERE asset_id=? ORDER BY created_at DESC LIMIT 1",
        (asset_id,),
    )[0]
    assert job["status"] == "failed"
    assert job["error_code"] == "interrupted_process"


def test_analyze_does_not_read_escaped_storage_path(visual_env):
    with TestClient(main.app) as client:
        item = upload(client, picture(), auto=False).json()["items"][0]
        asset_id = item["asset_id"]
        store = visual_assets.get_visual_store()
        bait = visual_env / "secret-for-vision.txt"
        bait.write_text("do-not-send-to-vision", encoding="utf-8")
        store._conn.execute("UPDATE visual_assets SET storage_path=? WHERE id=?", (str(bait), asset_id))
        store._conn.commit()
        response = client.post(f"/api/visual-assets/{asset_id}/analyze")
        assert response.status_code == 404
        assert bait.read_text(encoding="utf-8") == "do-not-send-to-vision"


def test_visual_search_limit_is_rejected_above_cap(visual_env):
    with TestClient(main.app) as client:
        response = client.get("/api/visual-assets/search", params={"limit": 1000})
        assert response.status_code == 422


def test_rerun_does_not_downgrade_verified_or_ignored_observations(visual_env):
    with TestClient(main.app) as client:
        item = upload(client, picture(), auto=False).json()["items"][0]
        asset_id = item["asset_id"]
    store = visual_assets.get_visual_store()
    verified_id = store.add_observation(
        asset_id, "scene", label="社課", status="probable",
        confidence=0.7, source="directory_taxonomy_v1",
        evidence={"path": "場景/上學期社課"},
    )
    with store._lock:
        store._conn.execute("UPDATE visual_observations SET status='verified' WHERE id=?", (verified_id,))
        store._conn.commit()
    store.add_observation(
        asset_id, "scene", label="社課", status="probable",
        confidence=0.4, source="directory_taxonomy_v1",
        evidence={"path": "場景/上學期社課"},
    )
    assert store._rows("SELECT status,confidence FROM visual_observations WHERE id=?", (verified_id,))[0]["status"] == "verified"
    ignored_id = store.add_observation(
        asset_id, "scene", label="其他", status="probable",
        confidence=0.5, source="directory_taxonomy_v1",
    )
    with store._lock:
        store._conn.execute("UPDATE visual_observations SET review_action='ignored' WHERE id=?", (ignored_id,))
        store._conn.commit()
    store.add_observation(
        asset_id, "scene", label="其他", status="probable",
        confidence=0.9, source="directory_taxonomy_v1",
    )
    row = store._rows("SELECT status,review_action FROM visual_observations WHERE id=?", (ignored_id,))[0]
    assert row["review_action"] == "ignored"
    assert store._rows("SELECT COUNT(*) AS n FROM visual_observations WHERE asset_id=? AND label='其他'", (asset_id,))[0]["n"] == 1
