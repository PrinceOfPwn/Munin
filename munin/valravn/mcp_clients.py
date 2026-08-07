"""Small MCP transport clients used by the Valravn mesh.

The runtime deliberately keeps these clients narrow: discovery, tool calls and
resource reads. We do not mirror remote tool schemas into Munin's own MCP
surface; Valravn exposes a compact gateway instead so the model is not flooded
with hundreds of upstream tools at once.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
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
    """Perform one MCP request using Streamable HTTP with a fresh session.

    The initialize response negotiates the protocol revision. Subsequent
    requests carry that revision plus any server-issued session ID, and a
    best-effort DELETE releases stateful sessions once the call completes.
    """
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            session_id = ""
            session_headers = dict(headers)
            try:
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
                init_result = _json_payload_from_response(init, init_id)
                negotiated = str(init_result.get("protocolVersion") or _PROTOCOL_VERSION)
                session_id = init.headers.get("mcp-session-id", "")
                session_headers["MCP-Protocol-Version"] = negotiated
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
            finally:
                if session_id:
                    try:
                        client.delete(url, headers=session_headers)
                    except httpx.HTTPError:
                        # Session deletion is optional server-side and must not
                        # overwrite the actual tool result/failure.
                        pass
    except (httpx.HTTPError, McpTransportError):
        raise
    except Exception as exc:  # pragma: no cover - defensive boundary
        raise McpTransportError(f"streamable MCP call failed: {type(exc).__name__}: {exc}") from exc


def _kill_process_tree(process: subprocess.Popen[str]) -> None:
    """Terminate the worker and any MCP/container descendants after timeout."""
    if process.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                check=False,
            )
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            process.kill()
        except OSError:
            pass


def stdio_call(
    command: list[str],
    method: str,
    params: dict[str, Any] | None = None,
    *,
    env: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> McpCallResult:
    """Call a stdio MCP through an isolated official-SDK lifecycle.

    The isolated worker prevents async event-loop ownership conflicts. It runs
    in its own process group so a timeout also kills descendant MCP processes
    such as ``docker compose run`` rather than leaving them orphaned.
    """
    if not command:
        raise McpTransportError("empty stdio MCP command")
    if not all(isinstance(item, str) and item for item in command):
        raise McpTransportError("stdio MCP command must contain non-empty strings")

    request = {
        "command": command,
        "method": method,
        "params": params or {},
        "env": env or {},
    }
    proc_env = dict(os.environ)
    popen_kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "munin.valravn.stdio_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=proc_env,
            **popen_kwargs,
        )
        try:
            stdout, stderr = process.communicate(
                input=json.dumps(request, ensure_ascii=False),
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            _kill_process_tree(process)
            stdout, stderr = process.communicate()
            raise McpTransportError(f"stdio MCP timed out after {timeout}s") from exc
    except OSError as exc:
        raise McpTransportError(f"cannot start stdio MCP worker: {exc}") from exc

    try:
        envelope = json.loads((stdout or "").strip())
    except json.JSONDecodeError as exc:
        tail = (stderr or "").strip()[-1200:]
        raise McpTransportError(
            f"stdio MCP worker returned invalid JSON (exit={process.returncode}, stderr={tail!r})"
        ) from exc
    if not isinstance(envelope, dict):
        raise McpTransportError("stdio MCP worker returned a non-object envelope")
    if not envelope.get("ok"):
        error = envelope.get("error") or {}
        message = error.get("message") if isinstance(error, dict) else str(error)
        tail = (stderr or "").strip()[-1200:]
        detail = f"; stderr={tail!r}" if tail else ""
        raise McpTransportError(f"stdio MCP failed: {message or 'unknown error'}{detail}")

    result = envelope.get("result")
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
    return {"content": content, "is_error": bool(result.get("isError") or result.get("is_error"))}
