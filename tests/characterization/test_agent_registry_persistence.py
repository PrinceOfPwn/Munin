"""Register → restart (new instance) → rebuild — identical runnable."""
import pytest
pytest.importorskip("munin.core.autonomy.agent_registry")

from munin.core.autonomy.agent_registry import AgentRegistry
from munin.core.autonomy.spec import SubagentSpec


@pytest.fixture
def db_file(tmp_path):
    return str(tmp_path / "test_agents.db")


def test_register_and_list(db_file):
    registry = AgentRegistry(db_file)
    spec = SubagentSpec(name="ldap_specialist", purpose="LDAP enumeration", runtime_type="persisted_subagent_dict")
    agent_id, version = registry.register_agent(spec, created_by="supervisor", parent_run="run-1")

    agents = registry.list_registered_agents()
    assert any(a["agent_id"] == agent_id for a in agents)


def test_rebuild_after_new_instance(db_file):
    """Simulate restart: new AgentRegistry instance reads same DB."""
    spec = SubagentSpec(name="exploit_specialist", purpose="Exploit development", runtime_type="persisted_subagent_dict")

    registry1 = AgentRegistry(db_file)
    agent_id, version = registry1.register_agent(spec)

    # New instance = simulated restart
    registry2 = AgentRegistry(db_file)
    rebuilt = registry2.rebuild_agent(agent_id, tools=[])
    assert rebuilt is not None
    assert rebuilt["name"] == "exploit_specialist"
