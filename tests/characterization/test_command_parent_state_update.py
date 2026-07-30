"""LangGraph Command and swarm_member route tests."""
import pytest

def test_langgraph_command_exists():
    langgraph = pytest.importorskip("langgraph")
    try:
        from langgraph.types import Command
        assert Command is not None
    except ImportError:
        pytest.skip("langgraph.types.Command not in this version")

def test_swarm_member_route(monkeypatch):
    monkeypatch.delenv("MUNIN_LANGGRAPH_URL", raising=False)
    pytest.importorskip("munin.core.autonomy.subagent_factory")
    from munin.core.autonomy.spec import SubagentSpec
    from munin.core.autonomy.subagent_factory import SubagentFactory
    factory = SubagentFactory(tools=[])
    spec = SubagentSpec(name="swarm_test", purpose="test", runtime_type="swarm_member")
    try:
        agent = factory.create_subagent(spec)
        assert agent is not None
    except ImportError:
        pytest.skip("deepagents or langgraph not installed")
