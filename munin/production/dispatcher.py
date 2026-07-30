"""Leased bridge from durable runs to Munin's ReAct executor.

In addition to durable tool/run telemetry, the dispatcher now exposes live model
activity. Assistant text is written into the existing placeholder while it is
generated. Provider-supplied reasoning is retained only for a short viewing
window and is then scrubbed automatically; hidden chain-of-thought is never
synthesised.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from typing import Any

from ..core.llm_stream import llm_stream_scope
from .store import ProductionStore
from .store_v3_1 import install_v3_1_extensions


class ProductionDispatcher:
    """Executes one leased run without making in-process jobs authoritative."""

    def __init__(self, store: ProductionStore, settings: Any, *, worker_id: str) -> None:
        self.store = install_v3_1_extensions(store)
        self.settings = settings
        self.worker_id = worker_id

    def run_once(self) -> str | None:  # noqa: C901 - orchestration boundary
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
        assistant_stream: list[str] = []
        provider_stream: list[str] = []
        provider_reasoning_id: str | None = None
        last_refresh = 0.0
        stream_lock = threading.RLock()

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

        def refresh_clients(*, force: bool = False) -> None:
            """Invalidate live conversation/run caches without creating a new state."""

            nonlocal last_refresh
            now = time.monotonic()
            if not force and now - last_refresh < 0.25:
                return
            last_refresh = now
            self.store.append_conversation_broadcast(
                conversation_id=context["conversation_id"],
                kind="run-transition",
                payload={"run_id": claim["id"], "state": "running", "streaming": True},
            )

        def update_assistant_placeholder(content: str) -> None:
            """Make the final answer visible while the provider is still generating it."""

            now_ms = int(time.time() * 1000)
            safe_content = content[-1_000_000:]
            with self.store._transaction() as conn:  # noqa: SLF001 - same aggregate transaction
                conn.execute(
                    "UPDATE messages SET content=?,content_hash=?,updated_at_ms=?,version=version+1 "
                    "WHERE id=? AND kind='assistant_placeholder' AND status='running'",
                    (
                        safe_content,
                        hashlib.sha256(safe_content.encode()).hexdigest(),
                        now_ms,
                        claim["assistant_message_id"],
                    ),
                )

        def update_provider_reasoning(content: str, step: int) -> None:
            """Upsert one temporarily visible provider-reasoning row for this run."""

            nonlocal provider_reasoning_id
            if provider_reasoning_id is None:
                row = self.store.append_reasoning_event(
                    run_id=claim["id"],
                    kind="provider_reasoning",
                    content=content,
                    provider="stream",
                    persistence_enabled=True,
                    agent_name="munin",
                    step=step,
                )
                provider_reasoning_id = str(row["id"])
                return
            with self.store._transaction() as conn:  # noqa: SLF001
                conn.execute(
                    "UPDATE reasoning_events SET content=?,persisted=1 WHERE id=? AND run_id=?",
                    (content[-250_000:], provider_reasoning_id, claim["id"]),
                )

        def progress(event: dict[str, Any]) -> None:  # noqa: C901 - event adapter
            stage = str(event.get("stage", ""))
            tool_name = str(event.get("tool", ""))
            step = int(event.get("iteration") or 0)

            if stage == "assistant_delta":
                delta = str(event.get("delta") or event.get("message") or "")
                if not delta:
                    return
                with stream_lock:
                    assistant_stream.append(delta)
                    update_assistant_placeholder("".join(assistant_stream))
                    refresh_clients()
                return

            if stage == "provider_reasoning_delta" and bool(event.get("provider_exposed")):
                delta = str(event.get("delta") or event.get("message") or "")
                if not delta:
                    return
                with stream_lock:
                    provider_stream.append(delta)
                    update_provider_reasoning("".join(provider_stream), step)
                    refresh_clients()
                return

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
                refresh_clients(force=True)
                return

            # MuninAgent emits the provider's full reasoning field once after the
            # stream. The delta path already owns the live row, so update it rather
            # than creating a duplicate.
            if stage == "provider_reasoning" and bool(event.get("provider_exposed")):
                text = str(event.get("message") or "")
                if provider_reasoning_id and text:
                    update_provider_reasoning(text, step)
                    refresh_clients(force=True)
                    return
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
            refresh_clients(force=stage in {"model_stream_started", "model_stream_completed"})

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

        def scrub_provider_reasoning_later() -> None:
            """Remove provider reasoning after the operator had time to inspect it."""

            if not provider_reasoning_id:
                return
            retention = max(10, int(os.environ.get("MUNIN_LIVE_REASONING_SECONDS", "120")))
            if stop.wait(retention):
                # The run stopping starts the retention window; do not abort cleanup.
                time.sleep(retention)
            try:
                with self.store._transaction() as conn:  # noqa: SLF001
                    conn.execute(
                        "UPDATE reasoning_events SET content='[EPHEMERAL_EXPIRED]',persisted=0 "
                        "WHERE id=? AND run_id=?",
                        (provider_reasoning_id, claim["id"]),
                    )
            except Exception:
                return

        try:
            from ..core.munin_agent import MuninAgent

            if not (
                self.settings.llm_base_url
                and self.settings.llm_api_key
                and self.settings.llm_model
            ):
                raise RuntimeError("no configured LLM profile or environment fallback is available")
            with llm_stream_scope(progress):
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
                content=str(result.get("content") or result.get("summary") or "(no response)"),
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
            if provider_reasoning_id:
                threading.Thread(
                    target=scrub_provider_reasoning_later,
                    name=f"munin-reasoning-scrub-{claim['id']}",
                    daemon=True,
                ).start()
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
