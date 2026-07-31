"""Handoff tools for peer specialist transfer via LangGraph Command."""
from __future__ import annotations
from typing import Any


def make_handoff_tool(agent_name: str, *, description: str = "") -> Any:
    """Create a tool that transfers control to agent_name."""
    try:
        from langgraph_swarm import create_handoff_tool
        return create_handoff_tool(
            agent_name=agent_name,
            description=description or f"Transfer to {agent_name}"
        )
    except (ImportError, AttributeError):
        from langchain_core.tools import StructuredTool
        from pydantic import BaseModel

        class HandoffInput(BaseModel):
            message: str = ""

        def handoff_fn(message: str = "") -> dict:
            return {"goto": agent_name, "update": {"active_agent": agent_name}}

        return StructuredTool(
            name=f"transfer_to_{agent_name}",
            description=description or f"Transfer control to {agent_name}",
            args_schema=HandoffInput,
            func=handoff_fn,
        )


def make_handoff_tools_for_agents(agent_names: list[str]) -> list[Any]:
    """Create handoff tools for multiple agent names."""
    return [make_handoff_tool(name) for name in agent_names]
