# tags: [runtime, supervisor, orchestrator, core, langgraph, supervisor_runner, _split_think_tags, UNLIMITED_RECURSION_LIMIT, astream_events, _history_to_messages, _trailing_tag_prefix, DEFAULT_RECURSION_LIMIT, progress-envelopes, checkpoint-config, thread_id, ACTIVE_CONVERSATION_ID, ACTIVE_ACTOR_ID, memory-scoping]
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

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Callable, Iterable
from typing import Any

# LangGraph requires an integer ``recursion_limit`` even when the application
# deliberately does not impose a graph-step budget.  Use the largest practical
# Python integer as the explicit "unlimited" sentinel instead of inheriting
# LangGraph's small default (which aborts legitimate long-running agents).
# Operator cancellation, run leases, tool approval, and the model/tool
# middleware budgets remain the independent safety controls.
UNLIMITED_RECURSION_LIMIT = 2**31 - 1


def _recursion_limit_from_environment() -> int:
    raw = os.environ.get("MUNIN_RECURSION_LIMIT", "unlimited").strip().lower()
    if raw in {"", "0", "none", "infinite", "infinity", "unlimited"}:
        return UNLIMITED_RECURSION_LIMIT
    try:
        value = int(raw)
    except ValueError:
        return UNLIMITED_RECURSION_LIMIT
    return value if value > 0 else UNLIMITED_RECURSION_LIMIT


DEFAULT_RECURSION_LIMIT = _recursion_limit_from_environment()

logger = logging.getLogger(__name__)

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


def _trailing_tag_prefix(text: str, tag: str) -> int:
    """Return the suffix length that could become ``tag`` in the next delta."""
    lowered = text.lower()
    for length in range(min(len(text), len(tag) - 1), 0, -1):
        if tag.startswith(lowered[-length:]):
            return length
    return 0


def _split_think_tags(content: str, thinking_state: dict[str, Any]) -> tuple[str, str]:
    """Split provider-emitted ``<think>`` deltas into reasoning and answer.

    This is deliberately limited to explicit provider output. It does not infer
    latent chain-of-thought from ordinary assistant prose. Partial XML-like tags
    are retained across chunks so a network boundary cannot leak tag fragments
    into the final answer or truncate the visible provider reasoning.
    """
    pending = str(thinking_state.get("tag_prefix") or "") + content
    thinking_state["tag_prefix"] = ""
    reasoning: list[str] = []
    visible: list[str] = []
    cursor = 0
    in_think = bool(thinking_state.get("in_think"))
    lowered = pending.lower()

    while cursor < len(pending):
        if in_think:
            end = lowered.find("</think>", cursor)
            if end < 0:
                tail = pending[cursor:]
                prefix = _trailing_tag_prefix(tail, "</think>")
                reasoning.append(tail[:-prefix] if prefix else tail)
                if prefix:
                    thinking_state["tag_prefix"] = tail[-prefix:]
                thinking_state["in_think"] = True
                break
            reasoning.append(pending[cursor:end])
            cursor = end + len("</think>")
            in_think = False
            thinking_state["in_think"] = False
            continue

        start = lowered.find("<think", cursor)
        if start < 0:
            tail = pending[cursor:]
            prefix = _trailing_tag_prefix(tail, "<think")
            visible.append(tail[:-prefix] if prefix else tail)
            if prefix:
                thinking_state["tag_prefix"] = tail[-prefix:]
            break
        visible.append(pending[cursor:start])
        closing = lowered.find(">", start)
        if closing < 0:
            thinking_state["tag_prefix"] = pending[start:]
            break
        cursor = closing + 1
        in_think = True
        thinking_state["in_think"] = True

    return "".join(reasoning), "".join(visible)


def _block_text(block: dict[str, Any]) -> str:
    for key in ("text", "content", "reasoning_content", "thinking"):
        value = block.get(key)
        if isinstance(value, str):
            return value
    return ""


