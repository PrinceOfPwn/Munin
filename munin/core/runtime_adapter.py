"""
Runtime adapter — the single execution path into the Deep Agents supervisor.

Owns:
* conversation history → LangChain messages conversion,
* thread/checkpoint config (``thread_id`` = conversation; runs resume),
* ``astream_events`` (v2) → Munin progress envelope translation
  (reasoning deltas, run lifecycle).  Tool lifecycle envelopes
  (``tool_intent``/``tool_result``/``tool_failed``) are emitted by
  ``ProgressEmitMiddleware`` inside the graph — one channel per concern,
  no double emission.

Consumers: ``munin_chat`` (MCP), ``munin run`` (CLI), and any future
UI-message-stream adapter.
"""
from __future__ import annotations

import os
from typing import Any, AsyncIterator, Callable, Iterable

DEFAULT_RECURSION_LIMIT = int(os.environ.get("MUNIN_RECURSION_LIMIT", "100"))

_ROOT_GRAPH_NAMES = frozenset({"LangGraph", "munin", "munin_supervisor", "__end__"})


def _history_to_messages(history: Iterable[dict] | None) -> list[Any]:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage  # noqa: PLC0415

    messages: list[Any] = []
    for item in history or []:
        role = str(item.get("role", "")).lower()
        content = str(item.get("content", ""))
        if not content:
            continue
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))
        elif role == "system":
            messages.append(SystemMessage(content=content))
    return messages


def translate_event(event: dict, *, run_id: str) -> dict | None:
    """Translate one LangGraph astream_events(v2) event into a Munin envelope.

    Returns None for events with no user-facing meaning.
    """
    event_type = event.get("event", "")
    name = event.get("name", "")

    if event_type == "on_chat_model_stream":
        chunk = event.get("data", {}).get("chunk")
        content = getattr(chunk, "content", None)
        if content:
            text = content if isinstance(content, str) else str(content)
            return {"kind": "reasoning", "run_id": run_id, "text": text}
        return None

    if event_type == "on_chain_end" and name in _ROOT_GRAPH_NAMES:
        output = event.get("data", {}).get("output")
        final_text = ""
        if isinstance(output, dict):
            messages = output.get("messages") or []
            if messages:
                last = messages[-1]
                final_text = getattr(last, "content", "") or ""
        return {
            "kind": "run_state",
            "run_id": run_id,
            "state": "completed",
            "content": final_text,
        }

    if event_type == "on_chain_error":
        return {
            "kind": "run_state",
            "run_id": run_id,
            "state": "failed",
            "error": str(event.get("data", {}).get("error", "unknown")),
        }

    return None


async def supervisor_runner(
    prompt: str,
    *,
    run_id: str,
    conversation_id: str,
    tools: list[Any] | None = None,  # legacy kwarg — gateway owns the catalog now
    store: Any,
    progress_sink: Callable[[dict], None] | None = None,
    model: Any = None,
    system_prompt: str = "",  # composed in build_munin_supervisor (soul + policy)
    max_iterations: int | None = None,
    conversation_history: list[dict] | None = None,
    thread_id: str | None = None,
) -> AsyncIterator[dict]:
    """Run one Munin turn through the Deep Agents supervisor.

    Yields translated Munin progress envelopes.  ``tools``/``system_prompt``
    are accepted for backwards compatibility but the authoritative catalog and
    prompt are assembled inside ``build_munin_supervisor`` (gateway + soul).
    """
    from langchain_core.messages import HumanMessage  # noqa: PLC0415

    from .supervisor import build_munin_supervisor  # noqa: PLC0415

    middleware_events: list[dict] = []

    def wrapped_progress_sink(envelope: dict) -> None:
        middleware_events.append(envelope)
        if progress_sink is not None:
            try:
                progress_sink(envelope)
            except Exception:  # noqa: BLE001
                pass

    supervisor = build_munin_supervisor(
        state=store,
        model=model,
        run_id=run_id,
        progress_sink=wrapped_progress_sink,
    )

    messages = _history_to_messages(conversation_history)
    messages.append(HumanMessage(content=prompt))

    config = {
        "configurable": {"thread_id": thread_id or conversation_id or run_id},
        "recursion_limit": max_iterations or DEFAULT_RECURSION_LIMIT,
    }

    async for event in supervisor.astream_events(
        {"messages": messages}, config=config, version="v2"
    ):
        while middleware_events:
            yield middleware_events.pop(0)

        envelope = translate_event(event, run_id=run_id)
        if envelope is None:
            continue
        if progress_sink is not None:
            try:
                progress_sink(envelope)
            except Exception:  # noqa: BLE001 - observability must not sink a run
                pass
        yield envelope

    while middleware_events:
        yield middleware_events.pop(0)
