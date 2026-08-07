"""Isolated official-SDK stdio MCP client used by Valravn gateways.

This process exists so synchronous Munin tools can use the MCP SDK's async
stdio lifecycle without depending on whether their caller already owns an
event loop. One JSON request is read from stdin and one JSON result is written
to stdout; the target MCP server is started and shut down by the SDK.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_PASSTHROUGH_ENV = (
    "PATH",
    "HOME",
    "USERPROFILE",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TMP",
    "TEMP",
    "SystemRoot",
    "PATHEXT",
    "JAVA_HOME",
    "VIRTUAL_ENV",
    "PYTHONPATH",
)


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, exclude_none=True)
    if isinstance(value, dict):
        return {str(key): _dump(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_dump(item) for item in value]
    return value


async def _run(request: dict[str, Any]) -> dict[str, Any]:
    command = request.get("command") or []
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        raise ValueError("command must be a non-empty string array")

    method = str(request.get("method") or "")
    params = request.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError("params must be an object")

    # Third-party MCPs receive only process/bootstrap essentials by default.
    # Provider credentials must be forwarded explicitly by the selected
    # Valravn gateway through request["env"].
    child_env = {key: os.environ[key] for key in _PASSTHROUGH_ENV if key in os.environ}
    extra_env = request.get("env") or {}
    if isinstance(extra_env, dict):
        child_env.update({str(key): str(value) for key, value in extra_env.items()})

    server = StdioServerParameters(
        command=command[0],
        args=command[1:],
        env=child_env,
    )
    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            if method == "tools/list":
                result = await session.list_tools()
            elif method == "tools/call":
                name = str(params.get("name") or "")
                if not name:
                    raise ValueError("tools/call requires params.name")
                arguments = params.get("arguments") or {}
                if not isinstance(arguments, dict):
                    raise ValueError("tools/call params.arguments must be an object")
                result = await session.call_tool(name, arguments)
            elif method == "resources/list":
                result = await session.list_resources()
            elif method == "resources/read":
                uri = str(params.get("uri") or "")
                if not uri:
                    raise ValueError("resources/read requires params.uri")
                result = await session.read_resource(uri)
            elif method == "prompts/list":
                result = await session.list_prompts()
            else:
                raise ValueError(f"unsupported stdio MCP method: {method}")
    dumped = _dump(result)
    return dumped if isinstance(dumped, dict) else {"value": dumped}


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
        if not isinstance(request, dict):
            raise ValueError("request must be a JSON object")
        result = asyncio.run(_run(request))
        sys.stdout.write(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
        return 0
    except Exception as exc:
        sys.stdout.write(
            json.dumps(
                {
                    "ok": False,
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                },
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
