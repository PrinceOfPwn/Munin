"""
Runtime adapter — collapsed to supervisor-only path.

Previously accepted MUNIN_RUNTIME env flag (legacy | supervisor).
Flag removed: all execution goes through the LangGraph supervisor.
"""
from __future__ import annotations
from typing import Any, AsyncIterator, Callable


async def supervisor_runner(
    prompt: str,
    *,
    run_id: str,
    conversation_id: str,
    tools: list[Any],
    store: Any,
    progress_sink: Callable[[dict], None],
    model: str = "gpt-4o",
    system_prompt: str = "",
    max_iterations: int = 50,
) -> AsyncIterator[dict]:
    """
    Run a Munin agent turn through the LangGraph supervisor.

    Yields progress event dicts compatible with the SSE layer.
    Caller is responsible for catching exceptions and emitting run_state=failed.
    """
    from munin.core.supervisor import build_supervisor
    from munin.core.middleware import (
        OperatorGuidanceMiddleware,
        ProgressEmitMiddleware,
        RepetitionGuardMiddleware,
    )

    emit = ProgressEmitMiddleware(progress_sink=progress_sink, run_id=run_id)
    guidance = OperatorGuidanceMiddleware(run_id=run_id, store=store)
    guard = RepetitionGuardMiddleware(window_size=6, min_unique=3)

    supervisor = build_supervisor(
        tools=tools,
        model=model,
        system_prompt=system_prompt,
        extra_middleware=[emit, guidance, guard],
        store=store,
        run_id=run_id,
        max_iterations=max_iterations,
    )

    from langchain_core.messages import HumanMessage

    initial_state = {
        "messages": [HumanMessage(content=prompt)],
        "run_id": run_id,
        "conversation_id": conversation_id,
    }

    async for event in supervisor.astream_events(initial_state, version="v2"):
        # ProgressEmitMiddleware already called progress_sink for known events.
        # Yield raw events for callers that want the full LangGraph stream.
        yield event
