"""
Progress emission middleware — real LangChain 1.x ``AgentMiddleware``.

Emits Munin progress event dicts through ``progress_sink`` for tool lifecycle
(``tool_intent`` / ``tool_result``).  Token-level text streaming is *not* a
middleware concern: ``munin.core.runtime_adapter`` translates
``astream_events`` chunks for that; the two channels share the same envelope
format consumed by the SSE/BFF layer.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from ...mcp.audit import redact_secrets  # noqa: TID252

logger = logging.getLogger(__name__)

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


class ProgressEmitMiddleware(AgentMiddleware):
    """Wrap every tool call with progress emission (non-blocking, best-effort)."""

    def __init__(self, progress_sink: Callable[[dict], None], run_id: str):
        self.progress_sink = progress_sink
        self.run_id = run_id

    def _emit(self, event: dict) -> None:
        try:
            self.progress_sink(event)
        except Exception:  # noqa: BLE001 - observability must never sink a run
            logger.debug("progress sink raised", exc_info=True)

    def _before(self, request: Any) -> str:
        name, args, call_id = _tool_request_parts(request)
        self._emit(
            {
                "kind": "tool_intent",
                "run_id": self.run_id,
                "tool_name": name,
                "tool_call_id": call_id,
                "input": _deep_redact(args),
            }
        )
        return call_id

    def _after(self, call_id: str, result: Any, error: Exception | None = None) -> None:
        if error is not None:
            self._emit(
                {
                    "kind": "tool_failed",
                    "run_id": self.run_id,
                    "tool_call_id": call_id,
                    "error": redact_secrets(f"{type(error).__name__}: {error}"),
                }
            )
            return
        redacted_result = _deep_redact(result)
        output = redacted_result if isinstance(redacted_result, str) else repr(redacted_result)[:4000]
        self._emit(
            {
                "kind": "tool_result",
                "run_id": self.run_id,
                "tool_call_id": call_id,
                "output": output,
            }
        )

    # -- LangChain hooks -------------------------------------------------

    def wrap_tool_call(self, request: Any, handler: Callable) -> Any:
        call_id = self._before(request)
        try:
            result = handler(request)
        except Exception as exc:  # noqa: BLE001
            self._after(call_id, None, error=exc)
            raise
        self._after(call_id, result)
        return result

    async def awrap_tool_call(self, request: Any, handler: Callable) -> Any:
        call_id = self._before(request)
        try:
            result = await handler(request)
        except Exception as exc:  # noqa: BLE001
            self._after(call_id, None, error=exc)
            raise
        self._after(call_id, result)
        return result
