"""Characterization tests for MCP-side shared state (9 tables).

Documents current behavior of SharedStateStore:
  - All 9 tables exist and support round-trip insert+select
  - Atomic wake-claim (see also test_subagent_runner_parity.py)
  - SQL edge cases

Tests skip gracefully if munin imports fail.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Guard imports
# ---------------------------------------------------------------------------
shared_mod = pytest.importorskip("munin.mcp.shared_state")
config_mod = pytest.importorskip("munin.mcp.config")

SharedStateStore = shared_mod.SharedStateStore
Settings = config_mod.Settings


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def _make_store(tmp_path: Path) -> SharedStateStore:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        workspace_root=tmp_path,
        default_timeout=30,
        max_output_chars=8000,
        expected_egress_ip="",
        forbidden_egress_ip="",
        route_probe_ip="1.1.1.1",
        job_workers=1,
        github_token="",
        nvd_api_key="",
        munin_data_path=data_dir,
        munin_soul_path=tmp_path / "soul",
    )
    return SharedStateStore(settings)


# ---------------------------------------------------------------------------
# The 9 canonical MCP-side tables
# ---------------------------------------------------------------------------

EXPECTED_TABLES = {
    "shared_intel",
    "active_tasks",
    "agent_presence",
    "agent_messages",
    "episodic",
    "semantic",
    "procedural",
    "generated_graphs",
    "agent_wake_queue",
}


def _get_table_names(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    names = {row[0] for row in cursor.fetchall()}
    conn.close()
    return names


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_nine_tables_exist(tmp_path: Path) -> None:
    """All 9 MCP-side tables are created on SharedStateStore initialization."""
    store = _make_store(tmp_path)
    actual_tables = _get_table_names(store.db_path)

    missing = EXPECTED_TABLES - actual_tables
    assert not missing, f"Missing tables: {missing}"


def test_nine_tables_round_trip(tmp_path: Path) -> None:
    """Each of the 9 MCP tables supports basic insert + select without error."""
    store = _make_store(tmp_path)

    # 1. shared_intel
    store.publish_intel(
        target_ip="10.0.0.1",
        port=445,
        service="smb",
        finding_type="open_port",
        severity="info",
        details_json=json.dumps({"banner": "Samba 4.x"}),
        source_agent="test",
        status="NEW",
        tags="smb,recon",
        fingerprint="smb-10.0.0.1-445",
    )
    intel = store.query_intel(target_ip="10.0.0.1")
    assert len(intel) >= 1

    # 2. active_tasks (via claim_task which inserts into active_tasks)
    task_result = store.claim_task(
        target_ip="10.0.0.2",
        action="port_scan",
        assigned_agent="test_agent",
        lease_seconds=60,
        metadata_json=json.dumps({"priority": 1}),
        allow_steal_stale=False,
    )
    assert task_result.success

    # 3. agent_presence
    store.upsert_presence(
        agent_name="test_agent",
        role="scanner",
        status="RUNNING",
        current_task_id=None,
        metadata_json="{}",
    )
    presence = store.list_presence()
    names = [p["agent_name"] for p in presence]
    assert "test_agent" in names

    # 4. agent_messages
    msg_result = store.post_message(
        sender_agent="test_agent",
        recipient_agent="munin",
        subject="test subject",
        message_type="INFO",
        body="hello world",
        related_task_id=None,
        related_target_ip="",
        metadata_json="{}",
    )
    msg_id = msg_result["message_id"]
    assert isinstance(msg_id, int) and msg_id > 0
    messages = store.fetch_messages(recipient_agent="munin")
    assert len(messages) >= 1

    # 5. episodic
    store.episodic_record(
        agent="test_agent",
        action="test_action",
        input_data={"x": 1},
        output_data={"y": 2},
        tags=["test"],
    )
    episodes = store.episodic_query(agent="test_agent", limit=10)
    assert len(episodes) >= 1

    # 6. semantic
    store.semantic_remember("test_key", {"info": "some fact"})
    val = store.semantic_recall("test_key")
    assert val is not None
    assert val.get("info") == "some fact"

    # 7. procedural (via direct DB — no active script needed for the row)
    conn = sqlite3.connect(str(store.db_path))
    conn.execute(
        """INSERT INTO procedural (name, description, script_path,
           signature_json, tags, active, created_by_agent)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("gen__test_proc", "test", "/tmp/t.py", "{}", "[]", 1, "test"),
    )
    conn.commit()
    rows = conn.execute("SELECT name FROM procedural WHERE name='gen__test_proc'").fetchall()
    conn.close()
    assert len(rows) == 1

    # 8. generated_graphs
    store.graph_register(
        name="test_graph",
        purpose="test graph purpose",
        system_prompt="You are a test subagent.",
        tool_whitelist=["echo_tool"],
        reset_policy="on_reset",
        created_by_agent="test",
    )
    graph = store.graph_get("test_graph")
    assert graph is not None

    # 9. agent_wake_queue
    wake_id = store.enqueue_wake(target_agent="test_agent", task={"action": "test"})
    assert isinstance(wake_id, int) and wake_id > 0
    item = store.claim_wake_item(target_agent="test_agent", claimer_pid=1)
    assert item is not None


