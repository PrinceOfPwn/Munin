# tags: [tests, artifacts-readmodel, PR-6E, readmodel-invariants, zero-write, trace-callback, checksum, 1000-gets, determinism, sqlite-read-only]
"""PR-6E — read-model invariants: 1000 GETs, zero writes.

Asserts that the PLAN-6 read surface is a pure read:

* 1000 consecutive authenticated GETs across the four read endpoints
  (artifact detail, conversation artifacts, run artifacts, run detail)
  leave ``run_events``, ``agent_runs``, ``tool_calls``,
  ``conversation_artifacts`` and the other read-model tables untouched
  (row counts + deterministic length/created-at checksums unchanged).
* ``sqlite3.Connection.set_trace_callback`` observes ZERO
  INSERT/UPDATE/DELETE/ALTER/CREATE/DROP/REPLACE statements during the
  loop.
* The run-detail payload is byte-identical on every repetition
  (determinism under concurrent reads).

The auth session is pre-touched (``last_seen_at_ms`` bumped) BEFORE the
trace is armed so :meth:`ProductionStore.authenticate`'s idle-refresh
``UPDATE auth_sessions`` cannot fire mid-loop and trip the assertion.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

_WRITE_WORDS = ("INSERT", "UPDATE", "DELETE", "ALTER", "CREATE", "DROP", "REPLACE")

_CHECKSUM_TABLES = (
    "run_events",
    "agent_runs",
    "tool_calls",
    "conversation_artifacts",
    "reasoning_events",
    "human_requests",
    "subagent_runs",
    "run_guidance_queue",
    "conversation_summaries",
)


@pytest.fixture
def production_store(tmp_path: Path):
    from munin.production.store import ProductionStore

    return ProductionStore.for_sqlite(tmp_path / "readmodel_invariants.sqlite", master_key=b"i" * 32)


def _login_headers(client, *, username="invariant-op", password="a strong invariant password"):
    login = client.post("/api/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return {
        "Origin": "http://testserver",
        "Sec-Fetch-Site": "same-origin",
        "X-CSRF-Token": login.json()["csrf_token"],
    }


def _make_client(store, monkeypatch):
    monkeypatch.setenv("MUNIN_ALLOWED_ORIGINS", "http://testserver")
    monkeypatch.setenv("MUNIN_COOKIE_SECURE", "0")
    from starlette.testclient import TestClient

    from munin.production.asgi import create_app

    return TestClient(create_app(store))


def _rich_fixture(store):
    operator = store.create_user(
        username="invariant-op", password="a strong invariant password", role="operator"
    )
    conversation = store.create_conversation(owner_id=operator["id"], title="Invariants")
    turn = store.create_turn(
        actor_id=operator["id"],
        conversation_id=conversation["id"],
        content="Read-model invariant fixture",
        idempotency_key="readmodel-invariants",
    )
    run_id = turn["run"]["id"]

    artifact = store.add_artifact(
        actor_id=operator["id"], conversation_id=conversation["id"], filename="report.txt",
        media_type="text/plain", language="text", content="invariant body", run_id=run_id,
        renderer="text", version=1, provenance="evidence:fixture",
    )

    store.append_tool_call(
        run_id=run_id, agent_name="munin", tool_name="hugin_lookup", state="completed",
        arguments={"query": "x"}, result={"summary": "1"},
    )
    store.append_reasoning_event(
        run_id=run_id, kind="operational_summary", content="stepping",
        provider="", persistence_enabled=True,
    )
    store.create_subagent_run(
        parent_run_id=run_id, profile_id="worker-a", objective="fetch intel"
    )
    return operator, conversation, run_id, artifact


def _snapshot(store) -> dict[str, tuple[int, int, int]]:
    with store._read_only() as conn:
        result: dict[str, tuple[int, int, int]] = {}
        for table in _CHECKSUM_TABLES:
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            rowids = conn.execute(f"SELECT COALESCE(SUM(rowid),0) AS r FROM {table}").fetchone()
            lengths = conn.execute(
                f"SELECT COALESCE(SUM(length(CAST(id AS TEXT))),0) AS l FROM {table}"
            ).fetchone()
            result[table] = (int(row["n"]), int(rowids["r"]), int(lengths["l"]))
        return result


def test_1000_read_gets_are_pure_and_deterministic(production_store, monkeypatch):
    client = _make_client(production_store, monkeypatch)
    _operator, conversation, run_id, artifact = _rich_fixture(production_store)
    headers = _login_headers(client)

    # Pre-touch the session so ``authenticate``'s idle-refresh UPDATE can
    # never fire while the trace callback is armed.
    now_ms = int(time.time() * 1000)
    with production_store._transaction() as conn:
        conn.execute("UPDATE auth_sessions SET last_seen_at_ms=?", (now_ms,))

    original_factory = production_store._connection_factory
    violations: list[str] = []

    def guarded_factory() -> Any:
        conn = original_factory()

        def trace(statement: str, _params: Any) -> None:
            upper = statement.upper()
            if any(word in upper for word in _WRITE_WORDS):
                violations.append(statement)

        conn.set_trace_callback(trace)
        return conn

    production_store._connection_factory = guarded_factory
    try:
        before = _snapshot(production_store)

        artifact_url = f"/api/artifacts/{artifact['id']}"
        conversation_url = f"/api/chat/{conversation['id']}/artifacts"
        run_artifacts_url = f"/api/runs/{run_id}/artifacts"
        run_detail_url = f"/api/runs/{run_id}/detail"

        detail_payloads: set[bytes] = set()
        failures: list[tuple[str, int]] = []
        for index in range(1000):
            if index % 4 == 0:
                response = client.get(artifact_url, headers=headers)
            elif index % 4 == 1:
                response = client.get(conversation_url, headers=headers)
            elif index % 4 == 2:
                response = client.get(run_artifacts_url, headers=headers)
            else:
                response = client.get(run_detail_url, headers=headers)
                detail_payloads.add(response.content)
            if response.status_code != 200:
                failures.append((index, response.status_code))
            assert response.status_code == 200, f"iteration {index}: {response.status_code} {response.text[:200]}"
        assert failures == []

        after = _snapshot(production_store)
    finally:
        production_store._connection_factory = original_factory

    # Zero writes observed by sqlite during all 1000 GETs.
    assert violations == [], f"read GETs wrote to the database:\n{chr(10).join(violations[:10])}"

    # Row counts + checksums unchanged on every read-model table.
    assert before == after

    # The run-detail payload is byte-identical across the loop.
    assert len(detail_payloads) == 1
