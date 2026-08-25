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
