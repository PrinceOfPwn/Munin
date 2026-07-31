"""v3.1 extensions for :class:`ProductionStore`.

This module intentionally lives beside ``store.py`` instead of editing it.  The
existing store owns a checksum-guarded migration; adding tables *inside* that
migration would break the checksum and refuse to boot on every existing
deployment.  Instead the extensions here:

* Install their own **idempotent** schema (CREATE TABLE IF NOT EXISTS, plus a
  PRAGMA-guarded ADD COLUMN for ``tool_calls.parallel_group_id``).
* Attach new methods to the ``ProductionStore`` **instance** via
  :func:`types.MethodType` so callers get a fluent ``store.method(...)`` API
  without touching the base class.
* Reuse the store's private ``_connect()`` / ``_transaction()`` context
  managers so every write stays inside the same short IMMEDIATE transaction the
  rest of the aggregate uses.

Call :func:`install_v3_1_extensions(store)` exactly once at boot, right after
``store.migrate()``.  It is safe to call again — every operation is idempotent.

New tables
----------
* ``conversation_collaborators`` — owner / collaborator / viewer roles per
  conversation.  The existing ``conversation_participants`` table remains the
  source of truth for "who can see this conversation"; ``collaborators``
  layers a role model on top so the UI can distinguish an owner-only action
  (e.g. add teammate) from a collaborator's guidance/note contribution.
* ``conversation_notes`` — sidebar annotations that never reach the model.
* ``conversation_presence`` — last-seen + typing indicator per (conv, actor).
* ``run_guidance_queue`` — durable outbox of operator guidance waiting to be
  injected on the next ReAct iteration; the dispatcher drains this queue on
  every ``pre_iteration`` hook call.

New columns
-----------
* ``tool_calls.parallel_group_id`` (TEXT, nullable) — populated by the
  dispatcher when it emits N tool calls concurrently, so the UI can render
  them as a single group instead of a serial cascade.
* ``reasoning_events.metadata_json`` (TEXT, nullable) — arbitrary JSON payload
  used by :mod:`forge_progress` to carry the ``stage`` + extras through the
  read model.  Reads fall back to ``{}`` when the column is absent so tests
  that mount the base schema still pass.
"""

from __future__ import annotations

import json
import threading
import time
import types
import uuid
from typing import Any

from .store import ProductionStore, _now_ms

# Guards concurrent writes when the underlying store connection is not itself
# thread-safe (e.g. the sqlite3-based local test fixture).  A single RLock per
# store instance is enough: writes serialise, reads that don't need consistency
# stay lock-free.  The Turso HTTP driver is already safe for concurrent calls,
# in which case the lock adds a negligible acquire/release cost.
_store_locks: "dict[int, threading.RLock]" = {}
_store_locks_guard = threading.Lock()


def _store_lock(store: ProductionStore) -> threading.RLock:
    key = id(store)
    with _store_locks_guard:
        lock = _store_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _store_locks[key] = lock
    return lock


V3_1_MIGRATION_MARKER = "20260729_002_v3_1_collab_forge_parallel"

