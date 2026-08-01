"""Durable production aggregate for conversations, operations, and identity.

The existing ``SharedStateStore`` remains compatible with legacy MCP tools.
This module deliberately has a narrower contract: all operator-facing state is
stored as explicit aggregates with transaction, idempotency, lease fencing,
event provenance, and server-side authorization boundaries.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from argon2.low_level import Type
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .redaction import redact_payload, redact_text

logger = logging.getLogger(__name__)

MIGRATION_ID = "20260729_001_production_foundation"
REDACTION_POLICY_VERSION = "2026-07-29.1"
RUN_STATES = {"queued", "running", "waiting_for_human", "completed", "failed", "interrupted", "cancelled"}
FINAL_RUN_STATES = {"completed", "failed", "interrupted", "cancelled"}
ROLES = {"admin", "operator", "viewer"}
_FENCED_ARTIFACT = re.compile(r"```(?P<language>[A-Za-z0-9_+.-]*)[ \t]*\n(?P<content>[\s\S]*?)```")

_SESSION_TOUCH_INTERVAL_MS: int = int(os.environ.get("MUNIN_SESSION_TOUCH_INTERVAL_SECONDS", "120")) * 1000


def _now_ms() -> int:
    return int(time.time() * 1000)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _json(value: Any) -> str:
    return json.dumps(redact_payload(value), ensure_ascii=True, separators=(",", ":"), default=str)


def _row(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


_DDL: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS schema_migrations (
        migration_id TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at_ms INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('admin','operator','viewer')), disabled_at_ms INTEGER,
        created_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS auth_sessions (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE, csrf_hash TEXT NOT NULL,
        created_at_ms INTEGER NOT NULL, last_seen_at_ms INTEGER NOT NULL, idle_expires_at_ms INTEGER NOT NULL,
        absolute_expires_at_ms INTEGER NOT NULL, revoked_at_ms INTEGER, replaced_by_session_id TEXT,
        ip_address TEXT NOT NULL DEFAULT '', user_agent TEXT NOT NULL DEFAULT '',
        FOREIGN KEY(user_id) REFERENCES users(id)
    )""",
    """CREATE TABLE IF NOT EXISTS auth_rate_limits (
        subject TEXT PRIMARY KEY, failures INTEGER NOT NULL DEFAULT 0, blocked_until_ms INTEGER NOT NULL DEFAULT 0,
        updated_at_ms INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS password_recovery_tokens (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE, expires_at_ms INTEGER NOT NULL,
        consumed_at_ms INTEGER, requested_at_ms INTEGER NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id)
    )""",
    """CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'active', tags_json TEXT NOT NULL DEFAULT '[]', scope_json TEXT NOT NULL DEFAULT '{}',
        last_activity_at_ms INTEGER NOT NULL, archived_at_ms INTEGER, deleted_at_ms INTEGER, version INTEGER NOT NULL DEFAULT 1,
        created_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL,
        FOREIGN KEY(owner_id) REFERENCES users(id)
    )""",
    """CREATE TABLE IF NOT EXISTS conversation_participants (
        conversation_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL,
        added_at_ms INTEGER NOT NULL, removed_at_ms INTEGER,
        PRIMARY KEY(conversation_id,user_id), FOREIGN KEY(conversation_id) REFERENCES conversations(id),
        FOREIGN KEY(user_id) REFERENCES users(id)
    )""",
    """CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, sequence INTEGER NOT NULL, author_id TEXT,
        run_id TEXT, kind TEXT NOT NULL, status TEXT NOT NULL, content TEXT NOT NULL,
        content_hash TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, created_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL,
        UNIQUE(conversation_id,sequence), FOREIGN KEY(conversation_id) REFERENCES conversations(id),
        FOREIGN KEY(author_id) REFERENCES users(id)
    )""",
    """CREATE TABLE IF NOT EXISTS message_revisions (
        id TEXT PRIMARY KEY, message_id TEXT NOT NULL, version INTEGER NOT NULL, content TEXT NOT NULL,
        reason TEXT NOT NULL, actor_id TEXT, created_at_ms INTEGER NOT NULL, UNIQUE(message_id,version),
        FOREIGN KEY(message_id) REFERENCES messages(id)
    )""",
    """CREATE TABLE IF NOT EXISTS agent_runs (
        id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, actor_id TEXT NOT NULL, user_message_id TEXT NOT NULL,
        assistant_message_id TEXT NOT NULL, root_run_id TEXT, parent_run_id TEXT, attempt INTEGER NOT NULL DEFAULT 1,
        state TEXT NOT NULL CHECK(state IN ('queued','running','waiting_for_human','completed','failed','interrupted','cancelled')),
        idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL, lease_worker_id TEXT, lease_token TEXT,
        lease_expires_at_ms INTEGER, fencing_epoch INTEGER NOT NULL DEFAULT 0, cancel_requested_at_ms INTEGER,
        model_profile_id TEXT, budget_json TEXT NOT NULL DEFAULT '{}', context_manifest_json TEXT NOT NULL DEFAULT '{}',
        state_version INTEGER NOT NULL DEFAULT 1, created_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL,
        UNIQUE(conversation_id,actor_id,idempotency_key), FOREIGN KEY(conversation_id) REFERENCES conversations(id),
        FOREIGN KEY(actor_id) REFERENCES users(id)
    )""",
    """CREATE TABLE IF NOT EXISTS run_events (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL, sequence INTEGER NOT NULL, kind TEXT NOT NULL,
        payload_json TEXT NOT NULL, causation_id TEXT, correlation_id TEXT, actor_id TEXT,
        redaction_policy_version TEXT NOT NULL, created_at_ms INTEGER NOT NULL,
        UNIQUE(run_id,sequence), FOREIGN KEY(run_id) REFERENCES agent_runs(id)
    )""",
    """CREATE TABLE IF NOT EXISTS reasoning_events (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL, event_id TEXT NOT NULL, kind TEXT NOT NULL,
        content TEXT NOT NULL, provider TEXT NOT NULL DEFAULT '', agent_name TEXT NOT NULL DEFAULT '', step INTEGER NOT NULL DEFAULT 0,
        persisted INTEGER NOT NULL, provenance TEXT NOT NULL, created_at_ms INTEGER NOT NULL,
        FOREIGN KEY(run_id) REFERENCES agent_runs(id), FOREIGN KEY(event_id) REFERENCES run_events(id)
    )""",
    """CREATE TABLE IF NOT EXISTS tool_calls (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL, event_id TEXT, agent_name TEXT NOT NULL, tool_name TEXT NOT NULL,
        state TEXT NOT NULL, args_json TEXT NOT NULL, result_json TEXT NOT NULL DEFAULT '{}', scope_json TEXT NOT NULL DEFAULT '{}',
        started_at_ms INTEGER NOT NULL, finished_at_ms INTEGER, retry_of_id TEXT, FOREIGN KEY(run_id) REFERENCES agent_runs(id)
    )""",
    """CREATE TABLE IF NOT EXISTS subagent_runs (
        id TEXT PRIMARY KEY, parent_run_id TEXT NOT NULL, profile_id TEXT NOT NULL, state TEXT NOT NULL,
        objective TEXT NOT NULL, lease_token TEXT, started_at_ms INTEGER, finished_at_ms INTEGER,
        FOREIGN KEY(parent_run_id) REFERENCES agent_runs(id)
    )""",
    """CREATE TABLE IF NOT EXISTS human_requests (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL, action TEXT NOT NULL, args_hash TEXT NOT NULL, risk TEXT NOT NULL,
        evidence_json TEXT NOT NULL, scope_json TEXT NOT NULL, choices_json TEXT NOT NULL, nonce_hash TEXT NOT NULL,
        state TEXT NOT NULL, response_json TEXT NOT NULL DEFAULT '{}', expires_at_ms INTEGER NOT NULL,
        created_at_ms INTEGER NOT NULL, resolved_at_ms INTEGER, FOREIGN KEY(run_id) REFERENCES agent_runs(id)
    )""",
    """CREATE TABLE IF NOT EXISTS conversation_artifacts (
        id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, message_id TEXT, run_id TEXT, filename TEXT NOT NULL,
        media_type TEXT NOT NULL, language TEXT NOT NULL, content TEXT NOT NULL, content_hash TEXT NOT NULL,
        size_bytes INTEGER NOT NULL, created_at_ms INTEGER NOT NULL, FOREIGN KEY(conversation_id) REFERENCES conversations(id)
    )""",
    """CREATE TABLE IF NOT EXISTS conversation_summaries (
        id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, run_id TEXT, source_start_sequence INTEGER NOT NULL,
        source_end_sequence INTEGER NOT NULL, source_ids_json TEXT NOT NULL, source_hash TEXT NOT NULL,
        content TEXT NOT NULL DEFAULT '', model TEXT NOT NULL, prompt_version TEXT NOT NULL, confidence REAL NOT NULL, entities_json TEXT NOT NULL,
        findings_json TEXT NOT NULL, decisions_json TEXT NOT NULL, open_tasks_json TEXT NOT NULL, supersedes_id TEXT,
        created_at_ms INTEGER NOT NULL, FOREIGN KEY(conversation_id) REFERENCES conversations(id)
    )""",
    """CREATE TABLE IF NOT EXISTS provider_profiles (
        id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, label TEXT NOT NULL, provider TEXT NOT NULL, base_url TEXT NOT NULL,
        model TEXT NOT NULL, uses_json TEXT NOT NULL, key_fingerprint TEXT NOT NULL, ciphertext_json TEXT NOT NULL,
        wrapped_dek_json TEXT NOT NULL, kek_version TEXT NOT NULL, status TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0,
        created_at_ms INTEGER NOT NULL, rotated_at_ms INTEGER, revoked_at_ms INTEGER, updated_at_ms INTEGER NOT NULL,
        FOREIGN KEY(owner_id) REFERENCES users(id)
    )""",
    """CREATE TABLE IF NOT EXISTS audit_events (
        id TEXT PRIMARY KEY, actor_id TEXT, action TEXT NOT NULL, resource_type TEXT NOT NULL, resource_id TEXT NOT NULL,
        outcome TEXT NOT NULL, metadata_json TEXT NOT NULL, created_at_ms INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS operation_snapshots (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL, event_id TEXT NOT NULL, state_json TEXT NOT NULL, state_hash TEXT NOT NULL,
        redaction_policy_version TEXT NOT NULL, created_at_ms INTEGER NOT NULL, FOREIGN KEY(run_id) REFERENCES agent_runs(id)
    )""",
    """CREATE TABLE IF NOT EXISTS operation_branches (
        id TEXT PRIMARY KEY, parent_run_id TEXT NOT NULL, parent_branch_id TEXT, fork_event_id TEXT NOT NULL,
        replay_mode TEXT NOT NULL, state TEXT NOT NULL, provenance_json TEXT NOT NULL, created_at_ms INTEGER NOT NULL,
        FOREIGN KEY(parent_run_id) REFERENCES agent_runs(id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_conversations_owner_activity ON conversations(owner_id,last_activity_at_ms DESC)",
    "CREATE INDEX IF NOT EXISTS idx_messages_conversation_sequence ON messages(conversation_id,sequence)",
    "CREATE INDEX IF NOT EXISTS idx_runs_state_lease ON agent_runs(state,lease_expires_at_ms)",
    "CREATE INDEX IF NOT EXISTS idx_run_events_run_sequence ON run_events(run_id,sequence)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_token ON auth_sessions(token_hash)",
    "CREATE INDEX IF NOT EXISTS idx_recovery_token ON password_recovery_tokens(token_hash)",
)
MIGRATION_CHECKSUM = hashlib.sha256("\n".join(_DDL).encode()).hexdigest()

# ---------------------------------------------------------------------------
# Fase 2 (issue #9) idempotent additions
# ---------------------------------------------------------------------------
#
# The base ``_DDL`` above is checksum-locked and forward-only, so we can't
# edit it without breaking every existing deployment.  The additions below
# were previously injected at runtime by ``install_v3_1_extensions`` in the
# now-deleted ``store_v3_1.py``; Fase 2 merges the essentials inline so the
# ``ProductionStore`` boot path installs them without the monkey-patching
# indirection.  Everything is idempotent (``IF NOT EXISTS`` / PRAGMA-guarded
# ADD COLUMN) so re-running ``migrate()`` is a no-op.
_FASE2_DDL: tuple[str, ...] = (
    # Durable outbox: operator guidance waiting for the ReAct loop.  Drained
    # by ``OperatorGuidanceMiddleware`` on every pre-iteration hook.
    """CREATE TABLE IF NOT EXISTS run_guidance_queue (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        actor_username TEXT NOT NULL,
        body TEXT NOT NULL,
        target_agent_id TEXT,
        created_at_ms INTEGER NOT NULL,
        consumed_at_ms INTEGER,
        delivered_at_step INTEGER,
        budget_extension_seconds INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE INDEX IF NOT EXISTS guidance_queue_by_run
        ON run_guidance_queue(run_id, created_at_ms)""",
    # Cursor-addressable durable broadcast log for per-conversation events
    # (used by ``/api/chat`` to record run-transition markers so the store
    # holds a full audit trail — the SSE stream that used to consume this
    # was deleted in Fase 2).
    """CREATE TABLE IF NOT EXISTS conversation_broadcasts (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        created_at_ms INTEGER NOT NULL
    )""",
    """CREATE INDEX IF NOT EXISTS conversation_broadcasts_by_conv
        ON conversation_broadcasts(conversation_id, sequence)""",
)

# Optional column adds; guarded via PRAGMA at boot.
_FASE2_OPTIONAL_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("tool_calls", "parallel_group_id", "TEXT"),
    ("tool_calls", "tool_use_id", "TEXT"),
    ("reasoning_events", "metadata_json", "TEXT"),
)

# ---------------------------------------------------------------------------
# Local-first delta sync (conversation durability)
# ---------------------------------------------------------------------------
#
# GUI conversation rows are written to the LOCAL hot SQLite database only.
# A change outbox (``_sync_outbox``) records every touched (table, rowid)
# via AFTER INSERT/UPDATE/DELETE triggers; ``MuninStore.flush_pending_syncs``
# uploads ONLY the rows referenced by committed outbox entries (idempotent
# upserts through the ``production_`` namespace) and then deletes those
# entries below a ``MAX(seq)`` watermark.  Entries above the watermark
# survive — a writer that commits mid-sync is picked up by the next flush.
#
# Why triggers instead of manual dirty marking: every write path (including
# the ~40 methods of ``ProductionStore`` and the hot mirror helpers) would
# otherwise have to remember to flag rows; triggers are correct by
# construction and leave every call site untouched.
#
# Tables that stay out of the sync set on purpose:
#   * auth_sessions / auth_rate_limits / password_recovery_tokens /
#     run_guidance_queue — hot-only churn, disposable by design.
#   * provider_profiles / operation_snapshots / operation_branches —
#     durable-only aggregates, unchanged per-op writes.
#
# ``users`` IS in the sync set: the hot store mirrors durable-authoritative
# user rows (``_mirror_user``) so the hot side can resolve FKs locally, but
# those mirror writes must propagate to durable too — durable enforces the
# ``conversations.owner_id`` FK, so a sync that ships a conversation without
# its owning user would IntegrityError.
_SYNC_TABLES: tuple[str, ...] = (
    "users",
    "conversations",
    "conversation_participants",
    "messages",
    "message_revisions",
    "agent_runs",
    "run_events",
    "reasoning_events",
    "tool_calls",
    "subagent_runs",
    "human_requests",
    "conversation_artifacts",
    "conversation_summaries",
    "conversation_broadcasts",
    "audit_events",
)
# FK dependency order for hydration (durable → hot) and flush (hot → durable).
_SYNC_META_DDL: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS _sync_state (
        key TEXT PRIMARY KEY, value INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS _sync_outbox (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        table_name TEXT NOT NULL,
        rowid INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS _sync_outbox_by_table ON _sync_outbox(table_name, rowid)",
)


def _sync_trigger_sql(table: str) -> tuple[str, str, str]:
    """Return AFTER INSERT / UPDATE / DELETE triggers for one sync table.

    Table names come from the compile-time ``_SYNC_TABLES`` constant, so
    interpolation is injection-safe.  Rowid works for every table in the
    production schema (none are ``WITHOUT ROWID``; ``id`` is a TEXT primary
    key, so ``rowid`` stays distinct).  ``recursive_triggers`` is pinned OFF
    on hot connections, so the statement inside a trigger body never re-fires
    the trigger that invoked it.
    """
    insert = (
        f"CREATE TRIGGER IF NOT EXISTS trg_{table}_sync_ins AFTER INSERT ON {table} BEGIN "
        f"INSERT INTO _sync_outbox (table_name,rowid) VALUES ('{table}',NEW.rowid); END"
    )
    update = (
        f"CREATE TRIGGER IF NOT EXISTS trg_{table}_sync_upd AFTER UPDATE ON {table} BEGIN "
        f"INSERT INTO _sync_outbox (table_name,rowid) VALUES ('{table}',NEW.rowid); END"
    )
    delete = (
        f"CREATE TRIGGER IF NOT EXISTS trg_{table}_sync_del AFTER DELETE ON {table} BEGIN "
        f"INSERT INTO _sync_outbox (table_name,rowid) VALUES ('{table}',OLD.rowid); END"
    )
    return insert, update, delete

# The MCP state store predates Production Suite and already owns generic names
# such as ``conversations`` and ``messages`` in the operator's Turso database.
# Production Suite is a separate aggregate, so it must never reuse or mutate
# those legacy tables.  The adapter below namespaces only production SQL when
# using the shared remote backend; local unit fixtures retain the concise names
# in ``_DDL`` for readable assertions.
_PRODUCTION_TABLE_NAMES = (
    "schema_migrations",
    "users",
    "auth_sessions",
    "auth_rate_limits",
    "password_recovery_tokens",
    "conversations",
    "conversation_participants",
    "messages",
    "message_revisions",
    "agent_runs",
    "run_events",
    "reasoning_events",
    "tool_calls",
    "subagent_runs",
    "human_requests",
    "conversation_artifacts",
    "conversation_summaries",
    "provider_profiles",
    "audit_events",
    "operation_snapshots",
    "operation_branches",
)
_PRODUCTION_TABLE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(" + "|".join(sorted(_PRODUCTION_TABLE_NAMES, key=len, reverse=True)) + r")(?![A-Za-z0-9_])"
)


def _namespace_production_sql(sql: str) -> str:
    """Map Production Suite's generic table identifiers to its Turso namespace."""
    return _PRODUCTION_TABLE_PATTERN.sub(lambda match: f"production_{match.group(1)}", sql)


class _NamespacedConnection:
    """Small DB-API proxy that keeps Production Suite isolated from MCP tables."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def execute(self, sql: str, params: Any = ()) -> Any:
        return self._connection.execute(_namespace_production_sql(sql), params)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


class EnvelopeCipher:
    """Per-profile DEK envelope encryption with owner/profile/provider AAD."""

    def __init__(self, master_key: bytes, *, kek_version: str = "local-v1") -> None:
        if len(master_key) != 32:
            raise ValueError("master key must be exactly 32 bytes")
        self.master_key = master_key
        self.kek_version = kek_version

    @staticmethod
    def _b64(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii")

    @staticmethod
    def _unb64(value: str) -> bytes:
        return base64.urlsafe_b64decode(value.encode("ascii"))

    def encrypt(self, *, owner_id: str, profile_id: str, provider: str, plaintext: str) -> tuple[dict[str, str], dict[str, str], str]:
        aad = f"{owner_id}:{profile_id}:{provider}".encode()
        dek = secrets.token_bytes(32)
        data_nonce = secrets.token_bytes(12)
        dek_nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(dek).encrypt(data_nonce, plaintext.encode(), aad)
        wrapped_dek = AESGCM(self.master_key).encrypt(dek_nonce, dek, aad)
        fingerprint = hmac.new(self.master_key, plaintext.encode(), hashlib.sha256).hexdigest()[:24]
        return (
            {"nonce": self._b64(data_nonce), "ciphertext": self._b64(ciphertext)},
            {"nonce": self._b64(dek_nonce), "ciphertext": self._b64(wrapped_dek)},
            fingerprint,
        )

    def decrypt(self, *, owner_id: str, profile_id: str, provider: str, ciphertext: dict[str, str], wrapped_dek: dict[str, str]) -> str:
        aad = f"{owner_id}:{profile_id}:{provider}".encode()
        dek = AESGCM(self.master_key).decrypt(self._unb64(wrapped_dek["nonce"]), self._unb64(wrapped_dek["ciphertext"]), aad)
        return AESGCM(dek).decrypt(self._unb64(ciphertext["nonce"]), self._unb64(ciphertext["ciphertext"]), aad).decode()


class ProductionStore:
    """Repository façade with explicit transactions and durable operation events."""

    def __init__(self, connection_factory: Callable[[], Any], *, master_key: bytes) -> None:
        self._connection_factory = connection_factory
        self._passwords = PasswordHasher(time_cost=2, memory_cost=19_456, parallelism=1, hash_len=32, salt_len=16, type=Type.ID)
        self._cipher = EnvelopeCipher(master_key)
        # Fase 5: populated by :meth:`for_settings` when the backend is a
        # libsql URL.  ``None`` for sqlite/file backends (no pool needed).
        # The ASGI shutdown hook calls :meth:`close_pools` to drain this.
        self._libsql_pool: Any = None
        self.migrate()

    @classmethod
    def for_sqlite(cls, path: Path, *, master_key: bytes) -> ProductionStore:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        def connect() -> sqlite3.Connection:
            # Read paths use a short busy_timeout so a saturated writer fails
            # the request fast (the UI shows "backend busy") instead of
            # holding a spinner for the full 30s lock window. Write paths
            # still wait up to ``MUNIN_DB_WRITE_TIMEOUT_MS`` (default 5000).
            conn = sqlite3.connect(path, timeout=2, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=2000")
            conn.execute("PRAGMA journal_mode=WAL")
            return conn

        return cls(connect, master_key=master_key)

    @classmethod
    def for_settings(cls, settings: Any, *, master_key: bytes) -> ProductionStore:
        """Create a store over the configured authoritative libSQL connection.

        Fase 5 change: when ``settings.db_url`` points at a libsql:// endpoint,
        we build a bounded :class:`LibsqlConnectionPool` and hand back pooled
        proxies whose ``.close()`` returns the underlying socket to the pool
        instead of tearing down TLS + Hrana on every operation.  The saved
        ~200-500 ms per request dominates the DurableStore cost profile.

        For sqlite/file backends this falls through to the legacy one-shot
        factory — local sqlite is essentially free to reopen.
        """
        from ..mcp.persistence import open_pooled_connection

        pool_size = int(getattr(settings, "libsql_pool_size", 4) or 4)
        pool_timeout_s = float(getattr(settings, "libsql_pool_timeout_s", 10.0) or 10.0)
        native_factory, pool = open_pooled_connection(
            settings.db_url,
            default_path=settings.shared_state_db,
            auth_token=settings.db_auth_token,
            pool_size=pool_size,
            pool_timeout_s=pool_timeout_s,
        )

        def connect() -> Any:
            return _NamespacedConnection(native_factory())

        store = cls(connect, master_key=master_key)
        store._libsql_pool = pool  # type: ignore[assignment]  # ``None`` when backend is sqlite/file
        return store

    def close_pools(self) -> None:
        """Tear down any libsql connection pool owned by this store.

        Idempotent and safe to call from an ASGI shutdown hook; sqlite-only
        instances (no pool) treat this as a no-op.  Never raises — a broken
        native handle during shutdown must not prevent a clean process exit.
        """
        pool = getattr(self, "_libsql_pool", None)
        if pool is None:
            return
        with suppress(Exception):
            pool.close_all()
        self._libsql_pool = None

    @staticmethod
    def master_key_from_environment() -> bytes:
        """Read a 32-byte bootstrap KEK without ever logging its value."""
        raw = os.environ.get("MUNIN_MASTER_KEY", "").strip()
        if not raw:
            raise RuntimeError("MUNIN_MASTER_KEY must contain a 32-byte base64url or hex key")
        try:
            candidate = bytes.fromhex(raw)
        except ValueError:
            try:
                candidate = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
            except Exception as exc:  # pragma: no cover - malformed environment
                raise RuntimeError("MUNIN_MASTER_KEY is not valid base64url or hex") from exc
        if len(candidate) != 32:
            raise RuntimeError("MUNIN_MASTER_KEY must decode to exactly 32 bytes")
        return candidate

    def _connect(self) -> Any:
        return self._connection_factory()

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        """Write transaction: ``BEGIN IMMEDIATE`` acquires the RESERVED lock.

        Reserved for paths that mutate state. Read paths must use
        :meth:`_read_only` instead so concurrent logins / SSE polls never
        queue behind a long-running encryption write.
        """
        conn = self._connect()
        committed = False
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
            committed = True
        except Exception:
            if not committed:
                with suppress(Exception):
                    conn.execute("ROLLBACK")
            raise
        finally:
            with suppress(Exception):
                conn.close()

    @contextmanager
    def _read_only(self) -> Iterator[Any]:
        """Read path: ``BEGIN DEFERRED`` never takes the RESERVED lock.

        Under WAL SQLite lets any number of readers proceed concurrently with
        a single writer, so this context never blocks on a run persisting
        events or encrypting artifacts. Callers must not issue INSERT /
        UPDATE / DELETE inside this context — use :meth:`_transaction`.
        """
        conn = self._connect()
        rolled_back = False
        try:
            conn.execute("BEGIN DEFERRED")
            yield conn
        except Exception:
            rolled_back = True
            with suppress(Exception):
                conn.execute("ROLLBACK")
            raise
        else:
            with suppress(Exception):
                conn.execute("COMMIT")
        finally:
            with suppress(Exception):
                if rolled_back:
                    pass
                conn.close()

    def migrate(self) -> None:
        with self._transaction() as conn:
            conn.execute(_DDL[0])
            row = conn.execute("SELECT checksum FROM schema_migrations WHERE migration_id = ?", (MIGRATION_ID,)).fetchone()
            if not row:
                for statement in _DDL[1:]:
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations (migration_id,checksum,applied_at_ms) VALUES (?,?,?)",
                    (MIGRATION_ID, MIGRATION_CHECKSUM, _now_ms()),
                )
            elif row["checksum"] != MIGRATION_CHECKSUM:
                raise RuntimeError("migration checksum mismatch; production migrations are forward-only")
        # Fase 2 idempotent additions live outside the checksum-locked block
        # so they can evolve without breaking every existing deployment.
        self._install_fase2_essentials()

    def _install_fase2_essentials(self) -> None:
        """Idempotently install the ex-``store_v3_1`` schema + column additions."""
        with self._transaction() as conn:
            for ddl in _FASE2_DDL:
                conn.execute(ddl)
            for table, column, coltype in _FASE2_OPTIONAL_COLUMNS:
                rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
                names = {str(r["name"] if hasattr(r, "keys") else r[1]) for r in rows}
                if column not in names:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS tool_calls_by_parallel_group "
                "ON tool_calls(parallel_group_id)"
            )

    # ------------------------------------------------------------------
    # Local-first delta sync tracking (issue #9 §3 conversation durability).
    #
    # Installs the ``_sync_outbox`` table + AFTER INSERT/UPDATE/DELETE
    # triggers on every table in ``_SYNC_TABLES``. Must ONLY be called on a
    # local hot backend — the durable namespace adapter must never see the
    # triggers, or it would re-record its own writes. Idempotent
    # (``IF NOT EXISTS`` on every object). Skipped silently when the backend
    # is a namespaced Turso proxy (``_NamespacedConnection``) so it is always
    # safe to call from :meth:`MuninStore.from_settings`.
    # ------------------------------------------------------------------
    def install_sync_tracking(self) -> None:
        """Install the local-only change outbox (idempotent, hot-only)."""
        try:
            with self._transaction() as conn:
                for ddl in _SYNC_META_DDL:
                    conn.execute(ddl)
                for table in _SYNC_TABLES:
                    for trigger in _sync_trigger_sql(table):
                        conn.execute(trigger)
        except Exception:  # noqa: BLE001
            # Non-fatal: the durable backend (Turso namespaced) does not
            # need (and must not have) local triggers.  A local sqlite hot
            # backend always accepts them; if the backend refuses (e.g. an
            # older schema missing a tracked table) we still boot — sync
            # degrades to "no delta tracking", server stays authoritative.
            logger.debug("sync tracking install skipped", exc_info=True)

    def sync_outbox_pending(self) -> int:
        """Count unflushed outbox entries (0 when tracking is absent)."""
        with suppress(Exception):
            with self._read_only() as conn:
                row = conn.execute("SELECT COUNT(*) AS n FROM _sync_outbox").fetchone()
                return int(row["n"] if hasattr(row, "keys") else row[0]) if row else 0
        return 0

    def schema_tables(self) -> set[str]:
        with self._read_only() as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            return {str(row["name"]) for row in rows}

    def applied_migration_ids(self) -> list[str]:
        with self._read_only() as conn:
            return [str(row["migration_id"]) for row in conn.execute("SELECT migration_id FROM schema_migrations ORDER BY applied_at_ms").fetchall()]

    def _audit(self, conn: Any, *, actor_id: str | None, action: str, resource_type: str, resource_id: str, outcome: str, metadata: Any = None) -> None:
        conn.execute(
            "INSERT INTO audit_events (id,actor_id,action,resource_type,resource_id,outcome,metadata_json,created_at_ms) VALUES (?,?,?,?,?,?,?,?)",
            (_id("audit"), actor_id, action, resource_type, resource_id, outcome, _json(metadata or {}), _now_ms()),
        )

    def record_audit(self, *, actor_id: str | None, action: str, resource_type: str, resource_id: str, outcome: str, metadata: Any = None) -> None:
        with self._transaction() as conn:
            self._audit(conn, actor_id=actor_id, action=action, resource_type=resource_type, resource_id=resource_id, outcome=outcome, metadata=metadata)

    def create_user(self, *, username: str, password: str, role: str) -> dict[str, Any]:
        normalized = username.strip().lower()
        if not normalized or len(normalized) > 120 or role not in ROLES or len(password) < 12:
            raise ValueError("invalid user credentials or role")
        now = _now_ms()
        user = {"id": _id("usr"), "username": normalized, "role": role, "created_at_ms": now}
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO users (id,username,password_hash,role,created_at_ms,updated_at_ms) VALUES (?,?,?,?,?,?)",
                (user["id"], normalized, self._passwords.hash(password), role, now, now),
            )
            self._audit(conn, actor_id=user["id"], action="user.created", resource_type="user", resource_id=user["id"], outcome="success")
        return user

    def delete_user_for_test(self, *, username: str) -> bool:
        """Remove a CI fixture user (and its sessions) by exact username."""
        normalized = username.strip().lower()
        if not normalized.startswith("llm_smoke_"):
            raise ValueError("refusing to delete a non-fixture user")
        with self._transaction() as conn:
            row = conn.execute("SELECT id FROM users WHERE username=?", (normalized,)).fetchone()
            if not row:
                return False
            conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (row["id"],))
            conn.execute("DELETE FROM users WHERE id=?", (row["id"],))
            self._audit(conn, actor_id=None, action="user.deleted", resource_type="user", resource_id=row["id"], outcome="success")
        return True

    def bootstrap_admin(self, *, username: str, password: str) -> dict[str, Any] | None:
        with self._transaction() as conn:
            if conn.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone():
                return None
            normalized = username.strip().lower()
            if not normalized or len(password) < 12:
                raise ValueError("bootstrap credentials do not meet policy")
            now = _now_ms()
            result = {"id": _id("usr"), "username": normalized, "role": "admin", "created_at_ms": now}
            conn.execute(
                "INSERT INTO users (id,username,password_hash,role,created_at_ms,updated_at_ms) VALUES (?,?,?,?,?,?)",
                (result["id"], normalized, self._passwords.hash(password), "admin", now, now),
            )
            self._audit(conn, actor_id=result["id"], action="bootstrap.completed", resource_type="user", resource_id=result["id"], outcome="success")
            return result

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def _new_session(self, conn: Any, *, user_id: str, ip_address: str, user_agent: str, rotated_from: str | None = None) -> dict[str, Any]:
        now = _now_ms()
        token = secrets.token_urlsafe(48)
        csrf_token = secrets.token_urlsafe(32)
        session_id = _id("ses")
        conn.execute(
            """INSERT INTO auth_sessions (id,user_id,token_hash,csrf_hash,created_at_ms,last_seen_at_ms,idle_expires_at_ms,absolute_expires_at_ms,replaced_by_session_id,ip_address,user_agent)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (session_id, user_id, self._token_hash(token), self._token_hash(csrf_token), now, now, now + 8 * 60 * 60 * 1000, now + 7 * 24 * 60 * 60 * 1000, None, ip_address[:128], user_agent[:512]),
        )
        if rotated_from:
            conn.execute("UPDATE auth_sessions SET revoked_at_ms=?,replaced_by_session_id=? WHERE id=? AND revoked_at_ms IS NULL", (now, session_id, rotated_from))
        return {"session_id": session_id, "token": token, "csrf_token": csrf_token, "idle_expires_at_ms": now + 8 * 60 * 60 * 1000, "absolute_expires_at_ms": now + 7 * 24 * 60 * 60 * 1000}

    def login(self, *, username: str, password: str, ip_address: str, user_agent: str) -> dict[str, Any]:
        normalized = username.strip().lower()
        subject = f"login:{normalized}:{ip_address[:64]}"
        with self._transaction() as conn:
            limit = conn.execute("SELECT failures,blocked_until_ms FROM auth_rate_limits WHERE subject=?", (subject,)).fetchone()
            if limit and int(limit["blocked_until_ms"]) > _now_ms():
                raise PermissionError("login temporarily rate limited")
            user = conn.execute("SELECT * FROM users WHERE username=?", (normalized,)).fetchone()
            valid = False
            if user and not user["disabled_at_ms"]:
                try:
                    valid = self._passwords.verify(user["password_hash"], password)
                except (VerifyMismatchError, InvalidHashError):
                    valid = False
            if not valid:
                failures = int(limit["failures"] or 0) + 1 if limit else 1
                blocked = _now_ms() + min(15 * 60 * 1000, (2 ** min(failures, 8)) * 1000) if failures >= 5 else 0
                conn.execute(
                    "INSERT INTO auth_rate_limits (subject,failures,blocked_until_ms,updated_at_ms) VALUES (?,?,?,?) ON CONFLICT(subject) DO UPDATE SET failures=excluded.failures,blocked_until_ms=excluded.blocked_until_ms,updated_at_ms=excluded.updated_at_ms",
                    (subject, failures, blocked, _now_ms()),
                )
                self._audit(conn, actor_id=None, action="login.failed", resource_type="session", resource_id=subject, outcome="denied")
                raise PermissionError("invalid credentials")
            conn.execute("DELETE FROM auth_rate_limits WHERE subject=?", (subject,))
            result = self._new_session(conn, user_id=user["id"], ip_address=ip_address, user_agent=user_agent)
            self._audit(conn, actor_id=user["id"], action="login.succeeded", resource_type="session", resource_id=result["session_id"], outcome="success")
            return result

    def issue_password_recovery(self, *, username: str, ttl_seconds: int = 1_800) -> dict[str, str] | None:
        """Return plaintext only to a trusted delivery adapter; DB receives a hash."""
        normalized = username.strip().lower()
        with self._transaction() as conn:
            user = conn.execute("SELECT id FROM users WHERE username=? AND disabled_at_ms IS NULL", (normalized,)).fetchone()
            if not user:
                self._audit(conn, actor_id=None, action="password_recovery.requested", resource_type="user", resource_id=normalized[:120], outcome="accepted")
                return None
            token = secrets.token_urlsafe(32)
            now = _now_ms()
            conn.execute("UPDATE password_recovery_tokens SET consumed_at_ms=? WHERE user_id=? AND consumed_at_ms IS NULL", (now, user["id"]))
            conn.execute("INSERT INTO password_recovery_tokens (id,user_id,token_hash,expires_at_ms,requested_at_ms) VALUES (?,?,?,?,?)", (_id("recover"), user["id"], self._token_hash(token), now + max(60, ttl_seconds) * 1000, now))
            self._audit(conn, actor_id=user["id"], action="password_recovery.requested", resource_type="user", resource_id=user["id"], outcome="accepted")
            return {"user_id": user["id"], "token": token}

    def consume_password_recovery(self, *, token: str, new_password: str) -> bool:
        if len(new_password) < 12:
            raise ValueError("password must have at least 12 characters")
        now = _now_ms()
        with self._transaction() as conn:
            row = conn.execute("SELECT * FROM password_recovery_tokens WHERE token_hash=?", (self._token_hash(token),)).fetchone()
            if not row or row["consumed_at_ms"] or int(row["expires_at_ms"]) < now:
                return False
            conn.execute("UPDATE password_recovery_tokens SET consumed_at_ms=? WHERE id=? AND consumed_at_ms IS NULL", (now, row["id"]))
            conn.execute("UPDATE users SET password_hash=?,updated_at_ms=? WHERE id=?", (self._passwords.hash(new_password), now, row["user_id"]))
            conn.execute("UPDATE auth_sessions SET revoked_at_ms=? WHERE user_id=? AND revoked_at_ms IS NULL", (now, row["user_id"]))
            self._audit(conn, actor_id=row["user_id"], action="password_recovery.completed", resource_type="user", resource_id=row["user_id"], outcome="success")
            return True

    def authenticate(self, token: str) -> dict[str, Any] | None:
        now = _now_ms()
        with self._read_only() as conn:
            row = conn.execute(
                """SELECT u.id,u.username,u.role,u.disabled_at_ms,s.id AS session_id,s.idle_expires_at_ms,s.absolute_expires_at_ms,s.revoked_at_ms,s.last_seen_at_ms
                FROM auth_sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?""",
                (self._token_hash(token),),
            ).fetchone()
            if not row or row["disabled_at_ms"] or row["revoked_at_ms"] or int(row["idle_expires_at_ms"]) <= now or int(row["absolute_expires_at_ms"]) <= now:
                return None
            session_id = row["session_id"]
            absolute = int(row["absolute_expires_at_ms"])
            last_seen = int(row["last_seen_at_ms"])
        if last_seen < now - _SESSION_TOUCH_INTERVAL_MS:
            try:
                with self._transaction() as conn:
                    conn.execute(
                        "UPDATE auth_sessions"
                        " SET last_seen_at_ms=?,idle_expires_at_ms=?"
                        " WHERE id=? AND revoked_at_ms IS NULL"
                        " AND last_seen_at_ms < ?",
                        (now, min(now + 8 * 60 * 60 * 1000, absolute), session_id,
                         now - _SESSION_TOUCH_INTERVAL_MS),
                    )
            except sqlite3.OperationalError:
                pass
        return {"id": row["id"], "username": row["username"], "role": row["role"], "session_id": session_id}

    def rotate_session(self, token: str) -> dict[str, Any]:
        with self._transaction() as conn:
            row = conn.execute("SELECT id,user_id,revoked_at_ms FROM auth_sessions WHERE token_hash=?", (self._token_hash(token),)).fetchone()
            if not row or row["revoked_at_ms"]:
                raise PermissionError("session is not active")
            result = self._new_session(conn, user_id=row["user_id"], ip_address="", user_agent="", rotated_from=row["id"])
            self._audit(conn, actor_id=row["user_id"], action="session.rotated", resource_type="session", resource_id=row["id"], outcome="success")
            return result

    def validate_csrf(self, *, session_id: str, csrf_token: str) -> bool:
        if not csrf_token:
            return False
        with self._read_only() as conn:
            row = conn.execute("SELECT csrf_hash,revoked_at_ms FROM auth_sessions WHERE id=?", (session_id,)).fetchone()
            return bool(row and not row["revoked_at_ms"] and hmac.compare_digest(str(row["csrf_hash"]), self._token_hash(csrf_token)))

    def refresh_csrf(self, session_id: str) -> str:
        """Issue a new anti-CSRF value after authenticating an HttpOnly session."""
        token = secrets.token_urlsafe(32)
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT revoked_at_ms,idle_expires_at_ms,absolute_expires_at_ms FROM auth_sessions WHERE id=?",
                (session_id,),
            ).fetchone()
            now = _now_ms()
            if not row or row["revoked_at_ms"] or int(row["idle_expires_at_ms"]) <= now or int(row["absolute_expires_at_ms"]) <= now:
                raise PermissionError("session is not active")
            conn.execute("UPDATE auth_sessions SET csrf_hash=? WHERE id=?", (self._token_hash(token), session_id))
        return token

    def revoke_session(self, session_id: str, *, actor_id: str) -> bool:
        with self._transaction() as conn:
            row = conn.execute("SELECT user_id,revoked_at_ms FROM auth_sessions WHERE id=?", (session_id,)).fetchone()
            if not row or row["user_id"] != actor_id or row["revoked_at_ms"]:
                return False
            conn.execute("UPDATE auth_sessions SET revoked_at_ms=? WHERE id=?", (_now_ms(), session_id))
            self._audit(conn, actor_id=actor_id, action="session.revoked", resource_type="session", resource_id=session_id, outcome="success")
            return True

    def session_record(self, session_id: str) -> dict[str, Any] | None:
        with self._read_only() as conn:
            row = conn.execute("SELECT id,user_id,created_at_ms,last_seen_at_ms,idle_expires_at_ms,absolute_expires_at_ms,revoked_at_ms,replaced_by_session_id FROM auth_sessions WHERE id=?", (session_id,)).fetchone()
            return _row(row) if row else None

    def create_conversation(self, *, owner_id: str, title: str, tags: list[str] | None = None, scope: dict[str, Any] | None = None) -> dict[str, Any]:
        now = _now_ms()
        result = {"id": _id("conv"), "owner_id": owner_id, "title": " ".join(title.split())[:160] or "New conversation", "created_at_ms": now}
        with self._transaction() as conn:
            self._require_user(conn, owner_id)
            conn.execute(
                "INSERT INTO conversations (id,owner_id,title,tags_json,scope_json,last_activity_at_ms,created_at_ms,updated_at_ms) VALUES (?,?,?,?,?,?,?,?)",
                (result["id"], owner_id, result["title"], _json(tags or []), _json(scope or {}), now, now, now),
            )
            conn.execute("INSERT INTO conversation_participants (conversation_id,user_id,role,added_at_ms) VALUES (?,?,?,?)", (result["id"], owner_id, "owner", now))
            self._audit(conn, actor_id=owner_id, action="conversation.created", resource_type="conversation", resource_id=result["id"], outcome="success")
        return result

    @staticmethod
    def _require_user(conn: Any, user_id: str) -> None:
        if not conn.execute("SELECT 1 FROM users WHERE id=? AND disabled_at_ms IS NULL", (user_id,)).fetchone():
            raise PermissionError("actor is not active")

    @staticmethod
    def _next_sequence(conn: Any, *, table: str, key: str, key_value: str) -> int:
        if (table, key) == ("messages", "conversation_id"):
            row = conn.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 AS next_sequence FROM messages WHERE conversation_id=?",
                (key_value,),
            ).fetchone()
        elif (table, key) == ("run_events", "run_id"):
            row = conn.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 AS next_sequence FROM run_events WHERE run_id=?",
                (key_value,),
            ).fetchone()
        else:  # pragma: no cover - defensive programmer error
            raise ValueError("unsupported sequence source")
        return int(row["next_sequence"])

    def _require_participant(self, conn: Any, *, actor_id: str, conversation_id: str) -> None:
        row = conn.execute(
            "SELECT 1 FROM conversation_participants WHERE conversation_id=? AND user_id=? AND removed_at_ms IS NULL",
            (conversation_id, actor_id),
        ).fetchone()
        if not row:
            raise PermissionError("actor is not authorized for this conversation")

    def _append_event(self, conn: Any, *, run_id: str, kind: str, payload: Any, actor_id: str | None = None, causation_id: str | None = None) -> dict[str, Any]:
        sequence = self._next_sequence(conn, table="run_events", key="run_id", key_value=run_id)
        event = {"id": _id("evt"), "run_id": run_id, "sequence": sequence, "kind": kind, "payload": redact_payload(payload), "created_at_ms": _now_ms()}
        conn.execute(
            "INSERT INTO run_events (id,run_id,sequence,kind,payload_json,causation_id,correlation_id,actor_id,redaction_policy_version,created_at_ms) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (event["id"], run_id, sequence, kind, _json(event["payload"]), causation_id, run_id, actor_id, REDACTION_POLICY_VERSION, event["created_at_ms"]),
        )
        return event

    def create_turn(self, *, actor_id: str, conversation_id: str, content: str, idempotency_key: str) -> dict[str, Any]:
        if not content.strip() or not idempotency_key.strip():
            raise ValueError("content and idempotency key are required")
        if len(content) > 1_000_000:
            raise ValueError("message exceeds maximum size")
        request_hash = hashlib.sha256(content.encode()).hexdigest()
        with self._transaction() as conn:
            self._require_participant(conn, actor_id=actor_id, conversation_id=conversation_id)
            previous = conn.execute(
                "SELECT * FROM agent_runs WHERE conversation_id=? AND actor_id=? AND idempotency_key=?",
                (conversation_id, actor_id, idempotency_key),
            ).fetchone()
            if previous:
                if previous["request_hash"] != request_hash:
                    raise ValueError("idempotency key was reused with a different request body")
                return {"idempotent_replay": True, "run": self._run_dict(previous), "user_message_id": previous["user_message_id"], "assistant_message_id": previous["assistant_message_id"]}
            now = _now_ms()
            user_message_id = _id("msg")
            assistant_message_id = _id("msg")
            run_id = _id("run")
            user_sequence = self._next_sequence(conn, table="messages", key="conversation_id", key_value=conversation_id)
            safe_content = redact_text(content)
            conn.execute(
                "INSERT INTO messages (id,conversation_id,sequence,author_id,run_id,kind,status,content,content_hash,created_at_ms,updated_at_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (user_message_id, conversation_id, user_sequence, actor_id, run_id, "user", "completed", safe_content, hashlib.sha256(safe_content.encode()).hexdigest(), now, now),
            )
            conn.execute(
                "INSERT INTO messages (id,conversation_id,sequence,author_id,run_id,kind,status,content,content_hash,created_at_ms,updated_at_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (assistant_message_id, conversation_id, user_sequence + 1, None, run_id, "assistant_placeholder", "queued", "", hashlib.sha256(b"").hexdigest(), now, now),
            )
            conn.execute(
                """INSERT INTO agent_runs (id,conversation_id,actor_id,user_message_id,assistant_message_id,root_run_id,attempt,state,idempotency_key,request_hash,created_at_ms,updated_at_ms)
                VALUES (?,?,?,?,?,?,1,'queued',?,?,?,?)""",
                (run_id, conversation_id, actor_id, user_message_id, assistant_message_id, run_id, idempotency_key, request_hash, now, now),
            )
            event = self._append_event(conn, run_id=run_id, kind="run.queued", payload={"message_id": user_message_id, "assistant_message_id": assistant_message_id}, actor_id=actor_id)
            conn.execute("UPDATE conversations SET last_activity_at_ms=?,updated_at_ms=?,version=version+1 WHERE id=?", (now, now, conversation_id))
            self._audit(conn, actor_id=actor_id, action="turn.created", resource_type="run", resource_id=run_id, outcome="success", metadata={"event_id": event["id"]})
            return {"idempotent_replay": False, "run": {"id": run_id, "state": "queued", "fencing_epoch": 0}, "user_message_id": user_message_id, "assistant_message_id": assistant_message_id}

    @staticmethod
    def _run_dict(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"], "conversation_id": row["conversation_id"], "state": row["state"], "attempt": int(row["attempt"]),
            "fencing_epoch": int(row["fencing_epoch"]), "assistant_message_id": row["assistant_message_id"], "updated_at_ms": int(row["updated_at_ms"]),
        }

    # ``claim_next_run`` was removed in Fase 2 (issue #9). The lease-based
    # dispatcher is gone; ``POST /api/chat`` in ``munin/production/chat.py``
    # promotes a queued run to ``running`` inline via ``_claim_direct`` and
    # holds the lease for the lifetime of the request handler.

    def complete_run(self, *, run_id: str, lease_token: str, content: str, outcome: str) -> bool:
        if outcome not in FINAL_RUN_STATES:
            raise ValueError("outcome must be a final run state")
        with self._transaction() as conn:
            row = conn.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
            if not row or row["state"] != "running" or not hmac.compare_digest(str(row["lease_token"] or ""), lease_token):
                return False
            now = _now_ms()
            if int(row["lease_expires_at_ms"] or 0) < now:
                return False
            safe_content = redact_text(content)
            changed = conn.execute(
                "UPDATE agent_runs SET state=?,lease_token=NULL,lease_expires_at_ms=NULL,state_version=state_version+1,updated_at_ms=? WHERE id=? AND state='running' AND lease_token=? AND fencing_epoch=?",
                (outcome, now, run_id, lease_token, row["fencing_epoch"]),
            )
            if int(changed.rowcount) != 1:
                return False
            message = conn.execute("SELECT version FROM messages WHERE id=?", (row["assistant_message_id"],)).fetchone()
            conn.execute(
                "UPDATE messages SET kind='assistant',status=?,content=?,content_hash=?,version=version+1,updated_at_ms=? WHERE id=? AND version=?",
                (outcome, safe_content, hashlib.sha256(safe_content.encode()).hexdigest(), now, row["assistant_message_id"], message["version"]),
            )
            conn.execute(
                "INSERT INTO message_revisions (id,message_id,version,content,reason,created_at_ms) VALUES (?,?,?,?,?,?)",
                (_id("rev"), row["assistant_message_id"], int(message["version"]) + 1, safe_content, f"run.{outcome}", now),
            )
            for index, match in enumerate(_FENCED_ARTIFACT.finditer(safe_content), start=1):
                language = (match.group("language") or "markdown").lower()
                content = match.group("content")
                if content.strip():
                    extension, media_type = {"python": ("py", "text/x-python"), "py": ("py", "text/x-python"), "json": ("json", "application/json"), "markdown": ("md", "text/markdown"), "md": ("md", "text/markdown")}.get(language, ("txt", "text/plain"))
                    self._insert_artifact(conn, conversation_id=row["conversation_id"], message_id=row["assistant_message_id"], run_id=run_id, filename=f"run-{run_id[-8:]}-{index}.{extension}", media_type=media_type, language=language, content=content, now=now)
            self._append_event(conn, run_id=run_id, kind=f"run.{outcome}", payload={"assistant_message_id": row["assistant_message_id"]})
            conn.execute("UPDATE conversations SET last_activity_at_ms=?,updated_at_ms=?,version=version+1 WHERE id=?", (now, now, row["conversation_id"]))
            return True

    @staticmethod
    def _insert_artifact(conn: Any, *, conversation_id: str, message_id: str | None, run_id: str | None, filename: str, media_type: str, language: str, content: str, now: int | None = None) -> dict[str, Any]:
        safe_content = redact_text(content)
        if not safe_content or len(safe_content.encode()) > 1_000_000:
            raise ValueError("artifact content is empty or exceeds maximum size")
        artifact = {"id": _id("artifact"), "conversation_id": conversation_id, "message_id": message_id, "run_id": run_id, "filename": re.sub(r"[^A-Za-z0-9._-]", "_", filename)[:180] or "artifact.txt", "media_type": media_type[:120] or "text/plain", "language": language[:48] or "text", "content": safe_content, "content_hash": hashlib.sha256(safe_content.encode()).hexdigest(), "size_bytes": len(safe_content.encode()), "created_at_ms": now or _now_ms()}
        conn.execute(
            "INSERT INTO conversation_artifacts (id,conversation_id,message_id,run_id,filename,media_type,language,content,content_hash,size_bytes,created_at_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (artifact["id"], artifact["conversation_id"], artifact["message_id"], artifact["run_id"], artifact["filename"], artifact["media_type"], artifact["language"], artifact["content"], artifact["content_hash"], artifact["size_bytes"], artifact["created_at_ms"]),
        )
        return artifact

    def add_artifact(self, *, actor_id: str, conversation_id: str, filename: str, media_type: str, language: str, content: str, message_id: str | None = None, run_id: str | None = None) -> dict[str, Any]:
        with self._transaction() as conn:
            self._require_participant(conn, actor_id=actor_id, conversation_id=conversation_id)
            artifact = self._insert_artifact(conn, conversation_id=conversation_id, message_id=message_id, run_id=run_id, filename=filename, media_type=media_type, language=language, content=content)
            self._audit(conn, actor_id=actor_id, action="artifact.created", resource_type="artifact", resource_id=artifact["id"], outcome="success", metadata={"content_hash": artifact["content_hash"]})
            return {key: value for key, value in artifact.items() if key != "content"}

    def get_artifact(self, *, actor_id: str, artifact_id: str) -> dict[str, Any]:
        with self._read_only() as conn:
            artifact = conn.execute("SELECT a.* FROM conversation_artifacts a WHERE a.id=?", (artifact_id,)).fetchone()
            if not artifact:
                raise KeyError(artifact_id)
            self._require_participant(conn, actor_id=actor_id, conversation_id=artifact["conversation_id"])
            return _row(artifact)

    # ``renew_lease`` was removed in Fase 2 (issue #9). The dispatcher owned
    # the heartbeat loop; ``/api/chat`` now owns the run for the duration of
    # the request and burns a lease long enough (``MUNIN_CHAT_LEASE_SECONDS``,
    # default 4h) that mid-run renewals are not needed.

    def run_execution_context(self, *, run_id: str) -> dict[str, Any]:
        """Load the bounded durable transcript a worker is allowed to use."""
        with self._read_only() as conn:
            run = conn.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
            if not run:
                raise KeyError(run_id)
            rows = conn.execute(
                "SELECT kind,content FROM messages WHERE conversation_id=? AND kind IN ('user','assistant') ORDER BY sequence DESC LIMIT 16",
                (run["conversation_id"],),
            ).fetchall()
            prompt = conn.execute("SELECT content FROM messages WHERE id=?", (run["user_message_id"],)).fetchone()
            return {
                "run": self._run_dict(run),
                "actor_id": run["actor_id"],
                "conversation_id": run["conversation_id"],
                "message": prompt["content"] if prompt else "",
                "history": [{"role": "assistant" if row["kind"] == "assistant" else "user", "content": row["content"]} for row in reversed(rows)],
            }

    def force_run_lease_expiry(self, run_id: str, when: Any) -> None:
        millis = int(when.timestamp() * 1000) if hasattr(when, "timestamp") else int(when)
        with self._transaction() as conn:
            conn.execute("UPDATE agent_runs SET lease_expires_at_ms=? WHERE id=?", (millis, run_id))

    def recover_expired_runs(self) -> list[str]:
        recovered: list[str] = []
        with self._transaction() as conn:
            now = _now_ms()
            rows = conn.execute("SELECT * FROM agent_runs WHERE state='running' AND lease_expires_at_ms IS NOT NULL AND lease_expires_at_ms < ?", (now,)).fetchall()
            for row in rows:
                changed = conn.execute(
                    "UPDATE agent_runs SET state='interrupted',lease_token=NULL,lease_expires_at_ms=NULL,state_version=state_version+1,updated_at_ms=? WHERE id=? AND state='running' AND fencing_epoch=?",
                    (now, row["id"], row["fencing_epoch"]),
                )
                if int(changed.rowcount) != 1:
                    continue
                conn.execute("UPDATE messages SET status='interrupted',updated_at_ms=?,version=version+1 WHERE id=?", (now, row["assistant_message_id"]))
                self._append_event(conn, run_id=row["id"], kind="run.interrupted", payload={"reason": "lease_expired"})
                recovered.append(str(row["id"]))
        return recovered

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._read_only() as conn:
            row = conn.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise KeyError(run_id)
            return self._run_dict(row)

    def get_run_for_actor(self, *, actor_id: str, run_id: str) -> dict[str, Any]:
        with self._read_only() as conn:
            row = conn.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise KeyError(run_id)
            self._require_participant(conn, actor_id=actor_id, conversation_id=row["conversation_id"])
            return self._run_dict(row)

    def get_run_detail_for_actor(self, *, actor_id: str, run_id: str) -> dict[str, Any]:
        with self._read_only() as conn:
            run = conn.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
            if not run:
                raise KeyError(run_id)
            self._require_participant(conn, actor_id=actor_id, conversation_id=run["conversation_id"])
            reasoning = conn.execute("SELECT * FROM reasoning_events WHERE run_id=? ORDER BY created_at_ms,id", (run_id,)).fetchall()
            tools = conn.execute("SELECT * FROM tool_calls WHERE run_id=? ORDER BY started_at_ms,id", (run_id,)).fetchall()
            subagents = conn.execute("SELECT * FROM subagent_runs WHERE parent_run_id=? ORDER BY started_at_ms,id", (run_id,)).fetchall()
            requests = conn.execute("SELECT * FROM human_requests WHERE run_id=? ORDER BY created_at_ms,id", (run_id,)).fetchall()
            artifacts = conn.execute("SELECT id,filename,media_type,language,content_hash,size_bytes,created_at_ms FROM conversation_artifacts WHERE run_id=? ORDER BY created_at_ms,id", (run_id,)).fetchall()
            return {
                "run": self._run_dict(run), "events": self.list_run_events(run_id),
                "reasoning": [{"id": row["id"], "event_id": row["event_id"], "kind": row["kind"], "content": row["content"], "provider": row["provider"], "agent_name": row["agent_name"], "step": int(row["step"]), "persisted": bool(row["persisted"]), "provenance": row["provenance"], "created_at_ms": int(row["created_at_ms"])} for row in reasoning],
                "tools": [{"id": row["id"], "event_id": row["event_id"], "agent_name": row["agent_name"], "tool_name": row["tool_name"], "state": row["state"], "arguments": json.loads(row["args_json"]), "result": json.loads(row["result_json"]), "scope": json.loads(row["scope_json"]), "started_at_ms": int(row["started_at_ms"]), "finished_at_ms": row["finished_at_ms"]} for row in tools],
                "subagents": [{"id": row["id"], "profile_id": row["profile_id"], "state": row["state"], "objective": row["objective"], "started_at_ms": row["started_at_ms"], "finished_at_ms": row["finished_at_ms"]} for row in subagents],
                "human_requests": [{"id": row["id"], "action": row["action"], "risk": row["risk"], "evidence": json.loads(row["evidence_json"]), "scope": json.loads(row["scope_json"]), "choices": json.loads(row["choices_json"]), "state": row["state"], "expires_at_ms": int(row["expires_at_ms"]), "created_at_ms": int(row["created_at_ms"]), "resolved_at_ms": row["resolved_at_ms"]} for row in requests],
                "artifacts": [_row(row) for row in artifacts],
            }

    def list_run_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._read_only() as conn:
            rows = conn.execute("SELECT * FROM run_events WHERE run_id=? ORDER BY sequence", (run_id,)).fetchall()
            return [{"id": row["id"], "run_id": row["run_id"], "sequence": int(row["sequence"]), "kind": row["kind"], "payload": json.loads(row["payload_json"]), "created_at_ms": int(row["created_at_ms"])} for row in rows]

    def run_events_after(self, *, run_id: str, after_sequence: int) -> list[dict[str, Any]]:
        """Return events with sequence > after_sequence, filtering in SQL.

        Uses the existing ``idx_run_events_run_sequence`` index; avoids
        deserialising the full run history on every SSE poll.
        """
        threshold = max(0, after_sequence)
        with self._read_only() as conn:
            rows = conn.execute(
                "SELECT * FROM run_events"
                " WHERE run_id=? AND sequence>?"
                " ORDER BY sequence",
                (run_id, threshold),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "run_id": row["run_id"],
                "sequence": int(row["sequence"]),
                "kind": row["kind"],
                "payload": json.loads(row["payload_json"]),
                "created_at_ms": int(row["created_at_ms"]),
            }
            for row in rows
        ]

    def get_conversation(self, *, actor_id: str, conversation_id: str) -> dict[str, Any]:
        with self._read_only() as conn:
            self._require_participant(conn, actor_id=actor_id, conversation_id=conversation_id)
            conversation = conn.execute("SELECT * FROM conversations WHERE id=? AND deleted_at_ms IS NULL", (conversation_id,)).fetchone()
            if not conversation:
                raise KeyError(conversation_id)
            messages = conn.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY sequence", (conversation_id,)).fetchall()
            runs = conn.execute("SELECT * FROM agent_runs WHERE conversation_id=? ORDER BY created_at_ms", (conversation_id,)).fetchall()
            return {
                "conversation": _row(conversation),
                "messages": [{"id": row["id"], "kind": row["kind"], "status": row["status"], "content": row["content"], "sequence": int(row["sequence"]), "run_id": row["run_id"]} for row in messages],
                "runs": [self._run_dict(row) for row in runs],
            }

    def list_conversations(
        self,
        *,
        actor_id: str,
        query: str = "",
        status: str = "",
        include_archived: bool = False,
        limit: int = 50,
        cursor_ms: int | None = None,
    ) -> dict[str, Any]:
        """Server-side owner/participant search and cursor pagination."""
        with self._read_only() as conn:
            normalized_query = query.strip().lower()[:160]
            needle = f"%{normalized_query}%"
            normalized_status = status.strip()[:40]
            normalized_cursor = int(cursor_ms) if cursor_ms is not None else None
            params: list[Any] = [
                actor_id,
                int(include_archived),
                normalized_status,
                normalized_status,
                normalized_cursor,
                normalized_cursor,
                normalized_query,
                needle,
                needle,
                needle,
                needle,
                needle,
                needle,
                needle,
                needle,
                max(1, min(int(limit), 100)),
            ]
            rows = conn.execute(
                """SELECT c.id,c.owner_id,c.title,c.summary,c.status,c.tags_json,c.scope_json,c.last_activity_at_ms,c.archived_at_ms,c.version,
                    (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) AS message_count
                   FROM conversations c JOIN conversation_participants p ON p.conversation_id=c.id
                   WHERE p.user_id=? AND p.removed_at_ms IS NULL AND c.deleted_at_ms IS NULL
                     AND (?=1 OR c.archived_at_ms IS NULL)
                     AND (?='' OR c.status=?)
                     AND (? IS NULL OR c.last_activity_at_ms < ?)
                     AND (?='' OR LOWER(c.title) LIKE ? OR LOWER(c.summary) LIKE ? OR LOWER(c.tags_json) LIKE ?
                         OR EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id=c.id AND LOWER(m.content) LIKE ?)
                         OR EXISTS (SELECT 1 FROM conversation_summaries s WHERE s.conversation_id=c.id AND (LOWER(s.findings_json) LIKE ? OR LOWER(s.entities_json) LIKE ?))
                         OR EXISTS (SELECT 1 FROM tool_calls t JOIN agent_runs r ON r.id=t.run_id WHERE r.conversation_id=c.id AND (LOWER(t.tool_name) LIKE ? OR LOWER(t.agent_name) LIKE ?)))
                   ORDER BY c.last_activity_at_ms DESC,c.id DESC LIMIT ?""",
                params,
            ).fetchall()
            results = []
            for row in rows:
                results.append(
                    {
                        "id": row["id"], "owner_id": row["owner_id"], "title": row["title"], "summary": row["summary"],
                        "status": row["status"], "tags": json.loads(row["tags_json"]), "scope": json.loads(row["scope_json"]),
                        "last_activity_at_ms": int(row["last_activity_at_ms"]), "archived_at_ms": row["archived_at_ms"],
                        "message_count": int(row["message_count"]), "version": int(row["version"]),
                    }
                )
            next_cursor = results[-1]["last_activity_at_ms"] if len(results) == max(1, min(int(limit), 100)) else None
            return {"conversations": results, "next_cursor_ms": next_cursor}

    def rename_conversation(self, *, actor_id: str, conversation_id: str, title: str, expected_version: int) -> dict[str, Any]:
        normalized = " ".join(title.split())[:160]
        if not normalized:
            raise ValueError("title is required")
        with self._transaction() as conn:
            self._require_participant(conn, actor_id=actor_id, conversation_id=conversation_id)
            now = _now_ms()
            changed = conn.execute(
                "UPDATE conversations SET title=?,version=version+1,updated_at_ms=? WHERE id=? AND version=? AND deleted_at_ms IS NULL",
                (normalized, now, conversation_id, expected_version),
            )
            if int(changed.rowcount) != 1:
                raise RuntimeError("conversation version conflict")
            row = conn.execute("SELECT title,version FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            self._audit(conn, actor_id=actor_id, action="conversation.renamed", resource_type="conversation", resource_id=conversation_id, outcome="success")
            return {"id": conversation_id, "title": row["title"], "version": int(row["version"])}

    def set_conversation_archive(self, *, actor_id: str, conversation_id: str, archived: bool, expected_version: int) -> dict[str, Any]:
        with self._transaction() as conn:
            self._require_participant(conn, actor_id=actor_id, conversation_id=conversation_id)
            now = _now_ms()
            changed = conn.execute(
                "UPDATE conversations SET archived_at_ms=?,version=version+1,updated_at_ms=? WHERE id=? AND version=? AND deleted_at_ms IS NULL",
                (now if archived else None, now, conversation_id, expected_version),
            )
            if int(changed.rowcount) != 1:
                raise RuntimeError("conversation version conflict")
            row = conn.execute("SELECT archived_at_ms,version FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            self._audit(conn, actor_id=actor_id, action="conversation.archived" if archived else "conversation.restored", resource_type="conversation", resource_id=conversation_id, outcome="success")
            return {"id": conversation_id, "archived_at_ms": row["archived_at_ms"], "version": int(row["version"])}

    def soft_delete_conversation(self, *, actor_id: str, conversation_id: str, expected_version: int) -> bool:
        with self._transaction() as conn:
            row = conn.execute("SELECT owner_id FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            if not row or (row["owner_id"] != actor_id and not self._is_admin(conn, actor_id)):
                raise PermissionError("only the owner or admin may delete a conversation")
            changed = conn.execute(
                "UPDATE conversations SET deleted_at_ms=?,version=version+1,updated_at_ms=? WHERE id=? AND version=? AND deleted_at_ms IS NULL",
                (_now_ms(), _now_ms(), conversation_id, expected_version),
            )
            if int(changed.rowcount) != 1:
                raise RuntimeError("conversation version conflict")
            self._audit(conn, actor_id=actor_id, action="conversation.soft_deleted", resource_type="conversation", resource_id=conversation_id, outcome="success")
            return True

    @staticmethod
    def _is_admin(conn: Any, actor_id: str) -> bool:
        row = conn.execute("SELECT role FROM users WHERE id=? AND disabled_at_ms IS NULL", (actor_id,)).fetchone()
        return bool(row and row["role"] == "admin")

    def export_conversation(self, *, actor_id: str, conversation_id: str) -> dict[str, Any]:
        aggregate = self.get_conversation(actor_id=actor_id, conversation_id=conversation_id)
        with self._read_only() as conn:
            artifacts = conn.execute("SELECT id,filename,media_type,language,content_hash,size_bytes,run_id,message_id FROM conversation_artifacts WHERE conversation_id=? ORDER BY created_at_ms", (conversation_id,)).fetchall()
            events = conn.execute("SELECT e.* FROM run_events e JOIN agent_runs r ON r.id=e.run_id WHERE r.conversation_id=? ORDER BY e.created_at_ms,e.sequence", (conversation_id,)).fetchall()
            aggregate["artifacts"] = [_row(row) for row in artifacts]
            aggregate["events"] = [{**_row(row), "payload": json.loads(row["payload_json"])} for row in events]
            return redact_payload(aggregate)

    def append_reasoning_event(self, *, run_id: str, kind: str, content: str, provider: str, persistence_enabled: bool, agent_name: str = "munin", step: int = 0) -> dict[str, Any]:
        # ``operator_guidance`` — Fase 1a: a durable audit row for operator
        # hints injected mid-run via ``POST /api/chat/{run_id}/guidance``.
        # The middleware still delivers the guidance through the queue; this
        # row is the audit trail (mirrors the pre-issue-9 ``operator.guidance``
        # run-event but lives on reasoning_events so the AI SDK stream can
        # surface it inline alongside model output).
        if kind not in {"provider_reasoning", "operational_summary", "tool_intent", "observation", "decision", "model_request", "operator_guidance"}:
            raise ValueError("unknown reasoning event kind")
        safe_content = redact_text(content)
        with self._transaction() as conn:
            run = conn.execute("SELECT 1 FROM agent_runs WHERE id=?", (run_id,)).fetchone()
            if not run:
                raise KeyError(run_id)
            event = self._append_event(conn, run_id=run_id, kind=f"reasoning.{kind}", payload={"provider": provider, "agent": agent_name, "step": step})
            reasoning_id = _id("reason")
            conn.execute(
                "INSERT INTO reasoning_events (id,run_id,event_id,kind,content,provider,agent_name,step,persisted,provenance,created_at_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (reasoning_id, run_id, event["id"], kind, safe_content if persistence_enabled else "[PERSISTENCE_DISABLED]", provider[:64], agent_name[:128], max(0, step), int(persistence_enabled), "provider" if kind == "provider_reasoning" else "operational", _now_ms()),
            )
            return {"id": reasoning_id, "event_id": event["id"], "content": safe_content if persistence_enabled else "[PERSISTENCE_DISABLED]", "kind": kind}

    def append_tool_call(
        self,
        *,
        run_id: str,
        agent_name: str,
        tool_name: str,
        state: str,
        arguments: Any,
        result: Any = None,
        scope: Any = None,
        tool_call_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist a redacted tool lifecycle as an immutable event plus a queryable read model."""
        if state not in {"queued", "running", "completed", "failed", "cancelled"}:
            raise ValueError("unknown tool state")
        now = _now_ms()
        safe_args, safe_result, safe_scope = redact_payload(arguments), redact_payload(result or {}), redact_payload(scope or {})
        with self._transaction() as conn:
            run = conn.execute("SELECT id FROM agent_runs WHERE id=?", (run_id,)).fetchone()
            if not run:
                raise KeyError(run_id)
            identifier = tool_call_id or _id("tool")
            current = conn.execute("SELECT * FROM tool_calls WHERE id=?", (identifier,)).fetchone()
            event = self._append_event(conn, run_id=run_id, kind=f"tool.{state}", payload={"tool_call_id": identifier, "tool": tool_name, "agent": agent_name, "scope": safe_scope})
            if current:
                if current["run_id"] != run_id or current["tool_name"] != tool_name:
                    raise ValueError("tool call identity conflicts with an existing call")
                conn.execute(
                    "UPDATE tool_calls SET state=?,result_json=?,finished_at_ms=? WHERE id=?",
                    (state, _json(safe_result), now if state in {"completed", "failed", "cancelled"} else None, identifier),
                )
            else:
                conn.execute(
                    "INSERT INTO tool_calls (id,run_id,event_id,agent_name,tool_name,state,args_json,result_json,scope_json,started_at_ms,finished_at_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (identifier, run_id, event["id"], agent_name[:128], tool_name[:160], state, _json(safe_args), _json(safe_result), _json(safe_scope), now, now if state in {"completed", "failed", "cancelled"} else None),
                )
            return {"id": identifier, "event_id": event["id"], "state": state, "tool_name": tool_name, "arguments": safe_args, "result": safe_result}

    def create_subagent_run(self, *, parent_run_id: str, profile_id: str, objective: str) -> dict[str, Any]:
        if not objective.strip():
            raise ValueError("subagent objective is required")
        now = _now_ms()
        subagent = {"id": _id("subrun"), "parent_run_id": parent_run_id, "profile_id": profile_id[:160], "objective": redact_text(objective)[:4_000], "state": "queued", "created_at_ms": now}
        with self._transaction() as conn:
            if not conn.execute("SELECT 1 FROM agent_runs WHERE id=?", (parent_run_id,)).fetchone():
                raise KeyError(parent_run_id)
            conn.execute(
                "INSERT INTO subagent_runs (id,parent_run_id,profile_id,state,objective) VALUES (?,?,?,?,?)",
                (subagent["id"], parent_run_id, subagent["profile_id"], subagent["state"], subagent["objective"]),
            )
            self._append_event(conn, run_id=parent_run_id, kind="subagent.queued", payload={"subagent_run_id": subagent["id"], "profile_id": subagent["profile_id"], "objective": subagent["objective"]})
        return subagent

    def request_human_decision(
        self,
        *,
        run_id: str,
        action: str,
        risk: str,
        evidence: Any,
        scope: Any,
        choices: list[str],
        expires_in_seconds: int = 3_600,
    ) -> dict[str, Any]:
        """Gate work with a nonce that is stored only as a hash and consumed once."""
        if risk not in {"low", "medium", "high", "critical"} or not choices:
            raise ValueError("human request requires a risk level and choices")
        nonce = secrets.token_urlsafe(32)
        now = _now_ms()
        request = {"id": _id("hitl"), "run_id": run_id, "action": redact_text(action)[:500], "risk": risk, "choices": [redact_text(choice)[:120] for choice in choices], "expires_at_ms": now + max(60, expires_in_seconds) * 1000, "nonce": nonce}
        with self._transaction() as conn:
            run = conn.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
            if not run or run["state"] not in {"queued", "running"}:
                raise ValueError("only a queued or running run can request human input")
            conn.execute(
                "INSERT INTO human_requests (id,run_id,action,args_hash,risk,evidence_json,scope_json,choices_json,nonce_hash,state,expires_at_ms,created_at_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (request["id"], run_id, request["action"], hashlib.sha256(_json(scope).encode()).hexdigest(), risk, _json(evidence), _json(scope), _json(request["choices"]), self._token_hash(nonce), "waiting", request["expires_at_ms"], now),
            )
            conn.execute(
                "UPDATE agent_runs SET state='waiting_for_human',lease_token=NULL,lease_expires_at_ms=NULL,state_version=state_version+1,updated_at_ms=? WHERE id=?",
                (now, run_id),
            )
            conn.execute("UPDATE messages SET status='waiting_for_human',updated_at_ms=?,version=version+1 WHERE id=?", (now, run["assistant_message_id"]))
            self._append_event(conn, run_id=run_id, kind="human_request.created", payload={"human_request_id": request["id"], "action": request["action"], "risk": risk, "choices": request["choices"]})
        return request

    def resolve_human_decision(self, *, actor_id: str, request_id: str, choice: str, nonce: str, guidance: str = "") -> dict[str, Any]:
        now = _now_ms()
        with self._transaction() as conn:
            request = conn.execute("SELECT h.*,r.conversation_id,r.assistant_message_id FROM human_requests h JOIN agent_runs r ON r.id=h.run_id WHERE h.id=?", (request_id,)).fetchone()
            if not request:
                raise KeyError(request_id)
            self._require_participant(conn, actor_id=actor_id, conversation_id=request["conversation_id"])
            allowed = json.loads(request["choices_json"])
            if request["state"] != "waiting" or int(request["expires_at_ms"]) < now or choice not in allowed or not hmac.compare_digest(request["nonce_hash"], self._token_hash(nonce)):
                raise PermissionError("human request is invalid, expired, or already resolved")
            response = {"choice": choice, "guidance": redact_text(guidance)[:4_000], "actor_id": actor_id}
            conn.execute("UPDATE human_requests SET state='resolved',response_json=?,resolved_at_ms=? WHERE id=? AND state='waiting'", (_json(response), now, request_id))
            terminal = choice.lower().startswith("reject")
            target = "cancelled" if terminal else "queued"
            conn.execute("UPDATE agent_runs SET state=?,state_version=state_version+1,updated_at_ms=? WHERE id=? AND state='waiting_for_human'", (target, now, request["run_id"]))
            conn.execute("UPDATE messages SET status=?,updated_at_ms=?,version=version+1 WHERE id=?", (target, now, request["assistant_message_id"]))
            self._append_event(conn, run_id=request["run_id"], kind="human_request.resolved", payload={"human_request_id": request_id, "choice": choice, "guidance": response["guidance"]}, actor_id=actor_id)
            self._audit(conn, actor_id=actor_id, action="human_request.resolved", resource_type="human_request", resource_id=request_id, outcome="success", metadata={"choice": choice})
            return {"id": request_id, "run_id": request["run_id"], "state": target, "choice": choice}

    def request_run_cancellation(self, *, actor_id: str, run_id: str) -> dict[str, Any]:
        with self._transaction() as conn:
            run = conn.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
            if not run:
                raise KeyError(run_id)
            self._require_participant(conn, actor_id=actor_id, conversation_id=run["conversation_id"])
            if run["state"] in FINAL_RUN_STATES:
                return self._run_dict(run)
            now = _now_ms()
            conn.execute("UPDATE agent_runs SET state='cancelled',cancel_requested_at_ms=?,lease_token=NULL,lease_expires_at_ms=NULL,state_version=state_version+1,updated_at_ms=? WHERE id=?", (now, now, run_id))
            conn.execute("UPDATE messages SET status='cancelled',updated_at_ms=?,version=version+1 WHERE id=?", (now, run["assistant_message_id"]))
            self._append_event(conn, run_id=run_id, kind="run.cancelled", payload={"reason": "operator_request"}, actor_id=actor_id)
            self._audit(conn, actor_id=actor_id, action="run.cancelled", resource_type="run", resource_id=run_id, outcome="success")
            return {**self._run_dict(run), "state": "cancelled", "updated_at_ms": now}

    def retry_run(self, *, actor_id: str, run_id: str) -> dict[str, Any]:
        """Create a new attempt without mutating the original run or its events."""
        with self._transaction() as conn:
            previous = conn.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
            if not previous:
                raise KeyError(run_id)
            self._require_participant(conn, actor_id=actor_id, conversation_id=previous["conversation_id"])
            if previous["state"] not in FINAL_RUN_STATES:
                raise ValueError("only a final run can be retried")
            now = _now_ms()
            retry_id = _id("run")
            assistant_id = _id("msg")
            sequence = self._next_sequence(conn, table="messages", key="conversation_id", key_value=previous["conversation_id"])
            request_hash = f"retry:{previous['id']}:{now}"
            conn.execute(
                "INSERT INTO messages (id,conversation_id,sequence,run_id,kind,status,content,content_hash,created_at_ms,updated_at_ms) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (assistant_id, previous["conversation_id"], sequence, retry_id, "assistant_placeholder", "queued", "", hashlib.sha256(b"").hexdigest(), now, now),
            )
            conn.execute(
                "INSERT INTO agent_runs (id,conversation_id,actor_id,user_message_id,assistant_message_id,root_run_id,parent_run_id,attempt,state,idempotency_key,request_hash,model_profile_id,budget_json,context_manifest_json,created_at_ms,updated_at_ms) VALUES (?,?,?,?,?,?,?,?,'queued',?,?,?,?,?,?,?)",
                (retry_id, previous["conversation_id"], actor_id, previous["user_message_id"], assistant_id, previous["root_run_id"] or previous["id"], previous["id"], int(previous["attempt"]) + 1, f"retry:{previous['id']}:{now}", request_hash, previous["model_profile_id"], previous["budget_json"], previous["context_manifest_json"], now, now),
            )
            self._append_event(conn, run_id=retry_id, kind="run.retried", payload={"retry_of_run_id": previous["id"], "attempt": int(previous["attempt"]) + 1}, actor_id=actor_id, causation_id=previous["id"])
            conn.execute("UPDATE conversations SET last_activity_at_ms=?,updated_at_ms=?,version=version+1 WHERE id=?", (now, now, previous["conversation_id"]))
            self._audit(conn, actor_id=actor_id, action="run.retried", resource_type="run", resource_id=retry_id, outcome="success", metadata={"retry_of": previous["id"]})
            retried = conn.execute("SELECT * FROM agent_runs WHERE id=?", (retry_id,)).fetchone()
            return self._run_dict(retried)

    # ``append_operator_guidance`` was removed in Fase 2 (issue #9).  Operator
    # guidance now flows through ``POST /api/chat/{run_id}/guidance`` which:
    #   1. Enqueues on the ``run_guidance_queue`` (durable outbox) via
    #      :meth:`enqueue_guidance` below.
    #   2. Records a durable audit row via
    #      :meth:`append_reasoning_event` with ``kind="operator_guidance"``.
    # Both entry points are exercised by :mod:`munin.production.chat`.

    def save_provider_profile(self, *, actor_id: str, label: str, provider: str, base_url: str, model: str, uses: list[str], plaintext_key: str) -> dict[str, Any]:
        if not plaintext_key or not base_url.startswith("https://"):
            raise ValueError("provider profile requires an HTTPS base URL and key")
        profile_id = _id("profile")
        now = _now_ms()
        ciphertext, wrapped_dek, fingerprint = self._cipher.encrypt(owner_id=actor_id, profile_id=profile_id, provider=provider, plaintext=plaintext_key)
        with self._transaction() as conn:
            self._require_user(conn, actor_id)
            conn.execute(
                "INSERT INTO provider_profiles (id,owner_id,label,provider,base_url,model,uses_json,key_fingerprint,ciphertext_json,wrapped_dek_json,kek_version,status,created_at_ms,updated_at_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (profile_id, actor_id, label[:120], provider[:64], base_url[:500], model[:240], _json(uses), fingerprint, _json(ciphertext), _json(wrapped_dek), self._cipher.kek_version, "active", now, now),
            )
            self._audit(conn, actor_id=actor_id, action="provider_profile.created", resource_type="provider_profile", resource_id=profile_id, outcome="success", metadata={"fingerprint": fingerprint})
        return {"id": profile_id, "label": label[:120], "provider": provider[:64], "model": model[:240], "uses": list(uses), "key_fingerprint": fingerprint, "status": "active"}

    def reveal_provider_key(self, *, actor_id: str, profile_id: str) -> str:
        with self._read_only() as conn:
            row = conn.execute("SELECT * FROM provider_profiles WHERE id=? AND owner_id=? AND status='active' AND revoked_at_ms IS NULL", (profile_id, actor_id)).fetchone()
            if not row:
                raise PermissionError("provider profile is unavailable")
            return self._cipher.decrypt(owner_id=actor_id, profile_id=profile_id, provider=row["provider"], ciphertext=json.loads(row["ciphertext_json"]), wrapped_dek=json.loads(row["wrapped_dek_json"]))

    def list_provider_profiles(self, *, actor_id: str) -> list[dict[str, Any]]:
        with self._read_only() as conn:
            rows = conn.execute("SELECT id,label,provider,base_url,model,uses_json,key_fingerprint,status,active,created_at_ms,rotated_at_ms,revoked_at_ms,updated_at_ms FROM provider_profiles WHERE owner_id=? ORDER BY updated_at_ms DESC", (actor_id,)).fetchall()
            return [{**_row(row), "uses": json.loads(row["uses_json"]), "active": bool(row["active"])} for row in rows]

    def set_active_provider_profile(self, *, actor_id: str, profile_id: str) -> dict[str, Any]:
        with self._transaction() as conn:
            row = conn.execute("SELECT * FROM provider_profiles WHERE id=? AND owner_id=? AND status='active' AND revoked_at_ms IS NULL", (profile_id, actor_id)).fetchone()
            if not row:
                raise PermissionError("provider profile is unavailable")
            conn.execute("UPDATE provider_profiles SET active=0,updated_at_ms=? WHERE owner_id=?", (_now_ms(), actor_id))
            conn.execute("UPDATE provider_profiles SET active=1,updated_at_ms=? WHERE id=?", (_now_ms(), profile_id))
            self._audit(conn, actor_id=actor_id, action="provider_profile.activated", resource_type="provider_profile", resource_id=profile_id, outcome="success")
            return {"id": profile_id, "active": True}

    def revoke_provider_profile(self, *, actor_id: str, profile_id: str) -> bool:
        with self._transaction() as conn:
            changed = conn.execute("UPDATE provider_profiles SET status='revoked',active=0,revoked_at_ms=?,updated_at_ms=? WHERE id=? AND owner_id=? AND revoked_at_ms IS NULL", (_now_ms(), _now_ms(), profile_id, actor_id))
            if int(changed.rowcount) != 1:
                return False
            self._audit(conn, actor_id=actor_id, action="provider_profile.revoked", resource_type="provider_profile", resource_id=profile_id, outcome="success")
            return True

    def rotate_provider_profile(self, *, actor_id: str, profile_id: str, plaintext_key: str) -> dict[str, Any]:
        with self._read_only() as conn:
            prior = conn.execute("SELECT * FROM provider_profiles WHERE id=? AND owner_id=? AND status='active' AND revoked_at_ms IS NULL", (profile_id, actor_id)).fetchone()
            if not prior:
                raise PermissionError("provider profile is unavailable")
            data = _row(prior)
        replacement = self.save_provider_profile(actor_id=actor_id, label=str(data["label"]), provider=str(data["provider"]), base_url=str(data["base_url"]), model=str(data["model"]), uses=json.loads(data["uses_json"]), plaintext_key=plaintext_key)
        with self._transaction() as transaction:
            transaction.execute("UPDATE provider_profiles SET status='rotated',active=0,rotated_at_ms=?,updated_at_ms=? WHERE id=? AND owner_id=?", (_now_ms(), _now_ms(), profile_id, actor_id))
            transaction.execute("UPDATE provider_profiles SET active=1 WHERE id=?", (replacement["id"],))
            self._audit(transaction, actor_id=actor_id, action="provider_profile.rotated", resource_type="provider_profile", resource_id=profile_id, outcome="success", metadata={"replacement_id": replacement["id"]})
        return {**replacement, "active": True, "rotated_from": profile_id}

    def create_snapshot(self, *, run_id: str, event_id: str) -> dict[str, Any]:
        events = self.list_run_events(run_id)
        if not any(event["id"] == event_id for event in events):
            raise KeyError(event_id)
        state = {"run_id": run_id, "through_event_id": event_id, "events": events}
        encoded = _json(state)
        snapshot = {"id": _id("snap"), "run_id": run_id, "event_id": event_id, "state_hash": hashlib.sha256(encoded.encode()).hexdigest(), "created_at_ms": _now_ms()}
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO operation_snapshots (id,run_id,event_id,state_json,state_hash,redaction_policy_version,created_at_ms) VALUES (?,?,?,?,?,?,?)",
                (snapshot["id"], run_id, event_id, encoded, snapshot["state_hash"], REDACTION_POLICY_VERSION, snapshot["created_at_ms"]),
            )
        return snapshot

    def recorded_replay(self, *, run_id: str, snapshot_id: str) -> dict[str, Any]:
        with self._read_only() as conn:
            row = conn.execute("SELECT * FROM operation_snapshots WHERE id=? AND run_id=?", (snapshot_id, run_id)).fetchone()
            if not row:
                raise KeyError(snapshot_id)
            return {"mode": "recorded", "egress_enabled": False, "run_id": run_id, "snapshot_id": snapshot_id, "state": json.loads(row["state_json"])}

    def create_operation_branch(
        self,
        *,
        actor_id: str,
        parent_run_id: str,
        fork_event_id: str,
        hypothesis: str,
        replay_mode: str = "recorded",
        parent_branch_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a recorded what-if branch; it cannot execute real tools by default."""
        if replay_mode not in {"recorded", "sandbox"}:
            raise ValueError("unsupported replay mode")
        with self._transaction() as conn:
            run = conn.execute("SELECT conversation_id FROM agent_runs WHERE id=?", (parent_run_id,)).fetchone()
            if not run:
                raise KeyError(parent_run_id)
            self._require_participant(conn, actor_id=actor_id, conversation_id=run["conversation_id"])
            event = conn.execute("SELECT id FROM run_events WHERE id=? AND run_id=?", (fork_event_id, parent_run_id)).fetchone()
            if not event:
                raise KeyError(fork_event_id)
            branch = {"id": _id("branch"), "parent_run_id": parent_run_id, "parent_branch_id": parent_branch_id, "fork_event_id": fork_event_id, "replay_mode": replay_mode, "state": "draft", "provenance": {"hypothesis": redact_text(hypothesis)[:4_000], "tool_egress": False, "created_by": actor_id}, "created_at_ms": _now_ms()}
            conn.execute(
                "INSERT INTO operation_branches (id,parent_run_id,parent_branch_id,fork_event_id,replay_mode,state,provenance_json,created_at_ms) VALUES (?,?,?,?,?,?,?,?)",
                (branch["id"], branch["parent_run_id"], branch["parent_branch_id"], branch["fork_event_id"], branch["replay_mode"], branch["state"], _json(branch["provenance"]), branch["created_at_ms"]),
            )
            self._append_event(conn, run_id=parent_run_id, kind="replay.branch_created", payload={"branch_id": branch["id"], "fork_event_id": fork_event_id, "replay_mode": replay_mode, "tool_egress": False}, actor_id=actor_id)
            self._audit(conn, actor_id=actor_id, action="replay.branch_created", resource_type="operation_branch", resource_id=branch["id"], outcome="success")
            return branch

    def compare_operation_branch(self, *, actor_id: str, branch_id: str) -> dict[str, Any]:
        with self._read_only() as conn:
            branch = conn.execute("SELECT b.*,r.conversation_id FROM operation_branches b JOIN agent_runs r ON r.id=b.parent_run_id WHERE b.id=?", (branch_id,)).fetchone()
            if not branch:
                raise KeyError(branch_id)
            self._require_participant(conn, actor_id=actor_id, conversation_id=branch["conversation_id"])
            fork = conn.execute("SELECT sequence FROM run_events WHERE id=? AND run_id=?", (branch["fork_event_id"], branch["parent_run_id"])).fetchone()
            original = conn.execute("SELECT kind,payload_json,created_at_ms FROM run_events WHERE run_id=? AND sequence>=? ORDER BY sequence", (branch["parent_run_id"], fork["sequence"])).fetchall()
            return {
                "branch": {"id": branch["id"], "state": branch["state"], "replay_mode": branch["replay_mode"], "fork_event_id": branch["fork_event_id"], "provenance": json.loads(branch["provenance_json"])},
                "original": [{"kind": row["kind"], "payload": json.loads(row["payload_json"]), "created_at_ms": int(row["created_at_ms"])} for row in original],
                "branch_events": [],
                "diff": {"tool_egress": "disabled", "findings": [], "artifacts": [], "cost": {"original": None, "branch": None}},
            }

    def create_conversation_summary(
        self,
        *,
        actor_id: str,
        conversation_id: str,
        source_message_ids: list[str],
        summary: str,
        model: str,
        prompt_version: str,
        confidence: float,
        entities: list[str] | None = None,
        findings: list[str] | None = None,
        decisions: list[str] | None = None,
        open_tasks: list[str] | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Store compaction with immutable source provenance; raw messages are never deleted."""
        if not source_message_ids or not summary.strip() or not 0 <= confidence <= 1:
            raise ValueError("summary requires sources, content, and a confidence between zero and one")
        with self._transaction() as conn:
            self._require_participant(conn, actor_id=actor_id, conversation_id=conversation_id)
            marks = ",".join("?" for _ in source_message_ids)
            messages = conn.execute(
                f"SELECT id,sequence,content_hash FROM messages WHERE conversation_id=? AND id IN ({marks}) ORDER BY sequence",  # noqa: S608 -- placeholders are generated only from IDs.
                [conversation_id, *source_message_ids],
            ).fetchall()
            if len(messages) != len(set(source_message_ids)):
                raise ValueError("all summary source messages must belong to the conversation")
            source_ids = [row["id"] for row in messages]
            source_hash = hashlib.sha256("|".join(str(row["content_hash"]) for row in messages).encode()).hexdigest()
            record = {"id": _id("summary"), "conversation_id": conversation_id, "run_id": run_id, "source_start_sequence": int(messages[0]["sequence"]), "source_end_sequence": int(messages[-1]["sequence"]), "source_ids": source_ids, "source_hash": source_hash, "model": model[:240], "prompt_version": prompt_version[:120], "confidence": confidence, "entities": redact_payload(entities or []), "findings": redact_payload(findings or []), "decisions": redact_payload(decisions or []), "open_tasks": redact_payload(open_tasks or []), "summary": redact_text(summary)[:50_000], "created_at_ms": _now_ms()}
            conn.execute(
                "INSERT INTO conversation_summaries (id,conversation_id,run_id,source_start_sequence,source_end_sequence,source_ids_json,source_hash,content,model,prompt_version,confidence,entities_json,findings_json,decisions_json,open_tasks_json,created_at_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (record["id"], record["conversation_id"], record["run_id"], record["source_start_sequence"], record["source_end_sequence"], _json(record["source_ids"]), record["source_hash"], record["summary"], record["model"], record["prompt_version"], record["confidence"], _json(record["entities"]), _json(record["findings"]), _json(record["decisions"]), _json(record["open_tasks"]), record["created_at_ms"]),
            )
            conn.execute("UPDATE conversations SET summary=?,updated_at_ms=?,version=version+1 WHERE id=?", (record["summary"], record["created_at_ms"], conversation_id))
            self._audit(conn, actor_id=actor_id, action="memory.compacted", resource_type="conversation_summary", resource_id=record["id"], outcome="success", metadata={"source_count": len(source_ids), "model": record["model"]})
            return record

    # ------------------------------------------------------------------
    # Fase 2 (issue #9) — ex-``store_v3_1`` essentials merged inline
    # ------------------------------------------------------------------
    #
    # Only the pieces the AI-SDK chat path (``/api/chat`` +
    # ``/api/chat/{run_id}/guidance``) actually consumes survived the
    # migration.  Collaborators, notes, presence, and the conversation SSE
    # stream were dropped along with the FlightDeck UI that drove them.

    def enqueue_guidance(
        self,
        *,
        run_id: str,
        actor_id: str,
        actor_username: str,
        body: str,
        target_agent_id: str | None = None,
        budget_extension_seconds: int = 0,
    ) -> dict[str, Any]:
        """Durable outbox for operator guidance mid-run.

        Drained by ``OperatorGuidanceMiddleware`` via
        :meth:`consume_pending_guidance` before every model call, so a hint
        reaches the ReAct loop as a ``HumanMessage`` named ``operator``.
        """
        trimmed = body.strip()
        if not trimmed:
            raise ValueError("guidance body is required")
        if len(trimmed) > 4_000:
            raise ValueError("guidance exceeds maximum size (4000 chars)")
        entry = {
            "id": _id("guidance"),
            "run_id": run_id,
            "actor_id": actor_id,
            "actor_username": actor_username,
            "body": trimmed,
            "target_agent_id": target_agent_id,
            "created_at_ms": _now_ms(),
            "consumed_at_ms": None,
            "delivered_at_step": None,
            "budget_extension_seconds": int(budget_extension_seconds or 0),
        }
        with self._transaction() as conn:
            conn.execute(
                """INSERT INTO run_guidance_queue
                    (id, run_id, actor_id, actor_username, body, target_agent_id,
                     created_at_ms, budget_extension_seconds)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry["id"],
                    run_id,
                    actor_id,
                    actor_username,
                    trimmed,
                    target_agent_id,
                    entry["created_at_ms"],
                    entry["budget_extension_seconds"],
                ),
            )
        return entry

    def consume_pending_guidance(
        self,
        *,
        run_id: str,
        target_agent_id: str | None = None,
        delivered_at_step: int | None = None,
    ) -> list[dict[str, Any]]:
        """Atomically claim all pending guidance rows for a run."""
        now = _now_ms()
        with self._transaction() as conn:
            if target_agent_id is None:
                rows = conn.execute(
                    """SELECT * FROM run_guidance_queue
                       WHERE run_id = ? AND consumed_at_ms IS NULL
                       ORDER BY created_at_ms""",
                    (run_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM run_guidance_queue
                       WHERE run_id = ? AND consumed_at_ms IS NULL
                         AND (target_agent_id = ? OR target_agent_id IS NULL)
                       ORDER BY created_at_ms""",
                    (run_id, target_agent_id),
                ).fetchall()
            results: list[dict[str, Any]] = []
            for row in rows:
                entry = _row(row)
                conn.execute(
                    "UPDATE run_guidance_queue SET consumed_at_ms=?, delivered_at_step=? WHERE id=?",
                    (now, delivered_at_step, entry["id"]),
                )
                entry["consumed_at_ms"] = now
                entry["delivered_at_step"] = delivered_at_step
                results.append(entry)
            return results

    def list_run_guidance(self, *, run_id: str) -> list[dict[str, Any]]:
        with self._read_only() as conn:
            rows = conn.execute(
                "SELECT * FROM run_guidance_queue WHERE run_id = ? ORDER BY created_at_ms",
                (run_id,),
            ).fetchall()
            return [_row(row) for row in rows]

    # Broadcast log — used by ``/api/chat`` to record run-transition markers.
    # The SSE consumer that used to fan these out (``GET /api/conversations/
    # {id}/events``) was deleted in Fase 2; the table stays for audit only.
    _BROADCAST_KINDS = frozenset({"run-transition"})

    def append_conversation_broadcast(
        self,
        *,
        conversation_id: str,
        kind: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if kind not in self._BROADCAST_KINDS:
            raise ValueError(f"invalid broadcast kind: {kind}")
        now = _now_ms()
        entry_id = _id("bcast")
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS seq FROM conversation_broadcasts "
                "WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            seq = int((row["seq"] if row and row["seq"] is not None else 0)) + 1
            conn.execute(
                """INSERT INTO conversation_broadcasts
                    (id, conversation_id, kind, payload_json, sequence, created_at_ms)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    entry_id,
                    conversation_id,
                    kind,
                    json.dumps(payload, default=str, separators=(",", ":")),
                    seq,
                    now,
                ),
            )
        return {
            "id": entry_id,
            "conversation_id": conversation_id,
            "kind": kind,
            "payload": payload,
            "sequence": seq,
            "created_at_ms": now,
        }


# =====================================================================
# Fase 4 (issue #9) — Split store: hot (SQLite local) / durable (Turso)
# =====================================================================
#
# Rationale.  libsql v0.1.11 + Turso free tier serialise every operation
# through a process-wide mutex and open a fresh connection per statement.
# On a busy backend that cost dominates request latency (auth checks hit
# 200-2000ms, guidance polling stalls the ReAct loop).  Fase 4 routes
# high-churn, non-durable state onto a local SQLite ("hot") database and
# keeps long-lived rows on Turso ("durable").
#
# Hot tables (authoritative in HotStore; disposable across reboots):
#     auth_sessions, auth_rate_limits, password_recovery_tokens,
#     run_guidance_queue, users (write-through mirror of durable users so
#     the auth path can join locally), conversation_participants (mirror,
#     same reason), and — while a run is in flight —
#     agent_runs, run_events, reasoning_events, tool_calls,
#     subagent_runs, human_requests.
#
# Durable tables (authoritative in DurableStore):
#     users, conversations, conversation_participants, messages,
#     message_revisions, conversation_artifacts, conversation_summaries,
#     provider_profiles, audit_events, operation_snapshots,
#     operation_branches, conversation_broadcasts, plus the migrated
#     ``agent_runs`` + derivative rows once a run reaches a terminal state.
#
# Both wrapped stores install the full :func:`_DDL` schema; the split is
# expressed in the routing, not the DDL.  ``MuninStore.complete_run`` is
# the explicit migration point that copies the run + its derivative
# events from HotStore to DurableStore and deletes the hot copies.
#
# The facade preserves the public ``ProductionStore`` API so ``chat.py``,
# ``asgi.py``, and MCP handlers keep working without callsite changes.
# Private accessors used by ``chat.py`` (``_transaction``, ``_read_only``,
# ``_append_event``) default to the durable store; hot equivalents are
# exposed as ``_hot_transaction`` / ``_hot_read_only`` /
# ``_hot_append_event`` for the pieces that need explicit hot access.


class MuninStore:
    """Split-backend store: hot SQLite for churn, durable Turso for durability.

    See the module-level Fase 4 header above for the table-by-table
    routing table and the rationale.  Instantiate via :meth:`from_settings`
    (the canonical boot path) or directly with two ``ProductionStore``
    instances for tests.
    """

    def __init__(self, *, hot: ProductionStore, durable: ProductionStore) -> None:
        self._hot = hot
        self._durable = durable
        # Same envelope cipher & password hasher on both — the encrypted
        # payloads only ever live on the durable side, so we surface the
        # durable copy for callers that peek at ``store._cipher`` /
        # ``store._passwords`` (e.g. legacy tests).
        self._cipher = durable._cipher  # noqa: SLF001 - façade forwarding
        self._passwords = durable._passwords  # noqa: SLF001 - façade forwarding
        # Local-first delta sync bookkeeping (issue #9 §3). ``_settings`` is
        # set by :meth:`from_settings`; direct (test) constructors leave it
        # ``None`` and the sync helpers degrade to no-ops.
        self._settings: Any = None
        self._sync_last_ms: int = 0

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def from_settings(cls, settings: Any, *, master_key: bytes) -> MuninStore:
        """Build a split store from :class:`Settings`.

        * Hot backend → :meth:`ProductionStore.for_sqlite` at
          ``settings.hot_db_path`` (SQLite local; WAL; 2s busy timeout).
        * Durable backend → :meth:`ProductionStore.for_settings` when
          ``settings.durable_db_url`` is a libsql URL.  When durable is
          empty (dev / hot-only mode) we point durable at the same hot
          file so the ProductionStore machinery still has a place to
          store audit rows.  In this hot-only mode nothing survives a
          reboot, matching the pre-Fase-4 pure-SQLite defaults.
        """
        hot = ProductionStore.for_sqlite(Path(settings.hot_db_path), master_key=master_key)
        durable_url = getattr(settings, "durable_db_url", "") or ""
        if durable_url:
            # Reuse ``for_settings`` — it knows how to route libsql:// vs
            # file://.  We synthesise a shadow settings so we don't have
            # to touch the shared Settings dataclass mid-boot.
            from dataclasses import replace  # noqa: PLC0415 - lazy import
            shadow = replace(
                settings,
                db_url=durable_url,
                db_auth_token=getattr(settings, "durable_db_auth_token", "") or "",
            )
            durable = ProductionStore.for_settings(shadow, master_key=master_key)
        else:
            # Degenerate mode: no durable backend configured.  Point durable
            # at the hot file so every call still lands on *some* store —
            # this matches the pre-Fase-4 sqlite-only development setup and
            # is intentionally opt-in via an empty durable URL.
            durable = hot
        store = cls(hot=hot, durable=durable)
        store._settings = settings  # noqa: SLF001 - sync tuning lives on MuninStore
        # Install the local-first change outbox ONLY on the hot backend —
        # the durable (Turso namespaced) backend must never see the triggers
        # (it would re-record its own writes). Skipped automatically when
        # hot is itself a namespaced Turso proxy (dev with MUNIN_DB_URL=libsql).
        try:
            hot.install_sync_tracking()
        except Exception:  # noqa: BLE001
            logger.debug("hot sync tracking not installable", exc_info=True)
        return store

    # ------------------------------------------------------------------
    # Internal accessors used by chat.py (SLF001)
    # ------------------------------------------------------------------
    #
    # ``chat.py`` still opens ``store._transaction()`` for the placeholder
    # UPDATE (messages live in the durable store) and calls
    # ``store._append_event`` from ``_claim_direct``.  We expose the durable
    # accessors as the defaults; hot equivalents live under ``_hot_*``.

    @property
    def _connection_factory(self) -> Callable[[], Any]:
        return self._durable._connection_factory  # noqa: SLF001

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        with self._durable._transaction() as conn:  # noqa: SLF001
            yield conn

    @contextmanager
    def _read_only(self) -> Iterator[Any]:
        with self._durable._read_only() as conn:  # noqa: SLF001
            yield conn

    @contextmanager
    def _hot_transaction(self) -> Iterator[Any]:
        with self._hot._transaction() as conn:  # noqa: SLF001
            yield conn

    @contextmanager
    def _hot_read_only(self) -> Iterator[Any]:
        with self._hot._read_only() as conn:  # noqa: SLF001
            yield conn

    def _append_event(self, conn: Any, **kwargs: Any) -> dict[str, Any]:
        # ``chat.py::_claim_direct`` uses this while holding a hot
        # transaction — the delegated implementation is state-free.
        return ProductionStore._append_event(self._hot, conn, **kwargs)  # noqa: SLF001

    _hot_append_event = _append_event  # alias for readers who want explicitness

    # ------------------------------------------------------------------
    # Fase 5: pool lifecycle
    # ------------------------------------------------------------------

    def close_pools(self) -> None:
        """Drain libsql connection pools owned by the durable + hot backends.

        Wired to the ASGI ``shutdown`` event in :mod:`munin.server` so a
        rolling restart cleanly releases every Turso socket.  In hot-only
        mode ``self._hot is self._durable`` — we guard against calling
        ``close_pools`` twice on the same underlying ``ProductionStore``.

        Before draining, flushes any pending local-first delta sync rows so
        the conversation timeline and run events survive the restart
        (``MUNIN_SYNC_AT_END`` default-on). Flush failures are non-fatal —
        hot rows stay so the next boot can retry.
        """
        if getattr(self._settings, "sync_at_end", True):
            with suppress(Exception):
                self.flush_pending_syncs()
        seen: set[int] = set()
        for backend in (self._durable, self._hot):
            if id(backend) in seen:
                continue
            seen.add(id(backend))
            with suppress(Exception):
                backend.close_pools()

    # ------------------------------------------------------------------
    # Mirror helpers — write-through cache for durable rows the hot side
    # needs (users, conversation_participants).
    # ------------------------------------------------------------------

    def _mirror_user(self, row: dict[str, Any]) -> None:
        """Copy a durable ``users`` row into the hot store (idempotent)."""
        now = _now_ms()
        try:
            with self._hot._transaction() as conn:  # noqa: SLF001
                existing = conn.execute("SELECT id FROM users WHERE id=?", (row["id"],)).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE users SET username=?,password_hash=?,role=?,disabled_at_ms=?,updated_at_ms=? WHERE id=?",
                        (
                            row["username"], row["password_hash"], row["role"],
                            row.get("disabled_at_ms"), now, row["id"],
                        ),
                    )
                else:
                    conn.execute(
                        "INSERT INTO users (id,username,password_hash,role,disabled_at_ms,created_at_ms,updated_at_ms) VALUES (?,?,?,?,?,?,?)",
                        (
                            row["id"], row["username"], row["password_hash"], row["role"],
                            row.get("disabled_at_ms"),
                            int(row.get("created_at_ms") or now), now,
                        ),
                    )
        except Exception:  # noqa: BLE001 - mirror is best-effort
            pass

    def _mirror_participant(self, *, conversation_id: str, user_id: str, role: str = "owner") -> None:
        """Copy a durable ``conversation_participants`` row into the hot store."""
        now = _now_ms()
        try:
            with self._hot._transaction() as conn:  # noqa: SLF001
                existing = conn.execute(
                    "SELECT 1 FROM conversation_participants WHERE conversation_id=? AND user_id=?",
                    (conversation_id, user_id),
                ).fetchone()
                if not existing:
                    conn.execute(
                        "INSERT INTO conversation_participants (conversation_id,user_id,role,added_at_ms) VALUES (?,?,?,?)",
                        (conversation_id, user_id, role, now),
                    )
        except Exception:  # noqa: BLE001
            pass

    def _hydrate_hot_user(self, username: str) -> None:
        """Fault-in a durable ``users`` row into the hot store on demand.

        Called from the login / recovery paths so the hot store's local
        ``auth_sessions × users`` join has a row to point at even after
        the hot database was recreated (fresh runner, ``/tmp`` wipe, …).
        """
        normalized = username.strip().lower()
        with self._hot._read_only() as conn:  # noqa: SLF001
            if conn.execute("SELECT 1 FROM users WHERE username=?", (normalized,)).fetchone():
                return
        try:
            with self._durable._read_only() as conn:  # noqa: SLF001
                row = conn.execute("SELECT * FROM users WHERE username=?", (normalized,)).fetchone()
                if row:
                    self._mirror_user(_row(row))
        except Exception:  # noqa: BLE001 - best-effort; login will still fail loudly
            pass

    def _hydrate_hot_participant(self, *, actor_id: str, conversation_id: str) -> None:
        """Fault-in a participant row into the hot store on demand."""
        with self._hot._read_only() as conn:  # noqa: SLF001
            if conn.execute(
                "SELECT 1 FROM conversation_participants WHERE conversation_id=? AND user_id=?",
                (conversation_id, actor_id),
            ).fetchone():
                return
        try:
            with self._durable._read_only() as conn:  # noqa: SLF001
                row = conn.execute(
                    "SELECT role FROM conversation_participants WHERE conversation_id=? AND user_id=? AND removed_at_ms IS NULL",
                    (conversation_id, actor_id),
                ).fetchone()
                if row:
                    self._mirror_participant(
                        conversation_id=conversation_id,
                        user_id=actor_id,
                        role=str(row["role"]),
                    )
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Migration
    # ------------------------------------------------------------------
    #
    # ``ProductionStore.migrate()`` was called by both wrapped instances
    # at construction time, so both databases already carry the full DDL.
    # This method is kept as a public alias so callers that still invoke
    # ``store.migrate()`` (dev boot, tests) don't crash.

    def migrate(self) -> None:
        self._hot.migrate()
        if self._durable is not self._hot:
            self._durable.migrate()

    def schema_tables(self) -> set[str]:
        return self._durable.schema_tables()

    def applied_migration_ids(self) -> list[str]:
        return self._durable.applied_migration_ids()

    # ------------------------------------------------------------------
    # Users (durable authoritative; mirror to hot)
    # ------------------------------------------------------------------

    def create_user(self, *, username: str, password: str, role: str) -> dict[str, Any]:
        user = self._durable.create_user(username=username, password=password, role=role)
        try:
            with self._durable._read_only() as conn:  # noqa: SLF001
                row = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
                if row:
                    self._mirror_user(_row(row))
        except Exception:  # noqa: BLE001
            pass
        return user

    def bootstrap_admin(self, *, username: str, password: str) -> dict[str, Any] | None:
        result = self._durable.bootstrap_admin(username=username, password=password)
        if result is not None:
            try:
                with self._durable._read_only() as conn:  # noqa: SLF001
                    row = conn.execute("SELECT * FROM users WHERE id=?", (result["id"],)).fetchone()
                    if row:
                        self._mirror_user(_row(row))
            except Exception:  # noqa: BLE001
                pass
        return result

    def delete_user_for_test(self, *, username: str) -> bool:
        deleted = self._durable.delete_user_for_test(username=username)
        if deleted:
            self._hot.delete_user_for_test(username=username)
        return deleted

    # ------------------------------------------------------------------
    # Auth (hot)
    # ------------------------------------------------------------------

    def login(self, *, username: str, password: str, ip_address: str, user_agent: str) -> dict[str, Any]:
        # Ensure the hot ``users`` mirror is populated before HotStore's
        # ``login`` looks up credentials by username.
        self._hydrate_hot_user(username)
        return self._hot.login(
            username=username, password=password, ip_address=ip_address, user_agent=user_agent,
        )

    def authenticate(self, token: str) -> dict[str, Any] | None:
        # HOT-ONLY: this is called on every authenticated HTTP request.
        # If the durable backend is down or unreachable, authenticate
        # still resolves in <1ms against the local SQLite file.
        return self._hot.authenticate(token)

    def rotate_session(self, token: str) -> dict[str, Any]:
        return self._hot.rotate_session(token)

    def refresh_csrf(self, session_id: str) -> str:
        return self._hot.refresh_csrf(session_id)

    def validate_csrf(self, *, session_id: str, csrf_token: str) -> bool:
        return self._hot.validate_csrf(session_id=session_id, csrf_token=csrf_token)

    def revoke_session(self, session_id: str, *, actor_id: str) -> bool:
        return self._hot.revoke_session(session_id, actor_id=actor_id)

    def session_record(self, session_id: str) -> dict[str, Any] | None:
        return self._hot.session_record(session_id)

    def issue_password_recovery(self, *, username: str, ttl_seconds: int = 1_800) -> dict[str, str] | None:
        self._hydrate_hot_user(username)
        return self._hot.issue_password_recovery(username=username, ttl_seconds=ttl_seconds)

    def consume_password_recovery(self, *, token: str, new_password: str) -> bool:
        consumed = self._hot.consume_password_recovery(token=token, new_password=new_password)
        if consumed and self._durable is not self._hot:
            # Propagate the new hash to the durable ``users`` row so a
            # future cold hot start still trusts the new password.  Best
            # effort: if Turso is unreachable we log and continue.
            now = _now_ms()
            try:
                with self._hot._read_only() as conn:  # noqa: SLF001
                    rows = conn.execute(
                        "SELECT id, password_hash, updated_at_ms FROM users WHERE updated_at_ms >= ?",
                        (now - 60_000,),
                    ).fetchall()
                for row in rows:
                    with suppress(Exception):
                        with self._durable._transaction() as dconn:  # noqa: SLF001
                            dconn.execute(
                                "UPDATE users SET password_hash=?,updated_at_ms=? WHERE id=?",
                                (row["password_hash"], int(row["updated_at_ms"]), row["id"]),
                            )
                            dconn.execute(
                                "UPDATE auth_sessions SET revoked_at_ms=? WHERE user_id=? AND revoked_at_ms IS NULL",
                                (int(row["updated_at_ms"]), row["id"]),
                            )
            except Exception:  # noqa: BLE001
                pass
        return consumed

    # ------------------------------------------------------------------
    # Guidance (hot-only)
    # ------------------------------------------------------------------

    def enqueue_guidance(self, **kwargs: Any) -> dict[str, Any]:
        return self._hot.enqueue_guidance(**kwargs)

    def consume_pending_guidance(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._hot.consume_pending_guidance(**kwargs)

    def list_run_guidance(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._hot.list_run_guidance(**kwargs)

    # ------------------------------------------------------------------
    # Conversations (durable) — mirror participants to hot on write.
    # ------------------------------------------------------------------

    def create_conversation(self, *, owner_id: str, title: str, tags: list[str] | None = None, scope: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self._durable.create_conversation(owner_id=owner_id, title=title, tags=tags, scope=scope)
        self._mirror_participant(conversation_id=result["id"], user_id=owner_id, role="owner")
        return result

    # ------------------------------------------------------------------
    # Turns / runs (durable stub message + hot run)
    # ------------------------------------------------------------------

    def create_turn(self, *, actor_id: str, conversation_id: str, content: str, idempotency_key: str) -> dict[str, Any]:
        """Split the turn creation across the two backends.

        * Durable: participant check, user message row, assistant
          placeholder message row (so ``get_conversation`` sees the
          in-flight turn), conversation ``last_activity_at_ms`` bump,
          audit row.
        * Hot: full ``agent_runs`` row + initial ``run.queued`` event.
          The idempotency check reads hot first (queued/running runs)
          and falls back to durable (finalised runs migrated by
          :meth:`complete_run`) so retries with the same key correctly
          replay across both backends.
        """
        if not content.strip() or not idempotency_key.strip():
            raise ValueError("content and idempotency key are required")
        if len(content) > 1_000_000:
            raise ValueError("message exceeds maximum size")
        request_hash = hashlib.sha256(content.encode()).hexdigest()

        # --- idempotency: check hot then durable
        for backend in (self._hot, self._durable) if self._durable is not self._hot else (self._hot,):
            try:
                with backend._read_only() as conn:  # noqa: SLF001
                    previous = conn.execute(
                        "SELECT * FROM agent_runs WHERE conversation_id=? AND actor_id=? AND idempotency_key=?",
                        (conversation_id, actor_id, idempotency_key),
                    ).fetchone()
                    if previous:
                        if previous["request_hash"] != request_hash:
                            raise ValueError("idempotency key was reused with a different request body")
                        return {
                            "idempotent_replay": True,
                            "run": ProductionStore._run_dict(previous),  # noqa: SLF001
                            "user_message_id": previous["user_message_id"],
                            "assistant_message_id": previous["assistant_message_id"],
                        }
            except ValueError:
                raise
            except Exception:  # noqa: BLE001 - backend miss falls through
                continue

        now = _now_ms()
        user_message_id = _id("msg")
        assistant_message_id = _id("msg")
        run_id = _id("run")
        safe_content = redact_text(content)
        safe_hash = hashlib.sha256(safe_content.encode()).hexdigest()
        empty_hash = hashlib.sha256(b"").hexdigest()

        # --- durable: participant check, messages, conversation, audit ---
        with self._durable._transaction() as conn:  # noqa: SLF001
            self._durable._require_participant(conn, actor_id=actor_id, conversation_id=conversation_id)  # noqa: SLF001
            user_sequence = ProductionStore._next_sequence(conn, table="messages", key="conversation_id", key_value=conversation_id)  # noqa: SLF001
            conn.execute(
                "INSERT INTO messages (id,conversation_id,sequence,author_id,run_id,kind,status,content,content_hash,created_at_ms,updated_at_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (user_message_id, conversation_id, user_sequence, actor_id, run_id, "user", "completed", safe_content, safe_hash, now, now),
            )
            conn.execute(
                "INSERT INTO messages (id,conversation_id,sequence,author_id,run_id,kind,status,content,content_hash,created_at_ms,updated_at_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (assistant_message_id, conversation_id, user_sequence + 1, None, run_id, "assistant_placeholder", "queued", "", empty_hash, now, now),
            )
            conn.execute("UPDATE conversations SET last_activity_at_ms=?,updated_at_ms=?,version=version+1 WHERE id=?", (now, now, conversation_id))
            self._durable._audit(  # noqa: SLF001
                conn, actor_id=actor_id, action="turn.created", resource_type="run",
                resource_id=run_id, outcome="success", metadata={"event_id": None},
            )

        # --- hot: agent_run + initial run.queued event ---
        # Make sure the hot store also has the participant row (for
        # ``_require_participant`` checks inside hot-routed methods) and
        # the placeholder message shell (for chat.py's ``_update_placeholder``
        # if a caller later reroutes it).
        self._mirror_participant(conversation_id=conversation_id, user_id=actor_id, role="owner")
        with self._hot._transaction() as conn:  # noqa: SLF001
            conn.execute(
                """INSERT INTO agent_runs (id,conversation_id,actor_id,user_message_id,assistant_message_id,root_run_id,attempt,state,idempotency_key,request_hash,created_at_ms,updated_at_ms)
                VALUES (?,?,?,?,?,?,1,'queued',?,?,?,?)""",
                (run_id, conversation_id, actor_id, user_message_id, assistant_message_id, run_id, idempotency_key, request_hash, now, now),
            )
            ProductionStore._append_event(  # noqa: SLF001
                self._hot,
                conn,
                run_id=run_id,
                kind="run.queued",
                payload={"message_id": user_message_id, "assistant_message_id": assistant_message_id},
                actor_id=actor_id,
            )
        return {
            "idempotent_replay": False,
            "run": {"id": run_id, "state": "queued", "fencing_epoch": 0},
            "user_message_id": user_message_id,
            "assistant_message_id": assistant_message_id,
        }

    def run_execution_context(self, *, run_id: str) -> dict[str, Any]:
        """Hybrid read: run from HOT (or DURABLE if already migrated), history from DURABLE."""
        run_row: Any = None
        for backend in (self._hot, self._durable) if self._durable is not self._hot else (self._hot,):
            try:
                with backend._read_only() as conn:  # noqa: SLF001
                    run_row = conn.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
                    if run_row:
                        break
            except Exception:  # noqa: BLE001
                continue
        if not run_row:
            raise KeyError(run_id)
        with self._durable._read_only() as conn:  # noqa: SLF001
            rows = conn.execute(
                "SELECT kind,content FROM messages WHERE conversation_id=? AND kind IN ('user','assistant') ORDER BY sequence DESC LIMIT 16",
                (run_row["conversation_id"],),
            ).fetchall()
            prompt = conn.execute("SELECT content FROM messages WHERE id=?", (run_row["user_message_id"],)).fetchone()
        return {
            "run": ProductionStore._run_dict(run_row),  # noqa: SLF001
            "actor_id": run_row["actor_id"],
            "conversation_id": run_row["conversation_id"],
            "message": prompt["content"] if prompt else "",
            "history": [
                {"role": "assistant" if row["kind"] == "assistant" else "user", "content": row["content"]}
                for row in reversed(rows)
            ],
        }

    def claim_run_direct(self, *, run_id: str) -> tuple[str, str]:
        """Promote a queued run to ``running`` (chat.py's ``_claim_direct``).

        agent_runs live in the hot store while active, so this is entirely
        a hot-transaction affair.  The placeholder message state
        transition (queued → running) is applied to the durable copy so
        clients polling ``get_conversation`` see the update.
        """
        import os as _os  # noqa: PLC0415
        import secrets as _secrets  # noqa: PLC0415
        CHAT_LEASE_SECONDS = int(_os.environ.get("MUNIN_CHAT_LEASE_SECONDS", str(4 * 3600)))
        with self._hot._transaction() as conn:  # noqa: SLF001
            row = conn.execute(
                "SELECT * FROM agent_runs WHERE id=? AND state='queued'",
                (run_id,),
            ).fetchone()
            if not row:
                raise RuntimeError(f"run {run_id} is not queued (already running or terminal)")
            now = _now_ms()
            lease_token = _secrets.token_urlsafe(32)
            next_epoch = int(row["fencing_epoch"]) + 1
            conn.execute(
                "UPDATE agent_runs SET state='running',lease_worker_id=?,lease_token=?,"
                "lease_expires_at_ms=?,fencing_epoch=?,state_version=state_version+1,"
                "updated_at_ms=? WHERE id=? AND state='queued'",
                (
                    f"chat-{_os.getpid()}",
                    lease_token,
                    now + max(60, CHAT_LEASE_SECONDS) * 1000,
                    next_epoch,
                    now,
                    run_id,
                ),
            )
            ProductionStore._append_event(  # noqa: SLF001
                self._hot,
                conn,
                run_id=run_id,
                kind="run.claimed",
                payload={"worker_id": f"chat-{_os.getpid()}", "fencing_epoch": next_epoch},
            )
            assistant_message_id = str(row["assistant_message_id"])
        # Mirror the message status flip to the durable store (best effort).
        try:
            with self._durable._transaction() as conn:  # noqa: SLF001
                conn.execute(
                    "UPDATE messages SET status='running',updated_at_ms=?,version=version+1 WHERE id=?",
                    (_now_ms(), assistant_message_id),
                )
        except Exception:  # noqa: BLE001
            pass
        return lease_token, assistant_message_id

    def update_placeholder_content(self, *, assistant_message_id: str, content: str) -> None:
        """Write the running assistant placeholder's live content (durable)."""
        safe = content[-1_000_000:]
        now = _now_ms()
        try:
            with self._durable._transaction() as conn:  # noqa: SLF001
                conn.execute(
                    "UPDATE messages SET content=?,content_hash=?,updated_at_ms=?,version=version+1 "
                    "WHERE id=? AND kind='assistant_placeholder' AND status='running'",
                    (safe, hashlib.sha256(safe.encode()).hexdigest(), now, assistant_message_id),
                )
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Hot in-progress event writes
    # ------------------------------------------------------------------

    def append_reasoning_event(self, **kwargs: Any) -> dict[str, Any]:
        # Hot while the run is in flight; migrated to durable on complete.
        return self._hot.append_reasoning_event(**kwargs)

    def append_tool_call(self, **kwargs: Any) -> dict[str, Any]:
        return self._hot.append_tool_call(**kwargs)

    def create_subagent_run(self, **kwargs: Any) -> dict[str, Any]:
        return self._hot.create_subagent_run(**kwargs)

    def request_human_decision(self, **kwargs: Any) -> dict[str, Any]:
        return self._hot.request_human_decision(**kwargs)

    def resolve_human_decision(self, **kwargs: Any) -> dict[str, Any]:
        # Participant check needs the hot mirror.  ``resolve_human_decision``
        # inside ProductionStore calls ``_require_participant`` against the
        # same connection, so make sure that mirror exists first.
        actor_id = kwargs.get("actor_id")
        request_id = kwargs.get("request_id")
        if actor_id and request_id:
            try:
                with self._hot._read_only() as conn:  # noqa: SLF001
                    row = conn.execute(
                        "SELECT r.conversation_id FROM human_requests h JOIN agent_runs r ON r.id=h.run_id WHERE h.id=?",
                        (request_id,),
                    ).fetchone()
                    conversation_id = row["conversation_id"] if row else None
                if conversation_id:
                    self._hydrate_hot_participant(actor_id=actor_id, conversation_id=conversation_id)
            except Exception:  # noqa: BLE001
                pass
        return self._hot.resolve_human_decision(**kwargs)

    def request_run_cancellation(self, **kwargs: Any) -> dict[str, Any]:
        actor_id = kwargs.get("actor_id")
        run_id = kwargs.get("run_id")
        # Check hot first — active runs live there.
        with self._hot._read_only() as conn:  # noqa: SLF001
            row = conn.execute("SELECT conversation_id FROM agent_runs WHERE id=?", (run_id,)).fetchone()
            if row and actor_id:
                self._hydrate_hot_participant(actor_id=actor_id, conversation_id=str(row["conversation_id"]))
        if row:
            return self._hot.request_run_cancellation(**kwargs)
        # Already migrated — return the durable snapshot.
        return self._durable.request_run_cancellation(**kwargs)

    def retry_run(self, *, actor_id: str, run_id: str) -> dict[str, Any]:
        # Look up the source run in either backend; migrated runs live in
        # durable.  We defer to ProductionStore for the actual work,
        # mirroring participants so hot has the row too.
        for backend, is_hot in ((self._hot, True), (self._durable, False)) if self._durable is not self._hot else ((self._hot, True),):
            try:
                with backend._read_only() as conn:  # noqa: SLF001
                    row = conn.execute("SELECT conversation_id FROM agent_runs WHERE id=?", (run_id,)).fetchone()
            except Exception:  # noqa: BLE001
                row = None
            if row:
                if not is_hot:
                    self._mirror_participant(conversation_id=str(row["conversation_id"]), user_id=actor_id)
                return backend.retry_run(actor_id=actor_id, run_id=run_id)
        raise KeyError(run_id)

    def recover_expired_runs(self) -> list[str]:
        return self._hot.recover_expired_runs()

    def force_run_lease_expiry(self, run_id: str, when: Any) -> None:
        # Active runs live in hot; also apply to durable in case caller is
        # inspecting a migrated run.
        with suppress(Exception):
            self._hot.force_run_lease_expiry(run_id, when)
        if self._durable is not self._hot:
            with suppress(Exception):
                self._durable.force_run_lease_expiry(run_id, when)

    # ------------------------------------------------------------------
    # Run queries: hot first, then durable.
    # ------------------------------------------------------------------

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._hot._read_only() as conn:  # noqa: SLF001
            row = conn.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
            if row:
                return ProductionStore._run_dict(row)  # noqa: SLF001
        if self._durable is self._hot:
            raise KeyError(run_id)
        return self._durable.get_run(run_id)

    def get_run_for_actor(self, *, actor_id: str, run_id: str) -> dict[str, Any]:
        with self._hot._read_only() as conn:  # noqa: SLF001
            row = conn.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        if row:
            self._hydrate_hot_participant(actor_id=actor_id, conversation_id=str(row["conversation_id"]))
            return self._hot.get_run_for_actor(actor_id=actor_id, run_id=run_id)
        if self._durable is self._hot:
            raise KeyError(run_id)
        return self._durable.get_run_for_actor(actor_id=actor_id, run_id=run_id)

    def get_run_detail_for_actor(self, *, actor_id: str, run_id: str) -> dict[str, Any]:
        with self._hot._read_only() as conn:  # noqa: SLF001
            row = conn.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        if row:
            self._hydrate_hot_participant(actor_id=actor_id, conversation_id=str(row["conversation_id"]))
            return self._hot.get_run_detail_for_actor(actor_id=actor_id, run_id=run_id)
        if self._durable is self._hot:
            raise KeyError(run_id)
        return self._durable.get_run_detail_for_actor(actor_id=actor_id, run_id=run_id)

    def list_run_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._hot._read_only() as conn:  # noqa: SLF001
            row = conn.execute("SELECT 1 FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        if row:
            return self._hot.list_run_events(run_id)
        if self._durable is self._hot:
            return []
        return self._durable.list_run_events(run_id)

    def run_events_after(self, *, run_id: str, after_sequence: int) -> list[dict[str, Any]]:
        with self._hot._read_only() as conn:  # noqa: SLF001
            row = conn.execute("SELECT 1 FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        if row:
            return self._hot.run_events_after(run_id=run_id, after_sequence=after_sequence)
        if self._durable is self._hot:
            return []
        return self._durable.run_events_after(run_id=run_id, after_sequence=after_sequence)

    # ------------------------------------------------------------------
    # Local-first delta sync (issue #9 §3 conversation durability).
    # ------------------------------------------------------------------

    def sync_due(self) -> bool:
        """True when the opportunistic interval has elapsed AND there are dirty rows.

        ``MUNIN_SYNC_INTERVAL=0`` (default) disables opportunistic syncs — the
        only flush points are ``complete_run`` and ``close_pools``. A positive
        interval lets a long-running server trickle the conversation delta to
        Turso without waiting for shutdown.
        """
        if getattr(self._settings, "sync_interval_s", 0) <= 0:
            return False
        if not self._sync_dirty:
            return False
        last_ms = self._sync_last_ms
        return (time.time() * 1000 - last_ms) >= self._settings.sync_interval_s * 1000

    @property
    def _sync_dirty(self) -> int:
        return self._hot.sync_outbox_pending()

    def flush_pending_syncs(self, *, batch_size: int | None = None) -> int:
        """Upload the local hot delta to the durable backend.

        Reads every (table, rowid) entry in ``_sync_outbox`` below a
        high-watermark captured at the START of the flush, upserts those rows
        into durable (idempotent via primary keys), then deletes entries
        ``<= watermark``. Entries committed during the flush survive and are
        picked up by the next call — writers never block a flush.

        Returns the number of rows synced. No-op (returns 0) when:
        * hot-only mode (no separate durable backend); or
        * the durable backend is itself a namespaced Turso proxy without sync
          tracking; or
        * the outbox has no entries.

        Idempotent: re-running with the same outbox replays the same upserts
        (INSERT OR REPLACE on primary-key columns), so a crash between the
        durable commit and the outbox trim is safe — the next flush replays.
        """
        if self._durable is self._hot:
            return 0
        limit = int(batch_size or getattr(self._settings, "sync_batch_size", 500))
        if limit <= 0:
            limit = 500
        # Phase 1: read the watermark + entries from hot (read-only). A
        # writer committing during this window writes entries ABOVE the
        # watermark (AUTOINCREMENT seq is monotonic), so they survive the trim.
        try:
            with self._hot._read_only() as hconn:  # noqa: SLF001
                watermark_row = hconn.execute(
                    "SELECT MAX(seq) AS m FROM _sync_outbox"
                ).fetchone()
                watermark = int(
                    (watermark_row["m"] if hasattr(watermark_row, "keys") else watermark_row[0])
                    if watermark_row else 0
                )
                if watermark == 0:
                    return 0
                entries = [
                    {"seq": r[0], "table_name": r[1], "rowid": r[2]}
                    for r in hconn.execute(
                        "SELECT seq, table_name, rowid FROM _sync_outbox "
                        "WHERE seq <= ? ORDER BY seq LIMIT ?",
                        (watermark, limit),
                    ).fetchall()
                ]
                if not entries:
                    return 0
                # Group by table and read every referenced row's full column
                # set ONCE (still inside the read-only txn so the snapshot is
                # internally consistent). We capture both the column names
                # and the row tuples up front — later we write them to durable
                # without touching hot again.
                by_table: dict[str, list[int]] = {}
                for entry in entries:
                    by_table.setdefault(entry["table_name"], []).append(entry["rowid"])
                payload: dict[str, tuple[list[str], list[tuple]]] = {}
                for table, rowids in by_table.items():
                    if table not in _SYNC_TABLES:
                        continue
                    ph = ",".join("?" * len(rowids))
                    cols_row = hconn.execute(
                        f"SELECT * FROM {table} WHERE rowid IN ({ph})", rowids
                    ).fetchall()
                    if not len(cols_row):
                        continue
                    col_names = list(
                        cols_row[0].keys()
                        if hasattr(cols_row[0], "keys")
                        else [d[0] for d in cols_row[0].cursor.description]
                    )
                    rows_to_write = [tuple(r[c] for c in col_names) for r in cols_row]
                    payload[table] = (col_names, rows_to_write)
            if not payload:
                return 0
            # Phase 2: write everything to durable in ONE transaction so the
            # sync is atomic (all-or-nothing) — if durable rejects one table
            # (e.g. an FK violation), the outbox is left intact and the next
            # flush retries. FK order: write parents before children by
            # sorting tables so users/conversations precede messages/runs.
            order = {t: i for i, t in enumerate(_SYNC_TABLES)}
            ordered = sorted(payload.items(), key=lambda kv: order.get(kv[0], 999))
            col_csvs: dict[str, str] = {}
            placeholder_csvs: dict[str, str] = {}
            for table, (col_names, _) in ordered:
                col_csvs[table] = ", ".join(col_names)
                placeholder_csvs[table] = ", ".join("?" * len(col_names))
            synced = 0
            try:
                with self._durable._transaction() as dconn:  # noqa: SLF001
                    for table, (col_names, rows_to_write) in ordered:
                        sql = (
                            f"INSERT OR REPLACE INTO {table} "
                            f"({col_csvs[table]}) VALUES ({placeholder_csvs[table]})"
                        )
                        for row in rows_to_write:
                            dconn.execute(sql, row)
                        synced += len(rows_to_write)
            except Exception:  # noqa: BLE001
                logger.debug("sync flush durable write failed", exc_info=True)
                return 0  # leave outbox intact for retry
            # Phase 3: trim only after a fully-committed durable transaction.
            if synced > 0:
                with suppress(Exception):
                    with self._hot._transaction() as hconn:  # noqa: SLF001
                        hconn.execute(
                            "DELETE FROM _sync_outbox WHERE seq <= ?", (watermark,)
                        )
            self._sync_last_ms = int(time.time() * 1000)
            return synced
        except Exception:  # noqa: BLE001
            logger.debug("sync flush aborted", exc_info=True)
            return 0

    # ------------------------------------------------------------------
    # Migration — the explicit hot → durable move point.
    # ------------------------------------------------------------------

    def complete_run(self, *, run_id: str, lease_token: str, content: str, outcome: str) -> bool:
        """Finalise a hot run and migrate all derivative rows to durable.

        Semantics match ``ProductionStore.complete_run``: idempotent
        against the lease token and fencing epoch, records the final
        ``run.<outcome>`` event, updates the assistant message row and
        emits its revision, extracts fenced-code artifacts.  In addition
        this method:

        1. Copies the hot ``agent_runs`` row (with the final state) into
           durable.
        2. Copies all ``run_events``, ``reasoning_events``, ``tool_calls``,
           ``subagent_runs``, and ``human_requests`` from hot to durable.
        3. Deletes the migrated rows from hot so subsequent queries pick
           up the durable copy.

        On any failure after step 1 the hot copies remain; a retry
        replays the migration and INSERT-OR-IGNORE keeps durable
        idempotent.
        """
        if outcome not in FINAL_RUN_STATES:
            raise ValueError("outcome must be a final run state")

        # Hot-only shortcut: when there's no separate durable backend the
        # legacy ProductionStore semantics apply.  Delegate to the hot
        # implementation and short-circuit.
        if self._durable is self._hot:
            return self._hot.complete_run(
                run_id=run_id, lease_token=lease_token, content=content, outcome=outcome,
            )

        now = _now_ms()

        # --- Step 1: mark the hot run terminal + collect rows ---
        with self._hot._transaction() as conn:  # noqa: SLF001
            row = conn.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
            if not row or row["state"] != "running" or not hmac.compare_digest(str(row["lease_token"] or ""), lease_token):
                return False
            if int(row["lease_expires_at_ms"] or 0) < now:
                return False
            fencing_epoch = int(row["fencing_epoch"])
            changed = conn.execute(
                "UPDATE agent_runs SET state=?,lease_token=NULL,lease_expires_at_ms=NULL,state_version=state_version+1,updated_at_ms=? WHERE id=? AND state='running' AND lease_token=? AND fencing_epoch=?",
                (outcome, now, run_id, lease_token, fencing_epoch),
            )
            if int(changed.rowcount) != 1:
                return False
            # Emit the terminal run event ON HOT — it'll get copied to
            # durable below.
            ProductionStore._append_event(  # noqa: SLF001
                self._hot,
                conn,
                run_id=run_id,
                kind=f"run.{outcome}",
                payload={"assistant_message_id": row["assistant_message_id"]},
            )
            run_data = _row(row)
            events = [_row(r) for r in conn.execute(
                "SELECT * FROM run_events WHERE run_id=? ORDER BY sequence", (run_id,)
            ).fetchall()]
            reasoning_rows = [_row(r) for r in conn.execute(
                "SELECT * FROM reasoning_events WHERE run_id=?", (run_id,)
            ).fetchall()]
            tool_rows = [_row(r) for r in conn.execute(
                "SELECT * FROM tool_calls WHERE run_id=?", (run_id,)
            ).fetchall()]
            subagent_rows = [_row(r) for r in conn.execute(
                "SELECT * FROM subagent_runs WHERE parent_run_id=?", (run_id,)
            ).fetchall()]
            hitl_rows = [_row(r) for r in conn.execute(
                "SELECT * FROM human_requests WHERE run_id=?", (run_id,)
            ).fetchall()]

        assistant_message_id = str(run_data["assistant_message_id"])
        safe_content = redact_text(content)
        safe_hash = hashlib.sha256(safe_content.encode()).hexdigest()

        # --- Step 2: durable — insert copies, finalise message, extract
        # artifacts, bump conversation activity, audit ---
        try:
            with self._durable._transaction() as conn:  # noqa: SLF001
                # 2a. agent_runs — idempotent INSERT-OR-IGNORE using the
                # hot row's final state.  If a previous complete_run
                # migrated it we skip the derivative inserts too.
                existed = conn.execute("SELECT id FROM agent_runs WHERE id=?", (run_id,)).fetchone()
                if not existed:
                    conn.execute(
                        """INSERT INTO agent_runs (
                            id,conversation_id,actor_id,user_message_id,assistant_message_id,
                            root_run_id,parent_run_id,attempt,state,idempotency_key,request_hash,
                            lease_worker_id,lease_token,lease_expires_at_ms,fencing_epoch,cancel_requested_at_ms,
                            model_profile_id,budget_json,context_manifest_json,state_version,created_at_ms,updated_at_ms
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            run_data["id"], run_data["conversation_id"], run_data["actor_id"],
                            run_data["user_message_id"], run_data["assistant_message_id"],
                            run_data.get("root_run_id"), run_data.get("parent_run_id"),
                            int(run_data.get("attempt") or 1), outcome,
                            run_data["idempotency_key"], run_data["request_hash"],
                            None, None, None,
                            int(run_data.get("fencing_epoch") or 0),
                            run_data.get("cancel_requested_at_ms"),
                            run_data.get("model_profile_id"),
                            run_data.get("budget_json") or "{}",
                            run_data.get("context_manifest_json") or "{}",
                            int(run_data.get("state_version") or 1) + 1,
                            int(run_data.get("created_at_ms") or now), now,
                        ),
                    )
                    # 2b. events + reasoning + tool_calls + subagents + human_requests
                    for e in events:
                        conn.execute(
                            "INSERT INTO run_events (id,run_id,sequence,kind,payload_json,causation_id,correlation_id,actor_id,redaction_policy_version,created_at_ms) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (
                                e["id"], e["run_id"], int(e["sequence"]), e["kind"],
                                e["payload_json"], e.get("causation_id"), e.get("correlation_id"),
                                e.get("actor_id"), e["redaction_policy_version"],
                                int(e["created_at_ms"]),
                            ),
                        )
                    for r in reasoning_rows:
                        conn.execute(
                            "INSERT INTO reasoning_events (id,run_id,event_id,kind,content,provider,agent_name,step,persisted,provenance,created_at_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                r["id"], r["run_id"], r["event_id"], r["kind"], r["content"],
                                r["provider"], r["agent_name"], int(r["step"]),
                                int(r["persisted"]), r["provenance"], int(r["created_at_ms"]),
                            ),
                        )
                    for t in tool_rows:
                        conn.execute(
                            "INSERT INTO tool_calls (id,run_id,event_id,agent_name,tool_name,state,args_json,result_json,scope_json,started_at_ms,finished_at_ms,retry_of_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                t["id"], t["run_id"], t.get("event_id"), t["agent_name"],
                                t["tool_name"], t["state"], t["args_json"], t["result_json"],
                                t["scope_json"], int(t["started_at_ms"]),
                                t.get("finished_at_ms"), t.get("retry_of_id"),
                            ),
                        )
                    for s in subagent_rows:
                        conn.execute(
                            "INSERT INTO subagent_runs (id,parent_run_id,profile_id,state,objective,lease_token,started_at_ms,finished_at_ms) VALUES (?,?,?,?,?,?,?,?)",
                            (
                                s["id"], s["parent_run_id"], s["profile_id"], s["state"],
                                s["objective"], s.get("lease_token"),
                                s.get("started_at_ms"), s.get("finished_at_ms"),
                            ),
                        )
                    for h in hitl_rows:
                        conn.execute(
                            "INSERT INTO human_requests (id,run_id,action,args_hash,risk,evidence_json,scope_json,choices_json,nonce_hash,state,response_json,expires_at_ms,created_at_ms,resolved_at_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                h["id"], h["run_id"], h["action"], h["args_hash"], h["risk"],
                                h["evidence_json"], h["scope_json"], h["choices_json"],
                                h["nonce_hash"], h["state"], h["response_json"],
                                int(h["expires_at_ms"]), int(h["created_at_ms"]),
                                h.get("resolved_at_ms"),
                            ),
                        )

                # 2c. finalise the assistant message on durable.
                message = conn.execute("SELECT version FROM messages WHERE id=?", (assistant_message_id,)).fetchone()
                if message is not None:
                    conn.execute(
                        "UPDATE messages SET kind='assistant',status=?,content=?,content_hash=?,version=version+1,updated_at_ms=? WHERE id=? AND version=?",
                        (outcome, safe_content, safe_hash, now, assistant_message_id, message["version"]),
                    )
                    conn.execute(
                        "INSERT INTO message_revisions (id,message_id,version,content,reason,created_at_ms) VALUES (?,?,?,?,?,?)",
                        (_id("rev"), assistant_message_id, int(message["version"]) + 1, safe_content, f"run.{outcome}", now),
                    )

                # 2d. artifacts extracted from the response content.
                for index, match in enumerate(_FENCED_ARTIFACT.finditer(safe_content), start=1):
                    language = (match.group("language") or "markdown").lower()
                    body = match.group("content")
                    if body.strip():
                        extension, media_type = {
                            "python": ("py", "text/x-python"),
                            "py": ("py", "text/x-python"),
                            "json": ("json", "application/json"),
                            "markdown": ("md", "text/markdown"),
                            "md": ("md", "text/markdown"),
                        }.get(language, ("txt", "text/plain"))
                        ProductionStore._insert_artifact(  # noqa: SLF001
                            conn,
                            conversation_id=run_data["conversation_id"],
                            message_id=assistant_message_id,
                            run_id=run_id,
                            filename=f"run-{run_id[-8:]}-{index}.{extension}",
                            media_type=media_type,
                            language=language,
                            content=body,
                            now=now,
                        )

                # 2e. bump the conversation activity marker.
                conn.execute(
                    "UPDATE conversations SET last_activity_at_ms=?,updated_at_ms=?,version=version+1 WHERE id=?",
                    (now, now, run_data["conversation_id"]),
                )
        except Exception:
            # Durable write failed — leave hot rows in place so a caller
            # retry can re-attempt migration.  Do NOT delete from hot.
            raise

        # --- Step 3: delete migrated rows from hot ---
        with suppress(Exception):
            with self._hot._transaction() as conn:  # noqa: SLF001
                conn.execute("DELETE FROM run_events WHERE run_id=?", (run_id,))
                conn.execute("DELETE FROM reasoning_events WHERE run_id=?", (run_id,))
                conn.execute("DELETE FROM tool_calls WHERE run_id=?", (run_id,))
                conn.execute("DELETE FROM subagent_runs WHERE parent_run_id=?", (run_id,))
                conn.execute("DELETE FROM human_requests WHERE run_id=?", (run_id,))
                conn.execute("DELETE FROM agent_runs WHERE id=?", (run_id,))
        # --- Step 4: trickle the rest of the conversation delta (new
        # messages, conversation_participants, summaries, run events still
        # hot) to durable so the operator sees them after a restart. The
        # explicit migrate above handled THIS run's rows; the outbox tracks
        # everything else (e.g. the user/assistant message rows committed by
        # chat.py on the durable side via _transaction but mirrored here).
        if getattr(self._settings, "sync_at_end", True):
            with suppress(Exception):
                self.flush_pending_syncs()
        return True

    # ------------------------------------------------------------------
    # Everything else — default to durable.
    # ------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        # ``__getattr__`` is only consulted for attributes not found on
        # the instance/class — every explicit override above wins.  This
        # keeps the facade compact for the ~40 durable-only methods
        # (conversations, artifacts, provider profiles, snapshots,
        # branches, audit, summaries, broadcasts, …).
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._durable, name)
