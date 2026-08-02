"""
Progress emission middleware — real LangChain 1.x ``AgentMiddleware``.

Emits Munin progress event dicts through ``progress_sink`` for tool lifecycle
(``tool_intent`` / ``tool_result``).  Token-level text streaming is *not* a
middleware concern: ``munin.core.runtime_adapter`` translates
``astream_events`` chunks for that; the two channels share the same envelope
format consumed by the SSE/BFF layer.
"""
from __future__ import annotations

import contextvars
import json
import logging
from typing import Any, Callable

from ...mcp.audit import redact_secrets  # noqa: TID252
from ..execution_progress import tool_call_scope, tool_progress_scope

logger = logging.getLogger(__name__)

# Per-invocation overrides set by ``runtime_adapter.supervisor_runner`` so a
# process-wide cached supervisor graph can serve many runs.  Middleware always
# prefers the live override and falls back to its constructor args — which
# keeps direct unit constructions (``ProgressEmitMiddleware(run_id=...)``) green.
ACTIVE_RUN_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "munin_supervisor_active_run_id", default=None
)
ACTIVE_PROGRESS_SINK: contextvars.ContextVar[Callable[[dict], None] | None] = contextvars.ContextVar(
    "munin_supervisor_active_progress_sink", default=None
)

try:  # LangChain 1.x middleware surface
    from langchain.agents.middleware import AgentMiddleware
except ImportError:  # pragma: no cover - older langchain
    class AgentMiddleware:  # type: ignore[no-redef]
        pass


def _tool_request_parts(request: Any) -> tuple[str, dict, str]:
    """Extract (name, args, call_id) from a ToolCallRequest defensively."""
    tool_call = getattr(request, "tool_call", None) or {}
    name = tool_call.get("name") or getattr(request, "name", "unknown_tool")
    args = tool_call.get("args") or getattr(request, "args", {}) or {}
    call_id = tool_call.get("id") or getattr(request, "id", "")
    return str(name), dict(args), str(call_id)


def _deep_redact(value: Any) -> Any:
    """Recursively redact secrets, parsing JSON strings first."""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return json.dumps(redact_secrets(parsed))
        except (json.JSONDecodeError, TypeError):
            return redact_secrets(value)
    if isinstance(value, dict):
        return {k: _deep_redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_deep_redact(item) for item in value]
    return redact_secrets(value)


def _render_tool_result(result: Any) -> str:
    """Extract the human-visible payload from LangChain tool responses.

    ``StructuredTool`` normally returns a ``ToolMessage`` whose useful value
    lives in ``content`` (and, for ``content_and_artifact`` tools, in
    ``artifact``).  Falling back to ``repr(ToolMessage)`` can render an empty
    content field even when the structured result is available elsewhere.
    """
    content = getattr(result, "content", None)
    if content not in (None, "", []):
        value = content
    else:
        artifact = getattr(result, "artifact", None)
        value = artifact if artifact not in (None, "", []) else result
    if isinstance(value, str):
        return value[:8_000]
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:8_000]
    except (TypeError, ValueError):
        return repr(value)[:8_000]


class ProgressEmitMiddleware(AgentMiddleware):
    """Wrap every tool call with progress emission (non-blocking, best-effort)."""

    def __init__(self, progress_sink: Callable[[dict], None], run_id: str):
        # These are the per-build fallback. A cached graph serves many runs,
        # so the live (run_id, sink) is read from ``ACTIVE_*`` contextvars set
        # by ``runtime_adapter.supervisor_runner`` on each invocation. The
        # fallback keeps the original direct-construction contract intact
        # (see ``tests/characterization/test_progress_emit_middleware.py``).
        self.progress_sink = progress_sink
        self.run_id = run_id

    def _resolve_sink(self) -> Callable[[dict], None]:
        live = ACTIVE_PROGRESS_SINK.get()
        return live if live is not None else self.progress_sink

    def _resolve_run_id(self) -> str:
        live = ACTIVE_RUN_ID.get()
        return live if live not in (None, "") else self.run_id

    def _emit(self, event: dict) -> None:
        try:
            self._resolve_sink()(event)
        except Exception:  # noqa: BLE001 - observability must never sink a run
            logger.debug("progress sink raised", exc_info=True)

    def _before(self, request: Any) -> tuple[str, str]:
        name, args, call_id = _tool_request_parts(request)
        self._emit(
            {
                "kind": "tool_intent",
                "run_id": self._resolve_run_id(),
                "tool_name": name,
                "tool_call_id": call_id,
                "input": _deep_redact(args),
            }
        )
        return name, call_id

    def _after(self, call_id: str, name: str, result: Any, error: Exception | None = None) -> None:
        if error is not None:
            self._emit(
                {
                    "kind": "tool_failed",
                    "run_id": self._resolve_run_id(),
                    "tool_name": name,
                    "tool_call_id": call_id,
                    "error": redact_secrets(f"{type(error).__name__}: {error}"),
                }
            )
            return
        redacted_result = _deep_redact(result)
        output = _render_tool_result(redacted_result)
        self._emit(
            {
                "kind": "tool_result",
                "run_id": self._resolve_run_id(),
                "tool_name": name,
                "tool_call_id": call_id,
                "output": output,
            }
        )

    # -- LangChain hooks -------------------------------------------------

    def wrap_tool_call(self, request: Any, handler: Callable) -> Any:
        name, call_id = self._before(request)
        try:
            with tool_call_scope(name, call_id), tool_progress_scope(
                lambda event: self._emit(
                    {
                        **event,
                        "tool_name": event.get("tool_name") or name,
                        "tool_call_id": event.get("tool_call_id") or call_id,
                        "run_id": event.get("run_id") or self._resolve_run_id(),
                    }
                )
            ):
                result = handler(request)
        except Exception as exc:  # noqa: BLE001
            self._after(call_id, name, None, error=exc)
            raise
        self._after(call_id, name, result)
        return result

    async def awrap_tool_call(self, request: Any, handler: Callable) -> Any:
        name, call_id = self._before(request)
        try:
            with tool_call_scope(name, call_id), tool_progress_scope(
                lambda event: self._emit(
                    {
                        **event,
                        "tool_name": event.get("tool_name") or name,
                        "tool_call_id": event.get("tool_call_id") or call_id,
                        "run_id": event.get("run_id") or self._resolve_run_id(),
                    }
                )
            ):
                result = await handler(request)
        except Exception as exc:  # noqa: BLE001
            self._after(call_id, name, None, error=exc)
            raise
        self._after(call_id, name, result)
        return result
