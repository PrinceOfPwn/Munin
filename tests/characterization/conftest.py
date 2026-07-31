"""Shared fixtures for characterization tests.

Extends ``tests/conftest.py`` (``isolated_workspace`` + ``store``) with a
``scripted_llm_factory`` that creates deterministic LLM doubles for
end-to-end coordinator/subagent/HITL loop tests, and a
``fake_chat_model_factory`` for build-only tests that need a
``BaseChatModel`` compatible with ``langchain.agents.create_agent`` /
``deepagents.create_deep_agent`` without touching the network.
"""

from __future__ import annotations

from typing import Any

import pytest


class _ScriptedLLM:
    """Plays back a pre-written list of OpenAI-shape chat completions in order.

    Reusable across coord / subagent / hitl parity tests so the agent loop runs
    end-to-end without a real provider.  Asserts shape::

        chat(messages: list[dict], tools: list[dict], temperature: float) -> dict
    """

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = responses
        self.calls = 0

    def chat(self, *, messages: list, tools: list, temperature: float, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        return self._responses[(self.calls - 1) % len(self._responses)]


@pytest.fixture
def scripted_llm_factory():
    """Factory that creates ``_ScriptedLLM`` instances from a response list."""
    return lambda responses: _ScriptedLLM(responses)


@pytest.fixture
def fake_chat_model_factory():
    """Factory for a tool-bindable ``BaseChatModel`` mock.

    Idiomatic langchain pattern for build-only tests: subclass
    ``BaseChatModel``, override ``bind_tools`` to return ``self`` (the base
    raises ``NotImplementedError``), implement ``_generate`` to immediately
    return an empty AIMessage. ``create_agent`` / ``create_deep_agent`` /
    ``build_swarm`` need the object graph to assemble — the agent never runs
    in these tests, so the model never emits real content.

    Use this for tests that only need ``SubagentFactory.create_subagent`` /
    ``build_swarm`` to construct an object graph offline — never for tests
    asserting model reasoning (those should use ``scripted_llm_factory``
    against the legacy ``MuninAgent.respond`` path, or a provider double).
    """
    from langchain_core.language_models.chat_models import (  # noqa: PLC0415
        BaseChatModel,
    )
    from langchain_core.messages import AIMessage  # noqa: PLC0415
    from langchain_core.outputs import (  # noqa: PLC0415
        ChatGeneration,
        ChatResult,
    )

    class _ToolBindingFakeChatModel(BaseChatModel):
        """Minimal ``BaseChatModel`` with no-op ``bind_tools`` / ``_generate``."""

        def bind_tools(self, tools, **kwargs):  # noqa: ARG002
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ARG002
            message = AIMessage(content="")
            return ChatResult(generations=[ChatGeneration(message=message)])

        @property
        def _llm_type(self) -> str:  # pragma: no cover - BaseChatModel abstract stub
            return "fake-tool-binding"

    return _ToolBindingFakeChatModel
