"""MCP tool `tool_forge` — spawns the ReAct sub-agent that writes new Python tools.

The heavy lifting (ReAct loop, langgraph-codeact sandbox, AST guard) lives in
``munin/subagents/tool_forge.py``. This module is just the thin MCP surface:

- Consults ``list_generated_tools`` first to avoid regenerating an existing tool.
- Instantiates :class:`ToolForgeSubagent` and runs it.
- On success, registers the generated tool via :mod:`munin.mcp.registry`.

Callers (Munin core, another agent, or a human) invoke ``tool_forge(spec=...)``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..main import MCP, STATE, audited_tool  # noqa: TID252
from .. import registry  # noqa: TID252
from ..shared_state import _coerce_int  # noqa: TID252
from ...core.execution_progress import emit_tool_progress  # noqa: TID252

logger = logging.getLogger("munin-mcp.forge")


def _guess_tag(spec: str) -> str:
    lowered = spec.lower()
    for tag in ("ldap", "kerberos", "smb", "http", "kv", "cve", "hugin", "ad"):
        if tag in lowered:
            return tag
    return ""


_EXPLICIT_NAME_PATTERNS = (
    r"\b(?:tool|herramienta)\s+(?:named|called|llamad[ao]|denominad[ao])\s+['\"`]?([a-zA-Z][a-zA-Z0-9_-]{1,80})",
    r"\b(?:create|forge|crea|forja)\s+(?:a\s+|una\s+)?(?:new\s+|nueva\s+)?(?:tool|herramienta)\s+['\"`]?([a-zA-Z][a-zA-Z0-9_-]{1,80})",
)


def _requested_tool_name(spec: str) -> str:
    """Return an explicitly requested name, never a guessed semantic match."""
    for pattern in _EXPLICIT_NAME_PATTERNS:
        match = re.search(pattern, spec, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _existing_match(spec: str) -> dict[str, Any] | None:
    """Reuse only a tool named explicitly by the caller.

    A two-keyword overlap (for example ``ldap`` + ``security``) is not a
    contract. The old heuristic silently replaced specialised audits with an
    unrelated enumeration tool, which made forging feel unreliable.
    """
    requested_name = _requested_tool_name(spec)
    return registry.resolve_tool_by_name(STATE, requested_name) if requested_name else None


def _coerce_bool(value: Any, default: bool = False) -> bool:
    """Accept MCP clients that send JSON booleans as strings."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    if value is None:
        return default
    return bool(value)


@MCP.tool()
@audited_tool("tool_forge", "documentation", lambda *a, **k: "sync")
def tool_forge(
    spec: str,
    allowed_imports_csv: str = "",
    max_iterations: int = 5,
    force_regenerate: bool = False,
    run_id: str = "",
) -> dict[str, Any]:
    """Forge a new Python tool from a natural-language spec.

    Consults the existing generated-tool catalog first; if a close match exists (and
    ``force_regenerate`` is False) returns a pointer to it instead of forging anew.

    Runs the ReAct sub-agent :class:`ToolForgeSubagent` which iterates
    write → validate (AST guard) → execute in sandbox → refine, up to
    ``max_iterations`` times. On success, hot-loads the tool as ``gen__<slug>``
    (invocable by any MCP client from that moment on).
    """
    if not spec.strip():
        return {"ok": False, "tool": "tool_forge", "mode": "sync", "summary": "empty spec", "error": {"code": "bad_input", "message": "spec required"}}

    force = _coerce_bool(force_regenerate)
    iterations = max(1, min(_coerce_int(max_iterations, 5), 12))

    if not force:
        existing = _existing_match(spec)
        if existing:
            return {
                "ok": True,
                "tool": "tool_forge",
                "mode": "sync",
                "summary": f"existing tool '{existing['name']}' matches — skipping forge",
                "data": {
                    "reused": True,
                    "match_reason": "exact_requested_name",
                    "existing": existing,
                },
            }

    try:
        # Lazy import — subagents pull in langchain/langgraph which are heavier than
        # the plain MCP server surface. Don't slow down MCP startup.
        from ...subagents.tool_forge import ToolForgeSubagent  # noqa: TID252
    except Exception as exc:
        return {"ok": False, "tool": "tool_forge", "mode": "sync", "summary": "subagent import failed", "error": {"code": "import_failed", "message": str(exc)}}

    allowed_imports = [x.strip() for x in allowed_imports_csv.split(",") if x.strip()]
    emit_tool_progress({"stage": "forge_queued", "message": "Preparing isolated tool forge"})
    subagent = ToolForgeSubagent(
        state=STATE,
        allowed_imports=allowed_imports,
        max_iterations=iterations,
        on_progress=emit_tool_progress,
    )
    outcome = subagent.forge(spec)
    if not outcome.get("ok"):
        return {"ok": False, "tool": "tool_forge", "mode": "sync", "summary": outcome.get("summary", "forge failed"), "error": outcome.get("error", {"code": "forge_failed", "message": "unknown"})}

    # Hot-load into MCP + persist in procedural.
    try:
        emit_tool_progress({"stage": "forge_registration", "message": "Registering generated tool in MCP"})
        registered = registry.register(
            MCP,
            STATE,
            slug=outcome["slug"],
            description=outcome.get("description", spec),
            script_path=outcome["script_path"],
            function_name=outcome["function_name"],
            signature=outcome.get("signature", {}),
            tags=outcome.get("tags", []),
            created_by_agent="tool_forge",
        )
    except Exception as exc:
        return {"ok": False, "tool": "tool_forge", "mode": "sync", "summary": "registry.register failed", "error": {"code": "registry_failed", "message": str(exc)}}

    # Persist to git if enabled (runner mode). No-op locally unless MUNIN_AUTO_COMMIT=1.
    try:
        from .. import git_persist  # noqa: TID252,PLC0415
        git_persist.commit_forged_tool(
            script_path=outcome["script_path"],
            tool_name=registered["name"],
            description=outcome.get("description", spec)[:200],
        )
    except Exception as exc:  # pragma: no cover — persistence is best-effort
        logger.warning("git_persist.commit_forged_tool failed: %s", exc)

    return {
        "ok": True,
        "tool": "tool_forge",
        "mode": "sync",
        "summary": f"forged {registered['name']}",
        "data": {
            "reused": False,
            "iterations": outcome.get("iterations"),
            "registered": registered,
            "log": outcome.get("log_summary", ""),
        },
    }
