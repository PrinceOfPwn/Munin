"""Crash/restart recovery contracts for the durable chat executor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest


class _CheckpointSaver:
    def __init__(self, value: object | None = None) -> None:
        self.value = value if value is not None else object()
        self.thread_ids: list[str] = []

    async def aget_tuple(self, config):  # noqa: ANN001
        self.thread_ids.append(str(config["configurable"]["thread_id"]))
        return self.value


def _queued_run(store, *, key: str):  # noqa: ANN001
    operator = store.create_user(
        username=f"recovery-{key}", password=f"a strong recovery password {key}", role="operator"
    )
    conversation = store.create_conversation(owner_id=operator["id"], title="Recovery")
    turn = store.create_turn(
        actor_id=operator["id"],
        conversation_id=conversation["id"],
        content="Resume the durable operation",
        idempotency_key=key,
    )
    return operator, conversation, turn


@pytest.mark.asyncio
async def test_expired_chat_run_is_fenced_and_resumes_same_checkpoint(tmp_path, monkeypatch):
    from munin.production import chat
    from munin.production.store import ProductionStore

    store = ProductionStore.for_sqlite(tmp_path / "recovery.sqlite", master_key=b"r" * 32)
    operator, conversation, turn = _queued_run(store, key="expired-checkpoint")
    run_id = turn["run"]["id"]
    original_token, _ = chat._claim_direct(store, run_id=run_id)
    store.force_run_lease_expiry(run_id, datetime.now(UTC) - timedelta(seconds=1))

    launched: list[dict] = []
    monkeypatch.setattr(chat, "_launch_chat_run", lambda **kwargs: launched.append(kwargs))
    saver = _CheckpointSaver()
    recovered = await chat.recover_persisted_chat_runs(
        store=store,
        shared_state=SimpleNamespace(langgraph_checkpointer=saver),
    )

    assert recovered == [run_id]
    assert len(launched) == 1
    launch = launched[0]
    assert launch["run_id"] == run_id
    assert launch["resume_from_checkpoint"] is True
    assert launch["resume_decisions"] is None
    assert saver.thread_ids == [conversation["id"]]
    run = store.get_run(run_id)
    assert run["state"] == "running"
    assert not store.renew_run_lease(
        run_id=run_id, lease_token=original_token, lease_seconds=60
    )
    assert int(run["fencing_epoch"]) == 2
    assert [event["kind"] for event in store.list_run_events(run_id)] == [
        "run.queued",
        "run.claimed",
        "run.recovery_queued",
        "run.claimed",
    ]


@pytest.mark.asyncio
async def test_recovery_never_autoruns_waiting_human_request(tmp_path, monkeypatch):
    from munin.production import chat
    from munin.production.store import ProductionStore

    store = ProductionStore.for_sqlite(tmp_path / "human.sqlite", master_key=b"h" * 32)
    _operator, _conversation, turn = _queued_run(store, key="waiting-human")
    run_id = turn["run"]["id"]
    chat._claim_direct(store, run_id=run_id)
    store.request_human_decision(
        run_id=run_id,
        action="approve scan",
        risk="high",
        evidence=["scope checked"],
        scope={"actions": [{"name": "nmap_scan", "args": {"target": "example.test"}}]},
        choices=["approve", "reject"],
    )

    launched: list[dict] = []
    monkeypatch.setattr(chat, "_launch_chat_run", lambda **kwargs: launched.append(kwargs))
    recovered = await chat.recover_persisted_chat_runs(
        store=store,
        shared_state=SimpleNamespace(langgraph_checkpointer=_CheckpointSaver()),
    )

    assert recovered == []
    assert launched == []
    assert store.get_run(run_id)["state"] == "waiting_for_human"


@pytest.mark.asyncio
async def test_resolved_hitl_recovery_uses_persisted_command_not_fresh_prompt(tmp_path, monkeypatch):
    from munin.production import chat
    from munin.production.store import ProductionStore

    store = ProductionStore.for_sqlite(tmp_path / "approved.sqlite", master_key=b"a" * 32)
    operator, conversation, turn = _queued_run(store, key="approved-human")
    run_id = turn["run"]["id"]
    chat._claim_direct(store, run_id=run_id)
    request = store.request_human_decision(
        run_id=run_id,
        action="approve scan",
        risk="high",
        evidence=["scope checked"],
        scope={"actions": [{"name": "nmap_scan", "args": {"target": "example.test"}}]},
        choices=["approve", "reject"],
    )
    store.resolve_human_decision(
        actor_id=operator["id"], request_id=request["id"], choice="approve", nonce=request["nonce"]
    )

    launched: list[dict] = []
    monkeypatch.setattr(chat, "_launch_chat_run", lambda **kwargs: launched.append(kwargs))
    recovered = await chat.recover_persisted_chat_runs(
        store=store,
        shared_state=SimpleNamespace(langgraph_checkpointer=_CheckpointSaver()),
    )

    assert recovered == [run_id]
    assert len(launched) == 1
    launch = launched[0]
    assert launch["conversation_id"] == conversation["id"]
    assert launch["prompt"] == "Resume the durable operation"
    assert launch["resume_from_checkpoint"] is False
    assert launch["resume_decisions"] == [{"type": "approve"}]
    event = next(event for event in store.list_run_events(run_id) if event["kind"] == "human_request.resolved")
    assert event["payload"] == {
        "human_request_id": request["id"],
        "request_id": request["id"],
        "choice": "approve",
        "resolution": "approved",
        "tool_name": "nmap_scan",
        "args": {"actions": [{"name": "nmap_scan", "args": {"target": "example.test"}}]},
        "guidance": "",
    }


def test_lease_heartbeat_refuses_cancelled_or_fenced_owner(tmp_path):
    from munin.production import chat
    from munin.production.store import ProductionStore

    store = ProductionStore.for_sqlite(tmp_path / "heartbeat.sqlite", master_key=b"l" * 32)
    operator, _conversation, turn = _queued_run(store, key="lease-heartbeat")
    run_id = turn["run"]["id"]
    lease_token, _ = chat._claim_direct(store, run_id=run_id)

    assert store.renew_run_lease(run_id=run_id, lease_token=lease_token, lease_seconds=60)
    wrong_token = f"{lease_token}-stale"
    assert not store.renew_run_lease(run_id=run_id, lease_token=wrong_token, lease_seconds=60)
    store.request_run_cancellation(actor_id=operator["id"], run_id=run_id)
    assert not store.renew_run_lease(run_id=run_id, lease_token=lease_token, lease_seconds=60)


@pytest.mark.asyncio
async def test_runtime_checkpoint_recovery_uses_no_new_human_message(monkeypatch):
    """``None`` tells LangGraph to continue the saved thread, not append input."""
    from munin.core import supervisor as supervisor_module
    from munin.core.runtime_adapter import supervisor_runner

    seen: list[object] = []

    class _Graph:
        async def astream_events(self, input_value, *, config, version):  # noqa: ANN001, ARG002
            seen.append(input_value)
            if False:  # pragma: no cover - establishes this as an async iterator
                yield {}

    monkeypatch.setattr(supervisor_module, "build_munin_supervisor", lambda **_: _Graph())
    events = [
        event
        async for event in supervisor_runner(
            "must not be appended again",
            run_id="run_recovery",
            conversation_id="conversation_recovery",
            store=object(),
            resume_from_checkpoint=True,
        )
    ]

    assert events == []
    assert seen == [None]
