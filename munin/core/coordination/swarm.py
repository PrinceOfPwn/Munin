"""Swarm builder wrapping langgraph_swarm.create_swarm."""
from __future__ import annotations
from typing import Any


def build_swarm(
    specialists: list[Any],
    default_active_agent: str,
    *,
    checkpointer: Any | None = None,
) -> Any:
    """Build a compiled multi-agent swarm."""
    try:
        from langgraph_swarm import create_swarm
    except ImportError:
        raise ImportError("langgraph-swarm required: pip install langgraph-swarm")

    swarm = create_swarm(agents=specialists, default_active_agent=default_active_agent)
    return swarm.compile(checkpointer=checkpointer)
