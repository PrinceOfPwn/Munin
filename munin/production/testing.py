"""Strictly scoped Turso fixture helpers.

Every destructive cleanup path receives one validated `e2e_<run>_<suffix>`
identifier and deletes by exact conversation IDs, never by broad table wipes.
"""

from __future__ import annotations

import json
import re
import secrets
from typing import Any

from .store import ProductionStore, _now_ms

_TEST_RUN = re.compile(r"^e2e_[A-Za-z0-9-]{4,96}_[a-f0-9]{8}$")


def new_test_run_id(github_run_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9-]", "", github_run_id)[:96] or "local"
    return f"e2e_{safe}_{secrets.token_hex(4)}"


def create_fixture_conversation(store: ProductionStore, *, actor_id: str, test_run_id: str, title: str = "Production E2E fixture", ttl_seconds: int = 3_600) -> dict[str, Any]:
    if not _TEST_RUN.fullmatch(test_run_id):
        raise ValueError("invalid test run identifier")
    return store.create_conversation(owner_id=actor_id, title=title, tags=["created_by_test", test_run_id], scope={"test_run_id": test_run_id, "created_by_test": True, "created_at_ms": _now_ms(), "expires_at_ms": _now_ms() + max(60, ttl_seconds) * 1000})


def cleanup_test_run(store: ProductionStore, *, test_run_id: str) -> int:
    if not _TEST_RUN.fullmatch(test_run_id):
        raise ValueError("refusing cleanup for an invalid test run identifier")
    with store._transaction() as conn:  # exact rows are selected before every delete
        rows = conn.execute("SELECT id,scope_json FROM conversations WHERE tags_json LIKE ?", (f'%"{test_run_id}"%',)).fetchall()
        conversation_ids = [row["id"] for row in rows if json.loads(row["scope_json"]).get("test_run_id") == test_run_id]
        for conversation_id in conversation_ids:
            runs = conn.execute("SELECT id FROM agent_runs WHERE conversation_id=?", (conversation_id,)).fetchall()
            run_ids = [row["id"] for row in runs]
            messages = conn.execute("SELECT id FROM messages WHERE conversation_id=?", (conversation_id,)).fetchall()
            for run_id in run_ids:
                conn.execute("DELETE FROM reasoning_events WHERE run_id=?", (run_id,))
                conn.execute("DELETE FROM tool_calls WHERE run_id=?", (run_id,))
                conn.execute("DELETE FROM subagent_runs WHERE parent_run_id=?", (run_id,))
                conn.execute("DELETE FROM human_requests WHERE run_id=?", (run_id,))
                conn.execute("DELETE FROM operation_snapshots WHERE run_id=?", (run_id,))
                conn.execute("DELETE FROM operation_branches WHERE parent_run_id=?", (run_id,))
                conn.execute("DELETE FROM run_events WHERE run_id=?", (run_id,))
            for message in messages:
                conn.execute("DELETE FROM message_revisions WHERE message_id=?", (message["id"],))
            conn.execute("DELETE FROM conversation_artifacts WHERE conversation_id=?", (conversation_id,))
            conn.execute("DELETE FROM conversation_summaries WHERE conversation_id=?", (conversation_id,))
            conn.execute("DELETE FROM agent_runs WHERE conversation_id=?", (conversation_id,))
            conn.execute("DELETE FROM messages WHERE conversation_id=?", (conversation_id,))
            conn.execute("DELETE FROM conversation_participants WHERE conversation_id=?", (conversation_id,))
            conn.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
        remaining = conn.execute("SELECT COUNT(*) AS count FROM conversations WHERE tags_json LIKE ?", (f'%"{test_run_id}"%',)).fetchone()
        if int(remaining["count"]):
            raise RuntimeError("test fixture cleanup left residual conversations")
        return len(conversation_ids)


def janitor_expired_test_runs(store: ProductionStore, *, now_ms: int | None = None) -> int:
    now = now_ms or _now_ms()
    conn = store._connect()
    try:
        rows = conn.execute("SELECT scope_json FROM conversations WHERE tags_json LIKE '%\"created_by_test\"%'").fetchall()
        expired = {scope.get("test_run_id") for row in rows if (scope := json.loads(row["scope_json"])).get("created_by_test") and int(scope.get("expires_at_ms", now + 1)) <= now}
    finally:
        conn.close()
    return sum(cleanup_test_run(store, test_run_id=run_id) for run_id in expired if isinstance(run_id, str) and _TEST_RUN.fullmatch(run_id))
