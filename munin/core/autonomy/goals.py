# tags: [core, orchestrator, persistence, sqlite, langgraph, GoalMiddleware, render_goal_block, GOAL_STATE, ACTIVE_GOAL, operator-objective, system-prompt-injection, goals-table, new_goal_id, AgentMiddleware, success_criteria]
"""
Persistent Goal — operator-owned objective with durable state.

A Goal lives in the production store (``goals`` table), survives refresh,
restart and reconnect, and is re-injected into the model context on every
model call while a GOAL-mode run is active (``ACTIVE_GOAL`` contextvar set by
the runtime adapter).  The operator owns the goal (create, update, pause,
complete via the API); the agent reflects progress through the durable plan
(``munin.core.autonomy.planning``).

Security: a goal never widens scope — its ``scope`` dict is advisory for the
model and the conversation's existing authorization boundaries still apply.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Literal

from langchain_core.messages import SystemMessage

from .context import ACTIVE_GOAL  # noqa: TID252

logger = logging.getLogger(__name__)

GOAL_STATE = Literal["pending", "active", "completed", "failed", "paused"]

try:  # LangChain 1.x middleware surface
    from langchain.agents.middleware import AgentMiddleware
except ImportError:  # pragma: no cover - older langchain
    class AgentMiddleware:  # type: ignore[no-redef]
        pass


def new_goal_id() -> str:
    return "goal_" + uuid.uuid4().hex[:12]


def render_goal_block(goal: dict | None) -> str:
    """System-prompt block for one model call: the persistent goal contract."""
    if not goal:
        return ""
    lines = [
        "## Persistent goal (operator-owned, survives restart)",
        f"Objective: {goal.get('objective') or ''}",
    ]
    criteria = goal.get("success_criteria") or []
    if criteria:
        lines.append("Success criteria: " + "; ".join(str(c) for c in criteria))
    scope = goal.get("scope") or {}
    if scope:
        lines.append("Authorized scope: " + str(scope))
    budget = goal.get("budget") or {}
    if budget:
        lines.append("Budget: " + str(budget))
    deadline = goal.get("deadline_ms")
    if deadline:
        remaining = max(0, int(deadline) - int(time.time() * 1000))
        lines.append(f"Deadline: {remaining // 1000}s remaining")
    lines.append(f"Goal state: {goal.get('state') or 'active'}")
    return "\n".join(lines)


class GoalMiddleware(AgentMiddleware):
    """Inject the persistent goal contract into every model call.

    Constructor args are build-time fallbacks; the live goal comes from the
    ``ACTIVE_GOAL`` contextvar set per invocation by
    ``runtime_adapter.supervisor_runner`` (cached-graph-safe).
    """

    def __init__(self, goal: dict | None = None):
        super().__init__()
        self._goal_fallback = goal

    def _resolve_goal(self) -> dict | None:
        live = ACTIVE_GOAL.get()
        return live if live is not None else self._goal_fallback

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        new_system = self._compose_system_message(request)
        if new_system is None:
            return handler(request)
        return handler(request.override(system_message=new_system))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        new_system = self._compose_system_message(request)
        if new_system is None:
            return await handler(request)
        return await handler(request.override(system_message=new_system))

    def _compose_system_message(self, request: Any) -> SystemMessage | None:
        block = render_goal_block(self._resolve_goal())
        if not block:
            return None
        extra = {"type": "text", "text": f"\n\n{block}"}
        system_message = getattr(request, "system_message", None)
        if system_message is not None:
            content = list(getattr(system_message, "content_blocks", None) or [])
            if isinstance(getattr(system_message, "content", None), list):
                content = list(system_message.content)
            elif isinstance(getattr(system_message, "content", None), str):
                content = [{"type": "text", "text": system_message.content}]
            content = [*content, extra]
        else:
            content = [extra]
        return SystemMessage(content=content)


__all__ = ["GoalMiddleware", "render_goal_block", "new_goal_id", "GOAL_STATE"]
