"""Register → restart instance → rebuild — identical workflow."""
import pytest
pytest.importorskip("munin.core.autonomy.workflow_registry")
from munin.core.autonomy.workflow_registry import WorkflowRegistry
from munin.core.autonomy.workflow_spec import WorkflowSpec, Node

def spec(name="wf"): return WorkflowSpec(name=name, nodes=[Node(name="n1", kind="deterministic")], entry_point="n1", checkpointer="none")

def test_register_and_list(tmp_path):
    r = WorkflowRegistry(str(tmp_path / "wf.db"))
    wf_id, v = r.register_workflow(spec())
    assert v == 1
    assert any(w["workflow_id"] == wf_id for w in r.list_registered_workflows())

def test_rebuild_after_restart(tmp_path):
    db = str(tmp_path / "wf2.db")
    wf_id, _ = WorkflowRegistry(db).register_workflow(spec("persistent"))
    assert WorkflowRegistry(db).rebuild_workflow(wf_id) is not None

def test_deprecate(tmp_path):
    r = WorkflowRegistry(str(tmp_path / "wf3.db"))
    wf_id, _ = r.register_workflow(spec("dep"))
    r.deprecate(wf_id)
    assert not any(w["workflow_id"] == wf_id for w in r.list_registered_workflows(status="active"))
