from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from munin.valravn import arsenal, talons
from munin.valravn.mcp_clients import stdio_call, streamable_http_call

ROOT = Path(__file__).resolve().parents[1]


def test_security_hub_manifest_covers_all_38_servers():
    data = arsenal.list_servers()
    assert data["count"] == 38
    ids = {item["id"] for item in data["servers"]}
    assert {"web/nuclei", "code/semgrep", "ad/bloodhound", "binary/radare2"} <= ids


def test_stdio_transport_lists_and_calls_tools():
    command = [sys.executable, str(ROOT / "tests" / "fixtures" / "mock_stdio_mcp.py")]
    listed = stdio_call(command, "tools/list").result
    names = {tool["name"] for tool in listed["tools"]}
    assert {"quick_scan", "list_templates"} <= names

    called = stdio_call(command, "tools/call", {"name": "list_templates", "arguments": {}}).result
    assert json.loads(called["content"][0]["text"])["called"] == "list_templates"


def test_streamable_http_transport_roundtrip():
    server = subprocess.Popen(
        [sys.executable, str(ROOT / "tests" / "fixtures" / "mock_streamable_mcp.py"), "--port", "19446"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                result = streamable_http_call("http://127.0.0.1:19446/mcp", "tools/list", timeout=1).result
                break
            except Exception:
                time.sleep(0.05)
        else:
            raise AssertionError("mock Streamable HTTP MCP did not become ready")
        assert any(tool["name"] == "list_proxy_http_history" for tool in result["tools"])
    finally:
        server.terminate()
        server.wait(timeout=5)


def test_talons_prefers_ultimate_and_uses_compact_listing(monkeypatch):
    monkeypatch.setenv("VALRAVN_TALON_ULTIMATE_URL", "http://ultimate.test/mcp")
    monkeypatch.setenv("VALRAVN_TALON_AWESOME_URL", "http://awesome.test/mcp")
    talons._TOOL_CACHE.clear()

    def fake_call(provider, method, params=None):
        if provider.name == "valravn-ultimate":
            if method == "tools/list":
                return {
                    "tools": [
                        {
                            "name": "list_proxy_http_history",
                            "description": "history",
                            "inputSchema": {"type": "object", "required": ["limit"]},
                        }
                    ]
                }
            if method == "tools/call":
                return {"content": [{"type": "text", "text": '{"ok":true}'}], "isError": False}
        raise RuntimeError("offline")

    monkeypatch.setattr(talons, "_call_provider", fake_call)
    status = talons.status(refresh=True)
    assert status["preferred"] == "valravn-ultimate"

    listed = talons.list_tools(query="history")
    assert listed["count"] == 1
    assert listed["tools"][0]["required"] == ["limit"]
    assert "input_schema" not in listed["tools"][0]

    called = talons.call_tool("list_proxy_http_history")
    assert called["provider"] == "valravn-ultimate"
    assert called["result"] == {"ok": True}


def test_talons_falls_back_to_awesome_when_ultimate_is_unavailable(monkeypatch):
    monkeypatch.setenv("VALRAVN_TALON_ULTIMATE_URL", "http://ultimate.test/mcp")
    monkeypatch.setenv("VALRAVN_TALON_AWESOME_URL", "http://awesome.test/mcp")
    talons._TOOL_CACHE.clear()

    def fake_call(provider, method, params=None):
        if provider.name == "valravn-ultimate":
            raise RuntimeError("ultimate offline")
        if provider.name == "valravn-awesome":
            if method == "tools/list":
                return {
                    "tools": [
                        {
                            "name": "list_proxy_http_history",
                            "description": "Awesome stable-ID history",
                            "inputSchema": {"type": "object", "properties": {}},
                        }
                    ]
                }
            if method == "tools/call":
                return {"content": [{"type": "text", "text": '{"fallback":true}'}], "isError": False}
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(talons, "_call_provider", fake_call)

    status = talons.status(refresh=True)
    assert status["preferred"] == "valravn-awesome"
    ultimate = next(item for item in status["providers"] if item["name"] == "valravn-ultimate")
    assert ultimate["reachable"] is False

    called = talons.call_tool("list_proxy_http_history")
    assert called["provider"] == "valravn-awesome"
    assert called["result"] == {"fallback": True}


def test_arsenal_command_override_supports_real_or_fixture_server(monkeypatch):
    command = [sys.executable, str(ROOT / "tests" / "fixtures" / "mock_stdio_mcp.py")]
    monkeypatch.setenv("VALRAVN_ARSENAL_NUCLEI_MCP_COMMAND_JSON", json.dumps(command))
    listed = arsenal.list_tools("web/nuclei")
    assert {item["name"] for item in listed["tools"]} >= {"quick_scan", "list_templates"}
    called = arsenal.call_tool("nuclei", "list_templates", {})
    assert called["result"] == {"called": "list_templates"}
