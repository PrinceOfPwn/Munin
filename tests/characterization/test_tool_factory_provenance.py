"""Tool factory: inspect/list surface over the real procedural registry."""
import pytest

pytest.importorskip("munin.core.autonomy.tool_factory")

from munin.core.autonomy.tool_factory import ToolFactory

SOURCE = '''
def my_tool() -> str:
    """Demo."""
    return "ok"
'''


def test_inspect_returns_tool_metadata(store):
    factory = ToolFactory(store, run_id="run-1")
    factory.create_tool(name="my_tool", source=SOURCE, description="demo tool")

    info = factory.inspect_registered_tool("gen__my_tool")
    assert info["name"] == "gen__my_tool"
    assert info["description"] == "demo tool"
    assert "source_code" not in info  # source withheld unless requested


def test_inspect_raises_key_error_for_unknown(store):
    factory = ToolFactory(store, run_id="run-1")
    with pytest.raises(KeyError):
        factory.inspect_registered_tool("gen__nonexistent")


def test_list_registered_tools(store):
    factory = ToolFactory(store, run_id="run-1")
    factory.create_tool(name="listed_tool", source=SOURCE.replace("my_tool", "listed_tool"))
    names = [t["name"] for t in factory.list_registered_tools()]
    assert "gen__listed_tool" in names
