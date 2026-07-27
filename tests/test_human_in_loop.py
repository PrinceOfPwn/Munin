from __future__ import annotations


class _CapturingLLM:
    def __init__(self) -> None:
        self.messages = None
        self.calls = 0

    def chat(self, *, messages, tools, temperature):
        self.calls += 1
        self.messages = list(messages)
        return {"choices": [{"message": {"role": "assistant", "content": "done"}}]}


def _agent(store, llm):
    from munin.subagents.base import ReActSubagentBase

    class TestAgent(ReActSubagentBase):
        name = "live_agent"
        role = "test"
        allowed_tools = set()
        max_iterations = 2

    return TestAgent(store, llm=llm)


def test_live_human_guidance_enters_next_model_context(store):
    llm = _CapturingLLM()
    agent = _agent(store, llm)
    store.post_message(
        sender_agent="human",
        recipient_agent="live_agent",
        subject="operator",
        message_type="HUMAN",
        body="Prioritize the LDAP evidence.",
        related_task_id=None,
        related_target_ip="",
        metadata_json="{}",
    )

    result = agent.handle_task({"prompt": "Investigate."})
    assert result["ok"] is True
    assert any("Prioritize the LDAP evidence." in item["content"] for item in llm.messages)
    events = store.episodic_query(agent="live_agent", action="human_guidance")
    assert events[0]["input"]["sender"] == "human"


def test_control_message_cancels_before_model_call(store):
    llm = _CapturingLLM()
    agent = _agent(store, llm)
    store.post_message(
        sender_agent="human",
        recipient_agent="live_agent",
        subject="operator",
        message_type="CONTROL",
        body="cancel",
        related_task_id=None,
        related_target_ip="",
        metadata_json="{}",
    )

    result = agent.handle_task({"prompt": "Investigate."})
    assert result["ok"] is False
    assert result["data"]["stop_reason"] == "human_cancelled"
    assert llm.calls == 0


def test_multiple_guidance_messages_keep_chronological_order(store):
    llm = _CapturingLLM()
    agent = _agent(store, llm)
    for body in ("First inspect X.", "Then avoid Y."):
        store.post_message(
            sender_agent="human",
            recipient_agent="live_agent",
            subject="operator",
            message_type="HUMAN",
            body=body,
            related_task_id=None,
            related_target_ip="",
            metadata_json="{}",
        )

    result = agent.handle_task({"prompt": "Investigate."})

    assert result["ok"] is True
    guidance = [
        item["content"]
        for item in llm.messages
        if item.get("role") == "user" and item["content"].startswith("[Live guidance")
    ]
    assert "First inspect X." in guidance[0]
    assert "Then avoid Y." in guidance[1]
