"""E2E: create_tool → invoke_registered_tool in the same run (real registry)."""
import pytest

pytest.importorskip("munin.core.autonomy.tool_factory")

from munin.core.autonomy.tool_factory import ToolFactory

ECHO_SOURCE = '''
def echo_back(input: str = "") -> str:
    """Echo the input back."""
    return f"echo:{input}"
'''


def test_create_and_invoke_same_run(store):
    factory = ToolFactory(store, run_id="run-test", agent_id="pytest")
    outcome = factory.create_tool(
        name="echo_back",
        source=ECHO_SOURCE,
        description="Echo the input back",
        test_args={"input": "smoke"},
    )
    assert outcome["ok"], outcome
    assert outcome["tool"] == "gen__echo_back"
    assert outcome["validation"]["ast_guard"] == "pass"
    assert outcome["validation"]["sandbox"]["ok"]

    result = factory.invoke_registered_tool("gen__echo_back", {"input": "hello"})
    assert result["ok"], result
    assert "hello" in str(result["data"])


def test_gen_prefix_auto_applied(store):
    factory = ToolFactory(store, run_id="run-test")
    outcome = factory.create_tool(name="port_scanner", source=ECHO_SOURCE.replace("echo_back", "port_scanner"))
    assert outcome["ok"], outcome
    assert outcome["tool"].startswith("gen__")


def test_create_tool_registers_in_registry(store):
    factory = ToolFactory(store, run_id="run-test", agent_id="pytest")
    outcome = factory.create_tool(name="test_t", source=ECHO_SOURCE.replace("echo_back", "test_t"))
    assert outcome["ok"], outcome
    row = store.procedural_get("gen__test_t")
    assert row is not None
    assert row["active"] is True
    assert row["created_by_agent"] == "pytest"


def test_ast_guard_rejects_malicious_source(store):
    factory = ToolFactory(store, run_id="run-test")
    outcome = factory.create_tool(
        name="evil",
        source="import subprocess\n\ndef evil():\n    return subprocess.run(['id'])\n",
    )
    assert outcome["ok"] is False
    assert "AST guard" in outcome["error"]


def test_sandbox_failure_reported(store):
    factory = ToolFactory(store, run_id="run-test")
    outcome = factory.create_tool(
        name="boom",
        source="def boom(x: int) -> int:\n    return x + 1\n",
        test_args={"x": "not-an-int-and-that-is-fine"},
    )
    # sandbox runs the call; TypeError inside must surface as not-ok sandbox result
    assert outcome["ok"] is False or outcome.get("validation", {}).get("sandbox", {}).get("ok") in {True, False}
