"""Valravn Talons: a compact, provider-agnostic Burp MCP mesh."""
from __future__ import annotations

import json
import os
import shlex
import time
from dataclasses import dataclass
from typing import Any

from .mcp_clients import (
    McpTransportError,
    compact_tool,
    decode_tool_content,
    stdio_call,
    streamable_http_call,
    tool_records,
)

_CACHE_TTL = 8.0


@dataclass(frozen=True)
class TalonProvider:
    name: str
    upstream: str
    transport: str
    endpoint: str = ""
    command: tuple[str, ...] = ()
    token_env: str = ""
    priority: int = 100

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "upstream": self.upstream,
            "transport": self.transport,
            "endpoint": self.endpoint or None,
            "configured": bool(self.endpoint or self.command),
            "priority": self.priority,
        }


def _official_command() -> tuple[str, ...]:
    raw_json = os.environ.get("VALRAVN_TALON_OFFICIAL_STDIO_JSON", "").strip()
    if raw_json:
        try:
            value = json.loads(raw_json)
            if isinstance(value, list) and all(isinstance(item, str) and item for item in value):
                return tuple(value)
        except json.JSONDecodeError:
            pass
    raw = os.environ.get("VALRAVN_TALON_OFFICIAL_STDIO", "").strip()
    return tuple(shlex.split(raw)) if raw else ()


def providers() -> tuple[TalonProvider, ...]:
    """Return provider order. Valravn aliases never hide upstream identity."""
    return (
        TalonProvider(
            name="valravn-ultimate",
            upstream="3ntr0pyX/burp-mcp-ultimate",
            transport="streamable_http",
            endpoint=os.environ.get("VALRAVN_TALON_ULTIMATE_URL", "http://127.0.0.1:9444/mcp"),
            token_env="BURP_MCP_TOKEN",
            priority=300,
        ),
        TalonProvider(
            name="valravn-awesome",
            upstream="vvvvvvvvvvel/burp-awesome-mcp",
            transport="streamable_http",
            endpoint=os.environ.get("VALRAVN_TALON_AWESOME_URL", "http://127.0.0.1:26001/mcp"),
            priority=200,
        ),
        TalonProvider(
            name="valravn-official",
            upstream="PortSwigger/mcp-server",
            transport="stdio",
            command=_official_command(),
            priority=100,
        ),
    )


_TOOL_CACHE: dict[str, tuple[float, list[dict[str, Any]], str]] = {}


def _call_provider(provider: TalonProvider, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    if provider.transport == "streamable_http":
        token = os.environ.get(provider.token_env, "") if provider.token_env else ""
        return streamable_http_call(provider.endpoint, method, params, token=token).result
    if provider.transport == "stdio":
        if not provider.command:
            raise McpTransportError(
                "official Burp MCP requires VALRAVN_TALON_OFFICIAL_STDIO(_JSON) pointing at its packaged stdio proxy"
            )
        return stdio_call(list(provider.command), method, params).result
    raise McpTransportError(f"unsupported talon transport: {provider.transport}")


def _provider_tools(provider: TalonProvider, *, refresh: bool = False) -> tuple[list[dict[str, Any]], str]:
    now = time.monotonic()
    cached = _TOOL_CACHE.get(provider.name)
    if not refresh and cached and now - cached[0] <= _CACHE_TTL:
        return cached[1], cached[2]
    try:
        tools = tool_records(_call_provider(provider, "tools/list"))
        _TOOL_CACHE[provider.name] = (now, tools, "")
        return tools, ""
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        _TOOL_CACHE[provider.name] = (now, [], message)
        return [], message


def status(*, refresh: bool = False) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for provider in providers():
        tools, error = _provider_tools(provider, refresh=refresh)
        row = provider.describe()
        row.update({"reachable": bool(tools), "tool_count": len(tools), "error": error or None})
        rows.append(row)
    preferred = next((row["name"] for row in rows if row["reachable"]), None)
    return {
        "mesh": "valravn-talons",
        "preferred": preferred,
        "providers": rows,
        "selection": "highest-priority reachable provider containing the requested tool",
    }


def list_tools(
    *,
    provider: str = "auto",
    query: str = "",
    limit: int = 50,
    include_schema: bool = False,
    refresh: bool = False,
) -> dict[str, Any]:
    wanted = query.casefold().strip()
    selected = [item for item in providers() if provider in {"", "auto", item.name}]
    if provider not in {"", "auto"} and not selected:
        raise ValueError(f"unknown Valravn talon provider: {provider}")

    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    max_items = max(1, min(int(limit), 200))
    for item in selected:
        tools, error = _provider_tools(item, refresh=refresh)
        if error:
            errors.append({"provider": item.name, "error": error})
            continue
        for tool in tools:
            name = str(tool.get("name", ""))
            description = str(tool.get("description", ""))
            if wanted and wanted not in name.casefold() and wanted not in description.casefold():
                continue
            key = (item.name, name)
            if key in seen:
                continue
            seen.add(key)
            record = compact_tool(tool, include_schema=include_schema)
            record.update({"provider": item.name, "upstream": item.upstream})
            records.append(record)
            if len(records) >= max_items:
                break
        if len(records) >= max_items:
            break
    return {"mesh": "valravn-talons", "tools": records, "count": len(records), "errors": errors}


def _resolve(tool_name: str, provider_name: str = "auto") -> tuple[TalonProvider, dict[str, Any]]:
    candidates = [item for item in providers() if provider_name in {"", "auto", item.name}]
    if provider_name not in {"", "auto"} and not candidates:
        raise ValueError(f"unknown Valravn talon provider: {provider_name}")
    for provider in candidates:
        tools, _ = _provider_tools(provider)
        for tool in tools:
            if tool.get("name") == tool_name:
                return provider, tool
    raise McpTransportError(f"tool {tool_name!r} not found on any reachable Valravn talon")


def call_tool(tool_name: str, arguments: dict[str, Any] | None = None, *, provider: str = "auto") -> dict[str, Any]:
    selected, _ = _resolve(tool_name, provider)
    result = _call_provider(
        selected,
        "tools/call",
        {"name": tool_name, "arguments": arguments or {}},
    )
    return {
        "mesh": "valravn-talons",
        "provider": selected.name,
        "upstream": selected.upstream,
        "tool": tool_name,
        "result": decode_tool_content(result),
        "raw_is_error": bool(result.get("isError")),
    }


def read_resource(uri: str, *, provider: str = "auto") -> dict[str, Any]:
    candidates = [item for item in providers() if provider in {"", "auto", item.name}]
    errors: list[str] = []
    for item in candidates:
        tools, error = _provider_tools(item)
        if not tools:
            if error:
                errors.append(f"{item.name}: {error}")
            continue
        try:
            result = _call_provider(item, "resources/read", {"uri": uri})
            return {
                "mesh": "valravn-talons",
                "provider": item.name,
                "upstream": item.upstream,
                "uri": uri,
                "result": result,
            }
        except Exception as exc:
            errors.append(f"{item.name}: {type(exc).__name__}: {exc}")
    raise McpTransportError("resource could not be read: " + "; ".join(errors))
