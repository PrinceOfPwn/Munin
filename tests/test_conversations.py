from __future__ import annotations


def test_conversation_service_replays_prior_turns_and_persists_artifacts(store):
    from munin.core.conversations import ConversationService

    service = ConversationService(store)
    first = service.prepare_turn(conversation_id="conv_persistence_demo", user_message="Find LDAP and Apache")
    assert first.history == []

    artifact = chr(96) * 3 + "python\nprint('report')\n" + chr(96) * 3
    assistant, artifacts = service.complete_turn(
        conversation_id=first.conversation_id,
        content="I found both services.\n\n" + artifact,
        tool_calls=[{"name": "ldap_who_am_i", "ok": True, "summary": "bound"}],
        stop_reason="final_answer",
        iterations=2,
    )

    assert assistant["role"] == "assistant"
    assert artifacts[0]["filename"].endswith(".py")
    assert artifacts[0]["content"] == "print('report')\n"

    second = service.prepare_turn(
        conversation_id=first.conversation_id,
        user_message="Do it now",
    )
    transcript = "\n".join(item["content"] for item in second.history)

    assert second.conversation_id == first.conversation_id
    assert "Find LDAP and Apache" in transcript
    assert "I found both services." in transcript

    record = store.conversation_get(conversation_id=first.conversation_id)
    assert record is not None
    assert [message["role"] for message in record["messages"]] == ["user", "assistant", "user"]
    assert record["artifacts"][0]["language"] == "python"


def test_conversation_tool_refuses_local_sqlite(store, monkeypatch):
    from munin.mcp.tools import munin_tools

    monkeypatch.setattr(munin_tools, "STATE", store)
    result = munin_tools.conversation_list()

    assert result["ok"] is False
    assert result["error"]["code"] == "turso_required"


def test_munin_chat_keeps_conversation_history_for_the_llm(store, monkeypatch):
    from dataclasses import replace

    from munin.core import munin_agent
    from munin.mcp.tools import munin_tools

    seen_messages: list[list[dict]] = []

    class RecordingLlm:
        def chat(self, **kwargs):
            seen_messages.append([dict(message) for message in kwargs["messages"]])
            return {"choices": [{"message": {"role": "assistant", "content": "continuing the investigation"}}]}

    real_agent = munin_agent.MuninAgent

    class RecordingAgent:
        def __init__(self, settings):
            self._agent = object.__new__(real_agent)
            self._agent.llm = RecordingLlm()
            self._agent.memory = type("Memory", (), {"log_step": staticmethod(lambda **kwargs: None)})()
            self._agent._system_prompt = lambda: "system"
            self._agent._current_catalog = lambda: {}

        def respond(self, *args, **kwargs):
            return self._agent.respond(*args, **kwargs)

    settings = replace(
        store.settings,
        llm_base_url="https://llm.invalid/v1",
        llm_api_key="configured",
        llm_model="test",
    )
    monkeypatch.setattr(munin_agent, "MuninAgent", RecordingAgent)
    monkeypatch.setattr(munin_tools, "STATE", store)
    monkeypatch.setattr(munin_tools, "_get_settings", lambda: settings)
    # The public MCP tool enforces Turso. Unit tests use the isolated SQLite
    # harness only to exercise the conversation protocol itself.
    monkeypatch.setattr(munin_tools, "_conversation_backend_error", lambda _settings: None)

    first = munin_tools.munin_chat("Find LDAP and Apache", conversation_id="conv_llm_context")
    second = munin_tools.munin_chat("Do it now", conversation_id="conv_llm_context")

    assert first["ok"] is True
    assert second["ok"] is True
    second_messages = seen_messages[-1]
    assert any(message["content"] == "Find LDAP and Apache" for message in second_messages)
    assert any(message["content"] == "continuing the investigation" for message in second_messages)
    assert second_messages[-1]["content"] == "Do it now"
