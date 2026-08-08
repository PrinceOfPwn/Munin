# tags: [tests, artifacts-readmodel, PR-6C, ASGI, TestClient, run-detail-readmodel, aggregated_tools, activities, commands, agents, approvals, guidance, artifacts, summaries, determinism, no-provider]
"""PR-6C — ``GET /api/runs/{run_id}/detail`` composite read-model.

Asserts:

* The response carries EXACTLY the ten contract keys: ``run_id``, ``state``,
  ``aggregated_tools``, ``activities``, ``commands``, ``agents``,
  ``approvals``, ``guidance``, ``artifacts``, ``summaries``.
* Each section is populated from the durable read-model tables with
  deterministic ordering (repeated GETs are byte-identical).
* The endpoint is a pure read: 404/403 contract, no provider invocation.
"""
from __future__ import annotations

from pathlib import Path

import pytest

DETAIL_KEYS = {
    "run_id",
    "state",
    "aggregated_tools",
    "activities",
    "commands",
    "agents",
    "approvals",
    "guidance",
    "artifacts",
    "summaries",
}


@pytest.fixture
def production_store(tmp_path: Path):
    from munin.production.store import ProductionStore

    return ProductionStore.for_sqlite(tmp_path / "run_detail.sqlite", master_key=b"d" * 32)


def _login_headers(client, *, username="run-detail-op", password="a strong detail password"):
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


def _rich_run(store):
    """Build a run that exercises every read-model section."""
    operator = store.create_user(
        username="run-detail-op", password="a strong detail password", role="operator"
    )
    conversation = store.create_conversation(owner_id=operator["id"], title="Detail run")
    turn = store.create_turn(
        actor_id=operator["id"],
        conversation_id=conversation["id"],
        content="Run a bounded recon workflow",
        idempotency_key="run-detail-key",
    )
    run_id = turn["run"]["id"]
    user_message_id = turn["user_message_id"]
    assistant_message_id = turn["assistant_message_id"]

    store.append_reasoning_event(
        run_id=run_id, kind="operational_summary",
        content="Surveying the host perimeter", provider="", persistence_enabled=True,
    )
    store.append_reasoning_event(
        run_id=run_id, kind="provider_reasoning",
        content="(explicit provider deliberation)", provider="test-provider",
        persistence_enabled=True, agent_name="munin", step=1,
    )

    store.append_tool_call(
        run_id=run_id, agent_name="munin", tool_name="hugin_lookup", state="completed",
        arguments={"query": "ldap"}, result={"summary": "3 nodes"},
    )
    tool_id = "tool_hugin_lookup_1"
    store.append_tool_call(
        run_id=run_id, agent_name="munin", tool_name="hugin_lookup", state="completed",
        arguments={"query": "smtp"}, result={"summary": "1 node"}, tool_call_id=tool_id,
    )
    store.append_tool_call(
        run_id=run_id, agent_name="munin", tool_name="ldap_who_am_i", state="failed",
        arguments={}, result={"error": "connection refused"},
    )
    store.append_tool_output_event(
        run_id=run_id, tool_name="hugin_lookup", tool_call_id=tool_id,
        job_id="job-1", stream="stdout", text="node up\n", sequence=1, elapsed_ms=12,
    )
    store.append_tool_output_event(
        run_id=run_id, tool_name="hugin_lookup", tool_call_id=tool_id,
        job_id="job-1", stream="stderr", text="warn\n", sequence=2, elapsed_ms=20, final=True,
    )

    subagent = store.create_subagent_run(
        parent_run_id=run_id, profile_id="port-scanner", objective="enumerate open ports",
    )

    request = store.request_human_decision(
        run_id=run_id, action="Approve port scan", risk="high",
        evidence=["target scope"], scope={"actions": [{"name": "port_scan"}]},
        choices=["approve", "reject"],
    )
    store.resolve_human_decision(
        actor_id=operator["id"], request_id=request["id"], choice="approve",
        nonce=request["nonce"], guidance="stay in scope",
    )

    guidance = store.enqueue_guidance(
        run_id=run_id, actor_id=operator["id"], actor_username=operator["username"],
        body="prefer nmap only",
    )
    store.transition_guidance_state(guidance["id"], "delivered_to_runtime")

    store.create_conversation_summary(
        actor_id=operator["id"], conversation_id=conversation["id"],
        source_message_ids=[user_message_id, assistant_message_id],
        summary="Recon run summarised for the operator", model="test-model",
        prompt_version="v1", confidence=0.9, run_id=run_id,
    )

    from munin.production.chat import _claim_direct

    lease_token, _assistant_id = _claim_direct(store, run_id=run_id)
    store.complete_run(
        run_id=run_id, lease_token=lease_token,
        content="Done.\n\n```python\nprint('report')\n```\n", outcome="completed",
    )

    return operator, conversation, run_id, {
        "subagent_id": subagent["id"],
        "human_request_id": request["id"],
        "guidance_id": guidance["id"],
    }


