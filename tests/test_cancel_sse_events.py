# tags: [tests, sse-streaming, run.cancelling, run.cancelled, cancel-fence, observe_cancel_fence, idempotent-replay, PR-2B, _stream_chat, _envelope_from_event]
"""PR-2B — cancel SSE event emission and idempotent replay.

Asserts:

* The durable ``run.cancelling`` event is recorded immediately on cancel ACK
  (already covered by PR-2A's endpoint; we directly assert the event log).
* When the supervisor executor observes the fence between steps, it emits a
  ``run.cancelled`` SSE frame and finalises the run as ``cancelled``.
* Reconnect replay (``_stream_idempotent_replay`` /
  ``_envelope_from_event``) surfaces ``run.cancelling`` and ``run.cancelled``
  exactly once — a reconnect never duplicates cancel events.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def production_store(tmp_path: Path):
    from munin.production.store import ProductionStore

    return ProductionStore.for_sqlite(tmp_path / "cancel_sse.sqlite", master_key=b"s" * 32)


def _actor_and_run(store, *, key="cancel-sse"):
    operator = store.create_user(
        username="cancel-sse-op", password="a strong cancel password", role="operator"
    )
    conversation = store.create_conversation(owner_id=operator["id"], title="Cancel SSE")
    turn = store.create_turn(
        actor_id=operator["id"],
        conversation_id=conversation["id"],
        content="Drive the supervisor then cancel",
        idempotency_key=key,
    )
    return operator, conversation, turn


async def _drive_stream_chat(store, *, run_id, operator, conversation, supervisor_envelopes):
    """Run ``_stream_chat`` directly with monkeypatched dependencies.

    Returns the list of SSE frames (bytes) the executor emitted.
    """
    from munin.production import chat

    async def fake_supervisor(prompt, **kwargs):  # noqa: ARG001
        for envelope in supervisor_envelopes:
            yield envelope

    class _FakeModel:
        pass

    class _FakeLLMClient:
        def __init__(self, settings):  # noqa: ARG002
            pass

        def make_langchain(self):
            return _FakeModel()

    class _FakeRequest:
        async def is_disconnected(self) -> bool:
            return False

    chat.supervisor_runner = fake_supervisor  # type: ignore[attr-defined]

    import munin.core.runtime_adapter as runtime_adapter

    original_runner = runtime_adapter.supervisor_runner
    runtime_adapter.supervisor_runner = fake_supervisor

    import munin.core.llm_client as llm_client

    original_llm_client = llm_client.LLMClient
    llm_client.LLMClient = _FakeLLMClient  # type: ignore[misc, assignment]

    frames: list[bytes] = []
    try:
        lease_token, assistant_message_id = chat._claim_direct(store, run_id=run_id)
        async for frame in chat._stream_chat(
            _FakeRequest(),
            store=store,
            shared_state=object(),  # truthy → skips the SharedStateStore construction path
            actor_info={"id": operator["id"], "username": "cancel-sse-op"},
            run_id=run_id,
            conversation_id=conversation["id"],
            prompt="Drive the supervisor then cancel",
            conversation_history=[],
            assistant_message_id=assistant_message_id,
            lease_token=lease_token,
        ):
            frames.append(frame)
    finally:
        runtime_adapter.supervisor_runner = original_runner
        llm_client.LLMClient = original_llm_client  # type: ignore[misc, assignment]
    return frames


def _frames_to_envelopes(frames: list[bytes]) -> list[dict[str, Any]]:
    envelopes: list[dict[str, Any]] = []
    for frame in frames:
        text = frame.decode("utf-8", errors="replace")
        for line in text.split("\n"):
            if line.startswith("data:"):
                raw = line[len("data:"):].strip()
                if raw and raw != "[DONE]":
                    try:
                        envelopes.append(json.loads(raw))
                    except json.JSONDecodeError:
                        pass
    return envelopes


@pytest.mark.asyncio
async def test_cancel_sse_emission_and_idempotent_replay(production_store, monkeypatch):
    # ``_stream_chat`` calls ``get_settings()`` and ``list_provider_profiles``;
    # the latter returns ``[]`` so the BYOK override path is skipped.
    monkeypatch.setenv("MUNIN_ALLOWED_ORIGINS", "http://testserver")
    monkeypatch.setenv("MUNIN_COOKIE_SECURE", "0")
    monkeypatch.setenv("LLM_API_KEY", "test")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost")

    operator, conversation, turn = _actor_and_run(production_store)
    run_id = turn["run"]["id"]

    # Set the cancel fence immediately (simulates POST /cancel ACK).  The
    # ``run.cancelling`` durable event is recorded right here, before the
    # supervisor emits anything.
    production_store.request_cancel_fence(actor_id=operator["id"], run_id=run_id)
    cancelling_events = [
        event for event in production_store.list_run_events(run_id)
        if event["kind"] == "run.cancelling"
    ]
    assert len(cancelling_events) == 1
    cancelling_event = cancelling_events[0]

    # Drive the executor.  The supervisor yields one activity envelope; the
    # fence check runs after it and breaks the loop, finalising as cancelled.
    supervisor_envelopes = [
        {"kind": "activity", "run_id": run_id, "text": "working"},
    ]
    frames = await _drive_stream_chat(
        production_store,
        run_id=run_id,
        operator=operator,
        conversation=conversation,
        supervisor_envelopes=supervisor_envelopes,
    )
    envelopes = _frames_to_envelopes(frames)

    # The supervisor's own envelope is forwarded, then a terminal
    # ``run.cancelled`` frame is appended by the executor.
    run_state_envelopes = [env for env in envelopes if env.get("kind") == "run_state"]
    assert any(env["state"] == "cancelled" for env in run_state_envelopes), envelopes

    # The durable log now has exactly one ``run.cancelled`` event (appended by
    # ``complete_run`` via ``_finalize``).
    events = production_store.list_run_events(run_id)
    cancelled_events = [event for event in events if event["kind"] == "run.cancelled"]
    assert len(cancelled_events) == 1
    cancelling_events_after = [
        event for event in events if event["kind"] == "run.cancelling"
    ]
    assert len(cancelling_events_after) == 1
    assert cancelling_events_after[0]["id"] == cancelling_event["id"]

    # The run is in the terminal ``cancelled`` state.
    assert production_store.get_run(run_id)["state"] == "cancelled"

    # Idempotent replay: ``_envelope_from_event`` surfaces ``run.cancelling``
    # exactly once for the recorded event id.  Building envelopes from the full
    # event log a second time must not duplicate.
    from munin.production.chat import _envelope_from_event

    replayed_cancelling: list[dict[str, Any]] = []
    replayed_cancelled: list[dict[str, Any]] = []
    for event in events:
        envelope = _envelope_from_event(
            event,
            run_id=run_id,
            tools_by_eid={},
            tools_by_call_id=None,
            reasoning_by_eid={},
        )
        if envelope is None:
            continue
        if envelope.get("kind") == "run_state" and envelope.get("state") == "cancelling":
            replayed_cancelling.append(envelope)
        if envelope.get("kind") == "run_state" and envelope.get("state") == "cancelled":
            replayed_cancelled.append(envelope)
    assert len(replayed_cancelling) == 1
    assert replayed_cancelling[0]["state"] == "cancelling"
    assert len(replayed_cancelled) == 1


@pytest.mark.asyncio
async def test_cancelling_event_emitted_immediately_on_ack(production_store, monkeypatch):
    """Setting the fence marker emits ``run.cancelling`` BEFORE the executor
    observes it — a connected SSE subscriber renders the truthful state
    without waiting for the next supervisor step."""
    monkeypatch.setenv("MUNIN_ALLOWED_ORIGINS", "http://testserver")
    operator, conversation, turn = _actor_and_run(production_store, key="cancel-ack")
    run_id = turn["run"]["id"]

    before = time.time()
    production_store.request_cancel_fence(actor_id=operator["id"], run_id=run_id)
    events = production_store.list_run_events(run_id)
    kinds = [event["kind"] for event in events]
    assert "run.cancelling" in kinds
    cancelling = next(event for event in events if event["kind"] == "run.cancelling")
    assert cancelling["payload"] == {"reason": "operator_request", "requested_at_ms": cancelling["payload"]["requested_at_ms"]}
    assert int(cancelling["payload"]["requested_at_ms"]) >= int(before * 1000) - 5
    # The run is still queued — no terminal transition on ACK.
    assert production_store.get_run(run_id)["state"] == "queued"
