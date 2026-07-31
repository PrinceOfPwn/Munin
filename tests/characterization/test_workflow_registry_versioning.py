"""Record execution and inspect workflow."""
import pytest, json
pytest.importorskip("munin.core.autonomy.workflow_registry")
from munin.core.autonomy.workflow_registry import WorkflowRegistry
from munin.core.autonomy.workflow_spec import WorkflowSpec, Node

def test_record_exec(tmp_path):
    r = WorkflowRegistry(str(tmp_path / "wf.db"))
    spec = WorkflowSpec(name="exec_wf", nodes=[Node(name="n1", kind="deterministic")], entry_point="n1", checkpointer="none")
    wf_id, version = r.register_workflow(spec)
    r.record_workflow_exec(wf_id, version, "Completed 3-node scan")
    info = r.inspect_registered_workflow(wf_id)
    history = json.loads(info["exec_history_json"])
    assert len(history) == 1 and "scan" in history[0]["result"]
