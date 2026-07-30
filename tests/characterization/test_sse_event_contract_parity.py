"""Characterization tests for SSE event shapes and silence detection.

These are schema-validation and state-machine tests against the expected
SSE event contract. They use mock/stub SSE streams — no real HTTP.

Targets future munin.production.asgi module. Tests skip gracefully until
that module exists.

Expected SSE event schemas:
  run_state:    {kind, run_id, state, timestamp}
  reasoning:    {kind, run_id, text}
  tool_intent:  {kind, run_id, tool_name, tool_call_id, input}

Silence detector states: connecting → live → stale → closed
Silence threshold: 45 seconds without events → stale
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------
try:
    import munin.production.asgi as asgi_mod  # type: ignore[import]
    _ASGI_AVAILABLE = True
except ImportError:
    _ASGI_AVAILABLE = False
    asgi_mod = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Stub SSE event helpers (used regardless of asgi_mod availability)
# ---------------------------------------------------------------------------

def _make_run_state_event(run_id: str, state: str) -> dict[str, Any]:
    """Construct a canonical run_state SSE event dict."""
    return {
        "kind": "run_state",
        "run_id": run_id,
        "state": state,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _make_reasoning_event(run_id: str, text: str) -> dict[str, Any]:
    """Construct a canonical reasoning SSE event dict."""
    return {
        "kind": "reasoning",
        "run_id": run_id,
        "text": text,
    }


def _make_tool_intent_event(run_id: str, tool_name: str, tool_call_id: str, input_args: dict[str, Any]) -> dict[str, Any]:
    """Construct a canonical tool_intent SSE event dict."""
    return {
        "kind": "tool_intent",
        "run_id": run_id,
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "input": input_args,
    }


# ---------------------------------------------------------------------------
# Schema validation helpers
# ---------------------------------------------------------------------------

def _validate_run_state_schema(event: dict[str, Any]) -> None:
    required = {"kind", "run_id", "state", "timestamp"}
    missing = required - set(event.keys())
    assert not missing, f"run_state event missing fields: {missing}"
    assert event["kind"] == "run_state"
    assert isinstance(event["run_id"], str) and event["run_id"]
    assert isinstance(event["state"], str) and event["state"]
    # Timestamp should be parseable ISO format
    datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))


def _validate_reasoning_schema(event: dict[str, Any]) -> None:
    required = {"kind", "run_id", "text"}
    missing = required - set(event.keys())
    assert not missing, f"reasoning event missing fields: {missing}"
    assert event["kind"] == "reasoning"
    assert isinstance(event["run_id"], str) and event["run_id"]
    assert isinstance(event["text"], str)


def _validate_tool_intent_schema(event: dict[str, Any]) -> None:
    required = {"kind", "run_id", "tool_name", "tool_call_id", "input"}
    missing = required - set(event.keys())
    assert not missing, f"tool_intent event missing fields: {missing}"
    assert event["kind"] == "tool_intent"
    assert isinstance(event["run_id"], str) and event["run_id"]
    assert isinstance(event["tool_name"], str) and event["tool_name"]
    assert isinstance(event["tool_call_id"], str) and event["tool_call_id"]
    assert isinstance(event["input"], dict)


# ---------------------------------------------------------------------------
# Tests — Schema Validation (pure, no asgi needed)
# ---------------------------------------------------------------------------

def test_run_state_event_schema() -> None:
    """run_state event has required fields: kind, run_id, state, timestamp."""
    event = _make_run_state_event(run_id="run-001", state="running")
    _validate_run_state_schema(event)


def test_reasoning_event_schema() -> None:
    """reasoning event has required fields: kind, run_id, text."""
    event = _make_reasoning_event(run_id="run-001", text="Analyzing LDAP structure...")
    _validate_reasoning_schema(event)


def test_tool_intent_event_schema() -> None:
    """tool_intent event has required fields: kind, run_id, tool_name, tool_call_id, input."""
    event = _make_tool_intent_event(
        run_id="run-001",
        tool_name="ldap_search",
        tool_call_id="call_abc123",
        input_args={"filter": "(cn=admin)", "base_dn": "dc=test,dc=com"},
    )
    _validate_tool_intent_schema(event)


def test_run_state_event_json_serializable() -> None:
    """SSE events must be JSON-serializable (no datetime objects, only strings)."""
    events = [
        _make_run_state_event("run-1", "pending"),
        _make_reasoning_event("run-1", "some text"),
        _make_tool_intent_event("run-1", "echo", "call_1", {"x": "y"}),
    ]
    for event in events:
        serialized = json.dumps(event)  # must not raise
        assert serialized  # non-empty
        recovered = json.loads(serialized)
        assert recovered["kind"] == event["kind"]


# ---------------------------------------------------------------------------
# Silence Detector state machine (stub implementation)
# ---------------------------------------------------------------------------

class _SilenceDetector:
    """Minimal stub silence detector for testing state transitions.

    States: connecting → live → stale → closed
    Transitions:
      - Any event received while connecting → live
      - Any event received while live → stay live (reset timer)
      - 45s without event while live → stale
      - Explicit close() → closed
    """

    STALE_AFTER_SECONDS = 45

    def __init__(self) -> None:
        self.state = "connecting"
        self._last_event_at: float | None = None

    def on_event(self, event: dict[str, Any]) -> None:
        self._last_event_at = time.monotonic()
        if self.state in ("connecting", "live"):
            self.state = "live"

    def check_stale(self, now: float | None = None) -> None:
        """Call periodically to detect silence."""
        if self.state != "live":
            return
        t = now if now is not None else time.monotonic()
        if self._last_event_at is not None and (t - self._last_event_at) >= self.STALE_AFTER_SECONDS:
            self.state = "stale"

    def close(self) -> None:
        self.state = "closed"


def test_silence_detector_states() -> None:
    """Silence detector transitions: connecting → live → stale → closed."""
    detector = _SilenceDetector()
    assert detector.state == "connecting"

    # Receive an event → go live
    detector.on_event({"kind": "run_state", "run_id": "r1", "state": "running", "timestamp": "2026-01-01T00:00:00Z"})
    assert detector.state == "live"

    # Simulate 45s of silence by faking the clock
    fake_last_event = time.monotonic() - 46
    detector._last_event_at = fake_last_event
    detector.check_stale(now=time.monotonic())
    assert detector.state == "stale"

    # Close → closed
    detector.close()
    assert detector.state == "closed"


def test_45s_silence_to_stale() -> None:
    """After 45s without events, detector state becomes stale."""
    detector = _SilenceDetector()
    detector.on_event({"kind": "reasoning", "run_id": "r1", "text": "hello"})
    assert detector.state == "live"

    # Simulate exactly 44s — should still be live
    detector._last_event_at = time.monotonic() - 44
    detector.check_stale(now=time.monotonic())
    assert detector.state == "live", "44s should not trigger stale"

    # Simulate exactly 45s — should become stale
    detector._last_event_at = time.monotonic() - 45
    detector.check_stale(now=time.monotonic())
    assert detector.state == "stale", "45s should trigger stale"


def test_last_event_id_resume() -> None:
    """Last-Event-ID header is used to resume SSE from the correct position.

    This is a contract test. We document that the SSE stream should support
    the Last-Event-ID HTTP header for reconnection. The stub here validates
    the event ID format expected by the server.
    """
    # SSE events that include an 'id' field can be resumed
    event_with_id = {
        "id": "run-001:42",        # format: {run_id}:{sequence}
        "kind": "reasoning",
        "run_id": "run-001",
        "text": "Step 42 reasoning",
    }

    # The id field should be parseable as "{run_id}:{sequence}"
    event_id = event_with_id.get("id", "")
    assert ":" in event_id, "Event ID should follow '{run_id}:{sequence}' format"
    parts = event_id.split(":", 1)
    run_id_part, seq_part = parts
    assert run_id_part == "run-001"
    assert seq_part.isdigit(), f"Sequence part should be numeric, got: {seq_part!r}"


# ---------------------------------------------------------------------------
# Integration-level tests (require asgi_mod — xfail if absent)
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    not _ASGI_AVAILABLE,
    reason="munin.production.asgi not implemented yet",
    strict=False,
)
def test_asgi_run_state_event_emitted() -> None:
    """ASGI app emits a run_state event when run state changes.

    Validates that the SSE endpoint produces events matching the schema.
    Skipped until munin.production.asgi is implemented.
    """
    assert asgi_mod is not None, "asgi module required"

    # When asgi is available, test that it emits structured events.
    # This is a placeholder that documents the expected interface.
    raise NotImplementedError("asgi SSE integration test not yet implemented")


@pytest.mark.xfail(
    not _ASGI_AVAILABLE,
    reason="munin.production.asgi not implemented yet",
    strict=False,
)
def test_asgi_silence_detector_integration() -> None:
    """ASGI silence detector is wired to the SSE stream.

    After 45s without events, the ASGI layer should transition to stale state
    and potentially send a keepalive or signal the client.
    """
    assert asgi_mod is not None, "asgi module required"
    raise NotImplementedError("asgi silence detector integration not yet implemented")
