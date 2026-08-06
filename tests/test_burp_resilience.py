"""Regression tests for Burp outages as capability-local failures."""

from __future__ import annotations

import httpx

from munin.mcp.tools import burp_tool


class _Response:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.text = "ok"

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _BrokenClient:
    def __init__(self) -> None:
        self.closed = False

    def get(self, path, params=None):
        request = httpx.Request("GET", f"http://127.0.0.1{path}")
        raise httpx.ReadError("peer closed connection", request=request)

    def close(self) -> None:
        self.closed = True


class _HealthyClient:
    def __init__(self, payload=None) -> None:
        self.payload = payload or {"status": "ok", "version": "test"}
        self.closed = False

    def get(self, path, params=None):
        return _Response(self.payload)

    def post(self, path, json=None):
        return _Response(self.payload)

    def put(self, path, json=None):
        return _Response(self.payload)

    def delete(self, path):
        return _Response(self.payload)

    def close(self) -> None:
        self.closed = True


def _clear_client_state() -> None:
    burp_tool._CLIENT = None
    burp_tool._CLIENT_KEY = None


def test_invalid_environment_never_breaks_base_url(monkeypatch):
    monkeypatch.setenv("BURP_API_PORT", "not-a-port")
    monkeypatch.setenv("BURP_API_TIMEOUT", "nan")
    monkeypatch.setenv("BURP_MAX_RESPONSE_SIZE", "-9")

    assert burp_tool._burp_base_url().endswith(":8111")
    assert burp_tool._burp_timeout() == 30.0
    assert burp_tool._burp_max_response() == 50_000


def test_successful_list_json_is_normalized_before_public_handlers(monkeypatch):
    monkeypatch.setattr(
        burp_tool,
        "_get_client",
        lambda: _HealthyClient(payload=[{"id": 1}, {"id": 2}]),
    )

    result = burp_tool._request("GET", "/api/proxy/history")

    assert result == {"items": [{"id": 1}, {"id": 2}]}
    assert burp_tool._is_error_envelope(result) is False


def test_mid_run_transport_failure_is_burp_only_and_non_fatal(monkeypatch):
    monkeypatch.setattr(burp_tool, "_get_client", lambda: _BrokenClient())
    resets: list[bool] = []
    monkeypatch.setattr(
        burp_tool,
        "_reset_client",
        lambda expected=None: resets.append(True),
    )

    result = burp_tool.burp_invoke("/api/proxy/history")

    assert result["ok"] is False
    assert result["error"]["code"] == "extension_unreachable"
    assert result["failure_scope"] == "burp_only"
    assert result["run_should_continue"] is True
    assert result["retryable"] is True
    assert resets == [True]


def test_transport_failure_discards_pool_and_next_call_can_recover(monkeypatch):
    _clear_client_state()
    created = [_BrokenClient(), _HealthyClient()]
    monkeypatch.setattr(
        burp_tool.httpx,
        "Client",
        lambda **kwargs: created.pop(0),
    )

    first = burp_tool._request("GET", "/api/health")
    second = burp_tool._request("GET", "/api/health")

    assert first["code"] == "extension_unreachable"
    assert second["status"] == "ok"
    assert burp_tool._CLIENT is not None
    _clear_client_state()


def test_outer_audit_boundary_catches_unexpected_handler_bug(monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("unexpected adapter bug")

    monkeypatch.setattr(burp_tool, "_request", explode)

    result = burp_tool.burp_status()

    assert result["ok"] is False
    assert result["error"]["code"] == "tool_failed"
