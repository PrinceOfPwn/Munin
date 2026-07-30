"""Partial aggregates from completed workers survive supervisor restart (conceptual)."""
import pytest
pytest.importorskip("munin.core.parallel.send_workers")

from munin.core.parallel.send_workers import WorkerState, fanout


def test_worker_state_is_typeddict():
    """WorkerState is a TypedDict with required keys."""
    state = WorkerState(messages=[], worker_index=0, task_args={}, aggregate=[])
    assert state["worker_index"] == 0
    assert state["aggregate"] == []


def test_aggregate_uses_operator_add_annotation():
    """The aggregate field uses Annotated[list, operator.add] for LangGraph reducer."""
    import operator
    from typing import get_type_hints, Annotated, get_args
    hints = get_type_hints(WorkerState, include_extras=True)
    agg_hint = hints["aggregate"]
    args = get_args(agg_hint)
    assert len(args) == 2
    assert args[1] is operator.add
