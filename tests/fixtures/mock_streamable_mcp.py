#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOOLS = [
    {
        "name": "list_proxy_http_history",
        "description": "List proxy history using stable IDs and compact projections.",
        "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer"}}},
    },
    {
        "name": "http_send_raw",
        "description": "Send a raw HTTP request.",
        "inputSchema": {"type": "object", "properties": {"request": {"type": "string"}}, "required": ["request"]},
    },
]


class Handler(BaseHTTPRequestHandler):
    server_version = "ValravnMock/1"

    def log_message(self, format, *args):
        return

    def _send(self, status: int, payload=None, *, session=False):
        body = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(status)
        if payload is not None:
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
        if session:
            self.send_header("Mcp-Session-Id", "valravn-e2e")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        message = json.loads(raw)
        method = message.get("method")
        if method == "notifications/initialized":
            self._send(202)
            return
        request_id = message.get("id")
        if method == "initialize":
            self._send(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {"tools": {}, "resources": {}},
                        "serverInfo": {"name": "mock-ultimate", "version": "1"},
                    },
                },
                session=True,
            )
            return
        if method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = message.get("params") or {}
            result = {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({"called": params.get("name"), "arguments": params.get("arguments") or {}}),
                    }
                ],
                "isError": False,
            }
        elif method == "resources/read":
            uri = (message.get("params") or {}).get("uri")
            result = {"contents": [{"uri": uri, "mimeType": "application/json", "text": "[]"}]}
        else:
            self._send(200, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": method}})
            return
        self._send(200, {"jsonrpc": "2.0", "id": request_id, "result": result})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=19444)
    args = parser.parse_args()
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
