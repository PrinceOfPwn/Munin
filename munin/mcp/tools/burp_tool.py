# tags: [valravn, mcp-tool, burp-suite, dast, capabilities, registry, BurpExtensionClient, burp_status, burp_health_check, burp_check_scope, burp_get_proxy_count, burp_invoke, resilience-wrapper, httpx-client, runtime-non-fatal, mesh-valravn]
"""Burp DAST surface wrapper — resilient HTTP bridge to the Valravn Burp extension.

This module is Munin's resilient gateway to the Burp Suite extension REST API
exposed by ``valravn/burp-extension/`` at ``127.0.0.1:8111``. It is intentionally
*synchronous* and *lazy* so the Munin runtime can boot even when:

- Burp is not running (CI, dev laptops without Burp, GitHub Actions runners)
- the Valravn extension is not loaded yet
- the operator hasn't installed Java 21 / the JAR

Every tool here wraps a remote HTTP call in try/except and converts any failure
(connect refused, timeout, HTTP 5xx, malformed JSON, unexpected exception) into a
structured ``{"ok": False, "error": {"code", "message", "hint"}}`` envelope. No
exception escapes the wrapper, so a Burp-side failure never cancels an in-flight
Munin run. The ``audited_tool`` decorator on each public tool adds a second
guardrail layer (its own exception capture + audit trail).

The Burp MCP server (``valravn/mcp-server/``, package ``burpsuite_mcp``) is the
canonical LLM-facing surface; this wrapper is Munin's *authority-side* bridge to
the same extension, so Munin can drive Burp directly without a stdio MCP
subprocess. Both surfaces target the same REST API on the same port.

See:
- ``valravn/burp-extension/src/main/java/com/valravn/server/ApiServer.java``
  for the authoritative endpoint list.
- ``valravn/mcp-server/src/burpsuite_mcp/client.py`` for the canonical httpx
  client that talks to the same API; this wrapper mirrors its error envelope
  contract (``_connect_error_envelope``, ``_http_status_envelope``) so error
  payloads are interchangeable.
- ``.opencode/skills/valravn-diagnostic/SKILL.md`` for failure-mode triage.
"""

from __future__ import annotations

import os
import threading
from typing import Any

import httpx

from ..main import MCP, audited_tool  # noqa: TID252

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# All five knobs come from the env to keep config declarative and match the
# upstream `burpsuite_mcp.config` defaults. Defaults intentionally mirror the
# extension's own config tab so single-host deployments work with zero env vars.

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8111
_DEFAULT_TIMEOUT = 30.0
_DEFAULT_MAX_RESPONSE = 50_000  # chars; same default as burpsuite_mcp.config


def _burp_base_url() -> str:
    host = os.environ.get("BURP_API_HOST", _DEFAULT_HOST).strip() or _DEFAULT_HOST
    port = int(os.environ.get("BURP_API_PORT", str(_DEFAULT_PORT)) or _DEFAULT_PORT)
    return f"http://{host}:{port}"


def _burp_timeout() -> float:
    raw = os.environ.get("BURP_API_TIMEOUT", str(_DEFAULT_TIMEOUT))
    try:
        return float(raw) if raw else _DEFAULT_TIMEOUT
    except ValueError:
        return _DEFAULT_TIMEOUT


def _burp_max_response() -> int:
    raw = os.environ.get("BURP_MAX_RESPONSE_SIZE", str(_DEFAULT_MAX_RESPONSE))
    try:
        return int(raw) if raw else _DEFAULT_MAX_RESPONSE
    except ValueError:
        return _DEFAULT_MAX_RESPONSE


# ---------------------------------------------------------------------------
# Lazy singleton httpx client
# ---------------------------------------------------------------------------
# A single ``httpx.Client`` is reused across calls so we benefit from HTTP
# keep-alive and don't pay TCP setup/teardown on every invocation. The client is
# created lazily on first use — not at import time — so initializing this module
# never makes a network call and never raises on a missing Burp. The lazy init is
# guarded by a lock to be safe under the deep-agents executor's thread fan-out.

_CLIENT: httpx.Client | None = None
_CLIENT_LOCK = threading.Lock()


def _get_client() -> httpx.Client:
    """Lazy, thread-safe httpx client. Never raises."""
    global _CLIENT
    if _CLIENT is None:
        with _CLIENT_LOCK:
            if _CLIENT is None:
                _CLIENT = httpx.Client(
                    base_url=_burp_base_url(),
                    timeout=_burp_timeout(),
                    # Align keepalive with the Java extension's 24-thread pool so
                    # we don't open more sockets than the server can service.
                    limits=httpx.Limits(max_connections=24, max_keepalive_connections=16),
                )
    return _CLIENT


