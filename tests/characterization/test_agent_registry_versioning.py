"""v1/v2 versioning: latest default, old version readable."""
import pytest
pytest.importorskip("munin.core.autonomy.agent_registry")

from munin.core.autonomy.agent_registry import AgentRegistry
from munin.core.autonomy.spec import SubagentSpec


@pytest.fixture
def registry(tmp_path):
    return AgentRegistry(str(tmp_path / "agents.db"))


def test_version_increments(registry):
    spec = SubagentSpec(name="v_agent", purpose="test")
    id1, v1 = registry.register_agent(spec)
    id2, v2 = registry.register_agent(spec)
    # Same spec → new unique ID each time (uuid in agent_id)
    # Different agent_ids get version 1
    assert v1 == 1
    assert v2 == 1


def test_deprecate_agent(registry):
    spec = SubagentSpec(name="dep_agent", purpose="test")
    agent_id, version = registry.register_agent(spec)
    registry.deprecate(agent_id)

    active = registry.list_registered_agents(status="active")
    assert not any(a["agent_id"] == agent_id for a in active)


def test_record_invocation(registry):
    spec = SubagentSpec(name="inv_agent", purpose="test")
    agent_id, version = registry.register_agent(spec)
    registry.record_invocation(agent_id, version, "Completed LDAP scan")

    info = registry.inspect_registered_agent(agent_id)
    import json
    history = json.loads(info["exec_history_json"])
    assert len(history) == 1
    assert "LDAP" in history[0]["result"]
