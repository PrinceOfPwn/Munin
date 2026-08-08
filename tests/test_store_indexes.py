# tags: [store, sqlite, indexes, performance, EXPLAIN_QUERY_PLAN, tests]
"""Plan 18 SQLite index regression tests against production query shapes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from munin.production.store import ProductionStore

# Drop both the original single-column Plan 18 indexes and their composite
# replacements so the BEFORE phase cannot accidentally use a stale index from
# an older database migration.
INDEXES_TO_DROP = (
    "idx_conversation_participants_user",
    "idx_tool_calls_run",
    "idx_tool_calls_run_started",
    "idx_agent_runs_conversation",
    "idx_reasoning_events_run",
    "idx_reasoning_events_run_created",
    "idx_human_requests_run",
    "idx_human_requests_run_created",
    "idx_subagent_runs_parent",
    "idx_subagent_runs_parent_started",
    "idx_conversation_artifacts_run",
    "idx_conversation_artifacts_run_created",
    "idx_conversation_summaries_conv",
    "idx_conversation_summaries_run_created",
)

# (name, sql, params, expect_before, ordered_query)
# The ordered queries intentionally mirror the SQL used by the run-detail
# read-model.  A useful index must satisfy both the run filter and ORDER BY;
# merely avoiding a table scan is not enough if SQLite still builds a temp
# B-tree to sort every row in a long-running operation.
TARGET_QUERIES = [
    (
        "list_conversations",
        "SELECT c.id, c.title FROM conversations c JOIN conversation_participants p ON p.conversation_id = c.id WHERE p.user_id = ? AND p.removed_at_ms IS NULL ORDER BY c.last_activity_at_ms DESC",
        ("user_1",),
        "scan",
        False,
    ),
    (
        "get_run_detail_reasoning_events",
        "SELECT id,content,agent_name,created_at_ms FROM reasoning_events WHERE run_id = ? AND kind='operational_summary' ORDER BY created_at_ms,id",
        ("run_1",),
        "scan",
        True,
    ),
    (
        "get_run_detail_tool_calls",
        "SELECT id,tool_name,agent_name,state,started_at_ms,finished_at_ms FROM tool_calls WHERE run_id = ? ORDER BY started_at_ms,id",
        ("run_1",),
        "scan",
        True,
    ),
    (
        "get_run_detail_subagent_runs",
        "SELECT id,profile_id,state,objective,started_at_ms,finished_at_ms FROM subagent_runs WHERE parent_run_id = ? ORDER BY started_at_ms,id",
        ("run_1",),
        "scan",
        True,
    ),
    (
        "get_run_detail_human_requests",
        "SELECT id,action,risk,state,choices_json,response_json,created_at_ms,expires_at_ms,resolved_at_ms FROM human_requests WHERE run_id = ? ORDER BY created_at_ms,id",
        ("run_1",),
        "scan",
        True,
    ),
    (
        "get_run_detail_conversation_artifacts",
        "SELECT id,message_id,filename,media_type,language,renderer,version,provenance,preview_url,download_url,content_hash,size_bytes,created_at_ms FROM conversation_artifacts WHERE run_id = ? ORDER BY created_at_ms,id",
        ("run_1",),
        "scan",
        True,
    ),
    (
        "agent_runs_by_conversation",
        "SELECT * FROM agent_runs WHERE conversation_id = ?",
        ("conv_1",),
        "index",
        False,
    ),
    (
        "conversation_summaries_by_run",
        "SELECT id,model,confidence,source_start_sequence,source_end_sequence,content,supersedes_id,created_at_ms FROM conversation_summaries WHERE run_id = ? ORDER BY created_at_ms,id",
        ("run_1",),
        "scan",
        True,
    ),
]


def _get_explain_plan(store: ProductionStore, sql: str, params: tuple[Any, ...]) -> list[str]:
    with store._read_only() as conn:
        cursor = conn.execute(f"EXPLAIN QUERY PLAN {sql}", params)
        rows = cursor.fetchall()
        lines = []
        for row in rows:
            if hasattr(row, "keys") and "detail" in row.keys():
                lines.append(str(row["detail"]))
            elif isinstance(row, (tuple, list)):
                lines.append(str(row[-1]))
            else:
                lines.append(str(row[3]) if len(row) > 3 else str(row))
        return lines


def test_plan18_store_indexes_explain_query_plan(tmp_path: Path) -> None:
    """Plan 18 indexes cover the actual filtering *and* ordering contract.

    Diagnostic plans remain in-memory.  Tests must never create or overwrite
    tracked files under ``tests/fixtures``; that made CI worktrees dirty and
    broke read-only source-tree execution.
    """
    store = ProductionStore.for_sqlite(tmp_path / "plan.sqlite", master_key=b"p" * 32)

    with store._transaction() as conn:
        for index_name in INDEXES_TO_DROP:
            conn.execute(f"DROP INDEX IF EXISTS {index_name}")

    for name, sql, params, expect_before, _ordered in TARGET_QUERIES:
        plan_text = " ".join(_get_explain_plan(store, sql, params)).upper()
        if expect_before == "scan":
            assert "SCAN" in plan_text, f"Query {name} before plan missing SCAN: {plan_text}"
        else:
            assert "USING INDEX" in plan_text or "COVERING INDEX" in plan_text, (
                f"Query {name} before plan expected a pre-existing index: {plan_text}"
            )

    store._install_plan18_indexes()

    for name, sql, params, _expect_before, ordered_query in TARGET_QUERIES:
        plan_text = " ".join(_get_explain_plan(store, sql, params)).upper()
        assert "USING INDEX" in plan_text or "COVERING INDEX" in plan_text, (
            f"Query {name} after plan missing index usage: {plan_text}"
        )
        if ordered_query:
            assert "USE TEMP B-TREE FOR ORDER BY" not in plan_text, (
                f"Query {name} still sorts outside the index: {plan_text}"
            )
