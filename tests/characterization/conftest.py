"""Shared fixtures for characterization tests.

Extends ``tests/conftest.py`` (``isolated_workspace`` + ``store``) with a
``scripted_llm_factory`` that creates deterministic LLM doubles for
end-to-end coordinator/subagent/HITL loop tests.
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
