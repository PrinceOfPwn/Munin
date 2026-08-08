# tags: [hitl-approval, core, orchestrator, langgraph, store, OperatorGuidanceMiddleware, ACTIVE_RUN_ID, consume_pending_guidance, HumanMessage, AgentMiddleware, before_model, abefore_model, after_model, aafter_model, operator-guidance, guidance-injection, audit-record, guidance-lifecycle, transition_guidance_state, applied_to_model_step, PR-2D]
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

import contextvars
import inspect
import logging
from typing import Any

from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)

# Per-invocation override hoisted by ``runtime_adapter.supervisor_runner``.
# Lets a process-wide cached supervisor graph serve many runs without losing
# the operator-guidance audit scoping (``consume_pending_guidance(run_id)``).
ACTIVE_RUN_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "munin_operator_guidance_active_run_id", default=None
)

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
        # PR-2D — guidance drained in ``before_model`` that is awaiting the
        # matching ``guidance.applied_to_model_step`` transition in
        # ``after_model``.  Held per-step so an exception in the model call
        # cannot leak drained ids into the next step.
        self._pending_apply: list[dict] | None = None

    # -- guidance drain ---------------------------------------------------

    def _resolve_run_id(self) -> str:
        live = ACTIVE_RUN_ID.get()
        return live if live not in (None, "") else self.run_id

    def _drain_sync(self) -> list[dict]:
        consume = getattr(self.store, "consume_pending_guidance", None)
        if consume is None:
            return []
        try:
            result = consume(run_id=self._resolve_run_id())
            if inspect.isawaitable(result):
                return []
            return list(result or [])
        except Exception:  # noqa: BLE001
            logger.debug("guidance drain failed", exc_info=True)
            return []

    async def _drain_async(self) -> list[dict]:
        consume = getattr(self.store, "consume_pending_guidance", None)
        if consume is None:
            return []
        try:
            result = consume(run_id=self._resolve_run_id())
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
                content=f"[Operator guidance]: {item.get('body', item)}",
                name="operator",
            )
            for item in items
        ]
        return {"messages": injected}

    def _mark_applied(self, items: list[dict]) -> None:
        """Flip drained guidance rows to ``applied_to_model_step``.

        PR-2D — the durable lifecycle sees three ticks per guidance row:

        * ``guidance.queued`` — emitted by :meth:`enqueue_guidance`.
        * ``guidance.delivered_to_runtime`` — emitted by
          :meth:`consume_pending_guidance` when the middleware drains the
          row out of the queue and into the next model input.
        * ``guidance.applied_to_model_step`` — emitted here, immediately
          after the drained ``HumanMessage(name='operator')`` was injected
          into the model input for the upcoming step.  We fire synchronously
          in ``before_model`` (instead of waiting for ``after_model``) so the
          audit row advances deterministically; the operator ``HumanMessage``
          is now part of the next model call, which the lifecycle card treats
          as "the step consumes it".  Defensive ``after_model``/``aafter_model``
          hooks flush any pending drain ids we could not mark eagerly.

        ``expired`` / ``superseded`` / ``undelivered`` have no single
        guaranteed hook point in the LangGraph loop under this middleware
        (TTL is process-managed, supersession happens at enqueue time, run
        termination reaches the executor not the middleware).  They are
        exercised via direct :meth:`transition_guidance_state` calls in unit
        tests and the E2E card.
        """
        transition = getattr(self.store, "transition_guidance_state", None)
        if transition is None:
            return
        run_id = self._resolve_run_id()
        for item in items:
            guidance_id = str(item.get("id") or "")
            if not guidance_id:
                continue
            try:
                transition(
                    guidance_id,
                    "applied_to_model_step",
                    applied_message_id=item.get("applied_message_id"),
                )
            except (KeyError, ValueError):
                logger.debug(
                    "guidance applied_to_model_step transition failed run=%s gid=%s",
                    run_id,
                    guidance_id,
                    exc_info=True,
                )
            except Exception:  # noqa: BLE001
                logger.debug(
                    "guidance applied_to_model_step transition failed run=%s gid=%s",
                    run_id,
                    guidance_id,
                    exc_info=True,
                )

    # -- LangChain hooks ---------------------------------------------------

    def before_model(self, state: dict, runtime: Any) -> dict | None:
        drained = self._drain_sync()
        injected = self._inject(state, drained)
        if injected is not None:
            # PR-2D — operator guidance is now in the next model input.
            # Advance the durable lifecycle tick eagerly so the E2E test
            # observes the transition deterministically.
            self._mark_applied(drained)
            # Still stash for defensive ``after_model`` over-coverage.
            self._pending_apply = drained
        return injected

    async def abefore_model(self, state: dict, runtime: Any) -> dict | None:
        drained = await self._drain_async()
        injected = self._inject(state, drained)
        if injected is not None:
            self._mark_applied(drained)
            self._pending_apply = drained
        return injected

    def after_model(self, state: dict, runtime: Any, response: Any) -> dict | None:
        items = self._pending_apply
        self._pending_apply = None
        if items:
            # Idempotent: the eager ``before_model`` tick already advanced
            # the row; ``transition_guidance_state`` to the same state is a
            # cheap no-op against the CHECK constraint.
            self._mark_applied(items)
        return None

    async def aafter_model(self, state: dict, runtime: Any, response: Any) -> dict | None:
        items = self._pending_apply
        self._pending_apply = None
        if items:
            self._mark_applied(items)
        return None
