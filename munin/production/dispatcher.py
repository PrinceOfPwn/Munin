"""Leased bridge from durable runs to Munin's current ReAct executor.

v3.1.1 hardening (source edit — no monkey-patch)
-----------------------------------------------
The dispatcher no longer relies on an import-time monkey-patch of
``MuninAgent.respond``.  ``MuninAgent.respond`` now natively accepts a
``pre_iteration_hook`` keyword and dispatches multi-tool batches through
:mod:`munin.production.parallel` on its own.  The dispatcher's job is just to:

* Drain ``run_guidance_queue`` at the top of every ReAct iteration and hand
  the assembled ``<operator_guidance>`` block back to Munin via the
  ``pre_iteration_hook`` callback — the callback also records a
  ``reasoning_events`` row of kind ``operational_summary`` so the UI timeline
  shows "operator {name} sent guidance: ..." attributed to the delivered
  step number.
* Consume the ``parallel_group_id`` and ``tool_use_id`` fields that
  ``MuninAgent.respond`` now emits directly on ``tool_start`` /
  ``tool_result`` progress events, and stamp them onto the ``tool_calls``
  row so the UI can render the batch as a single group.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from .store import ProductionStore
from .store_v3_1 import install_v3_1_extensions


class ProductionDispatcher:
    """Executes one leased run without making in-process jobs authoritative."""

    def __init__(self, store: ProductionStore, settings: Any, *, worker_id: str) -> None:
        self.store = install_v3_1_extensions(store)
        self.settings = settings
        self.worker_id = worker_id

    def run_once(self) -> str | None:  # noqa: C901 - matches the original layout
        claim = self.store.claim_next_run(worker_id=self.worker_id, lease_seconds=60)
        if not claim:
            return None
        context = self.store.run_execution_context(run_id=claim["id"])
        self.store.append_conversation_broadcast(
            conversation_id=context["conversation_id"],
            kind="run-transition",
            payload={"run_id": claim["id"], "state": "running"},
        )
        stop = threading.Event()
        tool_call_ids: dict[tuple[int, str], str] = {}
        tool_call_ids_by_use: dict[str, str] = {}

        def heartbeat() -> None:
            while not stop.wait(15):
                if not self.store.renew_lease(
                    run_id=claim["id"], lease_token=claim["lease_token"], lease_seconds=60
                ):
                    return

        heartbeat_thread = threading.Thread(
            target=heartbeat, name=f"munin-run-{claim['id']}", daemon=True
        )
        heartbeat_thread.start()

        def progress(event: dict[str, Any]) -> None:
            stage = str(event.get("stage", ""))
            tool_name = str(event.get("tool", ""))
            step = int(event.get("iteration") or 0)
            if stage == "tool_start" and tool_name:
                tool = self.store.append_tool_call_with_parallel_group(
                    run_id=claim["id"],
                    agent_name="munin",
                    tool_name=tool_name,
                    state="running",
                    arguments=event.get("arguments") or {},
                    scope={"conversation_id": context["conversation_id"]},
                    parallel_group_id=event.get("parallel_group_id"),
                    tool_use_id=event.get("tool_use_id"),
                )
                tool_call_ids[(step, tool_name)] = tool["id"]
                use_id = event.get("tool_use_id")
                if use_id:
                    tool_call_ids_by_use[str(use_id)] = tool["id"]
            elif stage == "tool_result" and tool_name:
                use_id = event.get("tool_use_id")
                tool_call_id = (
                    tool_call_ids_by_use.get(str(use_id))
                    if use_id
                    else tool_call_ids.get((step, tool_name))
                )
                self.store.append_tool_call(
                    run_id=claim["id"],
                    agent_name="munin",
                    tool_name=tool_name,
                    state="completed" if bool(event.get("ok", True)) else "failed",
                    arguments={},
                    result={
                        "summary": event.get("summary", ""),
                        "elapsed_ms": event.get("elapsed_ms"),
                    },
                    tool_call_id=tool_call_id,
                )
                # No reasoning event: the tool_calls row already carries the
                # result payload and the UI dedupes on ``tool_use_id``.  A
                # parallel reasoning entry here would duplicate the timeline.
                return
            if stage == "provider_reasoning" and bool(event.get("provider_exposed")):
                kind = "provider_reasoning"
            elif stage in {"reasoning", "llm_retry"}:
                kind = "model_request"
            elif stage == "tool_start":
                kind = "tool_intent"
            elif stage in {"observation", "decision"}:
                kind = "observation"
            else:
                kind = "operational_summary"
            self.store.append_reasoning_event(
                run_id=claim["id"],
                kind=kind,
                content=str(event.get("message") or stage or "operation progress"),
                provider="",
                persistence_enabled=(
                    kind != "provider_reasoning"
                    or os.environ.get("MUNIN_REASONING_PERSISTENCE", "0") == "1"
                ),
                agent_name="munin",
                step=step,
            )

        # ── v3.1.1 pre-iteration hook (native kwarg, no monkey-patch) ──────
        # Drain the operator guidance queue at the top of every ReAct step and
        # return the buffered bodies as a wrapped ``<operator_guidance>``
        # block so MuninAgent.respond can append it as a system message before
        # the next model call.
        def pre_iteration_hook(step: int) -> str | None:
            pending = self.store.consume_pending_guidance(
                run_id=claim["id"], target_agent_id=None, delivered_at_step=step
            )
            if not pending:
                return None
            blocks = "\n".join(
                (
                    f'<operator_guidance from="{g.get("actor_username") or g.get("actor_id")}" '
                    f'at="{int(g.get("created_at_ms") or 0)}">'
                    f'{g.get("body", "")}'
                    "</operator_guidance>"
                )
                for g in pending
            )
            extra_seconds = 0
            for g in pending:
                self.store.append_reasoning_event(
                    run_id=claim["id"],
                    kind="operational_summary",
                    content=(
                        f"Operator guidance received from "
                        f"{g.get('actor_username') or g.get('actor_id')} "
                        f"(delivered at step {step}): "
                        f"{str(g.get('body', ''))[:200]}"
                    ),
                    provider="",
                    persistence_enabled=True,
                    agent_name=g.get("target_agent_id") or "munin",
                    step=step,
                )
                extra_seconds += int(g.get("budget_extension_seconds") or 0)
                # Broadcast delivery so the conversation SSE stream flips the
                # inline GuidanceBlock chip from ``queued`` → ``delivered @ N``.
                self.store.append_conversation_broadcast(
                    conversation_id=context["conversation_id"],
                    kind="guidance-delivered",
                    payload={
                        "run_id": claim["id"],
                        "guidance_id": g.get("id"),
                        "delivered_at_step": step,
                        "actor_username": g.get("actor_username"),
                    },
                )
            # Apply queued budget extensions to the current lease so a
            # long-running run inherits the operator-granted extra time.
            if extra_seconds > 0:
                self.store.renew_lease(
                    run_id=claim["id"],
                    lease_token=claim["lease_token"],
                    lease_seconds=max(60, extra_seconds),
                )
            preface = (
                "The following operator guidance has been queued while you were working. "
                "Consider it in your next step; it does not replace the original objective, "
                "but supplements it with fresh operator direction.\n"
            )
            return preface + blocks

        try:
            from ..core.munin_agent import MuninAgent

            if not (
                self.settings.llm_base_url
                and self.settings.llm_api_key
                and self.settings.llm_model
            ):
                raise RuntimeError(
                    "no configured LLM profile or environment fallback is available"
                )
            result = MuninAgent(self.settings).respond(
                context["message"],
                conversation_id=context["conversation_id"],
                conversation_history=context["history"],
                progress=progress,
                pre_iteration_hook=pre_iteration_hook,
            )
            self.store.complete_run(
                run_id=claim["id"],
                lease_token=claim["lease_token"],
                content=str(
                    result.get("content") or result.get("summary") or "(no response)"
                ),
                outcome="completed",
            )
            self.store.append_conversation_broadcast(
                conversation_id=context["conversation_id"],
                kind="run-transition",
                payload={"run_id": claim["id"], "state": "completed"},
            )
        except Exception as exc:  # noqa: BLE001 - durable failure boundary
            self.store.complete_run(
                run_id=claim["id"],
                lease_token=claim["lease_token"],
                content=f"Operation failed: {exc}",
                outcome="failed",
            )
            self.store.append_conversation_broadcast(
                conversation_id=context["conversation_id"],
                kind="run-transition",
                payload={"run_id": claim["id"], "state": "failed", "error": str(exc)},
            )
        finally:
            stop.set()
            heartbeat_thread.join(timeout=2)
        return claim["id"]

    def run_forever(
        self,
        *,
        poll_seconds: float = 2.0,
        stop: threading.Event | None = None,
    ) -> None:
        """Durable queue worker: claims from Turso, not from an HTTP request."""
        stopper = stop or threading.Event()
        while not stopper.is_set():
            self.store.recover_expired_runs()
            if self.run_once() is None:
                stopper.wait(max(0.2, poll_seconds))
