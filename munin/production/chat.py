"""Deep Agents supervisor → SSE bridge (Fase 1a of issue #9 migration).

This module owns ``POST /api/chat`` and ``POST /api/chat/{run_id}/guidance``.
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

Envelopes are emitted by ``ProgressEmitMiddleware`` (tool lifecycle) and
``runtime_adapter.translate_event`` (reasoning + run_state), so this handler
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
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

log = logging.getLogger("munin.production.chat")

TERMINAL_STATES = {"completed", "failed", "cancelled", "interrupted"}
NON_TERMINAL_RUN_STATES = {"queued", "running", "waiting_for_human"}
CHAT_LEASE_SECONDS = int(os.environ.get("MUNIN_CHAT_LEASE_SECONDS", str(4 * 3600)))
# How often the idempotent-replay SSE stream re-checks the durable/hot store
# for new ``run_events`` and the current ``agent_runs.state``.  0.3s is
# imperceptible in the UI of a multi-minute run; we intentionally avoid an
# in-process pub/sub bus so replay survives across worker processes (see
# :func:`_stream_idempotent_replay`).
CHAT_REPLAY_POLL_SECONDS = float(os.environ.get("MUNIN_CHAT_REPLAY_POLL_SECONDS", "0.3"))


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
        if kind == "reasoning":
            text = str(envelope.get("text") or "")
            if text:
                assistant_buffer.append(text)
                _update_placeholder(
                    store,
                    assistant_message_id=assistant_message_id,
                    content="".join(assistant_buffer),
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
        detail = tools_by_eid.get(eid) or {}
        tool_call_id = payload.get("tool_call_id") if isinstance(payload, dict) else None
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
        # Live-stream reasoning is captured into the assistant placeholder
        # by ``_persist_envelope`` — not written to ``reasoning_events`` —
        # so most ``reasoning.<kind>`` rows we see here are audit-only
        # (``operator_guidance`` and forge summaries).  Only surface a
        # reasoning envelope when we actually have persisted content.
        detail = reasoning_by_eid.get(eid) or {}
        text = str(detail.get("content") or "")
        if not text:
            return None
        return {"kind": "reasoning", "run_id": run_id, "text": text}

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


def _current_placeholder_text(
    store: Any, *, actor_id: str, conversation_id: str, assistant_message_id: str
) -> str:
    """Snapshot the assistant placeholder body via ``get_conversation``.

    Used only at connect time so the replay client sees the reasoning text
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
    initial_state = current_state if current_state in TERMINAL_STATES else "running"

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
            {"kind": "reasoning", "run_id": run_id, "text": placeholder},
            sequence=sequence,
        )

    cursor = 0
    terminal_observed = current_state in TERMINAL_STATES

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
            reasoning_by_eid = {
                str(row.get("event_id")): row for row in (detail.get("reasoning") or [])
            }
            for event in events:
                cursor = max(cursor, int(event.get("sequence") or 0))
                envelope = _envelope_from_event(
                    event,
                    run_id=run_id,
                    tools_by_eid=tools_by_eid,
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
            if str(latest.get("state") or "") in TERMINAL_STATES:
                # The corresponding ``run.<state>`` event should show up on
                # the next tick; loop once more so we surface it verbatim.
                terminal_observed = True
        except KeyError:
            terminal_observed = True

        if terminal_observed:
            continue  # let the loop pick up the terminal event on next SELECT

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
    if final_content:
        sequence += 1
        yield _sse_frame(
            {
                "kind": "reasoning",
                "run_id": run_id,
                "text": final_content,
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
) -> AsyncIterator[bytes]:
    from ..core.llm_client import LLMClient
    from ..core.runtime_adapter import supervisor_runner
    from ..mcp.config import get_settings
    from ..mcp.shared_state import SharedStateStore

    settings = get_settings()

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

    try:
        async for envelope in supervisor_runner(
            prompt,
            run_id=run_id,
            conversation_id=conversation_id,
            store=shared_state,
            model=model,
            conversation_history=conversation_history,
            thread_id=conversation_id or run_id,
        ):
            _persist_envelope(
                store,
                envelope,
                run_id=run_id,
                assistant_message_id=assistant_message_id,
                assistant_buffer=assistant_buffer,
            )

            sequence += 1
            yield _sse_frame(envelope, sequence=sequence)

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

        if not finalized:
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

    yield b"event: close\ndata: {}\n\n"
    log.info(
        "chat: run_id=%s final_state=%s actor=%s error=%s",
        run_id,
        final_state,
        actor_info.get("username") or actor_info.get("id"),
        final_error,
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
                    return error_response(
                        409,
                        "run_in_progress",
                        "a run is still active in this conversation — send guidance instead of a new turn",
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

        stream = _stream_chat(
            request,
            store=store,
            shared_state=shared_state,
            actor_info=current,
            run_id=run_id,
            conversation_id=conversation_id,
            prompt=prompt,
            conversation_history=conversation_history,
            assistant_message_id=assistant_message_id,
            lease_token=lease_token,
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

    routes.append(Route("/api/chat", chat, methods=["POST"]))
    routes.append(
        Route("/api/chat/{run_id}/guidance", chat_guidance, methods=["POST"])
    )
