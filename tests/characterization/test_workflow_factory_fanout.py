"""Send fan-out edge compiles without error."""
import pytest
pytest.importorskip("munin.core.autonomy.workflow_factory")
pytest.importorskip("langgraph")
from munin.core.autonomy.workflow_spec import WorkflowSpec, Node, Edge, CustomState
from munin.core.autonomy.workflow_factory import create_workflow

def test_fanout_compiles():
    spec = WorkflowSpec(
        name="fanout_wf",
        nodes=[Node(name="coord", kind="deterministic"), Node(name="worker", kind="deterministic")],
        edges=[Edge(src="coord", dst="worker", kind="send", fanout_key="targets")],
        custom_state=[CustomState(name="targets", type="list"), CustomState(name="results", type="list", reducer="append")],
        entry_point="coord", finish_points=["worker"], checkpointer="none",
    )
    assert create_workflow(spec) is not None
