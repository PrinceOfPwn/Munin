# DEPRECATED: legacy coordination retained for characterization tests; do not extend. Prefer supervisor_v2 presence/wake path.
"""Native LangGraph coordination: swarm handoffs and peer specialists."""
from .swarm import build_swarm
from .handoff_tools import make_handoff_tool

__all__ = ["build_swarm", "make_handoff_tool"]
