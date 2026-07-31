"""
Repetition guard middleware — real LangChain 1.x ``AgentMiddleware``.

Anti-runaway safety net (issue #9: observable + configurable, NOT a product
cap).  After each model call it fingerprints the latest AI message; when the
last ``window_size`` outputs contain fewer than ``min_unique`` unique
fingerprints it nudges once, then trips with ``RepetitionGuardTripped`` so the
operator sees an explicit, attributable failure instead of a silent loop.

Thresholds are constructor/env-configurable (``MUNIN_REPEAT_WINDOW``,
``MUNIN_REPEAT_MIN_UNIQUE``).
"""
from __future__ import annotations

import os
from typing import Any

from langchain_core.messages import HumanMessage

try:
    from langchain.agents.middleware import AgentMiddleware
except ImportError:  # pragma: no cover
    class AgentMiddleware:  # type: ignore[no-redef]
        pass


class RepetitionGuardTripped(Exception):
    """Raised when the agent is stuck in an unresolvable repetition loop."""


class RepetitionGuardMiddleware(AgentMiddleware):
    NUDGE_MESSAGE = (
        "You seem to be repeating yourself. Try a different approach: "
        "use a different tool, reconsider your reasoning, or ask for clarification."
    )

    def __init__(
        self,
        window_size: int | None = None,
        min_unique: int | None = None,
    ):
        self.window_size = window_size or int(os.environ.get("MUNIN_REPEAT_WINDOW", "6"))
        self.min_unique = min_unique or int(os.environ.get("MUNIN_REPEAT_MIN_UNIQUE", "3"))
        self._recent: list[str] = []
        self._nudge_issued = False

    # ------------------------------------------------------------------

    def _track(self, state: dict) -> dict | None:
        messages = state.get("messages", [])
        fingerprint = None
        for msg in reversed(messages):
            if getattr(msg, "type", None) == "ai":
                fingerprint = str(getattr(msg, "content", ""))[:200]
                break
        if fingerprint is not None:
            self._recent.append(fingerprint)
            self._recent = self._recent[-self.window_size:]

        if len(self._recent) < self.window_size:
            return None
        if len(set(self._recent)) >= self.min_unique:
            return None

        if not self._nudge_issued:
            self._nudge_issued = True
            return {"messages": [HumanMessage(content=self.NUDGE_MESSAGE, name="system")]}

        raise RepetitionGuardTripped(
            f"Agent stuck: {len(set(self._recent))} unique outputs in last "
            f"{self.window_size} model calls"
        )

    # -- LangChain hooks ---------------------------------------------------

    def after_model(self, state: dict, runtime: Any) -> dict | None:
        return self._track(state)

    async def aafter_model(self, state: dict, runtime: Any) -> dict | None:
        return self._track(state)
