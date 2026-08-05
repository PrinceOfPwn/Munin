"""
Kernel meta-tool schema parity and graph diagnostics whitelist parity.

Regression coverage for two runtime breakages observed on the
``feat/discord-community-adapter`` branch:

1. ``st(list_registered_agents, ..., ListToolsArgs)`` advertised a
   ``gen_only`` parameter that the handler does not accept. An LLM that
   trusted that schema broke its own runtime with
   ``unexpected keyword argument 'gen_only'``.

2. ``_probe_graphs`` only knew the static subagent catalog; forged graphs
   whose whitelist trusted the advertised kernel meta-tool surface (and MCP
   native capability tools such as ``list_generated_tools`` /
   ``describe_generated_tool``) were flagged "unknown" and a live run failed
   its own health probe.
"""
from __future__ import annotations

import pytest

pytest.importorskip("munin.core.autonomy.kernel")
pytest.importorskip("munin.mcp.tools.diagnostics_tool")


def _tool_schema(kernel, name: str):
    for tool in kernel.meta_tools():
        if getattr(tool, "name", None) == name:
            return tool.args_schema
    raise AssertionError(f"meta tool {name!r} not registered")


def test_meta_tools_list_agents_does_not_expose_gen_only(store):
    from munin.core.autonomy.kernel import AutonomyKernel

    kernel = AutonomyKernel(store)
    schema = _tool_schema(kernel, "list_registered_agents")
    fields = schema.model_fields

    assert "gen_only" not in fields

    # The tool must be invocable with zero arguments — same contract the LLM
    # sees, no unexpected-keyword runtime failures possible.
    assert schema.model_json_schema()["properties"] == {}


def test_meta_tools_list_workflows_do_not_expose_gen_only(store):
    from munin.core.autonomy.kernel import AutonomyKernel

    kernel = AutonomyKernel(store)
    schema = _tool_schema(kernel, "list_registered_workflows")

    assert "gen_only" not in schema.model_fields


def test_meta_tools_list_tools_still_expose_gen_only(store):
    from munin.core.autonomy.kernel import AutonomyKernel

    kernel = AutonomyKernel(store)
    schema = _tool_schema(kernel, "list_registered_tools")

    assert "gen_only" in schema.model_fields


def test_probe_graphs_accepts_kernel_and_mcp_native_whitelist(store, monkeypatch):
    """A graph whose whitelist trusts the advertised runtime surface must not
    fail the health probe (regression for the forged ``tool_dependency_fixer``
    graph that broke the E2E smoke)."""
    from munin.mcp.tools import diagnostics_tool

    # Kernel meta-tools + MCP native capability tools, none of which is part of
    # the static subagent catalog.
    whitelist = [
        "list_registered_agents",
        "inspect_registered_agent",
        "list_registered_tools",
        "list_generated_tools",
        "inspect_registered_tool",
        "describe_generated_tool",
        "create_tool",
    ]
    store.graph_register(
        name="diag_fixture_graph",
        purpose="verify whitelist parity",
        system_prompt="noop",
        tool_whitelist=whitelist,
        reset_policy="on_reset",
        created_by_agent="test",
    )

    monkeypatch.setattr(diagnostics_tool, "STATE", store)
    result = diagnostics_tool._probe_graphs()

    assert result["ok"] is True, result
    assert result["total_active"] == 1