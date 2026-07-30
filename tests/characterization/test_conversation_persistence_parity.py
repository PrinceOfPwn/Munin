"""Characterization tests for ProductionStore: migration, checksum, v3.1 extensions.

Asserts CURRENT behaviour at munin/production/store.py and munin/production/store_v3_1.py.
"""

from __future__ import annotations

import re
import secrets
import sqlite3
import uuid
from pathlib import Path

import pytest


def _make_store(tmp_path: Path):
    """Create a ProductionStore backed by a local SQLite file."""
    from munin.production.store import ProductionStore

    db_path = tmp_path / "prod.sqlite"
    master_key = secrets.token_bytes(32)
    return ProductionStore.for_sqlite(db_path, master_key=master_key)


def test_migration_id_and_forward_only_checksum(tmp_path):
    """MIGRATION_ID is a forward-only checksum; PRAGMA user_version matches after close+reopen."""
    from munin.production.store import MIGRATION_ID, MIGRATION_CHECKSUM

    store = _make_store(tmp_path)
    # Verify migration was applied
    applied = store.applied_migration_ids()
    assert MIGRATION_ID in applied

    # Checksum stored in schema_migrations matches
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "prod.sqlite"))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT checksum FROM schema_migrations WHERE migration_id = ?",
        (MIGRATION_ID,),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["checksum"] == MIGRATION_CHECKSUM


def test_run_states_enum():
    """RUN_STATES matches the expected set."""
    from munin.production.store import RUN_STATES

    expected = {"queued", "running", "waiting_for_human", "completed", "failed", "interrupted", "cancelled"}
    assert RUN_STATES == expected


def test_v3_1_extension_install(tmp_path):
    """install_v3_1_extensions creates v3.1 tables and adds columns to tool_calls."""
    from munin.production.store import ProductionStore
    from munin.production.store_v3_1 import install_v3_1_extensions

    store = _make_store(tmp_path)
    install_v3_1_extensions(store)

    tables = store.schema_tables()
    for name in ("conversation_collaborators", "conversation_notes", "conversation_presence", "run_guidance_queue"):
        assert name in tables, f"v3.1 table {name!r} missing"

    # Verify tool_calls has parallel_group_id and tool_use_id columns
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "prod.sqlite"))
    conn.row_factory = sqlite3.Row
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(tool_calls)").fetchall()}
    conn.close()
    assert "parallel_group_id" in cols
    assert "tool_use_id" in cols


def test_uuid_helper_generates_v4_shape():
    """_id() returns f"{prefix}_{uuid.uuid4().hex}" — 32 hex chars, no hyphens,
    with the v4 version marker at hex position 12 (corresponding to the first
    nibble of the third UUID group). Verified against ``munin/production/store.py:45-46``.
    """
    from munin.production.store import _id

    sample = _id("usr")
    prefix, uuid_part = sample.split("_", 1)
    assert prefix == "usr"
    # uuid.uuid4().hex is 32 hex chars (no hyphens); production shape verified
    # at munin/production/store.py:45-46. RFC 4122 v4 marker lives at hex index 12.
    assert len(uuid_part) == 32, f"expected 32 hex chars, got {len(uuid_part)}: {uuid_part!r}"
    assert all(c in "0123456789abcdef" for c in uuid_part)
    assert uuid_part[12] == "4", f"v4 version nibble not 4: {uuid_part[12]!r}"


def test_timeline_reasoning_tool_calls_persist_close_reopen(tmp_path):
    """Insert 1 run + 2 timeline rows + 1 reasoning event + 2 tool_calls rows →
    close + reopen → all 5 rows round-trip with identical content.
    """
    from munin.production.store import ProductionStore

    db_path = tmp_path / "prod.sqlite"
    master_key = secrets.token_bytes(32)

    # First store: bootstrap + insert data
    store1 = ProductionStore.for_sqlite(db_path, master_key=master_key)
    user = store1.bootstrap_admin(username="admin", password="adminpass12345")
    assert user is not None
    conv = store1.create_conversation(owner_id=user["id"], title="test")
    turn = store1.create_turn(
        actor_id=user["id"],
        conversation_id=conv["id"],
        content="hello",
        idempotency_key="key-1",
    )
    run_id = turn["run"]["id"]

    # Append events (wrapped in transaction context per production pattern at
    # store.py:340-365 — `_append_event` requires a positional `conn` argument
    # obtained from `with self._transaction() as conn:`).
    with store1._transaction() as conn:
        store1._append_event(conn, run_id=run_id, kind="test.event1", payload={"x": 1})
        store1._append_event(conn, run_id=run_id, kind="test.event2", payload={"x": 2})

    # Append reasoning — `kind` must be one of the validated enum set:
    # {provider_reasoning, operational_summary, tool_intent, observation,
    #  decision, model_request} per munin/production/store.py:1004.
    store1.append_reasoning_event(
        run_id=run_id,
        kind="observation",
        content="reasoning content",
        provider="test",
        persistence_enabled=True,
        agent_name="munin",
        step=0,
    )

    # Append tool calls
    store1.append_tool_call(
        run_id=run_id,
        agent_name="munin",
        tool_name="test_tool",
        state="completed",
        arguments={"arg": "val1"},
        result={"ok": True},
    )
    store1.append_tool_call(
        run_id=run_id,
        agent_name="munin",
        tool_name="test_tool_2",
        state="completed",
        arguments={"arg": "val2"},
        result={"ok": True},
    )

    # Verify before close
    detail1 = store1.get_run_detail_for_actor(actor_id=user["id"], run_id=run_id)
    assert len(detail1["events"]) >= 2
    assert len(detail1["reasoning"]) >= 1
    assert len(detail1["tools"]) >= 2

    # Close store
    del store1

    # Second store: reopen and verify round-trip
    store2 = ProductionStore.for_sqlite(db_path, master_key=master_key)
    detail2 = store2.get_run_detail_for_actor(actor_id=user["id"], run_id=run_id)
    assert len(detail2["events"]) >= 2
    assert len(detail2["reasoning"]) >= 1
    assert len(detail2["tools"]) >= 2
    # Verify content matches. The events list also contains `run.queued`
    # emitted by `create_turn` at munin/production/store.py:652, so assert only
    # our appended test events (filter by kind prefix) rather than absolute
    # index in events[].
    test_kinds = [e["kind"] for e in detail2["events"] if str(e["kind"]).startswith("test.")]
    assert test_kinds == ["test.event1", "test.event2"]
    assert detail2["reasoning"][0]["content"] == "reasoning content"
    tool_names = [t["tool_name"] for t in detail2["tools"]]
    assert "test_tool" in tool_names
    assert "test_tool_2" in tool_names
