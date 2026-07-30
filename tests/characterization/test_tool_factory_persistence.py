"""Tool factory persistence: tools survive across factory instances."""
import pytest
pytest.importorskip("munin.core.autonomy.tool_factory")

from unittest.mock import MagicMock, call
from munin.core.autonomy.tool_factory import ToolFactory


def test_provenance_recorded_on_create():
    registry = MagicMock()
    registry.rehydrate.return_value = []
    factory = ToolFactory(registry=registry, store=MagicMock(), run_id="run-1", agent_id="test-agent")

    factory.create_tool("run nmap on target", name="nmap_scan", source="operator-request", deps=["nmap"])

    registry.record_provenance.assert_called_once()
    call_kwargs = registry.record_provenance.call_args
    assert call_kwargs.kwargs["creator_agent"] == "test-agent"
    assert call_kwargs.kwargs["parent_run"] == "run-1"
