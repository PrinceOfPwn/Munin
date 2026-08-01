"""Regression checks for the two Actions smoke harnesses."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(f"test_{name}", SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mcp_smoke_uses_canonical_direct_and_bff_endpoints(monkeypatch):
    smoke = _load_script("ci_live_smoke")
    monkeypatch.setattr(smoke, "BASE_URL", "http://127.0.0.1:8787")
    assert smoke._endpoint() == "http://127.0.0.1:8787/mcp/"
    assert smoke._endpoint(via_proxy=True) == "http://127.0.0.1:8787/mcp"


def test_live_llm_smoke_requires_tools_and_a_final_answer(monkeypatch):
    smoke = _load_script("live_llm_smoke")
    monkeypatch.setattr(smoke, "REQUIRED_TOOL_NAMES", frozenset({"ldap_search", "httpx_probe"}))
    with pytest.raises(RuntimeError, match="required tools"):
        smoke._validate_completed_run(
            {"tools": [{"tool_name": "ldap_search"}], "answer": "final evidence"}
        )
    with pytest.raises(RuntimeError, match="final assistant answer"):
        smoke._validate_completed_run(
            {
                "tools": [{"tool_name": "ldap_search"}, {"tool_name": "httpx_probe"}],
                "answer": "",
            }
        )
    smoke._validate_completed_run(
        {
            "tools": [{"tool_name": "ldap_search"}, {"tool_name": "httpx_probe"}],
            "answer": "The documented service is reachable.",
        }
    )


def test_httpx_binary_honours_explicit_operator_selection(monkeypatch):
    from munin.mcp.main import _httpx_binary

    monkeypatch.setenv("MUNIN_HTTPX_BINARY", "/opt/munin/bin/httpx")
    assert _httpx_binary() == "/opt/munin/bin/httpx"


def test_live_smoke_parses_and_replays_hitl_envelopes():
    smoke = _load_script("live_llm_smoke")
    raw = (
        "event: run-event\n"
        'data: {"kind":"human_request","request_id":"hitl-1","nonce":"nonce-1"}\n\n'
        "event: run-event\n"
        'data: {"kind":"run_state","state":"completed"}\n\n'
    )
    assert smoke._parse_sse_envelopes(raw)[0]["request_id"] == "hitl-1"
    assert smoke._terminal_state(smoke._parse_sse_envelopes(raw))["state"] == "completed"
