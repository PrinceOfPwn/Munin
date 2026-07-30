"""Request-scoped live telemetry for OpenAI-compatible model streams.

The production dispatcher installs a callback while a ReAct run is executing.
:class:`munin.core.llm_client.LLMClient` emits provider-supplied reasoning deltas
and assistant text deltas through this scope.  The callback is intentionally
best-effort and does not become part of Munin's durable memory by itself.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger("munin.llm.stream")

_StreamCallback = Callable[[dict[str, Any]], None]
_callback: ContextVar[_StreamCallback | None] = ContextVar("munin_llm_stream_callback", default=None)


@contextmanager
def llm_stream_scope(callback: _StreamCallback | None) -> Iterator[None]:
    """Install a live-stream observer for the current execution context."""

    token = _callback.set(callback)
    try:
        yield
    finally:
        _callback.reset(token)


def has_llm_stream_observer() -> bool:
    """Return whether the current request has a live observer attached."""

    return _callback.get() is not None


def emit_llm_stream(event: dict[str, Any]) -> None:
    """Deliver one live event without allowing telemetry to break execution."""

    callback = _callback.get()
    if callback is None:
        return
    try:
        callback(event)
    except Exception:  # pragma: no cover - observer failures are non-fatal
        logger.debug("LLM stream observer failed", exc_info=True)


__all__ = ["emit_llm_stream", "has_llm_stream_observer", "llm_stream_scope"]
