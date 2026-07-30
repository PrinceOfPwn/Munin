"""Characterization tests for MuninAgent.respond() event stream and stop reasons.

Asserts CURRENT behaviour at munin/core/munin_agent.py:289-637.
"""

from __future__ import annotations

import json


def _make_tool_call(name: str, args: dict, call_id: str = "call_1") -> dict:
    """Build an OpenAI-shape tool_calls entry."""
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def _make_completion(*, content: str = "", tool_calls: list | None = None) -> dict:
    """Build an OpenAI-shape chat completion dict."""
    msg: dict = {"role": "assistant", "content": content or None}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg}]}


def test_stop_reason_final_answer(isolated_workspace, scripted_llm_factory):
    from munin.core.munin_agent import MuninAgent

    llm = scripted_llm_factory([_make_completion(content="done")])
    agent = MuninAgent.__new__(MuninAgent)
    from munin.mcp.config import get_settings
    from munin.mcp.shared_state import SharedStateStore
    agent.settings = get_settings()
    agent.state = SharedStateStore(agent.settings)
    from munin.core.memory import Memory
    from munin.core.soul import SoulManager
    agent.memory = Memory(agent.state)
    agent.soul = SoulManager(agent.settings.munin_soul_path, agent.settings.munin_data_path)
    from munin.core.orchestrator import Orchestrator
    agent.orchestrator = Orchestrator(agent.state)
    agent.llm = llm

    result = agent.respond("hello")
    assert result["stop_reason"] == "final_answer"
    assert result["content"] == "done"


def test_stop_reason_max_iterations(isolated_workspace, scripted_llm_factory):
    from munin.core.munin_agent import MuninAgent

    # 2 tool-call responses — loop runs 2 iterations, then for-else sets max_iterations
    responses = [
        _make_completion(tool_calls=[_make_tool_call("ldap_search", {"filter": "a"}, "c1")]),
        _make_completion(tool_calls=[_make_tool_call("ldap_search", {"filter": "b"}, "c2")]),
        _make_completion(content="final"),  # never reached — loop exhausts at step 2
    ]
    llm = scripted_llm_factory(responses)
    agent = MuninAgent.__new__(MuninAgent)
    from munin.mcp.config import get_settings
    from munin.mcp.shared_state import SharedStateStore
    agent.settings = get_settings()
    agent.state = SharedStateStore(agent.settings)
    from munin.core.memory import Memory
    from munin.core.soul import SoulManager
    agent.memory = Memory(agent.state)
    agent.soul = SoulManager(agent.settings.munin_soul_path, agent.settings.munin_data_path)
    from munin.core.orchestrator import Orchestrator
    agent.orchestrator = Orchestrator(agent.state)
    agent.llm = llm

    result = agent.respond("hello", max_iterations=2)
    assert result["stop_reason"] == "max_iterations"


def test_stop_reason_repetition_detected(isolated_workspace, scripted_llm_factory):
    """Repetition guard trips when the same (tool, args) fingerprint fills
    WINDOW_SIZE=6 with fewer than MIN_UNIQUE=3 distinct entries, the nudge
    is injected once, and a second trip aborts with repetition_detected.
    """
    from munin.core.munin_agent import MuninAgent

    # Same tool+args every time — triggers repetition quickly
    same_tc = [_make_tool_call("ldap_search", {"filter": "(objectClass=*)"}, "c1")]
    responses = [_make_completion(tool_calls=same_tc) for _ in range(10)]
    llm = scripted_llm_factory(responses)

    agent = MuninAgent.__new__(MuninAgent)
    from munin.mcp.config import get_settings
    from munin.mcp.shared_state import SharedStateStore
    agent.settings = get_settings()
    agent.state = SharedStateStore(agent.settings)
    from munin.core.memory import Memory
    from munin.core.soul import SoulManager
    agent.memory = Memory(agent.state)
    agent.soul = SoulManager(agent.settings.munin_soul_path, agent.settings.munin_data_path)
    from munin.core.orchestrator import Orchestrator
    agent.orchestrator = Orchestrator(agent.state)
    agent.llm = llm

    # Capture the messages list via the LLM call to verify nudge appears
    captured_messages: list = []

    def capturing_chat(**kwargs):
        captured_messages.clear()
        captured_messages.extend(kwargs.get("messages", []))
        return llm.chat(**kwargs)

    agent.llm = type("CapturingLLM", (), {"chat": staticmethod(capturing_chat), "calls": property(lambda self: llm.calls)})()

    # The repetition guard at munin/core/munin_agent.py:565-610 trips in two
    # stages: WINDOW_SIZE=6 calls fill the window → nudge injected +
    # `recent_calls.clear()` (iter ~6), then a SECOND WINDOW_SIZE=6 identical
    # calls after the nudge → `stop_reason = "repetition_detected"` (iter ~12).
    # Setting `max_iterations=10` under-runs the budget so the for-else fallback
    # at munin/core/munin_agent.py:611-613 trips first with
    # `stop_reason = "max_iterations"` instead. Headroom: max_iterations=20 lets
    # the second trip trigger cleanly (worst-case budget needed is 6+6+1=13).
    result = agent.respond("loop test", max_iterations=20)
    assert result["stop_reason"] == "repetition_detected"

    # Verify the nudge system message appeared exactly once
    nudge_messages = [
        m for m in captured_messages
        if m.get("role") == "system" and "NOTICE: over your last" in m.get("content", "")
    ]
    assert len(nudge_messages) == 1, f"expected exactly 1 nudge, got {len(nudge_messages)}"