def _stream_parts(chunk: Any, thinking_state: dict[str, Any]) -> tuple[list[str], list[str], str]:
    """Extract explicit provider reasoning and normal assistant text from a chunk."""
    reasoning: list[str] = []
    visible: list[str] = []
    provider = ""
    additional = getattr(chunk, "additional_kwargs", None)
    if isinstance(additional, dict):
        for key in ("reasoning_content", "reasoning", "thinking", "reasoning_summary"):
            value = additional.get(key)
            if isinstance(value, str) and value:
                reasoning.append(value)
        provider = str(additional.get("provider") or additional.get("model_provider") or "")
    metadata = getattr(chunk, "response_metadata", None)
    if isinstance(metadata, dict):
        provider = provider or str(metadata.get("provider") or metadata.get("model_provider") or "")

    content = getattr(chunk, "content", None)
    if isinstance(content, list):
        for block in content:
            if isinstance(block, str):
                tagged_reasoning, text = _split_think_tags(block, thinking_state)
                if tagged_reasoning:
                    reasoning.append(tagged_reasoning)
                if text:
                    visible.append(text)
            elif isinstance(block, dict):
                block_type = str(block.get("type") or "")
                text = _block_text(block)
                if not text:
                    continue
                if block_type in {"reasoning", "thinking", "reasoning_content", "reasoning_summary"}:
                    reasoning.append(text)
                elif block_type in {"text", "output_text"}:
                    tagged_reasoning, answer = _split_think_tags(text, thinking_state)
                    if tagged_reasoning:
                        reasoning.append(tagged_reasoning)
                    if answer:
                        visible.append(answer)
    elif isinstance(content, str) and content:
        tagged_reasoning, text = _split_think_tags(content, thinking_state)
        if tagged_reasoning:
            reasoning.append(tagged_reasoning)
        if text:
            visible.append(text)

    return reasoning, visible, provider or "openai-compatible"


def translate_events(
    event: dict, *, run_id: str, thinking_state: dict[str, Any] | None = None
) -> list[dict]:
    """Translate one LangGraph event into zero or more Munin envelopes.

    A provider chunk can carry both an explicit reasoning delta and normal
    assistant text. Keeping them as distinct envelopes is essential for the
    AI SDK UIMessage protocol and for durable replay.
    """
    thinking_state = thinking_state if thinking_state is not None else {}
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
                return [{"kind": "human_interrupt", "run_id": run_id, "actions": requests}]

    if event_type == "on_chat_model_stream":
        chunk = event.get("data", {}).get("chunk")
        reasoning, text, provider = _stream_parts(chunk, thinking_state)
        step = max(1, int(thinking_state.get("model_step") or 1))
        envelopes: list[dict] = []
        for delta in reasoning:
            if delta:
                envelopes.append(
                    {
                        "kind": "provider_reasoning",
                        "run_id": run_id,
                        "text": delta,
                        "provider": provider,
                        "step": step,
                    }
                )
        for delta in text:
            if delta:
                envelopes.append({"kind": "assistant_text", "run_id": run_id, "text": delta})
        return envelopes

    if event_type == "on_chat_model_start":
        thinking_state["model_step"] = int(thinking_state.get("model_step") or 0) + 1
        return [
            {
                "kind": "activity",
                "run_id": run_id,
                "stage": "planning",
                "text": "Planning the next authorized action",
            }
        ]

    if event_type == "on_chain_end" and name in _ROOT_GRAPH_NAMES:
        output = event.get("data", {}).get("output")
        final_text = ""
        if isinstance(output, dict):
            messages = output.get("messages") or []
            if messages:
                last = messages[-1]
                _reasoning, text, _provider = _stream_parts(last, thinking_state)
                final_text = "".join(text)
        return [
            {
                "kind": "run_state",
                "run_id": run_id,
                "state": "completed",
                "content": final_text,
            }
        ]

    if event_type == "on_chain_error":
        return [
            {
                "kind": "run_state",
                "run_id": run_id,
                "state": "failed",
                "error": str(event.get("data", {}).get("error", "unknown")),
            }
        ]

    return []


