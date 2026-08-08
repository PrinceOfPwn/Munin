# tags: [orchestrator, wake, supervisor-v2, regression, idempotency]
"""Regression coverage for the supervisor_v2 wake contract."""

from __future__ import annotations

import pytest

from munin.core.orchestrator import Orchestrator


class _WakeState:
    def __init__(self, *, enqueue_error: Exception | None = None, presence_error: Exception | None = None) -> None:
        self.enqueue_error = enqueue_error
        self.presence_error = presence_error
        self.enqueued: list[dict] = []
        self.presence: list[dict] = []
        self.spawn_slot_calls = 0

    def enqueue_wake(self, **kwargs):
        if self.enqueue_error is not None:
            raise self.enqueue_error
        self.enqueued.append(dict(kwargs))
        return 41

    def try_claim_spawn_slot(self, **kwargs):  # pragma: no cover - must stay unused
        self.spawn_slot_calls += 1
        raise AssertionError("supervisor_v2 must not claim a subprocess spawn slot")

    def upsert_presence(self, **kwargs):
        self.presence.append(dict(kwargs))
        if self.presence_error is not None:
            raise self.presence_error
        return kwargs


def test_wake_enqueue_failure_is_the_only_raised_failure() -> None:
    state = _WakeState(enqueue_error=RuntimeError("db unavailable"))

    with pytest.raises(RuntimeError, match="db unavailable"):
        Orchestrator(state).wake("worker", {"objective": "x"})

    assert state.spawn_slot_calls == 0
    assert state.presence == []


def test_wake_presence_failure_preserves_successful_queue_identity() -> None:
    state = _WakeState(presence_error=RuntimeError("presence unavailable"))

    result = Orchestrator(state).wake("worker", {"objective": "x"})

    assert result["wake_id"] == 41
    assert result["queued"] is True
    assert result["spawned"] is False
    assert result["presence_updated"] is False
    assert result["warning"]["code"] == "presence_update_failed"
    assert "presence unavailable" in result["warning"]["message"]
    assert state.spawn_slot_calls == 0


def test_wake_supervisor_v2_never_claims_legacy_spawn_slot() -> None:
    state = _WakeState()

    result = Orchestrator(state).wake("worker", {"objective": "x"}, priority=7)

    assert result == {
        "wake_id": 41,
        "target_agent": "worker",
        "pid": None,
        "spawned": False,
        "queued": True,
        "presence_updated": True,
        "reason": "supervisor_v2_wake_path",
    }
    assert state.enqueued == [
        {"target_agent": "worker", "task": {"objective": "x"}, "priority": 7}
    ]
    assert state.spawn_slot_calls == 0
