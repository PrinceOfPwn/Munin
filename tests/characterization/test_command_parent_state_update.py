"""LangGraph Command exists; unsupported swarm stubs are not advertised."""
import pytest

def test_langgraph_command_exists():
    pytest.importorskip("langgraph")
    try:
        from langgraph.types import Command
        assert Command is not None
    except ImportError:
        pytest.skip("langgraph.types.Command not in this version")

def test_swarm_member_runtime_is_rejected_by_schema():
    pytest.importorskip("munin.core.autonomy.subagent_factory")
    from munin.core.autonomy.spec import SubagentSpec
    with pytest.raises(Exception):
        SubagentSpec(name="swarm_test", purpose="test", runtime_type="swarm_member")
