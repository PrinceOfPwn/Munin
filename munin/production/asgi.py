"""Authenticated ASGI boundary for the Munin Production Suite.

The MCP server remains available for editor/tool integrations.  Operator-facing
web traffic uses this service so browser sessions are opaque cookies rather than
shared MCP credentials stored in JavaScript.  MCP and Discord adapters call the
same :class:`ProductionStore` repository with a service actor.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import AsyncIterator
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from .agents import profile_catalog
from .page_agent import validate_page_action
from .store import ProductionStore


def _allowed_origins() -> set[str]:
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


class SecurityHeaders:
    """Small ASGI middleware compatible with the installed Starlette contract."""

    def __init__(self, app: Any, *, allowed_origins: set[str]) -> None:
        self.app = app
        self.allowed_origins = allowed_origins

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = {bytes(key).lower(): bytes(value) for key, value in scope.get("headers", [])}
        origin = headers.get(b"origin", b"").decode("latin-1").rstrip("/")
        if scope.get("method") == "OPTIONS":
            if origin not in self.allowed_origins:
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
                if origin in self.allowed_origins:
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

    allowed_origins = _allowed_origins()

    async def actor(request: Request, *, csrf: bool = False) -> dict[str, Any]:
        token = request.cookies.get("munin_session", "")
        result = store.authenticate(token)
        if not result:
            raise PermissionError("authentication required")
        if csrf:
            origin = request.headers.get("origin", "").rstrip("/")
            fetch_site = request.headers.get("sec-fetch-site", "")
            provided = request.headers.get("x-csrf-token", "")
            if not origin or origin not in allowed_origins or fetch_site not in {"same-origin", "same-site"}:
                raise PermissionError("cross-site request rejected")
            if not store.validate_csrf(session_id=result["session_id"], csrf_token=provided):
                raise PermissionError("CSRF validation failed")
        return result

    async def health(_: Request) -> Response:
        return JSONResponse({"ok": True, "service": "munin-production-api"})

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
        # A deployment wires the returned token to an approved out-of-band delivery adapter.
        # The HTTP response deliberately stays non-enumerating and never returns it.
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
            return JSONResponse(
                {
                    "ok": True,
                    "data": store.list_conversations(
                        actor_id=current["id"],
                        query=request.query_params.get("q", ""),
                        status=request.query_params.get("status", ""),
                        include_archived=request.query_params.get("archived") == "true",
                        limit=int(request.query_params.get("limit", "50")),
                        cursor_ms=int(request.query_params["cursor"]) if request.query_params.get("cursor") else None,
                    ),
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
                return JSONResponse({"ok": True, "data": store.get_conversation(actor_id=current["id"], conversation_id=conversation_id)})
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
            result = store.get_artifact(actor_id=current["id"], artifact_id=request.path_params["artifact_id"])
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
            result = store.create_turn(actor_id=current["id"], conversation_id=request.path_params["conversation_id"], content=str(data.get("content", "")), idempotency_key=key)
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
            result = store.get_run_for_actor(actor_id=current["id"], run_id=request.path_params["run_id"])
            return JSONResponse({"ok": True, "data": result})
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))
        except KeyError:
            return _error(404, "not_found", "run not found")

    async def run_detail(request: Request) -> Response:
        try:
            current = await actor(request)
            return JSONResponse({"ok": True, "data": store.get_run_detail_for_actor(actor_id=current["id"], run_id=request.path_params["run_id"])})
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

    async def run_guidance(request: Request) -> Response:
        try:
            current = await actor(request, csrf=True)
            data = await _payload(request)
            result = store.append_operator_guidance(actor_id=current["id"], run_id=request.path_params["run_id"], guidance=str(data.get("guidance", "")))
            return JSONResponse({"ok": True, "data": result}, status_code=201)
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))
        except KeyError:
            return _error(404, "not_found", "run not found")
        except ValueError as exc:
            return _error(400, "invalid_guidance", str(exc))

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
        try:
            current = await actor(request)
            run_id = request.path_params["run_id"]
            store.get_run_for_actor(actor_id=current["id"], run_id=run_id)
            after = int(request.headers.get("last-event-id", request.query_params.get("after", "0")) or 0)
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))

        async def stream() -> AsyncIterator[bytes]:
            cursor = after
            for _ in range(120):
                for event in store.run_events_after(run_id=run_id, after_sequence=cursor):
                    cursor = event["sequence"]
                    yield f"id: {cursor}\nevent: run-event\ndata: {json.dumps(event, separators=(',', ':'))}\n\n".encode()
                await asyncio.sleep(1)
            yield b"event: close\ndata: {}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

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
        Route("/api/runs/{run_id}/retry", run_retry, methods=["POST"]), Route("/api/runs/{run_id}/guidance", run_guidance, methods=["POST"]),
        Route("/api/runs/{run_id}/branches", branch, methods=["POST"]), Route("/api/branches/{branch_id}/compare", branch_compare, methods=["GET"]),
        Route("/api/human-requests/{request_id}/resolve", human_resolution, methods=["POST"]), Route("/api/runs/{run_id}/events", run_events, methods=["GET"]), Route("/api/runs/{run_id}/replay/{snapshot_id}", replay, methods=["GET"]),
    ]
    return SecurityHeaders(Starlette(debug=False, routes=routes), allowed_origins=allowed_origins)  # type: ignore[return-value]


def app_from_environment() -> Starlette:
    from ..mcp.config import get_settings

    settings = get_settings()
    if not settings.db_url.startswith(("libsql://", "libsqls://")):
        raise RuntimeError("production API requires MUNIN_DB_URL=libsql://... authoritative Turso storage")
    return create_app(ProductionStore.for_settings(settings, master_key=ProductionStore.master_key_from_environment()))
