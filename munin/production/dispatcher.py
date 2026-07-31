"""Leased bridge from durable runs to Munin's current ReAct executor."""

from __future__ import annotations

import os
import threading
from typing import Any

from .store import ProductionStore


class ProductionDispatcher:
    """Executes one leased run without making in-process jobs authoritative."""

    def __init__(self, store: ProductionStore, settings: Any, *, worker_id: str) -> None:
        self.store = store
        self.settings = settings
        self.worker_id = worker_id

    def run_once(self) -> str | None:
        claim = self.store.claim_next_run(worker_id=self.worker_id, lease_seconds=60)
        if not claim:
            return None
        context = self.store.run_execution_context(run_id=claim["id"])
        stop = threading.Event()
        tool_call_ids: dict[tuple[int, str], str] = {}

        def heartbeat() -> None:
            while not stop.wait(15):
                if not self.store.renew_lease(run_id=claim["id"], lease_token=claim["lease_token"], lease_seconds=60):
                    return

        heartbeat_thread = threading.Thread(target=heartbeat, name=f"munin-run-{claim['id']}", daemon=True)
        heartbeat_thread.start()

        def progress(event: dict[str, Any]) -> None:
            stage = str(event.get("stage", ""))
            tool_name = str(event.get("tool", ""))
            step = int(event.get("iteration") or 0)
            if stage == "tool_start" and tool_name:
                tool = self.store.append_tool_call(
                    run_id=claim["id"], agent_name="munin", tool_name=tool_name, state="running",
                    arguments=event.get("arguments") or {}, scope={"conversation_id": context["conversation_id"]},
                )
                tool_call_ids[(step, tool_name)] = tool["id"]
            elif stage == "tool_result" and tool_name:
                self.store.append_tool_call(
                    run_id=claim["id"], agent_name="munin", tool_name=tool_name,
                    state="completed" if bool(event.get("ok", True)) else "failed", arguments={},
                    result={"summary": event.get("summary", ""), "elapsed_ms": event.get("elapsed_ms")},
                    tool_call_id=tool_call_ids.get((step, tool_name)),
                )
            if stage == "provider_reasoning" and bool(event.get("provider_exposed")):
                kind = "provider_reasoning"
            elif stage in {"reasoning", "llm_retry"}:
                kind = "model_request"
            elif "tool" in stage and "start" in stage:
                kind = "tool_intent"
            elif "tool" in stage or stage in {"observation", "decision"}:
                kind = "observation"
            else:
                kind = "operational_summary"
            self.store.append_reasoning_event(
                run_id=claim["id"],
                kind=kind,
                content=str(event.get("message") or stage or "operation progress"),
                provider="",
                persistence_enabled=(kind != "provider_reasoning" or os.environ.get("MUNIN_REASONING_PERSISTENCE", "0") == "1"),
                agent_name="munin",
                step=step,
            )

        try:
            from ..core.munin_agent import MuninAgent

            if not (self.settings.llm_base_url and self.settings.llm_api_key and self.settings.llm_model):
                raise RuntimeError("no configured LLM profile or environment fallback is available")
            result = MuninAgent(self.settings).respond(
                context["message"], conversation_id=context["conversation_id"], conversation_history=context["history"], progress=progress
            )
            self.store.complete_run(
                run_id=claim["id"], lease_token=claim["lease_token"], content=str(result.get("content") or result.get("summary") or "(no response)"), outcome="completed"
            )
        except Exception as exc:  # noqa: BLE001 - durable failure boundary
            self.store.complete_run(run_id=claim["id"], lease_token=claim["lease_token"], content=f"Operation failed: {exc}", outcome="failed")
        finally:
            stop.set()
            heartbeat_thread.join(timeout=2)
        return claim["id"]
