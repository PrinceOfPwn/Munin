# tags: [coordination, subagent, langgraph, core, orchestrator, build_swarm, langgraph_swarm, create_swarm, default_active_agent, multi-agent-swarm, swarm-compile, checkpointer, specialists, agent-coordination, langgraph-extension]
# DEPRECATED: legacy coordination retained for characterization tests; do not extend. Prefer supervisor_v2 presence/wake path.
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
