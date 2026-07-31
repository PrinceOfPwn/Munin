"""Operator-safe progress events shared by long-running Munin tools.

This module deliberately carries lifecycle milestones only.  It is not a
channel for private model reasoning or hidden chain-of-thought.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any


_progress_callback: ContextVar[Callable[[dict[str, Any]], None] | None] = ContextVar(
    "munin_tool_progress_callback",
    default=None,
)


@contextmanager
def tool_progress_scope(callback: Callable[[dict[str, Any]], None] | None) -> Iterator[None]:
    """Expose one request's operator-safe progress callback to nested tools."""
    token = _progress_callback.set(callback)
    try:
        yield
    finally:
        _progress_callback.reset(token)


def emit_tool_progress(event: dict[str, Any]) -> None:
    """Best-effort emission; progress must never interrupt the real work."""
    callback = _progress_callback.get()
    if callback is None:
        return
    try:
        callback(dict(event))
    except Exception:
        return
