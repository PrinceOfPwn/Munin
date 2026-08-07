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


def test_create_tool_accepts_explicit_parameters_and_tags(store):
    """create_tool forwards an explicit JSON schema + spec + tags to the registry."""
    factory = ToolFactory(store, run_id="run-1", agent_id="agent-a")
    params = {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {"type": "object"}},
            "mode": {"type": "string", "enum": ["fast", "deep"]},
        },
        "required": ["items"],
    }
    outcome = factory.create_tool(
        name="explicit_schema",
        source='''\ndef explicit_schema(items, mode="fast"):
    """Explicit schema tool."""
    return {"ok": True, "n": len(items), "mode": mode}
''',
        description="explicit schema tool",
        parameters=params,
        spec="user wanted list-of-dict handling",
        tags=["trump-analyst", "quick-tools"],
    )
    assert outcome["ok"], outcome

    row = store.procedural_get("gen__explicit_schema")
    assert row is not None
    sig = row.get("signature") or {}
    assert sig["parameters"] == params
    assert sig["provenance"]["spec"] == "user wanted list-of-dict handling"
    assert "trump-analyst" in (row.get("tags") or [])
    assert "run:run-1" in (row.get("tags") or [])


def test_create_tool_derives_schema_from_typed_signature(store):
    """Without an explicit parameters dict, the schema is derived from the
    authored function signature (generics included, not degraded to string)."""
    factory = ToolFactory(store, run_id="run-2", agent_id="agent-b")
    source = '''\
def derive_me(targets: list[dict], retries: int = 3) -> dict:
    """Derived schema tool."""
    return {"ok": True, "targets": targets, "retries": retries}
'''
    outcome = factory.create_tool(name="derive_me", source=source)
    assert outcome["ok"], outcome

    row = store.procedural_get("gen__derive_me")
    assert row is not None
    sig = row.get("signature") or {}
    parameters = sig.get("parameters") or {}
    props = parameters.get("properties", {})
    assert props["targets"]["type"] == "array"
    assert props["targets"]["items"] == {"type": "object", "additionalProperties": {}}
    assert props["retries"]["type"] == "integer"
    assert props["retries"]["default"] == 3
    assert "targets" in parameters.get("required", [])
    assert "retries" not in parameters.get("required", [])


def test_create_tool_explicit_parameters_win_over_derived(store):
    """An explicit parameters dict is persisted as-is; the signature is not derived."""
    factory = ToolFactory(store, run_id="run-3", agent_id="agent-c")
    params = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}
    outcome = factory.create_tool(
        name="override_me",
        source='''\ndef override_me(targets: list[dict]) -> dict:
    """Explicit schema should win."""
    return {"ok": True}
''',
        parameters=params,
    )
    assert outcome["ok"], outcome

    row = store.procedural_get("gen__override_me")
    sig = (row or {}).get("signature") or {}
    assert sig["parameters"] == params
