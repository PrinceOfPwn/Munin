from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace


def _chunk(delta, *, finish_reason=None):
    return SimpleNamespace(
        model_dump=lambda: {
            "id": "chat-stream-1",
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish_reason,
                }
            ],
        }
    )


def test_llm_client_streams_visible_reasoning_and_answer(store):
    from munin.core.llm_client import LLMClient
    from munin.core.llm_stream import llm_stream_scope

    settings = replace(
        store.settings,
        llm_base_url="https://llm.example/v1",
        llm_api_key="configured",
        llm_model="test-model",
    )
    client = LLMClient(settings)
    captured_kwargs = {}

    def create(**kwargs):
        captured_kwargs.update(kwargs)
        return iter(
            [
                _chunk({"reasoning_content": "Checking context. "}),
                _chunk({"content": "Hello "}),
                _chunk({"content": "operator."}, finish_reason="stop"),
            ]
        )

    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    events = []
    with llm_stream_scope(events.append):
        result = client.chat(messages=[{"role": "user", "content": "hello"}])

    assert captured_kwargs["stream"] is True
    assert result["choices"][0]["message"]["content"] == "Hello operator."
    assert result["choices"][0]["message"]["reasoning_content"] == "Checking context. "
    assert [event["stage"] for event in events] == [
        "model_stream_started",
        "provider_reasoning_delta",
        "assistant_delta",
        "assistant_delta",
        "model_stream_completed",
    ]


def test_llm_client_reconstructs_streamed_tool_calls(store):
    from munin.core.llm_client import LLMClient
    from munin.core.llm_stream import llm_stream_scope

    settings = replace(
        store.settings,
        llm_base_url="https://llm.example/v1",
        llm_api_key="configured",
        llm_model="test-model",
    )
    client = LLMClient(settings)

    def create(**_kwargs):
        return iter(
            [
                _chunk(
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "ldap_", "arguments": '{"base_dn":'},
                            }
                        ]
                    }
                ),
                _chunk(
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {"name": "search", "arguments": '"dc=example,dc=com"}'},
                            }
                        ]
                    },
                    finish_reason="tool_calls",
                ),
            ]
        )

    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with llm_stream_scope(lambda _event: None):
        result = client.chat(messages=[{"role": "user", "content": "search"}], tools=[{"type": "function"}])

    call = result["choices"][0]["message"]["tool_calls"][0]
    assert call["id"] == "call_1"
    assert call["function"]["name"] == "ldap_search"
    assert call["function"]["arguments"] == '{"base_dn":"dc=example,dc=com"}'
