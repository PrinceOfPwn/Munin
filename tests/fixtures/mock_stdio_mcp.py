#!/usr/bin/env python3
from __future__ import annotations

import json
import sys

TOOLS = [
    {"name": "quick_scan", "description": "mock quick scan", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_templates", "description": "list templates", "inputSchema": {"type": "object", "properties": {}}},
]

for line in sys.stdin:
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        continue
    if "id" not in msg:
        continue
    method = msg.get("method")
    if method == "initialize":
        result = {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mock-arsenal", "version": "1"},
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = msg.get("params") or {}
        result = {
            "content": [{"type": "text", "text": json.dumps({"called": params.get("name")})}],
            "isError": False,
        }
    else:
        print(
            json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": method}}),
            flush=True,
        )
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
