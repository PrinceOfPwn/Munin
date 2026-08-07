#!/usr/bin/env python3
"""Live Valravn -> burp-mcp-ultimate -> Burp -> Juice Shop E2E.

The lab proves two independent paths against the same real Burp process:
1. Munin/Valravn Talons invokes Ultimate's Montoya-backed http_send_raw tool.
2. A request traverses Burp's real Proxy listener and is observable again via
   Ultimate's burp://proxy/history MCP resource.
"""
from __future__ import annotations

import json
import os
import socket
import time
from typing import Any
from urllib.parse import urlparse

from munin.valravn import talons


def _contains_status_200(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return any(token in text for token in ('"statusCode": 200', '"status": 200', 'HTTP/1.1 200', 'HTTP/1.0 200'))


def _wait_tcp(host: str, port: int, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError as exc:
            last = exc
            time.sleep(0.25)
    raise RuntimeError(f"TCP endpoint {host}:{port} did not become ready: {last}")


def _proxy_get(proxy_host: str, proxy_port: int, target: str, marker: str) -> str:
    parsed = urlparse(target)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ValueError("Juice Shop lab target must be plain HTTP")
    port = parsed.port or 80
    absolute = f"http://{parsed.hostname}:{port}/"
    request = (
        f"GET {absolute} HTTP/1.1\r\n"
        f"Host: {parsed.hostname}:{port}\r\n"
        f"X-Valravn-E2E: {marker}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    with socket.create_connection((proxy_host, proxy_port), timeout=10) as sock:
        sock.sendall(request)
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    return b"".join(chunks).decode("latin-1", errors="replace")


def main() -> int:
    target = os.environ.get("JUICE_SHOP_URL", "http://127.0.0.1:3000").rstrip("/")
    parsed = urlparse(target)
    if parsed.scheme != "http" or not parsed.hostname:
        raise SystemExit(f"invalid JUICE_SHOP_URL: {target}")

    ultimate_url = os.environ.get("VALRAVN_TALON_ULTIMATE_URL", "http://127.0.0.1:9444/mcp")
    proxy_host = os.environ.get("BURP_PROXY_HOST", "127.0.0.1")
    proxy_port = int(os.environ.get("BURP_PROXY_PORT", "8080"))

    # Force the live provider so an optional fallback can never make this CI
    # proof pass while Ultimate itself is broken.
    talons._TOOL_CACHE.clear()
    status = talons.status(refresh=True)
    ultimate = next(row for row in status["providers"] if row["name"] == "valravn-ultimate")
    assert ultimate["reachable"] is True, status
    assert ultimate["tool_count"] >= 100, status
    assert ultimate["endpoint"] == ultimate_url, status

    catalog = talons.list_tools(provider="valravn-ultimate", limit=200, refresh=True)
    names = {tool["name"] for tool in catalog["tools"]}
    required = {"burp_version", "http_send_raw", "intercept_set_mode"}
    assert required <= names, f"Ultimate missing required tools: {sorted(required - names)}"

    version = talons.call_tool("burp_version", {}, provider="valravn-ultimate")
    assert version["provider"] == "valravn-ultimate", version
    assert not version["raw_is_error"], version

    # Keep the proxy non-blocking even if a Burp default changes in the future.
    observe = talons.call_tool(
        "intercept_set_mode",
        {"mode": "observe"},
        provider="valravn-ultimate",
    )
    assert not observe["raw_is_error"], observe

    host = parsed.hostname
    port = parsed.port or 80
    raw = (
        "GET / HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "X-Valravn-MCP-E2E: direct-montoya\r\n"
        "Connection: close\r\n\r\n"
    )
    sent = talons.call_tool(
        "http_send_raw",
        {"host": host, "port": port, "secure": False, "request": raw},
        provider="valravn-ultimate",
    )
    assert not sent["raw_is_error"], sent
    assert _contains_status_200(sent["result"]), sent

    _wait_tcp(proxy_host, proxy_port)
    marker = f"valravn-{int(time.time() * 1000)}"
    proxied = _proxy_get(proxy_host, proxy_port, target, marker)
    assert " 200 " in proxied.split("\r\n", 1)[0], proxied[:500]

    # Read Proxy history back through Ultimate. This proves that Burp really
    # handled the proxied request and the MCP can observe the resulting state.
    deadline = time.monotonic() + 30
    last_history: Any = None
    while time.monotonic() < deadline:
        last_history = talons.read_resource("burp://proxy/history", provider="valravn-ultimate")
        if marker in json.dumps(last_history, ensure_ascii=False, default=str):
            break
        time.sleep(0.5)
    else:
        raise AssertionError(f"marker {marker!r} not found in Burp proxy history: {last_history!r}")

    print("Valravn live E2E OK")
    print(f"  Munin/Talons -> burp-mcp-ultimate -> Burp -> Juice Shop ({target})")
    print(f"  Burp Proxy {proxy_host}:{proxy_port} -> Juice Shop -> burp://proxy/history")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
