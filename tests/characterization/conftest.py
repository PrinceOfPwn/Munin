"""Shared fixtures for Munin characterization tests.

These fixtures capture *current* behavior without driving production code changes.
All fixtures are defensive — they skip gracefully if production modules are absent.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_text_response(content: str) -> dict[str, Any]:
    """Build a minimal OpenAI-format chat completion with no tool calls."""
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "model": "fake-model",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": None,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _make_tool_response(tool_name: str, arguments: dict[str, Any], call_id: str = "call_abc") -> dict[str, Any]:
    """Build a minimal OpenAI-format chat completion with one tool call."""
    return {
        "id": "chatcmpl-fake-tool",
        "object": "chat.completion",
        "model": "fake-model",
        "choices": [
            {
                "index": 0,
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
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
    }


class _CyclingLLMStub:
    """Deterministic LLM stub that cycles through a preset list of responses.

    Each call to `chat()` pops the next response from the queue.
    When the queue is exhausted, a text response with "(done)" is returned.
    """

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self._index = 0
        self.calls: list[dict[str, Any]] = []

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        self.calls.append({"messages": messages, "tools": tools})
        if self._index < len(self._responses):
            resp = self._responses[self._index]
            self._index += 1
            return resp
        return _make_text_response("(done)")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_llm_client() -> _CyclingLLMStub:
    """Deterministic LLM client that cycles through preset responses.

    Default: one tool_use call followed by a final text response.
    Tests can build their own stub by calling _CyclingLLMStub([...]) directly.
    """
    return _CyclingLLMStub(
        responses=[
            _make_tool_response("echo_tool", {"message": "hello"}),
            _make_text_response("Task complete."),
        ]
    )


@pytest.fixture
def fake_tool_catalog() -> dict[str, Any]:
    """Small catalog of deterministic fake tools.

    - echo_tool: returns the message back
    - add_tool: adds two numbers
    - state_only_tool: registered as state_only (no real callable needed — tested separately)
    - gen__example: a generated-prefix tool
    """

    def echo_tool(message: str) -> dict[str, Any]:
        return {"ok": True, "result": message, "summary": f"echoed: {message}"}

    def add_tool(a: float, b: float) -> dict[str, Any]:
        return {"ok": True, "result": a + b, "summary": f"{a} + {b} = {a + b}"}

    def gen__example(query: str) -> dict[str, Any]:
        return {"ok": True, "result": f"example result for {query}", "summary": "gen example ran"}

    return {
        "echo_tool": echo_tool,
        "add_tool": add_tool,
        "gen__example": gen__example,
    }


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Return a fresh SQLite file path inside tmp_path (file does not exist yet)."""
    return tmp_path / "test_shared_state.sqlite"


@pytest.fixture
def tmp_store(db_path: Path) -> Any:
    """Create a fresh SharedStateStore backed by a temp SQLite file.

    Skips the test if munin.mcp.shared_state cannot be imported.
    """
    munin_shared = pytest.importorskip("munin.mcp.shared_state")
    munin_config = pytest.importorskip("munin.mcp.config")

    Settings = munin_config.Settings
    SharedStateStore = munin_shared.SharedStateStore

    settings = Settings(
        workspace_root=db_path.parent,
        default_timeout=30,
        max_output_chars=8000,
        expected_egress_ip="",
        forbidden_egress_ip="",
        route_probe_ip="1.1.1.1",
        job_workers=1,
        github_token="",
        nvd_api_key="",
        munin_data_path=db_path.parent,
        munin_soul_path=db_path.parent / "soul",
    )
    return SharedStateStore(settings)
