"""Inventory and non-destructive mapping for existing local data.

The visual/domain tables remain authoritative.  This service only creates
lineage, generic entity mappings, graph edges and review proposals so it can be
run repeatedly without rewriting source files or silently promoting guesses.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from collections import Counter, defaultdict
from dataclasses import asdict, is_dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .. import config, retrieval
from .session_store import SessionStore, get_store
from .visual_assets import ALLOWED_MIME, VisualAssetStore, dumps, get_visual_store, new_id, now


MIGRATION_VERSION = "0005_data_organization_depth"
VALID_STATUSES = {"verified", "probable", "pending_review", "conflicted", "failed"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".avif", ".jfif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v", ".avi", ".mkv"}
DOCUMENT_EXTENSIONS = {".md", ".txt", ".csv", ".pdf", ".docx", ".pptx", ".xlsx", ".gs"}
IGNORED_IMPORT_DIRS = {
    ".git", ".venv", ".pytest_cache", ".playwright-cli", ".claude", ".codex",
    ".codex-remote-attachments", "__pycache__", "node_modules",
}


def _stable_id(prefix: str, *parts: object) -> str:
    raw = "\x1f".join(str(part or "") for part in parts)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _status(value: object, default: str = "pending_review") -> str:
    raw = str(value or default)
    aliases = {"confirmed": "verified", "possible": "probable", "pending": "pending_review", "conflict": "conflicted"}
    normalized = aliases.get(raw, raw)
    return normalized if normalized in VALID_STATUSES else default


def _json(value: object, default: object) -> object:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError):
        return default


class DataOrganizationService:
    """Create a server-truth inventory and map existing records idempotently."""

    def __init__(
        self,
        session_store: SessionStore | None = None,
        visual_store: VisualAssetStore | None = None,
    ) -> None:
        self.session_store = session_store or get_store()
        self.visual_store = visual_store or get_visual_store()

    def _owned_visual_scope(self, user_id: str, project_id: str = "") -> dict[str, set[str]]:
        """Return canonical entity ids reachable from assets the caller may own.

        The canonical taxonomy tables intentionally do not have an owner column.
        Reachability through an owned asset is therefore the ACL boundary; loading
        every canonical row here would leak another user's people or club names.
        """
        sql = "SELECT id,school_id,club_id FROM visual_assets WHERE user_id=? AND COALESCE(source,'')!='derived_thumbnail'"
        params: tuple[Any, ...] = (user_id,)
        if project_id:
            sql += " AND project_id=?"
            params += (project_id,)
        assets = self.visual_store._rows(sql, params)
        asset_ids = {str(row["id"]) for row in assets}
        scope = {
            "asset": asset_ids,
            "school": {str(row["school_id"]) for row in assets if row.get("school_id")},
            "club": {str(row["club_id"]) for row in assets if row.get("club_id")},
            "person": set(),
            "event": set(),
            "scene": set(),
        }
        if asset_ids:
            placeholders = ",".join("?" for _ in asset_ids)
            observations = self.visual_store._rows(
                f"SELECT entity_type,entity_id FROM visual_observations WHERE asset_id IN ({placeholders}) AND entity_id!='' AND review_action!='ignored'",
                tuple(asset_ids),
            )
            for row in observations:
                kind = str(row.get("entity_type") or "")
                if kind in scope:
                    scope[kind].add(str(row["entity_id"]))
        for table, kind in (("visual_people", "person"), ("visual_events", "event")):
            ids = scope[kind]
            if not ids:
                continue
            placeholders = ",".join("?" for _ in ids)
            for row in self.visual_store._rows(
                f"SELECT school_id,club_id FROM {table} WHERE id IN ({placeholders})", tuple(ids),
            ):
                if row.get("school_id"):
                    scope["school"].add(str(row["school_id"]))
                if row.get("club_id"):
                    scope["club"].add(str(row["club_id"]))
        if scope["club"]:
            placeholders = ",".join("?" for _ in scope["club"])
            for row in self.visual_store._rows(
                f"SELECT school_id FROM visual_clubs WHERE id IN ({placeholders})", tuple(scope["club"]),
            ):
                scope["school"].add(str(row["school_id"]))
        return scope

    def _scope_count(self, table: str, ids: set[str], extra: str = "", params: tuple[Any, ...] = ()) -> int:
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        clause = f" AND {extra}" if extra else ""
        return int(self.visual_store._conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE id IN ({placeholders}){clause}", tuple(ids) + params,
        ).fetchone()[0])

    def _knowledge_documents(self) -> list[dict[str, Any]]:
        grouped: dict[str, list[Any]] = defaultdict(list)
        for chunk in retrieval.get_index().chunks:
            grouped[str(chunk.path)].append(chunk)
        root = config.KNOWLEDGE_DIR.resolve()
        documents: list[dict[str, Any]] = []
        for path_value, chunks in sorted(grouped.items()):
            path = Path(path_value)
            try:
                resolved = path.resolve(strict=True)
                relative = resolved.relative_to(root).as_posix()
                digest = _sha_file(resolved)
                stat = resolved.stat()
            except (OSError, RuntimeError, ValueError):
                continue
            first = chunks[0]
            meta = first.meta
            metadata = meta.to_dict() if hasattr(meta, "to_dict") else asdict(meta) if is_dataclass(meta) else {}
            documents.append({
                "id": _stable_id("knowledge", relative, digest),
                "path": resolved,
                "relative_path": relative,
                "sha256": digest,
                "size_bytes": stat.st_size,
                "updated_at": str(int(stat.st_mtime)),
                "title": str(first.source).split(" › ")[0],
                "metadata": metadata,
            })
        return documents

    @staticmethod
    def _kind(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in IMAGE_EXTENSIONS:
            return "image"
        if suffix in VIDEO_EXTENSIONS:
            return "video"
        if suffix in DOCUMENT_EXTENSIONS:
            return "document"
        return "other"

    @staticmethod
    def _derived_thumbnail_source(row: dict[str, Any]) -> str:
        """Return the parent asset id for a previously imported rendition.

        Older organization runs could see ``data`` before the canonical
        ``data/visual-assets`` walk and import generated thumbnails as new
        images.  Those rows are retained for auditability, but are marked as
        derivatives so they cannot appear as independent searchable assets.
        """
        relative = PurePosixPath(str(row.get("relative_path") or "").replace("\\", "/"))
        parts = relative.parts
        try:
            visual_index = parts.index("visual-assets")
        except ValueError:
            return ""
        if len(parts) <= visual_index + 2:
            return ""
        filename = parts[-1].lower()
        if not (filename.startswith("thumbnail") or filename.startswith("rendition-")):
            return ""
        candidate = parts[visual_index + 2]
        return candidate if candidate.startswith("asset_") else ""

    def _reconcile_derived_assets(self, user_id: str, project_id: str = "") -> int:
        """Annotate legacy thumbnail imports without deleting or rewriting files."""
        sql = "SELECT id,user_id,relative_path,source,source_metadata FROM visual_assets WHERE user_id=?"
        params: tuple[Any, ...] = (user_id,)
        if project_id:
            sql += " AND project_id=?"
            params += (project_id,)
        rows = self.visual_store._conn.execute(sql, params).fetchall()
        updates: list[tuple[str, str, str, str, str]] = []
        for row in rows:
            data = dict(row)
            parent_id = self._derived_thumbnail_source(data)
            if not parent_id or data.get("source") == "derived_thumbnail":
                continue
            metadata = _json(data.get("source_metadata"), {})
            if not isinstance(metadata, dict):
                metadata = {}
            metadata.update({"derived_asset": True, "derived_from_asset_id": parent_id})
            # Only mark a row as a derivative when its encoded parent belongs
            # to the same owner.  This keeps owner isolation intact even if a
            # hand-edited path points at another user's asset id.
            parent = self.visual_store._conn.execute(
                "SELECT id FROM visual_assets WHERE id=? AND user_id=?", (parent_id, user_id),
            ).fetchone()
            duplicate_of = parent_id if parent else ""
            updates.append((dumps(metadata), duplicate_of, data["id"], now(), user_id))
        if not updates:
            return 0
        with self.visual_store._lock:
            self.visual_store._conn.executemany(
                "UPDATE visual_assets SET source='derived_thumbnail',source_metadata=?,duplicate_of=CASE WHEN ?!='' THEN ? ELSE duplicate_of END,review_status='verified',processing_state='completed',updated_at=? WHERE id=? AND user_id=?",
                [(metadata, duplicate, duplicate, stamp, asset_id, owner) for metadata, duplicate, asset_id, stamp, owner in updates],
            )
            self.visual_store._conn.executemany(
                "UPDATE review_queue SET status='ignored',reviewer_id='system:derived_thumbnail',reviewed_at=? WHERE asset_id=? AND owner_id=? AND status NOT IN ('approved','rejected','ignored')",
                [(_stamp, asset_id, owner) for _metadata, _duplicate, asset_id, _stamp, owner in updates],
            )
            self.visual_store._conn.commit()
        return len(updates)

    @staticmethod
    def _source_key(row: dict[str, Any]) -> str:
        metadata = _json(row.get("source_metadata"), {})
        if not isinstance(metadata, dict):
            return ""
        root = str(metadata.get("root") or "").strip()
        relative = str(metadata.get("relative_path") or "").replace("\\", "/").strip("/")
        return f"{root}:{relative}" if root and relative else ""

    @staticmethod
    def _filesystem_source_key(row: dict[str, Any]) -> str:
        relative = str(row.get("relative_path") or "").replace(chr(92), "/").strip("/")
        return f"{str(row.get('root') or '').strip()}:{relative}"

    @staticmethod
    def _source_sha_map(assets: list[dict[str, Any]]) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for row in assets:
            key = DataOrganizationService._source_key(row)
            digest = str(row.get("sha256") or "")
            if key and digest:
                mapping[key] = digest
        return mapping

    def _content_is_unregistered(
        self,
        row: dict[str, Any],
        registered_source_keys: set[str],
        registered_paths: set[str],
        source_sha: dict[str, str],
    ) -> bool:
        source_key = self._filesystem_source_key(row)
        digest = str(row.get("sha256") or "")
        if source_key and source_key in source_sha and digest and source_sha[source_key] != digest:
            return True
        if source_key in registered_source_keys:
            return False
        try:
            resolved = str((Path(str(row.get("_base_path") or config.OUTPUT_DIR)) / row["relative_path"]).resolve())
        except (OSError, RuntimeError, TypeError, ValueError):
            resolved = ""
        return resolved not in registered_paths

    def _lineage_source_keys(self, user_id: str) -> set[str]:
        """Return source keys recorded for loose files, including SHA dedupes.

        A filesystem duplicate can intentionally reuse an existing visual
        asset row, so its source metadata belongs to the first file while its
        own lineage still records the later path.  Inventory must consult both
        records to avoid reporting those legitimate, already-mapped files as
        unregistered on every reload.
        """
        keys: set[str] = set()
        rows = self.visual_store._conn.execute(
            "SELECT metadata FROM data_lineage WHERE owner_id=? AND resource_type='filesystem_asset'",
            (user_id,),
        ).fetchall()
        for row in rows:
            metadata = _json(row[0], {})
            if not isinstance(metadata, dict):
                continue
            root = str(metadata.get("root") or "").strip()
            relative = str(metadata.get("relative_path") or "").replace("\\", "/").strip("/")
            if root and relative:
                keys.add(f"{root}:{relative}")
        return keys

    def _filesystem_candidates(self, user_id: str = "") -> list[dict[str, Any]]:
        roots = [
            ("visual_assets", config.VISUAL_ASSET_DIR),
            ("outputs", config.OUTPUT_DIR),
            ("knowledge", config.KNOWLEDGE_DIR),
        ]
        # Additional roots may be a parent of one of the canonical roots
        # (the default ``data`` root is).  Keep the canonical walk as the
        # source of truth so a broader walk cannot re-import originals,
        # thumbnails, or generated renditions as loose user assets.
        canonical_roots = {
            Path(config.VISUAL_ASSET_DIR).resolve(): "visual_assets",
            Path(config.OUTPUT_DIR).resolve(): "outputs",
            Path(config.KNOWLEDGE_DIR).resolve(): "knowledge",
        }
        known_roots = {Path(value).resolve() for _label, value in roots}
        for value in getattr(config, "DATA_ORGANIZATION_IMPORT_ROOTS", ()):
            root = Path(value)
            try:
                resolved_root = root.resolve()
            except OSError:
                continue
            if resolved_root in known_roots:
                continue
            known_roots.add(resolved_root)
            roots.append((root.name or "local_data", root))
        records: list[dict[str, Any]] = []
        seen: set[Path] = set()
        local_owner = False
        if user_id:
            user_row = self.session_store._conn.execute("SELECT is_local FROM users WHERE id=?", (user_id,)).fetchone()
            local_owner = bool(user_row and user_row[0])
        for root_label, root_value in roots:
            root = Path(root_value)
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                if any(part in IGNORED_IMPORT_DIRS for part in path.parts):
                    continue
                # Office lock files (for example ``~$agenda.pptx``) are
                # transient implementation details, not user documents.
                if path.name.startswith("~$"):
                    continue
                try:
                    resolved = path.resolve(strict=True)
                    relative = resolved.relative_to(root.resolve()).as_posix()
                except (OSError, RuntimeError, ValueError):
                    continue
                if user_id and root_label == "visual_assets" and PurePosixPath(relative).parts[:1] != (user_id,):
                    continue
                # Loose output files have no owner metadata.  They are only
                # discoverable by the explicitly designated local owner;
                # remote users receive their own artifact rows instead.
                if user_id and not local_owner and root_label not in {"visual_assets", "knowledge"}:
                    # Additional roots (including the sibling media trees
                    # auto-discovered for the local workspace) have no
                    # per-file owner metadata.  Never expose them to a
                    # non-local account.
                    continue
                if resolved in seen or resolved.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
                    continue
                if any(
                    canonical_root != root.resolve() and canonical_root in resolved.parents
                    for canonical_root in canonical_roots
                ):
                    continue
                if root_label == "visual_assets" and path.name.startswith(("thumbnail", "rendition-")):
                    continue
                if root_label in {"outputs", "output"} and relative.startswith("playwright/"):
                    continue
                kind = self._kind(resolved)
                if kind == "other":
                    continue
                seen.add(resolved)
                try:
                    digest = _sha_file(resolved)
                except OSError:
                    digest = ""
                records.append({
                    "root": root_label,
                    "relative_path": relative,
                    "kind": kind,
                    "sha256": digest,
                    "size_bytes": resolved.stat().st_size,
                    "_base_path": str(root.resolve()),
                })
        return records

    def inventory(self, user_id: str, *, project_id: str = "", persist: bool = True) -> dict[str, Any]:
        if project_id and not self.session_store.get_project(project_id, user_id):
            raise ValueError("project 不屬於目前使用者")
        if persist:
            self._reconcile_derived_assets(user_id, project_id)
        asset_sql = "SELECT * FROM visual_assets WHERE user_id=?"
        asset_params: tuple[Any, ...] = (user_id,)
        if project_id:
            asset_sql += " AND project_id=?"
            asset_params += (project_id,)
        all_assets = self.visual_store._rows(asset_sql, asset_params)
        assets = [row for row in all_assets if row.get("source") != "derived_thumbnail"]
        visual_scope = self._owned_visual_scope(user_id, project_id)
        snapshot = self.session_store.export_sync_snapshot(user_id, project_id=project_id or None, limit=5000)
        knowledge = self._knowledge_documents()
        filesystem = self._filesystem_candidates(user_id)

        # Include retained derivative rows in the path set so their generated
        # files are not reclassified as loose imports after reconciliation.
        registered_paths = {
            str(Path(str(row.get("storage_path") or "")).resolve())
            for row in all_assets if row.get("storage_path")
        }
        registered_source_keys = {key for key in (self._source_key(row) for row in all_assets) if key}
        registered_source_keys.update(self._lineage_source_keys(user_id))
        source_sha = self._source_sha_map(all_assets)
        unregistered = [
            row for row in filesystem
            if row["root"] != "knowledge"
            and self._content_is_unregistered(row, registered_source_keys, registered_paths, source_sha)
        ]
        asset_types = Counter(str(row.get("asset_type") or "image") for row in assets)
        filesystem_types = Counter(row["kind"] for row in unregistered)
        hashes: dict[str, list[str]] = defaultdict(list)
        for row in filesystem:
            if row["sha256"]:
                hashes[row["sha256"]].append(f"{row['root']}:{row['relative_path']}")
        duplicate_groups = [paths for paths in hashes.values() if len(paths) > 1]

        pending_statuses = ("pending_review", "probable", "conflicted")
        placeholders = ",".join("?" for _ in pending_statuses)
        pending = sum(1 for row in assets if _status(row.get("review_status")) in pending_statuses)
        pending += int(self.visual_store._conn.execute(
            f"SELECT COUNT(*) FROM visual_observations o JOIN visual_assets a ON a.id=o.asset_id WHERE a.user_id=? AND o.status IN ({placeholders})" + (" AND a.project_id=?" if project_id else ""),
            (user_id,) + pending_statuses + ((project_id,) if project_id else ()),
        ).fetchone()[0])
        for table, kind in (("visual_people", "person"), ("visual_clubs", "club"), ("visual_events", "event")):
            pending += self._scope_count(table, visual_scope[kind], f"status IN ({placeholders})", pending_statuses)
        pending += int(self.visual_store._conn.execute(
            f"SELECT COUNT(*) FROM review_queue WHERE owner_id=? AND status IN ({placeholders})", (user_id,) + pending_statuses,
        ).fetchone()[0])
        pending += int(self.visual_store._conn.execute(
            f"SELECT COUNT(*) FROM entities WHERE owner_id=? AND verification_status IN ({placeholders})", (user_id,) + pending_statuses,
        ).fetchone()[0])
        failed_asset_ids = {
            str(row["id"]) for row in assets
            if _status(row.get("review_status")) == "failed" or row.get("processing_state") == "failed"
        }
        failed_asset_ids.update(
            str(row[0]) for row in self.visual_store._conn.execute(
                "SELECT DISTINCT a.id FROM visual_analysis_jobs j JOIN visual_assets a ON a.id=j.asset_id WHERE a.user_id=? AND j.status='failed'" + (" AND a.project_id=?" if project_id else ""),
                (user_id, project_id) if project_id else (user_id,),
            )
        )
        sync_failed = int(self.visual_store._conn.execute(
            "SELECT COUNT(*) FROM backend_sync_items i JOIN sync_runs r ON r.id=i.sync_run_id WHERE r.owner_id=? AND i.status='failed'",
            (user_id,),
        ).fetchone()[0])
        dates = self.visual_store._conn.execute(
            "SELECT COUNT(DISTINCT value) FROM visual_date_candidates d JOIN visual_assets a ON a.id=d.asset_id WHERE a.user_id=? AND d.status!='failed'",
            (user_id,),
        ).fetchone()[0]
        activity_dates = {
            str(row.get("start_at") or "")[:10] for row in snapshot["activities"] if row.get("start_at")
        }
        event_dates = {
            str(row[0]) for row in self.visual_store._conn.execute(
                "SELECT event_date FROM visual_events WHERE event_date!='' AND (school_id IN (SELECT school_id FROM visual_assets WHERE user_id=?) OR id IN (SELECT entity_id FROM visual_observations o JOIN visual_assets a ON a.id=o.asset_id WHERE a.user_id=?))",
                (user_id, user_id),
            )
        }
        missing_sources = sum(1 for row in assets if not str(row.get("source") or "").strip())
        missing_sources += sum(
            1 for row in snapshot["artifacts"]
            if not row.get("drive_url") and not (_json(row.get("meta"), {}) or {}).get("source")
        )
        statistics = {
            "images": asset_types["image"] + filesystem_types["image"],
            "videos": asset_types["video"] + filesystem_types["video"],
            "documents": len(knowledge) + len(snapshot["artifacts"]) + asset_types["document"] + filesystem_types["document"],
            "artifacts": len(snapshot["artifacts"]),
            "knowledge_sources": len(knowledge),
            "clubs": self._scope_count("visual_clubs", visual_scope["club"]),
            "schools": self._scope_count("visual_schools", visual_scope["school"]),
            "people": self._scope_count("visual_people", visual_scope["person"]),
            "events": self._scope_count("visual_events", visual_scope["event"]) + len(snapshot["activities"]),
            "dates": int(dates) + len(activity_dates | event_dates),
            "duplicate_assets": sum(1 for row in assets if row.get("duplicate_of")) + len(duplicate_groups),
            "missing_sources": missing_sources,
            "pending_review": pending,
            "analysis_failed": len(failed_asset_ids),
        }
        breakdown = {
            "registered_visual_assets": dict(asset_types),
            "unregistered_filesystem_candidates": dict(filesystem_types),
            "projects": len(snapshot["projects"]),
            "activities": len(snapshot["activities"]),
            "activity_tasks": len(snapshot["activity_tasks"]),
            "research_sources": len(snapshot["research_sources"]),
            "sessions_excluded_from_mapping": int(self.session_store._conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id=?", (user_id,)).fetchone()[0]),
            "messages_excluded_from_mapping": int(self.session_store._conn.execute("SELECT COUNT(*) FROM messages m JOIN sessions s ON s.id=m.session_id WHERE s.user_id=?", (user_id,)).fetchone()[0]),
            "social_reference_files": sum(1 for row in knowledge if str(row["relative_path"]).startswith("社群/")),
            "sync_failed": sync_failed,
            "duplicate_groups": duplicate_groups[:20],
        }
        manifest = {
            "roots": sorted({str(row["root"]) for row in filesystem}),
            "filesystem_candidates": len(filesystem),
            "unregistered_candidates": len(unregistered),
            "sample_unregistered": [{key: row[key] for key in ("root", "relative_path", "kind", "sha256")} for row in unregistered[:20]],
            "absolute_paths_exposed": False,
        }
        report_id = new_id("inventory")
        if persist:
            stamp = now()
            with self.visual_store._lock:
                self.visual_store._conn.execute(
                    "INSERT INTO data_inventory_reports(id,owner_id,project_id,statistics,breakdown,manifest,status,created_at,updated_at) VALUES(?,?,?,?,? ,?,'verified',?,?)",
                    (report_id, user_id, project_id, dumps(statistics), dumps(breakdown), dumps(manifest), stamp, stamp),
                )
                self.visual_store._conn.commit()
        return {"id": report_id, "status": "verified", "statistics": statistics, "breakdown": breakdown, "manifest": manifest, "created_at": now()}

    def latest_inventory(self, user_id: str, project_id: str = "") -> dict[str, Any] | None:
        rows = self.visual_store._rows(
            "SELECT * FROM data_inventory_reports WHERE owner_id=? AND project_id=? ORDER BY created_at DESC LIMIT 1",
            (user_id, project_id),
        )
        if not rows:
            return None
        row = rows[0]
        for key in ("statistics", "breakdown", "manifest"):
            row[key] = _json(row.get(key), {})
        return row

    def _source(self, owner_id: str, *, source_type: str, original_file: str, source_url: str = "", captured_at: str = "", reliability: float = 0.0) -> str:
        source_id = _stable_id("source", owner_id, source_type, source_url, original_file)
        with self.visual_store._lock:
            self.visual_store._conn.execute(
                "INSERT INTO sources(id,source_type,source_url,original_file,captured_at,reliability_score,owner_id,created_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET source_url=excluded.source_url,original_file=excluded.original_file,captured_at=excluded.captured_at,reliability_score=excluded.reliability_score",
                (source_id, source_type, source_url, original_file, captured_at, reliability, owner_id, now()),
            )
        return source_id

    def _lineage(self, owner_id: str, resource_type: str, resource_id: str, *, original_path: str = "", original_source: str = "", source_id: str = "", sha256: str = "", confidence: float = 0.0, verification_status: str = "pending_review", updated_at: str = "", metadata: dict[str, Any] | None = None) -> None:
        self.visual_store._conn.execute(
            "INSERT INTO data_lineage(resource_type,resource_id,owner_id,original_id,original_path,original_source,source_id,sha256,migration_version,confidence,verification_status,imported_at,source_updated_at,metadata) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(resource_type,resource_id,owner_id) DO UPDATE SET original_path=excluded.original_path,original_source=excluded.original_source,source_id=excluded.source_id,sha256=excluded.sha256,migration_version=excluded.migration_version,confidence=excluded.confidence,verification_status=excluded.verification_status,source_updated_at=excluded.source_updated_at,metadata=excluded.metadata",
            (resource_type, resource_id, owner_id, resource_id, original_path, original_source, source_id, sha256, MIGRATION_VERSION, confidence, _status(verification_status), now(), updated_at, dumps(metadata or {})),
        )

    @staticmethod
    def _mapped_entity_id(owner_id: str, kind: str, original_id: str) -> str:
        return _stable_id("mapped_entity", owner_id, kind, original_id)

    def _entity(self, owner_id: str, entity_id: str, kind: str, name: str, *, aliases: Iterable[str] = (), description: str = "", confidence: float = 0.0, status: str = "pending_review", source_id: str = "") -> str:
        mapped_id = self._mapped_entity_id(owner_id, kind, entity_id)
        self.visual_store._conn.execute(
            "INSERT INTO entities(id,type,name,aliases,description,confidence,verification_status,owner_id,source_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET type=excluded.type,name=excluded.name,aliases=excluded.aliases,description=excluded.description,confidence=excluded.confidence,verification_status=excluded.verification_status,owner_id=excluded.owner_id,source_id=excluded.source_id,updated_at=excluded.updated_at",
            (mapped_id, kind, name, dumps(list(aliases)), description, confidence, _status(status), owner_id, source_id, now(), now()),
        )
        return mapped_id

    def _edge(self, owner_id: str, from_type: str, from_id: str, to_type: str, to_id: str, relation: str, *, confidence: float, status: str, evidence: dict[str, Any], source_id: str = "") -> None:
        edge_id = _stable_id("edge", owner_id, from_type, from_id, to_type, to_id, relation)
        self.visual_store._conn.execute(
            "INSERT INTO entity_relationships(id,owner_id,from_type,from_id,to_type,to_id,relation_type,confidence,verification_status,evidence,source_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(owner_id,from_type,from_id,to_type,to_id,relation_type) DO UPDATE SET confidence=excluded.confidence,verification_status=excluded.verification_status,evidence=excluded.evidence,source_id=excluded.source_id,updated_at=excluded.updated_at",
            (edge_id, owner_id, from_type, from_id, to_type, to_id, relation, confidence, _status(status), dumps(evidence), source_id, now(), now()),
        )

    def _review(self, owner_id: str, asset_id: str, proposed: dict[str, Any], confidence: float, status: str = "pending_review") -> None:
        review_id = _stable_id("review", owner_id, asset_id, dumps(proposed))
        self.visual_store._conn.execute(
            "INSERT OR IGNORE INTO review_queue(id,asset_id,proposed_change,confidence,status,owner_id,created_at) VALUES(?,?,?,?,?,?,?)",
            (review_id, asset_id, dumps(proposed), confidence, _status(status), owner_id, now()),
        )

    def _resource_catalog(self, user_id: str, project_id: str) -> list[tuple[str, str, dict[str, Any]]]:
        catalog: list[tuple[str, str, dict[str, Any]]] = []
        visual_scope = self._owned_visual_scope(user_id, project_id)
        for table, resource_type in (
            ("visual_assets", "visual_asset"), ("visual_schools", "school"), ("visual_clubs", "club"),
            ("visual_people", "person"), ("visual_scenes", "scene"), ("visual_events", "event"),
        ):
            if table == "visual_assets":
                rows = self.visual_store._rows("SELECT * FROM visual_assets WHERE user_id=? AND COALESCE(source,'')!='derived_thumbnail'" + (" AND project_id=?" if project_id else ""), (user_id, project_id) if project_id else (user_id,))
            else:
                ids = visual_scope[resource_type]
                if not ids:
                    rows = []
                else:
                    placeholders = ",".join("?" for _ in ids)
                    rows = self.visual_store._rows(f"SELECT * FROM {table} WHERE id IN ({placeholders})", tuple(ids))
            catalog.extend((resource_type, str(row["id"]), row) for row in rows)
        for document in self._knowledge_documents():
            catalog.append(("knowledge_document", str(document["id"]), document))
        visual_path_sql = "SELECT storage_path FROM visual_assets WHERE user_id=?"
        visual_path_params: tuple[Any, ...] = (user_id,)
        if project_id:
            visual_path_sql += " AND project_id=?"
            visual_path_params += (project_id,)
        registered_paths = {
            str(Path(str(row["storage_path"] or "")).resolve())
            for row in self.visual_store._conn.execute(visual_path_sql, visual_path_params).fetchall()
            if row["storage_path"]
        }
        registered_source_keys = {
            key for key in (
                self._source_key(row)
                for row in self.visual_store._rows(
                    "SELECT source_metadata FROM visual_assets WHERE user_id=?" + (" AND project_id=?" if project_id else ""),
                    (user_id, project_id) if project_id else (user_id,),
                )
            ) if key
        }
        registered_source_keys.update(self._lineage_source_keys(user_id))
        source_sha = {
            key: str(row.get("sha256") or "")
            for row in self.visual_store._rows(
                "SELECT source_metadata,sha256 FROM visual_assets WHERE user_id=?" + (" AND project_id=?" if project_id else ""),
                (user_id, project_id) if project_id else (user_id,),
            )
            if (key := self._source_key(row))
        }
        for candidate in self._filesystem_candidates(user_id):
            if candidate["root"] == "knowledge":
                continue
            root = Path(str(candidate.get("_base_path") or ""))
            if not root:
                continue
            resolved = (root / str(candidate["relative_path"])).resolve()
            source_key = self._filesystem_source_key(candidate)
            known_sha = source_sha.get(source_key, "")
            if known_sha and known_sha == str(candidate.get("sha256") or ""):
                continue
            if not known_sha and (str(resolved) in registered_paths or source_key in registered_source_keys):
                continue
            previous = ""
            if known_sha and known_sha != str(candidate.get("sha256") or ""):
                previous_rows = self.visual_store._rows(
                    "SELECT id FROM visual_assets WHERE user_id=? AND sha256=? ORDER BY created_at DESC LIMIT 1",
                    (user_id, known_sha),
                )
                previous = str(previous_rows[0]["id"]) if previous_rows else ""
            candidate = {**candidate, "path": resolved, "replaces_asset_id": previous}
            resource_id = _stable_id("loose_file", user_id, candidate["root"], candidate["relative_path"], candidate["sha256"])
            catalog.append(("filesystem_asset", resource_id, candidate))
        snapshot = self.session_store.export_sync_snapshot(user_id, project_id=project_id or None, limit=5000)
        for key, resource_type in (("artifacts", "artifact"), ("activities", "activity"), ("research_sources", "research_source")):
            catalog.extend((resource_type, str(row["id"]), row) for row in snapshot[key])
        return catalog

    @staticmethod
    def _directory_taxonomy(row: dict[str, Any]) -> dict[str, Any]:
        """Derive conservative, reviewable labels from an organized folder tree.

        The sibling media trees contain useful human curation (for example
        ``淡大劇本/場景/上學期社課``).  This is evidence about how a file was
        organized, not proof of a person's identity or a formally verified
        event.  Keep the result as probable observations with a relative path
        witness so a reviewer can promote or ignore it later.
        """
        root = str(row.get("root") or "").strip()
        relative = str(row.get("relative_path") or "").replace("\\", "/").strip("/")
        segments = [part for part in PurePosixPath(relative).parts if part and part not in {".", ".."}]
        # A filename is often IMG_####; categories are represented by parent
        # folders, so use parent segments for event candidates.
        folders = segments[:-1] if segments else []
        searchable = " ".join([root, *segments])
        evidence = {
            "source": "directory_taxonomy",
            "taxonomy_version": "directory-v1",
            "root": root,
            "relative_path": relative,
        }
        observations: list[dict[str, Any]] = []
        tku_roots = {"淡大劇本", "淡大所有照片", "招生影片"}
        if root in tku_roots:
            observations.append({"entity_type": "school", "label": "淡江大學", "confidence": 0.94})
        # The script/scene tree is explicitly the 淡大禪學社 production tree;
        # keep this probable and never write a verified club identity.
        if root in {"淡大劇本", "招生影片"}:
            observations.append({"entity_type": "club", "label": "領袖禪學社", "confidence": 0.88})

        scene_rules = (
            ("茶會", "茶會"), ("社課", "社課"), ("教室", "教室"),
            ("禪堂", "禪堂"), ("舞台", "舞台"), ("講座", "講座"),
            ("演講", "講座"), ("校園", "校園"), ("空景", "校園"),
            ("報到", "報到區"), ("戶外", "戶外"), ("出遊", "戶外"),
            ("捷運", "戶外"), ("克難坡", "戶外"), ("排球場", "戶外"),
            ("大梵寺", "戶外"), ("聚餐", "聚餐"), ("湯圓", "聚餐"),
            ("場佈", "活動現場"), ("擺攤", "活動現場"), ("社評", "活動現場"),
            ("社辦", "活動現場"), ("倒數", "活動現場"), ("校友會", "活動現場"),
        )
        scene_labels: set[str] = set()
        for keyword, label in scene_rules:
            if keyword in searchable:
                scene_labels.add(label)
        for label in sorted(scene_labels):
            observations.append({"entity_type": "scene", "label": label, "confidence": 0.84})

        event_keywords = (
            "茶會", "社課", "演講", "講座", "社評", "期初", "期中", "期末",
            "聚餐", "出遊", "校友會", "場佈", "擺攤", "社辦", "呼吸", "倒數",
            "最後一堂", "第八幕", "校慶",
        )
        event_label = next(
            (segment for segment in reversed(folders) if any(keyword in segment for keyword in event_keywords) and not segment.startswith("_")),
            "",
        )
        if event_label:
            observations.append({"entity_type": "event", "label": event_label[:300], "confidence": 0.78})

        # Compact Gregorian dates such as 20251206 and ordinary YYYY-MM-DD
        # names are safe date candidates; academic-year labels without a month
        # remain only in the event/path evidence to avoid invented dates.
        date_candidates: list[str] = []
        for match in re.finditer(r"(?<!\d)(20\d{2})[-_年]?(\d{1,2})[-_月]?(\d{1,2})(?:日)?(?!\d)", searchable):
            year, month, day = match.groups()
            date_candidates.append(f"{year}-{month}-{day}")
        return {"observations": observations, "dates": list(dict.fromkeys(date_candidates)), "evidence": evidence}

    def _apply_directory_taxonomy(self, user_id: str, asset_id: str, row: dict[str, Any]) -> None:
        taxonomy = self._directory_taxonomy(row)
        evidence = taxonomy["evidence"]
        for observation in taxonomy["observations"]:
            self.visual_store.add_observation(
                asset_id,
                str(observation["entity_type"]),
                label=str(observation["label"]),
                status="probable",
                confidence=float(observation["confidence"]),
                source="directory_taxonomy_v1",
                evidence=evidence,
            )
        for value in taxonomy["dates"]:
            self.visual_store.add_date(
                asset_id,
                value,
                source="directory_taxonomy_v1",
                confidence=0.78,
                status="probable",
                evidence=json.dumps(evidence, ensure_ascii=False),
            )

    def _map_resource(self, user_id: str, resource_type: str, resource_id: str, row: dict[str, Any], project_id: str = "") -> None:
        if resource_type == "filesystem_asset":
            path = Path(str(row.get("path") or "")).resolve(strict=True)
            if not path.is_file() or _sha_file(path) != str(row.get("sha256") or ""):
                raise ValueError("來源檔案在盤點後已變更")
            relative = f"{row.get('root')}:{row.get('relative_path')}"
            existing_lineage = self.visual_store._rows(
                "SELECT metadata FROM data_lineage WHERE resource_type=? AND resource_id=? AND owner_id=?",
                (resource_type, resource_id, user_id),
            )
            mapped_asset_id = ""
            if existing_lineage:
                mapped_asset_id = str((_json(existing_lineage[0].get("metadata"), {}) or {}).get("mapped_asset_id") or "")
                if mapped_asset_id and not self.visual_store.owned_asset(mapped_asset_id, user_id):
                    mapped_asset_id = ""
            if not mapped_asset_id and row.get("sha256"):
                duplicate = self.visual_store._rows(
                    "SELECT id FROM visual_assets WHERE user_id=? AND sha256=? ORDER BY created_at LIMIT 1",
                    (user_id, row["sha256"]),
                )
                mapped_asset_id = str(duplicate[0]["id"]) if duplicate else ""
            mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if mime_type not in ALLOWED_MIME:
                mime_type = next((candidate for candidate, suffix in ALLOWED_MIME.items() if suffix == path.suffix.lower()), mime_type)
            if mapped_asset_id and project_id:
                self.visual_store._conn.execute(
                    "UPDATE visual_assets SET project_id=?,updated_at=? WHERE id=? AND user_id=? AND (project_id='' OR project_id IS NULL)",
                    (project_id[:160], now(), mapped_asset_id, user_id),
                )
            if not mapped_asset_id and mime_type in ALLOWED_MIME:
                # Keep large local videos and already-organized photo trees in
                # place.  We inspect image/document bytes for metadata, but
                # only create managed thumbnails; no second multi-GB binary
                # tree is written under data/visual-assets.
                inspection_bytes = path.read_bytes() if row.get("kind") != "video" else b"external-video"
                imported = self.visual_store.create_asset(
                    user_id=user_id, filename=path.name, mime_type=mime_type, content=inspection_bytes,
                    source="existing_data_import", relative_path=str(row.get("relative_path") or ""),
                    source_metadata={"root": row.get("root"), "relative_path": row.get("relative_path"), "sha256": row.get("sha256")},
                    external_path=str(path), sha256_override=str(row.get("sha256") or ""),
                    project_id=project_id,
                    supersedes_asset_id=str(row.get("replaces_asset_id") or ""),
                )
                mapped_asset_id = str(imported["asset_id"])
            source_id = self._source(
                user_id, source_type="existing_filesystem", original_file=relative,
                captured_at=str(path.stat().st_mtime_ns), reliability=1.0,
            )
            status = "pending_review" if mapped_asset_id else "failed"
            self._lineage(
                user_id, resource_type, resource_id, original_path=relative,
                original_source="existing_filesystem", source_id=source_id,
                sha256=str(row.get("sha256") or ""), confidence=1.0,
                verification_status=status, updated_at=str(path.stat().st_mtime_ns),
                metadata={"root": row.get("root"), "relative_path": row.get("relative_path"), "mapped_asset_id": mapped_asset_id, "mime_type": mime_type},
            )
            if not mapped_asset_id:
                raise ValueError(f"不支援匯入的素材格式：{path.suffix.lower() or mime_type}")
            # Apply the human-curated folder taxonomy after the asset exists.
            # It is idempotent and remains probable/reviewable evidence.
            self._apply_directory_taxonomy(user_id, mapped_asset_id, row)
            self._edge(
                user_id, "filesystem_file", resource_id, "asset", mapped_asset_id, "imported_as",
                confidence=1.0, status="verified", evidence={"sha256": row.get("sha256"), "relative_path": row.get("relative_path")}, source_id=source_id,
            )
            asset_row = self.visual_store._rows("SELECT * FROM visual_assets WHERE id=? AND user_id=?", (mapped_asset_id, user_id))[0]
            self._map_resource(user_id, "visual_asset", mapped_asset_id, asset_row, project_id=project_id)
            return

        if resource_type == "visual_asset":
            original_path = str(row.get("storage_path") or "")
            source_id = self._source(user_id, source_type=str(row.get("source") or "visual_asset"), original_file=original_path, captured_at=str(row.get("uploaded_at") or ""), reliability=1.0 if row.get("source") else 0.3)
            self._lineage(user_id, resource_type, resource_id, original_path=original_path, original_source=str(row.get("source") or ""), source_id=source_id, sha256=str(row.get("sha256") or ""), confidence=float(row.get("overall_confidence") or 0), verification_status=str(row.get("review_status") or "pending_review"), updated_at=str(row.get("updated_at") or ""), metadata={"relative_path": row.get("relative_path") or "", "manifest_id": row.get("manifest_id") or ""})
            source_metadata = _json(row.get("source_metadata"), {})
            if isinstance(source_metadata, dict) and source_metadata.get("root") and source_metadata.get("relative_path"):
                # Existing external imports may already have lineage, so apply
                # the folder taxonomy while reconciling the canonical asset
                # branch as well as during the first loose-file mapping.
                self._apply_directory_taxonomy(
                    user_id,
                    resource_id,
                    {"root": source_metadata.get("root"), "relative_path": source_metadata.get("relative_path")},
                )
            for kind, entity_id, relation in (("school", row.get("school_id"), "belongs_to_school"), ("club", row.get("club_id"), "depicts_club")):
                if entity_id:
                    mapped_id = self._mapped_entity_id(user_id, kind, str(entity_id))
                    self.visual_store._conn.execute(
                        "INSERT OR REPLACE INTO asset_entities(asset_id,entity_id,relation_type,confidence,evidence,verified_by,created_at) VALUES(?,?,?,?,?,?,?)",
                        (resource_id, mapped_id, relation, 1.0, dumps({"source": "asset_metadata", "source_id": source_id, "original_entity_id": entity_id}), "user_input", now()),
                    )
                    self._edge(user_id, "asset", resource_id, kind, mapped_id, relation, confidence=1.0, status="verified", evidence={"source": "asset_metadata", "original_entity_id": entity_id}, source_id=source_id)
            observations = self.visual_store._rows("SELECT * FROM visual_observations WHERE asset_id=? AND review_action!='ignored'", (resource_id,))
            for observation in observations:
                kind = str(observation.get("entity_type") or "object")
                entity_id = str(observation.get("entity_id") or "")
                label = str(observation.get("label") or "")
                observation_status = _status(observation.get("status"))
                if not entity_id:
                    if kind == "person":
                        label = "未知人物"
                        entity_id = _stable_id("unknown_person", user_id, resource_id, observation["id"])
                    elif label:
                        entity_id = _stable_id("entity", user_id, kind, row.get("school_id"), label)
                if not entity_id:
                    continue
                normalized_kind = kind if kind in {"person", "club", "school", "event", "scene", "place", "object"} else "object"
                mapped_id = self._entity(user_id, entity_id, normalized_kind, label or "未命名候選", confidence=float(observation.get("confidence") or 0), status=observation_status, source_id=source_id)
                evidence = _json(observation.get("evidence"), {})
                self.visual_store._conn.execute(
                    "INSERT OR REPLACE INTO asset_entities(asset_id,entity_id,relation_type,confidence,evidence,verified_by,created_at) VALUES(?,?,?,?,?,?,?)",
                    (resource_id, mapped_id, kind, float(observation.get("confidence") or 0), dumps({"evidence": evidence, "original_entity_id": entity_id}), str(observation.get("verified_by") or ""), now()),
                )
                self._edge(user_id, "asset", resource_id, kind, mapped_id, f"depicts_{kind}", confidence=float(observation.get("confidence") or 0), status=observation_status, evidence={"observation_id": observation["id"], "evidence": evidence, "original_entity_id": entity_id}, source_id=source_id)
                if observation_status != "verified":
                    self._review(user_id, resource_id, {"type": "entity_observation", "observation_id": observation["id"], "entity_type": kind, "entity_id": mapped_id, "original_entity_id": entity_id, "label": label, "evidence": evidence}, float(observation.get("confidence") or 0), observation_status)
            vectors = (str(row.get("semantic_embedding") or "[]"), str(row.get("color_embedding") or "[]"))
            if vectors != ("[]", "[]"):
                self.visual_store._conn.execute(
                    "INSERT INTO embeddings(id,asset_id,text_embedding,image_embedding,model,owner_id,created_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(asset_id,model) DO UPDATE SET text_embedding=excluded.text_embedding,image_embedding=excluded.image_embedding",
                    (_stable_id("embedding", resource_id, "local-deterministic-v1"), resource_id, vectors[0], vectors[1], "local-deterministic-v1", user_id, now()),
                )
            if _status(row.get("review_status")) != "verified":
                self._review(user_id, resource_id, {"type": "asset_status", "analysis_status": row.get("analysis_status"), "review_status": row.get("review_status")}, float(row.get("overall_confidence") or 0), str(row.get("review_status") or "pending_review"))
            return

        if resource_type in {"school", "club", "person", "scene", "event"}:
            name = str(row.get("canonical_name") or row.get("name") or "")
            source_data = _json(row.get("source"), {})
            source_id = self._source(user_id, source_type="canonical_visual_entity", original_file=f"{resource_type}:{resource_id}", reliability=1.0 if source_data else 0.7)
            status = "verified" if resource_type in {"school", "scene"} else _status(row.get("status"))
            confidence = float(row.get("confidence") or (1.0 if status == "verified" else 0.7))
            mapped_id = self._entity(user_id, resource_id, resource_type, name, aliases=_json(row.get("aliases"), []), confidence=confidence, status=status, source_id=source_id)
            self._lineage(user_id, resource_type, resource_id, original_source="canonical_visual_entity", source_id=source_id, confidence=confidence, verification_status=status, updated_at=str(row.get("updated_at") or row.get("created_at") or ""))
            if row.get("school_id"):
                school_id = self._mapped_entity_id(user_id, "school", str(row["school_id"]))
                self._edge(user_id, resource_type, mapped_id, "school", school_id, "belongs_to_school", confidence=confidence, status=status, evidence={"field": "school_id", "original_entity_id": row["school_id"]}, source_id=source_id)
            if row.get("club_id"):
                club_id = self._mapped_entity_id(user_id, "club", str(row["club_id"]))
                self._edge(user_id, resource_type, mapped_id, "club", club_id, "belongs_to_club", confidence=confidence, status=status, evidence={"field": "club_id", "original_entity_id": row["club_id"]}, source_id=source_id)
            if resource_type == "event":
                self.visual_store._conn.execute(
                    "INSERT INTO events(id,name,date,time,location,club_id,school_id,verification_status,owner_id,source_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,date=excluded.date,time=excluded.time,location=excluded.location,club_id=excluded.club_id,school_id=excluded.school_id,verification_status=excluded.verification_status,source_id=excluded.source_id,updated_at=excluded.updated_at",
                    (mapped_id, name, row.get("event_date") or "", row.get("start_time") or "", row.get("location") or "", self._mapped_entity_id(user_id, "club", str(row.get("club_id") or "")) if row.get("club_id") else "", self._mapped_entity_id(user_id, "school", str(row.get("school_id") or "")) if row.get("school_id") else "", status, user_id, source_id, row.get("created_at") or now(), row.get("updated_at") or now()),
                )
                for kind, value, relation in (("date", row.get("event_date"), "occurs_on"), ("place", row.get("location"), "occurs_at")):
                    if value:
                        related_id = _stable_id("entity", user_id, kind, row.get("school_id"), value)
                        mapped_related_id = self._entity(user_id, related_id, kind, str(value), confidence=confidence, status=status, source_id=source_id)
                        self._edge(user_id, "event", mapped_id, kind, mapped_related_id, relation, confidence=confidence, status=status, evidence={"field": "event_date" if kind == "date" else "location"}, source_id=source_id)
            return

        if resource_type == "knowledge_document":
            metadata = row.get("metadata") or {}
            source_type = str(metadata.get("source_type") or "knowledge_file")
            source_id = self._source(user_id, source_type=source_type, original_file=str(row["relative_path"]), source_url=str(metadata.get("source_url") or ""), captured_at=str(metadata.get("captured_at") or ""), reliability=float(metadata.get("credibility") or (0.9 if source_type in {"curated", "playbook"} else 0.65)))
            status = "verified" if metadata.get("verification") in {"verified", "internal"} else "probable"
            self._lineage(user_id, resource_type, resource_id, original_path=str(row["relative_path"]), original_source=source_type, source_id=source_id, sha256=str(row["sha256"]), confidence=float(metadata.get("credibility") or 0.7), verification_status=status, updated_at=str(row.get("updated_at") or ""), metadata=metadata)
            target_id = str(metadata.get("entity_id") or "")
            organization = str(metadata.get("organization") or "")
            school = str(metadata.get("school") or "")
            if target_id and organization:
                mapped_target_id = self._entity(user_id, target_id, "club", organization, confidence=1.0 if metadata.get("source_url") else 0.8, status="verified" if metadata.get("source_url") else "probable", source_id=source_id)
                self._edge(user_id, "document", resource_id, "club", mapped_target_id, "documents_club", confidence=0.95, status=status, evidence={"metadata": "entity_id", "school": school, "original_entity_id": target_id}, source_id=source_id)
            activity = str(metadata.get("activity") or "")
            if activity:
                event_id = _stable_id("event_candidate", user_id, school, target_id, metadata.get("academic_year"), metadata.get("semester"), activity)
                mapped_event_id = self._entity(user_id, event_id, "event", activity, description="由文件 metadata 建立的活動候選，尚未自動合併正式活動", confidence=0.72, status="probable", source_id=source_id)
                self._edge(user_id, "document", resource_id, "event", mapped_event_id, "documents_event", confidence=0.72, status="probable", evidence={"activity": activity, "academic_year": metadata.get("academic_year"), "semester": metadata.get("semester"), "school": school}, source_id=source_id)
            return

        source_path = str(row.get("local_path") or row.get("source_file") or "")
        digest = ""
        if source_path:
            try:
                path = Path(source_path).resolve(strict=True)
                digest = _sha_file(path) if path.is_file() else ""
            except (OSError, RuntimeError):
                pass
        source_id = self._source(user_id, source_type=resource_type, original_file=source_path or resource_id, source_url=str(row.get("url") or row.get("drive_url") or ""), reliability=float(row.get("credibility") or 0.8))
        status = _status(row.get("verification") or ("verified" if resource_type in {"artifact", "activity"} else "pending_review"))
        self._lineage(user_id, resource_type, resource_id, original_path=source_path, original_source=resource_type, source_id=source_id, sha256=digest, confidence=float(row.get("credibility") or (1.0 if status == "verified" else 0.5)), verification_status=status, updated_at=str(row.get("updated_at") or row.get("created_at") or ""))

    def run(self, user_id: str, *, idempotency_key: str, project_id: str = "", resource_filter: set[tuple[str, str]] | None = None, resumed_from: str = "") -> dict[str, Any]:
        if project_id and not self.session_store.get_project(project_id, user_id):
            raise ValueError("project 不屬於目前使用者")
        raw_key = idempotency_key.strip()
        if len(raw_key) < 8:
            raise ValueError("idempotency_key 至少需要 8 個字元")
        source = "existing_data_organizer"
        scoped_key = f"{source}:{raw_key}"[:160]
        existing = self.visual_store._rows("SELECT id FROM sync_runs WHERE owner_id=? AND source=? AND idempotency_scope=?", (user_id, source, scoped_key))
        if existing:
            return self.get_run(str(existing[0]["id"]), user_id) or {}
        catalog = self._resource_catalog(user_id, project_id)
        if resource_filter is not None:
            catalog = [item for item in catalog if (item[0], item[1]) in resource_filter]
        run_id = new_id("sync")
        stamp = now()
        manifest = {"migration_version": MIGRATION_VERSION, "total": len(catalog), "project_id": project_id, "resource_types": dict(Counter(item[0] for item in catalog)), "non_destructive": True}
        with self.visual_store._lock:
            self.visual_store._conn.execute(
                "INSERT INTO sync_runs(id,source,owner_id,project_id,manifest,status,resumed_from,idempotency_key,idempotency_scope,created_at,updated_at) VALUES(?,?,?,?,?,'running',?,?,?,?,?)",
                (run_id, source, user_id, project_id, dumps(manifest), resumed_from, scoped_key, scoped_key, stamp, stamp),
            )
            for resource_type, resource_id, _row in catalog:
                self.visual_store._conn.execute(
                    "INSERT INTO backend_sync_items(id,sync_run_id,resource_type,resource_id,status,updated_at) VALUES(?,?,?,?,?,?)",
                    (new_id("syncitem"), run_id, resource_type, resource_id, "pending", stamp),
                )
            self.visual_store._conn.commit()
        for resource_type, resource_id, row in catalog:
            try:
                with self.visual_store._lock:
                    self._map_resource(user_id, resource_type, resource_id, row, project_id=project_id)
                    self.visual_store._conn.execute(
                        "UPDATE backend_sync_items SET status='completed',remote_id=?,attempts=attempts+1,updated_at=? WHERE sync_run_id=? AND resource_type=? AND resource_id=?",
                        (resource_id, now(), run_id, resource_type, resource_id),
                    )
                    self.visual_store._conn.commit()
            except Exception as exc:  # one bad item must not roll back completed mappings
                with self.visual_store._lock:
                    self.visual_store._conn.rollback()
                    self.visual_store._conn.execute(
                        "UPDATE backend_sync_items SET status='failed',error_code='mapping_failed',error_message=?,attempts=attempts+1,updated_at=? WHERE sync_run_id=? AND resource_type=? AND resource_id=?",
                        (str(exc)[:500], now(), run_id, resource_type, resource_id),
                    )
                    self.visual_store._conn.commit()
        with self.visual_store._lock:
            counts = self.visual_store._conn.execute("SELECT SUM(status='completed'),SUM(status='failed'),COUNT(*) FROM backend_sync_items WHERE sync_run_id=?", (run_id,)).fetchone()
            completed, failed, total = (int(value or 0) for value in counts)
            status = "completed" if completed == total else "partial" if completed else "failed"
            errors = self.visual_store._rows("SELECT resource_type,resource_id,error_code,error_message FROM backend_sync_items WHERE sync_run_id=? AND status='failed'", (run_id,))
            self.visual_store._conn.execute("UPDATE sync_runs SET status=?,imported_count=?,failed_count=?,error_log=?,updated_at=? WHERE id=?", (status, completed, failed, dumps(errors), now(), run_id))
            self.visual_store._conn.commit()
        return self.get_run(run_id, user_id) or {}

    def get_run(self, run_id: str, user_id: str) -> dict[str, Any] | None:
        rows = self.visual_store._rows("SELECT * FROM sync_runs WHERE id=? AND owner_id=? AND source='existing_data_organizer'", (run_id, user_id))
        if not rows:
            return None
        result = rows[0]
        result["manifest"] = _json(result.get("manifest"), {})
        result["error_log"] = _json(result.get("error_log"), [])
        result["items"] = self.visual_store._rows("SELECT resource_type,resource_id,status,error_code,error_message,attempts,updated_at FROM backend_sync_items WHERE sync_run_id=? ORDER BY resource_type,resource_id", (run_id,))
        return result

    def retry(self, run_id: str, user_id: str) -> dict[str, Any] | None:
        previous = self.get_run(run_id, user_id)
        if not previous:
            return None
        failed = {(str(item["resource_type"]), str(item["resource_id"])) for item in previous["items"] if item["status"] != "completed"}
        return self.run(user_id, idempotency_key=f"retry-{run_id}-{now()}", project_id=str(previous.get("project_id") or ""), resource_filter=failed, resumed_from=run_id)

    def list_queue(self, user_id: str, *, category: str = "pending_review", page: int = 1, limit: int = 30) -> tuple[list[dict[str, Any]], int]:
        category = category if category in {"verified", "pending_review", "conflicted", "failed", "unclassified", "missing_source", "duplicate"} else "pending_review"
        where = ["(a.user_id=? OR a.privacy IN ('shared','public'))"]
        params: list[Any] = [user_id]
        if category in VALID_STATUSES:
            where.append("a.review_status=?")
            params.append(category)
        elif category == "unclassified":
            where.append("a.school_id IS NULL AND a.club_id IS NULL AND NOT EXISTS (SELECT 1 FROM visual_observations o WHERE o.asset_id=a.id AND o.review_action!='ignored')")
        elif category == "missing_source":
            where.append("TRIM(a.source)=''")
        elif category == "duplicate":
            where.append("a.duplicate_of IS NOT NULL")
        condition = " AND ".join(where)
        total = int(self.visual_store._conn.execute(f"SELECT COUNT(*) FROM visual_assets a WHERE {condition}", tuple(params)).fetchone()[0])
        rows = self.visual_store._rows(
            f"SELECT a.*,COALESCE(s.canonical_name,'') school_name,COALESCE(c.name,'') club_name FROM visual_assets a LEFT JOIN visual_schools s ON s.id=a.school_id LEFT JOIN visual_clubs c ON c.id=a.club_id WHERE {condition} ORDER BY a.updated_at DESC LIMIT ? OFFSET ?",
            (*params, limit, (page - 1) * limit),
        )
        return [self.visual_store.public_asset(row) for row in rows], total
