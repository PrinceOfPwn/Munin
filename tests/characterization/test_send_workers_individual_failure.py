"""One worker fails; others complete; batch not aborted."""
import pytest
pytest.importorskip("munin.core.parallel.send_workers")

from munin.core.parallel.send_workers import make_worker_node, WorkerState


@pytest.mark.asyncio
async def test_failing_worker_captured_not_raised():
    def sometimes_fails(payload: str) -> str:
        if payload == "bad":
            raise ValueError("Intentional failure")
        return f"ok:{payload}"

    worker = make_worker_node(sometimes_fails)

    # Good worker
    state_ok = WorkerState(messages=[], worker_index=0, task_args={"payload": "good"}, aggregate=[])
    result_ok = await worker(state_ok)
    assert result_ok["aggregate"][0]["error"] is None
    assert "ok:good" in str(result_ok["aggregate"][0]["result"])

    # Failing worker
    state_bad = WorkerState(messages=[], worker_index=1, task_args={"payload": "bad"}, aggregate=[])
    result_bad = await worker(state_bad)
    assert result_bad["aggregate"][0]["error"] is not None
    assert "Intentional" in result_bad["aggregate"][0]["error"]
