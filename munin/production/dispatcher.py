"""
Dispatcher — routes run requests to the supervisor runner.
"""
from __future__ import annotations
import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)


async def dispatch_run(
    run_id: str,
    conversation_id: str,
    goal: str,
    *,
    store: Any,
    tools: list[Any],
    model: str = "gpt-4o",
    system_prompt: str = "",
    progress_sink=None,
) -> dict:
    from munin.core.runtime_adapter import supervisor_runner

    if progress_sink is None:
        def progress_sink(event):
            log.debug("progress: %s", event)

    try:
        store.set_run_state(run_id, "running")
        progress_sink({"kind": "run_state", "run_id": run_id, "state": "running"})

        async for _ in supervisor_runner(
            goal,
            run_id=run_id,
            conversation_id=conversation_id,
            tools=tools,
            store=store,
            progress_sink=progress_sink,
            model=model,
            system_prompt=system_prompt,
        ):
            pass

        store.set_run_state(run_id, "completed", finished=True)
        progress_sink({"kind": "run_state", "run_id": run_id, "state": "completed"})
        return {"run_id": run_id, "state": "completed"}

    except asyncio.CancelledError:
        store.set_run_state(run_id, "cancelled", finished=True)
        progress_sink({"kind": "run_state", "run_id": run_id, "state": "cancelled"})
        return {"run_id": run_id, "state": "cancelled"}

    except Exception as exc:
        log.exception("Run %s failed: %s", run_id, exc)
        store.set_run_state(run_id, "failed", finished=True)
        progress_sink({"kind": "run_state", "run_id": run_id, "state": "failed", "error": str(exc)})
        return {"run_id": run_id, "state": "failed", "error": str(exc)}
