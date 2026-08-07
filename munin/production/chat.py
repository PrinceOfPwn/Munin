# tags: [orchestrator, runtime, core, web-ui, coordination, subagent, hitl-approval, ProgressEmitMiddleware, register_chat_routes, _stream_idempotent_replay, sse-streaming, ai-sdk-v5, guidance-api, supervisor-runner, idempotency-key, memory-scoping, actor_id]
"""Deep Agents supervisor → SSE bridge (Fase 1a of issue #9 migration).

This module owns ``POST /api/chat``, ``GET /api/chat/{conversation_id}/stream``
and ``POST /api/chat/{run_id}/guidance``.
It replaces the legacy two-hop coupling (``POST /turns`` + ``GET /events``)
between the Next.js BFF and the Python backend: the AI SDK v5 request now
drives ``supervisor_runner`` directly in-process, streaming the same Munin
envelope shape the frontend translator already understands.

The legacy dispatcher/lease code path (``POST /api/production/conversations/
{id}/turns`` → ``ProductionDispatcher.run_once`` → ``GET /api/runs/{id}/
events``) is intentionally left in place for Fase 1. It gets deleted in
Fase 2 once Arch B is exercised end-to-end.

Envelope contract (mirrors the pre-migration wire format so
``app/src/lib/chat/translator.ts`` needs no changes):

* ``{"kind": "run_state", "run_id": ..., "state": "running"|"completed"|
   "failed"|"cancelled", "content"?: str, "error"?: str}`` — lifecycle
* ``{"kind": "reasoning", "text": ...}`` — assistant token delta
* ``{"kind": "tool_intent", "tool_call_id": ..., "tool_name": ...,
   "input": {...}}`` — before ``wrap_tool_call``
* ``{"kind": "tool_result", "tool_call_id": ..., "output": ...}`` — success
* ``{"kind": "tool_failed", "tool_call_id": ..., "error": ...}`` — exception
* ``{"kind": "tool_output", "tool_call_id": ..., "stream": ..., "text": ...}`` — live command output

Envelopes are emitted by ``ProgressEmitMiddleware`` (tool lifecycle) and
``runtime_adapter.translate_events`` (provider reasoning + run_state), so this handler
is only responsible for framing them as SSE and persisting side-effects.

Follow-up (post-Fase 5): ``POST /api/chat`` now also handles *reconnect* on
an idempotent replay.  A refresh of an in-flight run resubmits with the
same ``idempotency-key``; the handler detects this (``_find_prior_run_id``)
and opens an SSE stream that replays historical ``run_events`` from the
store and then polls for new ones until a terminal ``run_state`` appears
— instead of the previous JSON response, which the AI SDK client had no
way to render.  See :func:`_stream_idempotent_replay`.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import replace
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

log = logging.getLogger("munin.production.chat")

TERMINAL_STATES = {"completed", "failed", "cancelled", "interrupted"}
NON_TERMINAL_RUN_STATES = {"queued", "running", "waiting_for_human"}
CHAT_LEASE_SECONDS = int(os.environ.get("MUNIN_CHAT_LEASE_SECONDS", "120"))
# A short renewable lease makes a crash recoverable without granting a new
# process authority to steal a still-live worker.  Operators can increase the
# duration for a high-latency deployment; the heartbeat keeps legitimate
# long-running work alive either way.
CHAT_LEASE_RENEW_SECONDS = float(
    os.environ.get("MUNIN_CHAT_LEASE_RENEW_SECONDS", "30")
)
CHAT_RECOVERY_POLL_SECONDS = float(
    os.environ.get("MUNIN_CHAT_RECOVERY_POLL_SECONDS", "5")
)
# How often the idempotent-replay SSE stream re-checks the durable/hot store
# for new ``run_events`` and the current ``agent_runs.state``.  0.3s is
# imperceptible in the UI of a multi-minute run; we intentionally avoid an
# in-process pub/sub bus so replay survives across worker processes (see
# :func:`_stream_idempotent_replay`).
CHAT_REPLAY_POLL_SECONDS = float(os.environ.get("MUNIN_CHAT_REPLAY_POLL_SECONDS", "0.3"))

# Active executions are independent of an HTTP/SSE subscriber. The durable
# run-event log remains the cross-process replay source; this map only owns
# the current local task and prevents duplicate launches in one process.
_ACTIVE_RUN_TASKS: dict[str, asyncio.Task[None]] = {}


class _DetachedChatRequest:
    """Minimal request facade for a run that must outlive an SSE client."""

    async def is_disconnected(self) -> bool:
        return False


async def _renew_chat_lease(
    *,
    store: Any,
    run_id: str,
    lease_token: str,
    stop: asyncio.Event,
    lease_lost: asyncio.Event,
) -> None:
    """Keep an executor's lease alive; never revive a fenced/cancelled run."""
    interval = max(
        5.0,
        min(CHAT_LEASE_RENEW_SECONDS, max(5.0, CHAT_LEASE_SECONDS / 3)),
    )
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return
        except TimeoutError:
            pass
        try:
            current = bool(
                store.renew_run_lease(
                    run_id=run_id,
                    lease_token=lease_token,
                    lease_seconds=CHAT_LEASE_SECONDS,
                )
            )
        except Exception:  # noqa: BLE001 - failure must not create a second owner
            log.warning("chat: lease heartbeat failed run_id=%s", run_id, exc_info=True)
            current = False
        if not current:
            lease_lost.set()
            log.warning("chat: executor lost lease run_id=%s", run_id)
            return


async def _checkpoint_available(shared_state: Any, *, thread_id: str) -> bool | None:
    """Return whether the configured async LangGraph saver has this thread.

    ``None`` means that the saver could not be queried, which is distinct from
    an empty checkpoint.  Recovery leaves those runs queued rather than risk a
    duplicate external tool action on an unverifiable persistence backend.
    """
    saver = getattr(shared_state, "langgraph_checkpointer", None)
    getter = getattr(saver, "aget_tuple", None)
    if not callable(getter):
        return False
    try:
        checkpoint = await getter({"configurable": {"thread_id": thread_id}})
    except Exception:  # noqa: BLE001 - fail closed until the next recovery poll
        log.warning("chat: could not query checkpoint run thread=%s", thread_id, exc_info=True)
        return None
    return checkpoint is not None


def _now_ms() -> int:
    return int(time.time() * 1000)


def _sse_frame(envelope: dict[str, Any], *, sequence: int) -> bytes:
    payload = json.dumps(envelope, separators=(",", ":"))
    return f"id: {sequence}\nevent: run-event\ndata: {payload}\n\n".encode()


# Fase 3 (issue #9): ``_ChatStateAdapter`` — the composition shim that fused
# ``SharedStateStore`` (tools/soul/settings) with ``ProductionStore``
# (guidance queue) — was retired.  :mod:`munin.server` now binds
# ``ProductionStore.consume_pending_guidance`` onto the single
# ``SharedStateStore`` instance at process startup, so a plain
# ``SharedStateStore`` reference already satisfies both the tool gateway
# and :class:`OperatorGuidanceMiddleware`.  ``register_chat_routes`` now
# accepts that instance via the ``shared_state`` argument and passes it
# straight to ``supervisor_runner``.


def _claim_direct(store: Any, *, run_id: str) -> tuple[str, str]:
    """Move a queued run to ``running`` without the lease-worker claim path.

    Returns ``(lease_token, assistant_message_id)``. The lease is long
    (``MUNIN_CHAT_LEASE_SECONDS``) because a single request handler owns the
    run end-to-end — no other worker will try to steal it.

    Fase 4 (issue #9): agent_runs live in the hot SQLite backend and the
    placeholder message row lives in the durable Turso backend, so the
    two writes can no longer share one transaction.  When ``store`` is a
    :class:`MuninStore` façade we defer to its ``claim_run_direct``
    method (which handles the split atomically enough for the semantics
    we want); otherwise the pre-Fase-4 single-transaction path is used.
    """
    claim = getattr(store, "claim_run_direct", None)
    if callable(claim):
        return claim(run_id=run_id)
    with store._transaction() as conn:  # noqa: SLF001 - store aggregate txn
        row = conn.execute(
            "SELECT * FROM agent_runs WHERE id=? AND state='queued'",
            (run_id,),
        ).fetchone()
        if not row:
            raise RuntimeError(f"run {run_id} is not queued (already running or terminal)")
        now = _now_ms()
        lease_token = secrets.token_urlsafe(32)
        next_epoch = int(row["fencing_epoch"]) + 1
        conn.execute(
            "UPDATE agent_runs SET state='running',lease_worker_id=?,lease_token=?,"
            "lease_expires_at_ms=?,fencing_epoch=?,state_version=state_version+1,"
            "updated_at_ms=? WHERE id=? AND state='queued'",
            (
                f"chat-{os.getpid()}",
                lease_token,
                now + max(60, CHAT_LEASE_SECONDS) * 1000,
                next_epoch,
                now,
                run_id,
            ),
        )
        conn.execute(
            "UPDATE messages SET status='running',updated_at_ms=?,version=version+1 WHERE id=?",
            (now, row["assistant_message_id"]),
        )
        store._append_event(  # noqa: SLF001
            conn,
            run_id=run_id,
            kind="run.claimed",
            payload={"worker_id": f"chat-{os.getpid()}", "fencing_epoch": next_epoch},
        )
    return lease_token, str(row["assistant_message_id"])


