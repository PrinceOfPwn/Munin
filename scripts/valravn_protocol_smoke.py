#!/usr/bin/env python3
"""Exercise Munin's stdio MCP transport and assert the Valravn catalog is live."""
from __future__ import annotations

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED = {
    "valravn_status",
    "valravn_investigate_ioc",
    "valravn_investigate_organization",
    "valravn_search_assets",
    "valravn_investigate_cve",
    "valravn_investigate_network",
    "valravn_search_historical_web",
    "valravn_investigate_url",
    "valravn_validate_asset",
    "valravn_search_darkweb",
    "valravn_capture_web_evidence",
    "valravn_translate",
}


async def _run() -> None:
    env = dict(os.environ)
    env.setdefault("VALRAVN_RESOLVE_PUBLIC_HOSTS", "false")
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
            missing = EXPECTED - names
            assert not missing, f"missing Valravn tools: {sorted(missing)}"
            result = await session.call_tool("valravn_status", {"probe": False})
            assert not result.isError


def main() -> int:
    asyncio.run(_run())
    print("Valravn MCP protocol smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
