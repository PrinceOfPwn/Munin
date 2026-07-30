"""Swarm build test."""
import pytest
pytest.importorskip("munin.core.coordination.swarm")

def test_build_swarm_requires_langgraph_swarm():
    pytest.importorskip("langgraph_swarm")
    from munin.core.coordination.swarm import build_swarm
    from munin.core.autonomy.spec import SubagentSpec
    from munin.core.autonomy.subagent_factory import SubagentFactory

    factory = SubagentFactory(tools=[])
    agent_a = factory.create_subagent(SubagentSpec(name="agent_a", purpose="A", runtime_type="compiled_langgraph"))
    agent_b = factory.create_subagent(SubagentSpec(name="agent_b", purpose="B", runtime_type="compiled_langgraph"))
    swarm = build_swarm([agent_a, agent_b], default_active_agent="agent_a")
    assert swarm is not None
