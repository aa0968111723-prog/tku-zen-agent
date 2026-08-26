"""Safe, resumable synchronization of durable local data to InsForge."""

from __future__ import annotations

import hashlib
import json
import mimetypes
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import config, retrieval
from .insforge_adapters import BLOCKED_BY_EXTERNAL_DEPENDENCY, InsForgeAdapters, InsForgeUnavailable, get_insforge_adapters
from .insforge_sync import InsForgeSyncAdapter, _vector
from .session_store import SessionStore, get_store
from .visual_assets import VisualAssetStore, dumps, get_visual_store, new_id, now, semantic_embedding


SAFE_GROUPS = {"projects", "artifacts", "activities", "research_sources", "knowledge", "visual_assets"}
SENSITIVE_KEYS = {"api_key", "apikey", "authorization", "cookie", "password", "secret", "token"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pgvector(values: list[float]) -> str | None:
    return dumps(values) if values else None


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize(item)
            for key, item in value.items()
            if not any(marker in str(key).lower() for marker in SENSITIVE_KEYS)
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        return value.replace("\x00", "")
    return value


def _safe_artifact_file(path_value: str) -> Path | None:
    try:
        candidate = Path(path_value).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    roots = {config.OUTPUT_DIR.resolve(), config.VISUAL_EXPORT_DIR.resolve()}
    if not candidate.is_file() or not any(candidate == root or root in candidate.parents for root in roots):
        return None
    return candidate


@dataclass
class SyncResource:
    resource_type: str
    resource_id: str
    table: str
    payload: dict[str, Any]
    content_sha256: str = ""
    file_path: Path | None = None
    storage_key: str = ""
    mime_type: str = "application/octet-stream"
    metadata: dict[str, Any] = field(default_factory=dict)


class InsForgeDataSyncAdapter:
    """Synchronize selected durable records while leaving local data authoritative."""

    def __init__(
        self,
        session_store: SessionStore | None = None,
        visual_store: VisualAssetStore | None = None,
        adapters: InsForgeAdapters | None = None,
    ) -> None:
        self.session_store = session_store or get_store()
        self.visual_store = visual_store or get_visual_store()
        self.adapters = adapters or get_insforge_adapters()

    def _remote_owner(self, user_id: str) -> str:
        rows = self.visual_store._rows(
            "SELECT remote_owner_id FROM backend_user_mappings WHERE local_user_id=? AND backend='insforge' AND status='verified'",
            (user_id,),
        )
        return str(rows[0]["remote_owner_id"]) if rows else (config.INSFORGE_OWNER_ID or "")

    def _remember_owner_mapping(self, user_id: str, remote_owner: str) -> None:
        if not remote_owner:
            return
        stamp = now()
        with self.visual_store._lock:
            self.visual_store._conn.execute(
                "INSERT INTO backend_user_mappings(local_user_id,backend,remote_owner_id,status,created_at,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(local_user_id,backend) DO UPDATE SET remote_owner_id=excluded.remote_owner_id,status='verified',updated_at=excluded.updated_at",
                (user_id,"insforge",remote_owner,"verified",stamp,stamp),
            )
            self.visual_store._conn.commit()

    def _core_resources(self, user_id: str, project_id: str, groups: set[str]) -> list[SyncResource]:
        snapshot = self.session_store.export_sync_snapshot(
            user_id, project_id=project_id or None, limit=config.INSFORGE_SYNC_LIMIT,
        )
        resources: list[SyncResource] = []
        remote_owner = self._remote_owner(user_id)
        if "projects" in groups:
            for row in snapshot["projects"]:
                payload = {key: row.get(key) for key in ("id", "name", "task_type", "summary", "created_at", "updated_at")}
                payload["owner_id"] = remote_owner
                resources.append(SyncResource("project", str(row["id"]), "projects", payload))
        if "artifacts" in groups:
            for row in sorted(snapshot["artifacts"], key=lambda item: (str(item.get("created_at") or ""), int(item.get("version") or 0))):
                file_path = _safe_artifact_file(str(row.get("local_path") or ""))
                content_sha = _sha(file_path.read_bytes()) if file_path else ""
                storage_key = f"owners/{remote_owner}/artifacts/{row['id']}/{Path(str(row['filename'])).name}"
                payload = {
                    "id": row["id"], "owner_id": remote_owner, "project_id": row.get("project_id"),
                    "session_ref": "", "filename": Path(str(row.get("filename") or "artifact")).name,
                    "kind": row.get("kind") or "", "storage_path": "", "drive_url": row.get("drive_url") or "",
                    "sha256": content_sha, "size_bytes": file_path.stat().st_size if file_path else 0,
                    "version": int(row.get("version") or 1), "parent_id": row.get("parent_id"),
                    "metadata": _sanitize(row.get("meta") or {}), "created_at": row["created_at"],
                }
                resources.append(SyncResource("artifact", str(row["id"]), "artifacts", payload, content_sha, file_path, storage_key, mimetypes.guess_type(str(row.get("filename") or ""))[0] or "application/octet-stream"))
        if "activities" in groups:
            for row in snapshot["activities"]:
                payload = {key: row.get(key) for key in (
                    "id", "project_id", "name", "activity_type", "academic_year", "semester", "status",
                    "start_at", "end_at", "location", "goal", "audience", "signup_url", "notes", "created_at", "updated_at",
                )}
                payload.update({"owner_id": remote_owner, "activity_owner": row.get("owner") or ""})
                resources.append(SyncResource("activity", str(row["id"]), "activities", payload))
            for row in snapshot["activity_tasks"]:
                payload = {key: row.get(key) for key in (
                    "id", "activity_id", "title", "group_name", "assignee", "due_at", "status", "priority", "notes", "created_at", "updated_at",
                )}
                payload["owner_id"] = remote_owner
                resources.append(SyncResource("activity_task", str(row["id"]), "activity_tasks", payload))
        if "research_sources" in groups:
            for row in snapshot["research_sources"]:
                payload = {key: row.get(key) for key in (
                    "id", "project_id", "title", "url", "source_date", "summary", "credibility", "verification",
                    "source_type", "source_file", "entity_id", "school", "created_at",
                )}
                payload.update({"owner_id": remote_owner, "session_ref": ""})
                resources.append(SyncResource("research_source", str(row["id"]), "research_sources", _sanitize(payload)))
        return resources

    def _knowledge_resources(self, user_id: str, project_id: str) -> list[SyncResource]:
        remote_owner = self._remote_owner(user_id)
        index = retrieval.get_index()
        grouped: dict[str, list[Any]] = {}
        for chunk in index.chunks:
            source_type = str(getattr(chunk.meta, "source_type", "archive") or "archive")
            if source_type == "conversation" and not config.INSFORGE_ALLOW_CONVERSATION_KNOWLEDGE_SYNC:
                continue
            grouped.setdefault(str(chunk.path), []).append(chunk)
        documents: list[SyncResource] = []
        chunk_resources: list[SyncResource] = []
        root = config.KNOWLEDGE_DIR.resolve()
        for path_value, chunks in grouped.items():
            path = Path(path_value)
            try:
                resolved = path.resolve(strict=True)
                relative_path = resolved.relative_to(root).as_posix()
                content = resolved.read_bytes()
            except (OSError, ValueError, RuntimeError):
                continue
            file_sha = _sha(content)
            document_id = "kdoc_" + hashlib.sha1(f"{remote_owner}:{relative_path}:{file_sha}".encode()).hexdigest()[:24]
            first = chunks[0]
            source_type = str(getattr(first.meta, "source_type", "archive") or "archive")
            source_scope = str(getattr(first.meta, "source_scope", "internal") or "internal")
            storage_key = f"owners/{remote_owner}/knowledge/{file_sha}/{relative_path}"
            doc_payload = {
                "id": document_id, "owner_id": remote_owner, "project_id": project_id,
                "title": str(first.source).split(" › ")[0], "relative_path": relative_path,
                "storage_path": "", "source_type": source_type, "source_scope": source_scope,
                "sha256": file_sha, "size_bytes": len(content),
                "modified_at": datetime.fromtimestamp(resolved.stat().st_mtime, timezone.utc).isoformat(),
            }
            documents.append(SyncResource("knowledge_document", document_id, "knowledge_documents", doc_payload, file_sha, resolved, storage_key, "text/markdown"))
            for position, chunk in enumerate(chunks):
                text = str(chunk.text)
                chunk_sha = hashlib.sha256(text.encode()).hexdigest()
                chunk_id = "kchunk_" + hashlib.sha1(f"{document_id}:{position}:{chunk_sha}".encode()).hexdigest()[:24]
                metadata = _sanitize(vars(chunk.meta) if getattr(chunk, "meta", None) else {})
                payload = {
                    "id": chunk_id, "document_id": document_id, "owner_id": remote_owner, "project_id": project_id,
                    "chunk_index": position, "heading": str(chunk.heading), "content": text,
                    "source_label": str(chunk.source), "source_type": source_type, "source_scope": source_scope,
                    "school": str(metadata.get("organization_school") or metadata.get("school") or ""),
                    "entity_id": str(metadata.get("entity_id") or ""), "metadata": metadata,
                    "embedding": _pgvector(_vector(semantic_embedding(text))), "embedding_model": "local-deterministic-v1",
                    "content_sha256": chunk_sha,
                }
                chunk_resources.append(SyncResource("knowledge_chunk", chunk_id, "knowledge_chunks", payload, chunk_sha))
        return documents + chunk_resources

    def _visual_resources(self, user_id: str, project_id: str) -> list[SyncResource]:
        remote_owner = self._remote_owner(user_id)
        helper = InsForgeSyncAdapter(self.visual_store, self.adapters)
        resources: list[SyncResource] = []
        rows = self.visual_store._rows(
            "SELECT id FROM visual_assets WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, config.INSFORGE_SYNC_LIMIT),
        )
        for row in rows:
            asset = self.visual_store.owned_asset(str(row["id"]), user_id)
            if not asset:
                continue
            remote_asset, entities, relations = helper._asset_payload(asset, remote_owner, project_id)
            resources.append(SyncResource(
                "visual_asset", str(row["id"]), "visual_assets", remote_asset,
                str(asset.get("sha256") or ""), Path(str(asset.get("storage_path") or "")),
                str(remote_asset["storage_path"]), str(asset.get("mime_type") or "application/octet-stream"),
                {"entities": entities, "relations": relations, "semantic_embedding": asset.get("semantic_embedding"), "color_embedding": asset.get("color_embedding"), "privacy": asset.get("privacy")},
            ))
        return resources

    def discover(self, user_id: str, *, groups: set[str], project_id: str = "") -> list[SyncResource]:
        invalid = groups - SAFE_GROUPS
        if invalid:
            raise ValueError(f"不支援的同步類型：{', '.join(sorted(invalid))}")
        resources = self._core_resources(user_id, project_id, groups)
        if "knowledge" in groups:
            resources.extend(self._knowledge_resources(user_id, project_id))
        if "visual_assets" in groups:
            resources.extend(self._visual_resources(user_id, project_id))
        return resources

    def preview(self, user_id: str, *, groups: set[str], project_id: str = "") -> dict[str, Any]:
        resources = self.discover(user_id, groups=groups, project_id=project_id)
        counts: dict[str, int] = {}
        for resource in resources:
            counts[resource.resource_type] = counts.get(resource.resource_type, 0) + 1
        return {
            "dry_run": True, "groups": sorted(groups), "project_id": project_id,
            "counts": counts, "total": len(resources),
            "excluded": ["sessions", "messages", "working_memory", "retrieval_cache", "audit_logs", "credentials"],
            "contains_absolute_paths": False,
        }

    def _create_run(self, user_id: str, project_id: str, idempotency_key: str, groups: set[str], resources: list[SyncResource], resumed_from: str) -> tuple[str, bool]:
        with self.visual_store._lock:
            existing = self.visual_store._conn.execute(
                "SELECT id FROM sync_runs WHERE owner_id=? AND idempotency_key=?", (user_id, idempotency_key),
            ).fetchone()
            if existing:
                return str(existing[0]), False
            run_id = new_id("sync")
            stamp = now()
            counts: dict[str, int] = {}
            for resource in resources:
                counts[resource.resource_type] = counts.get(resource.resource_type, 0) + 1
            manifest = {"groups": sorted(groups), "counts": counts, "total": len(resources), "excluded": ["sessions", "messages", "working_memory", "retrieval_cache", "audit_logs", "credentials"]}
            self.visual_store._conn.execute(
                "INSERT INTO sync_runs(id,source,owner_id,project_id,manifest,status,resumed_from,idempotency_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (run_id, "insforge_core", user_id, project_id, dumps(manifest), "running", resumed_from, idempotency_key, stamp, stamp),
            )
            for resource in resources:
                self.visual_store._conn.execute(
                    "INSERT INTO backend_sync_items(id,sync_run_id,resource_type,resource_id,content_sha256,status,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (new_id("syncitem"), run_id, resource.resource_type, resource.resource_id, resource.content_sha256, "pending", stamp),
                )
            self.visual_store._conn.commit()
        return run_id, True

    def _mark(self, run_id: str, resource: SyncResource, status: str, *, remote_path: str = "", error_code: str = "", error_message: str = "") -> None:
        stamp = now()
        with self.visual_store._lock:
            self.visual_store._conn.execute(
                "UPDATE backend_sync_items SET status=?,remote_id=?,remote_storage_path=?,error_code=?,error_message=?,attempts=attempts+1,updated_at=? WHERE sync_run_id=? AND resource_type=? AND resource_id=?",
                (status, resource.resource_id if status == "completed" else "", remote_path, error_code, error_message[:500], stamp, run_id, resource.resource_type, resource.resource_id),
            )
            self.visual_store._conn.execute(
                "INSERT INTO backend_resource_refs(resource_type,resource_id,backend,remote_id,remote_storage_path,content_sha256,status,error_code,error_message,last_synced_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(resource_type,resource_id,backend) DO UPDATE SET remote_id=excluded.remote_id,remote_storage_path=excluded.remote_storage_path,content_sha256=excluded.content_sha256,status=excluded.status,error_code=excluded.error_code,error_message=excluded.error_message,last_synced_at=excluded.last_synced_at,updated_at=excluded.updated_at",
                (resource.resource_type, resource.resource_id, "insforge", resource.resource_id if status == "completed" else "", remote_path, resource.content_sha256, status, error_code, error_message[:500], stamp if status == "completed" else "", stamp),
            )
            self.visual_store._conn.commit()

    def _sync_resource(self, run_id: str, resource: SyncResource) -> None:
        try:
            if resource.resource_type.startswith("knowledge_") and not config.INSFORGE_ALLOW_KNOWLEDGE_SYNC:
                raise InsForgeUnavailable("knowledge 同步尚未明確允許")
            if resource.resource_type == "visual_asset" and resource.metadata.get("privacy") == "private" and not config.INSFORGE_ALLOW_PRIVATE_SYNC:
                raise InsForgeUnavailable("private 視覺素材未允許送往外部服務")
            payload = _sanitize(dict(resource.payload))
            remote_path = ""
            should_upload = bool(
                resource.file_path and resource.file_path.is_file() and (
                    resource.resource_type == "knowledge_document"
                    or resource.resource_type == "visual_asset"
                    or (resource.resource_type == "artifact" and config.INSFORGE_ALLOW_ARTIFACT_FILE_SYNC)
                )
            )
            if should_upload:
                uploaded = self.adapters.storage.upload_bytes(
                    config.INSFORGE_STORAGE_BUCKET, resource.storage_key,
                    resource.file_path.read_bytes(), resource.mime_type,
                )
                remote_path = str(uploaded.get("key") or resource.storage_key)
                payload["storage_path"] = remote_path
            self.adapters.database.upsert(resource.table, payload)
            if resource.resource_type == "visual_asset":
                entities = resource.metadata.get("entities") or []
                relations = resource.metadata.get("relations") or []
                if entities:
                    self.adapters.database.upsert("entities", entities)
                    self.adapters.database.upsert("asset_entities", relations, on_conflict="asset_id,entity_id,relation_type")
                text_vector = _vector(resource.metadata.get("semantic_embedding"))
                image_vector = _vector(resource.metadata.get("color_embedding"))
                if text_vector or image_vector:
                    self.adapters.database.upsert("embeddings", {"id": f"embedding_{resource.resource_id}_local", "asset_id": resource.resource_id, "text_embedding": _pgvector(text_vector), "image_embedding": _pgvector(image_vector), "model": "local-deterministic-v1", "owner_id": payload["owner_id"]}, on_conflict="asset_id,model")
            self._mark(run_id, resource, "completed", remote_path=remote_path)
        except InsForgeUnavailable as exc:
            self._mark(run_id, resource, "failed", error_code=exc.code, error_message=str(exc))
        except Exception as exc:
            self._mark(run_id, resource, "failed", error_code="sync_item_failed", error_message=str(exc))

    def _sync_batch(self, run_id: str, resources: list[SyncResource]) -> None:
        if not resources:
            return
        try:
            if resources[0].resource_type == "knowledge_chunk" and not config.INSFORGE_ALLOW_KNOWLEDGE_SYNC:
                raise InsForgeUnavailable("knowledge 同步尚未明確允許")
            self.adapters.database.upsert(resources[0].table,[_sanitize(resource.payload) for resource in resources])
            for resource in resources:
                self._mark(run_id,resource,"completed")
        except InsForgeUnavailable as exc:
            for resource in resources:
                self._mark(run_id,resource,"failed",error_code=exc.code,error_message=str(exc))
        except Exception as exc:
            for resource in resources:
                self._mark(run_id,resource,"failed",error_code="sync_item_failed",error_message=str(exc))

    def get_run(self, run_id: str, user_id: str) -> dict[str, Any] | None:
        rows = self.visual_store._rows("SELECT * FROM sync_runs WHERE id=? AND owner_id=? AND source='insforge_core'", (run_id, user_id))
        if not rows:
            return None
        run = rows[0]
        run["manifest"] = json.loads(run.get("manifest") or "{}")
        run["error_log"] = json.loads(run.get("error_log") or "[]")
        run["items"] = self.visual_store._rows("SELECT * FROM backend_sync_items WHERE sync_run_id=? ORDER BY resource_type,resource_id", (run_id,))
        return run

    def run(
        self, user_id: str, *, groups: set[str], project_id: str,
        idempotency_key: str, resumed_from: str = "",
        resource_filter: set[tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        resources = self.discover(user_id, groups=groups, project_id=project_id)
        if resource_filter is not None:
            resources = [resource for resource in resources if (resource.resource_type, resource.resource_id) in resource_filter]
        run_id, created = self._create_run(user_id, project_id, idempotency_key, groups, resources, resumed_from)
        if not created:
            return self.get_run(run_id, user_id) or {}
        remote_owner = self._remote_owner(user_id)
        if not config.INSFORGE_TRUSTED or not remote_owner:
            reason = "InsForge 未標記為 trusted" if not config.INSFORGE_TRUSTED else "未設定遠端 owner mapping"
            for resource in resources:
                self._mark(run_id, resource, "failed", error_code=BLOCKED_BY_EXTERNAL_DEPENDENCY, error_message=reason)
        else:
            self._remember_owner_mapping(user_id,remote_owner)
            batchable = {"project","activity","activity_task","research_source","knowledge_chunk"}
            position = 0
            while position < len(resources):
                resource = resources[position]
                if resource.resource_type in {"knowledge_document","artifact"}:
                    parallel = [resource]
                    position += 1
                    while position < len(resources) and len(parallel) < 60 and resources[position].resource_type == resource.resource_type:
                        parallel.append(resources[position])
                        position += 1
                    with ThreadPoolExecutor(max_workers=config.INSFORGE_SYNC_WORKERS) as executor:
                        list(executor.map(lambda item: self._sync_resource(run_id,item),parallel))
                    continue
                if resource.resource_type not in batchable:
                    self._sync_resource(run_id,resource)
                    position += 1
                    continue
                batch = [resource]
                position += 1
                while position < len(resources) and len(batch) < 50 and resources[position].table == resource.table and resources[position].resource_type == resource.resource_type:
                    batch.append(resources[position])
                    position += 1
                self._sync_batch(run_id,batch)
        self._finish(run_id, user_id)
        return self.get_run(run_id, user_id) or {}

    def _finish(self, run_id: str, user_id: str) -> None:
        with self.visual_store._lock:
            counts = self.visual_store._conn.execute(
                "SELECT SUM(status='completed'),SUM(status='failed'),COUNT(*) FROM backend_sync_items WHERE sync_run_id=?", (run_id,),
            ).fetchone()
            completed, failed, total = (int(value or 0) for value in counts)
            status = "completed" if total == completed else "partial" if completed else "blocked"
            errors = self.visual_store._rows(
                "SELECT resource_type,resource_id,error_code,error_message FROM backend_sync_items WHERE sync_run_id=? AND status='failed'", (run_id,),
            )
            self.visual_store._conn.execute(
                "UPDATE sync_runs SET status=?,imported_count=?,failed_count=?,error_log=?,updated_at=? WHERE id=? AND owner_id=?",
                (status, completed, failed, dumps(errors), now(), run_id, user_id),
            )
            self.visual_store._conn.commit()

    def retry(self, run_id: str, user_id: str) -> dict[str, Any] | None:
        previous = self.get_run(run_id, user_id)
        if not previous:
            return None
        groups = set(previous["manifest"].get("groups") or [])
        failed = {
            (str(item["resource_type"]), str(item["resource_id"]))
            for item in previous.get("items", []) if item.get("status") != "completed"
        }
        if previous.get("status") == "running":
            with self.visual_store._lock:
                self.visual_store._conn.execute(
                    "UPDATE sync_runs SET status='interrupted',updated_at=? WHERE id=? AND owner_id=?",
                    (now(),run_id,user_id),
                )
                self.visual_store._conn.commit()
        return self.run(
            user_id, groups=groups, project_id=str(previous.get("project_id") or ""),
            idempotency_key=f"{previous['idempotency_key']}:retry:{now()}", resumed_from=run_id,
            resource_filter=failed,
        )

    def rollback(self, run_id: str, user_id: str) -> dict[str, Any] | None:
        previous = self.get_run(run_id, user_id)
        if not previous:
            return None
        with self.visual_store._lock:
            self.visual_store._conn.execute("UPDATE sync_runs SET rollback_status='complete',status='rolled_back',updated_at=? WHERE id=? AND owner_id=?", (now(), run_id, user_id))
            self.visual_store._conn.execute(
                "UPDATE backend_resource_refs SET status='rolled_back_local',updated_at=? WHERE (resource_type,resource_id) IN (SELECT resource_type,resource_id FROM backend_sync_items WHERE sync_run_id=?)",
                (now(), run_id),
            )
            self.visual_store._conn.commit()
        return self.get_run(run_id, user_id)
