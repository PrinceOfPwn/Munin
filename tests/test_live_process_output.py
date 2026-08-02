from __future__ import annotations

import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest

from munin.mcp.opsec import ExecutionEngine


def _settings(tmp_path):
    return SimpleNamespace(
        workspace_root=tmp_path,
        max_output_chars=16_000,
        preflight_policy="off",
    )


def _slow_command() -> str:
    code = (
        "import sys,time; "
        "print('first line', flush=True); "
        "time.sleep(0.15); "
        "print('second line', flush=True); "
        "print('warning line', file=sys.stderr, flush=True)"
    )
    return subprocess.list2cmdline([sys.executable, "-c", code])


def test_process_output_is_emitted_before_result_is_compacted(tmp_path):
    events: list[dict] = []
    job = SimpleNamespace(
        cancel_requested=False,
        process_handle=None,
        process_pid=0,
        progress_sink=events.append,
    )
    engine = ExecutionEngine(_settings(tmp_path))

    result = engine.execute_job(
        job=job,
        tool="execute_command",
        level="active",
        command=_slow_command(),
        timeout=10,
    )

    output_events = [event for event in events if event.get("kind") == "tool_output"]
    assert result["ok"] is True
    assert [event["stream"] for event in output_events].count("stdout") == 2
    assert [event["stream"] for event in output_events].count("stderr") == 1
    assert sorted(event["sequence"] for event in output_events) == [1, 2, 3]
    assert "first line" in result["data"]["stdout"]
    assert "warning line" in result["data"]["stderr"]


def test_job_manager_progress_has_run_scoped_cursors():
    from munin.mcp.jobs import JobManager

    manager = JobManager(workers=1)
    try:
        job = manager.submit(
            tool="execute_command",
            level="active",
            target="localhost",
            command_preview="sleep",
            run_id="run-live",
            tool_call_id="call-live",
            fn=lambda _job: {"ok": True},
        )
        manager.add_progress(job.job_id, {"kind": "tool_output", "stream": "stdout", "text": "line"})
        cursors: dict[str, int] = {}
        first = manager.progress_for_run("run-live", cursors)
        second = manager.progress_for_run("run-live", cursors)
        assert first[0]["job_id"] == job.job_id
        assert first[0]["tool_call_id"] == "call-live"
        assert second == []
    finally:
        manager.shutdown()


def test_tool_output_event_round_trips_through_run_event_log(tmp_path):
    pytest.importorskip("argon2")
    from munin.production.chat import _envelope_from_event
    from munin.production.store import ProductionStore

    store = ProductionStore.for_sqlite(tmp_path / "output.sqlite", master_key=b"o" * 32)
    operator = store.create_user(username="output-operator", password="a strong output password", role="operator")
    conversation = store.create_conversation(owner_id=operator["id"], title="Live output")
    turn = store.create_turn(
        actor_id=operator["id"],
        conversation_id=conversation["id"],
        content="stream output",
        idempotency_key="live-output",
    )
    run_id = turn["run"]["id"]
    store.append_tool_output_event(
        run_id=run_id,
        tool_name="execute_command",
        tool_call_id="call-output",
        job_id="job-output",
        stream="stderr",
        text="warning",
        sequence=4,
        elapsed_ms=900,
    )

    event = store.list_run_events(run_id)[-1]
    envelope = _envelope_from_event(event, run_id=run_id, tools_by_eid={}, reasoning_by_eid={})
    assert envelope == {
        "kind": "tool_output",
        "run_id": run_id,
        "tool_call_id": "call-output",
        "tool_name": "execute_command",
        "job_id": "job-output",
        "stream": "stderr",
        "text": "warning",
        "sequence": 4,
        "elapsed_ms": 900,
        "final": False,
    }


def test_completed_tool_replay_joins_by_stable_call_id():
    from munin.production.chat import _envelope_from_event

    envelope = _envelope_from_event(
        {
            "id": "completion-event",
            "kind": "tool.completed",
            "payload": {"tool_call_id": "call-1"},
        },
        run_id="run-1",
        tools_by_eid={},
        tools_by_call_id={
            "call-1": {
                "id": "call-1",
                "tool_name": "execute_command",
                "result": {"summary": "final output"},
            }
        },
        reasoning_by_eid={},
    )

    assert envelope == {
        "kind": "tool_result",
        "run_id": "run-1",
        "tool_call_id": "call-1",
        "tool_name": "execute_command",
        "output": "final output",
    }

def test_partial_newline_free_output_is_streamed(tmp_path):
    events: list[dict] = []
    job = SimpleNamespace(
        cancel_requested=False,
        process_handle=None,
        process_pid=0,
        progress_sink=events.append,
    )
    engine = ExecutionEngine(_settings(tmp_path))
    code = "import sys,time; sys.stdout.write('working'); sys.stdout.flush(); time.sleep(0.2)"
    result = engine.execute_job(
        job=job,
        tool="execute_command",
        level="active",
        command=subprocess.list2cmdline([sys.executable, "-c", code]),
        timeout=10,
    )
    chunks = [event["text"] for event in events if event.get("kind") == "tool_output"]
    assert result["ok"] is True
    assert "working" in "".join(chunks)


