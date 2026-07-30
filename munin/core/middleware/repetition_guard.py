from __future__ import annotations
from typing import Any, Callable
from dataclasses import dataclass, field


class RepetitionGuardTripped(Exception):
    """Raised when the agent is stuck in an unresolvable repetition loop."""
    pass


class RepetitionGuardMiddleware:
    """
    Detects repetition loops in the assistant output stream.

    Window: last window_size assistant messages.
    If unique content count < min_unique → nudge once.
    If still repeating after nudge → raise RepetitionGuardTripped.
    """

    NUDGE_MESSAGE = (
        "You seem to be repeating yourself. Try a different approach: "
        "use a different tool, reconsider your reasoning, or ask for clarification."
    )

    def __init__(self, window_size: int = 6, min_unique: int = 3, max_iterations: int = 1000):
        self.window_size = window_size
        self.min_unique = min_unique
        self.max_iterations = max_iterations
        self._iteration_count = 0
        self._nudge_issued = False
        self._recent_messages: list[str] = []

    async def __call__(self, state: dict, next_fn: Callable) -> dict:
        self._iteration_count += 1
        self._update_window(state)

        if self._is_repeating():
            if not self._nudge_issued:
                self._nudge_issued = True
                state = self._inject_nudge(state)
            else:
                raise RepetitionGuardTripped(
                    f"Agent stuck after {self._iteration_count} iterations with "
                    f"{len(set(self._recent_messages))} unique messages in last {self.window_size}"
                )

        return await next_fn(state)

    def _update_window(self, state: dict) -> None:
        messages = state.get("messages", [])
        # Collect assistant/AI message content
        for msg in reversed(messages):
            content = getattr(msg, "content", None)
            if content and hasattr(msg, "type") and msg.type == "ai":
                self._recent_messages.append(str(content)[:200])  # fingerprint
                break
        # Trim window
        self._recent_messages = self._recent_messages[-self.window_size:]

    def _is_repeating(self) -> bool:
        if self._iteration_count <= self.window_size:
            return False
        if len(self._recent_messages) < self.window_size:
            return False
        return len(set(self._recent_messages)) < self.min_unique

    def _inject_nudge(self, state: dict) -> dict:
        from langchain_core.messages import HumanMessage
        nudge = HumanMessage(content=self.NUDGE_MESSAGE, name="system")
        return {**state, "messages": state.get("messages", []) + [nudge]}

    def reset(self) -> None:
        self._iteration_count = 0
        self._nudge_issued = False
        self._recent_messages = []
