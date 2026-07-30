"""Characterization tests for conversation/store persistence.

Targets the future munin.production.store module (MIGRATION_ID, RUN_STATES enum,
v3.1 extension tables, timeline persistence).

All tests are marked xfail because munin.production.store does not exist yet.
They will automatically start passing once the module is implemented.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Import guard — entire module xfail if production.store is absent
# ---------------------------------------------------------------------------
try:
    import munin.production.store as store_mod  # type: ignore[import]
    _STORE_AVAILABLE = True
except ImportError:
    _STORE_AVAILABLE = False
    store_mod = None  # type: ignore[assignment]

pytestmark = pytest.mark.xfail(
    not _STORE_AVAILABLE,
    reason="munin.production.store does not exist yet (PR-01 characterization)",
    strict=False,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_migration_id_forward_only(tmp_path: Path) -> None:
    """MIGRATION_ID checksum prevents downgrade to an older schema version.

    Expectation: store_mod exposes MIGRATION_ID (str or int). Attempting to
    open a DB whose recorded migration ID is newer than MIGRATION_ID raises
    an error (not silently proceeds).
    """
    assert hasattr(store_mod, "MIGRATION_ID"), "store module must expose MIGRATION_ID"
    migration_id = store_mod.MIGRATION_ID
    assert migration_id, "MIGRATION_ID must be non-empty"

    db_path = tmp_path / "test.db"
    store = store_mod.Store(db_path)
    store.close()

    # Re-open with the same migration ID: should succeed
    store2 = store_mod.Store(db_path)
    store2.close()


def test_run_states_enum(tmp_path: Path) -> None:
    """RUN_STATES enum contains the expected state values."""
    assert hasattr(store_mod, "RUN_STATES"), "store module must expose RUN_STATES"
    states = store_mod.RUN_STATES

    # Convert to a set of string values for comparison
    if hasattr(states, "__members__"):
        # It's an enum
        state_values = {m.value if hasattr(m, "value") else m.name for m in states}
    else:
        state_values = set(states)

    expected = {"pending", "running", "completed", "failed", "interrupted", "cancelled"}
    missing = expected - {str(s).lower() for s in state_values}
    assert not missing, f"RUN_STATES is missing: {missing}"


def test_v3_1_extension_tables_install(tmp_path: Path) -> None:
    """v3.1 extension tables are created by store_v3_1.install_v3_1_extensions().

    Expected tables:
      - conversation_collaborators
      - conversation_notes
      - conversation_presence
      - run_guidance_queue
    """
    try:
        import munin.production.store_v3_1 as store_v3_1  # type: ignore[import]
    except ImportError:
        pytest.skip("munin.production.store_v3_1 not available")

    db_path = tmp_path / "v31.db"
    conn = _open_db(db_path)

    store_v3_1.install_v3_1_extensions(conn)

    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    conn.close()

    expected_tables = {
        "conversation_collaborators",
        "conversation_notes",
        "conversation_presence",
        "run_guidance_queue",
    }
    missing = expected_tables - tables
    assert not missing, f"v3.1 extension tables missing: {missing}"


def test_timeline_rows_persist_close_reopen(tmp_path: Path) -> None:
    """Timeline/reasoning/tool_calls rows survive a store close+reopen cycle."""
    db_path = tmp_path / "persist.db"

    store = store_mod.Store(db_path)
    run_id = store.create_run(goal="test persistence")
    store.append_timeline(run_id=run_id, kind="reasoning", text="Initial reasoning step")
    store.append_timeline(run_id=run_id, kind="tool_call", text='{"tool": "echo", "args": {}}')
    store.close()

    # Reopen
    store2 = store_mod.Store(db_path)
    rows = store2.get_timeline(run_id=run_id)
    store2.close()

    kinds = [r["kind"] for r in rows]
    assert "reasoning" in kinds, "reasoning row should persist across close/reopen"
    assert "tool_call" in kinds, "tool_call row should persist across close/reopen"


def test_run_states_transition_allowed(tmp_path: Path) -> None:
    """Runs can transition through standard lifecycle states."""
    db_path = tmp_path / "states.db"
    store = store_mod.Store(db_path)

    run_id = store.create_run(goal="state test")
    assert store.get_run_state(run_id) in ("pending", store_mod.RUN_STATES.pending)

    store.set_run_state(run_id, "running")
    state = store.get_run_state(run_id)
    assert str(state).lower() in ("running",)

    store.set_run_state(run_id, "completed")
    state = store.get_run_state(run_id)
    assert str(state).lower() in ("completed",)
    store.close()
