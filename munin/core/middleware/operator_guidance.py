"""
Operator guidance middleware — real LangChain 1.x ``AgentMiddleware``.

Before every model call, drains pending operator guidance for this run from
the store and injects it as operator-named ``HumanMessage``s.  This replaces
the legacy dispatcher ``pre_iteration_hook`` coupling with the framework
hook, and keeps guidance auditable (the store write is the audit record).

The middleware is a no-op unless the store exposes ``drain_guidance(run_id)``
(ProductionStore does); SharedStateStore-only contexts simply skip.
"""
from __future__ import annotations

import inspect
import logging
from typing import Any

from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)

try:
    from langchain.agents.middleware import AgentMiddleware
except ImportError:  # pragma: no cover
    class AgentMiddleware:  # type: ignore[no-redef]
        pass


class OperatorGuidanceMiddleware(AgentMiddleware):
    """Inject pending operator guidance before each model call."""

    def __init__(self, run_id: str, store: Any):
        self.run_id = run_id
        self.store = store

    # -- guidance drain ---------------------------------------------------

    def _drain_sync(self) -> list[dict]:
        drain = getattr(self.store, "drain_guidance", None)
        if drain is None:
            return []
        try:
            result = drain(self.run_id)
            if inspect.isawaitable(result):
                # Sync hook cannot await; skip rather than corrupt the loop.
                return []
            return list(result or [])
        except Exception:  # noqa: BLE001
            logger.debug("guidance drain failed", exc_info=True)
            return []

    async def _drain_async(self) -> list[dict]:
        drain = getattr(self.store, "drain_guidance", None)
        if drain is None:
            return []
        try:
            result = drain(self.run_id)
            if inspect.isawaitable(result):
                result = await result
            return list(result or [])
        except Exception:  # noqa: BLE001
            logger.debug("guidance drain failed", exc_info=True)
            return []

    @staticmethod
    def _inject(state: dict, items: list[dict]) -> dict | None:
        if not items:
            return None
        injected = [
            HumanMessage(
                content=f"[Operator guidance]: {item.get('text', item)}",
                name="operator",
            )
            for item in items
        ]
        return {"messages": injected}

    # -- LangChain hooks ---------------------------------------------------

    def before_model(self, state: dict, runtime: Any) -> dict | None:
        return self._inject(state, self._drain_sync())

    async def abefore_model(self, state: dict, runtime: Any) -> dict | None:
        return self._inject(state, await self._drain_async())