def _update_placeholder(store: Any, *, assistant_message_id: str, content: str) -> None:
    # Fase 4: prefer the façade's ``update_placeholder_content`` which
    # writes against the durable backend (messages row lives there);
    # fall back to the bare ``ProductionStore`` code path for tests.
    update = getattr(store, "update_placeholder_content", None)
    if callable(update):
        update(assistant_message_id=assistant_message_id, content=content)
        return
    safe = content[-1_000_000:]
    now = _now_ms()
    try:
        with store._transaction() as conn:  # noqa: SLF001
            conn.execute(
                "UPDATE messages SET content=?,content_hash=?,updated_at_ms=?,version=version+1 "
                "WHERE id=? AND kind='assistant_placeholder' AND status='running'",
                (safe, hashlib.sha256(safe.encode()).hexdigest(), now, assistant_message_id),
            )
    except Exception:  # noqa: BLE001 - live-preview writes must not sink a run
        log.debug("chat: placeholder update failed", exc_info=True)


def _persist_envelope(
    store: Any,
    envelope: dict[str, Any],
    *,
    run_id: str,
    assistant_message_id: str,
    assistant_buffer: list[str],
) -> None:
    """Mirror an in-flight envelope to Turso (best-effort)."""
    kind = envelope.get("kind")
    try:
        if kind in {"assistant_text", "reasoning"}:
            text = str(envelope.get("text") or "")
            if text:
                assistant_buffer.append(text)
                _update_placeholder(
                    store,
                    assistant_message_id=assistant_message_id,
                    content="".join(assistant_buffer),
                )
            return
        if kind == "provider_reasoning":
            text = str(envelope.get("text") or "")
            if text:
                store.append_reasoning_event(
                    run_id=run_id,
                    kind="provider_reasoning",
                    content=text,
                    provider=str(envelope.get("provider") or "openai-compatible"),
                    persistence_enabled=True,
                    agent_name="munin",
                    step=max(0, int(envelope.get("step") or 0)),
                )
            return
        if kind == "activity":
            text = str(envelope.get("text") or "").strip()
            if text:
                # Persist only safe operational telemetry.  This is distinct
                # from provider/private reasoning and supplies reconnecting
                # clients with a faithful activity timeline.
                store.append_reasoning_event(
                    run_id=run_id,
                    kind="operational_summary",
                    content=text,
                    provider="",
                    persistence_enabled=True,
                    agent_name="munin",
                    step=0,
                )
            return
        if kind == "tool_intent":
            store.append_tool_call(
                run_id=run_id,
                agent_name="munin",
                tool_name=str(envelope.get("tool_name") or "unknown"),
                state="running",
                arguments=envelope.get("input") or {},
                tool_call_id=envelope.get("tool_call_id") or None,
            )
            return
        if kind == "tool_result":
            store.append_tool_call(
                run_id=run_id,
                agent_name="munin",
                tool_name=str(envelope.get("tool_name") or "unknown"),
                state="completed",
                arguments={},
                result={"summary": str(envelope.get("output") or "")[:4000]},
                tool_call_id=envelope.get("tool_call_id") or None,
            )
            return
        if kind == "tool_failed":
            store.append_tool_call(
                run_id=run_id,
                agent_name="munin",
                tool_name=str(envelope.get("tool_name") or "unknown"),
                state="failed",
                arguments={},
                result={"error": str(envelope.get("error") or "")[:4000]},
                tool_call_id=envelope.get("tool_call_id") or None,
            )
            return
        if kind == "tool_output":
            append_output = getattr(store, "append_tool_output_event", None)
            if append_output is not None and str(envelope.get("text") or ""):
                append_output(
                    run_id=run_id,
                    tool_name=str(envelope.get("tool_name") or "unknown"),
                    tool_call_id=str(envelope.get("tool_call_id") or ""),
                    job_id=str(envelope.get("job_id") or ""),
                    stream=str(envelope.get("stream") or "stdout"),
                    text=str(envelope.get("text") or ""),
                    sequence=int(envelope.get("sequence") or 0),
                    elapsed_ms=int(envelope.get("elapsed_ms") or 0),
                    final=bool(envelope.get("final")),
                )
            return
        if kind == "tool_heartbeat":
            # Heartbeats are transient transport signals. The output chunks
            # and terminal tool lifecycle are durable, so replay never needs
            # to reproduce every pulse.
            return
    except Exception:  # noqa: BLE001 - persistence best-effort
        log.debug("chat: envelope persistence failed (kind=%s)", kind, exc_info=True)


def _finalize(
    store: Any,
    *,
    run_id: str,
    lease_token: str,
    content: str,
    outcome: str,
    conversation_id: str,
) -> None:
    if outcome not in TERMINAL_STATES:
        outcome = "completed"
    try:
        accepted = store.complete_run(
            run_id=run_id, lease_token=lease_token, content=content, outcome=outcome
        )
    except Exception:  # noqa: BLE001
        log.debug("chat: complete_run raised", exc_info=True)
        accepted = False
    if accepted and conversation_id:
        try:
            store.append_conversation_broadcast(
                conversation_id=conversation_id,
                kind="run-transition",
                payload={"run_id": run_id, "state": outcome},
            )
        except Exception:  # noqa: BLE001
            log.debug("chat: run-transition broadcast failed", exc_info=True)


# ---------------------------------------------------------------------------
# Idempotent-replay reconnect (issue #9 follow-up)
#
# When the AI SDK client refreshes mid-run (browser reload, network blip),
# the resubmitted ``POST /api/chat`` carries the SAME ``idempotency-key`` as
# the in-flight run.  Pre-follow-up behaviour was to return a JSON envelope
# ``{ok:true, data:{idempotent_replay:true, run:{...}}}`` which the AI SDK
# client didn't know how to render — the tab was effectively stuck.
#
# The reconnect flow below detects that case *before* the "another run is
# active" guard rejects the request, and instead opens a fresh SSE stream
# that:
#
#   1. Re-emits historical ``run_events`` from the store as Munin envelopes.
#   2. If the run is still in a non-terminal state, keeps polling
#      ``store.run_events_after(run_id, after_sequence=cursor)`` at
#      ``CHAT_REPLAY_POLL_SECONDS`` intervals until a ``run.<terminal>``
#      event appears.
#   3. Closes the stream with ``event: close`` just like the primary path.
#
# ~~ Why polling instead of an in-process pub/sub bus?  ~~
# The original request handler (the one that owns the supervisor loop)
# runs in a different asyncio task; sharing an ``asyncio.Queue`` per run
# would require a process-wide registry and would break the moment we
# ever run Munin behind multiple worker processes.  Polling the store is
# transparent to that topology: the durable/hot backends already are the
# single source of truth for run events, and the ~300ms cadence is
# invisible to a UI that measures elapsed time in minutes.
# ---------------------------------------------------------------------------


def _find_prior_run_id(
    store: Any,
    *,
    conversation_id: str,
    actor_id: str,
    idempotency_key: str,
) -> str | None:
    """Return the ``agent_runs.id`` matching ``(conv, actor, key)``, if any.

    Consults the hot backend first (in-flight runs) then falls back to
    durable (already-migrated / terminal runs).  Uses the raw ``_hot_read_only``
    / ``_read_only`` context managers that :class:`MuninStore` already
    exposes; no new store method is needed.  Returns ``None`` when the tuple
    is unseen (i.e. a genuinely new turn).
    """
    query = (
        "SELECT id FROM agent_runs "
        "WHERE conversation_id=? AND actor_id=? AND idempotency_key=? "
        "LIMIT 1"
    )
    args = (conversation_id, actor_id, idempotency_key)
    for opener_name in ("_hot_read_only", "_read_only"):
        opener = getattr(store, opener_name, None)
        if opener is None:
            continue
        try:
            with opener() as conn:
                row = conn.execute(query, args).fetchone()
                if row:
                    return str(row["id"])
        except Exception:  # noqa: BLE001 - best-effort probe
            log.debug("chat: %s idempotent-replay probe failed", opener_name, exc_info=True)
    return None


