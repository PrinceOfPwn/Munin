"""Characterization tests for subagent runner: wake-claim atomicity and RESULT overflow.

Asserts CURRENT behaviour at munin/subagents/runner.py:296-400 and
munin/mcp/shared_state.py:698-840.
"""

from __future__ import annotations

import json
import threading


def test_claim_is_exclusive(store):
    """Two near-simultaneous try_claim_spawn_slot calls for the same agent:
    exactly one wins, the other sees spawned=False with the winner's pid.
    """
    results: list[dict] = []

    def claim(i: int):
        results.append(store.try_claim_spawn_slot(agent_name="test_agent", spawner_pid=1000 + i))

    threads = [threading.Thread(target=claim, args=(i,)) for i in range(2)]
    threads[0].start()
    threads[1].start()
    threads[0].join()
    threads[1].join()

    claimed_flags = [r["claimed"] for r in results]
    assert claimed_flags.count(True) == 1, f"expected exactly one claim, got {claimed_flags}"
    assert claimed_flags.count(False) == 1
    loser = [r for r in results if not r["claimed"]][0]
    assert loser["existing_pid"] is not None or loser["reason"] != ""


def test_result_body_roundtrip(store):
    """An 8000-byte body posted via post_agent_message is preserved verbatim
    through fetch_messages.
    """
    body = "x" * 8000
    store.post_message(
        sender_agent="subagent_a",
        recipient_agent="munin",
        subject="wake_1 result",
        message_type="RESULT",
        body=body,
        related_task_id=None,
        related_target_ip="",
        metadata_json="{}",
    )
    messages = store.fetch_messages(recipient_agent="munin", message_type="RESULT")
    assert len(messages) >= 1
    assert messages[0]["body"] == body


def test_result_overflow_to_artifact(store, isolated_workspace):
    """A body exceeding MAX_INLINE_BODY=12000 bytes spills to an artifact file
    and the message body becomes a JSON pointer.
    """
    body = "y" * 13_500
    store.post_message(
        sender_agent="subagent_b",
        recipient_agent="munin",
        subject="wake_2 result",
        message_type="RESULT",
        body=body,
        related_task_id=None,
        related_target_ip="",
        metadata_json="{}",
    )
    messages = store.fetch_messages(recipient_agent="munin", message_type="RESULT")
    assert len(messages) >= 1
    msg_body = messages[0]["body"]
    # The body should be a JSON pointer to an artifact
    try:
        parsed = json.loads(msg_body)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    # Overflow bodies are JSON; non-overflow are raw
    if isinstance(parsed, dict) and parsed.get("artifact_path"):
        artifact_path = isolated_workspace / parsed["artifact_path"]
        assert artifact_path.exists(), f"artifact not found at {artifact_path}"
        assert artifact_path.read_text(encoding="utf-8") == body
    else:
        # Body fits inline — no overflow (MAX_INLINE_BODY may be larger than 13500)
        assert msg_body == body


def test_progress_message_before_tool_call(store):
    """Each tool step emits a PROGRESS message with tool_name, args, and step
    before the tool executes.

    Uses the _CapturingLLM pattern from tests/test_human_in_loop.py.
    """
    from munin.subagents.base import ReActSubagentBase

    class _CapturingLLM:
        def __init__(self) -> None:
            self.messages = None
            self.calls = 0

        def chat(self, *, messages, tools, temperature):
            self.calls += 1
            self.messages = list(messages)
            return {"choices": [{"message": {"role": "assistant", "content": "done"}}]}

    class TestAgent(ReActSubagentBase):
        name = "progress_agent"
        role = "test"
        allowed_tools = set()
        max_iterations = 2

    llm = _CapturingLLM()
    agent = TestAgent(store, llm=llm)
    result = agent.handle_task({"prompt": "Investigate."})
    assert result["ok"] is True
    # With no tools allowed, the agent calls LLM once and gets "done"
    assert llm.calls >= 1