def test_tool_calls_log_ordering(isolated_workspace, scripted_llm_factory):
    from munin.core.munin_agent import MuninAgent

    responses = [
        _make_completion(tool_calls=[_make_tool_call("tool_alpha", {"x": 1}, "c1")]),
        _make_completion(tool_calls=[_make_tool_call("tool_beta", {"y": 2}, "c2")]),
        _make_completion(content="all done"),
    ]
    llm = scripted_llm_factory(responses)
    agent = MuninAgent.__new__(MuninAgent)
    from munin.mcp.config import get_settings
    from munin.mcp.shared_state import SharedStateStore
    agent.settings = get_settings()
    agent.state = SharedStateStore(agent.settings)
    from munin.core.memory import Memory
    from munin.core.soul import SoulManager
    agent.memory = Memory(agent.state)
    agent.soul = SoulManager(agent.settings.munin_soul_path, agent.settings.munin_data_path)
    from munin.core.orchestrator import Orchestrator
    agent.orchestrator = Orchestrator(agent.state)
    agent.llm = llm

    result = agent.respond("go")
    names = [c["name"] for c in result["tool_calls"]]
    assert names == ["tool_alpha", "tool_beta"]
    for entry in result["tool_calls"]:
        assert "name" in entry
        assert "arguments" in entry or "args" in entry
        assert "elapsed_ms" in entry
        assert "result" in entry or "error" in entry


def test_progress_event_sequence(isolated_workspace, scripted_llm_factory):
    from munin.core.munin_agent import MuninAgent

    responses = [
        _make_completion(tool_calls=[_make_tool_call("tool_a", {}, "c1")]),
        _make_completion(content="final"),
    ]
    llm = scripted_llm_factory(responses)
    agent = MuninAgent.__new__(MuninAgent)
    from munin.mcp.config import get_settings
    from munin.mcp.shared_state import SharedStateStore
    agent.settings = get_settings()
    agent.state = SharedStateStore(agent.settings)
    from munin.core.memory import Memory
    from munin.core.soul import SoulManager
    agent.memory = Memory(agent.state)
    agent.soul = SoulManager(agent.settings.munin_soul_path, agent.settings.munin_data_path)
    from munin.core.orchestrator import Orchestrator
    agent.orchestrator = Orchestrator(agent.state)
    agent.llm = llm

    events: list[dict] = []
    result = agent.respond("test", progress=events.append)

    stages = [e["stage"] for e in events]

    # reasoning must appear
    assert "reasoning" in stages
    # tool_start before tool_result for each tool call
    tool_starts = [i for i, s in enumerate(stages) if s == "tool_start"]
    tool_results = [i for i, s in enumerate(stages) if s == "tool_result"]
    assert len(tool_starts) == len(tool_results)
    for ts, tr in zip(tool_starts, tool_results):
        assert ts < tr
    # completed exactly once at the end
    assert stages.count("completed") == 1
    assert stages[-1] == "completed"


def test_operator_guidance_format(isolated_workspace, scripted_llm_factory):
    from munin.core.munin_agent import MuninAgent

    responses = [
        _make_completion(tool_calls=[_make_tool_call("tool_a", {}, "c1")]),
        _make_completion(tool_calls=[_make_tool_call("tool_b", {}, "c2")]),
        _make_completion(content="done"),
    ]
    llm = scripted_llm_factory(responses)
    agent = MuninAgent.__new__(MuninAgent)
    from munin.mcp.config import get_settings
    from munin.mcp.shared_state import SharedStateStore
    agent.settings = get_settings()
    agent.state = SharedStateStore(agent.settings)
    from munin.core.memory import Memory
    from munin.core.soul import SoulManager
    agent.memory = Memory(agent.state)
    agent.soul = SoulManager(agent.settings.munin_soul_path, agent.settings.munin_data_path)
    from munin.core.orchestrator import Orchestrator
    agent.orchestrator = Orchestrator(agent.state)
    agent.llm = llm

    captured: list = []

    def capturing_chat(**kwargs):
        captured.clear()
        captured.extend(kwargs.get("messages", []))
        return llm.chat(**kwargs)

    agent.llm = type("CapturingLLM", (), {"chat": staticmethod(capturing_chat)})()

    def hook(step):
        if step == 2:
            return "<operator_guidance>Prioritize LDAP.</operator_guidance>"
        return None

    result = agent.respond("go", pre_iteration_hook=hook)
    guidance_msgs = [
        m for m in captured
        if m.get("role") == "system" and "<operator_guidance>" in m.get("content", "")
    ]
    # Hook fires at step 2 (iteration index 1), so exactly one guidance message
    assert len(guidance_msgs) == 1
    assert "Prioritize LDAP." in guidance_msgs[0]["content"]
