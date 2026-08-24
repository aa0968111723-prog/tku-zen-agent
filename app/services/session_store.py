"""持久化的 Session / Project / Artifact 儲存（SQLite）。

為什麼要有這一層：
  · 原本 session 只在 process memory，伺服器一重開就全部消失
  · 部署後 session_id 由前端隨便給，猜到別人的字串就能讀別人的對話
  · artifact 直接把伺服器絕對路徑丟給前端，等於任意檔案下載的破口

所以這裡的每一筆 session / artifact 都綁 user_id，讀取一律驗歸屬。

併發：FastAPI 的 async handler 在 event loop、sync handler 在 threadpool，
所以用 check_same_thread=False 的單一連線加上 RLock。以社團規模（同時
幾個幹部在用）這樣最簡單也夠用，不需要連線池。
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import config

SCHEMA_VERSION = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(12)}"


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id           TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    is_local     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    task_type  TEXT NOT NULL DEFAULT '',
    summary    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
    title      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,
    role         TEXT NOT NULL,
    content      TEXT NOT NULL DEFAULT '',
    name         TEXT,
    tool_call_id TEXT,
    tool_calls   TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, seq);

CREATE TABLE IF NOT EXISTS artifacts (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id  TEXT REFERENCES sessions(id) ON DELETE SET NULL,
    project_id  TEXT REFERENCES projects(id) ON DELETE SET NULL,
    filename    TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT '',
    local_path  TEXT NOT NULL,
    drive_url   TEXT,
    version     INTEGER NOT NULL DEFAULT 1,
    parent_id   TEXT REFERENCES artifacts(id) ON DELETE SET NULL,
    meta        TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_user ON artifacts(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_artifacts_project ON artifacts(project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS working_memory (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, key)
);

CREATE TABLE IF NOT EXISTS retrieval_cache (
    project_id   TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    cache_key    TEXT NOT NULL,
    fingerprint  TEXT NOT NULL,
    query_text   TEXT NOT NULL,
    context_text TEXT NOT NULL,
    meta         TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL,
    PRIMARY KEY(project_id, cache_key)
);

CREATE TABLE IF NOT EXISTS research_sources (
    id             TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    session_id     TEXT REFERENCES sessions(id) ON DELETE SET NULL,
    title          TEXT NOT NULL DEFAULT '',
    url            TEXT NOT NULL DEFAULT '',
    source_date    TEXT NOT NULL DEFAULT '',
    summary        TEXT NOT NULL DEFAULT '',
    credibility    REAL NOT NULL DEFAULT 0,
    verification   TEXT NOT NULL DEFAULT 'needs_verification',
    source_type    TEXT NOT NULL DEFAULT 'external_reference',
    source_file    TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_research_sources_project ON research_sources(project_id, created_at DESC);
"""


@dataclass
class Artifact:
    id: str
    filename: str
    kind: str
    local_path: str
    drive_url: str | None
    version: int
    created_at: str
    project_id: str | None = None
    meta: dict[str, Any] | None = None
    parent_id: str | None = None

    def public(self) -> dict[str, Any]:
        """給前端看的樣子 —— 沒有伺服器絕對路徑。"""
        return {
            "artifact_id": self.id,
            "filename": self.filename,
            "kind": self.kind,
            "drive_url": self.drive_url,
            "version": self.version,
            "created_at": self.created_at,
            "parent_artifact_id": self.parent_id,
        }


class SessionStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or config.DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── users ────────────────────────────────────────────────

    def user_exists(self, user_id: str) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone()
        return row is not None

    def ensure_user(self, user_id: str | None = None, *, is_local: bool = False, display_name: str = "") -> str:
        """回傳 user_id；沒給或查無此人就開新的。"""
        with self._lock:
            if user_id:
                row = self._conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
                if row:
                    self._conn.execute("UPDATE users SET last_seen_at=? WHERE id=?", (_now(), user_id))
                    self._conn.commit()
                    return user_id
            uid = user_id or new_id("u")
            now = _now()
            self._conn.execute(
                "INSERT INTO users(id, display_name, is_local, created_at, last_seen_at) VALUES(?,?,?,?,?)",
                (uid, display_name, 1 if is_local else 0, now, now),
            )
            self._conn.commit()
            return uid

    # ── projects ─────────────────────────────────────────────

    def create_project(self, user_id: str, name: str, task_type: str = "") -> str:
        pid = new_id("p")
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO projects(id, user_id, name, task_type, created_at, updated_at) VALUES(?,?,?,?,?,?)",
                (pid, user_id, name[:120], task_type, now, now),
            )
            self._conn.commit()
        return pid

    def get_project(self, project_id: str, user_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM projects WHERE id=? AND user_id=?", (project_id, user_id)
            ).fetchone()
        return dict(row) if row else None

    def list_projects(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM projects WHERE user_id=? ORDER BY updated_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def set_project_summary(self, project_id: str, summary: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE projects SET summary=?, updated_at=? WHERE id=?", (summary, _now(), project_id)
            )
            self._conn.commit()

    def touch_project(self, project_id: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (_now(), project_id))
            self._conn.commit()

    # ── sessions ─────────────────────────────────────────────

    def create_session(self, user_id: str, project_id: str | None = None, title: str = "") -> str:
        sid = new_id("s")
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions(id, user_id, project_id, title, created_at, updated_at) VALUES(?,?,?,?,?,?)",
                (sid, user_id, project_id, title[:120], now, now),
            )
            self._conn.commit()
        return sid

    def get_session(self, session_id: str, user_id: str) -> dict[str, Any] | None:
        """驗歸屬 —— 猜到別人的 session_id 也讀不到。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE id=? AND user_id=?", (session_id, user_id)
            ).fetchone()
        return dict(row) if row else None

    def list_sessions(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sessions WHERE user_id=? ORDER BY updated_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def bind_session_project(self, session_id: str, project_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET project_id=?, updated_at=? WHERE id=?",
                (project_id, _now(), session_id),
            )
            self._conn.commit()

    def set_session_title(self, session_id: str, title: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET title=?, updated_at=? WHERE id=?", (title[:120], _now(), session_id)
            )
            self._conn.commit()

    def delete_session(self, session_id: str, user_id: str) -> bool:
        """只能刪自己的。"""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM sessions WHERE id=? AND user_id=?", (session_id, user_id)
            )
            self._conn.commit()
            return cur.rowcount > 0

    # ── messages ─────────────────────────────────────────────

    def append_message(self, session_id: str, message: dict[str, Any]) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS m FROM messages WHERE session_id=?", (session_id,)
            ).fetchone()
            seq = int(row["m"]) + 1
            self._conn.execute(
                "INSERT INTO messages(session_id, seq, role, content, name, tool_call_id, tool_calls, created_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (
                    session_id,
                    seq,
                    message.get("role", "user"),
                    message.get("content") or "",
                    message.get("name"),
                    message.get("tool_call_id"),
                    json.dumps(message["tool_calls"], ensure_ascii=False) if message.get("tool_calls") else None,
                    _now(),
                ),
            )
            self._conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (_now(), session_id))
            self._conn.commit()

    def load_messages(self, session_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        with self._lock:
            sql = "SELECT * FROM messages WHERE session_id=? ORDER BY seq"
            rows = self._conn.execute(sql, (session_id,)).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            m: dict[str, Any] = {"role": r["role"], "content": r["content"]}
            if r["name"]:
                m["name"] = r["name"]
            if r["tool_call_id"]:
                m["tool_call_id"] = r["tool_call_id"]
            if r["tool_calls"]:
                m["tool_calls"] = json.loads(r["tool_calls"])
            out.append(m)
        return out[-limit:] if limit else out

    def clear_messages(self, session_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            self._conn.commit()

    def message_count(self, session_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE session_id=?", (session_id,)
            ).fetchone()
        return int(row["n"])

    # ── artifacts ────────────────────────────────────────────

    def record_artifact(
        self,
        *,
        user_id: str,
        filename: str,
        local_path: str,
        kind: str = "",
        session_id: str | None = None,
        project_id: str | None = None,
        drive_url: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Artifact:
        """登記一份產出。同名檔案自動累加版本，並連回上一版。"""
        aid = new_id("a")
        now = _now()
        with self._lock:
            prev = self._conn.execute(
                "SELECT id, version FROM artifacts WHERE user_id=? AND filename=?"
                " ORDER BY version DESC LIMIT 1",
                (user_id, filename),
            ).fetchone()
            version = (int(prev["version"]) + 1) if prev else 1
            parent = prev["id"] if prev else None
            self._conn.execute(
                "INSERT INTO artifacts(id, user_id, session_id, project_id, filename, kind,"
                " local_path, drive_url, version, parent_id, meta, created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    aid, user_id, session_id, project_id, filename, kind,
                    str(local_path), drive_url, version, parent,
                    json.dumps(meta or {}, ensure_ascii=False), now,
                ),
            )
            self._conn.commit()
        return Artifact(
            id=aid, filename=filename, kind=kind, local_path=str(local_path),
            drive_url=drive_url, version=version, created_at=now,
            project_id=project_id, meta=meta or {}, parent_id=parent,
        )

    def get_artifact(self, artifact_id: str, user_id: str) -> Artifact | None:
        """驗歸屬 —— 這是 /api/download 的安全閘門。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM artifacts WHERE id=? AND user_id=?", (artifact_id, user_id)
            ).fetchone()
        if not row:
            return None
        return Artifact(
            id=row["id"], filename=row["filename"], kind=row["kind"],
            local_path=row["local_path"], drive_url=row["drive_url"],
            version=row["version"], created_at=row["created_at"],
            project_id=row["project_id"], meta=json.loads(row["meta"]), parent_id=row["parent_id"],
        )

    def update_artifact_drive_url(self, artifact_id: str, drive_url: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE artifacts SET drive_url=? WHERE id=?", (drive_url, artifact_id))
            self._conn.commit()

    def list_artifacts(
        self, user_id: str, project_id: str | None = None, limit: int = 30,
        *, include_history: bool = False,
    ) -> list[Artifact]:
        with self._lock:
            if project_id:
                rows = self._conn.execute(
                    "SELECT * FROM artifacts WHERE user_id=? AND project_id=?"
                    " ORDER BY created_at DESC, version DESC LIMIT ?",
                    (user_id, project_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM artifacts WHERE user_id=? ORDER BY created_at DESC, version DESC LIMIT ?",
                    (user_id, limit),
                ).fetchall()
        records = [
            Artifact(
                id=r["id"], filename=r["filename"], kind=r["kind"], local_path=r["local_path"],
                drive_url=r["drive_url"], version=r["version"], created_at=r["created_at"],
                project_id=r["project_id"], meta=json.loads(r["meta"]), parent_id=r["parent_id"],
            )
            for r in rows
        ]
        if include_history:
            return records[:limit]
        latest: list[Artifact] = []
        seen: set[str] = set()
        for record in records:
            if record.filename in seen:
                continue
            seen.add(record.filename)
            latest.append(record)
            if len(latest) >= limit:
                break
        return latest

    # ── retrieval cache ────────────────────────────────────

    def cache_retrieval(
        self, project_id: str, cache_key: str, fingerprint: str, query_text: str,
        context_text: str, meta: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO retrieval_cache(project_id, cache_key, fingerprint, query_text, context_text, meta, created_at)"
                " VALUES(?,?,?,?,?,?,?)"
                " ON CONFLICT(project_id, cache_key) DO UPDATE SET fingerprint=excluded.fingerprint,"
                " query_text=excluded.query_text, context_text=excluded.context_text, meta=excluded.meta,"
                " created_at=excluded.created_at",
                (project_id, cache_key[:160], fingerprint, query_text[:2000], context_text, json.dumps(meta or {}, ensure_ascii=False), _now()),
            )
            self._conn.commit()

    def get_retrieval_cache(self, project_id: str, cache_key: str, fingerprint: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM retrieval_cache WHERE project_id=? AND cache_key=? AND fingerprint=?",
                (project_id, cache_key, fingerprint),
            ).fetchone()
        if not row:
            return None
        return {
            "query_text": row["query_text"],
            "context_text": row["context_text"],
            "meta": json.loads(row["meta"]),
            "created_at": row["created_at"],
        }

    # ── research provenance ────────────────────────────────

    def record_research_sources(
        self, project_id: str, sources: list[dict[str, Any]], session_id: str | None = None,
    ) -> list[str]:
        ids: list[str] = []
        with self._lock:
            for source in sources:
                sid = new_id("rs")
                ids.append(sid)
                self._conn.execute(
                    "INSERT INTO research_sources(id, project_id, session_id, title, url, source_date, summary,"
                    " credibility, verification, source_type, source_file, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        sid, project_id, session_id,
                        str(source.get("title") or source.get("source") or "未命名來源")[:300],
                        str(source.get("url") or "")[:1000],
                        str(source.get("date") or source.get("source_date") or "")[:80],
                        str(source.get("summary") or source.get("excerpt") or "")[:2000],
                        float(source.get("credibility") or 0),
                        str(source.get("verification") or "needs_verification"),
                        str(source.get("source_type") or "external_reference"),
                        str(source.get("source_file") or "")[:300],
                        _now(),
                    ),
                )
            self._conn.commit()
        return ids

    def list_research_sources(self, project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM research_sources WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    # ── working memory ───────────────────────────────────────

    def remember(self, project_id: str, key: str, value: str, source: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO working_memory(project_id, key, value, source, updated_at)"
                " VALUES(?,?,?,?,?)"
                " ON CONFLICT(project_id, key) DO UPDATE SET value=excluded.value,"
                " source=excluded.source, updated_at=excluded.updated_at",
                (project_id, key[:120], value[:4000], source, _now()),
            )
            self._conn.commit()

    def recall(self, project_id: str) -> dict[str, dict[str, str]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value, source, updated_at FROM working_memory WHERE project_id=? ORDER BY key",
                (project_id,),
            ).fetchall()
        return {r["key"]: {"value": r["value"], "source": r["source"], "updated_at": r["updated_at"]} for r in rows}

    def forget(self, project_id: str, key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM working_memory WHERE project_id=? AND key=?", (project_id, key))
            self._conn.commit()


_store: SessionStore | None = None
_store_lock = threading.Lock()


def get_store() -> SessionStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = SessionStore()
    return _store
