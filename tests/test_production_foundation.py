# ruff: noqa: S106 -- inert credentials/tokens used only by local fixtures.

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def production_store(tmp_path):
    from munin.production.store import ProductionStore

    return ProductionStore.for_sqlite(tmp_path / "production.sqlite", master_key=b"m" * 32)


def test_migrations_create_the_production_aggregate_schema(production_store):
    expected = {
        "users",
        "auth_sessions",
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
    }
    assert expected <= production_store.schema_tables()
    assert production_store.applied_migration_ids() == ["20260729_001_production_foundation"]


def test_namespaced_remote_store_does_not_collide_with_legacy_mcp_tables(tmp_path: Path):
    from munin.production.store import ProductionStore, _NamespacedConnection

    path = tmp_path / "shared-turso-shape.sqlite"

    def connect():
        connection = sqlite3.connect(path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, legacy_value TEXT)")
        return _NamespacedConnection(connection)

    store = ProductionStore(connect, master_key=b"n" * 32)
    assert "production_conversations" in store.schema_tables()
    operator = store.create_user(username="namespaced", password="strong passphrase", role="operator")
    conversation = store.create_conversation(owner_id=operator["id"], title="Parallel durable state")
    assert conversation["id"]


def test_bootstrap_login_and_rotating_session_are_server_side(production_store):
    admin = production_store.bootstrap_admin(username="raven", password="correct horse battery staple")
    assert admin["role"] == "admin"
    assert production_store.bootstrap_admin(username="second", password="another password") is None

    session = production_store.login(
        username="raven",
        password="correct horse battery staple",
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    assert session["token"]
    assert "token" not in production_store.session_record(session["session_id"])
    actor = production_store.authenticate(session["token"])
    assert actor["id"] == admin["id"]
    rotated = production_store.rotate_session(session["token"])
    assert production_store.authenticate(session["token"]) is None
    assert production_store.authenticate(rotated["token"])["id"] == admin["id"]


def test_turn_is_atomic_idempotent_and_rejects_reused_key_with_new_body(production_store):
    operator = production_store.create_user(username="operator", password="strong passphrase", role="operator")
    conversation = production_store.create_conversation(owner_id=operator["id"], title="AD investigation")

    first = production_store.create_turn(
        actor_id=operator["id"],
        conversation_id=conversation["id"],
        content="Enumerate the domain controllers",
        idempotency_key="turn-001",
    )
    retry = production_store.create_turn(
        actor_id=operator["id"],
        conversation_id=conversation["id"],
        content="Enumerate the domain controllers",
        idempotency_key="turn-001",
    )
    assert retry["idempotent_replay"] is True
    assert retry["run"]["id"] == first["run"]["id"]
    aggregate = production_store.get_conversation(actor_id=operator["id"], conversation_id=conversation["id"])
    assert [message["kind"] for message in aggregate["messages"]] == ["user", "assistant_placeholder"]
    assert aggregate["messages"][1]["status"] == "queued"

    with pytest.raises(ValueError, match="idempotency key"):
        production_store.create_turn(
            actor_id=operator["id"],
            conversation_id=conversation["id"],
            content="Different body",
            idempotency_key="turn-001",
        )


def test_run_claim_is_direct_exclusive_and_lease_expiry_recovers(production_store):
    from munin.production.chat import _claim_direct

    operator = production_store.create_user(username="operator", password="strong passphrase", role="operator")
    conversation = production_store.create_conversation(owner_id=operator["id"], title="Web evidence")
    turn = production_store.create_turn(
        actor_id=operator["id"], conversation_id=conversation["id"], content="Collect evidence", idempotency_key="turn-002"
    )
    run_id = turn["run"]["id"]
    lease_token, _assistant_message_id = _claim_direct(production_store, run_id=run_id)
    assert production_store.get_run(run_id)["state"] == "running"
    with pytest.raises(RuntimeError, match="not queued"):
        _claim_direct(production_store, run_id=run_id)
    assert production_store.complete_run(
        run_id=run_id,
        lease_token="wrong-token",
        content="late result",
        outcome="completed",
    ) is False

    production_store.force_run_lease_expiry(run_id, datetime.now(UTC) - timedelta(seconds=1))
    assert production_store.recover_expired_runs() == [run_id]
    recovered = production_store.get_run(run_id)
    assert recovered["state"] == "interrupted"
    events = production_store.list_run_events(run_id)
    assert [event["kind"] for event in events] == ["run.queued", "run.claimed", "run.interrupted"]


def test_reasoning_redaction_envelope_profiles_and_recorded_replay(production_store):
    operator = production_store.create_user(username="operator", password="strong passphrase", role="operator")
    conversation = production_store.create_conversation(owner_id=operator["id"], title="Replay")
    turn = production_store.create_turn(
        actor_id=operator["id"], conversation_id=conversation["id"], content="Inspect artifact", idempotency_key="turn-003"
    )
    profile = production_store.save_provider_profile(
        actor_id=operator["id"],
        label="Groq summary",
        provider="groq",
        base_url="https://api.groq.com/openai/v1",
        model="small-summary",
        uses=["summaries"],
        plaintext_key="gsk_abcdefghijklmnopqrstuvwxyz1234567890",
    )
    assert "gsk_" not in str(profile)
    assert production_store.reveal_provider_key(actor_id=operator["id"], profile_id=profile["id"]).startswith("gsk_")

    event = production_store.append_reasoning_event(
        run_id=turn["run"]["id"],
        kind="provider_reasoning",
        content="Authorization: Bearer super-secret-token; test hypothesis",
        provider="groq",
        persistence_enabled=True,
    )
    assert "super-secret-token" not in event["content"]
    snapshot = production_store.create_snapshot(run_id=turn["run"]["id"], event_id=event["event_id"])
    replay = production_store.recorded_replay(run_id=turn["run"]["id"], snapshot_id=snapshot["id"])
    assert replay["mode"] == "recorded"
    assert replay["egress_enabled"] is False


def test_human_gate_tools_subagents_retry_and_recorded_branch(production_store):
    from munin.production.chat import _claim_direct

    operator = production_store.create_user(username="operator", password="strong passphrase", role="operator")
    conversation = production_store.create_conversation(owner_id=operator["id"], title="Durable operation")
    turn = production_store.create_turn(actor_id=operator["id"], conversation_id=conversation["id"], content="Validate only approved evidence", idempotency_key="turn-004")
    run_id = turn["run"]["id"]
    tool = production_store.append_tool_call(run_id=run_id, agent_name="Recon Coordinator", tool_name="scope_check", state="completed", arguments={"target": "approved"}, result={"allowed": True})
    assert tool["state"] == "completed"
    assert production_store.list_conversations(actor_id=operator["id"], query="scope_check")["conversations"][0]["id"] == conversation["id"]
    subagent = production_store.create_subagent_run(parent_run_id=run_id, profile_id="web-specialist", objective="Collect passive evidence")
    assert subagent["state"] == "queued"
    gate = production_store.request_human_decision(run_id=run_id, action="approve active validation", risk="high", evidence=["scope confirmed"], scope={"asset": "approved"}, choices=["Approve once", "Reject"])
    resolved = production_store.resolve_human_decision(actor_id=operator["id"], request_id=gate["id"], choice="Approve once", nonce=gate["nonce"], guidance="No destructive actions")
    assert resolved["state"] == "queued"
    lease_token, _ = _claim_direct(production_store, run_id=run_id)
    assert production_store.complete_run(run_id=run_id, lease_token=lease_token, content="Evidence recorded\n```python\nprint('approved')\n```", outcome="completed")
    artifact = production_store.get_run_detail_for_actor(actor_id=operator["id"], run_id=run_id)["artifacts"][0]
    assert production_store.get_artifact(actor_id=operator["id"], artifact_id=artifact["id"])["language"] == "python"
    retry = production_store.retry_run(actor_id=operator["id"], run_id=run_id)
    assert retry["attempt"] == 2
    events = production_store.list_run_events(run_id)
    branch = production_store.create_operation_branch(actor_id=operator["id"], parent_run_id=run_id, fork_event_id=events[0]["id"], hypothesis="What if only passive data is used?")
    comparison = production_store.compare_operation_branch(actor_id=operator["id"], branch_id=branch["id"])
    assert comparison["branch"]["replay_mode"] == "recorded"
    assert comparison["diff"]["tool_egress"] == "disabled"


def test_fixture_user_can_be_created_and_deleted_by_test(production_store):
    operator = production_store.create_user(username="llm_smoke_ci", password="fixture password v1", role="operator")
    assert production_store.authenticate(production_store.login(username="llm_smoke_ci", password="fixture password v1", ip_address="127.0.0.1", user_agent="pytest")["token"])["id"] == operator["id"]
    assert production_store.delete_user_for_test(username="llm_smoke_ci") is True
    with pytest.raises(PermissionError):
        production_store.login(username="llm_smoke_ci", password="fixture password v1", ip_address="127.0.0.1", user_agent="pytest")
    assert production_store.delete_user_for_test(username="llm_smoke_ci") is False
    with pytest.raises(ValueError, match="non-fixture"):
        production_store.delete_user_for_test(username="admin")


def test_job_manager_exposes_a_shutdown_lifecycle():
    from munin.mcp.jobs import JobManager

    manager = JobManager(workers=1)
    manager.shutdown()
    assert manager.is_shutdown is True


def test_test_namespace_cleanup_only_removes_its_exact_fixture(production_store):
    from munin.production.testing import cleanup_test_run, create_fixture_conversation, new_test_run_id

    operator = production_store.create_user(username="operator", password="strong passphrase", role="operator")
    first_run = new_test_run_id("local-123")
    second_run = new_test_run_id("local-123")
    first = create_fixture_conversation(production_store, actor_id=operator["id"], test_run_id=first_run)
    second = create_fixture_conversation(production_store, actor_id=operator["id"], test_run_id=second_run)
    assert cleanup_test_run(production_store, test_run_id=first_run) == 1
    with pytest.raises(PermissionError):
        production_store.get_conversation(actor_id=operator["id"], conversation_id=first["id"])
    assert production_store.get_conversation(actor_id=operator["id"], conversation_id=second["id"])["conversation"]["id"] == second["id"]


def test_asgi_login_uses_cookie_session_and_csrf_for_turns(production_store, monkeypatch):
    import json as _json

    from starlette.testclient import TestClient

    from munin.production.asgi import create_app

    monkeypatch.setenv("MUNIN_ALLOWED_ORIGINS", "http://testserver")
    monkeypatch.setenv("MUNIN_COOKIE_SECURE", "0")

    async def fake_chat_stream(request, **kwargs):  # noqa: ARG001 - stand-in for the real LLM run
        yield b": munin-chat-stream v1\n\n"
        envelope = _json.dumps(
            {"kind": "run_state", "run_id": kwargs["run_id"], "state": "completed", "content": "fake stream"}
        )
        yield f"id: 1\nevent: run-event\ndata: {envelope}\n\n".encode()
        yield b"event: close\ndata: {}\n\n"

    monkeypatch.setattr("munin.production.chat._stream_chat", fake_chat_stream)
    client = TestClient(create_app(production_store))
    assert client.post("/api/auth/bootstrap", json={"username": "admin", "password": "very secure test password"}).status_code == 201
    login = client.post("/api/auth/login", json={"username": "admin", "password": "very secure test password"})
    csrf = login.json()["csrf_token"]
    restored = client.get("/api/auth/session")
    assert restored.status_code == 200
    assert restored.json()["csrf_token"] != csrf
    csrf = restored.json()["csrf_token"]
    headers = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin", "X-CSRF-Token": csrf}
    created = client.post("/api/conversations", headers=headers, json={"title": "Durable API"})
    assert created.status_code == 201
    conversation_id = created.json()["data"]["id"]
    turn = client.post(
        "/api/chat",
        headers={**headers, "Idempotency-Key": "api-turn-1"},
        json={"conversation_id": conversation_id, "content": "Persist before work"},
    )
    assert turn.status_code == 200
    assert turn.headers["content-type"].startswith("text/event-stream")
    run_id = turn.headers["x-munin-run-id"]
    assert production_store.get_run(run_id)["state"] == "running"
    assert client.post(
        "/api/chat",
        headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
        json={"conversation_id": conversation_id, "content": "no csrf"},
    ).status_code == 403
