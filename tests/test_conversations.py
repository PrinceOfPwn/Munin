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


# test_munin_chat_keeps_conversation_history_for_the_llm was removed: it
# characterised the pre-issue-#9 munin_chat path that dispatched via
# munin.core.munin_agent.MuninAgent.respond(). The supervisor_runner /
# Deep Agents runtime replaced that path on this PR; conversation history
# propagation is now exercised by the runtime_adapter + supervisor integration
# tests under tests/characterization/.
