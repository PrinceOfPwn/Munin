"""Tool factory: provenance columns populated via inspect_registered_tool."""
import pytest
pytest.importorskip("munin.core.autonomy.tool_factory")

from unittest.mock import MagicMock
from munin.core.autonomy.tool_factory import ToolFactory


def test_inspect_returns_tool_metadata():
    registry = MagicMock()
    registry.rehydrate.return_value = [
        {"name": "gen__my_tool", "description": "test", "active": 1}
    ]
    factory = ToolFactory(registry=registry, store=MagicMock(), run_id="run-1")

    info = factory.inspect_registered_tool("gen__my_tool")
    assert info["name"] == "gen__my_tool"


def test_inspect_raises_key_error_for_unknown():
    registry = MagicMock()
    registry.rehydrate.return_value = []
    factory = ToolFactory(registry=registry, store=MagicMock(), run_id="run-1")

    with pytest.raises(KeyError):
        factory.inspect_registered_tool("gen__nonexistent")
