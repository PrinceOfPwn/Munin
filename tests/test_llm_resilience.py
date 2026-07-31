from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import httpx
from openai import APIStatusError


def _server_error() -> APIStatusError:
    request = httpx.Request("POST", "https://llm.example/v1/chat/completions")
    response = httpx.Response(500, request=request, json={"error": "temporary outage"})
    return APIStatusError("temporary outage", response=response, body={"error": "temporary outage"})


def test_llm_client_retries_transient_500_with_observable_backoff(store, monkeypatch):
    from munin.core import llm_client
    from munin.core.llm_client import LLMClient

    settings = replace(
        store.settings,
        llm_base_url="https://llm.example/v1",
        llm_api_key="configured",
        llm_model="test",
        llm_retry_attempts=3,
        llm_retry_base_delay=5.0,
        llm_retry_max_delay=60.0,
    )
    client = LLMClient(settings)
    calls = {"count": 0}

    def create(**_kwargs):
        calls["count"] += 1
        if calls["count"] < 3:
            raise _server_error()
        return SimpleNamespace(model_dump=lambda: {"choices": [{"message": {"content": "recovered"}}]})

    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(llm_client.time, "sleep", lambda _delay: None)
    events: list[dict] = []

    result = client.chat(messages=[{"role": "user", "content": "hello"}], on_retry=events.append)

    assert result["choices"][0]["message"]["content"] == "recovered"
    assert calls["count"] == 3
    assert [event["retry_in_seconds"] for event in events] == [5.0, 10.0]
    assert all(event["reason"] == "HTTP 500" for event in events)


def test_munin_chat_default_iteration_budget_is_unbounded(store, monkeypatch):
    from munin.core import munin_agent
    from munin.mcp.tools import munin_tools

    captured: dict = {}

    class FinalAgent:
        def __init__(self, _settings):
            pass

        def respond(self, *_args, **kwargs):
            captured.update(kwargs)
            return {"content": "done", "tool_calls": [], "iterations": 1, "stop_reason": "final_answer"}

    settings = replace(
        store.settings,
        llm_base_url="https://llm.example/v1",
        llm_api_key="configured",
        llm_model="test",
    )
    monkeypatch.setattr(munin_agent, "MuninAgent", FinalAgent)
    monkeypatch.setattr(munin_tools, "STATE", store)
    monkeypatch.setattr(munin_tools, "_get_settings", lambda: settings)
    monkeypatch.setattr(munin_tools, "_conversation_backend_error", lambda _settings: None)

    result = munin_tools.munin_chat("evaluate LDAP", conversation_id="unbounded_budget")

    assert result["ok"] is True
    assert captured["max_iterations"] is None
