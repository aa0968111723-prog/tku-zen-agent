"""Versioned, idempotent migrations for the visual asset subsystem.

The main application schema predates a migration ledger.  Visual assets use an
independent ledger so deployments can upgrade an existing SQLite database
without rebuilding it or losing the server-side processing state.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable


Migration = tuple[str, Callable[[sqlite3.Connection], None]]


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn: sqlite3.Connection, table: str, declaration: str) -> None:
    name = declaration.split()[0]
    if name not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {declaration}")


def _phase1_media_import(conn: sqlite3.Connection) -> None:
    for declaration in (
        "asset_type TEXT NOT NULL DEFAULT 'image'",
        "relative_path TEXT NOT NULL DEFAULT ''",
        "manifest_id TEXT NOT NULL DEFAULT ''",
        "aspect_ratio REAL NOT NULL DEFAULT 0",
        "people_count INTEGER NOT NULL DEFAULT 0",
        "overall_confidence REAL NOT NULL DEFAULT 0",
        "processing_state TEXT NOT NULL DEFAULT 'completed'",
        "last_confirmed_at TEXT NOT NULL DEFAULT ''",
        "supersedes_asset_id TEXT NOT NULL DEFAULT ''",
    ):
        _add_column(conn, "visual_assets", declaration)
    for declaration in (
        "attempts INTEGER NOT NULL DEFAULT 0",
        "next_retry_at TEXT NOT NULL DEFAULT ''",
        "idempotency_key TEXT NOT NULL DEFAULT ''",
    ):
        _add_column(conn, "visual_analysis_jobs", declaration)
    _add_column(conn, "visual_observations", "review_action TEXT NOT NULL DEFAULT ''")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS visual_imports (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            idempotency_key TEXT NOT NULL,
            root_name TEXT NOT NULL DEFAULT '',
            manifest_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending_review',
            total_count INTEGER NOT NULL DEFAULT 0,
            completed_count INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_synced_at TEXT NOT NULL DEFAULT '',
            UNIQUE(user_id, idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS idx_visual_imports_user
            ON visual_imports(user_id, updated_at DESC);

        CREATE TABLE IF NOT EXISTS visual_import_items (
            id TEXT PRIMARY KEY,
            import_id TEXT NOT NULL REFERENCES visual_imports(id) ON DELETE CASCADE,
            client_key TEXT NOT NULL,
            relative_path TEXT NOT NULL DEFAULT '',
            filename TEXT NOT NULL,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            last_modified TEXT NOT NULL DEFAULT '',
            sha256 TEXT NOT NULL DEFAULT '',
            asset_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending_review',
            error_code TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(import_id, client_key)
        );
        CREATE INDEX IF NOT EXISTS idx_visual_import_items_status
            ON visual_import_items(import_id, status, updated_at);
        """
    )

    # Canonical review vocabulary. Ignored is an action, not a truth status.
    mappings = {
        "confirmed": "verified",
        "possible": "probable",
        "pending": "pending_review",
        "conflict": "conflicted",
        "ignored": "pending_review",
    }
    for old, new in mappings.items():
        if old == "ignored":
            conn.execute("UPDATE visual_observations SET review_action='ignored' WHERE status=?", (old,))
        for table in ("visual_assets", "visual_observations", "visual_date_candidates", "visual_people", "visual_clubs", "visual_events"):
            if "status" in _columns(conn, table):
                conn.execute(f"UPDATE {table} SET status=? WHERE status=?", (new, old))
            elif table == "visual_assets":
                conn.execute("UPDATE visual_assets SET review_status=? WHERE review_status=?", (new, old))
    conn.execute("UPDATE visual_person_references SET status='verified' WHERE status='confirmed'")
    conn.execute("UPDATE visual_assets SET aspect_ratio=CASE WHEN height>0 THEN CAST(width AS REAL)/height ELSE 0 END")


