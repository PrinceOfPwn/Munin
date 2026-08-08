# tags: [runs, runtime, core, cancel-fence, api, asgi, run.cancelling, cancel-endpoint, request_cancel_fence, FenceProbe, PR-6B, PR-6C, run-detail-readmodel, run-artifacts]
"""Durable run-level API surface (cancel, retry, lifecycle probes, read-model).

PR-2A of Munin Issue #32 introduces a dedicated ``POST /api/chat/{run_id}/cancel``
endpoint.  The handler lives here so :mod:`munin.production.chat` can keep
focusing on the SSE / supervisor bridge, and so the cancel contract (auth,
fence marker, atomic HITL rejection, idempotent terminal state) is testable
in isolation.

The endpoint never performs the terminal ``run.cancelled`` transition itself:
it only records the ``cancel_requested_at_ms`` fence marker (via
:meth:`ProductionStore.request_cancel_fence`) and rejects pending HITL rows.
The detached supervisor observes the fence between steps and performs the
terminal transition (see :func:`observe_cancel_fence` + the executor loop in
:mod:`munin.production.chat`).

PR-6 (Issue #32) adds the read-only run surface alongside the mutating
cancel endpoint:

* ``GET /api/runs/{run_id}/artifacts`` — rich artifact metadata for one run.
* ``GET /api/runs/{run_id}/detail`` — deterministic composite read-model
  (ten fixed keys; pure SQL, no provider calls, byte-identical on replay).
"""
from __future__ import annotations

import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .store import FINAL_RUN_STATES, MuninStore, ProductionStore

log = logging.getLogger("munin.production.runs")


def observe_cancel_fence(store: Any, *, run_id: str) -> bool:
    """Return True when the durable fence marker says the run is cancelling.

    A cheap, read-only probe the executor (in :mod:`munin.production.chat`)
    calls between supervisor steps.  ``cancel_requested_at_ms`` is set by
    :meth:`ProductionStore.request_cancel_fence` and only cleared by a
    terminal transition, so a True result means "stop after the current
    step and finalise as cancelled".  Never raises — a transient store
    failure must not crash the supervisor loop.
    """
    getter = getattr(store, "get_run", None)
    if not callable(getter):
        return False
    try:
        run = getter(run_id)
    except KeyError:
        return False
    except Exception:  # noqa: BLE001 - never let a probe crash the loop
        log.debug("cancel fence probe failed run_id=%s", run_id, exc_info=True)
        return False
    if not isinstance(run, dict):
        return False
    marker = run.get("cancel_requested_at_ms")
    if marker is None:
        return False
    state = str(run.get("state") or "")
    # A terminal run that completed/failed before the fence was observed has
    # its marker cleared by :meth:`complete_run`; if it lingered (legacy
    # ``request_run_cancellation`` path), only treat a non-terminal state as
    # actively cancelling.
    return state not in FINAL_RUN_STATES


def register_run_routes(
    routes: list[Any],
    *,
    store: Any,
    actor_dependency: Any,
    error_response: Any,
) -> None:
    """Wire run-level mutating endpoints (cancel, in PR-2A) onto ``routes``.

    Mirrors :func:`munin.production.chat.register_chat_routes`: the closures
    ``actor(...)`` and ``_error(...)`` are owned by :mod:`munin.production.asgi`
    and passed in so we share one auth/CSRF/JSON machinery.
    """
    from starlette.routing import Route

    async def cancel_run(request: Request) -> Response:
        """POST /api/chat/{run_id}/cancel — durable run cancellation.

        Contract (PR-2A):

        * 202 ``{status:"cancelling", run_id, requested_at_ms}`` — the run
          was queued, running, or ``waiting_for_human``; the fence marker is
          set and pending HITL requests are atomically rejected.
        * 200 ``{status:<terminal>}`` — the run was already in a terminal
          state; the fence marker is NOT touched.
        * 404 — ``run_id`` does not exist.
        * 403 — the actor is not a participant (or auth/CSRF failed).
        """
        try:
            current = await actor_dependency(request, csrf=True)
        except PermissionError as exc:
            return error_response(403, "forbidden", str(exc))

        run_id = str(request.path_params["run_id"])

        try:
            result = store.request_cancel_fence(actor_id=current["id"], run_id=run_id)
        except KeyError:
            return error_response(404, "not_found", "run not found")
        except PermissionError as exc:
            return error_response(403, "forbidden", str(exc))

        requested_at_ms = result.get("cancel_requested_at_ms")
        state = str(result.get("state") or "")

        if state in FINAL_RUN_STATES:
            # Already terminal — no fence marker was written, no SSE event.
            return JSONResponse(
                {"ok": True, "status": state, "run_id": run_id},
                status_code=200,
            )

        # Non-terminal → 202 cancelling.  The durable ``run.cancelling`` event
        # has already been appended to ``run_events`` by the store; the SSE
        # replay stream (or a poller) surfaces it to connected clients.
        return JSONResponse(
            {
                "ok": True,
                "status": "cancelling",
                "run_id": run_id,
                "requested_at_ms": int(requested_at_ms or 0),
            },
            status_code=202,
        )

    routes.append(
        Route("/api/chat/{run_id}/cancel", cancel_run, methods=["POST"])
    )

    async def run_artifacts(request: Request) -> Response:
        """PR-6B — ``GET /api/runs/{run_id}/artifacts``.

        Rich artifact metadata for one run (oldest first); content bodies are
        served only by ``GET /api/artifacts/{artifact_id}``.
        """
        try:
            current = await actor_dependency(request)
        except PermissionError as exc:
            return error_response(403, "forbidden", str(exc))
        run_id = str(request.path_params["run_id"])
        try:
            result = store.list_artifacts_for_run(actor_id=current["id"], run_id=run_id)
        except KeyError:
            return error_response(404, "not_found", "run not found")
        except PermissionError as exc:
            return error_response(403, "forbidden", str(exc))
        return JSONResponse({"ok": True, "data": result})

    async def run_detail(request: Request) -> Response:
        """PR-6C — ``GET /api/runs/{run_id}/detail``.

        Deterministic composite read-model with EXACTLY the ten contract keys
        (run_id, state, aggregated_tools, activities, commands, agents,
        approvals, guidance, artifacts, summaries).  Pure store reads — never
        invokes the AI provider and never regenerates history, so repeated
        GETs are byte-identical.
        """
        try:
            current = await actor_dependency(request)
        except PermissionError as exc:
            return error_response(403, "forbidden", str(exc))
        run_id = str(request.path_params["run_id"])
        try:
            result = store.get_run_detail_readmodel(actor_id=current["id"], run_id=run_id)
        except KeyError:
            return error_response(404, "not_found", "run not found")
        except PermissionError as exc:
            return error_response(403, "forbidden", str(exc))
        return JSONResponse({"ok": True, "data": result})

    routes.append(
        Route("/api/runs/{run_id}/artifacts", run_artifacts, methods=["GET"])
    )
    routes.append(
        Route("/api/runs/{run_id}/detail", run_detail, methods=["GET"])
    )


__all__ = ["observe_cancel_fence", "register_run_routes", "FINAL_RUN_STATES", "MuninStore", "ProductionStore"]