def _envelope_from_event(
    event: dict[str, Any],
    *,
    run_id: str,
    tools_by_eid: dict[str, dict[str, Any]],
    tools_by_call_id: dict[str, dict[str, Any]] | None = None,
    reasoning_by_eid: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Translate one ``run_events`` row into a Munin envelope.

    The BFF's ``normalizeRunEvent`` already understands the dotted
    ``reasoning.<kind>`` / ``tool.<state>`` / ``run.<state>`` shape, so we
    could emit rows verbatim — but the ``payload_json`` we persist
    intentionally omits large blobs (the reasoning text lives in
    ``reasoning_events.content``; tool args/results live in
    ``tool_calls``).  We enrich here so the replay stream carries the
    same fidelity as the primary path.
    """
    kind = str(event.get("kind") or "")
    payload = event.get("payload") or {}
    eid = str(event.get("id") or "")

    if kind.startswith("run."):
        state = kind.split(".", 1)[1]
        if state == "claimed":
            return {"kind": "run_state", "run_id": run_id, "state": "running"}
        if state in TERMINAL_STATES:
            envelope: dict[str, Any] = {"kind": "run_state", "run_id": run_id, "state": state}
            if isinstance(payload, dict) and payload.get("assistant_message_id"):
                envelope["assistant_message_id"] = payload["assistant_message_id"]
            return envelope
        # queued/retried/etc. — nothing actionable client-side during replay.
        return None

    if kind.startswith("tool."):
        state = kind.split(".", 1)[1]
        if state == "output":
            if not isinstance(payload, dict):
                return None
            return {
                "kind": "tool_output",
                "run_id": run_id,
                "tool_call_id": payload.get("tool_call_id") or "",
                "tool_name": payload.get("tool_name") or "unknown",
                "job_id": payload.get("job_id") or "",
                "stream": payload.get("stream") or "stdout",
                "text": payload.get("text") or "",
                "sequence": int(payload.get("sequence") or 0),
                "elapsed_ms": int(payload.get("elapsed_ms") or 0),
                "final": bool(payload.get("final")),
            }
        tool_call_id = payload.get("tool_call_id") if isinstance(payload, dict) else None
        detail = tools_by_eid.get(eid) or {}
        if not detail and tools_by_call_id and tool_call_id:
            # A tool lifecycle appends one run-event per state, while the
            # read-model row keeps the original running-event id.  Completed
            # and failed replay events therefore need the stable call id.
            detail = tools_by_call_id.get(str(tool_call_id)) or {}
        tool_call_id = tool_call_id or detail.get("id")
        tool_name = payload.get("tool") if isinstance(payload, dict) else None
        tool_name = tool_name or detail.get("tool_name") or "unknown"
        if state == "running":
            return {
                "kind": "tool_intent",
                "run_id": run_id,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "input": detail.get("arguments") or {},
            }
        if state == "completed":
            result = detail.get("result") or {}
            summary = result.get("summary") if isinstance(result, dict) else result
            return {
                "kind": "tool_result",
                "run_id": run_id,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "output": summary if summary is not None else "",
            }
        if state == "failed":
            result = detail.get("result") or {}
            err = result.get("error") if isinstance(result, dict) else str(result or "")
            return {
                "kind": "tool_failed",
                "run_id": run_id,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "error": err,
            }
        return None

    if kind.startswith("reasoning."):
        # Assistant text lives in the placeholder. Provider-emitted reasoning
        # and the operational timeline each have their own durable event type.
        detail = reasoning_by_eid.get(eid) or {}
        text = str(detail.get("content") or "")
        if not text:
            return None
        detail_kind = str(detail.get("kind") or "")
        if detail_kind == "provider_reasoning":
            return {
                "kind": "provider_reasoning",
                "run_id": run_id,
                "text": text,
                "provider": str(detail.get("provider") or "openai-compatible"),
                "step": max(0, int(detail.get("step") or 0)),
            }
        if detail_kind == "operational_summary":
            return {"kind": "activity", "run_id": run_id, "text": text}
        if detail_kind == "operator_guidance":
            return {"kind": "guidance", "run_id": run_id, "text": text}
        return None

    if kind == "human_request.created":
        # The nonce is never persisted in clear text. Replay mints a fresh
        # one for the authenticated participant via
        # ``_pending_human_request_envelopes`` below.
        return None

    if kind.startswith("human_request.") or kind.startswith("subagent."):
        # Pass through — the BFF normalizer folds ``payload`` into the
        # top-level envelope and the client translator understands both.
        envelope = {"kind": kind, "run_id": run_id}
        if isinstance(payload, dict):
            envelope.update(payload)
        return envelope

    return None


def _load_run_detail(store: Any, *, actor_id: str, run_id: str) -> dict[str, Any]:
    """Fetch reasoning/tool bodies for the current run (best-effort)."""
    try:
        return store.get_run_detail_for_actor(actor_id=actor_id, run_id=run_id)
    except (PermissionError, KeyError):
        return {"reasoning": [], "tools": []}
    except Exception:  # noqa: BLE001 - never let enrichment sink a replay
        log.debug("chat: replay detail fetch failed run_id=%s", run_id, exc_info=True)
        return {"reasoning": [], "tools": []}


def _pending_human_request_envelopes(
    store: Any, *, actor_id: str, run_id: str, emitted_ids: set[str]
) -> list[dict[str, Any]]:
    """Return replayable HITL cards with a fresh server-issued nonce."""
    detail = _load_run_detail(store, actor_id=actor_id, run_id=run_id)
    envelopes: list[dict[str, Any]] = []
    for item in detail.get("human_requests") or []:
        request_id = str(item.get("id") or "")
        if not request_id or request_id in emitted_ids or item.get("state") != "waiting":
            continue
        try:
            minted = store.reissue_human_decision_nonce(
                actor_id=actor_id, request_id=request_id
            )
        except (KeyError, PermissionError):
            continue
        scope = item.get("scope") if isinstance(item.get("scope"), dict) else {}
        actions = scope.get("actions") if isinstance(scope, dict) else []
        names = [str(action.get("name") or "unknown") for action in actions if isinstance(action, dict)]
        envelopes.append(
            {
                "kind": "human_request",
                "run_id": run_id,
                "request_id": request_id,
                "tool_name": names[0] if len(names) == 1 else "multiple_tools",
                "args": {"actions": actions},
                "nonce": minted["nonce"],
                "choices": item.get("choices") or ["approve", "reject"],
            }
        )
        emitted_ids.add(request_id)
    return envelopes


def _current_placeholder_text(
    store: Any, *, actor_id: str, conversation_id: str, assistant_message_id: str
) -> str:
    """Snapshot the assistant placeholder body via ``get_conversation``.

    Used only at connect time so the replay client sees the assistant text
    accumulated so far.  Live increments during the poll loop are not
    re-streamed — the primary handler writes them straight to
    ``messages.content`` without emitting a ``run_events`` row, so there
    is no cheap way to detect a diff without either a schema change or an
    in-process pub/sub bus (both out of scope here — see module doc).
    """
    if not assistant_message_id or not conversation_id:
        return ""
    try:
        aggregate = store.get_conversation(
            actor_id=actor_id, conversation_id=conversation_id
        )
    except Exception:  # noqa: BLE001
        return ""
    for message in aggregate.get("messages", []):
        if str(message.get("id")) == assistant_message_id:
            return str(message.get("content") or "")
    return ""


async def _stream_idempotent_replay(
    request: Request,
    *,
    store: Any,
    actor_id: str,
    run_id: str,
) -> AsyncIterator[bytes]:
    """SSE stream that re-attaches to an existing (possibly in-flight) run.

    Behaviour:

    * Preamble + ``run_state: running`` (or the current terminal state).
    * One-shot ``reasoning`` envelope carrying the assistant placeholder's
      accumulated text, so the client renders the answer-so-far.
    * All historical ``run_events`` translated to Munin envelopes.
    * Polling loop against ``store.run_events_after`` +
      ``store.get_run`` until a terminal state is observed OR the client
      disconnects.
    * On terminal, one final ``run_state`` envelope carrying the final
      assistant content (best-effort re-read).
    * Standard ``event: close`` sentinel.
    """
    yield b": munin-chat-replay v1\n\n"
    sequence = 0

    try:
        run = store.get_run(run_id)
    except KeyError:
        sequence += 1
        yield _sse_frame(
            {
                "kind": "run_state",
                "run_id": run_id,
                "state": "failed",
                "error": "run not found for replay",
            },
            sequence=sequence,
        )
        yield b"event: close\ndata: {}\n\n"
        return

    conversation_id = str(run.get("conversation_id") or "")
    assistant_message_id = str(run.get("assistant_message_id") or "")
    current_state = str(run.get("state") or "queued")
    initial_state = current_state if current_state in (TERMINAL_STATES | {"waiting_for_human"}) else "running"

    sequence += 1
    yield _sse_frame(
        {"kind": "run_state", "run_id": run_id, "state": initial_state},
        sequence=sequence,
    )

    placeholder = _current_placeholder_text(
        store,
        actor_id=actor_id,
        conversation_id=conversation_id,
        assistant_message_id=assistant_message_id,
    )
    if placeholder:
        sequence += 1
        yield _sse_frame(
            {"kind": "assistant_text", "run_id": run_id, "text": placeholder},
            sequence=sequence,
        )
    emitted_placeholder = placeholder

    cursor = 0
    terminal_observed = current_state in TERMINAL_STATES
    emitted_human_request_ids: set[str] = set()

    while True:
        try:
            events = store.run_events_after(run_id=run_id, after_sequence=cursor)
        except KeyError:
            events = []
        except Exception:  # noqa: BLE001 - never let a transient store error kill the SSE
            log.debug("chat: replay events poll failed run_id=%s", run_id, exc_info=True)
            events = []

        if events:
            detail = _load_run_detail(store, actor_id=actor_id, run_id=run_id)
            tools_by_eid = {
                str(row.get("event_id")): row for row in (detail.get("tools") or [])
            }
            tools_by_call_id = {
                str(row.get("id")): row for row in (detail.get("tools") or []) if row.get("id")
            }
            reasoning_by_eid = {
                str(row.get("event_id")): row for row in (detail.get("reasoning") or [])
            }
            for event in events:
                cursor = max(cursor, int(event.get("sequence") or 0))
                envelope = _envelope_from_event(
                    event,
                    run_id=run_id,
                    tools_by_eid=tools_by_eid,
                    tools_by_call_id=tools_by_call_id,
                    reasoning_by_eid=reasoning_by_eid,
                )
                if envelope is None:
                    continue
                sequence += 1
                yield _sse_frame(envelope, sequence=sequence)
                if envelope.get("kind") == "run_state" and str(
                    envelope.get("state") or ""
                ) in TERMINAL_STATES:
                    terminal_observed = True

        if terminal_observed:
            break

        # Re-check the run's current state; if it terminated between polls
        # without an accompanying run_events row (defensive), still exit.
        try:
            latest = store.get_run(run_id)
            latest_state = str(latest.get("state") or "")
            if latest_state in TERMINAL_STATES:
                # The corresponding ``run.<state>`` event should show up on
                # the next tick; loop once more so we surface it verbatim.
                terminal_observed = True
            elif latest_state == "waiting_for_human":
                for envelope in _pending_human_request_envelopes(
                    store,
                    actor_id=actor_id,
                    run_id=run_id,
                    emitted_ids=emitted_human_request_ids,
                ):
                    sequence += 1
                    yield _sse_frame(envelope, sequence=sequence)
                # A HITL request is durable and the graph is checkpointed.
                # Close this viewer; the operator action starts a new durable
                # replay stream after ``Command(resume=...)`` is launched.
                break
        except KeyError:
            terminal_observed = True

        if terminal_observed:
            continue  # let the loop pick up the terminal event on next SELECT

        # The detached executor keeps the current visible answer in the
        # placeholder. Reading its suffix at the normal replay cadence gives
        # reconnecting clients live text without a second in-memory token bus
        # or one persisted event per token.
        current_placeholder = _current_placeholder_text(
            store,
            actor_id=actor_id,
            conversation_id=conversation_id,
            assistant_message_id=assistant_message_id,
        )
        if current_placeholder != emitted_placeholder:
            delta = (
                current_placeholder[len(emitted_placeholder):]
                if current_placeholder.startswith(emitted_placeholder)
                else current_placeholder
            )
            if delta:
                sequence += 1
                yield _sse_frame(
                    {"kind": "assistant_text", "run_id": run_id, "text": delta},
                    sequence=sequence,
                )
            emitted_placeholder = current_placeholder

        if await request.is_disconnected():
            log.info("chat: replay client disconnected run_id=%s", run_id)
            break

        await asyncio.sleep(CHAT_REPLAY_POLL_SECONDS)

    # Best-effort: attach the final placeholder body to the closing frame so
    # the AI SDK client renders the completed message even if it missed
    # intermediate reasoning deltas.
    final_content = _current_placeholder_text(
        store,
        actor_id=actor_id,
        conversation_id=conversation_id,
        assistant_message_id=assistant_message_id,
    )
    if final_content and final_content != emitted_placeholder:
        final_delta = (
            final_content[len(emitted_placeholder):]
            if final_content.startswith(emitted_placeholder)
            else final_content
        )
        if final_delta:
            sequence += 1
            yield _sse_frame(
                {
                    "kind": "assistant_text",
                    "run_id": run_id,
                    "text": final_delta,
                    "replay_final": True,
                },
                sequence=sequence,
            )

    yield b"event: close\ndata: {}\n\n"


async def _stream_chat(
    request: Request,
    *,
    store: Any,
    shared_state: Any,
    actor_info: dict[str, Any],
    run_id: str,
    conversation_id: str,
    prompt: str,
    conversation_history: list[dict[str, Any]],
    assistant_message_id: str,
    lease_token: str,
    resume_decisions: list[dict[str, Any]] | None = None,
    resume_from_checkpoint: bool = False,
    mode: Any = None,
    goal: dict[str, Any] | None = None,
) -> AsyncIterator[bytes]:
    from ..core.llm_client import LLMClient
    from ..core.runtime_adapter import supervisor_runner
    from ..mcp.config import get_settings
    from ..mcp.shared_state import SharedStateStore

    settings = get_settings()
    # The active operator-owned BYOK profile overrides process defaults for
    # this run only.  Keys are decrypted server-side and never cross the BFF.
    try:
        profiles = store.list_provider_profiles(actor_id=str(actor_info.get("id") or ""))
        active_profile = next((profile for profile in profiles if profile.get("active")), None)
        if active_profile:
            plaintext_key = store.reveal_provider_key(
                actor_id=str(actor_info.get("id") or ""),
                profile_id=str(active_profile["id"]),
            )
            settings = replace(
                settings,
                llm_base_url=str(active_profile.get("base_url") or settings.llm_base_url),
                llm_model=str(active_profile.get("model") or settings.llm_model),
                llm_api_key=plaintext_key,
            )
            log.info(
                "chat: using provider profile id=%s provider=%s model=%s",
                active_profile.get("id"),
                active_profile.get("provider"),
                active_profile.get("model"),
            )
    except Exception as exc:  # noqa: BLE001 - surface a bad selected profile clearly
        log.warning("chat: active provider profile unavailable: %s", exc)
        _finalize(
            store,
            run_id=run_id,
            lease_token=lease_token,
            content=f"Provider profile unavailable: {exc}",
            outcome="failed",
            conversation_id=conversation_id,
        )
        yield b": munin-chat-stream v1\n\n"
        yield _sse_frame(
            {"kind": "run_state", "run_id": run_id, "state": "failed", "error": str(exc)},
            sequence=1,
        )
        yield b"event: close\ndata: {}\n\n"
        return

    try:
        model = LLMClient(settings).make_langchain()
    except Exception as exc:  # noqa: BLE001
        log.warning("chat: model init failed: %s", exc)
        _finalize(
            store,
            run_id=run_id,
            lease_token=lease_token,
            content=f"Model unavailable: {exc}",
            outcome="failed",
            conversation_id=conversation_id,
        )
        yield b": munin-chat-stream v1\n\n"
        yield _sse_frame(
            {"kind": "run_state", "run_id": run_id, "state": "failed", "error": str(exc)},
            sequence=1,
        )
        yield b"event: close\ndata: {}\n\n"
        return

    # Fase 3: the shared_state instance is created ONCE in ``munin.server``
    # (or, for the legacy ``app_from_environment`` shim, lazily here) and
    # already carries ``consume_pending_guidance`` bound from the
    # ProductionStore.  No adapter needed.
    if shared_state is None:
        shared_state = SharedStateStore(settings)
        with suppress(Exception):
            shared_state.consume_pending_guidance = store.consume_pending_guidance  # type: ignore[assignment]
            shared_state.authorize_approved_tool_call = store.authorize_approved_tool_call  # type: ignore[assignment]

    lease_heartbeat_stop = asyncio.Event()
    lease_lost = asyncio.Event()
    lease_heartbeat = asyncio.create_task(
        _renew_chat_lease(
            store=store,
            run_id=run_id,
            lease_token=lease_token,
            stop=lease_heartbeat_stop,
            lease_lost=lease_lost,
        ),
        name=f"munin-chat-lease-{run_id}",
    )

    yield b": munin-chat-stream v1\n\n"
    sequence = 0

    sequence += 1
    yield _sse_frame(
        {"kind": "run_state", "run_id": run_id, "state": "running"},
        sequence=sequence,
    )
    try:
        store.append_conversation_broadcast(
            conversation_id=conversation_id,
            kind="run-transition",
            payload={"run_id": run_id, "state": "running"},
        )
    except Exception:  # noqa: BLE001
        log.debug("chat: initial broadcast failed", exc_info=True)

    assistant_buffer: list[str] = []
    finalized = False
    final_state = "completed"
    final_content = ""
    final_error: str | None = None
    paused_for_human = False

    runner_stream = supervisor_runner(
        prompt,
        run_id=run_id,
        conversation_id=conversation_id,
        store=shared_state,
        model=model,
        conversation_history=conversation_history,
        thread_id=conversation_id or run_id,
        human_request_store=store,
        resume_decisions=resume_decisions,
        resume_from_checkpoint=resume_from_checkpoint,
        mode=mode,
        goal=goal,
        actor_id=str(actor_info.get("id") or ""),
    )
    try:
        async for envelope in runner_stream:
            if lease_lost.is_set():
                # Another worker has fenced us or an operator cancelled the
                # run.  Do not persist or execute another graph step.
                final_state = "lease_lost"
                break
            _persist_envelope(
                store,
                envelope,
                run_id=run_id,
                assistant_message_id=assistant_message_id,
                assistant_buffer=assistant_buffer,
            )

            sequence += 1
            yield _sse_frame(envelope, sequence=sequence)

            if envelope.get("kind") == "human_request":
                paused_for_human = True
                final_state = "waiting_for_human"
                break

            if envelope.get("kind") == "run_state":
                state = str(envelope.get("state") or "")
                if state in TERMINAL_STATES:
                    final_state = state
                    final_content = str(envelope.get("content") or "") or "".join(assistant_buffer)
                    final_error = envelope.get("error")  # type: ignore[assignment]
                    _finalize(
                        store,
                        run_id=run_id,
                        lease_token=lease_token,
                        content=final_content or "(no response)",
                        outcome=state,
                        conversation_id=conversation_id,
                    )
                    finalized = True
                    break

            if await request.is_disconnected():
                log.info("chat: client disconnected, aborting run_id=%s", run_id)
                break

        if not finalized and not paused_for_human and not lease_lost.is_set():
            final_content = "".join(assistant_buffer) or "(no response)"
            _finalize(
                store,
                run_id=run_id,
                lease_token=lease_token,
                content=final_content,
                outcome="completed",
                conversation_id=conversation_id,
            )
            sequence += 1
            yield _sse_frame(
                {
                    "kind": "run_state",
                    "run_id": run_id,
                    "state": "completed",
                    "content": final_content,
                },
                sequence=sequence,
            )
            finalized = True
            final_state = "completed"
    except Exception as exc:  # noqa: BLE001 - durable failure boundary
        log.exception("chat: supervisor run failed run_id=%s", run_id)
        final_error = str(exc)
        _finalize(
            store,
            run_id=run_id,
            lease_token=lease_token,
            content=f"Operation failed: {exc}",
            outcome="failed",
            conversation_id=conversation_id,
        )
        sequence += 1
        yield _sse_frame(
            {"kind": "run_state", "run_id": run_id, "state": "failed", "error": str(exc)},
            sequence=sequence,
        )
        final_state = "failed"
    finally:
        # Close the runner in the request task before a HITL break. Relying on
        # implicit async-generator finalization can move ContextVar cleanup to
        # Starlette's finalizer task and lose the per-run context.
        with suppress(Exception):
            await runner_stream.aclose()
        lease_heartbeat_stop.set()
        lease_heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await lease_heartbeat

    yield b"event: close\ndata: {}\n\n"
    log.info(
        "chat: run_id=%s final_state=%s actor=%s error=%s",
        run_id,
        final_state,
        actor_info.get("username") or actor_info.get("id"),
        final_error,
    )


async def _execute_chat_run_detached(**kwargs: Any) -> None:
    """Drain a supervisor stream while persisting it, without an HTTP owner."""
    run_id = str(kwargs["run_id"])
    try:
        async for _frame in _stream_chat(_DetachedChatRequest(), **kwargs):
            # `_stream_chat` owns persistence and finalization. Connected
            # clients receive those frames through the durable replay stream.
            pass
    except Exception:  # noqa: BLE001 - never leave a task failure invisible
        log.exception("chat: detached executor crashed run_id=%s", run_id)
    finally:
        _ACTIVE_RUN_TASKS.pop(run_id, None)


def _launch_chat_run(**kwargs: Any) -> None:
    """Start at most one detached executor for an already-claimed run."""
    run_id = str(kwargs["run_id"])
    existing = _ACTIVE_RUN_TASKS.get(run_id)
    if existing is not None and not existing.done():
        return
    _ACTIVE_RUN_TASKS[run_id] = asyncio.create_task(
        _execute_chat_run_detached(**kwargs), name=f"munin-chat-{run_id}"
    )


async def recover_persisted_chat_runs(*, store: Any, shared_state: Any) -> list[str]:
    """Claim and relaunch durable chat work after a server restart.

    The store performs the only state mutation needed for crash recovery:
    expired ``running`` leases are fenced and changed to ``queued``.  This
    worker then claims queued rows atomically.  It never selects
    ``waiting_for_human`` or ``cancelled`` rows; approved HITL rows use the
    persisted ``Command`` decision while an orphaned ordinary run continues
    from its LangGraph checkpoint using the same conversation thread id.
    """
    requeue = getattr(store, "requeue_expired_runs_for_resume", None)
    candidates_for = getattr(store, "list_queued_chat_recovery_candidates", None)
    if not callable(requeue) or not callable(candidates_for):
        log.warning("chat: store does not support durable chat recovery")
        return []

    try:
        expired = requeue()
        if expired:
            log.info("chat: queued %d expired run(s) for checkpoint recovery", len(expired))
        candidates = candidates_for()
    except Exception:  # noqa: BLE001 - startup recovery must not block serving
        log.exception("chat: unable to enumerate durable recovery candidates")
        return []

    launched: list[str] = []
    for candidate in candidates:
        run_id = str(candidate["run_id"])
        existing = _ACTIVE_RUN_TASKS.get(run_id)
        if existing is not None and not existing.done():
            continue

        resume_decisions = candidate.get("resume_decisions")
        resume_from_checkpoint = bool(candidate.get("resume_from_checkpoint"))
        if resume_decisions is not None or resume_from_checkpoint:
            checkpoint_state = await _checkpoint_available(
                shared_state,
                thread_id=str(candidate["conversation_id"]),
            )
            if checkpoint_state is None:
                # A transient checkpoint-store failure must not turn into a
                # fresh invocation that could duplicate a tool call.
                continue
            if resume_decisions is not None and not checkpoint_state:
                log.error(
                    "chat: HITL run has no checkpoint; leaving queued run_id=%s", run_id
                )
                continue
            # An empty checkpoint means the process failed before LangGraph
            # accepted the original input, so restarting the original turn is
            # safe and is the only useful recovery action.
            resume_from_checkpoint = bool(checkpoint_state and resume_from_checkpoint)

        try:
            execution = store.run_execution_context(run_id=run_id)
            lease_token, assistant_message_id = _claim_direct(store, run_id=run_id)
        except (KeyError, RuntimeError):
            # Another server (or a live POST handler) won the fenced claim.
            continue
        except Exception:  # noqa: BLE001 - leave the queued row for the next poll
            log.exception("chat: unable to claim recovery candidate run_id=%s", run_id)
            continue

        # Fase 3: resume with the same operation contract and persistent goal
        # the original turn was created with (persisted on the agent_runs row).
        recovery_goal: dict[str, Any] | None = None
        try:
            recovery_goal = store.get_goal_for_conversation(
                conversation_id=str(candidate["conversation_id"])
            )
        except Exception:  # noqa: BLE001 - goal hydration must not block recovery
            recovery_goal = None
        goal_id = execution.get("goal_id")
        if recovery_goal and goal_id and str(recovery_goal.get("id")) != str(goal_id):
            recovery_goal = None

        # When recovering a HITL-approved run after a process restart, inject
        # a continuation directive via the durable guidance queue so the
        # OperatorGuidanceMiddleware drains it at the next ``before_model``
        # hook (AFTER the approved tools execute). This is the opencode-style
        # "projected history reload" but done inside the graph at the correct
        # point — NOT via Command(update=...) which would corrupt the
        # checkpoint's channel versions (see runtime_adapter.py comment).
        if resume_decisions is not None:
            recovery_prompt = str(execution.get("message") or "")
            guidance_body = (
                "Operator approved the pending tool execution. "
                "Resume the approved action, incorporate its result, "
                "and continue the workflow toward the original objective."
            )
            if recovery_prompt:
                guidance_body = (
                    f"Operator approved the pending tool execution. "
                    f"Your original objective was: \"{recovery_prompt[:500]}\". "
                    f"Resume the approved action, incorporate its result, "
                    f"and continue the workflow toward that objective. "
                    f"Do NOT ask the operator to repeat the objective — proceed now."
                )
            with suppress(Exception):
                store.enqueue_guidance(
                    run_id=run_id,
                    actor_id=str(candidate.get("actor_id") or ""),
                    actor_username="recovery",
                    body=guidance_body,
                )

        _launch_chat_run(
            store=store,
            shared_state=shared_state,
            actor_info={"id": str(candidate["actor_id"])},
            run_id=run_id,
            conversation_id=str(candidate["conversation_id"]),
            prompt=str(execution.get("message") or ""),
            conversation_history=list(execution.get("history") or []),
            assistant_message_id=assistant_message_id,
            lease_token=lease_token,
            resume_decisions=resume_decisions,
            resume_from_checkpoint=resume_from_checkpoint,
            mode=execution.get("mode") or "standard",
            goal=recovery_goal,
        )
        launched.append(run_id)
    return launched


async def chat_recovery_loop(*, store: Any, shared_state: Any) -> None:
    """Poll for expired owners for the lifetime of the ASGI process."""
    while True:
        try:
            await recover_persisted_chat_runs(store=store, shared_state=shared_state)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one failed scan must not stop recovery
            log.exception("chat: durable recovery scan failed")
        await asyncio.sleep(max(1.0, CHAT_RECOVERY_POLL_SECONDS))


def start_chat_recovery_worker(*, store: Any, shared_state: Any) -> asyncio.Task[None]:
    """Create the process-local scanner after the checkpointer is ready."""
    return asyncio.create_task(
        chat_recovery_loop(store=store, shared_state=shared_state),
        name="munin-chat-recovery",
    )


def register_chat_routes(
    routes: list[Any],
    *,
    store: Any,
    actor_dependency: Any,
    error_response: Any,
    payload_reader: Any,
    shared_state: Any = None,
) -> None:
    """Wire ``POST /api/chat`` and ``POST /api/chat/{run_id}/guidance``.

    ``asgi.py`` builds the closures for ``actor(...)`` and ``_payload(...)``
    and passes them in so we don't duplicate the auth/CSRF/JSON parsing
    machinery.  ``shared_state`` is the process-wide ``SharedStateStore``
    (created in :mod:`munin.server`); when omitted a per-request instance
    is built lazily inside ``_stream_chat`` to preserve the legacy
    ``app_from_environment`` code path.
    """
    from starlette.routing import Route

    async def chat(request: Request) -> Response:
        try:
            current = await actor_dependency(request, csrf=True)
        except PermissionError as exc:
            return error_response(403, "forbidden", str(exc))
        try:
            data = await payload_reader(request)
        except ValueError as exc:
            return error_response(400, "invalid_body", str(exc))

        conversation_id = str(data.get("conversation_id") or data.get("id") or "").strip()
        content = str(data.get("content") or data.get("message") or "").strip()
        message_id = str(data.get("message_id") or data.get("messageId") or "").strip()
        if not conversation_id:
            return error_response(400, "invalid_body", "conversation_id is required")
        if not content:
            return error_response(400, "invalid_body", "content is required")

        # Fase 3 (autonomous modes): resolve the operation mode + persistent
        # goal from the payload, applying the mode gates (GOAL requires a
        # goal; BEAST requires an explicit scope) before the turn is created.
        from ..core.autonomy.modes import OperationMode, parse_mode_policy  # noqa: PLC0415

        mode_value = data.get("mode")
        if mode_value is not None and mode_value != "":
            try:
                operation_mode = OperationMode(str(mode_value).lower())
            except ValueError:
                return error_response(400, "invalid_body", f"unknown mode {mode_value!r}")
        else:
            operation_mode = OperationMode.STANDARD
        mode_policy = parse_mode_policy(operation_mode)

        goal: dict[str, Any] | None = None
        goal_payload = data.get("goal") if isinstance(data.get("goal"), dict) else None
        if goal_payload:
            goal_id = str(goal_payload.get("id") or "").strip()
            if goal_id:
                existing = None
                try:
                    existing = store.get_goal_for_conversation(conversation_id=conversation_id)
                except Exception:  # noqa: BLE001
                    existing = None
                if not existing or str(existing.get("id")) != goal_id:
                    return error_response(404, "not_found", "goal does not exist in this conversation")
                goal = existing
            else:
                try:
                    goal = store.create_goal(
                        actor_id=current["id"],
                        conversation_id=conversation_id,
                        objective=str(goal_payload.get("objective") or ""),
                        success_criteria=list(goal_payload.get("success_criteria") or []),
                        scope=goal_payload.get("scope") or {},
                        budget=goal_payload.get("budget") or {},
                        deadline_ms=int(goal_payload["deadline_ms"]) if goal_payload.get("deadline_ms") else None,
                        mode=operation_mode.value,
                    )
                except ValueError as exc:
                    return error_response(400, "invalid_body", str(exc))
        elif mode_policy.requires_goal:
            try:
                goal = store.get_goal_for_conversation(conversation_id=conversation_id)
            except Exception:  # noqa: BLE001
                goal = None
            if not goal:
                return error_response(
                    400, "invalid_body", "goal mode requires a persistent goal (payload.goal)"
                )

        if mode_policy.requires_scope:
            scope = (goal or {}).get("scope") or (data.get("scope") if isinstance(data.get("scope"), dict) else None)
            if not scope:
                return error_response(
                    400, "invalid_body", "beast mode requires an explicit scope (payload.scope or goal.scope)"
                )

        idempotency_key = (
            request.headers.get("idempotency-key")
            or message_id
            or f"chat:{secrets.token_urlsafe(16)}"
        )

        # Idempotent-replay reconnect (issue #9 follow-up).  A refresh of an
        # in-flight run resubmits with the same ``idempotency-key``; detect
        # that here — *before* the "another run is active" guard would
        # reject the request with 409 — and open an SSE replay stream
        # instead.  See ``_stream_idempotent_replay`` for the polling
        # semantics.
        prior_run_id = _find_prior_run_id(
            store,
            conversation_id=conversation_id,
            actor_id=current["id"],
            idempotency_key=idempotency_key,
        )
        if prior_run_id:
            replay_stream = _stream_idempotent_replay(
                request,
                store=store,
                actor_id=current["id"],
                run_id=prior_run_id,
            )
            return StreamingResponse(
                replay_stream,
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache, no-transform",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                    "X-Munin-Run-Id": prior_run_id,
                    "X-Munin-Idempotent-Replay": "true",
                },
            )

        # Parity with legacy /turns: refuse a new turn if a run is still active.
        try:
            aggregate = store.get_conversation(
                actor_id=current["id"], conversation_id=conversation_id
            )
            for run in aggregate.get("runs", []):
                if run.get("state") in NON_TERMINAL_RUN_STATES:
                    active_run_id = str(run.get("id") or run.get("run_id") or "")
                    return JSONResponse(
                        {
                            "ok": False,
                            "error": {
                                "code": "run_in_progress",
                                "message": "a run is still active in this conversation — send guidance instead of a new turn",
                            },
                            **({"active_run_id": active_run_id} if active_run_id else {}),
                        },
                        status_code=409,
                    )
        except (PermissionError, KeyError):
            # Fall through — create_turn will fail with the same auth check.
            pass

        try:
            turn = store.create_turn(
                actor_id=current["id"],
                conversation_id=conversation_id,
                content=content,
                idempotency_key=idempotency_key,
                mode=operation_mode.value,
                goal_id=str((goal or {}).get("id") or "") or None,
            )
        except PermissionError as exc:
            return error_response(403, "forbidden", str(exc))
        except ValueError as exc:
            return error_response(
                409 if "idempotency" in str(exc) else 400, "invalid_turn", str(exc)
            )
        except KeyError:
            return error_response(404, "not_found", "conversation not found")

        run_id = turn["run"]["id"]
        assistant_message_id = turn["assistant_message_id"]

        # Idempotent replay: normally we take the ``_find_prior_run_id`` path
        # above and never reach here.  This branch stays as a defensive
        # fallback — ``create_turn`` sees the ``(conv, actor, key)`` tuple
        # slightly differently than our early probe if hot/durable drift
        # (e.g. mid-migration).  Rather than the pre-follow-up JSON
        # response (which the AI SDK client couldn't render), open a
        # replay SSE stream too.
        if turn.get("idempotent_replay"):
            replayed_run_id = str(turn["run"]["id"])
            replay_stream = _stream_idempotent_replay(
                request,
                store=store,
                actor_id=current["id"],
                run_id=replayed_run_id,
            )
            return StreamingResponse(
                replay_stream,
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache, no-transform",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                    "X-Munin-Run-Id": replayed_run_id,
                    "X-Munin-Idempotent-Replay": "true",
                },
            )

        try:
            exec_ctx = store.run_execution_context(run_id=run_id)
        except KeyError:
            return error_response(404, "not_found", "run not found")

        try:
            lease_token, _confirmed_assistant_id = _claim_direct(store, run_id=run_id)
        except RuntimeError as exc:
            return error_response(409, "claim_failed", str(exc))

        prompt = str(exec_ctx.get("message") or content)
        conversation_history = list(exec_ctx.get("history") or [])

        _launch_chat_run(
            store=store,
            shared_state=shared_state,
            actor_info=current,
            run_id=run_id,
            conversation_id=conversation_id,
            prompt=prompt,
            conversation_history=conversation_history,
            assistant_message_id=assistant_message_id,
            lease_token=lease_token,
            mode=operation_mode,
            goal=goal,
        )

        # The request is now a subscriber, not the execution owner. Browser
        # refreshes and transient network loss detach only this replay stream;
        # the supervisor keeps running until it reaches a terminal state.
        stream = _stream_idempotent_replay(
            request,
            store=store,
            actor_id=current["id"],
            run_id=run_id,
        )

        return StreamingResponse(
            stream,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
                "X-Munin-Run-Id": run_id,
            },
        )

    async def chat_guidance(request: Request) -> Response:
        """Enqueue operator guidance mid-run.

        Called by the BFF when the AI SDK client emits a
        ``data-operator-guidance`` UI part. Two side-effects, in order:

        1. ``ProductionStore.enqueue_guidance`` — feeds the middleware queue
           that :class:`OperatorGuidanceMiddleware` drains before the next
           model call, so the hint reaches the ReAct loop as a
           ``HumanMessage`` named ``operator``.
        2. ``ProductionStore.append_reasoning_event(kind="operator_guidance")``
           — writes the durable audit row so the UI can render the hint
           inline and the run detail preserves the sequence.
        """
        try:
            current = await actor_dependency(request, csrf=True)
        except PermissionError as exc:
            return error_response(403, "forbidden", str(exc))
        try:
            data = await payload_reader(request)
        except ValueError as exc:
            return error_response(400, "invalid_body", str(exc))

        run_id = request.path_params["run_id"]
        body = str(data.get("body") or data.get("guidance") or "").strip()
        if not body:
            return error_response(400, "invalid_guidance", "body is required")
        target_agent_id = data.get("target_agent_id")

        try:
            store.get_run_for_actor(actor_id=current["id"], run_id=run_id)
        except PermissionError as exc:
            return error_response(403, "forbidden", str(exc))
        except KeyError:
            return error_response(404, "not_found", "run not found")

        try:
            entry = store.enqueue_guidance(
                run_id=run_id,
                actor_id=current["id"],
                actor_username=current.get("username", current["id"]),
                body=body,
                target_agent_id=str(target_agent_id) if target_agent_id else None,
            )
        except ValueError as exc:
            return error_response(400, "invalid_guidance", str(exc))

        try:
            store.append_reasoning_event(
                run_id=run_id,
                kind="operator_guidance",
                content=body,
                provider="",
                persistence_enabled=True,
                agent_name="operator",
                step=0,
            )
        except Exception:  # noqa: BLE001 - audit best-effort
            log.debug("chat: operator_guidance audit write failed", exc_info=True)

        return JSONResponse({"ok": True, "data": entry}, status_code=201)

    async def resolve_human_request(request: Request) -> Response:
        """Resolve a persisted HITL decision as the authenticated operator.

        This is intentionally a server-authorized resource mutation, not an
        AI-SDK client-side tool approval: the nonce, participant membership,
        expiry and single-use transition are verified by the production store.
        A LangGraph interrupt/resume worker can consume the resulting durable
        decision without trusting any model-supplied boolean.
        """
        try:
            current = await actor_dependency(request, csrf=True)
        except PermissionError as exc:
            return error_response(403, "forbidden", str(exc))
        try:
            data = await payload_reader(request)
        except ValueError as exc:
            return error_response(400, "invalid_body", str(exc))

        choice = str(data.get("choice") or "").strip()
        nonce = str(data.get("nonce") or "").strip()
        if not choice or not nonce:
            return error_response(400, "invalid_human_resolution", "choice and nonce are required")
        request_id = str(request.path_params["request_id"])
        try:
            result = store.resolve_human_decision(
                actor_id=current["id"],
                request_id=request_id,
                choice=choice,
                nonce=nonce,
                guidance=str(data.get("guidance") or data.get("reason") or ""),
            )
        except PermissionError as exc:
            return error_response(403, "forbidden", str(exc))
        except KeyError:
            return error_response(404, "not_found", "human request not found")
        except ValueError as exc:
            return error_response(400, "invalid_human_resolution", str(exc))

        if result.get("state") == "queued":
            # Native Deep Agents HITL resumes its checkpoint with LangGraph
            # ``Command``.  The store's resolved request (nonce, membership,
            # choice) is the authority; the UI never submits a tool result.
            try:
                run = store.get_run(str(result["run_id"]))
                lease_token, assistant_message_id = _claim_direct(
                    store, run_id=str(result["run_id"])
                )
                # Fetch the original prompt + history so the guidance
                # message can remind the model WHAT it was doing before
                # the interrupt. Without this the model has amnesia.
                original_prompt = ""
                resume_history: list = []
                with suppress(Exception):
                    exec_ctx = store.run_execution_context(run_id=str(result["run_id"]))
                    original_prompt = str(exec_ctx.get("message") or "")
                    resume_history = list(exec_ctx.get("history") or [])
                guidance_body = (
                    "Operator approved the pending tool execution. "
                    "Resume the approved action, incorporate its result, "
                    "and continue the workflow toward the original objective."
                )
                if original_prompt:
                    guidance_body = (
                        f"Operator approved the pending tool execution. "
                        f"Your original objective was: \"{original_prompt[:500]}\". "
                        f"Resume the approved action, incorporate its result, "
                        f"and continue the workflow toward that objective. "
                        f"Do NOT ask the operator to repeat the objective — proceed now."
                    )
                # Inject the continuation directive so the model proceeds
                # with the approved tool work instead of hallucinating
                # "standing by" — OperatorGuidanceMiddleware drains this
                # before the first post-resume model call.
                with suppress(Exception):
                    store.enqueue_guidance(
                        run_id=str(result["run_id"]),
                        actor_id=str(current["id"]),
                        actor_username=str(current.get("username") or "operator"),
                        body=guidance_body,
                    )
                _launch_chat_run(
                    store=store,
                    shared_state=shared_state,
                    actor_info=current,
                    run_id=str(result["run_id"]),
                    conversation_id=str(run["conversation_id"]),
                    prompt=original_prompt,
                    conversation_history=resume_history,
                    assistant_message_id=assistant_message_id,
                    lease_token=lease_token,
                    resume_decisions=[{"type": "approve"}]
                    * int(result.get("decision_count") or 1),
                )
            except Exception as exc:  # noqa: BLE001 - do not report success for a stranded approval
                log.exception("chat: HITL resume launch failed request_id=%s", request_id)
                return error_response(500, "resume_failed", str(exc))
        return JSONResponse({"ok": True, "data": result})

    async def chat_resume(request: Request) -> Response:
        """Reconnect AI SDK ``useChat({resume: true})`` to an active run.

        AI SDK uses ``GET /api/chat/{chat_id}/stream``.  Munin's chat id is
        the conversation id, so resolve the authenticated actor's active run
        and replay the canonical persisted event log.  A 204 is the standard
        signal that there is no stream left to resume.
        """
        try:
            current = await actor_dependency(request, csrf=False)
        except PermissionError as exc:
            return error_response(403, "forbidden", str(exc))

        conversation_id = str(request.path_params["conversation_id"])
        try:
            aggregate = store.get_conversation(
                actor_id=current["id"], conversation_id=conversation_id
            )
        except PermissionError as exc:
            return error_response(403, "forbidden", str(exc))
        except KeyError:
            return error_response(404, "not_found", "conversation not found")

        active_runs = [
            run for run in aggregate.get("runs", [])
            if str(run.get("state") or "") in NON_TERMINAL_RUN_STATES
        ]
        if not active_runs:
            return Response(status_code=204)
        run = max(active_runs, key=lambda row: int(row.get("updated_at_ms") or 0))
        run_id = str(run["id"])
        return StreamingResponse(
            _stream_idempotent_replay(
                request, store=store, actor_id=current["id"], run_id=run_id
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
                "X-Munin-Run-Id": run_id,
                "X-Munin-Idempotent-Replay": "true",
            },
        )

    async def conversation_plan(request: Request) -> Response:
        """Hydrate the Goal + durable plan + timers for one conversation.

        The plan panel reads this on connect and polls it while the goal is
        active; the SSE stream carries live ``plan``/``todo``/``hypothesis``/
        ``replan``/``goal`` envelopes for in-run deltas.
        """
        try:
            current = await actor_dependency(request, csrf=False)
        except PermissionError as exc:
            return error_response(403, "forbidden", str(exc))
        conversation_id = str(request.path_params["conversation_id"])
        try:
            store.get_conversation(
                actor_id=current["id"], conversation_id=conversation_id
            )
        except PermissionError as exc:
            return error_response(403, "forbidden", str(exc))
        except KeyError:
            return error_response(404, "not_found", "conversation not found")
        try:
            plan = store.plan_snapshot(conversation_id=conversation_id)
            timers = store.list_timers(conversation_id=conversation_id)
        except Exception:  # noqa: BLE001 - degraded reads must not 500
            log.debug("chat: plan hydration failed", exc_info=True)
            plan = {"goal": None, "items": [], "updated_at_ms": 0}
            timers = []
        return JSONResponse(
            {
                "ok": True,
                "data": {
                    "conversation_id": conversation_id,
                    "goal": plan.get("goal"),
                    "items": plan.get("items") or [],
                    "updated_at_ms": plan.get("updated_at_ms") or 0,
                    "timers": timers,
                },
            }
        )

    async def create_timer_endpoint(request: Request) -> Response:
        """Create a durable server-side timer for a conversation."""
        try:
            current = await actor_dependency(request, csrf=True)
        except PermissionError as exc:
            return error_response(403, "forbidden", str(exc))
        try:
            data = await payload_reader(request)
        except ValueError as exc:
            return error_response(400, "invalid_body", str(exc))
        conversation_id = str(request.path_params["conversation_id"])
        try:
            store.get_conversation(
                actor_id=current["id"], conversation_id=conversation_id
            )
        except PermissionError as exc:
            return error_response(403, "forbidden", str(exc))
        except KeyError:
            return error_response(404, "not_found", "conversation not found")

        kind = str(data.get("kind") or data.get("timer_kind") or "goal_eval").strip()
        cadence_seconds = int(data.get("cadence_seconds") or 0)
        if cadence_seconds < 5:
            return error_response(400, "invalid_timer", "cadence_seconds must be >= 5")
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        try:
            timer = store.create_timer(
                conversation_id=conversation_id,
                actor_id=current["id"],
                kind=kind,
                due_at_ms=0,
                cadence_ms=cadence_seconds * 1000,
                payload=payload,
                goal_id=str(data.get("goal_id") or "") or None,
            )
        except ValueError as exc:
            return error_response(400, "invalid_timer", str(exc))
        return JSONResponse({"ok": True, "data": timer}, status_code=201)

    async def pause_timer_endpoint(request: Request) -> Response:
        try:
            current = await actor_dependency(request, csrf=True)
        except PermissionError as exc:
            return error_response(403, "forbidden", str(exc))
        timer_id = str(request.path_params["timer_id"])
        try:
            timer = store.pause_timer(actor_id=current["id"], timer_id=timer_id)
        except KeyError:
            return error_response(404, "not_found", "timer not found")
        return JSONResponse({"ok": True, "data": timer})

    async def cancel_timer_endpoint(request: Request) -> Response:
        try:
            current = await actor_dependency(request, csrf=True)
        except PermissionError as exc:
            return error_response(403, "forbidden", str(exc))
        timer_id = str(request.path_params["timer_id"])
        try:
            timer = store.cancel_timer(actor_id=current["id"], timer_id=timer_id)
        except KeyError:
            return error_response(404, "not_found", "timer not found")
        return JSONResponse({"ok": True, "data": timer})

    async def update_goal_endpoint(request: Request) -> Response:
        """Operator-owned goal mutations (state, criteria, scope, budget)."""
        try:
            current = await actor_dependency(request, csrf=True)
        except PermissionError as exc:
            return error_response(403, "forbidden", str(exc))
        try:
            data = await payload_reader(request)
        except ValueError as exc:
            return error_response(400, "invalid_body", str(exc))
        goal_id = str(request.path_params["goal_id"])
        fields: dict[str, Any] = {}
        for key in ("state", "objective", "deadline_ms", "last_tick_ms"):
            if key in data:
                fields[key] = data[key]
        for key in ("success_criteria", "scope", "budget"):
            if key in data and isinstance(data[key], (list, dict)):
                fields[key] = data[key]
        if "state" in fields and str(fields["state"]) not in {"pending", "active", "completed", "failed", "paused"}:
            return error_response(400, "invalid_goal", f"invalid goal state {fields['state']!r}")
        try:
            goal = store.update_goal(actor_id=current["id"], goal_id=goal_id, **fields)
        except KeyError:
            return error_response(404, "not_found", "goal not found")
        except ValueError as exc:
            return error_response(400, "invalid_goal", str(exc))
        return JSONResponse({"ok": True, "data": goal})

    routes.append(Route("/api/chat", chat, methods=["POST"]))
    routes.append(
        Route("/api/chat/{conversation_id}/stream", chat_resume, methods=["GET"])
    )
    routes.append(
        Route("/api/chat/{run_id}/guidance", chat_guidance, methods=["POST"])
    )
    routes.append(
        Route("/api/chat/{conversation_id}/plan", conversation_plan, methods=["GET"])
    )
    routes.append(
        Route("/api/chat/{conversation_id}/timers", create_timer_endpoint, methods=["POST"])
    )
    routes.append(
        Route(
            "/api/chat/{conversation_id}/timers/{timer_id}/pause",
            pause_timer_endpoint,
            methods=["POST"],
        )
    )
    routes.append(
        Route(
            "/api/chat/{conversation_id}/timers/{timer_id}/cancel",
            cancel_timer_endpoint,
            methods=["POST"],
        )
    )
    routes.append(
        Route("/api/goals/{goal_id}", update_goal_endpoint, methods=["PATCH"])
    )
    routes.append(
        Route(
            "/api/human-requests/{request_id}/resolve",
            resolve_human_request,
            methods=["POST"],
        )
    )
