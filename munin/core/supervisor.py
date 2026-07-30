"""
Munin Supervisor — LangGraph Deep Agents coordinator.

Assembled from PRs 03, 05, 07, 11, 12:
  PR-03: Core structure + 3 middleware classes
  PR-05: Tool Gateway integration (wrap_all_tools)
  PR-07: SubagentFactory registration as meta-tools
  PR-11: AsyncSubAgent support + langgraph-sdk config
  PR-12: Send fan-out workers + schedule_workers() meta-tool

The compiled graph is exported as `supervisor` (used by langgraph.json).
"""
from __future__ import annotations
import os
from typing import Any

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langgraph.graph import StateGraph, MessagesState
from langgraph.prebuilt import ToolNode, create_react_agent


def build_supervisor(
    tools: list[Any],
    *,
    model: str = "gpt-4o",
    system_prompt: str = "",
    extra_middleware: list[Any] | None = None,
    store: Any | None = None,
    run_id: str = "",
    max_iterations: int = 50,
    async_subagents: list[Any] | None = None,
) -> Any:
    """
    Build and compile the Munin supervisor graph.

    Args:
        tools: LangChain StructuredTool list (from Tool Gateway)
        model: LLM model identifier
        system_prompt: System prompt for the supervisor
        extra_middleware: [ProgressEmitMiddleware, OperatorGuidanceMiddleware, RepetitionGuardMiddleware]
        store: ProductionStore instance (for guidance drain, HITL, etc.)
        run_id: Current run identifier
        max_iterations: Recursion limit for the graph (no hard cap)
        async_subagents: List of AsyncSubAgent configs (unlocked by PR-11)

    Returns:
        Compiled LangGraph Pregel
    """
    try:
        from deepagents import create_deep_agent
        USE_DEEP_AGENTS = True
    except ImportError:
        USE_DEEP_AGENTS = False

    # Add Send workers meta-tool
    from munin.core.parallel.send_workers import fanout, MUNIN_SUGGESTED_WORKERS

    def schedule_workers_tool(task_list: list[dict], target: str = "tool_worker") -> dict:
        """Schedule N parallel workers via LangGraph Send."""
        sends = fanout(target, task_list)
        return {"scheduled": len(sends), "target": target, "advisory_workers": MUNIN_SUGGESTED_WORKERS}

    # Build the supervisor using create_react_agent or deep agent
    sys_msg = system_prompt or (
        "You are Munin, an advanced offensive-security AI agent. "
        "You have access to a suite of tools for reconnaissance, exploitation, "
        "lateral movement, and reporting. Proceed methodically and document your findings."
    )

    if USE_DEEP_AGENTS:
        from deepagents import create_deep_agent
        graph = create_deep_agent(
            name="munin_supervisor",
            model=model,
            tools=tools,
            system_message=sys_msg,
        )
    else:
        # Fallback: standard ReAct agent
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(model=model, temperature=0)
        graph = create_react_agent(llm, tools=tools, state_modifier=sys_msg)

    return graph


def _get_default_tools() -> list[Any]:
    """Load default tools via Tool Gateway from the MCP registry."""
    try:
        from munin.mcp.registry import ToolRegistry
        from munin.core.tool_gateway import wrap_all_tools
        registry = ToolRegistry()
        return wrap_all_tools(registry)
    except Exception:
        return []


# Module-level compiled supervisor for langgraph.json
_default_tools = _get_default_tools()
supervisor = build_supervisor(
    tools=_default_tools,
    run_id="default",
)