def _shutdown_client() -> None:
    """Close the shared client. Safe to call multiple times."""
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is not None:
            try:
                _CLIENT.close()
            except Exception:
                pass
            _CLIENT = None


# ---------------------------------------------------------------------------
# Error envelopes (mirror burpsuite_mcp.client contract)
# ---------------------------------------------------------------------------


def _connect_error_envelope() -> dict[str, Any]:
    return {
        "error": f"Cannot connect to Burp extension at {_burp_base_url()}. Is the extension loaded?",
        "code": "extension_unreachable",
        "hint": "Open Burp, ensure the Valravn extension is loaded on port BURP_API_PORT, then retry. CI environments without Burp should expect this error and downgrade gracefully.",
    }


def _http_status_envelope(e: httpx.HTTPStatusError) -> dict[str, Any]:
    """Preserve Java-side {error, code, hint} envelope when present."""
    body = e.response.text
    try:
        parsed = e.response.json()
        if isinstance(parsed, dict) and "error" in parsed:
            return {
                "error": parsed.get("error", body),
                "code": parsed.get("code", f"http_{e.response.status_code}"),
                "hint": parsed.get("hint", ""),
            }
    except Exception:
        pass
    return {
        "error": f"HTTP {e.response.status_code}: {body[:500]}",
        "code": f"http_{e.response.status_code}",
        "hint": "",
    }


def _generic_exception_envelope(e: Exception) -> dict[str, Any]:
    """Fallback envelope for unexpected httpx/client errors.

    ``str(e)`` is empty for some httpx exceptions (ReadTimeout('') /
    ConnectTimeout) — always include the class name so the operator gets
    actionable text.
    """
    detail = str(e) or "(no detail)"
    cls = type(e).__name__
    hint = ""
    if "Timeout" in cls:
        hint = (
            f"Burp extension didn't respond within {_burp_timeout()}s. "
            "The Java side may still be waiting on the target — "
            "raise BURP_API_TIMEOUT or shorten the target's read window."
        )
    elif "Connect" in cls:
        hint = "Verify the Burp extension is loaded and listening on BURP_API_PORT."
    return {"error": f"{cls}: {detail}", "code": "client_exception", "hint": hint}


# ---------------------------------------------------------------------------
# Low-level HTTP helpers
# ---------------------------------------------------------------------------


