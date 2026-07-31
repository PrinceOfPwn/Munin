"""Authenticated ASGI boundary for the Munin Production Suite.

The MCP server remains available for editor/tool integrations.  Operator-facing
web traffic uses this service so browser sessions are opaque cookies rather than
shared MCP credentials stored in JavaScript.  MCP and Discord adapters call the
same :class:`ProductionStore` repository with a service actor.

v3 additions (long-running operator UX):
  * ``run_events`` — the SSE stream is now capped by ``MUNIN_SSE_MAX_SECONDS``
    (default 4h) instead of a hard-coded 120-iteration loop, and emits
    ``heartbeat`` events every ``MUNIN_SSE_HEARTBEAT_SECONDS`` (default 20s)
    so idle-connection proxies (cloudflared, ngrok, corporate LBs) don't
    reap the pipe.  The stream terminates cleanly with ``event: close`` when
    the run reaches a terminal state, exposes the current lease/worker
    liveness in every heartbeat, and honours ``last-event-id`` on reconnect.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from collections.abc import AsyncIterator
from typing import Any

log = logging.getLogger("munin.production.asgi")

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from .agents import profile_catalog
from .page_agent import validate_page_action
from .store import ProductionStore
from .store_v3_1 import install_v3_1_extensions


TERMINAL_STATES = {"completed", "failed", "cancelled", "interrupted"}
NON_TERMINAL_RUN_STATES = {"queued", "running", "waiting_for_human"}


def _allowed_origins() -> set[str]:
    """Re-read MUNIN_ALLOWED_ORIGINS on every call so runtime env updates
    (e.g. a ngrok URL discovered after the server started) are honoured without
    a restart.  The env var is a comma-separated list of origins.
    """
    return {origin.rstrip("/") for origin in os.environ.get("MUNIN_ALLOWED_ORIGINS", "").split(",") if origin.strip()}


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": {"code": code, "message": message}}, status_code=status)


async def _payload(request: Request) -> dict[str, Any]:
    try:
        value = await request.json()
    except Exception as exc:
        raise ValueError("invalid JSON body") from exc
    if not isinstance(value, dict):
        raise ValueError("JSON body must be an object")
    return value


def _cookie_secure() -> bool:
    return os.environ.get("MUNIN_COOKIE_SECURE", "1") != "0"


def _worker_alive(snapshot: dict[str, Any]) -> bool:
    """Best-effort lease liveness check without adding a new store method.

    ``get_run`` and ``get_run_for_actor`` both expose ``lease_expires_at_ms``
    when a worker owns the lease.  The dispatcher heartbeats every 15s and a
    lease is 60s wide, so anything within 90s of "now" is a live worker.
    """
    lease_expires = snapshot.get("lease_expires_at_ms") or 0
    if not lease_expires:
        return False
    return int(lease_expires) > int(time.time() * 1000) - 90_000


def _phase_from_events(reasoning_events: list[dict[str, Any]] | None, tool_events: list[dict[str, Any]] | None) -> str:
    """Human-readable phase, inferred from the latest reasoning/tool signal."""
    if tool_events:
        pending = [t for t in tool_events if t.get("state") in {"pending", "running"}]
        if pending:
            latest = pending[-1]
            return f"tool:{latest.get('tool_name', 'unknown')}"
    if reasoning_events:
        latest_r = reasoning_events[-1]
        return f"reasoning:{latest_r.get('kind', 'unknown')}"
    return "waiting"


class SecurityHeaders:
    """Small ASGI middleware compatible with the installed Starlette contract.

    ``MUNIN_ALLOWED_ORIGINS`` is re-read on every request via ``_allowed_origins()``
    so that the set can be updated at runtime (e.g. by the CI workflow that
    discovers the ngrok URL after the server has already started).
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = {bytes(key).lower(): bytes(value) for key, value in scope.get("headers", [])}
        origin = headers.get(b"origin", b"").decode("latin-1").rstrip("/")
        allowed = _allowed_origins()  # re-read env on every request
        if scope.get("method") == "OPTIONS":
            if origin not in allowed:
                await _error(403, "origin_denied", "origin is not allowed")(scope, receive, send)
                return
            response = Response(status_code=204)
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Headers"] = "content-type, x-csrf-token, idempotency-key, last-event-id"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
            await response(scope, receive, send)
            return

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                response_headers = list(message.get("headers", []))
                response_headers.extend(
                    [
                        (b"content-security-policy", b"default-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"),
                        (b"x-content-type-options", b"nosniff"),
                        (b"referrer-policy", b"no-referrer"),
                        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                        (b"cross-origin-opener-policy", b"same-origin"),
                    ]
                )
                if origin in allowed:
                    response_headers.extend(
                        [
                            (b"access-control-allow-origin", origin.encode()),
                            (b"access-control-allow-credentials", b"true"),
                            (b"access-control-allow-headers", b"content-type, x-csrf-token, idempotency-key, last-event-id"),
                            (b"access-control-allow-methods", b"GET, POST, PATCH, DELETE, OPTIONS"),
                            (b"vary", b"Origin"),
                        ]
                    )
                message = {**message, "headers": response_headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


def create_app(store: ProductionStore) -> Starlette:
    """Create the session-authenticated production API over a shared store."""

    # v3.1: install the collab/notes/presence/guidance schema + method
    # extensions.  Idempotent — safe to call every boot.
    store = install_v3_1_extensions(store)

    # allowed_origins is intentionally NOT captured at startup — see _allowed_origins().

    async def actor(request: Request, *, csrf: bool = False) -> dict[str, Any]:
        token = request.cookies.get("munin_session", "")
        result = store.authenticate(token)
        if not result:
            raise PermissionError("authentication required")
        if csrf:
            origin = request.headers.get("origin", "").rstrip("/")
            fetch_site = request.headers.get("sec-fetch-site", "")
            provided = request.headers.get("x-csrf-token", "")
            # Re-read allowed origins on every CSRF check so ngrok / tunnel
            # URLs added after startup are accepted without a server restart.
            if not origin or origin not in _allowed_origins() or fetch_site not in {"same-origin", "same-site"}:
                raise PermissionError("cross-site request rejected")
            if not store.validate_csrf(session_id=result["session_id"], csrf_token=provided):
                raise PermissionError("CSRF validation failed")
        return result

    async def health(_: Request) -> Response:
        return JSONResponse({"ok": True, "service": "munin-production-api"})

    async def simulate_forge(request: Request) -> Response:
        """Development-only smoke path for the forge floating window.

        Emits the full canonical ``forge_*`` reasoning-event sequence against
        the given run so an operator can verify the UI wiring end-to-end
        without needing a live LLM or a real forge subagent invocation.

        Guarded by ``MUNIN_DEV_ENDPOINTS_ENABLED=1`` and by session auth so
        it can't be reached on production builds.  Not part of the public
        API surface — see VERIFY.md for the intended smoke-test flow.
        """
        if os.environ.get("MUNIN_DEV_ENDPOINTS_ENABLED", "").lower() not in {"1", "true", "yes"}:
            # 404 not 403 to avoid leaking the route's existence on prod builds.
            return _error(404, "not_found", "dev endpoint not enabled")
        try:
            current = await actor(request, csrf=True)
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))
        run_id = request.path_params["run_id"]
        log.warning(
            "dev endpoint /api/dev/simulate-forge invoked by actor=%s run_id=%s agent=%s",
            current.get("username") or current.get("id"),
            run_id,
            request.query_params.get("agent", "tool-forge"),
        )
        # Local import — forge_progress lives in the same package but pulling
        # it in at module load would create a cycle with parallel/dispatcher.
        from .forge_progress import emit_forge_stage

        agent_name = str(request.query_params.get("agent", "tool-forge"))
        stages: list[tuple[str, str, dict[str, Any]]] = [
            ("forge_propose", "Requesting implementation draft (1/3)", {"forge_iteration": 1}),
            ("forge_diff_ready", "Draft ready: hello_world_tool (256 bytes)", {"function_name": "hello_world_tool", "script_bytes": 256}),
            ("forge_typecheck_start", "Validating AST + import guard", {"function_name": "hello_world_tool"}),
            ("forge_typecheck_output", "Parsed 1 function, 0 unsafe imports", {"stream": "stdout"}),
            ("forge_typecheck_done", "Typecheck exit: ok=True", {"ok": True}),
            ("forge_sandbox_start", "Running draft in restricted-exec sandbox", {"function_name": "hello_world_tool"}),
            ("forge_sandbox_output", "sandbox: defined=True", {"stream": "stdout"}),
            ("forge_sandbox_done", "Sandbox exit: ok=True", {"ok": True}),
            ("forge_awaiting_approval", "Forged hello_world_tool — awaiting operator approval", {"tool_slug": "hello_world_tool"}),
            ("forge_completed", "Forged hello_world_tool in 1 iteration", {"tool_slug": "hello_world_tool", "iterations": 1}),
        ]
        emitted: list[str] = []
        for step, (stage, message, extra) in enumerate(stages, start=1):
            try:
                emit_forge_stage(
                    store,
                    run_id=run_id,
                    agent_name=agent_name,
                    stage=stage,
                    message=message,
                    step=step,
                    **extra,
                )
                emitted.append(stage)
            except Exception as exc:  # noqa: BLE001
                return _error(500, "emit_failed", f"stage {stage} failed: {exc}")
        return JSONResponse({"ok": True, "data": {"run_id": run_id, "agent_name": agent_name, "emitted": emitted}})

    async def agents(request: Request) -> Response:
        try:
            await actor(request)
            return JSONResponse({"ok": True, "data": profile_catalog()})
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))

    async def page_agent_action(request: Request) -> Response:
        try:
            current = await actor(request, csrf=True)
            data = await _payload(request)
            plan = validate_page_action(
                role=current["role"],
                feature_enabled=os.environ.get("MUNIN_PAGE_AGENT_ENABLED", "0") == "1",
                action=str(data.get("action", "")),
                target=str(data.get("target", "")),
                parameters=dict(data.get("parameters") or {}),
            )
            store.record_audit(actor_id=current["id"], action="page_agent.plan", resource_type="page_action", resource_id=plan.target, outcome="confirmation_required" if plan.requires_confirmation else "success", metadata={"action": plan.action})
            return JSONResponse({"ok": True, "data": {"action": plan.action, "target": plan.target, "parameters": plan.parameters, "requires_confirmation": plan.requires_confirmation}})
        except PermissionError as exc:
            return _error(403, "page_agent_denied", str(exc))
        except ValueError as exc:
            return _error(400, "invalid_page_agent_action", str(exc))

    async def provider_profiles(request: Request) -> Response:
        try:
            current = await actor(request, csrf=request.method == "POST")
            if request.method == "GET":
                return JSONResponse({"ok": True, "data": store.list_provider_profiles(actor_id=current["id"])})
            data = await _payload(request)
            profile = store.save_provider_profile(actor_id=current["id"], label=str(data.get("label", "")), provider=str(data.get("provider", "")), base_url=str(data.get("base_url", "")), model=str(data.get("model", "")), uses=list(data.get("uses") or []), plaintext_key=str(data.get("key", "")))
            return JSONResponse({"ok": True, "data": profile}, status_code=201)
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))
        except ValueError as exc:
            return _error(400, "invalid_provider_profile", str(exc))

    async def provider_profile_action(request: Request) -> Response:
        try:
            current = await actor(request, csrf=True)
            profile_id = request.path_params["profile_id"]
            action = request.path_params["action"]
            data = await _payload(request)
            if action == "activate":
                result: Any = store.set_active_provider_profile(actor_id=current["id"], profile_id=profile_id)
            elif action == "revoke":
                result = {"revoked": store.revoke_provider_profile(actor_id=current["id"], profile_id=profile_id)}
            elif action == "rotate":
                result = store.rotate_provider_profile(actor_id=current["id"], profile_id=profile_id, plaintext_key=str(data.get("key", "")))
            else:
                return _error(404, "not_found", "unknown provider action")
            return JSONResponse({"ok": True, "data": result})
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))
        except ValueError as exc:
            return _error(400, "invalid_provider_action", str(exc))

    async def bootstrap(request: Request) -> Response:
        data = await _payload(request)
        user = store.bootstrap_admin(username=str(data.get("username", "")), password=str(data.get("password", "")))
        if user is None:
            return _error(409, "bootstrap_consumed", "an administrator already exists")
        return JSONResponse({"ok": True, "user": user}, status_code=201)

    async def login(request: Request) -> Response:
        data = await _payload(request)
        try:
            session = store.login(
                username=str(data.get("username", "")),
                password=str(data.get("password", "")),
                ip_address=request.client.host if request.client else "",
                user_agent=request.headers.get("user-agent", ""),
            )
        except PermissionError as exc:
            return _error(401, "invalid_credentials", str(exc))
        response = JSONResponse({"ok": True, "csrf_token": session["csrf_token"], "expires_at_ms": session["absolute_expires_at_ms"]})
        response.set_cookie("munin_session", session["token"], httponly=True, secure=_cookie_secure(), samesite="strict", path="/")
        return response

    async def request_password_recovery(request: Request) -> Response:
        data = await _payload(request)
        store.issue_password_recovery(username=str(data.get("username", "")))
        return JSONResponse({"ok": True, "message": "If the account exists, recovery delivery has been requested."}, status_code=202)

    async def consume_password_recovery(request: Request) -> Response:
        data = await _payload(request)
        try:
            consumed = store.consume_password_recovery(token=str(data.get("token", "")), new_password=str(data.get("password", "")))
        except ValueError as exc:
            return _error(400, "invalid_password", str(exc))
        return JSONResponse({"ok": True, "consumed": consumed})

    async def session(request: Request) -> Response:
        try:
            current = await actor(request)
        except PermissionError:
            return _error(401, "unauthenticated", "login required")
        try:
            csrf_token = store.refresh_csrf(current["session_id"])
        except PermissionError:
            return _error(401, "unauthenticated", "login required")
        return JSONResponse({"ok": True, "actor": {key: current[key] for key in ("id", "username", "role")}, "csrf_token": csrf_token})

    async def logout(request: Request) -> Response:
        try:
            current = await actor(request, csrf=True)
        except PermissionError as exc:
            return _error(403, "csrf_or_auth", str(exc))
        store.revoke_session(current["session_id"], actor_id=current["id"])
        response = JSONResponse({"ok": True})
        response.delete_cookie("munin_session", path="/")
        return response

    async def conversations(request: Request) -> Response:
        try:
            current = await actor(request, csrf=request.method == "POST")
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))
        if request.method == "GET":
            data = await run_in_threadpool(
                store.list_conversations,
                actor_id=current["id"],
                query=request.query_params.get("q", ""),
                status=request.query_params.get("status", ""),
                include_archived=request.query_params.get("archived") == "true",
                limit=int(request.query_params.get("limit", "50")),
                cursor_ms=int(request.query_params["cursor"]) if request.query_params.get("cursor") else None,
            )
            return JSONResponse(
                {
                    "ok": True,
                    "data": data,
                }
            )
        data = await _payload(request)
        result = store.create_conversation(owner_id=current["id"], title=str(data.get("title", "New conversation")), tags=list(data.get("tags") or []), scope=dict(data.get("scope") or {}))
        return JSONResponse({"ok": True, "data": result}, status_code=201)

    async def conversation_detail(request: Request) -> Response:
        try:
            current = await actor(request, csrf=request.method in {"PATCH", "DELETE"})
            conversation_id = request.path_params["conversation_id"]
            if request.method == "GET":
                data = await run_in_threadpool(
                    store.get_conversation, actor_id=current["id"], conversation_id=conversation_id
                )
                return JSONResponse({"ok": True, "data": data})
            if request.method == "DELETE":
                data = await _payload(request)
                store.soft_delete_conversation(actor_id=current["id"], conversation_id=conversation_id, expected_version=int(data["version"]))
                return JSONResponse({"ok": True})
            data = await _payload(request)
            if "title" in data:
                result = store.rename_conversation(actor_id=current["id"], conversation_id=conversation_id, title=str(data["title"]), expected_version=int(data["version"]))
            else:
                result = store.set_conversation_archive(actor_id=current["id"], conversation_id=conversation_id, archived=bool(data.get("archived")), expected_version=int(data["version"]))
            return JSONResponse({"ok": True, "data": result})
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))
        except KeyError:
            return _error(404, "not_found", "conversation not found")
        except RuntimeError as exc:
            return _error(409, "version_conflict", str(exc))

    async def conversation_export(request: Request) -> Response:
        try:
            current = await actor(request)
            return JSONResponse({"ok": True, "data": store.export_conversation(actor_id=current["id"], conversation_id=request.path_params["conversation_id"])})
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))
        except KeyError:
            return _error(404, "not_found", "conversation not found")

    async def artifact(request: Request) -> Response:
        try:
            current = await actor(request)
            result = await run_in_threadpool(
                store.get_artifact, actor_id=current["id"], artifact_id=request.path_params["artifact_id"]
            )
            if request.query_params.get("download") == "true":
                return Response(result["content"], media_type=result["media_type"], headers={"Content-Disposition": f"attachment; filename={result['filename']}"})
            return JSONResponse({"ok": True, "data": result})
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))
        except KeyError:
            return _error(404, "not_found", "artifact not found")

    async def turn(request: Request) -> Response:
        try:
            current = await actor(request, csrf=True)
            data = await _payload(request)
            key = request.headers.get("idempotency-key", "")
            conversation_id = request.path_params["conversation_id"]
            # v3.1 multi-operator: reject a fresh turn if any run in this
            # conversation is still non-terminal.  The composer's Turn mode
            # must be disabled by the client, but we defend on the server so
            # a stale tab cannot interrupt an active run.
            try:
                aggregate = await run_in_threadpool(
                    store.get_conversation, actor_id=current["id"], conversation_id=conversation_id
                )
                for run in aggregate.get("runs", []):
                    if run.get("state") in NON_TERMINAL_RUN_STATES:
                        return _error(
                            409,
                            "run_in_progress",
                            "a run is still active in this conversation — send guidance instead of a new turn",
                        )
            except (PermissionError, KeyError):
                # Fall through to create_turn which does its own auth.
                pass
            result = store.create_turn(actor_id=current["id"], conversation_id=conversation_id, content=str(data.get("content", "")), idempotency_key=key)
            if not result["idempotent_replay"] and os.environ.get("MUNIN_PRODUCTION_AUTO_DISPATCH", "1") == "1":
                from ..mcp.config import get_settings
                from .dispatcher import ProductionDispatcher

                dispatcher = ProductionDispatcher(store, get_settings(), worker_id=f"api-{os.getpid()}")
                threading.Thread(target=dispatcher.run_once, name=f"munin-dispatch-{result['run']['id']}", daemon=True).start()
            return JSONResponse({"ok": True, "data": result}, status_code=200 if result["idempotent_replay"] else 201)
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))
        except ValueError as exc:
            return _error(409 if "idempotency" in str(exc) else 400, "invalid_turn", str(exc))

    async def run(request: Request) -> Response:
        try:
            current = await actor(request)
            result = await run_in_threadpool(
                store.get_run_for_actor, actor_id=current["id"], run_id=request.path_params["run_id"]
            )
            return JSONResponse({"ok": True, "data": result})
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))
        except KeyError:
            return _error(404, "not_found", "run not found")

    async def run_detail(request: Request) -> Response:
        try:
            current = await actor(request)
            data = await run_in_threadpool(
                store.get_run_detail_for_actor, actor_id=current["id"], run_id=request.path_params["run_id"]
            )
            return JSONResponse({"ok": True, "data": data})
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))
        except KeyError:
            return _error(404, "not_found", "run not found")

    async def run_cancel(request: Request) -> Response:
        try:
            current = await actor(request, csrf=True)
            return JSONResponse({"ok": True, "data": store.request_run_cancellation(actor_id=current["id"], run_id=request.path_params["run_id"])})
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))
        except KeyError:
            return _error(404, "not_found", "run not found")

    async def run_retry(request: Request) -> Response:
        try:
            current = await actor(request, csrf=True)
            result = store.retry_run(actor_id=current["id"], run_id=request.path_params["run_id"])
            return JSONResponse({"ok": True, "data": result}, status_code=201)
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))
        except KeyError:
            return _error(404, "not_found", "run not found")
        except ValueError as exc:
            return _error(409, "retry_not_available", str(exc))

    async def list_run_guidance(request: Request) -> Response:
        try:
            current = await actor(request)
            run_id = request.path_params["run_id"]
            store.get_run_for_actor(actor_id=current["id"], run_id=run_id)
            return JSONResponse({"ok": True, "data": store.list_run_guidance(run_id=run_id)})
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))
        except KeyError:
            return _error(404, "not_found", "run not found")

    async def run_guidance(request: Request) -> Response:
        try:
            current = await actor(request, csrf=True)
            data = await _payload(request)
            run_id = request.path_params["run_id"]
            # Accept either legacy `guidance` or v3.1 `body`.  v3.1 adds
            # `target_agent_id` so forge-window composers can address a
            # specific subagent, and `budget_extension_seconds` for the
            # elastic-budget affordance.
            body = str(data.get("body") or data.get("guidance") or "")
            target_agent_id = data.get("target_agent_id")
            budget_extension = int(data.get("budget_extension_seconds") or 0)
            # Preserve legacy behaviour: also record the guidance as a durable
            # run event so old UIs keep seeing it in the run log.
            legacy = store.append_operator_guidance(
                actor_id=current["id"], run_id=run_id, guidance=body
            )
            # v3.1: enqueue for injection into the next ReAct iteration.
            entry = store.enqueue_guidance(
                run_id=run_id,
                actor_id=current["id"],
                actor_username=current.get("username", current["id"]),
                body=body,
                target_agent_id=str(target_agent_id) if target_agent_id else None,
                budget_extension_seconds=budget_extension,
            )
            return JSONResponse(
                {"ok": True, "data": {"event": legacy, "queued": entry}},
                status_code=201,
            )
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))
        except KeyError:
            return _error(404, "not_found", "run not found")
        except ValueError as exc:
            return _error(400, "invalid_guidance", str(exc))

    # ── v3.1 collaboration endpoints ─────────────────────────────────────

    async def collaborators(request: Request) -> Response:
        try:
            current = await actor(request, csrf=request.method == "POST")
            conversation_id = request.path_params["conversation_id"]
            # A read requires at least viewer access; a write requires owner.
            store.require_collaborator_access(
                conversation_id=conversation_id,
                actor_id=current["id"],
                required_role="viewer",
            )
            if request.method == "GET":
                return JSONResponse(
                    {"ok": True, "data": store.list_collaborators(conversation_id=conversation_id)}
                )
            store.require_collaborator_access(
                conversation_id=conversation_id,
                actor_id=current["id"],
                required_role="owner",
            )
            data = await _payload(request)
            username = str(data.get("username", "")).strip().lower()
            role = str(data.get("role", "collaborator"))
            if not username:
                return _error(400, "invalid_collaborator", "username is required")
            # Resolve username → actor_id via the store's connection.
            conn = store._connect()  # noqa: SLF001
            try:
                row = conn.execute(
                    "SELECT id FROM users WHERE username=? AND disabled_at_ms IS NULL",
                    (username,),
                ).fetchone()
                if not row:
                    return _error(404, "not_found", "user not found")
                target_actor_id = row["id"]
            finally:
                conn.close()
            store.add_collaborator(
                conversation_id=conversation_id,
                actor_id=target_actor_id,
                role=role,
                added_by_actor_id=current["id"],
            )
            return JSONResponse(
                {"ok": True, "data": store.list_collaborators(conversation_id=conversation_id)},
                status_code=201,
            )
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))
        except ValueError as exc:
            return _error(400, "invalid_collaborator", str(exc))
        except KeyError:
            return _error(404, "not_found", "conversation not found")

    async def notes(request: Request) -> Response:
        try:
            current = await actor(request, csrf=request.method == "POST")
            conversation_id = request.path_params["conversation_id"]
            store.require_collaborator_access(
                conversation_id=conversation_id,
                actor_id=current["id"],
                required_role="viewer",
            )
            if request.method == "GET":
                after = int(request.query_params.get("after_ms", "0") or 0)
                return JSONResponse(
                    {"ok": True, "data": store.list_notes(conversation_id=conversation_id, after_ms=after)}
                )
            store.require_collaborator_access(
                conversation_id=conversation_id,
                actor_id=current["id"],
                required_role="collaborator",
            )
            data = await _payload(request)
            note = store.append_note(
                conversation_id=conversation_id,
                actor_id=current["id"],
                body=str(data.get("body", "")),
            )
            note.setdefault("actor_username", current.get("username"))
            return JSONResponse({"ok": True, "data": note}, status_code=201)
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))
        except ValueError as exc:
            return _error(400, "invalid_note", str(exc))
        except KeyError:
            return _error(404, "not_found", "conversation not found")

    async def presence(request: Request) -> Response:
        try:
            current = await actor(request, csrf=True)
            conversation_id = request.path_params["conversation_id"]
            store.require_collaborator_access(
                conversation_id=conversation_id,
                actor_id=current["id"],
                required_role="viewer",
            )
            data = await _payload(request)
            store.heartbeat_presence(
                conversation_id=conversation_id,
                actor_id=current["id"],
                typing=bool(data.get("typing", False)),
            )
            return JSONResponse(
                {"ok": True, "data": store.active_presence(conversation_id=conversation_id)}
            )
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))
        except KeyError:
            return _error(404, "not_found", "conversation not found")

    async def conversation_events(request: Request) -> Response:
        """SSE stream over the durable ``conversation_broadcasts`` table.

        Reactive by design: the loop only sleeps when the cursor is caught up.
        Every heartbeat re-checks collaborator access so revocations propagate
        without waiting for the browser to reconnect.
        """
        try:
            current = await actor(request)
            conversation_id = request.path_params["conversation_id"]
            store.require_collaborator_access(
                conversation_id=conversation_id,
                actor_id=current["id"],
                required_role="viewer",
            )
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))
        except KeyError:
            return _error(404, "not_found", "conversation not found")

        max_duration = int(os.environ.get("MUNIN_SSE_MAX_SECONDS", "14400"))
        heartbeat_every = int(os.environ.get("MUNIN_SSE_HEARTBEAT_SECONDS", "20"))
        actor_id = current["id"]

        async def stream() -> AsyncIterator[bytes]:
            loop = asyncio.get_event_loop()
            start = loop.time()
            last_heartbeat = start
            cursor = int(request.headers.get("last-event-id", request.query_params.get("after", "0")) or 0)
            yield b": munin-conversation-stream v3.1\n\n"
            while (loop.time() - start) < max_duration:
                now = loop.time()
                emitted = 0
                try:
                    broadcasts = store.conversation_broadcasts_after(
                        conversation_id=conversation_id, after_sequence=cursor
                    )
                except Exception as exc:  # noqa: BLE001
                    broadcasts = []
                    yield (
                        f"event: warning\ndata: {json.dumps({'message': str(exc)}, separators=(',', ':'))}\n\n"
                    ).encode()
                for entry in broadcasts:
                    cursor = int(entry["sequence"])
                    payload = json.dumps(entry.get("payload") or {}, separators=(",", ":"))
                    yield f"id: {cursor}\nevent: {entry['kind']}\ndata: {payload}\n\n".encode()
                    emitted += 1

                if (now - last_heartbeat) >= heartbeat_every:
                    # Re-verify access on every heartbeat so revocations propagate mid-stream.
                    try:
                        store.require_collaborator_access(
                            conversation_id=conversation_id,
                            actor_id=actor_id,
                            required_role="viewer",
                        )
                    except PermissionError:
                        yield (
                            "event: close\n"
                            f"data: {json.dumps({'reason': 'access_revoked'}, separators=(',', ':'))}\n\n"
                        ).encode()
                        return
                    hb = {
                        "elapsed_seconds": int(now - start),
                        "cursor": cursor,
                    }
                    yield f"event: heartbeat\ndata: {json.dumps(hb, separators=(',', ':'))}\n\n".encode()
                    last_heartbeat = now

                if await request.is_disconnected():
                    return
                # Only sleep when the cursor is caught up; a bursty producer
                # keeps the loop hot without hammering Turso between events.
                if emitted == 0:
                    await asyncio.sleep(1)

            yield f"event: close\ndata: {json.dumps({'reason': 'max_duration'}, separators=(',', ':'))}\n\n".encode()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    async def human_resolution(request: Request) -> Response:
        try:
            current = await actor(request, csrf=True)
            data = await _payload(request)
            result = store.resolve_human_decision(actor_id=current["id"], request_id=request.path_params["request_id"], choice=str(data.get("choice", "")), nonce=str(data.get("nonce", "")), guidance=str(data.get("guidance", "")))
            return JSONResponse({"ok": True, "data": result})
        except PermissionError as exc:
            return _error(403, "human_resolution_denied", str(exc))
        except KeyError:
            return _error(404, "not_found", "human request not found")

    async def branch(request: Request) -> Response:
        try:
            current = await actor(request, csrf=True)
            data = await _payload(request)
            result = store.create_operation_branch(actor_id=current["id"], parent_run_id=request.path_params["run_id"], fork_event_id=str(data.get("fork_event_id", "")), hypothesis=str(data.get("hypothesis", "")), replay_mode=str(data.get("replay_mode", "recorded")))
            return JSONResponse({"ok": True, "data": result}, status_code=201)
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))
        except KeyError:
            return _error(404, "not_found", "run or fork event not found")
        except ValueError as exc:
            return _error(400, "invalid_branch", str(exc))

    async def branch_compare(request: Request) -> Response:
        try:
            current = await actor(request)
            return JSONResponse({"ok": True, "data": store.compare_operation_branch(actor_id=current["id"], branch_id=request.path_params["branch_id"])})
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))
        except KeyError:
            return _error(404, "not_found", "branch not found")

    async def run_events(request: Request) -> Response:
        """Server-sent events for a run, optimised for very long operations.

        Fixes vs. the pre-v3 shape:

        * The lifespan is bounded by ``MUNIN_SSE_MAX_SECONDS`` (default 4h) so
          an idle-but-still-executing 3h30m run does not silently truncate at
          the two-minute mark of the previous ``range(120)`` loop.
        * Heartbeat events are emitted at ``MUNIN_SSE_HEARTBEAT_SECONDS``
          (default 20s) with the current inferred phase, elapsed time, worker
          liveness and last cursor.  ~20s beats stay under the default idle
          budgets of cloudflared, ngrok and typical corporate LBs.
        * Terminal run states end the stream cleanly with ``event: close``
          so the client can stop reconnecting instead of thrashing.
        * ``last-event-id`` continues to be honoured on reconnect so no
          buffered event is missed after a transient disconnect.
        """

        try:
            current = await actor(request)
            run_id = request.path_params["run_id"]
            store.get_run_for_actor(actor_id=current["id"], run_id=run_id)
            after = int(request.headers.get("last-event-id", request.query_params.get("after", "0")) or 0)
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))

        max_duration = int(os.environ.get("MUNIN_SSE_MAX_SECONDS", "14400"))
        heartbeat_every = int(os.environ.get("MUNIN_SSE_HEARTBEAT_SECONDS", "20"))
        actor_id = current["id"]

        async def stream() -> AsyncIterator[bytes]:
            cursor = after
            loop = asyncio.get_event_loop()
            start = loop.time()
            last_heartbeat = start
            # Initial marker so the client can flip its "connecting" state.
            yield b": munin-stream v3\n\n"
            while (loop.time() - start) < max_duration:
                emitted = 0
                try:
                    for event in store.run_events_after(run_id=run_id, after_sequence=cursor):
                        cursor = event["sequence"]
                        payload = json.dumps(event, separators=(",", ":"))
                        yield f"id: {cursor}\nevent: run-event\ndata: {payload}\n\n".encode()
                        emitted += 1
                except Exception as exc:  # noqa: BLE001
                    # Never let a transient store hiccup take down the SSE
                    # connection.  Emit a `warning` event so the UI can show
                    # a soft indicator and let the client reconnect.
                    payload = json.dumps({"message": str(exc)}, separators=(",", ":"))
                    yield f"event: warning\ndata: {payload}\n\n".encode()

                now = loop.time()
                # Always emit a heartbeat at the configured interval, even if
                # events were flushed this tick, so proxies see traffic and
                # the UI stays honest about elapsed time / phase.
                if (now - last_heartbeat) >= heartbeat_every:
                    try:
                        snapshot = store.get_run_for_actor(actor_id=actor_id, run_id=run_id)
                    except PermissionError:
                        yield (
                            "event: close\n"
                            f"data: {json.dumps({'reason': 'access_revoked'}, separators=(',', ':'))}\n\n"
                        ).encode()
                        return
                    except Exception:  # noqa: BLE001
                        snapshot = {"state": "unknown"}
                    reasoning = snapshot.get("reasoning") or []
                    tools = snapshot.get("tools") or []
                    heartbeat = {
                        "phase": _phase_from_events(reasoning, tools),
                        "state": snapshot.get("state", "unknown"),
                        "elapsed_seconds": int(now - start),
                        "cursor": cursor,
                        "worker_alive": _worker_alive(snapshot),
                        "reasoning_count": len(reasoning),
                        "tool_count": len(tools),
                    }
                    yield f"event: heartbeat\ndata: {json.dumps(heartbeat, separators=(',', ':'))}\n\n".encode()
                    last_heartbeat = now
                    if snapshot.get("state") in TERMINAL_STATES:
                        yield f"event: close\ndata: {json.dumps({'final_state': snapshot['state']}, separators=(',', ':'))}\n\n".encode()
                        return

                # Detect operator disconnect early to release the connection.
                if await request.is_disconnected():
                    return

                # Poll interval — short so events feel live, but not tight
                # enough to hammer the store.  Long-running runs spend most
                # of their time here between reasoning bursts.
                await asyncio.sleep(1)

            yield f"event: close\ndata: {json.dumps({'reason': 'max_duration', 'max_seconds': max_duration}, separators=(',', ':'))}\n\n".encode()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    async def replay(request: Request) -> Response:
        try:
            current = await actor(request)
            run_id = request.path_params["run_id"]
            store.get_run_for_actor(actor_id=current["id"], run_id=run_id)
            return JSONResponse({"ok": True, "data": store.recorded_replay(run_id=run_id, snapshot_id=request.path_params["snapshot_id"])})
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))
        except KeyError:
            return _error(404, "not_found", "replay snapshot not found")

    routes = [
        Route("/health", health), Route("/api/agents", agents, methods=["GET"]), Route("/api/page-agent/actions", page_agent_action, methods=["POST"]), Route("/api/provider-profiles", provider_profiles, methods=["GET", "POST"]), Route("/api/provider-profiles/{profile_id}/{action}", provider_profile_action, methods=["POST"]), Route("/api/auth/bootstrap", bootstrap, methods=["POST"]), Route("/api/auth/login", login, methods=["POST"]), Route("/api/auth/recovery/request", request_password_recovery, methods=["POST"]), Route("/api/auth/recovery/consume", consume_password_recovery, methods=["POST"]),
        Route("/api/auth/session", session, methods=["GET"]), Route("/api/auth/logout", logout, methods=["POST"]),
        Route("/api/conversations", conversations, methods=["GET", "POST"]), Route("/api/conversations/{conversation_id}/export", conversation_export, methods=["GET"]), Route("/api/conversations/{conversation_id}", conversation_detail, methods=["GET", "PATCH", "DELETE"]), Route("/api/artifacts/{artifact_id}", artifact, methods=["GET"]),
        Route("/api/conversations/{conversation_id}/turns", turn, methods=["POST"]), Route("/api/runs/{run_id}", run, methods=["GET"]),
        Route("/api/runs/{run_id}/detail", run_detail, methods=["GET"]), Route("/api/runs/{run_id}/cancel", run_cancel, methods=["POST"]),
        Route("/api/runs/{run_id}/retry", run_retry, methods=["POST"]),
        Route("/api/runs/{run_id}/guidance", run_guidance, methods=["POST"]),
        Route("/api/runs/{run_id}/guidance", list_run_guidance, methods=["GET"]),
        Route("/api/runs/{run_id}/branches", branch, methods=["POST"]), Route("/api/branches/{branch_id}/compare", branch_compare, methods=["GET"]),
        Route("/api/human-requests/{request_id}/resolve", human_resolution, methods=["POST"]), Route("/api/runs/{run_id}/events", run_events, methods=["GET"]), Route("/api/runs/{run_id}/replay/{snapshot_id}", replay, methods=["GET"]),
        # v3.1.1 dev-only smoke path for the forge floating window
        # (gated by MUNIN_DEV_ENDPOINTS_ENABLED=1 inside the handler).
        Route("/api/dev/simulate-forge/{run_id}", simulate_forge, methods=["POST"]),
        # v3.1 multi-operator collaboration
        Route("/api/conversations/{conversation_id}/collaborators", collaborators, methods=["GET", "POST"]),
        Route("/api/conversations/{conversation_id}/notes", notes, methods=["GET", "POST"]),
        Route("/api/conversations/{conversation_id}/presence", presence, methods=["POST"]),
        Route("/api/conversations/{conversation_id}/events", conversation_events, methods=["GET"]),
    ]
    return SecurityHeaders(Starlette(debug=False, routes=routes))  # type: ignore[return-value]


def app_from_environment() -> Starlette:
    from ..mcp.config import get_settings

    settings = get_settings()
    if not settings.db_url.startswith(("libsql://", "libsqls://")):
        raise RuntimeError("production API requires MUNIN_DB_URL=libsql://... authoritative Turso storage")
    return create_app(ProductionStore.for_settings(settings, master_key=ProductionStore.master_key_from_environment()))
