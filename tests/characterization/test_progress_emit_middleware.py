"""Characterization tests for ProgressEmitMiddleware."""
import pytest
from unittest.mock import MagicMock

pytest.importorskip("munin.core.middleware")

from munin.core.middleware.progress_emit import ProgressEmitMiddleware


def collect_events(events_input):
    collected = []
    middleware = ProgressEmitMiddleware(
        progress_sink=collected.append,
        run_id="run-test"
    )

    async def _run():
        async def _stream():
            for e in events_input:
                yield e
        async for _ in middleware.wrap_stream(_stream()):
            pass

    import asyncio
    asyncio.run(_run())
    return collected


def test_chat_model_stream_emits_reasoning():
    from langchain_core.messages import AIMessageChunk
    chunk = AIMessageChunk(content="thinking about target")
    events = [{"event": "on_chat_model_stream", "name": "ChatOpenAI", "data": {"chunk": chunk}}]

    result = collect_events(events)
    assert len(result) == 1
    assert result[0]["kind"] == "reasoning"
    assert "thinking about target" in result[0]["text"]


def test_tool_start_emits_tool_intent():
    events = [{
        "event": "on_tool_start",
        "name": "port_scan",
        "run_id": "tool-run-1",
        "data": {"input": {"host": "10.0.0.1", "ports": "80,443"}}
    }]

    result = collect_events(events)
    assert len(result) == 1
    assert result[0]["kind"] == "tool_intent"
    assert result[0]["tool_name"] == "port_scan"
    assert result[0]["input"]["host"] == "10.0.0.1"


def test_tool_end_emits_tool_result():
    events = [{
        "event": "on_tool_end",
        "name": "port_scan",
        "run_id": "tool-run-1",
        "data": {"output": "80/tcp open, 443/tcp open"}
    }]

    result = collect_events(events)
    assert len(result) == 1
    assert result[0]["kind"] == "tool_result"
    assert "80/tcp" in result[0]["output"]


def test_chain_end_emits_run_state_completed():
    events = [{"event": "on_chain_end", "name": "LangGraph", "data": {}}]

    result = collect_events(events)
    assert any(e["kind"] == "run_state" and e["state"] == "completed" for e in result)


def test_unknown_events_ignored():
    events = [{"event": "on_something_unknown", "name": "X", "data": {}}]
    result = collect_events(events)
    assert result == []
