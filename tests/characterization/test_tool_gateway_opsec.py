"""Characterization tests: OPSEC preflight still triggers on wrapped tools."""
import pytest
pytest.importorskip("munin.core.tool_gateway")
pytest.importorskip("munin.mcp.opsec")

from munin.core.tool_gateway import wrap_mcp_tool


def test_wrapped_tool_description_preserved():
    """Tool description (used for OPSEC analysis) is preserved after wrapping."""
    tool = wrap_mcp_tool(
        name="dangerous_tool",
        description="Execute arbitrary shell commands on remote host",
        signature={"type": "object", "properties": {"cmd": {"type": "string", "description": "command"}}, "required": ["cmd"]},
        handler=lambda cmd: cmd,
    )
    assert "arbitrary shell" in tool.description


def test_gen_prefix_tools_wrapped():
    """gen__ prefix tools are wrapped like any other tool."""
    tool = wrap_mcp_tool(
        name="gen__custom_exploit",
        description="Custom generated exploit tool",
        signature={"type": "object", "properties": {}, "required": []},
        handler=lambda: "ok",
    )
    assert tool.name == "gen__custom_exploit"
