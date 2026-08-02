"""Authenticated ASGI boundary for the Munin Production Suite.

Fase 2 of the issue-#9 migration (Arch A → Arch B) removed the vast bulk of
the legacy operator surface — everything driven by ``ProductionDispatcher``,
lease-worker SSE, the multi-operator collab/notes/presence layer, and the
old ``turns + runs SSE`` two-hop is gone.  What remains is the minimum the
new AI SDK v5 client (``AppShell`` + ``AgentConsole``) actually needs:

* Auth: session/login/logout/bootstrap + CSRF + password recovery.
* Conversations: list, create, GET/PATCH/DELETE aggregate, export.
* Artifacts: read + inline download.
* Provider profiles: encrypted HTTPS OpenAI-compatible BYOK metadata and
  active-profile selection; plaintext keys never leave the server boundary.
* ``POST /api/chat`` and ``POST /api/chat/{run_id}/guidance`` (wired by
  :mod:`munin.production.chat`, which owns the supervisor_runner → SSE
  bridge).

Everything else — ``/api/runs/**``, ``/api/branches/**``, HITL resolve,
collaborators/notes/presence/broadcasts, agents catalog,
page-agent actions, dev simulate-forge — was deleted in Fase 2 and will
either be reintroduced as an AI-SDK data-part flow (HITL resolve, forthcoming)
or dropped for good.  See the migration kill-list for the full inventory.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger("munin.production.asgi")

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .chat import register_chat_routes
from .store import MuninStore, ProductionStore


def _allowed_origins() -> set[str]:
    """Re-read ``MUNIN_ALLOWED_ORIGINS`` on every call so runtime env updates
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