def test_run_detail_exact_keys_and_sections(production_store, monkeypatch):
    client = _make_client(production_store, monkeypatch)
    _operator, _conversation, run_id, ids = _rich_run(production_store)

    response = client.get(f"/api/runs/{run_id}/detail", headers=_login_headers(client))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    data = body["data"]
    assert set(data.keys()) == DETAIL_KEYS
    assert data["run_id"] == run_id
    assert data["state"] == "completed"

    # aggregated_tools — grouped, deterministic by tool name.
    tools = {item["tool_name"]: item for item in data["aggregated_tools"]}
    assert set(tools) == {"hugin_lookup", "ldap_who_am_i"}
    assert tools["hugin_lookup"]["call_count"] == 2
    assert tools["hugin_lookup"]["by_state"] == {"completed": 2}
    assert tools["ldap_who_am_i"]["by_state"] == {"failed": 1}

    # activities — operational summaries only, never provider reasoning.
    assert [item["content"] for item in data["activities"]] == [
        "Surveying the host perimeter"
    ]

    # commands — tool calls that emitted bounded output chunks.
    assert len(data["commands"]) == 1
    command = data["commands"][0]
    assert command["tool_name"] == "hugin_lookup"
    assert command["chunk_count"] == 2
    assert command["streams"] == ["stdout", "stderr"]

    # agents — durable subagent runs for this parent.
    assert len(data["agents"]) == 1
    assert data["agents"][0]["id"] == ids["subagent_id"]
    assert data["agents"][0]["profile_id"] == "port-scanner"
    assert data["agents"][0]["state"] == "queued"

    # approvals — durable HITL rows with a derived resolution.
    assert len(data["approvals"]) == 1
    approval = data["approvals"][0]
    assert approval["id"] == ids["human_request_id"]
    assert approval["action"] == "Approve port scan"
    assert approval["resolution"] == "approved"
    assert approval["choices"] == ["approve", "reject"]

    # guidance — durable outbox rows with lifecycle state.
    assert len(data["guidance"]) == 1
    guidance = data["guidance"][0]
    assert guidance["id"] == ids["guidance_id"]
    assert guidance["body"] == "prefer nmap only"
    assert guidance["state"] == "delivered_to_runtime"

    # artifacts — fenced-code extraction from complete_run, rich metadata.
    assert len(data["artifacts"]) == 1
    artifact = data["artifacts"][0]
    assert artifact["filename"].endswith(".py")
    assert int(artifact["version"]) == 1
    assert "content" not in artifact

    # summaries — compaction records attached to the run.
    assert len(data["summaries"]) == 1
    summary = data["summaries"][0]
    assert summary["model"] == "test-model"
    assert summary["confidence"] == 0.9
    assert "Recon run summarised" in summary["content"]


def test_run_detail_deterministic_across_repeated_gets(production_store, monkeypatch):
    client = _make_client(production_store, monkeypatch)
    _operator, _conversation, run_id, _ids = _rich_run(production_store)
    headers = _login_headers(client)

    first = client.get(f"/api/runs/{run_id}/detail", headers=headers)
    second = client.get(f"/api/runs/{run_id}/detail", headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.content == second.content


def test_run_detail_404_unknown_run(production_store, monkeypatch):
    client = _make_client(production_store, monkeypatch)
    production_store.create_user(
        username="run-detail-op", password="a strong detail password", role="operator"
    )
    response = client.get("/api/runs/run_missing/detail", headers=_login_headers(client))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_run_detail_403_for_non_participant(production_store, monkeypatch):
    client = _make_client(production_store, monkeypatch)
    _operator, _conversation, run_id, _ids = _rich_run(production_store)

    production_store.create_user(
        username="run-detail-intruder", password="a strong intruder password", role="operator"
    )
    intruder_headers = _login_headers(
        client, username="run-detail-intruder", password="a strong intruder password"
    )
    response = client.get(f"/api/runs/{run_id}/detail", headers=intruder_headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_run_detail_never_invokes_provider(production_store, monkeypatch):
    """The read-model must be pure SQL — no provider, no regeneration."""
    client = _make_client(production_store, monkeypatch)
    _operator, _conversation, run_id, _ids = _rich_run(production_store)

    calls: list[str] = []

    def _guard(name):
        def _wrapper(*_args, **_kwargs):
            calls.append(name)
            raise AssertionError(f"read-model invoked provider path: {name}")

        return _wrapper

    for name in ("reveal_provider_key", "list_provider_profiles"):
        monkeypatch.setattr(production_store, name, _guard(name))

    response = client.get(f"/api/runs/{run_id}/detail", headers=_login_headers(client))
    assert response.status_code == 200
    assert calls == []