_V3_1_DDL: tuple[str, ...] = (
    # ── multi-operator collaboration ────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS conversation_collaborators (
        conversation_id TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('owner','collaborator','viewer')),
        added_at_ms INTEGER NOT NULL,
        added_by_actor_id TEXT NOT NULL,
        PRIMARY KEY (conversation_id, actor_id)
    )""",
    """CREATE INDEX IF NOT EXISTS conversation_collaborators_by_actor
        ON conversation_collaborators(actor_id)""",
    """CREATE TABLE IF NOT EXISTS conversation_notes (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        body TEXT NOT NULL,
        created_at_ms INTEGER NOT NULL
    )""",
    """CREATE INDEX IF NOT EXISTS conversation_notes_by_conversation
        ON conversation_notes(conversation_id, created_at_ms)""",
    """CREATE TABLE IF NOT EXISTS conversation_presence (
        conversation_id TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        last_seen_ms INTEGER NOT NULL,
        typing_at_ms INTEGER,
        PRIMARY KEY (conversation_id, actor_id)
    )""",
    # ── forge / guidance queue ──────────────────────────────────────────
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
    # ── conversation broadcast log (backs the conversation SSE cursor) ─
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


# ---------------------------------------------------------------------------
# Column existence guard — Turso/libsql supports ADD COLUMN but only when the
# column is missing.  ``PRAGMA table_info`` is universal (sqlite + libsql).
# ---------------------------------------------------------------------------


def _column_exists(conn: Any, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    for row in rows:
        # PRAGMA rows expose columns by index (0,1,2,...) — 1 == column name.
        name = row["name"] if hasattr(row, "keys") else row[1]
        if str(name) == column:
            return True
    return False


def _apply_column_migrations(store: ProductionStore) -> None:
    """Add optional columns to existing tables without breaking the base checksum."""
    from .store import _namespace_production_sql  # local import to avoid cycles

    # NOTE: the base store namespaces `tool_calls` → `production_tool_calls`
    # when running against the shared Turso DB, but the namespacing proxy is
    # only wired on *statements executed through the store*.  We do the same by
    # calling `conn.execute` — the proxy is applied for us.  The migration
    # therefore uses the un-namespaced identifier and lets the proxy rewrite.
    with _store_lock(store), store._transaction() as conn:  # noqa: SLF001 - reuse the store's txn
        if not _column_exists(conn, "tool_calls", "parallel_group_id"):
            conn.execute("ALTER TABLE tool_calls ADD COLUMN parallel_group_id TEXT")
        if not _column_exists(conn, "tool_calls", "tool_use_id"):
            # Anthropic / OpenAI tool_use identifiers survive across events so
            # the UI can correlate a running call with its result even when
            # they arrive out of order.
            conn.execute("ALTER TABLE tool_calls ADD COLUMN tool_use_id TEXT")
        if not _column_exists(conn, "reasoning_events", "metadata_json"):
            conn.execute("ALTER TABLE reasoning_events ADD COLUMN metadata_json TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS tool_calls_by_parallel_group "
            "ON tool_calls(parallel_group_id)"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    return {key: row[key] for key in row.keys()}


# ---------------------------------------------------------------------------
# Collaborators
# ---------------------------------------------------------------------------

_ROLE_ORDER = {"viewer": 0, "collaborator": 1, "owner": 2}


def add_collaborator(
    self: ProductionStore,
    *,
    conversation_id: str,
    actor_id: str,
    role: str,
    added_by_actor_id: str,
) -> None:
    if role not in _ROLE_ORDER:
        raise ValueError(f"invalid collaborator role: {role}")
    now = _now_ms()
    with _store_lock(self), self._transaction() as conn:
        # Ensure caller is at least a collaborator (owner only for role=owner).
        if role == "owner":
            existing = conn.execute(
                "SELECT role FROM conversation_collaborators WHERE conversation_id=? AND actor_id=?",
                (conversation_id, added_by_actor_id),
            ).fetchone()
            if not existing or existing["role"] != "owner":
                # Fall back to the ``conversations.owner_id`` column set at
                # conversation creation time — the very first owner may not
                # be recorded in the collaborators table yet.
                owner_row = conn.execute(
                    "SELECT owner_id FROM conversations WHERE id=?",
                    (conversation_id,),
                ).fetchone()
                if not owner_row or owner_row["owner_id"] != added_by_actor_id:
                    raise PermissionError("only the owner may grant the owner role")
        conn.execute(
            """INSERT INTO conversation_collaborators
                (conversation_id, actor_id, role, added_at_ms, added_by_actor_id)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(conversation_id, actor_id) DO UPDATE SET
                 role = excluded.role,
                 added_at_ms = excluded.added_at_ms,
                 added_by_actor_id = excluded.added_by_actor_id""",
            (conversation_id, actor_id, role, now, added_by_actor_id),
        )
        # Keep ``conversation_participants`` in sync so the base ``_require_
        # participant`` check still accepts the new collaborator.
        conn.execute(
            """INSERT INTO conversation_participants
                (conversation_id, user_id, role, added_at_ms)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(conversation_id, user_id) DO UPDATE SET
                 role = excluded.role,
                 removed_at_ms = NULL""",
            (conversation_id, actor_id, role, now),
        )
        self._audit(  # noqa: SLF001 - internal helper reused
            conn,
            actor_id=added_by_actor_id,
            action="collaborator.added",
            resource_type="conversation",
            resource_id=conversation_id,
            outcome="success",
            metadata={"target_actor": actor_id, "role": role},
        )


def list_collaborators(
    self: ProductionStore, *, conversation_id: str
) -> list[dict[str, Any]]:
    conn = self._connect()
    try:
        rows = conn.execute(
            """SELECT c.conversation_id, c.actor_id, c.role, c.added_at_ms, c.added_by_actor_id,
                       u.username AS actor_username
               FROM conversation_collaborators c
               LEFT JOIN users u ON u.id = c.actor_id
               WHERE c.conversation_id = ?
               ORDER BY c.added_at_ms""",
            (conversation_id,),
        ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            entry = _row_dict(row)
            entry.setdefault("actor_username", entry.get("actor_id"))
            results.append(entry)
        # If the collaborators table hasn't been populated yet, surface the
        # ``conversations.owner_id`` as the implicit owner so the UI can render
        # a non-empty presence list.
        if not results:
            owner = conn.execute(
                """SELECT c.owner_id AS actor_id, u.username AS actor_username, c.created_at_ms AS added_at_ms
                   FROM conversations c LEFT JOIN users u ON u.id = c.owner_id
                   WHERE c.id = ?""",
                (conversation_id,),
            ).fetchone()
            if owner:
                results.append(
                    {
                        "conversation_id": conversation_id,
                        "actor_id": owner["actor_id"],
                        "actor_username": owner["actor_username"] or owner["actor_id"],
                        "role": "owner",
                        "added_at_ms": int(owner["added_at_ms"] or 0),
                        "added_by_actor_id": owner["actor_id"],
                    }
                )
        return results
    finally:
        conn.close()


def require_collaborator_access(
    self: ProductionStore,
    *,
    conversation_id: str,
    actor_id: str,
    required_role: str = "collaborator",
) -> None:
    if required_role not in _ROLE_ORDER:
        raise ValueError(f"invalid required role: {required_role}")
    threshold = _ROLE_ORDER[required_role]
    conn = self._connect()
    try:
        row = conn.execute(
            "SELECT role FROM conversation_collaborators WHERE conversation_id=? AND actor_id=?",
            (conversation_id, actor_id),
        ).fetchone()
        if row and _ROLE_ORDER.get(str(row["role"]), -1) >= threshold:
            return
        # Fallbacks: the base conversation owner is implicitly owner; any
        # existing participant is at least a viewer.
        owner = conn.execute(
            "SELECT owner_id FROM conversations WHERE id=?", (conversation_id,)
        ).fetchone()
        if owner and owner["owner_id"] == actor_id:
            return
        participant = conn.execute(
            """SELECT role FROM conversation_participants
               WHERE conversation_id=? AND user_id=? AND removed_at_ms IS NULL""",
            (conversation_id, actor_id),
        ).fetchone()
        if participant and _ROLE_ORDER.get(str(participant["role"]), 0) >= threshold:
            return
        raise PermissionError("collaborator access denied for this conversation")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Notes — sidebar annotations that never reach the model
# ---------------------------------------------------------------------------


def append_note(
    self: ProductionStore,
    *,
    conversation_id: str,
    actor_id: str,
    body: str,
) -> dict[str, Any]:
    trimmed = body.strip()
    if not trimmed:
        raise ValueError("note body is required")
    if len(trimmed) > 8_000:
        raise ValueError("note exceeds maximum size (8000 chars)")
    note = {
        "id": _id("note"),
        "conversation_id": conversation_id,
        "actor_id": actor_id,
        "body": trimmed,
        "created_at_ms": _now_ms(),
    }
    with _store_lock(self), self._transaction() as conn:
        self.require_collaborator_access(  # type: ignore[attr-defined]
            conversation_id=conversation_id,
            actor_id=actor_id,
            required_role="collaborator",
        )
        conn.execute(
            """INSERT INTO conversation_notes
                (id, conversation_id, actor_id, body, created_at_ms)
               VALUES (?, ?, ?, ?, ?)""",
            (note["id"], conversation_id, actor_id, trimmed, note["created_at_ms"]),
        )
    # Enrich with the poster's username for UI convenience.
    conn = self._connect()
    try:
        row = conn.execute(
            "SELECT username FROM users WHERE id=?", (actor_id,)
        ).fetchone()
        note["actor_username"] = row["username"] if row else actor_id
    finally:
        conn.close()
    # Emit a durable broadcast so the conversation SSE stream picks it up
    # via cursor rather than polling ``list_notes`` every second.
    self.append_conversation_broadcast(  # type: ignore[attr-defined]
        conversation_id=conversation_id, kind="note-appended", payload=note,
    )
    return note


def list_notes(
    self: ProductionStore,
    *,
    conversation_id: str,
    after_ms: int = 0,
) -> list[dict[str, Any]]:
    conn = self._connect()
    try:
        rows = conn.execute(
            """SELECT n.id, n.conversation_id, n.actor_id, n.body, n.created_at_ms,
                       u.username AS actor_username
               FROM conversation_notes n
               LEFT JOIN users u ON u.id = n.actor_id
               WHERE n.conversation_id = ? AND n.created_at_ms > ?
               ORDER BY n.created_at_ms""",
            (conversation_id, max(0, int(after_ms))),
        ).fetchall()
        return [_row_dict(row) for row in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Presence — last seen + typing
# ---------------------------------------------------------------------------


def heartbeat_presence(
    self: ProductionStore,
    *,
    conversation_id: str,
    actor_id: str,
    typing: bool,
) -> None:
    now = _now_ms()
    with _store_lock(self), self._transaction() as conn:
        before = conn.execute(
            "SELECT actor_id, typing_at_ms, last_seen_ms FROM conversation_presence "
            "WHERE conversation_id=?",
            (conversation_id,),
        ).fetchall()
        conn.execute(
            """INSERT INTO conversation_presence
                (conversation_id, actor_id, last_seen_ms, typing_at_ms)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(conversation_id, actor_id) DO UPDATE SET
                 last_seen_ms = excluded.last_seen_ms,
                 typing_at_ms = CASE
                    WHEN excluded.typing_at_ms IS NOT NULL THEN excluded.typing_at_ms
                    ELSE conversation_presence.typing_at_ms
                 END""",
            (conversation_id, actor_id, now, now if typing else None),
        )

    def _signature(rows: list[Any]) -> str:
        parts = sorted(
            (
                str(r["actor_id"]),
                bool(r["typing_at_ms"]) and (now - int(r["typing_at_ms"] or 0)) <= 5_000,
                bool(r["last_seen_ms"]) and (now - int(r["last_seen_ms"] or 0)) <= 45_000,
            )
            for r in rows
        )
        return json.dumps(parts, separators=(",", ":"), default=str)

    after = self.active_presence(conversation_id=conversation_id)  # type: ignore[attr-defined]
    before_sig = _signature(before)
    after_rows = [
        {"actor_id": p["actor_id"], "typing_at_ms": p.get("typing_at_ms"), "last_seen_ms": p["last_seen_ms"]}
        for p in after
    ]
    after_sig = _signature(after_rows)  # type: ignore[arg-type]
    if before_sig != after_sig:
        self.append_conversation_broadcast(  # type: ignore[attr-defined]
            conversation_id=conversation_id, kind="presence-changed", payload={"presence": after},
        )


def active_presence(
    self: ProductionStore,
    *,
    conversation_id: str,
    max_age_ms: int = 45_000,
) -> list[dict[str, Any]]:
    now = _now_ms()
    conn = self._connect()
    try:
        rows = conn.execute(
            """SELECT p.actor_id, p.last_seen_ms, p.typing_at_ms, u.username AS actor_username
               FROM conversation_presence p
               LEFT JOIN users u ON u.id = p.actor_id
               WHERE p.conversation_id = ?
                 AND p.last_seen_ms >= ?
               ORDER BY p.last_seen_ms DESC""",
            (conversation_id, now - max(1_000, int(max_age_ms))),
        ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            entry = _row_dict(row)
            typing_at = entry.get("typing_at_ms")
            # A typing signal older than 5s is considered stale — the client
            # only re-sends typing=true while keystrokes are landing.
            entry["typing"] = bool(typing_at) and (now - int(typing_at)) <= 5_000
            entry.setdefault("actor_username", entry.get("actor_id"))
            results.append(entry)
        return results
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Guidance queue — outbox of operator hints waiting for the ReAct loop
# ---------------------------------------------------------------------------


def enqueue_guidance(
    self: ProductionStore,
    *,
    run_id: str,
    actor_id: str,
    actor_username: str,
    body: str,
    target_agent_id: str | None = None,
    budget_extension_seconds: int = 0,
) -> dict[str, Any]:
    trimmed = body.strip()
    if not trimmed:
        raise ValueError("guidance body is required")
    if len(trimmed) > 4_000:
        raise ValueError("guidance exceeds maximum size (4000 chars)")
    row = {
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
    with _store_lock(self), self._transaction() as conn:
        conn.execute(
            """INSERT INTO run_guidance_queue
                (id, run_id, actor_id, actor_username, body, target_agent_id,
                 created_at_ms, budget_extension_seconds)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["id"],
                run_id,
                actor_id,
                actor_username,
                trimmed,
                target_agent_id,
                row["created_at_ms"],
                row["budget_extension_seconds"],
            ),
        )
    return row


