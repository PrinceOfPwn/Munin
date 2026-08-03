# tags: [store, sqlite, indexes, performance, EXPLAIN_QUERY_PLAN, tests]
"""Plan 18 SQLite index regression tests and EXPLAIN QUERY PLAN verification."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from munin.production.store import ProductionStore

INDEXES_TO_DROP = (
    "idx_conversation_participants_user",
    "idx_tool_calls_run",
    "idx_agent_runs_conversation",
    "idx_reasoning_events_run",
    "idx_human_requests_run",
    "idx_subagent_runs_parent",
    "idx_conversation_artifacts_run",
    "idx_conversation_summaries_conv",
)

# Each entry: (name, sql, params, expect_before).
# expect_before == "scan": the query performs a full table scan once the
# _PLAN18 indexes are dropped (verified via EXPLAIN QUERY PLAN).
# expect_before == "index": the query is already served by a pre-existing
# index even without _PLAN18 — agent_runs carries
# UNIQUE(conversation_id, actor_id, idempotency_key), whose leading column
# conversation_id covers this lookup via sqlite_autoindex_agent_runs_2.
TARGET_QUERIES = [
    (
        "list_conversations",
        "SELECT c.id, c.title FROM conversations c JOIN conversation_participants p ON p.conversation_id = c.id WHERE p.user_id = ? AND p.removed_at_ms IS NULL ORDER BY c.last_activity_at_ms DESC",
        ("user_1",),
        "scan",
    ),
    (
        "get_run_detail_reasoning_events",
        "SELECT * FROM reasoning_events WHERE run_id = ?",
        ("run_1",),
        "scan",
    ),
    (
        "get_run_detail_tool_calls",
        "SELECT * FROM tool_calls WHERE run_id = ?",
        ("run_1",),
        "scan",
    ),
    (
        "get_run_detail_subagent_runs",
        "SELECT * FROM subagent_runs WHERE parent_run_id = ?",
        ("run_1",),
        "scan",
    ),
    (
        "get_run_detail_human_requests",
        "SELECT * FROM human_requests WHERE run_id = ?",
        ("run_1",),
        "scan",
    ),
    (
        "get_run_detail_conversation_artifacts",
        "SELECT * FROM conversation_artifacts WHERE run_id = ?",
        ("run_1",),
        "scan",
    ),
    (
        "agent_runs_by_conversation",
        "SELECT * FROM agent_runs WHERE conversation_id = ?",
        ("conv_1",),
        "index",
    ),
    (
        "conversation_summaries_by_conv",
        "SELECT * FROM conversation_summaries WHERE conversation_id = ?",
        ("conv_1",),
        "scan",
    ),
]


def _get_explain_plan(store: ProductionStore, sql: str, params: tuple[Any, ...]) -> list[str]:
    with store._read_only() as conn:
        cursor = conn.execute(f"EXPLAIN QUERY PLAN {sql}", params)
        rows = cursor.fetchall()
        lines = []
        for r in rows:
            if hasattr(r, "keys") and "detail" in r.keys():
                lines.append(str(r["detail"]))
            elif isinstance(r, (tuple, list)):
                lines.append(str(r[-1]))
            else:
                lines.append(str(r[3]) if len(r) > 3 else str(r))
        return lines


def test_plan18_store_indexes_explain_query_plan(tmp_path: Path) -> None:
    store = ProductionStore.for_sqlite(tmp_path / "plan.sqlite", master_key=b"p" * 32)
    fixtures_dir = Path(__file__).parent / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    before_file = fixtures_dir / "explain_query_plan_before.txt"
    after_file = fixtures_dir / "explain_query_plan_after.txt"

    # Step 1: Drop Plan 18 indexes to simulate BEFORE state
    with store._transaction() as conn:
        for idx in INDEXES_TO_DROP:
            conn.execute(f"DROP INDEX IF EXISTS {idx}")

    before_outputs: list[str] = [
        "# Stage: BEFORE (Plan 18 Indexes Dropped - Full Table Scans)",
        "# Spec: munin-pr0 PR-0 performance regression baseline",
        "",
    ]

    for name, sql, params, expect_before in TARGET_QUERIES:
        plan_lines = _get_explain_plan(store, sql, params)
        plan_text = " ".join(plan_lines)
        if expect_before == "scan":
            assert "SCAN" in plan_text.upper(), f"Query {name} before plan missing SCAN: {plan_text}"
        else:
            assert "USING INDEX" in plan_text.upper(), (
                f"Query {name} before plan expected a pre-existing index: {plan_text}"
            )
        before_outputs.append(f"# Query: {name}")
        before_outputs.append(f"# SQL: {sql}")
        before_outputs.extend(plan_lines)
        before_outputs.append("")

    before_file.write_text("\n".join(before_outputs), encoding="utf-8")

    # Step 2: Reinstall Plan 18 indexes to test AFTER state
    store._install_plan18_indexes()

    after_outputs: list[str] = [
        "# Stage: AFTER (Plan 18 Indexes Installed)",
        "# Spec: munin-pr0 PR-0 performance regression benchmark",
        "",
    ]

    for name, sql, params, expect_before in TARGET_QUERIES:
        plan_lines = _get_explain_plan(store, sql, params)
        plan_text = " ".join(plan_lines)
        assert ("USING INDEX" in plan_text.upper() or "COVERING INDEX" in plan_text.upper()), (
            f"Query {name} after plan missing index usage: {plan_text}"
        )
        after_outputs.append(f"# Query: {name}")
        after_outputs.append(f"# SQL: {sql}")
        after_outputs.extend(plan_lines)
        after_outputs.append("")

    after_file.write_text("\n".join(after_outputs), encoding="utf-8")
