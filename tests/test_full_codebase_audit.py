"""Full-audit regression: ACL, project scope, jobs, persistence, concurrent edits."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, main
from app.services import audit, security, visual_assets, visual_analysis
from app.services.fal import FalError
from app.services.session_store import get_store
from tests.test_visual_asset_database import picture, upload
from tests.test_visual_asset_phase1 import folder_payload


@pytest.fixture()
def visual_env(tmp_db, monkeypatch):
    root = Path(tempfile.mkdtemp(prefix="tku_visual_audit_"))
    monkeypatch.setattr(config, "VISUAL_ASSET_DIR", root / "assets")
    monkeypatch.setattr(config, "VISUAL_EXPORT_DIR", root / "exports")
    monkeypatch.setattr(config, "VISUAL_MAX_FILE_BYTES", 2_000_000)
    monkeypatch.setattr(config, "VISUAL_MAX_BATCH", 30)
    monkeypatch.setattr(config, "VISUAL_SEARCH_LIMIT", 60)
    if visual_assets._visual_store is not None:
        visual_assets._visual_store.close()
        visual_assets._visual_store = None
    yield root
    if visual_assets._visual_store is not None:
        visual_assets._visual_store.close()
        visual_assets._visual_store = None
    shutil.rmtree(root, ignore_errors=True)


def test_folder_import_keeps_project_id_on_every_item(visual_env, tmp_db):
    user_id = tmp_db.ensure_user("u_local", is_local=True)
    project_id = tmp_db.create_project(user_id, "淡江招生影片", "recruitment")
    entries = [
        {"client_key": "a", "relative_path": "校園/一.png"},
        {"client_key": "b", "relative_path": "校園/二.png"},
    ]
    with TestClient(main.app) as client:
        response = client.post(
            "/api/visual-assets/import",
            **folder_payload(
                entries,
                [("一.png", picture(color=(10, 20, 30)), "image/png"), ("二.png", picture(color=(40, 50, 60)), "image/png")],
                key="folder-audit-project",
                project_id=project_id,
            ),
        )
    assert response.status_code == 201
    items = response.json()["items"]
    assert len(items) == 2
    assert {item["project_id"] for item in items} == {project_id}
    store = visual_assets.get_visual_store()
    rows = store._rows("SELECT project_id FROM visual_assets WHERE user_id=?", (user_id,))
    assert {row["project_id"] for row in rows} == {project_id}


def test_unscoped_import_is_not_guessed_and_enters_review(visual_env, tmp_db):
    user_id = tmp_db.ensure_user("u_local", is_local=True)
    tmp_db.create_project(user_id, "不該被猜測的專案", "promotion")
    entries = [{"client_key": "u1", "relative_path": "未分類/圖.png"}]
    with TestClient(main.app) as client:
        response = client.post(
            "/api/visual-assets/import",
            **folder_payload(entries, [("圖.png", picture(), "image/png")], key="folder-unscoped"),
        )
    assert response.status_code == 201
    item = response.json()["items"][0]
    assert item["project_id"] == ""
    store = visual_assets.get_visual_store()
    queued = store._rows("SELECT proposed_change FROM review_queue WHERE asset_id=?", (item["asset_id"],))
    assert queued
    assert "missing_project_id" in queued[0]["proposed_change"]


def test_tku_and_nccu_assets_do_not_cross_search(visual_env, tmp_db):
    tmp_db.ensure_user("u_local", is_local=True)
    with TestClient(main.app) as client:
        tku = upload(client, picture(color=(20, 90, 180)), name="tku.png", school="淡江大學", club="領袖禪學社")
        nccu = upload(client, picture(color=(180, 40, 40)), name="nccu.png", school="國立政治大學", club="政大禪學社")
        assert tku.status_code == nccu.status_code == 201
        tku_id = tku.json()["items"][0]["asset_id"]
        nccu_id = nccu.json()["items"][0]["asset_id"]
        tku_hits = client.get("/api/visual-assets/search", params={"q": "淡江大學 領袖禪學社", "school": "淡江大學"}).json()
        nccu_hits = client.get("/api/visual-assets/search", params={"q": "國立政治大學", "school": "國立政治大學"}).json()
    tku_ids = {item["asset_id"] for item in tku_hits["items"]}
    nccu_ids = {item["asset_id"] for item in nccu_hits["items"]}
    assert tku_id in tku_ids
    assert nccu_id not in tku_ids
    assert nccu_id in nccu_ids
    assert tku_id not in nccu_ids


def test_same_name_people_are_not_auto_merged(visual_env, tmp_db):
    user_id = tmp_db.ensure_user("u_local", is_local=True)
    store = visual_assets.get_visual_store()
    with TestClient(main.app) as client:
        first = upload(client, picture(color=(11, 22, 33)), name="tku-person.png", school="淡江大學").json()["items"][0]
        second = upload(client, picture(color=(44, 55, 66)), name="nccu-person.png", school="國立政治大學").json()["items"][0]
        a = store.confirm_entity(
            user_id=user_id, asset_id=first["asset_id"], entity_type="person", action="confirm",
            values={"name": "王小明", "school": "淡江大學"},
        )
        b = store.confirm_entity(
            user_id=user_id, asset_id=second["asset_id"], entity_type="person", action="confirm",
            values={"name": "王小明", "school": "國立政治大學"},
        )
    people = store._rows("SELECT id,school_id,name FROM visual_people WHERE name=?", ("王小明",))
    assert len(people) == 2
    assert len({row["school_id"] for row in people}) == 2
    ids = {obs["entity_id"] for obs in a["observations"] + b["observations"] if obs.get("entity_type") == "person" and obs.get("entity_id")}
    assert len(ids) == 2


def test_viewer_cannot_modify_shared_asset(visual_env, tmp_db):
    owner = tmp_db.ensure_user("u_local", is_local=True)
    viewer = tmp_db.ensure_user("u_viewer")
    with TestClient(main.app) as client:
        created = upload(client, picture(), name="shared.png").json()["items"][0]
        asset_id = created["asset_id"]
        patched = client.patch(f"/api/visual-assets/{asset_id}", json={"privacy": "shared"})
        assert patched.status_code == 200
    store = visual_assets.get_visual_store()
    assert store.visible_asset(asset_id, viewer)
    assert store.patch_asset(asset_id, viewer, {"privacy": "public"}) is None
    with pytest.raises(visual_assets.VisualAssetError):
        store.confirm_asset(asset_id, viewer, reason="viewer")


def test_reload_keeps_project_and_job_state(visual_env, tmp_db):
    user_id = tmp_db.ensure_user("u_local", is_local=True)
    project_id = tmp_db.create_project(user_id, "招生影片", "recruitment")
    with TestClient(main.app) as client:
        item = client.post(
            "/api/visual-assets/upload",
            files=[("files", ("keep.png", picture(), "image/png"))],
            data={"auto_analyze": "false", "project_id": project_id, "school": "淡江大學"},
        ).json()["items"][0]
        asset_id = item["asset_id"]
    store = visual_assets.get_visual_store()
    store.close()
    visual_assets._visual_store = None
    reopened = visual_assets.get_visual_store()
    row = reopened.get_asset(asset_id, user_id)
    assert row and row["project_id"] == project_id
    assert Path(reopened._rows("SELECT storage_path FROM visual_assets WHERE id=?", (asset_id,))[0]["storage_path"]).is_file()


def test_analysis_failure_does_not_overwrite_verified_review(visual_env, tmp_db, monkeypatch):
    user_id = tmp_db.ensure_user("u_local", is_local=True)
    async def boom(_url):
        raise FalError("offline", code="vision_network")

    monkeypatch.setattr(visual_analysis.fal, "analyze_visual_asset", boom)
    with TestClient(main.app) as client:
        item = upload(client, picture(), auto=False).json()["items"][0]
        asset_id = item["asset_id"]
        confirmed = client.post(f"/api/visual-assets/{asset_id}/confirm", json={"reason": "人工先確認"})
        assert confirmed.status_code == 200
        blocked = client.post(f"/api/visual-assets/{asset_id}/analyze")
        assert blocked.status_code == 503
        assert blocked.json()["detail"]["code"] == "BLOCKED_BY_EXTERNAL_DEPENDENCY"
        detail = client.get(f"/api/visual-assets/{asset_id}").json()
    assert detail["review_status"] == "verified"
    assert detail["processing_state"] == "failed"
    assert detail["analysis_job"]["status"] == "failed"


def test_same_path_replacement_reanalyzes_without_overwriting(visual_env, tmp_db):
    tmp_db.ensure_user("u_local", is_local=True)
    entries = [{"client_key": "same", "relative_path": "A/image.png"}]
    with TestClient(main.app) as client:
        first = client.post(
            "/api/visual-assets/import",
            **folder_payload(entries, [("image.png", picture(color=(10, 10, 10)), "image/png")], key="same-path-x"),
        )
        second = client.post(
            "/api/visual-assets/import",
            **folder_payload(entries, [("image.png", picture(color=(200, 10, 10)), "image/png")], key="same-path-x"),
        )
    assert first.status_code == second.status_code == 201
    old_id = first.json()["items"][0]["asset_id"]
    new_item = second.json()["items"][0]
    assert new_item["asset_id"] != old_id
    assert new_item["supersedes_asset_id"] == old_id
    store = visual_assets.get_visual_store()
    assert store.get_asset(old_id, "u_local")
    hashes = {
        row["id"]: row["sha256"]
        for row in store._rows("SELECT id,sha256 FROM visual_assets WHERE id IN (?,?)", (old_id, new_item["asset_id"]))
    }
    assert hashes[old_id] != hashes[new_item["asset_id"]]


def test_project_search_does_not_leak_other_project(visual_env, tmp_db):
    user_id = tmp_db.ensure_user("u_local", is_local=True)
    project_a = tmp_db.create_project(user_id, "淡江招生影片", "recruitment")
    project_b = tmp_db.create_project(user_id, "政大紀錄", "promotion")
    with TestClient(main.app) as client:
        a = client.post(
            "/api/visual-assets/upload",
            files=[("files", ("a.png", picture(color=(1, 2, 3)), "image/png"))],
            data={"auto_analyze": "false", "project_id": project_a, "school": "淡江大學"},
        ).json()["items"][0]
        b = client.post(
            "/api/visual-assets/upload",
            files=[("files", ("b.png", picture(color=(9, 8, 7)), "image/png"))],
            data={"auto_analyze": "false", "project_id": project_b, "school": "國立政治大學"},
        ).json()["items"][0]
        scoped = client.get("/api/visual-assets/search", params={"project_id": project_a}).json()
        denied = client.get("/api/visual-assets/search", params={"project_id": "p_not_owned"})
    ids = {item["asset_id"] for item in scoped["items"]}
    assert a["asset_id"] in ids
    assert b["asset_id"] not in ids
    assert denied.status_code == 422


def test_pagination_does_not_repeat_or_drop(visual_env, tmp_db):
    tmp_db.ensure_user("u_local", is_local=True)
    with TestClient(main.app) as client:
        ids = []
        for index in range(5):
            ids.append(
                upload(client, picture(color=(index * 20, 40, 80)), name=f"page-{index}.png").json()["items"][0]["asset_id"]
            )
        page1 = client.get("/api/visual-assets/search", params={"q": "page", "page": 1, "limit": 2}).json()
        page2 = client.get("/api/visual-assets/search", params={"q": "page", "page": 2, "limit": 2}).json()
        page3 = client.get("/api/visual-assets/search", params={"q": "page", "page": 3, "limit": 2}).json()
    seen = [item["asset_id"] for item in page1["items"] + page2["items"] + page3["items"]]
    assert len(seen) == len(set(seen))
    assert len(seen) == page1["total"] == page2["total"] == 5
    assert set(ids) == set(seen)


def test_concurrent_edit_rejects_stale_human_patch(visual_env, tmp_db):
    tmp_db.ensure_user("u_local", is_local=True)
    with TestClient(main.app) as client:
        item = upload(client, picture(), auto=False).json()["items"][0]
        asset_id = item["asset_id"]
        first = client.patch(f"/api/visual-assets/{asset_id}", json={"privacy": "shared", "if_match_updated_at": item["updated_at"]})
        assert first.status_code == 200
        stale = client.patch(
            f"/api/visual-assets/{asset_id}",
            json={"privacy": "public", "if_match_updated_at": item["updated_at"]},
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "stale_update"
        current = client.get(f"/api/visual-assets/{asset_id}").json()
        assert current["privacy"] == "shared"


def test_audit_typeerror_is_not_swallowed(tmp_db, monkeypatch):
    def boom(**kwargs):
        raise TypeError("unexpected keyword")

    monkeypatch.setattr(get_store(), "append_audit", boom)
    with pytest.raises(TypeError):
        security.audit("acl.denied", actor_user_id="u_local", success=False)
    with pytest.raises(TypeError):
        audit.write_audit(action="acl.denied", actor_user_id="u_local", ok=False)


def test_audit_masks_tokens(tmp_db):
    assert security.audit(
        "acl.denied",
        actor_user_id="u_local",
        detail={"token": "nvapi-ABCDEFGHIJKLMNOP", "reason": "owner_mismatch"},
        success=False,
    ) is True
    row = tmp_db.list_audit(limit=1)[0]
    assert "ABCDEFGHIJKLMNOP" not in row["detail"] or "…" in row["detail"]
    assert "owner_mismatch" in row["detail"]
