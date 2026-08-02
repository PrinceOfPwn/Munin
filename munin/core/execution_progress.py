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
_tool_name: ContextVar[str | None] = ContextVar("munin_active_tool_name", default=None)
_tool_call_id: ContextVar[str | None] = ContextVar("munin_active_tool_call_id", default=None)


@contextmanager
def tool_progress_scope(callback: Callable[[dict[str, Any]], None] | None) -> Iterator[None]:
    """Expose one request's operator-safe progress callback to nested tools."""
    token = _progress_callback.set(callback)
    try:
        yield
    finally:
        _progress_callback.reset(token)


@contextmanager
def tool_call_scope(tool_name: str, tool_call_id: str) -> Iterator[None]:
    """Expose the active tool identity to nested process/job adapters."""
    name_token = _tool_name.set(tool_name)
    call_token = _tool_call_id.set(tool_call_id)
    try:
        yield
    finally:
        _tool_name.reset(name_token)
        _tool_call_id.reset(call_token)


def active_tool_identity() -> tuple[str, str]:
    """Return the current graph tool identity, if one is bound."""
    return _tool_name.get() or "", _tool_call_id.get() or ""


def emit_tool_progress(event: dict[str, Any]) -> None:
    """Best-effort emission; progress must never interrupt the real work."""
    callback = _progress_callback.get()
    if callback is None:
        return
    try:
        callback(dict(event))
    except Exception:
        return
