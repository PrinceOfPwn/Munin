# tags: [valravn, mcp-tool, burp, arsenal, security-hub, mcp-gateway, talons]
"""Compact Munin MCP surface for Valravn Talons and Arsenal."""
from __future__ import annotations

import logging
from typing import Any

from ...valravn import arsenal, talons  # noqa: TID252
from ..main import MCP, audited_tool  # noqa: TID252

logger = logging.getLogger("munin-mcp.valravn-mesh")


def _ok(tool: str, summary: str, data: Any) -> dict[str, Any]:
    return {"ok": True, "tool": tool, "mode": "sync", "summary": summary, "data": data}


def _fail(tool: str, exc: Exception, *, code: str = "valravn_gateway_failed") -> dict[str, Any]:
    # Remote MCP exceptions may contain command lines, local URLs, provider
    # diagnostics, or credentials. Public tool output exposes only a stable
    # failure envelope; logs retain the exception class but not raw text.
    logger.warning("%s failed with %s", tool, type(exc).__name__)
    return {
        "ok": False,
        "tool": tool,
        "mode": "sync",
        "summary": f"{tool} failed locally",
        "degraded": True,
        "failure_scope": "valravn_capability_only",
        "run_should_continue": True,
        "error": {
            "code": code,
            "message": "The Valravn capability failed locally; inspect redacted operator logs for diagnostics.",
        },
    }


def _authorization_required(tool: str) -> dict[str, Any]:
    return {
        "ok": False,
        "tool": tool,
        "mode": "sync",
        "summary": "explicit authorized=true is required for generic active dispatch",
        "error": {
            "code": "authorization_required",
            "message": "Generic Valravn execution gateways can invoke active security tools.",
            "hint": "Use only on an explicitly authorized target and pass authorized=true.",
        },
    }


@MCP.tool()
@audited_tool("valravn_talons_status", "passive", lambda *a, **k: "sync")
def valravn_talons_status(refresh: bool = False, run_id: str = "") -> dict[str, Any]:
    """Discover Burp MCP providers (Ultimate, Awesome, official proxy) and their health."""
    try:
        data = talons.status(refresh=refresh)
        return _ok("valravn_talons_status", f"Valravn Talons preferred={data.get('preferred')}", data)
    except Exception as exc:
        return _fail("valravn_talons_status", exc)


@MCP.tool()
@audited_tool("valravn_talons_tools", "passive", lambda *a, **k: "sync")
def valravn_talons_tools(
    provider: str = "auto",
    query: str = "",
    limit: int = 50,
    include_schema: bool = False,
    refresh: bool = False,
    run_id: str = "",
) -> dict[str, Any]:
    """List a compact Burp tool catalog; request schemas only for tools you intend to use."""
    try:
        data = talons.list_tools(
            provider=provider,
            query=query,
            limit=limit,
            include_schema=include_schema,
            refresh=refresh,
        )
        return _ok("valravn_talons_tools", f"Valravn Talons returned {data['count']} tools", data)
    except Exception as exc:
        return _fail("valravn_talons_tools", exc)


@MCP.tool()
@audited_tool("valravn_talons_read", "passive", lambda *a, **k: "sync")
def valravn_talons_read(uri: str, provider: str = "auto", run_id: str = "") -> dict[str, Any]:
    """Read an MCP resource such as burp://proxy/history without exposing every remote tool."""
    try:
        data = talons.read_resource(uri, provider=provider)
        return _ok("valravn_talons_read", f"read {uri} via {data['provider']}", data)
    except Exception as exc:
        return _fail("valravn_talons_read", exc)


@MCP.tool()
@audited_tool("valravn_talons_call", "active", lambda *a, **k: "sync")
def valravn_talons_call(
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    provider: str = "auto",
    authorized: bool = False,
    run_id: str = "",
) -> dict[str, Any]:
    """Invoke one discovered Burp MCP tool through the preferred Valravn Talon."""
    if not authorized:
        return _authorization_required("valravn_talons_call")
    try:
        data = talons.call_tool(tool_name, arguments, provider=provider)
        return _ok("valravn_talons_call", f"{tool_name} via {data['provider']}", data)
    except Exception as exc:
        return _fail("valravn_talons_call", exc)


@MCP.tool()
@audited_tool("valravn_arsenal_status", "passive", lambda *a, **k: "sync")
def valravn_arsenal_status(run_id: str = "") -> dict[str, Any]:
    """Inspect the local Valravn Arsenal installation derived from mcp-security-hub."""
    try:
        data = arsenal.status()
        return _ok("valravn_arsenal_status", f"Valravn Arsenal servers={data['server_count']}", data)
    except Exception as exc:
        return _fail("valravn_arsenal_status", exc)


@MCP.tool()
@audited_tool("valravn_arsenal_list", "passive", lambda *a, **k: "sync")
def valravn_arsenal_list(
    category: str = "",
    available_only: bool = False,
    run_id: str = "",
) -> dict[str, Any]:
    """List Valravn-namespaced Security Hub servers without loading their tool schemas."""
    try:
        data = arsenal.list_servers(category=category, available_only=available_only)
        return _ok("valravn_arsenal_list", f"Valravn Arsenal returned {data['count']} servers", data)
    except Exception as exc:
        return _fail("valravn_arsenal_list", exc)


@MCP.tool()
@audited_tool("valravn_arsenal_tools", "passive", lambda *a, **k: "sync")
def valravn_arsenal_tools(
    server: str,
    query: str = "",
    limit: int = 80,
    include_schema: bool = False,
    run_id: str = "",
) -> dict[str, Any]:
    """Discover tools from one Arsenal server on demand (list -> select -> call)."""
    try:
        data = arsenal.list_tools(server, query=query, limit=limit, include_schema=include_schema)
        return _ok("valravn_arsenal_tools", f"{server}: {data['count']} tools", data)
    except Exception as exc:
        return _fail("valravn_arsenal_tools", exc)


@MCP.tool()
@audited_tool("valravn_arsenal_call", "active", lambda *a, **k: "sync")
def valravn_arsenal_call(
    server: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    authorized: bool = False,
    run_id: str = "",
) -> dict[str, Any]:
    """Invoke one tool on one Arsenal server; active dispatch requires explicit authorization."""
    if not authorized:
        return _authorization_required("valravn_arsenal_call")
    try:
        data = arsenal.call_tool(server, tool_name, arguments)
        return _ok("valravn_arsenal_call", f"{server}/{tool_name}", data)
    except Exception as exc:
        return _fail("valravn_arsenal_call", exc)


VALRAVN_MESH_TOOLS = frozenset(
    {
        "valravn_talons_status",
        "valravn_talons_tools",
        "valravn_talons_read",
        "valravn_talons_call",
        "valravn_arsenal_status",
        "valravn_arsenal_list",
        "valravn_arsenal_tools",
        "valravn_arsenal_call",
    }
)
