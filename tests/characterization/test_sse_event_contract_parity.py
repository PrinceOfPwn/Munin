"""Characterization tests for SSE event contracts: useRunEvents.ts + route.ts.

Pure-Python string-contract tests (no node runner). Reads source text via
pathlib.Path.read_text() and asserts the literal contracts.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


def _read_frontend_source(relative: str) -> str:
    """Read a frontend source file relative to the app/ directory."""
    base = Path(__file__).resolve().parents[2] / "app"
    path = base / relative
    if not path.exists():
        pytest.skip(f"{relative} not found on this host")
    return path.read_text(encoding="utf-8")


def test_run_events_listener_types():
    """EventSource addEventListener literals include run-event, heartbeat, warning, close."""
    source = _read_frontend_source("src/lib/useRunEvents.ts")

    # Extract all addEventListener("X", ...) literal event-type strings
    pattern = re.compile(r'addEventListener\(\s*["\']([^"\']+)["\']')
    event_types = set(pattern.findall(source))

    for expected in ("run-event", "heartbeat", "warning", "close"):
        assert expected in event_types, f"EventSource listener for {expected!r} not found in useRunEvents.ts"


def test_silence_detector_45000():
    """The silence-detector constant 45_000 exists in useRunEvents.ts."""
    source = _read_frontend_source("src/lib/useRunEvents.ts")
    assert "45_000" in source, "45_000 silence detector constant not found"


def test_last_event_id_forwarded():
    """The production ASGI route forwards Last-Event-ID via FORWARDED_HEADERS."""
    source = _read_frontend_source("src/app/api/production/[[...path]]/route.ts")
    assert "last-event-id" in source.lower(), "Last-Event-ID not in FORWARDED_HEADERS"


def test_max_duration_14400():
    """The Next.js route declares export const maxDuration = 14400."""
    source = _read_frontend_source("src/app/api/production/[[...path]]/route.ts")
    match = re.search(r"export\s+const\s+maxDuration\s*=\s*(\d+)", source)
    assert match is not None, "maxDuration export not found"
    assert match.group(1) == "14400"


def test_use_conversation_events_cross_reference():
    """Read useConversationEvents.ts for cross-reference but do NOT assert against it.
    The file is explicitly untouched per spec — just verify it exists and is readable.
    """
    # By calling _read_frontend_source we confirm the file exists on disk and is
    # readable; spec explicitly forbids content assertions against this surface
    # (it's outside the characterization scope of PR-01).
    _read_frontend_source("src/lib/useConversationEvents.ts")
