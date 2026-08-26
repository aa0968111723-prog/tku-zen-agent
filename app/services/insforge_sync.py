"""Resumable, idempotent local-to-InsForge visual metadata sync."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import config
from .insforge_adapters import BLOCKED_BY_EXTERNAL_DEPENDENCY, InsForgeAdapters, InsForgeUnavailable, get_insforge_adapters
from .visual_assets import VisualAssetStore, dumps, new_id, now


def _json(value: Any, default: Any) -> Any:
    try:
        parsed = json.loads(value or "") if isinstance(value, str) else value
        return parsed if parsed is not None else default
    except (TypeError, ValueError):
        return default


def _vector(value: Any, size: int = 1536) -> list[float]:
    values = _json(value, [])
    if not isinstance(values, list):
        return []
    try:
        result = [float(v) for v in values[:size]]
    except (TypeError, ValueError):
        return []
    return (result + [0.0] * size)[:size] if result else []


class InsForgeSyncAdapter:
    """Coordinates local truth, remote adapters and durable sync state."""

    backend_name = "insforge"

    def __init__(self, store: VisualAssetStore, adapters: InsForgeAdapters | None = None) -> None:
        self.store = store
        self.adapters = adapters or get_insforge_adapters()

    def _owner_id(self, user_id: str) -> str:
        # A local ``u_local`` ID is not a valid remote identity.  Require an
        # explicit mapping instead of silently crossing tenant boundaries.
        return config.INSFORGE_OWNER_ID or (user_id if user_id.count("-") == 4 else "")

    def _create_run(self, user_id: str, *, project_id: str, idempotency_key: str, asset_ids: list[str], resumed_from: str = "") -> dict[str, Any]:
        with self.store._lock:
            existing = self.store._conn.execute("SELECT * FROM sync_runs WHERE owner_id=? AND idempotency_key=?", (user_id, idempotency_key)).fetchone()
            if existing:
                return dict(existing)
            stamp = now()
            run_id = new_id("sync")
            self.store._conn.execute(
                "INSERT INTO sync_runs(id,source,owner_id,project_id,manifest,status,resumed_from,idempotency_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (run_id, self.backend_name, user_id, project_id[:160], dumps({"asset_ids": asset_ids}), "running", resumed_from[:80], idempotency_key[:160], stamp, stamp),
            )
            for asset_id in asset_ids:
                self.store._conn.execute("INSERT OR IGNORE INTO sync_run_items(id,sync_run_id,asset_id,status,updated_at) VALUES(?,?,?,?,?)", (new_id("syncitem"), run_id, asset_id, "pending", stamp))
            self.store._conn.commit()
        return self.get_run(run_id, user_id) or {}

    def get_run(self, run_id: str, user_id: str) -> dict[str, Any] | None:
        rows = self.store._rows("SELECT * FROM sync_runs WHERE id=? AND owner_id=?", (run_id, user_id))
        if not rows:
            return None
        run = rows[0]
        run["manifest"] = _json(run.get("manifest"), {})
        run["error_log"] = _json(run.get("error_log"), [])
        run["items"] = self.store._rows("SELECT * FROM sync_run_items WHERE sync_run_id=? ORDER BY updated_at", (run_id,))
        return run

    def _asset_payload(self, asset: dict[str, Any], remote_owner: str, project_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        asset_id = str(asset.get("asset_id") or asset.get("id") or "")
        if not asset_id:
            raise ValueError("visual asset payload is missing id")
        observations = self.store._rows("SELECT * FROM visual_observations WHERE asset_id=?", (asset_id,))
        entities: list[dict[str, Any]] = []
        relations: list[dict[str, Any]] = []
        for obs in observations:
            entity_id = str(obs.get("entity_id") or "")
            label = str(obs.get("candidate_label") or obs.get("label") or "").strip()
            entity_type = str(obs.get("entity_type") or "object")
            if not entity_id or not label:
                continue
            entities.append({"id": entity_id, "type": entity_type if entity_type in {"person", "club", "school", "event", "scene", "place", "object"} else "object", "name": label, "aliases": [], "description": "", "confidence": float(obs.get("confidence") or 0), "verification_status": obs.get("status") or "pending_review", "owner_id": remote_owner, "source_id": None})
            relations.append({"asset_id": asset_id, "entity_id": entity_id, "relation_type": entity_type, "confidence": float(obs.get("confidence") or 0), "evidence": _json(obs.get("evidence"), {}), "verified_by": str(obs.get("verified_by") or "")})
        remote_asset = {
            "id": asset_id,
            "project_id": project_id,
            "owner_id": remote_owner,
            "storage_path": f"{asset.get('user_id') or remote_owner}/{asset_id}/original{asset.get('extension') or ''}",
            "thumbnail_path": f"{asset.get('user_id') or remote_owner}/{asset_id}/thumbnail.jpg",
            "mime_type": asset.get("mime_type") or "application/octet-stream",
            "width": int(asset.get("width") or 0), "height": int(asset.get("height") or 0),
            "duration": float(asset.get("duration") or 0), "sha256": asset.get("sha256") or "",
            "exif_date": asset.get("exif_date") or None, "source": asset.get("source") or "",
            "permission_status": asset.get("privacy") or "private", "analysis_status": asset.get("review_status") or "pending_review",
        }
        return remote_asset, entities, relations

    def _sync_one(self, run_id: str, user_id: str, asset_id: str, project_id: str, remote_owner: str) -> dict[str, Any]:
        asset = self.store.owned_asset(asset_id, user_id)
        if not asset:
            return self._mark_item(run_id, asset_id, "failed", error_code="not_found", error_message="素材不存在或不屬於目前使用者")
        if asset.get("privacy") == "private" and not config.INSFORGE_ALLOW_PRIVATE_SYNC:
            return self._mark_item(run_id, asset_id, "failed", error_code=BLOCKED_BY_EXTERNAL_DEPENDENCY, error_message="private 素材未允許送往外部服務")
        try:
            remote_asset, entities, relations = self._asset_payload(asset, remote_owner, project_id)
            self.adapters.database.upsert("visual_assets", remote_asset)
            if entities:
                self.adapters.database.upsert("entities", entities)
                self.adapters.database.upsert("asset_entities", relations, on_conflict="asset_id,entity_id,relation_type")
            text_vector = _vector(asset.get("semantic_embedding"))
            image_vector = _vector(asset.get("color_embedding"))
            if text_vector or image_vector:
                self.adapters.database.upsert("embeddings", {"id": f"embedding_{asset_id}_local", "asset_id": asset_id, "text_embedding": dumps(text_vector) if text_vector else None, "image_embedding": dumps(image_vector) if image_vector else None, "model": "local-deterministic-v1", "owner_id": remote_owner}, on_conflict="asset_id,model")
            storage_result: dict[str, Any] = {}
            source_path = Path(str(asset.get("storage_path") or ""))
            if config.INSFORGE_TRUSTED and source_path.is_file() and (asset.get("privacy") != "private" or config.INSFORGE_ALLOW_PRIVATE_SYNC):
                storage_result = self.adapters.storage.upload_bytes(config.INSFORGE_STORAGE_BUCKET, str(remote_asset["storage_path"]), source_path.read_bytes(), str(asset.get("mime_type") or "application/octet-stream"))
            return self._mark_item(run_id, asset_id, "completed", remote_id=asset_id, remote_storage_path=str(storage_result.get("key") or remote_asset["storage_path"]))
        except InsForgeUnavailable as exc:
            return self._mark_item(run_id, asset_id, "failed", error_code=exc.code, error_message=str(exc))
        except Exception as exc:  # malformed one item must not abort the batch
            return self._mark_item(run_id, asset_id, "failed", error_code="sync_item_failed", error_message=str(exc)[:500])

    def _mark_item(self, run_id: str, asset_id: str, status: str, *, remote_id: str = "", remote_storage_path: str = "", error_code: str = "", error_message: str = "") -> dict[str, Any]:
        stamp = now()
        with self.store._lock:
            self.store._conn.execute("UPDATE sync_run_items SET status=?,remote_id=?,remote_storage_path=?,error_code=?,error_message=?,attempts=attempts+1,updated_at=? WHERE sync_run_id=? AND asset_id=?", (status, remote_id, remote_storage_path, error_code, error_message, stamp, run_id, asset_id))
            self.store._conn.execute("INSERT INTO visual_asset_backend_refs(asset_id,backend,remote_id,remote_storage_path,status,error_code,error_message,last_synced_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(asset_id) DO UPDATE SET backend=excluded.backend,remote_id=excluded.remote_id,remote_storage_path=excluded.remote_storage_path,status=excluded.status,error_code=excluded.error_code,error_message=excluded.error_message,last_synced_at=excluded.last_synced_at,updated_at=excluded.updated_at", (asset_id, self.backend_name, remote_id, remote_storage_path, status, error_code, error_message, stamp if status == "completed" else "", stamp))
            self.store._conn.commit()
        return {"asset_id": asset_id, "status": status, "error_code": error_code, "error_message": error_message}

    def run(self, user_id: str, *, asset_ids: list[str], project_id: str = "", idempotency_key: str, resumed_from: str = "") -> dict[str, Any]:
        asset_ids = list(dict.fromkeys(asset_ids))[:100]
        if not asset_ids:
            raise ValueError("至少指定一個 asset_id")
        remote_owner = self._owner_id(user_id)
        run = self._create_run(user_id, project_id=project_id, idempotency_key=idempotency_key, asset_ids=asset_ids, resumed_from=resumed_from)
        run_id = str(run["id"])
        if not config.INSFORGE_TRUSTED:
            for asset_id in asset_ids:
                self._mark_item(run_id, asset_id, "failed", error_code=BLOCKED_BY_EXTERNAL_DEPENDENCY, error_message="InsForge 尚未標記為 trusted")
        elif not remote_owner:
            for asset_id in asset_ids:
                self._mark_item(run_id, asset_id, "failed", error_code=BLOCKED_BY_EXTERNAL_DEPENDENCY, error_message="未設定 INSFORGE_OWNER_ID，拒絕將本地 tenant 映射至遠端")
        else:
            for item in self.store._rows("SELECT asset_id,status FROM sync_run_items WHERE sync_run_id=? ORDER BY rowid", (run_id,)):
                if item.get("status") == "completed":
                    continue
                self._sync_one(run_id, user_id, str(item["asset_id"]), project_id, remote_owner)
        with self.store._lock:
            counts = self.store._conn.execute("SELECT SUM(status='completed'),SUM(status='failed'),COUNT(*) FROM sync_run_items WHERE sync_run_id=?", (run_id,)).fetchone()
            imported, failed, total = (int(v or 0) for v in counts)
            status = "completed" if imported == total else "partial" if imported else "blocked" if failed == total else "failed"
            errors = self.store._rows("SELECT asset_id,error_code,error_message FROM sync_run_items WHERE sync_run_id=? AND status='failed'", (run_id,))
            self.store._conn.execute("UPDATE sync_runs SET status=?,imported_count=?,failed_count=?,error_log=?,updated_at=? WHERE id=? AND owner_id=?", (status, imported, failed, dumps(errors), now(), run_id, user_id))
            self.store._conn.commit()
        return self.get_run(run_id, user_id) or {}

    def retry(self, run_id: str, user_id: str, asset_ids: list[str] | None = None) -> dict[str, Any] | None:
        run = self.get_run(run_id, user_id)
        if not run:
            return None
        selected = asset_ids or [str(i["asset_id"]) for i in run.get("items", []) if i.get("status") != "completed"]
        return self.run(user_id, asset_ids=selected, project_id=str(run.get("project_id") or ""), idempotency_key=f"{run.get('idempotency_key','')}:retry:{now()}", resumed_from=run_id)

    def rollback(self, run_id: str, user_id: str) -> dict[str, Any] | None:
        run = self.get_run(run_id, user_id)
        if not run:
            return None
        # Never delete originals or issue remote destructive deletes.  Mark the
        # local reference as rolled back; an operator can reconcile remote data.
        with self.store._lock:
            self.store._conn.execute("UPDATE sync_runs SET rollback_status='complete',status='rolled_back',updated_at=? WHERE id=? AND owner_id=?", (now(), run_id, user_id))
            self.store._conn.execute("UPDATE visual_asset_backend_refs SET status='rolled_back_local',updated_at=? WHERE asset_id IN (SELECT asset_id FROM sync_run_items WHERE sync_run_id=?)", (now(), run_id))
            self.store._conn.commit()
        return self.get_run(run_id, user_id)
