# tags: [tests, cancel-fence, run.cancelling, request_cancel_fence, reject_human_requests_for_run, observe_cancel_fence, cancel-endpoint, PR-2A]
"""PR-2A — durable run cancellation endpoint and fence marker contracts.

Covers:

* ``POST /api/chat/{run_id}/cancel`` returns 202 for queued/running/
  ``waiting_for_human`` runs, 200 for already-terminal runs (without touching
  the fence marker), and 404 for an unknown run.
* :meth:`ProductionStore.request_cancel_fence` sets
  ``cancel_requested_at_ms`` and emits a durable ``run.cancelling`` event
  without performing a terminal transition.
* Pending HITL requests for the run are atomically rejected with a
  ``human_request.resolved`` event.
* :func:`munin.production.runs.observe_cancel_fence` returns True only for a
  non-terminal run whose fence marker is set.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def production_store(tmp_path: Path):
    from munin.production.store import ProductionStore

    return ProductionStore.for_sqlite(tmp_path / "cancel.sqlite", master_key=b"c" * 32)


def _make_actor(store):
    return store.create_user(
        username="cancel-op", password="a strong cancel password", role="operator"
    )


def _make_conversation(store, *, owner_id):
    return store.create_conversation(owner_id=owner_id, title="Cancel contract")


def _queued_run(store, *, actor_id, conversation_id, key="cancel-key"):
    return store.create_turn(
        actor_id=actor_id,
        conversation_id=conversation_id,
        content="Run that we will cancel",
        idempotency_key=key,
    )


def _claim_running(store, *, run_id):
    from munin.production import chat

    return chat._claim_direct(store, run_id=run_id)


def _waiting_for_human(store, *, actor_id, run_id, conversation_id):
    gate = store.request_human_decision(
        run_id=run_id,
        action="authorize destructive op",
        risk="high",
        evidence=["scope confirmed"],
        scope={"target": "approved"},
        choices=["approve", "deny"],
    )
    # Move the run into ``waiting_for_human`` the way the executor does: the
    # supervisor emits a human_request and the run state flips via the store.
    # ``request_human_decision`` itself leaves the run ``running``; the
    # lifecycle transition is owned by the executor.  For this test we model
    # the same durable transition by setting state directly while preserving
    # the lease so a later fence check observes a non-terminal state.
    with store._transaction() as conn:  # noqa: SLF001
        conn.execute(
            "UPDATE agent_runs SET state='waiting_for_human',"
            "state_version=state_version+1 WHERE id=?",
            (run_id,),
        )
    return gate


def test_cancel_endpoint_returns_202_for_queued_run_and_sets_fence(production_store, monkeypatch):
    from starlette.testclient import TestClient

    from munin.production.asgi import create_app

    monkeypatch.setenv("MUNIN_ALLOWED_ORIGINS", "http://testserver")
    monkeypatch.setenv("MUNIN_COOKIE_SECURE", "0")
    client = TestClient(create_app(production_store))

    operator = _make_actor(production_store)
    conversation = _make_conversation(production_store, owner_id=operator["id"])
    turn = _queued_run(
        production_store, actor_id=operator["id"], conversation_id=conversation["id"]
    )
    run_id = turn["run"]["id"]

    login = client.post(
        "/api/auth/login",
        json={"username": "cancel-op", "password": "a strong cancel password"},
    )
    csrf = login.json()["csrf_token"]
    headers = {
        "Origin": "http://testserver",
        "Sec-Fetch-Site": "same-origin",
        "X-CSRF-Token": csrf,
    }

    response = client.post(f"/api/chat/{run_id}/cancel", headers=headers, json={})
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "cancelling"
    assert body["run_id"] == run_id
    assert isinstance(body["requested_at_ms"], int) and body["requested_at_ms"] > 0

    # The run stays non-terminal (queued) — the executor performs the
    # terminal transition at its next step.
    run = production_store.get_run(run_id)
    assert run["state"] == "queued"

    # The fence marker is persisted.
    with production_store._read_only() as conn:  # noqa: SLF001
        marker = conn.execute(
            "SELECT cancel_requested_at_ms FROM agent_runs WHERE id=?",
            (run_id,),
        ).fetchone()
    assert marker["cancel_requested_at_ms"] == body["requested_at_ms"]

    # A durable ``run.cancelling`` event is in the log.
    kinds = [event["kind"] for event in production_store.list_run_events(run_id)]
    assert "run.cancelling" in kinds


def test_cancel_endpoint_returns_202_for_running_run_and_rejects_hitl(production_store, monkeypatch):
    from starlette.testclient import TestClient

    from munin.production.asgi import create_app

    monkeypatch.setenv("MUNIN_ALLOWED_ORIGINS", "http://testserver")
    monkeypatch.setenv("MUNIN_COOKIE_SECURE", "0")
    client = TestClient(create_app(production_store))

    operator = _make_actor(production_store)
    conversation = _make_conversation(production_store, owner_id=operator["id"])
    turn = _queued_run(
        production_store,
        actor_id=operator["id"],
        conversation_id=conversation["id"],
        key="cancel-running",
    )
    run_id = turn["run"]["id"]
    _claim_running(production_store, run_id=run_id)
    gate = _waiting_for_human(
        production_store, actor_id=operator["id"], run_id=run_id, conversation_id=conversation["id"]
    )

    login = client.post(
        "/api/auth/login",
        json={"username": "cancel-op", "password": "a strong cancel password"},
    )
    csrf = login.json()["csrf_token"]
    headers = {
        "Origin": "http://testserver",
        "Sec-Fetch-Site": "same-origin",
        "X-CSRF-Token": csrf,
    }

    response = client.post(f"/api/chat/{run_id}/cancel", headers=headers, json={})
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "cancelling"

    # The run is still ``waiting_for_human`` — the executor observes the fence
    # and performs the terminal transition.  No immediate state mutation.
    assert production_store.get_run(run_id)["state"] == "waiting_for_human"

    # The pending HITL request was atomically rejected with a durable event.
    events = production_store.list_run_events(run_id)
    resolved = [event for event in events if event["kind"] == "human_request.resolved"]
    assert len(resolved) == 1
    assert resolved[0]["payload"]["resolution"] == "rejected"
    assert resolved[0]["payload"]["request_id"] == gate["id"]

    with production_store._read_only() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT state FROM human_requests WHERE id=?", (gate["id"],)
        ).fetchone()
    assert row["state"] == "rejected"


def test_cancel_endpoint_returns_200_for_terminal_run_without_writing_fence(production_store, monkeypatch):
    from starlette.testclient import TestClient

    from munin.production.asgi import create_app

    monkeypatch.setenv("MUNIN_ALLOWED_ORIGINS", "http://testserver")
    monkeypatch.setenv("MUNIN_COOKIE_SECURE", "0")
    client = TestClient(create_app(production_store))

    operator = _make_actor(production_store)
    conversation = _make_conversation(production_store, owner_id=operator["id"])
    turn = _queued_run(
        production_store,
        actor_id=operator["id"],
        conversation_id=conversation["id"],
        key="cancel-terminal",
    )
    run_id = turn["run"]["id"]
    lease_token, _ = _claim_running(production_store, run_id=run_id)
    assert production_store.complete_run(
        run_id=run_id, lease_token=lease_token, content="done", outcome="completed"
    )

    login = client.post(
        "/api/auth/login",
        json={"username": "cancel-op", "password": "a strong cancel password"},
    )
    csrf = login.json()["csrf_token"]
    headers = {
        "Origin": "http://testserver",
        "Sec-Fetch-Site": "same-origin",
        "X-CSRF-Token": csrf,
    }

    response = client.post(f"/api/chat/{run_id}/cancel", headers=headers, json={})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"

    # No fence marker was written for an already-terminal run.
    with production_store._read_only() as conn:  # noqa: SLF001
        marker = conn.execute(
            "SELECT cancel_requested_at_ms FROM agent_runs WHERE id=?",
            (run_id,),
        ).fetchone()
    assert marker["cancel_requested_at_ms"] is None

    # No ``run.cancelling`` event was emitted.
    kinds = [event["kind"] for event in production_store.list_run_events(run_id)]
    assert "run.cancelling" not in kinds


def test_cancel_endpoint_returns_404_for_missing_run(production_store, monkeypatch):
    from starlette.testclient import TestClient

    from munin.production.asgi import create_app

    monkeypatch.setenv("MUNIN_ALLOWED_ORIGINS", "http://testserver")
    monkeypatch.setenv("MUNIN_COOKIE_SECURE", "0")
    client = TestClient(create_app(production_store))
    _make_actor(production_store)
    login = client.post(
        "/api/auth/login",
        json={"username": "cancel-op", "password": "a strong cancel password"},
    )
    csrf = login.json()["csrf_token"]
    headers = {
        "Origin": "http://testserver",
        "Sec-Fetch-Site": "same-origin",
        "X-CSRF-Token": csrf,
    }

    response = client.post("/api/chat/run_missing/cancel", headers=headers, json={})
    assert response.status_code == 404


def test_observe_cancel_fence_probe(production_store):
    from munin.production.runs import observe_cancel_fence

    operator = _make_actor(production_store)
    conversation = _make_conversation(production_store, owner_id=operator["id"])
    turn = _queued_run(
        production_store,
        actor_id=operator["id"],
        conversation_id=conversation["id"],
        key="cancel-probe",
    )
    run_id = turn["run"]["id"]

    # No fence marker yet → not cancelling.
    assert observe_cancel_fence(production_store, run_id=run_id) is False

    production_store.request_cancel_fence(actor_id=operator["id"], run_id=run_id)
    # Fence set on a queued run → cancelling.
    assert observe_cancel_fence(production_store, run_id=run_id) is True

    # Unknown run → False (no raise).
    assert observe_cancel_fence(production_store, run_id="run_does_not_exist") is False


def test_cancel_endpoint_requires_csrf_and_participant(production_store, monkeypatch):
    from starlette.testclient import TestClient

    from munin.production.asgi import create_app

    monkeypatch.setenv("MUNIN_ALLOWED_ORIGINS", "http://testserver")
    monkeypatch.setenv("MUNIN_COOKIE_SECURE", "0")
    client = TestClient(create_app(production_store))

    operator = _make_actor(production_store)
    conversation = _make_conversation(production_store, owner_id=operator["id"])
    turn = _queued_run(
        production_store,
        actor_id=operator["id"],
        conversation_id=conversation["id"],
        key="cancel-no-csrf",
    )
    run_id = turn["run"]["id"]

    login = client.post(
        "/api/auth/login",
        json={"username": "cancel-op", "password": "a strong cancel password"},
    )
    csrf = login.json()["csrf_token"]

    # Missing CSRF → 403.
    bad_headers = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}
    response = client.post(f"/api/chat/{run_id}/cancel", headers=bad_headers, json={})
    assert response.status_code == 403

    # Cross-site → 403.
    cross_headers = {
        "Origin": "http://evil.example",
        "Sec-Fetch-Site": "cross-site",
        "X-CSRF-Token": csrf,
    }
    response = client.post(f"/api/chat/{run_id}/cancel", headers=cross_headers, json={})
    assert response.status_code == 403
