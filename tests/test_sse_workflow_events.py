# tags: [tests, sse-streaming, artifacts-readmodel, PR-6D, workflow-events, subagent_started, subagent_state, subagent.queued, subagent.started, subagent.state, envelope-normalization, activity-reuse, no-producer, runtime_adapter]
"""PR-6D — SSE workflow event vocabulary.

PLAN-6 wanted ``subagent_spawn`` / ``subagent_handoff`` / ``workflow_step`` /
``fanout_branch`` envelopes on the SSE stream.  v1.0.0 has no runtime
producers for three of those four kinds: the module that originally emitted
them (``munin.production.agents.py``) was deleted in PR-0, swarm handoffs
(``core/coordination/handoff_tools.py``) and Send-API fanout
(``core/parallel/send_workers.py``) are graph-internal and never emit
envelopes.  Per the PLAN-6 directive we REUSE the existing vocabulary instead
of inventing producers:

* ``subagent_spawn``   -> ``subagent.queued`` (durable, ``create_subagent_run``)
  normalized to the frontend ``subagent_started`` envelope with the stable
  ``subagent_id`` / ``name`` / ``state`` fields the translator already renders.
* ``subagent_handoff`` -> no v1.0.0 producer; a handoff tool call surfaces as
  ``tool_*`` + ``activity`` envelopes today.
* ``workflow_step``    -> ``activity`` (operational summaries / model-step
  staging already emit this).
* ``fanout_branch``    -> no v1.0.0 producer; per-worker spawns surface as
  ``subagent_started`` envelopes.

This module asserts the normalization contract on ``_envelope_from_event``
(replay path), the durable producer contract (``subagent.queued``), and that
the live adapter never invents the unproduced kinds.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from langchain_core.messages import AIMessageChunk


@pytest.fixture
def production_store(tmp_path: Path):
    from munin.production.store import ProductionStore

    return ProductionStore.for_sqlite(tmp_path / "sse_workflow.sqlite", master_key=b"w" * 32)


def _event(kind: str, payload: dict[str, Any], *, eid: str = "evt-1") -> dict[str, Any]:
    return {"id": eid, "kind": kind, "payload": payload, "sequence": 1, "created_at_ms": 1}


def _translate(event: dict[str, Any]) -> dict[str, Any] | None:
    from munin.production.chat import _envelope_from_event

    return _envelope_from_event(
        event,
        run_id="run-1",
        tools_by_eid={},
        tools_by_call_id=None,
        reasoning_by_eid={},
    )


def test_subagent_queued_normalizes_to_subagent_started():
    envelope = _translate(
        _event("subagent.queued", {"subagent_run_id": "subrun-1", "profile_id": "scanner", "objective": "enumerate ports"})
    )
    assert envelope is not None
    assert envelope["kind"] == "subagent_started"
    assert envelope["run_id"] == "run-1"
    assert envelope["subagent_id"] == "subrun-1"
    assert envelope["name"] == "scanner"
    assert envelope["state"] == "started"
    assert envelope["objective"] == "enumerate ports"


def test_subagent_started_and_state_transitions():
    started = _translate(
        _event("subagent.started", {"subagent_id": "subrun-1", "name": "scanner", "state": "started"})
    )
    assert started["kind"] == "subagent_started"
    assert started["subagent_id"] == "subrun-1"
    assert started["state"] == "started"

    running = _translate(
        _event("subagent.state", {"subagent_id": "subrun-1", "name": "scanner", "state": "running"})
    )
    assert running["kind"] == "subagent_state"
    assert running["subagent_id"] == "subrun-1"
    assert running["state"] == "running"

    completed = _translate(
        _event("subagent.completed", {"subagent_id": "subrun-1", "name": "scanner"})
    )
    assert completed["kind"] == "subagent_state"
    assert completed["state"] == "completed"

    failed = _translate(
        _event("subagent.failed", {"subagent_id": "subrun-1", "name": "scanner", "state": "failed"})
    )
    assert failed["kind"] == "subagent_state"
    assert failed["state"] == "failed"


def test_subagent_envelope_without_id_is_dropped():
    assert _translate(_event("subagent.queued", {"objective": "no identity"})) is None
    assert _translate(_event("subagent.state", {})) is None


def test_create_subagent_run_produces_normalized_replay_envelope(production_store):
    operator = production_store.create_user(
        username="sse-workflow-op", password="a strong workflow password", role="operator"
    )
    conversation = production_store.create_conversation(owner_id=operator["id"], title="Workflow SSE")
    turn = production_store.create_turn(
        actor_id=operator["id"],
        conversation_id=conversation["id"],
        content="Spawn a worker",
        idempotency_key="sse-workflow",
    )
    run_id = turn["run"]["id"]

    subagent = production_store.create_subagent_run(
        parent_run_id=run_id, profile_id="port-scanner", objective="enumerate open ports"
    )

    queued_events = [
        event for event in production_store.list_run_events(run_id)
        if event["kind"] == "subagent.queued"
    ]
    assert len(queued_events) == 1
    assert queued_events[0]["payload"]["subagent_run_id"] == subagent["id"]

    envelope = _translate(queued_events[0])
    assert envelope is not None
    assert envelope["kind"] == "subagent_started"
    assert envelope["subagent_id"] == subagent["id"]
    assert envelope["name"] == "port-scanner"
    assert envelope["state"] == "started"


def test_workflow_step_reuses_activity_kind():
    """``workflow_step`` is served by the existing ``activity`` envelope."""
    envelope = _translate(
        _event("reasoning.operational_summary", {"provider": "", "agent": "munin", "step": 1}, eid="evt-reason-1"),
    )
    # The payload does not carry the text — it lives on reasoning_events and
    # must be supplied via the ``reasoning_by_eid`` index, mirroring the live
    # replay path.  Without the index the envelope is skipped, never garbled.
    assert envelope is None

    from munin.production.chat import _envelope_from_event

    envelope = _envelope_from_event(
        _event("reasoning.operational_summary", {"provider": "", "agent": "munin", "step": 1}, eid="evt-reason-1"),
        run_id="run-1",
        tools_by_eid={},
        tools_by_call_id=None,
        reasoning_by_eid={"evt-reason-1": {"event_id": "evt-reason-1", "kind": "operational_summary", "content": "Planning next step", "provider": "", "agent_name": "munin", "step": 1, "persisted": True, "provenance": "operational", "created_at_ms": 1}},
    )
    assert envelope is not None
    assert envelope["kind"] == "activity"
    assert envelope["text"] == "Planning next step"


def test_live_adapter_never_invents_unproduced_kinds():
    """Guard: the live envelope adapter emits only the known vocabulary.

    If a future producer wires ``subagent_handoff`` / ``workflow_step`` /
    ``fanout_branch`` into ``translate_events``, this test fails and the
    frontend contract (muninUiSchemas.ts, translator.ts, route.ts) must be
    extended deliberately — never silently.
    """
    from munin.core.runtime_adapter import translate_events

    sample_events = [
        {"event": "on_chat_model_start", "name": "chat_model"},
        {"event": "on_chat_model_stream", "name": "chat_model", "data": {"chunk": AIMessageChunk(content="hi")}},
        {"event": "on_chain_end", "name": "LangGraph", "data": {"output": {"messages": []}}},
    ]
    produced: set[str] = set()
    for sample in sample_events:
        for envelope in translate_events(sample, run_id="run-1"):
            produced.add(str(envelope.get("kind")))
    assert not (produced & {"subagent_handoff", "workflow_step", "fanout_branch"}), (
        "live adapter must not invent unproduced workflow kinds"
    )
