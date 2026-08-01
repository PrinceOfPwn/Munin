"""
Runtime adapter — the single execution path into the Deep Agents supervisor.

Owns:
* conversation history → LangChain messages conversion,
* thread/checkpoint config (``thread_id`` = conversation; runs resume),
* ``astream_events`` (v2) → Munin progress envelope translation
  (assistant deltas, safe operational activity, run lifecycle). Tool lifecycle envelopes
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


def _display_text(content: Any, thinking_state: dict[str, Any]) -> str:
    """Return only normal assistant text, excluding provider private thought.

    Providers use both typed reasoning blocks and raw ``<think>`` conventions.
    The state machine matters because a tag/body can be split across deltas.
    """
    if isinstance(content, list):
        visible: list[str] = []
        for block in content:
            if isinstance(block, str):
                visible.append(block)
            elif isinstance(block, dict) and str(block.get("type") or "") in {"text", "output_text"}:
                text = block.get("text")
                if isinstance(text, str):
                    visible.append(text)
        content = "".join(visible)
    elif isinstance(content, dict):
        if str(content.get("type") or "") not in {"text", "output_text"}:
            return ""
        content = content.get("text")
    if not isinstance(content, str) or not content:
        return ""

    pending = str(thinking_state.get("tag_prefix") or "") + content
    thinking_state["tag_prefix"] = ""
    output: list[str] = []
    cursor = 0
    in_think = bool(thinking_state.get("in_think"))
    lower = pending.lower()
    while cursor < len(pending):
        if in_think:
            end = lower.find("</think>", cursor)
            if end < 0:
                thinking_state["in_think"] = True
                return "".join(output)
            cursor = end + len("</think>")
            in_think = False
            thinking_state["in_think"] = False
            continue
        start = lower.find("<think", cursor)
        if start < 0:
            tail = pending[cursor:]
            for prefix_len in range(min(len(tail), len("<think") - 1), 0, -1):
                if "<think".startswith(tail[-prefix_len:].lower()):
                    output.append(tail[:-prefix_len])
                    thinking_state["tag_prefix"] = tail[-prefix_len:]
                    return "".join(output)
            output.append(tail)
            break
        output.append(pending[cursor:start])
        closing = lower.find(">", start)
        if closing < 0:
            thinking_state["tag_prefix"] = pending[start:]
            return "".join(output)
        cursor = closing + 1
        in_think = True
        thinking_state["in_think"] = True
    return "".join(output)


def translate_event(
    event: dict, *, run_id: str, thinking_state: dict[str, Any] | None = None
) -> dict | None:
    """Translate one LangGraph astream_events(v2) event into a Munin envelope.

    Returns None for events with no user-facing meaning.
    """
    event_type = event.get("event", "")
    name = event.get("name", "")

    if event_type == "on_chain_stream":
        chunk = event.get("data", {}).get("chunk")
        # Deep Agents' native HumanInTheLoopMiddleware emits this LangGraph
        # interrupt checkpoint.  Keep the provider/model internals out of the
        # stream; the production adapter turns the typed action request into
        # an authenticated, durable Munin human-request resource below.
        if isinstance(chunk, dict) and chunk.get("__interrupt__"):
            requests: list[dict[str, Any]] = []
            for item in chunk["__interrupt__"]:
                value = getattr(item, "value", item)
                if not isinstance(value, dict):
                    continue
                actions = value.get("action_requests")
                if isinstance(actions, list):
                    requests.extend(action for action in actions if isinstance(action, dict))
            if requests:
                return {"kind": "human_interrupt", "run_id": run_id, "actions": requests}

    if event_type == "on_chat_model_stream":
        chunk = event.get("data", {}).get("chunk")
        content = getattr(chunk, "content", None)
        if content:
            text = _display_text(content, thinking_state if thinking_state is not None else {})
            # A model token is the assistant's response, not its private
            # reasoning.  Never relabel it as chain-of-thought merely to make
            # it visible in the UI.
            if text:
                return {"kind": "assistant_text", "run_id": run_id, "text": text}
        return None

    if event_type == "on_chat_model_start":
        # This is deliberate operational telemetry: it tells the operator
        # that the graph has entered a planning/model step without exposing
        # hidden chain-of-thought or provider reasoning traces.
        return {
            "kind": "activity",
            "run_id": run_id,
            "stage": "planning",
            "text": "Planning the next authorized action",
        }

    if event_type == "on_chain_end" and name in _ROOT_GRAPH_NAMES:
        output = event.get("data", {}).get("output")
        final_text = ""
        if isinstance(output, dict):
            messages = output.get("messages") or []
            if messages:
                last = messages[-1]
                final_text = _display_text(
                    getattr(last, "content", "") or "",
                    thinking_state if thinking_state is not None else {},
                )
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


def _persist_human_interrupt(
    *,
    store: Any,
    run_id: str,
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Convert a Deep Agents interrupt into Munin's durable HITL resource."""
    from ..mcp.audit import redact_secrets  # noqa: TID252, PLC0415

    safe_actions = redact_secrets(actions)
    names = [str(action.get("name") or "unknown") for action in safe_actions]
    request = store.request_human_decision(
        run_id=run_id,
        action="Approve tool execution: " + ", ".join(names),
        risk="critical" if "extension_open_pr" in names else "high",
        evidence=[str(action.get("description") or "")[:1_000] for action in safe_actions],
        scope={"actions": safe_actions},
        choices=["approve", "reject"],
    )
    return {
        "kind": "human_request",
        "run_id": run_id,
        "request_id": request["id"],
        "tool_name": names[0] if len(names) == 1 else "multiple_tools",
        "args": {"actions": safe_actions},
        "nonce": request["nonce"],
        "choices": request["choices"],
    }


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
    human_request_store: Any | None = None,
    resume_decisions: list[dict[str, Any]] | None = None,
    resume_from_checkpoint: bool = False,
) -> AsyncIterator[dict]:
    """Run one Munin turn through the Deep Agents supervisor.

    Yields translated Munin progress envelopes.  ``tools``/``system_prompt``
    are accepted for backwards compatibility but the authoritative catalog and
    prompt are assembled inside ``build_munin_supervisor`` (gateway + soul).
    """
    from langchain_core.messages import HumanMessage  # noqa: PLC0415

    from .middleware.operator_guidance import ACTIVE_RUN_ID as _OG_RUN_ID
    from .middleware.progress_emit import (
        ACTIVE_PROGRESS_SINK as _PE_SINK,
    )
    from .middleware.progress_emit import ACTIVE_RUN_ID as _PE_RUN_ID
    from .supervisor import build_munin_supervisor  # noqa: PLC0415

    middleware_events: list[dict] = []
    thinking_state: dict[str, Any] = {}

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

    config = {
        "configurable": {"thread_id": thread_id or conversation_id or run_id},
        "recursion_limit": max_iterations or DEFAULT_RECURSION_LIMIT,
    }

    # Bind the per-invocation middleware overrides. The supervisor graph is
    # process-wide (cached), so its middleware instances are SHARED across
    # runs; they read these contextvars at hook time to recover the live
    # ``run_id`` and ``progress_sink`` for *this* call. Tokens are reset in a
    # ``finally`` so a concurrent task never inherits a stale binding.
    tok_og_rid = _OG_RUN_ID.set(run_id)
    tok_pe_rid = _PE_RUN_ID.set(run_id)
    tok_pe_sink = _PE_SINK.set(wrapped_progress_sink)
    try:
        input_value: Any = None
        if resume_decisions is not None:
            from langgraph.types import Command  # noqa: PLC0415

            input_value = Command(resume={"decisions": resume_decisions})
        elif not resume_from_checkpoint:
            messages = _history_to_messages(conversation_history)
            messages.append(HumanMessage(content=prompt))
            input_value = {"messages": messages}

        async for event in supervisor.astream_events(input_value, config=config, version="v2"):
            while middleware_events:
                yield middleware_events.pop(0)

            envelope = translate_event(event, run_id=run_id, thinking_state=thinking_state)
            if envelope is None:
                continue
            if envelope.get("kind") == "human_interrupt":
                try:
                    envelope = _persist_human_interrupt(
                        store=human_request_store or store,
                        run_id=run_id,
                        actions=list(envelope.get("actions") or []),
                    )
                except Exception as exc:  # noqa: BLE001 - fail closed on a missing approval record
                    envelope = {
                        "kind": "run_state",
                        "run_id": run_id,
                        "state": "failed",
                        "error": f"could not persist human approval request: {exc}",
                    }
            if progress_sink is not None:
                try:
                    progress_sink(envelope)
                except Exception:  # noqa: BLE001 - observability must not sink a run
                    pass
            yield envelope

        while middleware_events:
            yield middleware_events.pop(0)
    finally:
        _PE_SINK.reset(tok_pe_sink)
        _PE_RUN_ID.reset(tok_pe_rid)
        _OG_RUN_ID.reset(tok_og_rid)
