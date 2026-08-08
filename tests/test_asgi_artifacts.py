# tags: [tests, artifacts-readmodel, PR-6A, ASGI, TestClient, artifact-endpoint, renderer, provenance, download, inline, 403, 404]
"""PR-6A — ``GET /api/artifacts/{artifact_id}`` exposes the rich metadata.

Asserts:

* The authenticated artifact endpoint returns the PR-6 renderer-contract
  fields (``renderer``, ``version``, ``provenance``, ``preview_url``,
  ``download_url``) alongside the pre-existing metadata.
* ``?download=true`` still streams the body with an attachment disposition.
* Unknown ids return the 404 JSON contract; non-participants get 403.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def production_store(tmp_path: Path):
    from munin.production.store import ProductionStore

    return ProductionStore.for_sqlite(tmp_path / "asgi_artifacts.sqlite", master_key=b"b" * 32)


def _login_headers(client, *, username="artifacts-asgi-op", password="a strong asgi artifacts password"):
    login = client.post("/api/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    csrf = login.json()["csrf_token"]
    return {
        "Origin": "http://testserver",
        "Sec-Fetch-Site": "same-origin",
        "X-CSRF-Token": csrf,
    }


def _setup(store):
    operator = store.create_user(
        username="artifacts-asgi-op", password="a strong asgi artifacts password", role="operator"
    )
    conversation = store.create_conversation(owner_id=operator["id"], title="ASGI artifacts")
    artifact = store.add_artifact(
        actor_id=operator["id"],
        conversation_id=conversation["id"],
        filename="asgi.py",
        media_type="text/x-python",
        language="python",
        content="print('asgi artifact')\n",
        run_id="run-asgi",
        renderer="code",
        version=2,
        provenance="evidence:asgi-report",
        preview_url="/api/artifacts/peek",
        download_url="/api/artifacts/direct",
    )
    return operator, conversation, artifact


def test_artifact_endpoint_returns_rich_metadata(production_store, monkeypatch):
    monkeypatch.setenv("MUNIN_ALLOWED_ORIGINS", "http://testserver")
    monkeypatch.setenv("MUNIN_COOKIE_SECURE", "0")
    from starlette.testclient import TestClient

    from munin.production.asgi import create_app

    client = TestClient(create_app(production_store))
    _operator, _conversation, artifact = _setup(production_store)

    response = client.get(f"/api/artifacts/{artifact['id']}", headers=_login_headers(client))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    data = body["data"]
    assert data["id"] == artifact["id"]
    assert data["renderer"] == "code"
    assert int(data["version"]) == 2
    assert data["provenance"] == "evidence:asgi-report"
    assert data["preview_url"] == "/api/artifacts/peek"
    assert data["download_url"] == "/api/artifacts/direct"
    assert data["content"] == "print('asgi artifact')\n"


def test_artifact_download_still_streams_body(production_store, monkeypatch):
    monkeypatch.setenv("MUNIN_ALLOWED_ORIGINS", "http://testserver")
    monkeypatch.setenv("MUNIN_COOKIE_SECURE", "0")
    from starlette.testclient import TestClient

    from munin.production.asgi import create_app

    client = TestClient(create_app(production_store))
    _operator, _conversation, artifact = _setup(production_store)

    response = client.get(
        f"/api/artifacts/{artifact['id']}?download=true", headers=_login_headers(client)
    )
    assert response.status_code == 200
    assert response.text == "print('asgi artifact')\n"
    disposition = response.headers.get("content-disposition", "")
    assert disposition.startswith("attachment; filename=")
    assert "asgi.py" in disposition


def test_artifact_endpoint_404_for_unknown_id(production_store, monkeypatch):
    monkeypatch.setenv("MUNIN_ALLOWED_ORIGINS", "http://testserver")
    monkeypatch.setenv("MUNIN_COOKIE_SECURE", "0")
    from starlette.testclient import TestClient

    from munin.production.asgi import create_app

    client = TestClient(create_app(production_store))
    _setup(production_store)

    response = client.get("/api/artifacts/artifact_does_not_exist", headers=_login_headers(client))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_artifact_endpoint_403_for_non_participant(production_store, monkeypatch):
    monkeypatch.setenv("MUNIN_ALLOWED_ORIGINS", "http://testserver")
    monkeypatch.setenv("MUNIN_COOKIE_SECURE", "0")
    from starlette.testclient import TestClient

    from munin.production.asgi import create_app

    client = TestClient(create_app(production_store))
    _operator, _conversation, artifact = _setup(production_store)

    production_store.create_user(
        username="artifacts-intruder", password="a strong intruder password", role="operator"
    )
    intruder_headers = _login_headers(
        client, username="artifacts-intruder", password="a strong intruder password"
    )

    response = client.get(f"/api/artifacts/{artifact['id']}", headers=intruder_headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
