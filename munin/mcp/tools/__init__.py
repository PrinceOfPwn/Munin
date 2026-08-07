"""Munin MCP tool submodules — LDAP, Tavily, Hugin, forge, and Munin helpers."""

from __future__ import annotations

import sys

# ``munin.mcp.main`` imports this package only after the shared FastMCP singleton
# exists. Register the compact Valravn mesh surface at that point. Direct imports
# of individual tool modules during tests skip this eager registration and can
# import ``valravn_mesh_tool`` explicitly without creating a second MCP instance.
_main = sys.modules.get("munin.mcp.main")
if _main is not None and hasattr(_main, "MCP"):
    from . import valravn_mesh_tool as _valravn_mesh_tool  # noqa: F401,E402
