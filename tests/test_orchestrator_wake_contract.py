# tags: [orchestrator, core, subagent, wake, supervisor_v2, contract, regression]
"""Orchestrator.wake supervisor_v2 contract tests.

PR-0 (plan18) pinned the wake path: `munin.subagents.runner` does not ship in
v1.0.0, so the winner branch must NOT spawn a subprocess. It releases the
claim slot to IDLE presence and reports ``spawned=False`` with
``reason="supervisor_v2_wake_path"``.

These tests anchor the EXPECTED behavior of the current runtime, as opposed to
the legacy supervisor_v1 spawn behavior documented in
``Orchestrator._spawn_runner``.
"""

from __future__ import annotations

import os

from munin.core.orchestrator import Orchestrator


def test_wake_winner_returns_supervisor_v2_contract(store) -> None:
    orch = Orchestrator(store)
    result = orch.wake(
        "ldap_agent",
        {"action": "get_user_groups", "username": "neji"},
        priority=0,
    )

    assert result["target_agent"] == "ldap_agent"
    assert result["spawned"] is False
    assert result["pid"] is None
    assert result["reason"] == "supervisor_v2_wake_path"
    assert isinstance(result["wake_id"], int) and result["wake_id"] > 0

    # The claim slot was released to IDLE presence (no lease, no subprocess).
    presence = {p["agent_name"]: p for p in store.list_presence(stale_after_seconds=1800)}
    assert presence["ldap_agent"]["status"] == "IDLE"
    assert presence["ldap_agent"]["role"] == ""
    assert presence["ldap_agent"]["current_task_id"] is None


def test_wake_never_calls_legacy_spawn_runner(store, monkeypatch) -> None:
    """Regression: supervisor_v2 wake must not fall back to spawning a runner."""
    orch = Orchestrator(store)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("legacy _spawn_runner must not be invoked on the supervisor_v2 wake path")

    monkeypatch.setattr(orch, "_spawn_runner", fail_if_called)

    result = orch.wake("recon_agent", {"task": "fingerprint"}, priority=1)
    assert result["spawned"] is False
    assert result["reason"] == "supervisor_v2_wake_path"


def test_wake_live_runner_returns_existing_pid(store) -> None:
    """A live IDLE presence still suppresses spawning (store-level claim wins)."""
    orch = Orchestrator(store)
    first = orch.wake("ldap_agent", {"action": "probe"}, priority=0)
    second = orch.wake("ldap_agent", {"action": "probe"}, priority=0)

    # Both enqueued, no spawn in either call; the second observes the first's
    # presence via try_claim_spawn_slot and returns without a reason marker.
    assert first["spawned"] is False
    assert second["spawned"] is False
    assert second["target_agent"] == "ldap_agent"