def _insforge_backend(conn: sqlite3.Connection) -> None:
    """Add a stable local mapping layer for the optional InsForge backend.

    These tables intentionally do not replace the existing ``visual_*`` tables.
    They let sync runs be resumed/rolled back while keeping the local database
    authoritative when the external service is unavailable.
    """
    for declaration in (
        "project_id TEXT NOT NULL DEFAULT ''",
        "owner_id TEXT NOT NULL DEFAULT ''",
        "duration REAL NOT NULL DEFAULT 0",
        "permission_status TEXT NOT NULL DEFAULT 'private'",
    ):
        _add_column(conn, "visual_assets", declaration)
    conn.execute("UPDATE visual_assets SET owner_id=user_id WHERE owner_id='' OR owner_id IS NULL")
    conn.execute("UPDATE visual_assets SET permission_status=privacy WHERE permission_status='' OR permission_status IS NULL OR permission_status='private'")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sources (
            id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            original_file TEXT NOT NULL DEFAULT '',
            captured_at TEXT NOT NULL DEFAULT '',
            reliability_score REAL NOT NULL DEFAULT 0,
            owner_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_sources_owner ON sources(owner_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS entities (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            aliases TEXT NOT NULL DEFAULT '[]',
            description TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 0,
            verification_status TEXT NOT NULL DEFAULT 'pending_review',
            owner_id TEXT NOT NULL DEFAULT '',
            source_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_entities_scope ON entities(owner_id, type, name);

        CREATE TABLE IF NOT EXISTS asset_entities (
            asset_id TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            relation_type TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 0,
            evidence TEXT NOT NULL DEFAULT '{}',
            verified_by TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(asset_id, entity_id, relation_type)
        );
        CREATE INDEX IF NOT EXISTS idx_asset_entities_entity ON asset_entities(entity_id, asset_id);

        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            date TEXT NOT NULL DEFAULT '',
            time TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '',
            club_id TEXT NOT NULL DEFAULT '',
            school_id TEXT NOT NULL DEFAULT '',
            verification_status TEXT NOT NULL DEFAULT 'pending_review',
            owner_id TEXT NOT NULL DEFAULT '',
            source_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_events_scope ON events(owner_id, school_id, date);

        CREATE TABLE IF NOT EXISTS review_queue (
            id TEXT PRIMARY KEY,
            asset_id TEXT NOT NULL,
            proposed_change TEXT NOT NULL DEFAULT '{}',
            confidence REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending_review',
            reviewer_id TEXT NOT NULL DEFAULT '',
            reviewed_at TEXT NOT NULL DEFAULT '',
            owner_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_review_queue_scope ON review_queue(owner_id, status, created_at DESC);

        CREATE TABLE IF NOT EXISTS embeddings (
            id TEXT PRIMARY KEY,
            asset_id TEXT NOT NULL,
            text_embedding TEXT NOT NULL DEFAULT '[]',
            image_embedding TEXT NOT NULL DEFAULT '[]',
            model TEXT NOT NULL DEFAULT '',
            owner_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(asset_id, model)
        );

        CREATE TABLE IF NOT EXISTS sync_runs (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL DEFAULT 'insforge',
            owner_id TEXT NOT NULL DEFAULT '',
            project_id TEXT NOT NULL DEFAULT '',
            manifest TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending',
            imported_count INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0,
            resumed_from TEXT NOT NULL DEFAULT '',
            error_log TEXT NOT NULL DEFAULT '[]',
            rollback_status TEXT NOT NULL DEFAULT 'not_requested',
            idempotency_key TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(owner_id, idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS idx_sync_runs_scope ON sync_runs(owner_id, updated_at DESC);

        CREATE TABLE IF NOT EXISTS sync_run_items (
            id TEXT PRIMARY KEY,
            sync_run_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            remote_id TEXT NOT NULL DEFAULT '',
            remote_storage_path TEXT NOT NULL DEFAULT '',
            error_code TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            attempts INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(sync_run_id, asset_id)
        );
        CREATE INDEX IF NOT EXISTS idx_sync_run_items_status ON sync_run_items(sync_run_id, status);

        CREATE TABLE IF NOT EXISTS visual_asset_backend_refs (
            asset_id TEXT PRIMARY KEY,
            backend TEXT NOT NULL,
            remote_id TEXT NOT NULL DEFAULT '',
            remote_storage_path TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            error_code TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            last_synced_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )


def _insforge_core_data_sync(conn: sqlite3.Connection) -> None:
    """Durable state for syncing non-visual records without changing them."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS backend_sync_items (
            id TEXT PRIMARY KEY,
            sync_run_id TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            content_sha256 TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            remote_id TEXT NOT NULL DEFAULT '',
            remote_storage_path TEXT NOT NULL DEFAULT '',
            error_code TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            attempts INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(sync_run_id, resource_type, resource_id)
        );
        CREATE INDEX IF NOT EXISTS idx_backend_sync_items_status
            ON backend_sync_items(sync_run_id, status, resource_type);

        CREATE TABLE IF NOT EXISTS backend_resource_refs (
            resource_type TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            backend TEXT NOT NULL DEFAULT 'insforge',
            remote_id TEXT NOT NULL DEFAULT '',
            remote_storage_path TEXT NOT NULL DEFAULT '',
            content_sha256 TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            error_code TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            last_synced_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(resource_type, resource_id, backend)
        );

        CREATE TABLE IF NOT EXISTS backend_user_mappings (
            local_user_id TEXT NOT NULL,
            backend TEXT NOT NULL DEFAULT 'insforge',
            remote_owner_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'verified',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(local_user_id, backend)
        );
        """
    )


def _data_organization_depth(conn: sqlite3.Connection) -> None:
    """Add lineage, inventory, graph and project/scene/shot context records.

    Domain entities continue to live in the existing ``visual_*`` and generic
    ``entities`` tables.  These tables record mappings and relationships only;
    they are not a second people/club/school database.
    """
    _add_column(conn, "sync_runs", "idempotency_scope TEXT NOT NULL DEFAULT ''")
    conn.execute(
        "UPDATE sync_runs SET idempotency_scope=source || ':' || idempotency_key "
        "WHERE idempotency_scope=''"
    )
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS data_inventory_reports (
            id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            project_id TEXT NOT NULL DEFAULT '',
            statistics TEXT NOT NULL DEFAULT '{}',
            breakdown TEXT NOT NULL DEFAULT '{}',
            manifest TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'verified',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_inventory_reports_owner
            ON data_inventory_reports(owner_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS data_lineage (
            resource_type TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            original_id TEXT NOT NULL DEFAULT '',
            original_path TEXT NOT NULL DEFAULT '',
            original_source TEXT NOT NULL DEFAULT '',
            source_id TEXT NOT NULL DEFAULT '',
            sha256 TEXT NOT NULL DEFAULT '',
            migration_version TEXT NOT NULL DEFAULT '0005_data_organization_depth',
            confidence REAL NOT NULL DEFAULT 0,
            verification_status TEXT NOT NULL DEFAULT 'pending_review',
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            source_updated_at TEXT NOT NULL DEFAULT '',
            metadata TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY(resource_type, resource_id, owner_id)
        );
        CREATE INDEX IF NOT EXISTS idx_data_lineage_source
            ON data_lineage(owner_id, source_id, verification_status);
        CREATE INDEX IF NOT EXISTS idx_data_lineage_sha
            ON data_lineage(owner_id, sha256) WHERE sha256!='';

        CREATE TABLE IF NOT EXISTS entity_relationships (
            id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            from_type TEXT NOT NULL,
            from_id TEXT NOT NULL,
            to_type TEXT NOT NULL,
            to_id TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0,
            verification_status TEXT NOT NULL DEFAULT 'pending_review',
            evidence TEXT NOT NULL DEFAULT '{}',
            source_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(owner_id, from_type, from_id, to_type, to_id, relation_type)
        );
        CREATE INDEX IF NOT EXISTS idx_entity_graph_from
            ON entity_relationships(owner_id, from_type, from_id);
        CREATE INDEX IF NOT EXISTS idx_entity_graph_to
            ON entity_relationships(owner_id, to_type, to_id);

        CREATE TABLE IF NOT EXISTS project_context_nodes (
            id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            parent_id TEXT NOT NULL DEFAULT '',
            node_type TEXT NOT NULL,
            title TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            requirements TEXT NOT NULL DEFAULT '{}',
            verification_status TEXT NOT NULL DEFAULT 'pending_review',
            source_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(owner_id, project_id, node_type, parent_id, position, title)
        );
        CREATE INDEX IF NOT EXISTS idx_project_context_order
            ON project_context_nodes(owner_id, project_id, node_type, position);

        CREATE TABLE IF NOT EXISTS project_asset_links (
            context_node_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            match_score REAL NOT NULL DEFAULT 0,
            reasons TEXT NOT NULL DEFAULT '[]',
            verification_status TEXT NOT NULL DEFAULT 'pending_review',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(context_node_id, asset_id, owner_id)
        );
        CREATE INDEX IF NOT EXISTS idx_project_asset_links_asset
            ON project_asset_links(owner_id, asset_id, match_score DESC);

        CREATE UNIQUE INDEX IF NOT EXISTS idx_sync_runs_source_scope
            ON sync_runs(owner_id, source, idempotency_scope)
            WHERE idempotency_scope!='';
        """
    )


def apply_visual_migrations(conn: sqlite3.Connection, baseline_sql: str) -> list[str]:
    """Apply missing migrations and return the versions applied this run."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS visual_schema_migrations (
               version TEXT PRIMARY KEY,
               applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    applied = {str(row[0]) for row in conn.execute("SELECT version FROM visual_schema_migrations")}
    migrations: list[Migration] = [
        ("0001_initial_visual_schema", lambda current: current.executescript(baseline_sql)),
        ("0002_phase1_media_import", _phase1_media_import),
        ("0003_insforge_visual_backend", _insforge_backend),
        ("0004_insforge_core_data_sync", _insforge_core_data_sync),
        ("0005_data_organization_depth", _data_organization_depth),
    ]
    completed: list[str] = []
    for version, migration in migrations:
        if version in applied:
            continue
        migration(conn)
        conn.execute("INSERT INTO visual_schema_migrations(version) VALUES(?)", (version,))
        conn.commit()
        completed.append(version)
    return completed
