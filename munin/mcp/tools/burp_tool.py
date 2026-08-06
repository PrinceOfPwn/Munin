# tags: [valravn, mcp-tool, burp-suite, dast, capabilities, registry, BurpExtensionClient, burp_status, burp_health_check, burp_check_scope, burp_get_proxy_count, burp_invoke, resilience-wrapper, httpx-client, runtime-non-fatal, mesh-valravn]
"""Resilient HTTP bridge from Munin to the Valravn Burp extension.

Burp is an optional execution surface. Importing this module never probes the
extension, and every call converts Burp/configuration/transport failures into a
normal tool result. A Burp outage must degrade only the Burp capability; it
must never abort the enclosing Munin run.

The Java extension exposes its REST API on ``127.0.0.1:8111`` by default.
``burp_invoke`` is the generic dispatcher while the other functions provide
small typed probes used by Munin and diagnostics.
"""

from __future__ import annotations

import math
import os
import threading
from typing import Any
from urllib.parse import urlparse

import httpx

from ..main import MCP, audited_tool  # noqa: TID252

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8111
_DEFAULT_TIMEOUT = 30.0
_DEFAULT_MAX_RESPONSE = 50_000
_ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "DELETE"})

_CLIENT: httpx.Client | None = None
_CLIENT_KEY: tuple[str, float] | None = None
_CLIENT_LOCK = threading.Lock()


def _burp_host() -> str:
    """Return a non-empty host without allowing config parsing to raise."""
    raw = os.environ.get("BURP_API_HOST", _DEFAULT_HOST)
    host = str(raw).strip() or _DEFAULT_HOST
    if "://" in host:
        parsed = urlparse(host)
        host = parsed.hostname or _DEFAULT_HOST
    return host


def _burp_port() -> int:
    """Return a valid TCP port, falling back instead of raising."""
    raw = os.environ.get("BURP_API_PORT", str(_DEFAULT_PORT))
    try:
        port = int(str(raw).strip() or _DEFAULT_PORT)
    except (TypeError, ValueError):
        return _DEFAULT_PORT
    return port if 1 <= port <= 65_535 else _DEFAULT_PORT


def _burp_base_url() -> str:
    host = _burp_host()
    # httpx expects IPv6 literals in brackets.
    rendered_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"http://{rendered_host}:{_burp_port()}"


