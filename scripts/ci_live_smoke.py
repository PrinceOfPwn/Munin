#!/usr/bin/env python3
"""End-to-end smoke test for the live Munin GitHub Actions lab.

This uses the same Streamable HTTP MCP transport as the web terminal.  It is
deliberately limited to the isolated Actions services (``ldap`` and
``apache``), proving that the server, auth/session handling, LDAP data, Apache
fixture, active-tool gates, and generated evidence path work together.

Set ``MUNIN_SMOKE_BASE_URL`` to validate a reverse proxy instead of the direct
MCP listener.  ``--proxy-only`` keeps that check small while still exercising
initialize, session propagation, and a real tool call through the proxy.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any


BASE_URL = os.environ.get("MUNIN_SMOKE_BASE_URL", "http://127.0.0.1:8890").rstrip("/")
AUTH_TOKEN = os.environ.get("MUNIN_MCP_AUTH_TOKEN", "")
LDAP_TARGET = os.environ.get("MUNIN_SMOKE_LDAP_TARGET", "ldap")
APACHE_TARGET = os.environ.get("MUNIN_SMOKE_APACHE_TARGET", "apache")


def _endpoint() -> str:
    return BASE_URL if BASE_URL.endswith("/mcp") else f"{BASE_URL}/mcp"


def _read_rpc_response(response: Any) -> dict[str, Any]:
    raw = response.read().decode("utf-8", errors="replace")
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        return json.loads(raw)
    for line in raw.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise RuntimeError(f"MCP response did not contain JSON-RPC data: {raw[:500]!r}")


class McpClient:
    def __init__(self) -> None:
        self.session_id = ""

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if AUTH_TOKEN:
            headers["Authorization"] = f"Bearer {AUTH_TOKEN}"
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        return headers

    def initialize(self) -> None:
        deadline = time.monotonic() + 90
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            request = urllib.request.Request(
                _endpoint(),
                data=json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "munin-actions-e2e", "version": "1.0"},
                        },
                    }
                ).encode(),
                headers=self._headers(),
            )
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    self.session_id = response.headers.get("mcp-session-id", "")
                    _read_rpc_response(response)
                if self.session_id:
                    return
                last_error = RuntimeError("initialize response has no mcp-session-id")
            except (OSError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
                last_error = exc
            time.sleep(2)
        raise RuntimeError(f"MCP did not initialize within 90 seconds: {last_error}")

    def rpc(self, request_id: int, method: str, params: dict[str, Any], timeout: int = 90) -> dict[str, Any]:
        request = urllib.request.Request(
            _endpoint(),
            data=json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}).encode(),
            headers=self._headers(),
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            message = _read_rpc_response(response)
        if message.get("error"):
            raise RuntimeError(f"MCP {method} error: {message['error']}")
        return message

    def tool(
        self,
        request_id: int,
        name: str,
        arguments: dict[str, Any],
        timeout: int = 120,
        require_ok: bool = True,
    ) -> dict[str, Any]:
        message = self.rpc(request_id, "tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
        result = message.get("result", {})
        if result.get("isError"):
            raise RuntimeError(f"{name} returned MCP isError: {result}")
        chunks = result.get("content", [])
        if not chunks:
            raise RuntimeError(f"{name} returned no content: {result}")
        text = "".join(str(chunk.get("text", "")) for chunk in chunks if chunk.get("type") == "text")
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise RuntimeError(f"{name} returned non-JSON tool content: {text[:500]!r}") from exc
        if require_ok and not payload.get("ok"):
            raise RuntimeError(f"{name} failed: {payload.get('error') or payload.get('summary')}")
        return payload


def _contains(payload: dict[str, Any], expected: str, label: str) -> None:
    rendered = json.dumps(payload, ensure_ascii=False)
    if expected not in rendered:
        raise RuntimeError(f"{label} did not contain {expected!r}: {rendered[:1200]}")


def _tool_names(message: dict[str, Any]) -> set[str]:
    return {str(item.get("name", "")) for item in message.get("result", {}).get("tools", [])}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proxy-only", action="store_true", help="only validate the same-origin GUI proxy")
    parser.add_argument("--require-turso", action="store_true", help="fail unless diagnostics reports a libsql backend")
    args = parser.parse_args()

    client = McpClient()
    client.initialize()
    catalog = client.rpc(2, "tools/list", {})
    required_tools = {
        "ldap_search",
        "nmap_scan",
        "httpx_probe",
        "execute_command",
        "munin_diagnostics",
        "hugin_rag_search",
        "extension_forge",
        "graph_forge",
        "tool_forge",
    }
    missing = sorted(required_tools - _tool_names(catalog))
    if missing:
        raise RuntimeError(f"missing required MCP tools: {missing}")
    print(f"OK catalog: {len(_tool_names(catalog))} tools; critical surface registered")

    diagnostics = client.tool(
        3,
        "munin_diagnostics",
        {"mode": "quick", "run_id": "actions-e2e-diagnostics"},
        require_ok=False,
    )
    if not diagnostics.get("ok"):
        details = diagnostics.get("data", {})
        raise RuntimeError(
            "munin_diagnostics hard failures: "
            f"{details.get('hard_failures', [])}; checks={json.dumps(details.get('checks', []), ensure_ascii=False)}"
        )
    if args.require_turso:
        _contains(diagnostics, "libsql", "Turso diagnostics")
    print("OK diagnostics reachable through MCP")

    if args.proxy_only:
        print(f"OK same-origin GUI proxy: {_endpoint()}")
        return

    ldap = client.tool(
        4,
        "ldap_search",
        {
            "filter_template": "(&(objectClass=device)(cn=WEB01))",
            "attributes_csv": "cn,description,owner",
            "size_limit": 5,
            "run_id": "actions-e2e-ldap-web01",
        },
    )
    _contains(ldap, "WEB01", "LDAP web fixture")
    _contains(ldap, "Apache", "LDAP web fixture")
    print("OK LDAP fixture contains the Apache training node")

    ldap_nmap = client.tool(
        5,
        "nmap_scan",
        {
            "target": LDAP_TARGET,
            "scan_type": "-sT",
            "ports": "389",
            "additional_args": "-Pn",
            "mode": "sync",
            "timeout": 60,
            "run_id": "actions-e2e-nmap-ldap",
        },
    )
    _contains(ldap_nmap, "389/tcp", "LDAP nmap scan")
    print("OK nmap_scan reached the authorized LDAP service")

    apache_nmap = client.tool(
        6,
        "nmap_scan",
        {
            "target": APACHE_TARGET,
            "scan_type": "-sV",
            "ports": "80",
            "additional_args": "-Pn",
            "mode": "sync",
            "timeout": 60,
            "run_id": "actions-e2e-nmap-apache",
        },
    )
    _contains(apache_nmap, "80/tcp", "Apache nmap scan")
    _contains(apache_nmap, "Apache", "Apache nmap scan")
    print("OK nmap_scan fingerprinted the isolated Apache fixture")

    httpx = client.tool(
        7,
        "httpx_probe",
        {
            "targets": f"http://{APACHE_TARGET}:80",
            "mode": "sync",
            "timeout": 60,
            "run_id": "actions-e2e-httpx-apache",
        },
    )
    _contains(httpx, "httpx_probe", "Apache httpx probe")
    print("OK httpx_probe collected web evidence from Apache")

    command = client.tool(
        8,
        "execute_command",
        {
            "command": f"curl -fsSI http://{APACHE_TARGET}:80/",
            "target": f"{APACHE_TARGET}:80",
            "mode": "sync",
            "timeout": 30,
            "run_id": "actions-e2e-execute-command",
        },
    )
    _contains(command, "200", "execute_command Apache probe")
    print("OK execute_command ran only against the declared lab target")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"::error::{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
