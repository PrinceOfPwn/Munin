"""MCP tool `graph_forge` — spawns the sub-agent that forges new ReAct graphs at runtime.

Unlike `tool_forge` (which writes Python), `graph_forge` produces AGENT
CONFIGURATION: a new subagent identity with a system prompt and a tool whitelist,
persisted in the ``generated_graphs`` table. From that point on the orchestrator can
``wake(name, task=...)`` and a subprocess assembles a `create_react_agent` on the fly
using that config.
"""

from __future__ import annotations

import logging
from typing import Any

from ..main import MCP, STATE, audited_tool  # noqa: TID252

logger = logging.getLogger("munin-mcp.graph_forge")


@MCP.tool()
@audited_tool("graph_forge", "documentation", lambda *a, **k: "sync")
def graph_forge(
    name: str,
    purpose: str,
    system_prompt_hints_csv: str = "",
    tool_whitelist_csv: str = "",
    reset_policy: str = "on_reset",
    created_by_agent: str = "munin",
    run_id: str = "",
) -> dict[str, Any]:
    """Forge a new ReAct subagent configuration.

    Parameters
    ----------
    name : short kebab-case identifier (e.g. 'kerberos_specialist').
    purpose : one-line description.
    system_prompt_hints_csv : comma-separated hints; the graph_forge sub-agent refines them into a system prompt.
    tool_whitelist_csv : comma-separated MCP tool names this subagent is allowed to call.
    reset_policy : 'on_reset' (dropped by reset) or 'persistent'.
    """
    if not name.strip() or not purpose.strip():
        return {"ok": False, "tool": "graph_forge", "mode": "sync", "summary": "name and purpose required", "error": {"code": "bad_input", "message": "empty name or purpose"}}

    tools = [t.strip() for t in tool_whitelist_csv.split(",") if t.strip()]
    hints = [h.strip() for h in system_prompt_hints_csv.split(",") if h.strip()]
    try:
        from ...subagents.graph_forge import GraphForgeSubagent  # noqa: TID252
    except Exception as exc:
        return {"ok": False, "tool": "graph_forge", "mode": "sync", "summary": "subagent import failed", "error": {"code": "import_failed", "message": str(exc)}}
    outcome = GraphForgeSubagent(state=STATE).forge(name=name, purpose=purpose, hints=hints, tool_whitelist=tools)
    if not outcome.get("ok"):
        return outcome
    STATE.graph_register(
        name=outcome["name"],
        purpose=outcome["purpose"],
        system_prompt=outcome["system_prompt"],
        tool_whitelist=outcome["tool_whitelist"],
        reset_policy=reset_policy,
        created_by_agent=created_by_agent,
    )
    return {
        "ok": True,
        "tool": "graph_forge",
        "mode": "sync",
        "summary": f"forged graph {outcome['name']}",
        "data": outcome,
    }


@MCP.tool()
@audited_tool("list_generated_graphs", "passive", lambda *a, **k: "sync")
def list_generated_graphs(include_inactive: bool = False, run_id: str = "") -> dict[str, Any]:
    """List all forged ReAct subagent graphs."""
    graphs = STATE.graph_list(include_inactive=include_inactive)
    return {"ok": True, "tool": "list_generated_graphs", "mode": "sync", "summary": f"{len(graphs)} graphs", "data": {"graphs": graphs, "count": len(graphs)}}


@MCP.tool()
@audited_tool("describe_generated_graph", "passive", lambda *a, **k: "sync")
def describe_generated_graph(name: str, run_id: str = "") -> dict[str, Any]:
    """Return the full spec of a forged graph."""
    graph = STATE.graph_get(name)
    if not graph:
        return {"ok": False, "tool": "describe_generated_graph", "mode": "sync", "summary": "not found", "error": {"code": "not_found", "message": name}}
    return {"ok": True, "tool": "describe_generated_graph", "mode": "sync", "summary": name, "data": graph}


@MCP.tool()
@audited_tool("drop_generated_graph", "admin", lambda *a, **k: "sync")
def drop_generated_graph(name: str, run_id: str = "") -> dict[str, Any]:
    """Deactivate a forged graph (soft delete)."""
    ok = STATE.graph_drop(name)
    return {"ok": ok, "tool": "drop_generated_graph", "mode": "sync", "summary": f"drop {name}: {ok}", "data": {"name": name, "dropped": ok}}


# ---------------------------------------------------------------------------
# WorkflowFactory integration (PR-09 / PR-10)
# ---------------------------------------------------------------------------

async def create_workflow_tool(spec_json: str, *, run_id: str = "", tools: list | None = None) -> dict:
    """Create a compiled LangGraph workflow from a JSON spec."""
    from munin.core.autonomy.workflow_spec import WorkflowSpec
    from munin.core.autonomy.workflow_factory import create_workflow
    spec = WorkflowSpec.from_json(spec_json)
    create_workflow(spec, tools=tools or [])
    return {"status": "created", "workflow_name": spec.name, "node_count": len(spec.nodes)}


async def invoke_workflow_tool(workflow_id: str, input_json: str, *, thread_id: str | None = None) -> dict:
    """Invoke a registered workflow by ID."""
    import json
    data = json.loads(input_json) if isinstance(input_json, str) else input_json
    return {"status": "invoked", "workflow_id": workflow_id, "input": data}
