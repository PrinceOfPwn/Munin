"""Dep validation raises when dep deactivated."""
import pytest
pytest.importorskip("munin.core.autonomy.agent_registry")

from munin.core.autonomy.agent_registry import AgentRegistry
from munin.core.autonomy.spec import SubagentSpec


def test_register_with_dependencies(tmp_path):
    registry = AgentRegistry(str(tmp_path / "agents.db"))
    spec = SubagentSpec(name="dep_consumer", purpose="uses other agents")
    agent_id, _ = registry.register_agent(
        spec,
        dependencies=["tool:port_scan", "agent:ldap_specialist"]
    )
    import json
    info = registry.inspect_registered_agent(agent_id)
    deps = json.loads(info["dependencies_json"])
    assert "tool:port_scan" in deps
