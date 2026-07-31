"""Characterization tests for ProgressEmitMiddleware + runtime event translation.

Two channels, one envelope format:
* middleware awrap_tool_call → tool_intent / tool_result / tool_failed
* runtime_adapter.translate_event → reasoning / run_state (text + lifecycle)
"""
import pytest
from unittest.mock import MagicMock

pytest.importorskip("munin.core.middleware")

from munin.core.middleware.progress_emit import ProgressEmitMiddleware
from munin.core.runtime_adapter import translate_event


def _request(name: str, args: dict, call_id: str = "call-1"):
    req = MagicMock()
    req.tool_call = {"name": name, "args": args, "id": call_id}
    return req


@pytest.mark.asyncio
async def test_wrap_tool_call_emits_intent_and_result():
    collected: list[dict] = []
    middleware = ProgressEmitMiddleware(progress_sink=collected.append, run_id="run-t")

    async def handler(request):
        return "80/tcp open"

    result = await middleware.awrap_tool_call(
        _request("port_scan", {"host": "10.0.0.1"}), handler
    )
    assert result == "80/tcp open"
    kinds = [e["kind"] for e in collected]
    assert kinds == ["tool_intent", "tool_result"]
    assert collected[0]["tool_name"] == "port_scan"
    assert collected[0]["input"]["host"] == "10.0.0.1"
    assert "80/tcp" in collected[1]["output"]


@pytest.mark.asyncio
async def test_wrap_tool_call_emits_failure_and_reraises():
    collected: list[dict] = []
    middleware = ProgressEmitMiddleware(progress_sink=collected.append, run_id="run-t")

    async def handler(request):
        raise RuntimeError("nmap died")

    with pytest.raises(RuntimeError):
        await middleware.awrap_tool_call(_request("port_scan", {}), handler)
    kinds = [e["kind"] for e in collected]
    assert kinds == ["tool_intent", "tool_failed"]
    assert "nmap died" in collected[1]["error"]


# -- translate_event (runtime_adapter) ---------------------------------------


def test_chat_model_stream_translates_to_reasoning():
    from langchain_core.messages import AIMessageChunk

    event = {
        "event": "on_chat_model_stream",
        "name": "ChatOpenAI",
        "data": {"chunk": AIMessageChunk(content="thinking about target")},
    }
    envelope = translate_event(event, run_id="run-t")
    assert envelope["kind"] == "reasoning"
    assert "thinking about target" in envelope["text"]


def test_root_chain_end_translates_to_run_state_with_content():
    from langchain_core.messages import AIMessage

    event = {
        "event": "on_chain_end",
        "name": "LangGraph",
        "data": {"output": {"messages": [AIMessage(content="final answer")]}},
    }
    envelope = translate_event(event, run_id="run-t")
    assert envelope["kind"] == "run_state"
    assert envelope["state"] == "completed"
    assert envelope["content"] == "final answer"


def test_unknown_events_ignored():
    assert translate_event({"event": "on_something_unknown", "name": "X", "data": {}}, run_id="r") is None
