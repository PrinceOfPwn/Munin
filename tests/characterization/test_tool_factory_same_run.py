"""E2E: create_tool → invoke_registered_tool in same factory instance."""
import pytest
pytest.importorskip("munin.core.autonomy.tool_factory")

from unittest.mock import MagicMock
from munin.core.autonomy.tool_factory import ToolFactory


def make_factory():
    registry = MagicMock()
    registry.rehydrate.return_value = []
    registry.get_handler.return_value = None
    store = MagicMock()
    return ToolFactory(registry=registry, store=store, run_id="run-test")


def test_create_and_invoke_same_run():
    factory = make_factory()
    name = factory.create_tool("echo the input back", name="echo_back")
    assert name == "gen__echo_back"

    result = factory.invoke_registered_tool("gen__echo_back", {"input": "hello"})
    assert "hello" in str(result)


def test_gen_prefix_auto_applied():
    factory = make_factory()
    name = factory.create_tool("scan ports", name="port_scanner")
    assert name.startswith("gen__")


def test_create_tool_registers_in_registry():
    factory = make_factory()
    factory.create_tool("test tool", name="test_t")
    factory._registry.register.assert_called_once()
