"""Valravn Arsenal: a compact gateway to FuzzingLabs/mcp-security-hub."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any

from .mcp_clients import McpTransportError, compact_tool, decode_tool_content, stdio_call, tool_records

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST = _REPO_ROOT / "valravn" / "arsenal" / "security_hub.json"
_DEFAULT_ROOT = _REPO_ROOT / "valravn" / "upstreams" / "mcp-security-hub"


def _load_manifest() -> dict[str, Any]:
    return json.loads(_MANIFEST.read_text(encoding="utf-8"))


def upstream_root() -> Path:
    value = os.environ.get("VALRAVN_ARSENAL_ROOT", "").strip()
    return Path(value).expanduser().resolve() if value else _DEFAULT_ROOT


def _entry(server: str) -> dict[str, Any]:
    manifest = _load_manifest()
    for item in manifest.get("servers", []):
        if server in {item.get("id"), item.get("alias"), item.get("service")}:
            return item
    raise ValueError(f"unknown Valravn Arsenal server: {server}")


def _env_key(service: str) -> str:
    return "VALRAVN_ARSENAL_" + service.upper().replace("-", "_") + "_COMMAND_JSON"


def _command(entry: dict[str, Any]) -> list[str]:
    service = str(entry["service"])
    raw_json = os.environ.get(_env_key(service), "").strip()
    if raw_json:
        value = json.loads(raw_json)
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            raise ValueError(f"{_env_key(service)} must be a JSON string array")
        return value

    raw = os.environ.get(_env_key(service).removesuffix("_JSON"), "").strip()
    if raw:
        return shlex.split(raw)

    root = upstream_root()
    compose = root / "docker-compose.yml"
    if compose.is_file() and shutil.which("docker"):
        return ["docker", "compose", "-f", str(compose), "run", "--rm", "-T", service]

    server_py = root / str(entry["path"]) / "server.py"
    if server_py.is_file():
        return [sys.executable, str(server_py)]

    raise McpTransportError(
        f"{service} is not installed; run valravn/arsenal/bootstrap.py or set {_env_key(service)}"
    )


def status() -> dict[str, Any]:
    manifest = _load_manifest()
    root = upstream_root()
    return {
        "mesh": "valravn-arsenal",
        "upstream": manifest.get("upstream"),
        "upstream_commit": manifest.get("upstream_commit"),
        "license": manifest.get("license"),
        "root": str(root),
        "installed": root.is_dir(),
        "docker": bool(shutil.which("docker")),
        "server_count": len(manifest.get("servers", [])),
        "naming": "Valravn aliases are stable; upstream service/tool identities are preserved in metadata.",
    }


def list_servers(*, category: str = "", available_only: bool = False) -> dict[str, Any]:
    manifest = _load_manifest()
    root = upstream_root()
    wanted = category.casefold().strip()
    rows: list[dict[str, Any]] = []
    for item in manifest.get("servers", []):
        if wanted and str(item.get("category", "")).casefold() != wanted:
            continue
        path = root / str(item["path"])
        env_configured = bool(os.environ.get(_env_key(str(item["service"])), "").strip())
        available = path.is_dir() or env_configured
        if available_only and not available:
            continue
        rows.append({**item, "available": available, "upstream": manifest.get("upstream")})
    return {"mesh": "valravn-arsenal", "servers": rows, "count": len(rows)}


def list_tools(server: str, *, query: str = "", limit: int = 80, include_schema: bool = False) -> dict[str, Any]:
    entry = _entry(server)
    result = stdio_call(_command(entry), "tools/list").result
    wanted = query.casefold().strip()
    records: list[dict[str, Any]] = []
    for tool in tool_records(result):
        name = str(tool.get("name", ""))
        description = str(tool.get("description", ""))
        if wanted and wanted not in name.casefold() and wanted not in description.casefold():
            continue
        record = compact_tool(tool, include_schema=include_schema)
        record.update({"server": entry["id"], "upstream_service": entry["service"]})
        records.append(record)
        if len(records) >= max(1, min(int(limit), 200)):
            break
    return {"mesh": "valravn-arsenal", "server": entry["id"], "tools": records, "count": len(records)}


def call_tool(server: str, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    entry = _entry(server)
    result = stdio_call(
        _command(entry),
        "tools/call",
        {"name": tool_name, "arguments": arguments or {}},
        timeout=float(os.environ.get("VALRAVN_ARSENAL_CALL_TIMEOUT", "120")),
    ).result
    return {
        "mesh": "valravn-arsenal",
        "server": entry["id"],
        "upstream_service": entry["service"],
        "tool": tool_name,
        "result": decode_tool_content(result),
        "raw_is_error": bool(result.get("isError")),
    }
