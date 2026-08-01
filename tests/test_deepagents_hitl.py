"""Durable adapter tests for the native Deep Agents HITL checkpoint flow."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_runtime_interrupt_becomes_durable_replayable_human_request(
    tmp_path, monkeypatch
):
    from munin.core import supervisor as supervisor_module
    from munin.core.runtime_adapter import supervisor_runner
    from munin.production.chat import _claim_direct
    from munin.production.store import ProductionStore

    production_store = ProductionStore.for_sqlite(
        tmp_path / "production.sqlite", master_key=b"m" * 32
    )

    operator = production_store.bootstrap_admin(
        username="hitl-adapter", password="A secure password 123!"
    )
    conversation = production_store.create_conversation(
        owner_id=operator["id"], title="HITL adapter", scope={}
    )
    turn = production_store.create_turn(
        actor_id=operator["id"],
        conversation_id=conversation["id"],
        content="scan the approved target",
        idempotency_key="native-hitl-001",
    )
    run_id = turn["run"]["id"]
    _claim_direct(production_store, run_id=run_id)

    class FakeGraph:
        async def astream_events(self, _input, *, config, version):  # noqa: ANN001, ARG002
            interrupt = type(
                "Interrupt",
                (),
                {
                    "value": {
                        "action_requests": [
                            {
                                "name": "nmap_scan",
                                "args": {"target": "approved.example"},
                                "description": "Run an authorized scan",
                            }
                        ]
                    }
                },
            )()
            yield {
                "event": "on_chain_stream",
                "name": "LangGraph",
                "data": {"chunk": {"__interrupt__": (interrupt,)}},
            }

    monkeypatch.setattr(supervisor_module, "build_munin_supervisor", lambda **_: FakeGraph())

    events = [
        event
        async for event in supervisor_runner(
            "scan the approved target",
            run_id=run_id,
            conversation_id=conversation["id"],
            store=object(),
            human_request_store=production_store,
        )
    ]
    human = next(event for event in events if event["kind"] == "human_request")
    assert human["tool_name"] == "nmap_scan"
    assert human["nonce"]
    assert production_store.get_run(run_id)["state"] == "waiting_for_human"

    replacement = production_store.reissue_human_decision_nonce(
        actor_id=operator["id"], request_id=human["request_id"]
    )
    assert replacement["nonce"] != human["nonce"]
    with pytest.raises(PermissionError):
        production_store.resolve_human_decision(
            actor_id=operator["id"],
            request_id=human["request_id"],
            choice="approve",
            nonce=human["nonce"],
        )
    resolved = production_store.resolve_human_decision(
        actor_id=operator["id"],
        request_id=human["request_id"],
        choice="approve",
        nonce=replacement["nonce"],
    )
    assert resolved["state"] == "queued"
    assert resolved["decision_count"] == 1


def test_runtime_recursion_budget_is_unlimited_by_default(monkeypatch):
    from munin.core import runtime_adapter

    monkeypatch.delenv("MUNIN_RECURSION_LIMIT", raising=False)
    assert (
        runtime_adapter._recursion_limit_from_environment()
        == runtime_adapter.UNLIMITED_RECURSION_LIMIT
    )

    monkeypatch.setenv("MUNIN_RECURSION_LIMIT", "0")
    assert (
        runtime_adapter._recursion_limit_from_environment()
        == runtime_adapter.UNLIMITED_RECURSION_LIMIT
    )

    monkeypatch.setenv("MUNIN_RECURSION_LIMIT", "17")
    assert runtime_adapter._recursion_limit_from_environment() == 17
