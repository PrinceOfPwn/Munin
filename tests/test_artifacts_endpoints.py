# tags: [tests, artifacts-readmodel, PR-6B, ASGI, TestClient, conversation-artifacts, run-artifacts, listing-endpoints, 403, 404]
"""PR-6B — artifact listing endpoints.

Asserts:

* ``GET /api/chat/{conversation_id}/artifacts`` returns rich metadata,
  chronological (created_at ASC, per the PR-6B card), with the content body
  omitted.
* ``GET /api/runs/{run_id}/artifacts`` returns the same contract for one
  run, oldest first.
* Both endpoints enforce the participant boundary (403) and the 404 JSON
  contract for unknown conversation/run ids.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def production_store(tmp_path: Path):
    from munin.production.store import ProductionStore

    return ProductionStore.for_sqlite(tmp_path / "artifacts_endpoints.sqlite", master_key=b"c" * 32)


def _login_headers(client, *, username="artifacts-ep-op", password="a strong endpoints password"):
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


def _setup(store):
    operator = store.create_user(
        username="artifacts-ep-op", password="a strong endpoints password", role="operator"
    )
    conversation = store.create_conversation(owner_id=operator["id"], title="Listing")
    turn = store.create_turn(
        actor_id=operator["id"],
        conversation_id=conversation["id"],
        content="Produce a report artifact",
        idempotency_key="artifacts-ep-listing",
    )
    run_id = turn["run"]["id"]
    older = store.add_artifact(
        actor_id=operator["id"], conversation_id=conversation["id"], filename="older.txt",
        media_type="text/plain", language="text", content="older", run_id=run_id,
        renderer="text", version=1,
    )
    newer = store.add_artifact(
        actor_id=operator["id"], conversation_id=conversation["id"], filename="newer.json",
        media_type="application/json", language="json", content='{"ok": true}', run_id=run_id,
        renderer="code", version=2, provenance="evidence:report",
        preview_url="/p", download_url="/d",
    )
    return operator, conversation, run_id, older, newer


def test_conversation_artifacts_lists_oldest_first(production_store, monkeypatch):
    client = _make_client(production_store, monkeypatch)
    _operator, conversation, _run_id, older, newer = _setup(production_store)

    response = client.get(f"/api/chat/{conversation['id']}/artifacts", headers=_login_headers(client))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert [item["id"] for item in data] == [older["id"], newer["id"]]

    first = data[0]
    assert first["filename"] == "older.txt"
    assert first["renderer"] == "text"
    assert int(first["version"]) == 1
    assert first["provenance"] is None
    assert first["preview_url"] is None
    assert first["download_url"] is None
    assert "content" not in first
    assert "content_hash" in first
    assert "size_bytes" in first


def test_run_artifacts_lists_oldest_first(production_store, monkeypatch):
    client = _make_client(production_store, monkeypatch)
    _operator, _conversation, run_id, older, newer = _setup(production_store)

    response = client.get(f"/api/runs/{run_id}/artifacts", headers=_login_headers(client))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert [item["id"] for item in data] == [older["id"], newer["id"]]
    assert all("content" not in item for item in data)
    assert {item["filename"] for item in data} == {"older.txt", "newer.json"}


def test_conversation_artifacts_404_unknown_conversation(production_store, monkeypatch):
    client = _make_client(production_store, monkeypatch)
    _setup(production_store)
    response = client.get("/api/chat/conv_missing/artifacts", headers=_login_headers(client))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_run_artifacts_404_unknown_run(production_store, monkeypatch):
    client = _make_client(production_store, monkeypatch)
    _setup(production_store)
    response = client.get("/api/runs/run_missing/artifacts", headers=_login_headers(client))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_artifacts_listings_403_for_non_participant(production_store, monkeypatch):
    client = _make_client(production_store, monkeypatch)
    _operator, conversation, run_id, _older, _newer = _setup(production_store)

    production_store.create_user(
        username="artifacts-ep-intruder", password="a strong intruder password", role="operator"
    )
    intruder_headers = _login_headers(
        client, username="artifacts-ep-intruder", password="a strong intruder password"
    )

    conversation_response = client.get(
        f"/api/chat/{conversation['id']}/artifacts", headers=intruder_headers
    )
    assert conversation_response.status_code == 403
    assert conversation_response.json()["error"]["code"] == "forbidden"

    run_response = client.get(f"/api/runs/{run_id}/artifacts", headers=intruder_headers)
    assert run_response.status_code == 403
    assert run_response.json()["error"]["code"] == "forbidden"
