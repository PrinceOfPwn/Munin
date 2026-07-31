"""
Send workers — native LangGraph fan-out replacing ThreadPoolExecutor parallel.py.

Key differences from the old parallel.py:
- No MUNIN_MAX_PARALLEL_TOOLS hard cap — N workers = N items in the fan-out list
- MUNIN_SUGGESTED_WORKERS is advisory only (default 4), not enforced
- One worker failing does NOT abort the batch (error captured in aggregate)
- Partial aggregates from completed workers are preserved on supervisor restart
  (via LangGraph checkpointer)

Usage in supervisor:
    from langgraph.types import Send
    from munin.core.parallel.send_workers import fanout, WorkerState

    def coordinator(state: SupervisorState) -> list[Send]:
        return fanout("tool_worker", state["pending_tools"])
"""
from __future__ import annotations
import os
from typing import Annotated, Any, TypedDict
import operator


MUNIN_SUGGESTED_WORKERS: int = int(os.environ.get("MUNIN_SUGGESTED_WORKERS", "4"))


class WorkerState(TypedDict):
    """State for a single fan-out worker and the fan-in aggregator."""
    messages: list
    items: list  # input only: the full work list the coordinator fans out over
    worker_index: int
    task_args: dict
    aggregate: Annotated[list, operator.add]


def fanout(target_node: str, items: list[Any]) -> list:
    """
    Create Send objects for each item in items, routing to target_node.

    Each Send carries a WorkerState-compatible dict with:
      - worker_index: position in the original list
      - task_args: the item itself (wrapped in a dict if not already)
      - aggregate: [] (accumulated by operator.add reducer)

    Args:
        target_node: LangGraph node name to fan out to
        items: List of items to distribute. Each becomes one worker invocation.

    Returns:
        List of Send objects (use as return value from a conditional_edges fn)
    """
    try:
        from langgraph.types import Send
    except ImportError:
        raise ImportError("langgraph required for Send fan-out")

    sends = []
    for i, item in enumerate(items):
        task_args = item if isinstance(item, dict) else {"payload": item}
        sends.append(Send(target_node, {
            "worker_index": i,
            "task_args": task_args,
            "aggregate": [],
        }))
    return sends


def make_worker_node(tool_fn):
    """
    Wrap a tool function as a LangGraph worker node.

    The worker:
    - Calls tool_fn(task_args)
    - Appends the result to aggregate (via Annotated[list, operator.add])
    - On failure, appends an error dict instead of raising (batch continues)

    Returns a LangGraph-compatible node function.
    """
    async def worker_node(state: WorkerState) -> WorkerState:
        try:
            import inspect
            if inspect.iscoroutinefunction(tool_fn):
                result = await tool_fn(**state["task_args"])
            else:
                result = tool_fn(**state["task_args"])
            outcome = {"index": state["worker_index"], "result": result, "error": None}
        except Exception as exc:
            outcome = {"index": state["worker_index"], "result": None, "error": str(exc)}

        return {
            "aggregate": [outcome],
        }

    return worker_node