def translate_event(
    event: dict, *, run_id: str, thinking_state: dict[str, Any] | None = None
) -> dict | None:
    """Compatibility wrapper for single-envelope consumers and older tests."""
    envelopes = translate_events(event, run_id=run_id, thinking_state=thinking_state)
    return envelopes[0] if envelopes else None


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
    mode: Any = None,
    goal: dict[str, Any] | None = None,
    actor_id: str = "",
) -> AsyncIterator[dict]:
    """Run one Munin turn through the Deep Agents supervisor.

    Yields translated Munin progress envelopes.  ``tools``/``system_prompt``
    are accepted for backwards compatibility but the authoritative catalog and
    prompt are assembled inside ``build_munin_supervisor`` (gateway + soul).

    ``mode`` selects the operation contract (approval levels, budgets,
    planning middleware).  ``goal`` is the persistent-goal snapshot injected
    by ``GoalMiddleware``; when present a ``plan`` snapshot envelope is
    emitted up front so clients hydrate the goal + TODO panel immediately.
    """
    from langchain_core.messages import HumanMessage  # noqa: PLC0415

    from .autonomy.context import (  # noqa: PLC0415
        ACTIVE_ACTOR_ID,
        ACTIVE_CONVERSATION_ID,
        ACTIVE_EMITTER,
        ACTIVE_GOAL,
        ACTIVE_MODE,
        ACTIVE_PLAN_SNAPSHOT,
        ACTIVE_STORE,
    )
    from .middleware.operator_guidance import ACTIVE_RUN_ID as _OG_RUN_ID
    from .middleware.progress_emit import (
        ACTIVE_PROGRESS_SINK as _PE_SINK,
    )
    from .middleware.progress_emit import ACTIVE_RUN_ID as _PE_RUN_ID
    from .supervisor import build_munin_supervisor  # noqa: PLC0415

    thinking_state: dict[str, Any] = {}
    event_queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=2048)
    loop = asyncio.get_running_loop()

    def wrapped_progress_sink(envelope: dict) -> None:
        if progress_sink is not None:
            try:
                progress_sink(envelope)
            except Exception:  # noqa: BLE001
                pass
        try:
            def enqueue() -> None:
                try:
                    event_queue.put_nowait(("envelope", dict(envelope)))
                except asyncio.QueueFull:  # pragma: no cover - backpressure guard
                    logger.warning(
                        "dropping progress envelope because the run queue is full "
                        "(run_id=%s kind=%s)",
                        run_id,
                        envelope.get("kind"),
                    )

            loop.call_soon_threadsafe(enqueue)
        except RuntimeError:  # pragma: no cover - shutdown guard
            pass

    supervisor = build_munin_supervisor(
        state=store,
        model=model,
        run_id=run_id,
        progress_sink=wrapped_progress_sink,
        mode=mode,
    )

    config = {
        "configurable": {"thread_id": thread_id or conversation_id or run_id},
        # ``max_iterations`` is retained for explicit programmatic callers;
        # omitted/zero values use the unlimited sentinel above.
        "recursion_limit": (
            max_iterations
            if max_iterations and max_iterations > 0
            else DEFAULT_RECURSION_LIMIT
        ),
    }

    # Bind the per-invocation middleware overrides. The supervisor graph is
    # process-wide (cached), so its middleware instances are SHARED across
    # runs; they read these contextvars at hook time to recover the live
    # ``run_id`` and ``progress_sink`` for *this* call. Tokens are reset in a
    # ``finally`` so a concurrent task never inherits a stale binding.
    tok_og_rid = _OG_RUN_ID.set(run_id)
    tok_pe_rid = _PE_RUN_ID.set(run_id)
    tok_pe_sink = _PE_SINK.set(wrapped_progress_sink)
    tok_st = ACTIVE_STORE.set(store)
    tok_mode = ACTIVE_MODE.set(str(mode or "standard").lower())
    tok_goal = ACTIVE_GOAL.set(goal)
    tok_emit = ACTIVE_EMITTER.set(wrapped_progress_sink)
    tok_conv = ACTIVE_CONVERSATION_ID.set(str(conversation_id or ""))
    tok_actor = ACTIVE_ACTOR_ID.set(str(actor_id or ""))
    plan_snapshot: dict[str, Any] | None = None
    try:
        plan_snapshot_provider = getattr(store, "plan_snapshot", None)
        if callable(plan_snapshot_provider):
            try:
                plan_snapshot = plan_snapshot_provider(conversation_id=conversation_id)
            except Exception:  # noqa: BLE001 - hydration must never sink a run
                plan_snapshot = None
        tok_plan = ACTIVE_PLAN_SNAPSHOT.set(plan_snapshot)
        if plan_snapshot and (plan_snapshot.get("items") or plan_snapshot.get("goal")):
            yield {
                "kind": "plan",
                "run_id": run_id,
                "goal": plan_snapshot.get("goal"),
                "items": plan_snapshot.get("items") or [],
                "updated_at_ms": plan_snapshot.get("updated_at_ms") or 0,
            }

        input_value: Any = None
        if resume_decisions is not None:
            from langgraph.types import Command  # noqa: PLC0415

            # Deep Agents HITL resume: the checkpointer preserves the full
            # graph state (all prior messages, the interrupted AIMessage with
            # tool_calls, etc.). ``Command(resume={"decisions": [...]})`` loads
            # that checkpoint and the HITL middleware's ``after_model`` hook
            # replays the interrupt with the operator's decisions, muting the
            # approved tool_calls (or removing rejected ones) and routing to
            # the tools node. After tools execute, the graph loops back to
            # ``before_model`` where ``OperatorGuidanceMiddleware`` drains any
            # guidance the approval path enqueued (e.g. "Operator approved...
            # your original objective was X... proceed now") and injects it
            # as a ``HumanMessage`` — that is the opencode-style "projected
            # history reload" but done INSIDE the graph at the correct point
            # in the message flow, not via ``Command(update=...)`` which would
            # corrupt the checkpoint's channel versions and trigger the model
            # node with stale task_ids (causing the run to terminate silently
            # after the model responds to the injected HumanMessage without
            # ever processing the approved tool_calls).
            #
            # DO NOT add ``update={"messages": [HumanMessage(...)]}`` here.
            # The continuation directive must be enqueued via
            # ``store.enqueue_guidance(run_id=..., body=...)`` by the caller
            # (chat.py resolve endpoint, discord_adapter._resume_approved_run,
            # or chat.recover_persisted_chat_runs) so the
            # ``OperatorGuidanceMiddleware`` injects it at ``before_model``
            # AFTER the approved tools have run and produced ``ToolMessage``
            # results — the correct point in the message flow.
            input_value = Command(resume={"decisions": resume_decisions})
        elif not resume_from_checkpoint:
            messages = _history_to_messages(conversation_history)
            messages.append(HumanMessage(content=prompt))
            input_value = {"messages": messages}

        graph_finished = asyncio.Event()

        async def consume_graph() -> None:
            try:
                async for event in supervisor.astream_events(input_value, config=config, version="v2"):
                    await event_queue.put(("graph", event))
            except BaseException as exc:  # noqa: BLE001 - surface through the generator task
                await event_queue.put(("error", exc))
            finally:
                # Do not enqueue a terminal sentinel here.  Async command
                # tools can still be flushing stdout/stderr after the graph
                # emits its final chain event; the progress pump owns the
                # close barrier once those chunks are drained.
                graph_finished.set()

        async def pump_job_progress() -> None:
            try:
                # JOBS is created alongside the live FastMCP catalog in
                # ``mcp.main``. Importing it from ``mcp.jobs`` raises and,
                # if swallowed, would leave the graph consumer waiting for a
                # terminal progress sentinel forever.
                from ..mcp.main import JOBS  # noqa: PLC0415
            except Exception:  # pragma: no cover - lightweight/runtime tests
                # Some isolated adapter tests intentionally do not bootstrap
                # FastMCP. They still need a deterministic close barrier.
                await graph_finished.wait()
                await event_queue.put(("progress_done", None))
                return
            cursors: dict[str, int] = {}
            while True:
                for event in JOBS.progress_for_run(run_id, cursors):
                    await event_queue.put(("envelope", event))
                if graph_finished.is_set() and not JOBS.has_active_run(run_id):
                    # A job can append its last chunks and become inactive
                    # between the drain above and this check. Read once more
                    # before inserting the terminal sentinel.
                    for event in JOBS.progress_for_run(run_id, cursors):
                        await event_queue.put(("envelope", event))
                    await event_queue.put(("progress_done", None))
                    return
                await asyncio.sleep(0.2)

        graph_task = asyncio.create_task(consume_graph(), name=f"munin-graph-{run_id}")
        progress_task = asyncio.create_task(pump_job_progress(), name=f"munin-job-progress-{run_id}")
        try:
            while True:
                source, payload = await event_queue.get()
                if source == "progress_done":
                    break
                if source == "error":
                    raise payload
                if source == "envelope":
                    yield payload
                    continue

                for envelope in translate_events(payload, run_id=run_id, thinking_state=thinking_state):
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
        finally:
            progress_task.cancel()
            graph_task.cancel()
            await asyncio.gather(progress_task, graph_task, return_exceptions=True)
    finally:
        # Starlette can close a streamed async generator from its disconnect
        # finalizer task rather than the task that created it. ContextVar
        # tokens are task-bound, so resetting one from that finalizer raises a
        # noisy ``Token ... was created in a different Context`` exception.
        # Clear the current context in that defensive case; the originating
        # request context is discarded with its task and cannot leak into the
        # next request.
        for variable, token in (
            (_PE_SINK, tok_pe_sink),
            (_PE_RUN_ID, tok_pe_rid),
            (_OG_RUN_ID, tok_og_rid),
            (ACTIVE_STORE, tok_st),
            (ACTIVE_MODE, tok_mode),
            (ACTIVE_GOAL, tok_goal),
            (ACTIVE_EMITTER, tok_emit),
            (ACTIVE_PLAN_SNAPSHOT, tok_plan),
            (ACTIVE_CONVERSATION_ID, tok_conv),
            (ACTIVE_ACTOR_ID, tok_actor),
        ):
            try:
                variable.reset(token)
            except ValueError:
                variable.set(None)
