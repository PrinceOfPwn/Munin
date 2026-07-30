from __future__ import annotations
from typing import Any, Callable


class OperatorGuidanceMiddleware:
    """
    Drains pending operator guidance from the store and injects it
    as HumanMessage at the start of each graph iteration.
    """

    def __init__(self, run_id: str, store: Any):
        self.run_id = run_id
        self.store = store

    async def __call__(self, state: dict, next_fn: Callable) -> dict:
        """Called before each graph node execution to inject pending guidance."""
        # Drain guidance from store
        guidance_items = await self._drain_guidance()
        if guidance_items:
            # Inject as HumanMessage into messages
            from langchain_core.messages import HumanMessage
            injected = [
                HumanMessage(content=f"[Operator guidance]: {item['text']}",
                             name="operator")
                for item in guidance_items
            ]
            state = {**state, "messages": state.get("messages", []) + injected}
        return await next_fn(state)

    async def _drain_guidance(self) -> list[dict]:
        """Drain all pending guidance for this run from the store."""
        try:
            # Try store method if available
            if hasattr(self.store, 'drain_guidance'):
                return await self.store.drain_guidance(self.run_id)
            return []
        except Exception:
            return []

    def as_pre_iteration_hook(self) -> Callable:
        """Return a synchronous pre_iteration_hook compatible callable."""
        def hook(state: dict) -> dict:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                guidance = loop.run_until_complete(self._drain_guidance())
            except Exception:
                guidance = []
            if guidance:
                from langchain_core.messages import HumanMessage
                injected = [
                    HumanMessage(content=f"[Operator guidance]: {g['text']}", name="operator")
                    for g in guidance
                ]
                return {**state, "messages": state.get("messages", []) + injected}
            return state
        return hook