def test_connection_proxy_rowcount(tmp_path: Path) -> None:
    """SQLite INSERT into agent_messages returns a valid rowcount.

    This documents that the store's internal connection correctly tracks
    row-level changes (analogous to ConnectionProxy.rowcount contract).
    """
    store = _make_store(tmp_path)
    conn = sqlite3.connect(str(store.db_path))
    cursor = conn.execute(
        """INSERT INTO agent_messages
           (sender_agent, recipient_agent, subject, message_type, body,
            related_task_id, related_target_ip, metadata, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("a", "b", "subj", "INFO", "body", None, "", "{}", "NEW"),
    )
    conn.commit()
    rowcount = cursor.rowcount
    conn.close()
    assert rowcount == 1, f"Expected rowcount=1 after INSERT, got {rowcount}"


def test_comment_safe_splitter(tmp_path: Path) -> None:
    """SQL statements split correctly on semicolons, ignoring -- comments.

    SQLite executescript() handles this natively. This test documents the
    expectation that multi-statement SQL with inline comments does not break.
    """
    db_path = tmp_path / "splitter_test.db"
    conn = sqlite3.connect(str(db_path))

    # executescript should handle -- comments and multiple statements
    conn.executescript("""
        -- This is a comment; it should not be treated as a statement separator
        CREATE TABLE IF NOT EXISTS tbl_a (id INTEGER PRIMARY KEY);
        -- Another comment
        CREATE TABLE IF NOT EXISTS tbl_b (id INTEGER PRIMARY KEY);
        INSERT INTO tbl_a (id) VALUES (1); -- inline comment
    """)

    cursor = conn.execute("SELECT count(*) FROM tbl_a")
    count = cursor.fetchone()[0]
    conn.close()

    assert count == 1, "INSERT should have created one row in tbl_a"


def test_munin_db_url_empty_uses_local_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When MUNIN_DB_URL env var is empty, SharedStateStore uses local SQLite file.

    Documents the fallback behavior: no MUNIN_DB_URL → db_path under munin_data_path.
    """
    monkeypatch.delenv("MUNIN_DB_URL", raising=False)

    data_dir = tmp_path / "local_data"
    data_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings(
        workspace_root=tmp_path,
        default_timeout=30,
        max_output_chars=8000,
        expected_egress_ip="",
        forbidden_egress_ip="",
        route_probe_ip="1.1.1.1",
        job_workers=1,
        github_token="",
        nvd_api_key="",
        munin_data_path=data_dir,
        munin_soul_path=tmp_path / "soul",
    )

    store = SharedStateStore(settings)
    db_path = store.db_path

    assert db_path.is_absolute(), "db_path should be absolute"
    assert str(data_dir) in str(db_path), (
        f"db_path ({db_path}) should be inside munin_data_path ({data_dir})"
    )
    # Verify it's a real SQLite file after init
    assert db_path.exists(), "SQLite DB file should exist after store initialization"


def test_episodic_query_filters_by_agent(tmp_path: Path) -> None:
    """episodic_query filters correctly by agent name."""
    store = _make_store(tmp_path)

    store.episodic_record(agent="agent_x", action="action_1", input_data={}, output_data={}, tags=[])
    store.episodic_record(agent="agent_y", action="action_2", input_data={}, output_data={}, tags=[])
    store.episodic_record(agent="agent_x", action="action_3", input_data={}, output_data={}, tags=[])

    results_x = store.episodic_query(agent="agent_x", limit=100)
    results_y = store.episodic_query(agent="agent_y", limit=100)

    assert all(r["agent"] == "agent_x" for r in results_x), "All results should be for agent_x"
    assert all(r["agent"] == "agent_y" for r in results_y), "All results should be for agent_y"
    assert len(results_x) == 2
    assert len(results_y) == 1


def test_semantic_list_prefix_filter(tmp_path: Path) -> None:
    """semantic_list filters keys by prefix."""
    store = _make_store(tmp_path)

    store.semantic_remember("ns:key_a", "value_a")
    store.semantic_remember("ns:key_b", "value_b")
    store.semantic_remember("other:key_c", "value_c")

    ns_items = store.semantic_list(prefix="ns:")
    other_items = store.semantic_list(prefix="other:")

    ns_keys = [item["key"] for item in ns_items]
    assert "ns:key_a" in ns_keys
    assert "ns:key_b" in ns_keys
    assert "other:key_c" not in ns_keys

    other_keys = [item["key"] for item in other_items]
    assert "other:key_c" in other_keys
    assert "ns:key_a" not in other_keys
