"""Declarative DSL spec → compiled Pregel."""
import pytest
pytest.importorskip("munin.core.autonomy.workflow_factory")
pytest.importorskip("langgraph")
from munin.core.autonomy.workflow_spec import WorkflowSpec, Node, Edge
from munin.core.autonomy.workflow_factory import create_workflow

def simple_spec():
    return WorkflowSpec(
        name="test_wf",
        nodes=[Node(name="step1", kind="deterministic"), Node(name="step2", kind="deterministic")],
        edges=[Edge(src="step1", dst="step2")],
        entry_point="step1", finish_points=["step2"], checkpointer="none",
    )

def test_spec_compiles():
    assert create_workflow(simple_spec()) is not None

def test_compiled_is_invocable():
    g = create_workflow(simple_spec())
    assert hasattr(g, "invoke") or hasattr(g, "ainvoke")
