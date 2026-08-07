#!/usr/bin/env python3
"""End-to-end protocol smoke for the Valravn Talons + Arsenal mesh."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


def _decode(result):
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if not isinstance(text, str):
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    raise AssertionError(f"tool returned no decodable content: {result!r}")


async def _run(nuclei_server: Path | None) -> None:
    mock_http = subprocess.Popen(
        [sys.executable, str(ROOT / "tests" / "fixtures" / "mock_streamable_mcp.py"), "--port", "19444"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    temp_root = Path(tempfile.mkdtemp(prefix="valravn-mesh-e2e-"))
    try:
        time.sleep(0.25)
        env = dict(os.environ)
        env["VALRAVN_TALON_ULTIMATE_URL"] = "http://127.0.0.1:19444/mcp"
        env["VALRAVN_TALON_AWESOME_URL"] = "http://127.0.0.1:19445/mcp"
        env.pop("VALRAVN_TALON_OFFICIAL_STDIO", None)
        env.pop("VALRAVN_TALON_OFFICIAL_STDIO_JSON", None)

        arsenal_command = [
            sys.executable,
            str(nuclei_server or (ROOT / "tests" / "fixtures" / "mock_stdio_mcp.py")),
        ]
        env["VALRAVN_ARSENAL_NUCLEI_MCP_COMMAND_JSON"] = json.dumps(arsenal_command)

        # The real Security Hub Nuclei server is container-oriented and defaults
        # to /app/output. For a direct stdio E2E run, inject writable paths while
        # keeping the exact upstream server implementation unchanged.
        if nuclei_server is not None:
            output_dir = temp_root / "nuclei-output"
            templates_dir = temp_root / "nuclei-templates"
            output_dir.mkdir(parents=True, exist_ok=True)
            templates_dir.mkdir(parents=True, exist_ok=True)
            env["NUCLEI_OUTPUT_DIR"] = str(output_dir)
            env["NUCLEI_TEMPLATES_PATH"] = str(templates_dir)

        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "munin.mcp.main", "--transport", "stdio"],
            env=env,
        )
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                required = {
                    "valravn_talons_status",
                    "valravn_talons_tools",
                    "valravn_talons_read",
                    "valravn_talons_call",
                    "valravn_arsenal_status",
                    "valravn_arsenal_list",
                    "valravn_arsenal_tools",
                    "valravn_arsenal_call",
                }
                assert required <= names, f"missing mesh tools: {sorted(required - names)}"

                status = _decode(await session.call_tool("valravn_talons_status", {"refresh": True}))
                assert status["ok"] is True
                assert status["data"]["preferred"] == "valravn-ultimate"

                listed = _decode(
                    await session.call_tool(
                        "valravn_talons_tools",
                        {"provider": "auto", "query": "history", "include_schema": False},
                    )
                )
                assert listed["ok"] is True
                assert any(item["name"] == "list_proxy_http_history" for item in listed["data"]["tools"])
                assert all("input_schema" not in item for item in listed["data"]["tools"])

                denied = _decode(
                    await session.call_tool(
                        "valravn_talons_call",
                        {"tool_name": "http_send_raw", "arguments": {"request": "GET / HTTP/1.1"}},
                    )
                )
                assert denied["ok"] is False
                assert denied["error"]["code"] == "authorization_required"

                called = _decode(
                    await session.call_tool(
                        "valravn_talons_call",
                        {
                            "tool_name": "http_send_raw",
                            "arguments": {"request": "GET / HTTP/1.1"},
                            "authorized": True,
                        },
                    )
                )
                assert called["ok"] is True
                assert called["data"]["provider"] == "valravn-ultimate"

                resource = _decode(
                    await session.call_tool(
                        "valravn_talons_read",
                        {"uri": "burp://proxy/history", "provider": "valravn-ultimate"},
                    )
                )
                assert resource["ok"] is True

                arsenal_status = _decode(await session.call_tool("valravn_arsenal_status", {}))
                assert arsenal_status["ok"] is True
                assert arsenal_status["data"]["server_count"] == 38

                arsenal_list = _decode(await session.call_tool("valravn_arsenal_list", {"category": "web"}))
                assert arsenal_list["ok"] is True
                assert any(item["id"] == "web/nuclei" for item in arsenal_list["data"]["servers"])

                arsenal_tools = _decode(
                    await session.call_tool(
                        "valravn_arsenal_tools",
                        {"server": "web/nuclei", "query": "template"},
                    )
                )
                assert arsenal_tools["ok"] is True
                assert any(item["name"] == "list_templates" for item in arsenal_tools["data"]["tools"])

                arsenal_call = _decode(
                    await session.call_tool(
                        "valravn_arsenal_call",
                        {
                            "server": "web/nuclei",
                            "tool_name": "list_templates",
                            "arguments": {},
                            "authorized": True,
                        },
                    )
                )
                assert arsenal_call["ok"] is True
    finally:
        mock_http.terminate()
        mock_http.wait(timeout=5)
        shutil.rmtree(temp_root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--security-hub-nuclei", type=Path, default=None)
    args = parser.parse_args()
    asyncio.run(asyncio.wait_for(_run(args.security_hub_nuclei), timeout=180))
    print("Valravn mesh E2E passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
