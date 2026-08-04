"""Swarm handoff tool creation tests — LEGACY characterization.

Anchors the deprecated ``munin.core.coordination`` swarm handoff builders
(retained for characterization; do not extend). Prefer the supervisor_v2
presence/wake path (see ``tests/test_orchestrator_wake_contract.py``).
"""
import pytest
pytest.importorskip("munin.core.coordination")
from munin.core.coordination.handoff_tools import make_handoff_tool, make_handoff_tools_for_agents

def test_make_handoff_tool_returns_something():
    tool = make_handoff_tool("ldap_specialist")
    assert tool is not None

def test_make_handoff_tools_for_multiple():
    tools = make_handoff_tools_for_agents(["recon_agent", "exploit_agent"])
    assert len(tools) == 2

def test_handoff_tool_has_description():
    tool = make_handoff_tool("recon_agent", description="Recon specialist")
    assert getattr(tool, "description", "")
