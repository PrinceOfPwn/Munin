"""Characterization tests for MuninAgent.respond() loop.

These tests document the *current* behavior of the respond() loop without
driving production code changes. They skip gracefully if munin imports fail.

respond() is synchronous and returns:
    {"content": str, "iterations": int, "tool_calls": list[dict]}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Guard import — skip entire module if munin is not importable
# ---------------------------------------------------------------------------
munin_agent_mod = pytest.importorskip("munin.core.munin_agent")
MuninAgent = munin_agent_mod.MuninAgent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_text_response(content: str) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": None,
                },
                "finish_reason": "stop",
            }
        ]
    }


def _make_tool_response(tool_name: str, arguments: dict[str, Any], call_id: str = "call_x01") -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }


def _make_agent(tmp_path: Path, llm_responses: list[dict[str, Any]], extra_tools: dict[str, Any] | None = None) -> MuninAgent:
    """Build a MuninAgent with mocked LLM and a minimal file system."""
    config_mod = pytest.importorskip("munin.mcp.config")
    shared_mod = pytest.importorskip("munin.mcp.shared_state")

    Settings = config_mod.Settings
    SharedStateStore = shared_mod.SharedStateStore

    soul_dir = tmp_path / "soul"
    data_dir = tmp_path / "data"
    soul_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings(
        workspace_root=tmp_path,
        default_timeout=30,
        max_output_chars=8000,
        expected_egress_ip="",
        forbidden_egress_ip="",
        route_probe_ip="1.1.1.1",
        job_workers=1,
        github_token="",
        nvd_api_key="",
        munin_data_path=data_dir,
        munin_soul_path=soul_dir,
    )

    call_index = {"i": 0}

    def fake_chat(*args: Any, **kwargs: Any) -> dict[str, Any]:
        idx = call_index["i"]
        call_index["i"] += 1
        if idx < len(llm_responses):
            return llm_responses[idx]
        return _make_text_response("(done)")

    agent = MuninAgent.__new__(MuninAgent)
    agent.settings = settings
    agent.state = SharedStateStore(settings)

    # Stub out SoulManager so no soul files need to exist
    soul_stub = MagicMock()
    soul_stub.as_system_prompt.return_value = "You are Munin."
    agent.soul = soul_stub

    # Stub Memory
    memory_stub = MagicMock()
    memory_stub.summarize_for_prompt.return_value = ""
    memory_stub.known_tools.return_value = []
    memory_stub.log_step.return_value = None
    agent.memory = memory_stub

    # Stub Orchestrator
    agent.orchestrator = MagicMock()

    # Stub LLM
    llm_stub = MagicMock()
    llm_stub.chat.side_effect = fake_chat
    agent.llm = llm_stub

    # Patch _current_catalog to return only safe fake tools
    def _fake_catalog() -> dict[str, Any]:
        base: dict[str, Any] = {}
        if extra_tools:
            base.update(extra_tools)
        return base

    agent._current_catalog = _fake_catalog  # type: ignore[method-assign]

    return agent


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_respond_emits_tool_calls_log(tmp_path: Path) -> None:
    """respond() returns a dict with a non-empty tool_calls list when LLM calls a tool."""
    def fake_echo(message: str) -> dict[str, Any]:
        return {"ok": True, "result": message, "summary": "echoed"}

    agent = _make_agent(
        tmp_path,
        llm_responses=[
            _make_tool_response("fake_echo", {"message": "ping"}),
            _make_text_response("done"),
        ],
        extra_tools={"fake_echo": fake_echo},
    )
    result = agent.respond("ping pong")

    assert "tool_calls" in result, "respond() must include tool_calls key"
    assert isinstance(result["tool_calls"], list)
    assert len(result["tool_calls"]) >= 1
    assert result["tool_calls"][0]["name"] == "fake_echo"


def test_respond_stop_reason_end_turn(tmp_path: Path) -> None:
    """When LLM returns no tool calls, respond() stops and includes final text."""
    agent = _make_agent(
        tmp_path,
        llm_responses=[_make_text_response("Hello, I can help with that.")],
    )
    result = agent.respond("hello")

    assert "content" in result
    assert result["content"] == "Hello, I can help with that."
    # No tool calls expected
    assert result["tool_calls"] == []


def test_respond_stop_reason_tool_use(tmp_path: Path) -> None:
    """When LLM returns a tool call, respond() processes it and continues the loop."""
    call_log: list[str] = []

    def spy_tool(x: str) -> dict[str, Any]:
        call_log.append(x)
        return {"ok": True, "result": x, "summary": "spy called"}

    agent = _make_agent(
        tmp_path,
        llm_responses=[
            _make_tool_response("spy_tool", {"x": "secret"}),
            _make_text_response("spy result processed"),
        ],
        extra_tools={"spy_tool": spy_tool},
    )
    result = agent.respond("run spy")

    # Tool should have been invoked
    assert "secret" in call_log, "spy_tool should have been called with x='secret'"
    # Final content should come from the second LLM response
    assert result["content"] == "spy result processed"
    assert result["iterations"] >= 2


def test_respond_event_stream_ordering(tmp_path: Path) -> None:
    """tool_calls log entries appear in invocation order (not reversed or shuffled)."""
    call_order: list[str] = []

    def tool_a(v: str) -> dict[str, Any]:
        call_order.append("a")
        return {"ok": True, "result": v, "summary": "a done"}

    def tool_b(v: str) -> dict[str, Any]:
        call_order.append("b")
        return {"ok": True, "result": v, "summary": "b done"}

    # Two separate iterations: first call tool_a, then tool_b
    agent = _make_agent(
        tmp_path,
        llm_responses=[
            _make_tool_response("tool_a", {"v": "1"}, call_id="call_001"),
            _make_tool_response("tool_b", {"v": "2"}, call_id="call_002"),
            _make_text_response("finished"),
        ],
        extra_tools={"tool_a": tool_a, "tool_b": tool_b},
    )
    result = agent.respond("run a then b")

    names = [tc["name"] for tc in result["tool_calls"]]
    assert names.index("tool_a") < names.index("tool_b"), (
        "tool_a should appear before tool_b in tool_calls log"
    )
