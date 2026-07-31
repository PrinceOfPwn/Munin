"""Tool factory persistence: tools survive across factory instances (same DB)."""
import pytest

pytest.importorskip("munin.core.autonomy.tool_factory")

from munin.core.autonomy.tool_factory import ToolFactory

SOURCE = '''
def nmap_helper(target: str = "") -> str:
    """Pretend to scan."""
    return f"scanned:{target}"
'''


def test_tool_survives_across_factory_instances(store):
    first = ToolFactory(store, run_id="run-1", agent_id="agent-a")
    outcome = first.create_tool(name="nmap_helper", source=SOURCE, description="scan helper")
    assert outcome["ok"], outcome

    # A brand-new factory instance (new run) resolves the same tool from the registry.
    second = ToolFactory(store, run_id="run-2", agent_id="agent-b")
    result = second.invoke_registered_tool("gen__nmap_helper", {"target": "10.0.0.5"})
    assert result["ok"], result
    assert "10.0.0.5" in str(result["data"])


def test_provenance_recorded_on_create(store):
    factory = ToolFactory(store, run_id="run-1", agent_id="agent-a")
    outcome = factory.create_tool(name="prov_tool", source=SOURCE.replace("nmap_helper", "prov_tool"))
    assert outcome["ok"], outcome

    row = store.procedural_get("gen__prov_tool")
    assert row is not None
    provenance = (row.get("signature") or {}).get("provenance", {})
    assert provenance["creator_agent"] == "agent-a"
    assert provenance["parent_run"] == "run-1"
    assert provenance["validation"]["ast_guard"] == "pass"
