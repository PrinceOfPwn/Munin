"""Characterization tests: Tool Gateway converts catalog callables faithfully."""
import pytest

pytest.importorskip("munin.core.tool_gateway")

from munin.core.tool_gateway import gateway_tools, to_structured_tool, wrap_mcp_tool


def test_wrap_sync_tool_invocable():
    def echo(text: str) -> str:
        return f"echo: {text}"

    tool = to_structured_tool("echo", echo)
    assert tool.invoke({"text": "hello"}) == "echo: hello"


@pytest.mark.asyncio
async def test_wrap_async_tool_invocable():
    async def async_scan(host: str) -> str:
        return f"scanned {host}"

    tool = to_structured_tool("async_scan", async_scan)
    result = await tool.ainvoke({"host": "10.0.0.1"})
    assert "10.0.0.1" in result


def test_run_id_hidden_from_schema():
    def scan(target: str, run_id: str = "") -> str:
        return f"{target} ({run_id})"

    tool = to_structured_tool("scan", scan)
    schema = tool.args
    assert "target" in schema
    assert "run_id" not in schema
    # and the wrapped callable still works with the hidden param defaulted
    assert tool.invoke({"target": "host"}) == "host ()"


def test_tool_name_preserved():
    tool = wrap_mcp_tool("my_tool", "desc", {}, lambda: None)
    assert tool.name == "my_tool"


def test_gateway_tools_include_state_bound_catalog(store):
    tools = gateway_tools(store, include_generated=False)
    names = {t.name for t in tools}
    # state-bound domain tools from the legacy catalog must be present
    assert "memory_remember" in names
    assert "post_agent_message" in names
    # and they must be invocable StructuredTools
    remember = next(t for t in tools if t.name == "memory_remember")
    assert remember.description


def test_gateway_tools_include_generated(store):
    from munin.core.autonomy.tool_factory import ToolFactory

    factory = ToolFactory(store, run_id="run-gw")
    outcome = factory.create_tool(
        name="gw_tool",
        source="def gw_tool() -> str:\n    return 'gw'\n",
    )
    assert outcome["ok"], outcome

    names = {t.name for t in gateway_tools(store)}
    assert "gen__gw_tool" in names