def test_long_newline_free_output_is_not_truncated(tmp_path, monkeypatch):
    import munin.mcp.opsec as opsec

    monkeypatch.setattr(opsec, "_PROCESS_OUTPUT_CHUNK_CHARS", 256)
    payload = "x" * 4_000
    events: list[dict] = []
    job = SimpleNamespace(
        cancel_requested=False,
        process_handle=None,
        process_pid=0,
        progress_sink=events.append,
    )
    engine = ExecutionEngine(_settings(tmp_path))
    result = engine.execute_job(
        job=job,
        tool="execute_command",
        level="active",
        command=subprocess.list2cmdline([sys.executable, "-c", f"print({payload!r}, end='')"]),
        timeout=10,
    )
    streamed = "".join(
        event["text"] for event in events if event.get("kind") == "tool_output"
    )
    assert result["ok"] is True
    assert streamed == payload
    assert result["data"]["stdout"] == payload


def test_live_output_is_redacted_before_progress_emission(tmp_path):
    events: list[dict] = []
    job = SimpleNamespace(
        cancel_requested=False,
        process_handle=None,
        process_pid=0,
        progress_sink=events.append,
    )
    engine = ExecutionEngine(_settings(tmp_path))
    secret = "sk-" + "a" * 40
    result = engine.execute_job(
        job=job,
        tool="execute_command",
        level="active",
        command=shlex.join([sys.executable, "-c", f"print({secret!r})"]),
        timeout=10,
    )
    rendered = "".join(
        event.get("text", "") for event in events if event.get("kind") == "tool_output"
    )
    assert secret not in rendered
    assert secret not in result["data"]["stdout"]
    assert "REDACTED" in rendered


def test_job_manager_reserves_cursor_metadata_and_keeps_unread_events(monkeypatch):
    import munin.mcp.jobs as jobs

    monkeypatch.setattr(jobs, "MAX_PENDING_PROGRESS_EVENTS", 512)
    manager = jobs.JobManager(workers=1)
    try:
        job = manager.submit(
            tool="execute_command",
            level="active",
            target="localhost",
            command_preview="echo",
            run_id="run-cursor",
            tool_call_id="call-cursor",
            fn=lambda _job: {"ok": True},
        )
        for value in range(250):
            manager.add_progress(
                job.job_id,
                {
                    "kind": "tool_output",
                    "text": str(value),
                    "sequence": 1,
                    "run_id": "attacker-run",
                    "job_id": "attacker-job",
                },
            )
        cursors: dict[str, int] = {}
        events = manager.progress_for_run("run-cursor", cursors)
        assert len(events) == 250
        assert [event["sequence"] for event in events] == list(range(1, 251))
        assert all(event["run_id"] == "run-cursor" for event in events)
        assert all(event["job_id"] == job.job_id for event in events)
        assert all(event["source_sequence"] == 1 for event in events)
        assert manager.progress_for_run("run-cursor", cursors) == []
    finally:
        manager.shutdown()


def test_durable_tool_output_is_compacted_to_a_bounded_tail(tmp_path, monkeypatch):
    pytest.importorskip("argon2")
    import munin.production.store as store_module
    from munin.production.store import ProductionStore

    monkeypatch.setattr(store_module, "_MAX_DURABLE_TOOL_OUTPUT_EVENTS", 16)
    monkeypatch.setattr(store_module, "_MAX_DURABLE_TOOL_OUTPUT_BYTES", 65_536)
    store = ProductionStore.for_sqlite(tmp_path / "bounded-output.sqlite", master_key=b"b" * 32)
    operator = store.create_user(username="bounded-output", password="a strong bounded password", role="operator")
    conversation = store.create_conversation(owner_id=operator["id"], title="Bounded output")
    turn = store.create_turn(
        actor_id=operator["id"],
        conversation_id=conversation["id"],
        content="stream output",
        idempotency_key="bounded-output",
    )
    run_id = turn["run"]["id"]
    for sequence in range(40):
        store.append_tool_output_event(
            run_id=run_id,
            tool_name="execute_command",
            tool_call_id="call-bounded",
            job_id="job-bounded",
            stream="stdout",
            text=f"line-{sequence}",
            sequence=sequence,
        )
    output_events = [event for event in store.list_run_events(run_id) if event["kind"] == "tool.output"]
    assert len(output_events) == 16
    assert [event["payload"]["text"] for event in output_events] == [
        f"line-{sequence}" for sequence in range(24, 40)
    ]