def _positive_finite_float(raw: Any, default: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value > 0 else default


def _burp_timeout() -> float:
    return _positive_finite_float(
        os.environ.get("BURP_API_TIMEOUT", str(_DEFAULT_TIMEOUT)),
        _DEFAULT_TIMEOUT,
    )


def _burp_max_response() -> int:
    raw = os.environ.get("BURP_MAX_RESPONSE_SIZE", str(_DEFAULT_MAX_RESPONSE))
    try:
        value = int(str(raw).strip() or _DEFAULT_MAX_RESPONSE)
    except (TypeError, ValueError):
        return _DEFAULT_MAX_RESPONSE
    return value if value > 0 else _DEFAULT_MAX_RESPONSE


def _client_key() -> tuple[str, float]:
    return (_burp_base_url(), _burp_timeout())


def _close_client(client: httpx.Client | None) -> None:
    if client is None:
        return
    try:
        client.close()
    except Exception:
        # Client cleanup is best effort and must never affect the run.
        pass


def _reset_client(expected: httpx.Client | None = None) -> None:
    """Discard stale keep-alive connections after a transport failure.

    ``expected`` prevents a late failure from one thread from closing a newer
    client that another thread has already installed.
    """
    global _CLIENT, _CLIENT_KEY
    with _CLIENT_LOCK:
        if expected is not None and _CLIENT is not expected:
            return
        old = _CLIENT
        _CLIENT = None
        _CLIENT_KEY = None
    _close_client(old)


def _shutdown_client() -> None:
    """Compatibility alias used by tests and process shutdown hooks."""
    _reset_client()


def _get_client() -> httpx.Client:
    """Return a lazy client, rebuilding it when endpoint settings change."""
    global _CLIENT, _CLIENT_KEY
    key = _client_key()
    if _CLIENT is not None and _CLIENT_KEY == key:
        return _CLIENT

    with _CLIENT_LOCK:
        key = _client_key()
        if _CLIENT is not None and _CLIENT_KEY == key:
            return _CLIENT

        old = _CLIENT
        _CLIENT = httpx.Client(
            base_url=key[0],
            timeout=key[1],
            limits=httpx.Limits(
                max_connections=24,
                max_keepalive_connections=16,
            ),
        )
        _CLIENT_KEY = key

    _close_client(old)
    return _CLIENT


def _connect_error_envelope(exc: Exception | None = None) -> dict[str, Any]:
    detail = f" ({type(exc).__name__}: {exc})" if exc else ""
    return {
        "error": (
            f"Cannot connect to Burp extension at {_burp_base_url()}{detail}. "
            "Is the extension loaded?"
        ),
        "code": "extension_unreachable",
        "hint": (
            "Burp is optional. Continue the Munin run with other capabilities; "
            "retry Burp after the extension is available."
        ),
    }


def _timeout_error_envelope(exc: Exception) -> dict[str, Any]:
    detail = str(exc) or type(exc).__name__
    return {
        "error": f"Burp extension timed out after {_burp_timeout()}s: {detail}",
        "code": "extension_timeout",
        "hint": (
            "Continue the Munin run with other capabilities. Retry later or raise "
            "BURP_API_TIMEOUT when the endpoint is expected to be slow."
        ),
    }


def _http_status_envelope(exc: httpx.HTTPStatusError) -> dict[str, Any]:
    body = exc.response.text
    try:
        parsed = exc.response.json()
    except Exception:
        parsed = None
    if isinstance(parsed, dict) and parsed.get("error"):
        return {
            "error": parsed.get("error", body),
            "code": parsed.get("code", f"http_{exc.response.status_code}"),
            "hint": parsed.get("hint", ""),
        }
    return {
        "error": f"HTTP {exc.response.status_code}: {body[:500]}",
        "code": f"http_{exc.response.status_code}",
        "hint": "The Burp call failed, but the Munin run can continue.",
    }


def _generic_exception_envelope(exc: Exception) -> dict[str, Any]:
    detail = str(exc) or "(no detail)"
    return {
        "error": f"{type(exc).__name__}: {detail}",
        "code": "client_exception",
        "hint": (
            "The Burp capability failed locally. Continue the Munin run with "
            "remaining tools and inspect the Burp diagnostic trace."
        ),
    }


def _normalize_payload(response: httpx.Response) -> dict[str, Any]:
    """Normalize every successful HTTP response to a mapping.

    A list/scalar JSON body used to escape ``_request`` and later crash callers
    that assumed ``dict.get`` existed. Keeping the low-level contract total
    makes every public tool non-throwing even when the extension returns an
    unexpected-but-valid JSON shape.
    """
    try:
        parsed = response.json()
    except ValueError:
        return {"text": response.text[:_burp_max_response()]}

    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        return {"items": parsed}
    return {"value": parsed}


def _request(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Issue one Burp request and always return a dictionary."""
    verb = str(method or "").upper()
    if verb not in _ALLOWED_METHODS:
        return {
            "error": f"Unsupported method: {method}",
            "code": "bad_method",
            "hint": "Use GET, POST, PUT or DELETE.",
        }

    client: httpx.Client | None = None
    try:
        client = _get_client()
        if verb == "GET":
            response = client.get(path, params=params)
        elif verb == "POST":
            response = client.post(path, json=json_body or {})
        elif verb == "PUT":
            response = client.put(path, json=json_body or {})
        else:
            response = client.delete(path)
        response.raise_for_status()
        return _normalize_payload(response)
    except httpx.TimeoutException as exc:
        # A dead request can poison a keep-alive connection. Force the next
        # invocation to start from a fresh pool so Burp can recover in-place.
        _reset_client(client)
        return _timeout_error_envelope(exc)
    except httpx.TransportError as exc:
        _reset_client(client)
        return _connect_error_envelope(exc)
    except httpx.HTTPStatusError as exc:
        return _http_status_envelope(exc)
    except Exception as exc:  # pragma: no cover - final non-fatal boundary
        _reset_client(client)
        return _generic_exception_envelope(exc)


def _ok(
    tool: str,
    summary: str,
    data: dict[str, Any],
    *,
    artifacts: list[Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": True,
        "tool": tool,
        "mode": "sync",
        "summary": summary,
        "data": data,
    }
    if artifacts:
        result["artifacts"] = artifacts
    return result


def _error(tool: str, envelope: dict[str, Any]) -> dict[str, Any]:
    """Return a Burp-scoped failure that explicitly preserves run continuity."""
    code = str(envelope.get("code") or "burp_failed")
    return {
        "ok": False,
        "tool": tool,
        "mode": "sync",
        "summary": f"{tool} failed: {envelope.get('error', 'unknown error')}",
        "degraded": True,
        "failure_scope": "burp_only",
        "run_should_continue": True,
        "retryable": code
        in {
            "extension_unreachable",
            "extension_timeout",
            "client_exception",
        },
        "error": {
            "code": code,
            "message": envelope.get("error", "unknown error"),
            "hint": envelope.get(
                "hint",
                "Continue the Munin run with remaining capabilities.",
            ),
        },
    }


def _is_error_envelope(payload: Any) -> bool:
    return isinstance(payload, dict) and bool(payload.get("error"))


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
    """Inspect extension state without making Burp a runtime dependency."""
    payload = _request("GET", "/api/health")
    if _is_error_envelope(payload):
        return _error("burp_status", payload)

    data: dict[str, Any] = {
        "extension": payload.get("extension", "Valravn MCP"),
        "version": payload.get("version", "unknown"),
        "status": payload.get("status", "unknown"),
        "base_url": _burp_base_url(),
        "timeout_seconds": _burp_timeout(),
    }
    if probe:
        scope_probe = _request("GET", "/api/scope")
        if _is_error_envelope(scope_probe):
            data["scope_probe"] = {
                "reachable": False,
                "code": scope_probe.get("code"),
            }
        else:
            include_rules = scope_probe.get("include_rules", [])
            exclude_rules = scope_probe.get("exclude_rules", [])
            data["scope_probe"] = {
                "reachable": True,
                "include_rules_count": (
                    len(include_rules) if isinstance(include_rules, list) else 0
                ),
                "exclude_rules_count": (
                    len(exclude_rules) if isinstance(exclude_rules, list) else 0
                ),
                "mode": scope_probe.get("mode", "unknown"),
            }
    return _ok(
        "burp_status",
        f"Burp extension {data['status']} at {data['base_url']}",
        data,
    )


@MCP.tool()
@audited_tool("burp_health_check", "passive", lambda *a, **k: "sync")
def burp_health_check(run_id: str = "") -> dict[str, Any]:
    """Return extension health as data; an outage is a non-fatal tool result."""
    payload = _request("GET", "/api/health")
    if _is_error_envelope(payload):
        return _error("burp_health_check", payload)

    healthy = payload.get("status") == "ok"
    return _ok(
        "burp_health_check",
        "Burp extension is healthy"
        if healthy
        else "Burp extension responded but is unhealthy",
        {
            "healthy": bool(healthy),
            "status": payload.get("status"),
            "base_url": _burp_base_url(),
        },
    )


@MCP.tool()
@audited_tool("burp_check_scope", "passive", lambda *a, **k: "sync")
def burp_check_scope(url: str, run_id: str = "") -> dict[str, Any]:
    """Ask the extension whether a URL is in its operator-owned scope."""
    if not isinstance(url, str) or not url.strip():
        return _error(
            "burp_check_scope",
            {
                "error": "url is required",
                "code": "bad_args",
                "hint": "Pass a fully-qualified URL string.",
            },
        )

    payload = _request(
        "POST",
        "/api/scope/check",
        json_body={"url": url},
    )
    if _is_error_envelope(payload):
        return _error("burp_check_scope", payload)

    in_scope = bool(payload.get("in_scope", False))
    return _ok(
        "burp_check_scope",
        f"URL is {'in' if in_scope else 'out of'} scope",
        {
            "in_scope": in_scope,
            "url": url,
            "mode": payload.get("mode", "operator"),
        },
    )


@MCP.tool()
@audited_tool("burp_get_proxy_count", "passive", lambda *a, **k: "sync")
def burp_get_proxy_count(host: str = "", run_id: str = "") -> dict[str, Any]:
    """Read the Proxy history size, optionally filtered by host."""
    params = {"host": host} if host else None
    payload = _request("GET", "/api/proxy/count", params=params)
    if _is_error_envelope(payload):
        return _error("burp_get_proxy_count", payload)

    count = payload.get("count", payload.get("total", 0))
    return _ok(
        "burp_get_proxy_count",
        f"Proxy history has {count} entries"
        + (f" for host={host}" if host else ""),
        {
            "count": count,
            "host_filter": host or None,
            "base_url": _burp_base_url(),
        },
    )


@MCP.tool()
@audited_tool("burp_invoke", "active", lambda *a, **k: "sync")
def burp_invoke(
    endpoint: str,
    method: str = "GET",
    json_body: dict[str, Any] | None = None,
    run_id: str = "",
) -> dict[str, Any]:
    """Dispatch to a Valravn extension endpoint without propagating failures."""
    if not isinstance(endpoint, str) or not endpoint.strip():
        return _error(
            "burp_invoke",
            {
                "error": "endpoint is required",
                "code": "bad_args",
                "hint": "Pass an API path such as /api/scanner/scan.",
            },
        )

    path = endpoint.strip()
    if path.startswith(("http://", "https://")):
        parsed = urlparse(path)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
    if not path.startswith("/"):
        path = f"/{path}"

    verb = str(method or "GET").upper()
    payload = _request(verb, path, json_body=json_body)
    if _is_error_envelope(payload):
        return _error("burp_invoke", payload)

    return _ok(
        "burp_invoke",
        f"{verb} {path} returned",
        payload,
    )