def create_http_app(store: Any, *, shared_state: Any = None) -> Starlette:
    """Create the session-authenticated production HTTP API over a shared store.

    Fase 3 (issue #9): renamed from ``create_app`` — see :mod:`munin.server`
    for the composed entry-point that mounts this alongside the FastMCP
    transport under a single Starlette app.  ``shared_state`` is the
    :class:`SharedStateStore` instance the AI-SDK chat handler forwards to
    ``supervisor_runner``; when the caller is the legacy
    ``app_from_environment`` shim it is left ``None`` and
    ``register_chat_routes`` falls back to a lazy per-request construction.

    Fase 4 (issue #9): ``store`` is now either a :class:`ProductionStore`
    (legacy / test fixtures) or a :class:`MuninStore` façade that routes
    calls across a hot SQLite backend and a durable Turso backend.  The
    handlers only touch the public method surface both classes expose,
    so no branching is required here.
    """

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

    # ── auth ────────────────────────────────────────────────────────────

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

    # ── conversations ──────────────────────────────────────────────────

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

    async def provider_profiles(request: Request) -> Response:
        try:
            current = await actor(request, csrf=request.method == "POST")
            if request.method == "GET":
                return JSONResponse({"ok": True, "data": store.list_provider_profiles(actor_id=current["id"])})
            data = await _payload(request)
            result = store.save_provider_profile(
                actor_id=current["id"],
                label=str(data.get("label") or data.get("provider") or "Provider"),
                provider=str(data.get("provider") or "openai-compatible"),
                base_url=str(data.get("base_url") or data.get("endpoint") or ""),
                model=str(data.get("model") or ""),
                uses=list(data.get("uses") or ["chat"]),
                plaintext_key=str(data.get("api_key") or data.get("key") or ""),
            )
            if bool(data.get("activate")):
                result = store.set_active_provider_profile(actor_id=current["id"], profile_id=result["id"])
            return JSONResponse({"ok": True, "data": result}, status_code=201)
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))
        except ValueError as exc:
            return _error(400, "invalid_provider_profile", str(exc))

    async def provider_profile_activate(request: Request) -> Response:
        try:
            current = await actor(request, csrf=True)
            if request.path_params["profile_id"] == "default":
                result = store.clear_active_provider_profile(actor_id=current["id"])
                return JSONResponse({"ok": True, "data": result})
            result = store.set_active_provider_profile(
                actor_id=current["id"], profile_id=request.path_params["profile_id"]
            )
            return JSONResponse({"ok": True, "data": result})
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))
        except KeyError:
            return _error(404, "not_found", "provider profile not found")

    inline_artifact_media_types = {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "text/plain",
        "text/csv",
        "text/markdown",
        "application/json",
    }

    async def artifact(request: Request) -> Response:
        try:
            current = await actor(request)
            result = store.get_artifact(actor_id=current["id"], artifact_id=request.path_params["artifact_id"])
            download_requested = request.query_params.get("download") == "true"
            inline_requested = request.query_params.get("inline") == "true"
            if download_requested or inline_requested:
                media_type = str(result["media_type"] or "application/octet-stream")
                base_media_type = media_type.split(";", 1)[0].strip().lower()
                disposition = (
                    "inline"
                    if inline_requested
                    and not download_requested
                    and base_media_type in inline_artifact_media_types
                    else "attachment"
                )
                filename = str(result["filename"]).replace('"', "")
                return Response(
                    result["content"],
                    media_type=media_type,
                    headers={
                        "Content-Disposition": f'{disposition}; filename="{filename}"',
                        "X-Content-Type-Options": "nosniff",
                    },
                )
            return JSONResponse({"ok": True, "data": result})
        except PermissionError as exc:
            return _error(403, "forbidden", str(exc))
        except KeyError:
            return _error(404, "not_found", "artifact not found")

    routes = [
        Route("/health", health),
        # ── auth ────────────────────────────────────────────────────
        Route("/api/auth/bootstrap", bootstrap, methods=["POST"]),
        Route("/api/auth/login", login, methods=["POST"]),
        Route("/api/auth/recovery/request", request_password_recovery, methods=["POST"]),
        Route("/api/auth/recovery/consume", consume_password_recovery, methods=["POST"]),
        Route("/api/auth/session", session, methods=["GET"]),
        Route("/api/auth/logout", logout, methods=["POST"]),
        # ── conversations ───────────────────────────────────────────
        Route("/api/conversations", conversations, methods=["GET", "POST"]),
        Route("/api/conversations/{conversation_id}/export", conversation_export, methods=["GET"]),
        Route("/api/conversations/{conversation_id}", conversation_detail, methods=["GET", "PATCH", "DELETE"]),
        Route("/api/provider-profiles", provider_profiles, methods=["GET", "POST"]),
        Route("/api/provider-profiles/{profile_id}/activate", provider_profile_activate, methods=["POST"]),
        # ── artifacts (read-only; writes come from run finalisation) ─
        Route("/api/artifacts/{artifact_id}", artifact, methods=["GET"]),
    ]

    # Fase 1a (issue #9): AI SDK v5 chat endpoint that drives supervisor_runner
    # directly.  Post-Fase-2 this is the ONLY runtime surface the frontend
    # needs; the pre-migration ``/turns`` + ``/events`` two-hop is gone.
    register_chat_routes(
        routes,
        store=store,
        actor_dependency=actor,
        error_response=_error,
        payload_reader=_payload,
        shared_state=shared_state,
    )

    return SecurityHeaders(Starlette(debug=False, routes=routes))  # type: ignore[return-value]


# ``create_app`` is retained as a backward-compatible alias for the pre-Fase-3
# test suite and CLI paths.  New callers should use ``create_http_app`` and
# supply ``shared_state`` explicitly; :mod:`munin.server` is the canonical
# composed entry-point.
def create_app(store: Any) -> Starlette:  # noqa: D401 - back-compat shim
    """Backward-compatible alias for :func:`create_http_app` (no shared_state)."""
    return create_http_app(store=store, shared_state=None)


def app_from_environment() -> Starlette:
    from ..mcp.config import get_settings

    settings = get_settings()
    # Fase 4: prefer the split ``MuninStore``.  We accept either a Turso
    # URL on ``durable_db_url`` (canonical Fase-4 config) or the legacy
    # ``db_url`` env var — ``MuninStore.from_settings`` handles both.
    # A missing durable URL is now valid: the store degrades to
    # hot-only SQLite for development / hot-only smoke tests.
    return create_http_app(
        store=MuninStore.from_settings(
            settings, master_key=ProductionStore.master_key_from_environment()
        ),
        shared_state=None,
    )
