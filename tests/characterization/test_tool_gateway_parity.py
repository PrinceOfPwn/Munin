"""Characterization tests: Tool Gateway wraps tools identically to direct dispatch."""
import pytest
pytest.importorskip("munin.core.tool_gateway")

from munin.core.tool_gateway import wrap_mcp_tool, _signature_to_pydantic


def test_wrap_sync_tool_invocable():
    def echo(text: str) -> str:
        return f"echo: {text}"

    tool = wrap_mcp_tool(
        name="echo",
        description="Echo input",
        signature={"type": "object", "properties": {"text": {"type": "string", "description": "text"}}, "required": ["text"]},
        handler=echo,
    )

    result = tool.invoke({"text": "hello"})
    assert result == "echo: hello"


@pytest.mark.asyncio
async def test_wrap_async_tool_invocable():
    async def async_scan(host: str) -> str:
        return f"scanned {host}"

    tool = wrap_mcp_tool(
        name="async_scan",
        description="Scan a host",
        signature={"type": "object", "properties": {"host": {"type": "string", "description": "host"}}, "required": ["host"]},
        handler=async_scan,
    )

    result = await tool.ainvoke({"host": "10.0.0.1"})
    assert "10.0.0.1" in result


def test_signature_to_pydantic_required_fields():
    sig = {
        "type": "object",
        "properties": {
            "required_param": {"type": "string", "description": "required"},
            "optional_param": {"type": "integer", "description": "optional"},
        },
        "required": ["required_param"],
    }
    Model = _signature_to_pydantic("test_tool", sig)

    # Required field present
    instance = Model(required_param="value")
    assert instance.required_param == "value"
    assert instance.optional_param is None


def test_tool_name_preserved():
    tool = wrap_mcp_tool("my_tool", "desc", {"type": "object", "properties": {}, "required": []}, lambda: None)
    assert tool.name == "my_tool"
