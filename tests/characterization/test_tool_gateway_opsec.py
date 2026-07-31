"""Characterization tests: OPSEC-relevant metadata survives gateway wrapping."""
import pytest

pytest.importorskip("munin.core.tool_gateway")
pytest.importorskip("munin.mcp.opsec")

from munin.core.tool_gateway import to_structured_tool


def test_wrapped_tool_description_preserved():
    """Tool description (used for OPSEC analysis) is preserved after wrapping."""
    def dangerous_tool(cmd: str) -> str:
        """Execute arbitrary shell commands on remote host"""
        return cmd

    tool = to_structured_tool("dangerous_tool", dangerous_tool)
    assert "arbitrary shell" in tool.description


def test_gen_prefix_tools_wrapped():
    """gen__ prefix tools are wrapped like any other tool."""
    def custom_exploit() -> str:
        return "ok"

    tool = to_structured_tool("gen__custom_exploit", custom_exploit)
    assert tool.name == "gen__custom_exploit"