def consume_pending_guidance(
    self: ProductionStore,
    *,
    run_id: str,
    target_agent_id: str | None = None,
    delivered_at_step: int | None = None,
) -> list[dict[str, Any]]:
    now = _now_ms()
    with _store_lock(self), self._transaction() as conn:
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
            entry = _row_dict(row)
            conn.execute(
                "UPDATE run_guidance_queue SET consumed_at_ms=?, delivered_at_step=? WHERE id=?",
                (now, delivered_at_step, entry["id"]),
            )
            entry["consumed_at_ms"] = now
            entry["delivered_at_step"] = delivered_at_step
            results.append(entry)
        return results


def list_run_guidance(
    self: ProductionStore, *, run_id: str
) -> list[dict[str, Any]]:
    conn = self._connect()
    try:
        rows = conn.execute(
            """SELECT * FROM run_guidance_queue WHERE run_id = ? ORDER BY created_at_ms""",
            (run_id,),
        ).fetchall()
        return [_row_dict(row) for row in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Parallel tool group helpers
# ---------------------------------------------------------------------------


def append_tool_call_with_parallel_group(
    self: ProductionStore,
    *,
    run_id: str,
    agent_name: str,
    tool_name: str,
    state: str,
    arguments: Any,
    result: Any = None,
    scope: Any = None,
    tool_call_id: str | None = None,
    parallel_group_id: str | None = None,
    tool_use_id: str | None = None,
) -> dict[str, Any]:
    """Delegate to :meth:`ProductionStore.append_tool_call` and stamp v3.1 columns."""
    entry = self.append_tool_call(
        run_id=run_id,
        agent_name=agent_name,
        tool_name=tool_name,
        state=state,
        arguments=arguments,
        result=result,
        scope=scope,
        tool_call_id=tool_call_id,
    )
    if parallel_group_id or tool_use_id:
        with _store_lock(self), self._transaction() as conn:
            conn.execute(
                "UPDATE tool_calls SET parallel_group_id = COALESCE(?, parallel_group_id), "
                "tool_use_id = COALESCE(?, tool_use_id) WHERE id = ?",
                (parallel_group_id, tool_use_id, entry["id"]),
            )
        entry["parallel_group_id"] = parallel_group_id
        entry["tool_use_id"] = tool_use_id
    return entry


# ---------------------------------------------------------------------------
# Conversation broadcasts — durable cursor-addressable event log
# ---------------------------------------------------------------------------


_BROADCAST_KINDS = frozenset(
    {"note-appended", "presence-changed", "run-transition", "guidance-delivered"}
)


def append_conversation_broadcast(
    self: ProductionStore,
    *,
    conversation_id: str,
    kind: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if kind not in _BROADCAST_KINDS:
        raise ValueError(f"invalid broadcast kind: {kind}")
    now = _now_ms()
    entry_id = _id("bcast")
    with _store_lock(self), self._transaction() as conn:
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
            (entry_id, conversation_id, kind, json.dumps(payload, default=str, separators=(",", ":")), seq, now),
        )
    return {
        "id": entry_id,
        "conversation_id": conversation_id,
        "kind": kind,
        "payload": payload,
        "sequence": seq,
        "created_at_ms": now,
    }


def conversation_broadcasts_after(
    self: ProductionStore,
    *,
    conversation_id: str,
    after_sequence: int = 0,
    limit: int = 500,
) -> list[dict[str, Any]]:
    conn = self._connect()
    try:
        rows = conn.execute(
            """SELECT id, conversation_id, kind, payload_json, sequence, created_at_ms
               FROM conversation_broadcasts
               WHERE conversation_id = ? AND sequence > ?
               ORDER BY sequence
               LIMIT ?""",
            (conversation_id, max(0, int(after_sequence)), max(1, int(limit))),
        ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            entry = _row_dict(row)
            raw = entry.pop("payload_json", "{}")
            try:
                entry["payload"] = json.loads(raw) if raw else {}
            except (json.JSONDecodeError, TypeError):
                entry["payload"] = {}
            results.append(entry)
        return results
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Installer
# ---------------------------------------------------------------------------


_INSTALLED_FLAG = "__munin_v3_1_installed__"


def _wrap_append_reasoning_event(store: ProductionStore) -> None:
    """Add optional ``metadata`` support to ``append_reasoning_event``.

    The base method has a fixed signature; forge callers want to pass a
    structured stage payload alongside the redacted content.  We wrap the
    original method so passing ``metadata=...`` no longer raises ``TypeError``
    and, when the ``reasoning_events.metadata_json`` column exists, the
    payload is stored on the row for the frontend to consume.
    """
    original = store.append_reasoning_event

    def wrapped(
        *,
        run_id: str,
        kind: str,
        content: str,
        provider: str,
        persistence_enabled: bool,
        agent_name: str = "munin",
        step: int = 0,
        metadata: Any = None,
    ) -> dict[str, Any]:
        result = original(
            run_id=run_id,
            kind=kind,
            content=content,
            provider=provider,
            persistence_enabled=persistence_enabled,
            agent_name=agent_name,
            step=step,
        )
        if metadata is not None:
            with _store_lock(store), store._transaction() as conn:  # noqa: SLF001
                conn.execute(
                    "UPDATE reasoning_events SET metadata_json=? WHERE id=?",
                    (json.dumps(metadata, separators=(",", ":"), default=str), result["id"]),
                )
        return result

    setattr(store, "append_reasoning_event", wrapped)


def install_v3_1_extensions(store: ProductionStore) -> ProductionStore:
    """Apply schema + attach methods to a ``ProductionStore`` instance."""
    if getattr(store, _INSTALLED_FLAG, False):
        return store
    # 1. Additive schema.
    with _store_lock(store), store._transaction() as conn:  # noqa: SLF001
        for ddl in _V3_1_DDL:
            conn.execute(ddl)
    _apply_column_migrations(store)

    # 2. Attach methods.
    # Wrap append_reasoning_event to accept optional metadata (used by forge).
    _wrap_append_reasoning_event(store)

    for name, func in {
        "add_collaborator": add_collaborator,
        "list_collaborators": list_collaborators,
        "require_collaborator_access": require_collaborator_access,
        "append_note": append_note,
        "list_notes": list_notes,
        "heartbeat_presence": heartbeat_presence,
        "active_presence": active_presence,
        "enqueue_guidance": enqueue_guidance,
        "consume_pending_guidance": consume_pending_guidance,
        "list_run_guidance": list_run_guidance,
        "append_tool_call_with_parallel_group": append_tool_call_with_parallel_group,
        "append_conversation_broadcast": append_conversation_broadcast,
        "conversation_broadcasts_after": conversation_broadcasts_after,
    }.items():
        setattr(store, name, types.MethodType(func, store))

    setattr(store, _INSTALLED_FLAG, True)
    return store


__all__ = [
    "install_v3_1_extensions",
    "V3_1_MIGRATION_MARKER",
]
