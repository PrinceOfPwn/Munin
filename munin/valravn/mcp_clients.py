"""Small MCP transport clients used by the Valravn mesh.

The runtime deliberately keeps these clients narrow: discovery, tool calls and
resource reads. We do not mirror remote tool schemas into Munin's own MCP
surface; Valravn exposes a compact gateway instead so the model is not flooded
with hundreds of upstream tools at once.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Any

import httpx

_PROTOCOL_VERSION = "2025-03-26"
_CLIENT_INFO = {"name": "munin-valravn", "version": "1"}


class McpTransportError(RuntimeError):
    """Transport/protocol error with a stable human-readable message."""


@dataclass(frozen=True)
class McpCallResult:
    result: dict[str, Any]
    transport: str


def _json_payload_from_response(response: httpx.Response, request_id: int) -> dict[str, Any]:
    text = response.text or ""
    content_type = response.headers.get("content-type", "").lower()
    candidates: list[Any] = []

    if "text/event-stream" in content_type or text.lstrip().startswith("event:") or "\ndata:" in text:
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw or raw == "[DONE]":
                continue
            try:
                candidates.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    else:
        try:
            candidates.append(response.json())
        except ValueError as exc:
            raise McpTransportError(f"MCP server returned non-JSON HTTP {response.status_code}") from exc

    for payload in candidates:
        if isinstance(payload, dict) and payload.get("id") == request_id:
            if payload.get("error"):
                raise McpTransportError(f"MCP error: {payload['error']}")
            result = payload.get("result")
            return result if isinstance(result, dict) else {"value": result}
    raise McpTransportError(f"MCP response did not include request id {request_id}")


def streamable_http_call(
    url: str,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    token: str = "",
    timeout: float = 15.0,
) -> McpCallResult:
    """Perform one MCP request using Streamable HTTP with a fresh session."""
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            init_id = 1
            init = client.post(
                url,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": init_id,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": _PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": _CLIENT_INFO,
                    },
                },
            )
            init.raise_for_status()
            _json_payload_from_response(init, init_id)
            session_id = init.headers.get("mcp-session-id", "")
            session_headers = dict(headers)
            if session_id:
                session_headers["Mcp-Session-Id"] = session_id

            ready = client.post(
                url,
                headers=session_headers,
                json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            )
            if ready.status_code >= 400:
                ready.raise_for_status()

            request_id = 2
            response = client.post(
                url,
                headers=session_headers,
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params or {},
                },
            )
            response.raise_for_status()
            result = _json_payload_from_response(response, request_id)
            return McpCallResult(result=result, transport="streamable_http")
    except (httpx.HTTPError, McpTransportError):
        raise
    except Exception as exc:  # pragma: no cover - defensive boundary
        raise McpTransportError(f"streamable MCP call failed: {type(exc).__name__}: {exc}") from exc


def _parse_stdio_results(stdout: str) -> dict[int, dict[str, Any]]:
    found: dict[int, dict[str, Any]] = {}
    for line in stdout.splitlines():
        raw = line.strip()
        if not raw.startswith("{"):
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("id"), int):
            continue
        found[payload["id"]] = payload
    return found


def stdio_call(
    command: list[str],
    method: str,
    params: dict[str, Any] | None = None,
    *,
    env: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> McpCallResult:
    """Spawn a stdio MCP server for one request and close it after the response."""
    if not command:
        raise McpTransportError("empty stdio MCP command")
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": _CLIENT_INFO,
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": method, "params": params or {}},
    ]
    payload = "".join(json.dumps(item, separators=(",", ":")) + "\n" for item in messages)
    proc_env = dict(os.environ)
    if env:
        proc_env.update({str(k): str(v) for k, v in env.items()})

    try:
        process = subprocess.run(
            command,
            input=payload,
            capture_output=True,
            text=True,
            env=proc_env,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise McpTransportError(f"stdio MCP timed out after {timeout}s") from exc
    except OSError as exc:
        raise McpTransportError(f"cannot start stdio MCP: {exc}") from exc

    responses = _parse_stdio_results(process.stdout)
    response = responses.get(2)
    if response is None:
        stderr = (process.stderr or "").strip()[-800:]
        raise McpTransportError(
            f"stdio MCP returned no response for request 2 (exit={process.returncode}, stderr={stderr!r})"
        )
    if response.get("error"):
        raise McpTransportError(f"MCP error: {response['error']}")
    result = response.get("result")
    return McpCallResult(
        result=result if isinstance(result, dict) else {"value": result},
        transport="stdio",
    )


def tool_records(result: dict[str, Any]) -> list[dict[str, Any]]:
    tools = result.get("tools")
    if not isinstance(tools, list):
        return []
    return [item for item in tools if isinstance(item, dict) and isinstance(item.get("name"), str)]


def compact_tool(tool: dict[str, Any], *, include_schema: bool = False) -> dict[str, Any]:
    schema = tool.get("inputSchema") or tool.get("input_schema") or {}
    required = schema.get("required", []) if isinstance(schema, dict) else []
    record: dict[str, Any] = {
        "name": tool.get("name", ""),
        "description": str(tool.get("description") or "")[:320],
        "required": required if isinstance(required, list) else [],
    }
    if include_schema:
        record["input_schema"] = schema
    return record


def decode_tool_content(result: dict[str, Any]) -> Any:
    """Turn common MCP text-content envelopes into JSON when possible."""
    content = result.get("content")
    if not isinstance(content, list):
        return result
    texts = [item.get("text") for item in content if isinstance(item, dict) and item.get("type") == "text"]
    texts = [text for text in texts if isinstance(text, str)]
    if len(texts) == 1:
        try:
            return json.loads(texts[0])
        except json.JSONDecodeError:
            return texts[0]
    return {"content": content, "is_error": bool(result.get("isError"))}
