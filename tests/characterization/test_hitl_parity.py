"""Characterization tests for Human-in-the-Loop (HITL) behavior.

Targets future dispatcher/store HITL integration. All tests are xfail until
the relevant modules are implemented.

Expected interfaces (to be implemented):
  - munin.production.store.Store.pause_for_human(run_id) → sets state to waiting_for_human
  - munin.production.store.Store.approve_tool_call(run_id, tool_args) → forwards args
  - munin.production.store.Store.reject_tool_call(run_id, rationale) → injects rationale
  - munin.production.store.Store.queue_guidance(run_id, guidance)
  - munin.production.store.RUN_STATES.waiting_for_human
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------
try:
    import munin.production.store as store_mod  # type: ignore[import]
    _STORE_AVAILABLE = True
except ImportError:
    _STORE_AVAILABLE = False
    store_mod = None  # type: ignore[assignment]

pytestmark = pytest.mark.xfail(
    not _STORE_AVAILABLE,
    reason="munin.production.store HITL interface not implemented yet (PR-01 characterization)",
    strict=False,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store(tmp_path: Path) -> Any:
    return store_mod.Store(tmp_path / "hitl.db")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_hitl_pause_sets_waiting_state(tmp_path: Path) -> None:
    """Pausing a run sets its state to waiting_for_human.

    After pause_for_human(), the run state must be retrievable as
    'waiting_for_human' (or equivalent enum member).
    """
    store = _make_store(tmp_path)
    run_id = store.create_run(goal="hitl pause test")

    store.set_run_state(run_id, "running")
    store.pause_for_human(run_id)

    state = str(store.get_run_state(run_id)).lower()
    assert "waiting" in state or "human" in state, (
        f"State after pause should contain 'waiting' or 'human', got: {state}"
    )
    store.close()


def test_hitl_approve_forwards_args(tmp_path: Path) -> None:
    """Approved tool args are correctly forwarded into the run context.

    After approve_tool_call(), the approved args should be retrievable
    so the agent can re-enter the loop with the human-approved parameters.
    """
    store = _make_store(tmp_path)
    run_id = store.create_run(goal="hitl approve test")
    store.pause_for_human(run_id)

    approved_args = {"tool_name": "ldap_search", "filter": "(cn=admin)", "base_dn": "dc=test,dc=com"}
    store.approve_tool_call(run_id, approved_args)

    # The approved args should be visible for the agent to consume
    pending = store.get_pending_hitl_action(run_id)
    assert pending is not None, "Approved action should be pending for agent to pick up"
    assert pending.get("decision") == "approved"
    assert pending.get("tool_args") == approved_args or pending.get("args") == approved_args
    store.close()


def test_hitl_reject_injects_rationale(tmp_path: Path) -> None:
    """Rejected tool call injects rationale into the next agent iteration.

    After reject_tool_call(), the rationale is stored so the agent sees it
    as feedback in its next reasoning step.
    """
    store = _make_store(tmp_path)
    run_id = store.create_run(goal="hitl reject test")
    store.pause_for_human(run_id)

    rationale = "This LDAP filter is too broad — narrow to specific OU"
    store.reject_tool_call(run_id, rationale=rationale)

    pending = store.get_pending_hitl_action(run_id)
    assert pending is not None
    assert pending.get("decision") == "rejected"

    # Rationale should be accessible
    stored_rationale = pending.get("rationale") or pending.get("reason") or ""
    assert rationale in stored_rationale or stored_rationale in rationale, (
        f"Expected rationale to be stored, got: {stored_rationale!r}"
    )
    store.close()


def test_guidance_queued_visible_at_boundary(tmp_path: Path) -> None:
    """Guidance queued after pause is visible at the next agent step boundary.

    queue_guidance() appends guidance that the agent reads at the start of
    its next iteration (at the iteration boundary, not mid-step).
    """
    store = _make_store(tmp_path)
    run_id = store.create_run(goal="guidance queue test")

    guidance_text = "Focus on Domain Admins group — ignore service accounts"
    store.queue_guidance(run_id, guidance=guidance_text)

    # Guidance should be retrievable
    pending_guidance = store.get_queued_guidance(run_id)
    assert pending_guidance, "Should have at least one queued guidance item"

    texts = [g.get("guidance") or g.get("text") or g for g in pending_guidance]
    found = any(guidance_text in str(t) for t in texts)
    assert found, f"Queued guidance not found. Got: {texts}"
    store.close()


def test_hitl_state_resumes_after_approval(tmp_path: Path) -> None:
    """After approval, run state can transition back to 'running'.

    The HITL flow: running → waiting_for_human → (approve) → running.
    """
    store = _make_store(tmp_path)
    run_id = store.create_run(goal="resume test")
    store.set_run_state(run_id, "running")
    store.pause_for_human(run_id)

    # Approve and resume
    store.approve_tool_call(run_id, {"tool_name": "safe_tool", "arg": "value"})
    store.set_run_state(run_id, "running")

    state = str(store.get_run_state(run_id)).lower()
    assert "running" in state, f"After approval+resume, state should be running, got: {state}"
    store.close()