def _request(method: str, path: str, *, json_body: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Issue a request to the Burp extension and return a dict envelope.

    Always returns a dict. Never raises. On any failure the dict carries an
    ``error`` sub-dict with ``code``/``message``/``hint``.
    """
    try:
        client = _get_client()
        if method.upper() == "GET":
            resp = client.get(path, params=params)
        elif method.upper() == "POST":
            resp = client.post(path, json=json_body or {})
        elif method.upper() == "DELETE":
            resp = client.delete(path)
        elif method.upper() == "PUT":
            resp = client.put(path, json=json_body or {})
        else:
            return {"error": f"Unsupported method: {method}", "code": "bad_method", "hint": "Use GET, POST, PUT or DELETE."}
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:
            return {"ok": True, "text": resp.text[:_burp_max_response()]}
    except httpx.ConnectError:
        return _connect_error_envelope()
    except httpx.HTTPStatusError as e:
        return _http_status_envelope(e)
    except Exception as e:  # pragma: no cover - guardrail
        return _generic_exception_envelope(e)


# ---------------------------------------------------------------------------
# Result helpers (match valravn_tool pattern for caller-side consistency)
# ---------------------------------------------------------------------------


def _ok(tool: str, summary: str, data: dict[str, Any], *, artifacts: list[Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": True, "tool": tool, "mode": "sync", "summary": summary, "data": data}
    if artifacts:
        result["artifacts"] = artifacts
    return result


def _error(tool: str, err_envelope: dict[str, Any]) -> dict[str, Any]:
    """Wrap a low-level error envelope into a tool-level result envelope.

    ``err_envelope`` is the dict returned by ``_request`` on failure; it has
    ``error`` (string), ``code`` (string), ``hint`` (string) keys.
    """
    return {
        "ok": False,
        "tool": tool,
        "mode": "sync",
        "summary": f"{tool} failed: {err_envelope.get('error', 'unknown error')}",
        "error": {
            "code": err_envelope.get("code", "burp_failed"),
            "message": err_envelope.get("error", "unknown error"),
            "hint": err_envelope.get("hint", ""),
        },
    }


def _is_error_envelope(payload: dict[str, Any]) -> bool:
    """A payload is an error envelope if it carries a top-level non-empty ``error``."""
    return bool(payload.get("error"))


# ---------------------------------------------------------------------------
# Public tools
# ---------------------------------------------------------------------------

BURP_TOOLS = frozenset(
    {
        "burp_status",
        "burp_health_check",
        "burp_check_scope",
        "burp_get_proxy_count",
        "burp_invoke",
    }
)


@MCP.tool()
@audited_tool("burp_status", "passive", lambda *a, **k: "sync")
def burp_status(probe: bool = False, run_id: str = "") -> dict[str, Any]:
    """Inspect the Valravn Burp extension: load state, version and reachable endpoints.

    Passive. Safe to call from CI runners without Burp installed — returns a
    structured ``ok=False`` envelope (``code=extension_unreachable``) when the
    extension is not running, without raising.
    """
    payload = _request("GET", "/api/health")
    if _is_error_envelope(payload):
        return _error("burp_status", payload)
    data = {
        "extension": payload.get("extension", "Valravn MCP"),
        "version": payload.get("version", "unknown"),
        "status": payload.get("status", "unknown"),
        "base_url": _burp_base_url(),
        "timeout_seconds": _burp_timeout(),
    }
    if probe:
        # Cheap secondary probe: scope endpoint returns the active scope rules.
        scope_probe = _request("GET", "/api/scope")
        if not _is_error_envelope(scope_probe):
            data["scope_probe"] = {
                "include_rules_count": len(scope_probe.get("include_rules", [])) if isinstance(scope_probe.get("include_rules"), list) else 0,
                "exclude_rules_count": len(scope_probe.get("exclude_rules", [])) if isinstance(scope_probe.get("exclude_rules"), list) else 0,
                "mode": scope_probe.get("mode", "unknown"),
            }
        else:
            data["scope_probe"] = {"reachable": False, "code": scope_probe.get("code")}
    return _ok("burp_status", f"Burp extension {data['status']} at {data['base_url']}", data)


@MCP.tool()
@audited_tool("burp_health_check", "passive", lambda *a, **k: "sync")
def burp_health_check(run_id: str = "") -> dict[str, Any]:
    """Cheap boolean probe: is the Valravn Burp extension reachable right now?

    Returns ``{"ok": True, "data": {"healthy": True}}`` when the extension HTTP
    API is up; ``{"ok": False, "error": {"code": "extension_unreachable"}}``
    otherwise. Useful as a guard before any ``burp_invoke`` call.
    """
    payload = _request("GET", "/api/health")
    healthy = (not _is_error_envelope(payload)) and payload.get("status") == "ok"
    return _ok(
        "burp_health_check",
        "Burp extension is healthy" if healthy else "Burp extension unreachable",
        {"healthy": bool(healthy), "status": payload.get("status") if not _is_error_envelope(payload) else None, "base_url": _burp_base_url()},
    )


@MCP.tool()
@audited_tool("burp_check_scope", "passive", lambda *a, **k: "sync")
def burp_check_scope(url: str, run_id: str = "") -> dict[str, Any]:
    """Ask the Burp extension whether ``url`` is in the configured engagement scope.

    Wraps ``POST /api/scope/check`` with body ``{"url": url}``. The extension's
    scope is owned by the operator (defaults to ``operator`` mode — out-of-scope
    requests are logged to ``.valravn-intel/_audit.log`` and proceed; ``strict``
    mode hard-blocks). This tool is passive (read-only) and never mutates scope.
    """
    if not url or not isinstance(url, str):
        return _error("burp_check_scope", {"error": "url is required", "code": "bad_args", "hint": "Pass a fully-qualified URL string."})
    payload = _request("POST", "/api/scope/check", json_body={"url": url})
    if _is_error_envelope(payload):
        return _error("burp_check_scope", payload)
    in_scope = bool(payload.get("in_scope", False))
    return _ok(
        "burp_check_scope",
        f"URL is {'in' if in_scope else 'out of'} scope",
        {"in_scope": in_scope, "url": url, "mode": payload.get("mode", "operator")},
    )


@MCP.tool()
@audited_tool("burp_get_proxy_count", "passive", lambda *a, **k: "sync")
def burp_get_proxy_count(host: str = "", run_id: str = "") -> dict[str, Any]:
    """Sub-millisecond read of the Burp Proxy history size (and host-scoped slice if given).

    Wraps ``GET /api/proxy/count`` with optional ``host`` query param for
    host-exact filtering. Useful as a fast "did my probe land in Proxy?" check
    without paging the full history. Passive.
    """
    params: dict[str, Any] = {}
    if host:
        params["host"] = host
    payload = _request("GET", "/api/proxy/count", params=params)
    if _is_error_envelope(payload):
        return _error("burp_get_proxy_count", payload)
    count = payload.get("count", payload.get("total", 0))
    return _ok(
        "burp_get_proxy_count",
        f"Proxy history has {count} entries" + (f" for host={host}" if host else ""),
        {"count": count, "host_filter": host or None, "base_url": _burp_base_url()},
    )


@MCP.tool()
@audited_tool("burp_invoke", "active", lambda *a, **k: "sync")
def burp_invoke(
    endpoint: str,
    method: str = "GET",
    json_body: dict[str, Any] | None = None,
    run_id: str = "",
) -> dict[str, Any]:
    """Generic resilient dispatcher to any Valravn Burp extension REST endpoint.

    This is the generic surface for the ~250 individual Burp tools shaped behind
    the extension API. Instead of wrapping each one explicitly, ``burp_invoke``
    lets the agent (or operator) call any endpoint by path + method, with an
    arbitrary JSON body. All errors are caught and returned as structured
    envelopes — the Munin runtime never cancels an in-flight run because a Burp
    tool raised.

    Safety:
    - Active classification: this tool is marked ``active`` for audit because
      arbitrary endpoints can drive Burp scanner / collaborator / send flows.
      Engagement policy still applies — ``check_scope`` runs in the extension
      itself before any HTTP send tool fires.
    - The extension owns scope enforcement, never this wrapper.

    Args:
        endpoint: Path under the extension API, e.g. ``/api/scanner/scan``,
            ``/api/proxy/history``, ``/api/collaborator/payload``. ``http://``
            prefixes are stripped automatically (the base URL is configured
            from ``BURP_API_HOST`` / ``BURP_API_PORT``).
        method: HTTP verb — ``GET``, ``POST``, ``PUT``, ``DELETE``. Defaults to
            ``GET``. Unknown verbs return a structured ``bad_method`` error.
        json_body: Optional JSON-serializable body for POST/PUT. Pass ``None``
            or ``{}`` for bodyless calls.

    Returns:
        A dict envelope ``{"ok", "tool", "mode", "summary", "data"}`` on success,
        or ``{"ok": False, "error": {"code", "message", "hint"}}`` on failure.
        The extension's own structured response is passed through verbatim under
        ``data``.
    """
    if not endpoint or not isinstance(endpoint, str):
        return _error("burp_invoke", {"error": "endpoint is required", "code": "bad_args", "hint": "Pass the extension API path, e.g. /api/scanner/scan."})
    # Tolerate callers that pass a full URL by mistake.
    path = endpoint
    if path.startswith("http://") or path.startswith("https://"):
        # Best-effort strip; httpx with base_url would otherwise complain.
        try:
            from urllib.parse import urlparse

            parsed = urlparse(path)
            path = parsed.path or "/"
            if not path.startswith("/"):
                path = "/" + path
        except Exception:
            pass
    if not path.startswith("/"):
        path = "/" + path

    verb = (method or "GET").upper()
    payload = _request(verb, path, json_body=json_body)
    if _is_error_envelope(payload):
        return _error("burp_invoke", payload)
    # Pass the extension's payload through under `data`; truncate long text.
    cleaned = payload
    if isinstance(payload, dict):
        # Already structured — pass-through.
        cleaned = payload
    elif isinstance(payload, (list, tuple)):
        cleaned = {"items": list(payload)}
    else:
        cleaned = {"value": payload}
    return _ok(
        "burp_invoke",
        f"{verb} {path} returned",
        cleaned,
    )


# ---------------------------------------------------------------------------
# Capability catalog entry (declared here so it lives next to the wrapper)
# ---------------------------------------------------------------------------
# The canonical capability profile list lives in munin/mcp/capabilities.py.
# This block is informational only — capabilities.py imports BURP_TOOLS at the
# bottom and adds the ``burp_dast`` profile, so the catalog stays the SSOT.
