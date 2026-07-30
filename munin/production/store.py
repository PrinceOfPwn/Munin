"""
ProductionStore — SQLite-backed persistent store (v3.5).

Migration v3.5 absorbs all v3.1 tables inline.
Forward-only checksum guard prevents downgrade.
"""
from __future__ import annotations
import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

MIGRATION_ID = "v3.5"
MIGRATION_CHECKSUM = hashlib.sha256(MIGRATION_ID.encode()).hexdigest()[:16]

RUN_STATES = frozenset({
    "pending", "running", "completed", "failed", "interrupted",
    "cancelled", "waiting_for_human",
})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    goal TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT 'gpt-4o',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS timeline_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    actor_id TEXT NOT NULL DEFAULT '',
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tool_call_id TEXT NOT NULL DEFAULT '',
    parallel_group_id TEXT,
    tool_use_id TEXT,
    input_json TEXT NOT NULL DEFAULT '{}',
    output_text TEXT,
    error_text TEXT,
    state TEXT NOT NULL DEFAULT 'pending',
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS human_requests (
    request_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    args_json TEXT NOT NULL DEFAULT '{}',
    resolution TEXT,
    resolved_at TEXT,
    requested_by_actor_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reasoning_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    text TEXT NOT NULL,
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    mime_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    uri TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    event_json TEXT NOT NULL,
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_metadata (
    run_id TEXT PRIMARY KEY,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversation_collaborators (
    collaborator_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    joined_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversation_notes (
    note_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    collaborator_id TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversation_presence (
    collaborator_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (collaborator_id, conversation_id)
);
CREATE TABLE IF NOT EXISTS run_guidance_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    text TEXT NOT NULL,
    queued_at TEXT NOT NULL,
    delivered_at TEXT
);
CREATE TABLE IF NOT EXISTS procedural (
    name TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    spec TEXT NOT NULL DEFAULT '',
    creator_agent TEXT NOT NULL DEFAULT '',
    parent_run TEXT,
    deps TEXT NOT NULL DEFAULT '[]',
    validation_results TEXT NOT NULL DEFAULT '[]',
    exec_history TEXT NOT NULL DEFAULT '[]',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_registry (
    agent_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    definition_json TEXT NOT NULL,
    runtime_type TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT 'supervisor',
    parent_run TEXT,
    dependencies_json TEXT NOT NULL DEFAULT '[]',
    model_config_json TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    last_invocation_at TEXT,
    exec_history_json TEXT NOT NULL DEFAULT '[]',
    artifacts_uri TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (agent_id, version)
);
CREATE TABLE IF NOT EXISTS workflow_registry (
    workflow_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    definition_json TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT 'supervisor',
    parent_run TEXT,
    dependencies_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    last_invocation_at TEXT,
    exec_history_json TEXT NOT NULL DEFAULT '[]',
    artifacts_uri TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workflow_id, version)
);
CREATE TABLE IF NOT EXISTS conversation_broadcasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS _migrations (
    migration_id TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
"""


class ProductionStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.RLock()
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _migrate(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
                existing = conn.execute(
                    "SELECT checksum FROM _migrations WHERE migration_id = ?",
                    (MIGRATION_ID,)
                ).fetchone()
                if existing:
                    if existing["checksum"] != MIGRATION_CHECKSUM:
                        raise RuntimeError(
                            f"Migration ID {MIGRATION_ID} checksum mismatch — downgrade detected."
                        )
                else:
                    conn.execute(
                        "INSERT INTO _migrations(migration_id, checksum, applied_at) VALUES(?,?,?)",
                        (MIGRATION_ID, MIGRATION_CHECKSUM, _now())
                    )
                conn.commit()

    def create_run(self, conversation_id: str, goal: str = "", model: str = "gpt-4o") -> str:
        run_id = str(uuid.uuid4())
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO runs(run_id,conversation_id,state,goal,model,started_at) VALUES(?,?,?,?,?,?)",
                    (run_id, conversation_id, "running", goal, model, _now())
                )
                conn.commit()
        return run_id

    def set_run_state(self, run_id: str, state: str, *, finished: bool = False) -> None:
        assert state in RUN_STATES, f"Invalid state: {state!r}"
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE runs SET state=?, finished_at=? WHERE run_id=?",
                    (state, _now() if finished else None, run_id)
                )
                conn.commit()

    def get_run(self, run_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def get_active_run(self, conversation_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE conversation_id=? AND state='running' ORDER BY started_at DESC LIMIT 1",
                (conversation_id,)
            ).fetchone()
        return dict(row) if row else None

    def append_message(self, run_id: str, conversation_id: str, role: str, content: str,
                       actor_id: str = "") -> int:
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    "INSERT INTO timeline_messages(run_id,conversation_id,role,content,actor_id,ts) VALUES(?,?,?,?,?,?)",
                    (run_id, conversation_id, role, content, actor_id, _now())
                )
                conn.commit()
                return cur.lastrowid

    def get_timeline(self, run_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM timeline_messages WHERE run_id=? ORDER BY id ASC", (run_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def record_tool_call(self, run_id: str, tool_name: str, tool_call_id: str = "",
                         input_json: dict | None = None, parallel_group_id: str | None = None) -> int:
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    "INSERT INTO tool_calls(run_id,tool_name,tool_call_id,parallel_group_id,input_json,state,started_at) VALUES(?,?,?,?,?,?,?)",
                    (run_id, tool_name, tool_call_id, parallel_group_id,
                     json.dumps(input_json or {}), "pending", _now())
                )
                conn.commit()
                return cur.lastrowid

    def update_tool_call(self, rowid: int, *, state: str = "completed",
                         output: str | None = None, error: str | None = None) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE tool_calls SET state=?,output_text=?,error_text=?,finished_at=? WHERE id=?",
                    (state, output, error, _now(), rowid)
                )
                conn.commit()

    def create_human_request(self, run_id: str, tool_name: str, args: dict,
                             requested_by_actor_id: str = "") -> str:
        request_id = str(uuid.uuid4())
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO human_requests(request_id,run_id,tool_name,args_json,requested_by_actor_id,created_at) VALUES(?,?,?,?,?,?)",
                    (request_id, run_id, tool_name, json.dumps(args), requested_by_actor_id, _now())
                )
                conn.commit()
        return request_id

    def resolve_human_request(self, request_id: str, resolution: str) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE human_requests SET resolution=?,resolved_at=? WHERE request_id=?",
                    (resolution, _now(), request_id)
                )
                conn.commit()

    def queue_guidance(self, run_id: str, text: str) -> int:
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    "INSERT INTO run_guidance_queue(run_id,text,queued_at) VALUES(?,?,?)",
                    (run_id, text, _now())
                )
                conn.commit()
                return cur.lastrowid

    def drain_guidance(self, run_id: str) -> list[dict]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT id, text FROM run_guidance_queue WHERE run_id=? AND delivered_at IS NULL ORDER BY id ASC",
                    (run_id,)
                ).fetchall()
                if rows:
                    ids = [r["id"] for r in rows]
                    placeholders = ",".join("?" * len(ids))
                    conn.execute(
                        f"UPDATE run_guidance_queue SET delivered_at=? WHERE id IN ({placeholders})",
                        [_now()] + ids
                    )
                    conn.commit()
                return [{"id": r["id"], "text": r["text"]} for r in rows]

    def upsert_presence(self, collaborator_id: str, conversation_id: str) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO conversation_presence(collaborator_id,conversation_id,last_seen_at) VALUES(?,?,?) ON CONFLICT(collaborator_id,conversation_id) DO UPDATE SET last_seen_at=excluded.last_seen_at",
                    (collaborator_id, conversation_id, _now())
                )
                conn.commit()

    def push_broadcast(self, conversation_id: str, kind: str, payload: dict) -> int:
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    "INSERT INTO conversation_broadcasts(conversation_id,kind,payload_json,ts) VALUES(?,?,?,?)",
                    (conversation_id, kind, json.dumps(payload), _now())
                )
                conn.commit()
                return cur.lastrowid

    def get_broadcasts_since(self, conversation_id: str, since_id: int = 0) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM conversation_broadcasts WHERE conversation_id=? AND id>? ORDER BY id ASC",
                (conversation_id, since_id)
            ).fetchall()
        return [dict(r) for r in rows]

    def append_reasoning(self, run_id: str, text: str) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO reasoning_steps(run_id,text,ts) VALUES(?,?,?)",
                    (run_id, text, _now())
                )
                conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
