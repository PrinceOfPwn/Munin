"""Munin-native MCP tools: wake/sleep orchestration, soul I/O, memory helpers, and
the catalog of generated tools (`list_generated_tools`) that Munin queries every ReAct
step before invoking `tool_forge`."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from ..main import MCP, STATE, audited_tool  # noqa: TID252
from .. import registry  # noqa: TID252

logger = logging.getLogger("munin-mcp.munin_tools")


def _get_settings() -> Any:
    from ..config import get_settings  # noqa: TID252 - re-read env each call so tests can monkeypatch

    return get_settings()


def _safe_soul_path(path_str: str) -> Path:
    """Ensure the caller doesn't escape soul/ via ../ traversal (CWE-22)."""
    settings = _get_settings()
    root = settings.munin_soul_path.resolve()
    candidate = (root / path_str).resolve()
    if root not in candidate.parents and candidate != root:
        raise ValueError("soul path escapes soul/ root")
    return candidate


# ─────────────────────────────────────────────
# Generated tool catalog
# ─────────────────────────────────────────────

@MCP.tool()
@audited_tool("list_generated_tools", "passive", lambda *a, **k: "sync")
def list_generated_tools(tag: str = "", run_id: str = "") -> dict[str, Any]:
    """Catalog of tools produced by tool_forge — Munin consults this BEFORE invoking tool_forge to avoid regeneration."""
    rows = registry.list_generated(STATE, tag=tag)
    return {"ok": True, "tool": "list_generated_tools", "mode": "sync", "summary": f"{len(rows)} generated tools", "data": {"tools": rows, "count": len(rows)}}


@MCP.tool()
@audited_tool("describe_generated_tool", "passive", lambda *a, **k: "sync")
def describe_generated_tool(name: str, include_script: bool = False, run_id: str = "") -> dict[str, Any]:
    """Return the spec of a generated tool. Optionally include the Python source."""
    row = registry.resolve_tool_by_name(STATE, name)
    if not row:
        return {"ok": False, "tool": "describe_generated_tool", "mode": "sync", "summary": "not found", "error": {"code": "not_found", "message": name}}
    if include_script:
        try:
            row = {**row, "script": Path(row["script_path"]).read_text(encoding="utf-8")}
        except Exception as exc:
            row = {**row, "script_error": str(exc)}
    return {"ok": True, "tool": "describe_generated_tool", "mode": "sync", "summary": row["name"], "data": row}


@MCP.tool()
@audited_tool("run_generated_tool", "documentation", lambda *a, **k: "sync")
def run_generated_tool(name: str, args_json: str = "{}", run_id: str = "") -> dict[str, Any]:
    """Invoke a generated tool by name. Redundant with calling gen__<name> directly, but useful for introspection."""
    row = registry.resolve_tool_by_name(STATE, name)
    if not row:
        return {"ok": False, "tool": "run_generated_tool", "mode": "sync", "summary": "not found", "error": {"code": "not_found", "message": name}}
    try:
        args = json.loads(args_json or "{}")
        if not isinstance(args, dict):
            raise ValueError("args_json must be an object")
    except Exception as exc:
        return {"ok": False, "tool": "run_generated_tool", "mode": "sync", "summary": "bad args_json", "error": {"code": "bad_input", "message": str(exc)}}
    try:
        callable_fn = registry._load_callable(Path(row["script_path"]), row["signature"].get("function_name") or row["name"].replace("gen__", ""))
        result = callable_fn(**args)
    except Exception as exc:
        return {"ok": False, "tool": "run_generated_tool", "mode": "sync", "summary": "exec failed", "error": {"code": "generated_tool_failed", "message": str(exc)}}
    return {"ok": True, "tool": "run_generated_tool", "mode": "sync", "summary": row["name"], "data": {"result": result}}


@MCP.tool()
@audited_tool("deactivate_generated_tool", "admin", lambda *a, **k: "sync")
def deactivate_generated_tool(name: str, run_id: str = "") -> dict[str, Any]:
    """Soft-delete a generated tool (marks active=0). Use `munin reset` for hard purge."""
    ok = registry.deactivate(STATE, name)
    return {"ok": ok, "tool": "deactivate_generated_tool", "mode": "sync", "summary": f"deactivate {name}: {ok}", "data": {"name": name, "deactivated": ok}}


# ─────────────────────────────────────────────
# Soul I/O
# ─────────────────────────────────────────────

@MCP.tool()
@audited_tool("soul_list", "passive", lambda *a, **k: "sync")
def soul_list(run_id: str = "") -> dict[str, Any]:
    """List all Markdown files under soul/."""
    settings = _get_settings()
    root = settings.munin_soul_path
    if not root.exists():
        return {"ok": True, "tool": "soul_list", "mode": "sync", "summary": "soul/ empty", "data": {"files": []}}
    files = [{"path": str(p.relative_to(root)), "size": p.stat().st_size} for p in root.rglob("*.md")]
    return {"ok": True, "tool": "soul_list", "mode": "sync", "summary": f"{len(files)} soul files", "data": {"files": files, "root": str(root)}}


@MCP.tool()
@audited_tool("soul_read", "passive", lambda *a, **k: "sync")
def soul_read(path: str, run_id: str = "") -> dict[str, Any]:
    """Read a Markdown file under soul/."""
    try:
        target = _safe_soul_path(path)
    except ValueError as exc:
        return {"ok": False, "tool": "soul_read", "mode": "sync", "summary": "bad path", "error": {"code": "path_escape", "message": str(exc)}}
    if not target.exists():
        return {"ok": False, "tool": "soul_read", "mode": "sync", "summary": "not found", "error": {"code": "not_found", "message": path}}
    return {"ok": True, "tool": "soul_read", "mode": "sync", "summary": f"read {path}", "data": {"path": path, "content": target.read_text(encoding="utf-8")}}


@MCP.tool()
@audited_tool("soul_propose_edit", "documentation", lambda *a, **k: "sync")
def soul_propose_edit(path: str, new_content: str, rationale: str = "", run_id: str = "") -> dict[str, Any]:
    """Propose a soul edit — QUEUED, NOT APPLIED. Human operator reviews at soul_pending/ before merge.

    Munin CAN'T rewrite its own identity in runtime. This deliberately requires a human in the loop.
    """
    settings = _get_settings()
    pending_root = settings.munin_data_path / "soul_pending"
    pending_root.mkdir(parents=True, exist_ok=True)
    try:
        _ = _safe_soul_path(path)  # only validates traversal
    except ValueError as exc:
        return {"ok": False, "tool": "soul_propose_edit", "mode": "sync", "summary": "bad path", "error": {"code": "path_escape", "message": str(exc)}}
    digest = hashlib.sha256(new_content.encode("utf-8")).hexdigest()[:12]
    proposal = pending_root / f"{Path(path).stem}.{digest}.pending.md"
    proposal.write_text(new_content, encoding="utf-8")
    meta = pending_root / f"{Path(path).stem}.{digest}.meta.json"
    meta.write_text(json.dumps({"target_path": path, "rationale": rationale, "sha256": digest}, ensure_ascii=True), encoding="utf-8")
    return {"ok": True, "tool": "soul_propose_edit", "mode": "sync", "summary": f"proposal queued at {proposal.name}", "data": {"proposal_path": str(proposal), "meta_path": str(meta)}}


# ─────────────────────────────────────────────
# Memory (episodic + semantic)
# ─────────────────────────────────────────────

@MCP.tool()
@audited_tool("memory_remember", "documentation", lambda *a, **k: "sync")
def memory_remember(key: str, value_json: str, run_id: str = "") -> dict[str, Any]:
    """Persist a semantic fact to shared_state.sqlite."""
    try:
        value = json.loads(value_json)
    except Exception as exc:
        return {"ok": False, "tool": "memory_remember", "mode": "sync", "summary": "bad value_json", "error": {"code": "bad_input", "message": str(exc)}}
    row = STATE.semantic_remember(key, value)
    return {"ok": True, "tool": "memory_remember", "mode": "sync", "summary": f"remembered {key}", "data": row}


@MCP.tool()
@audited_tool("memory_recall", "passive", lambda *a, **k: "sync")
def memory_recall(key: str, run_id: str = "") -> dict[str, Any]:
    """Recall a semantic fact."""
    value = STATE.semantic_recall(key)
    if value is None:
        return {"ok": False, "tool": "memory_recall", "mode": "sync", "summary": "not found", "error": {"code": "not_found", "message": key}}
    return {"ok": True, "tool": "memory_recall", "mode": "sync", "summary": f"recalled {key}", "data": {"key": key, "value": value}}


@MCP.tool()
@audited_tool("memory_list", "passive", lambda *a, **k: "sync")
def memory_list(prefix: str = "", limit: int = 100, run_id: str = "") -> dict[str, Any]:
    """List semantic facts. Filter by key prefix."""
    rows = STATE.semantic_list(prefix=prefix, limit=limit)
    return {"ok": True, "tool": "memory_list", "mode": "sync", "summary": f"{len(rows)} facts", "data": {"facts": rows, "count": len(rows)}}


@MCP.tool()
@audited_tool("episodic_query", "passive", lambda *a, **k: "sync")
def episodic_query(agent: str = "", action: str = "", limit: int = 100, run_id: str = "") -> dict[str, Any]:
    """Recent episodic events (tool calls, ReAct steps, orchestrator decisions)."""
    rows = STATE.episodic_query(agent=agent, action=action, limit=limit)
    return {"ok": True, "tool": "episodic_query", "mode": "sync", "summary": f"{len(rows)} events", "data": {"events": rows, "count": len(rows)}}


# ─────────────────────────────────────────────
# Subagent tool catalog — for graph design
# ─────────────────────────────────────────────

@MCP.tool()
@audited_tool("list_subagent_tools", "passive", lambda *a, **k: "sync")
def list_subagent_tools(category: str = "", run_id: str = "") -> dict[str, Any]:
    """Return the full catalog of tools available to subagents, organized by category.

    Use this before calling graph_forge to choose an appropriate tool_whitelist_csv.
    Pass category= to filter (e.g. 'ldap', 'intel', 'memory', 'messaging').
    """
    from ...subagents.base import list_subagent_tools as _list  # noqa: PLC0415
    return _list(category=category)


# ─────────────────────────────────────────────
# Conversational interface — munin_chat
# ─────────────────────────────────────────────

@MCP.tool()
@audited_tool("munin_chat", "passive", lambda *a, **k: "sync")
def munin_chat(
    message: str,
    max_iterations: int = 6,
    run_id: str = "",
) -> dict[str, Any]:
    """Full conversational ReAct interface. Send natural language — Munin reasons,
    calls tools autonomously, and returns a response plus the tool-call log so the
    frontend can render each step as an inline card.
    Requires LLM_BASE_URL / LLM_API_KEY / LLM_MODEL to be configured."""
    if not message.strip():
        return {
            "ok": False, "tool": "munin_chat", "mode": "sync",
            "summary": "empty message",
            "error": {"code": "bad_input", "message": "message is required"},
        }
    settings = _get_settings()
    if not settings.llm_base_url or not settings.llm_api_key or not settings.llm_model:
        return {
            "ok": False, "tool": "munin_chat", "mode": "sync",
            "summary": "LLM not configured",
            "error": {
                "code": "config_missing",
                "message": "LLM_BASE_URL, LLM_API_KEY and LLM_MODEL must be set to use munin_chat",
            },
        }
    try:
        from ...core.munin_agent import MuninAgent  # noqa: PLC0415
        agent = MuninAgent(settings)
        result = agent.respond(
            message.strip(),
            max_iterations=max(1, min(int(max_iterations), 400)),
        )
    except Exception as exc:
        logger.exception("munin_chat: agent error")
        return {
            "ok": False, "tool": "munin_chat", "mode": "sync",
            "summary": "agent error",
            "error": {"code": "agent_error", "message": str(exc)},
        }
    content = result.get("content", "")
    return {
        "ok": True,
        "tool": "munin_chat",
        "mode": "sync",
        "summary": content[:120] if content else "(no response)",
        "data": {
            "content": content,
            "tool_calls": result.get("tool_calls", []),
            "iterations": result.get("iterations", 0),
        },
    }


# ─────────────────────────────────────────────
# Code Inspection & Self-Diagnostics for Munin
# ─────────────────────────────────────────────

@MCP.tool()
@audited_tool("munin_read_source", "passive", lambda *a, **k: "sync")
def munin_read_source(rel_path: str = "", action: str = "list", run_id: str = "") -> dict[str, Any]:
    """Allows Munin to inspect its own codebase (source files under munin/ and app/).
    Action 'list': returns directory tree. Action 'read': reads a specific source file."""
    settings = _get_settings()
    base_dir = settings.munin_db_path.parent.resolve()  # Repository root

    if action == "list":
        allowed_dirs = ["munin", "app"]
        files_found: list[dict[str, Any]] = []
        for ad in allowed_dirs:
            target = base_dir / ad
            if target.exists():
                for p in target.glob("**/*"):
                    if p.is_file() and not any(part in p.parts for part in [".git", "__pycache__", "node_modules", ".next", ".venv", "out"]):
                        try:
                            rpath = p.relative_to(base_dir).as_posix()
                            files_found.append({"path": rpath, "size": p.stat().st_size})
                        except Exception:
                            pass
        return {
            "ok": True,
            "tool": "munin_read_source",
            "mode": "sync",
            "summary": f"{len(files_found)} source files listed",
            "data": {"files": files_found[:300], "count": len(files_found)},
        }

    if action == "read":
        if not rel_path.strip():
            return {"ok": False, "tool": "munin_read_source", "mode": "sync", "summary": "missing rel_path", "error": {"code": "bad_input", "message": "rel_path is required for action=read"}}
        candidate = (base_dir / rel_path.strip()).resolve()
        if base_dir not in candidate.parents and candidate != base_dir:
            return {"ok": False, "tool": "munin_read_source", "mode": "sync", "summary": "path escape", "error": {"code": "path_traversal", "message": "rel_path escapes repository root"}}
        if not candidate.exists() or not candidate.is_file():
            return {"ok": False, "tool": "munin_read_source", "mode": "sync", "summary": "file not found", "error": {"code": "not_found", "message": rel_path}}
        try:
            content = candidate.read_text(encoding="utf-8")
            return {
                "ok": True,
                "tool": "munin_read_source",
                "mode": "sync",
                "summary": f"read {rel_path} ({len(content)} chars)",
                "data": {"path": rel_path, "content": content, "size": len(content)},
            }
        except Exception as exc:
            return {"ok": False, "tool": "munin_read_source", "mode": "sync", "summary": "read failed", "error": {"code": "read_error", "message": str(exc)}}

    return {"ok": False, "tool": "munin_read_source", "mode": "sync", "summary": "invalid action", "error": {"code": "bad_action", "message": "action must be 'list' or 'read'"}}


@MCP.tool()
@audited_tool("munin_self_diagnose", "passive", lambda *a, **k: "sync")
def munin_self_diagnose(run_id: str = "") -> dict[str, Any]:
    """Self-diagnostic tool: scans system status, issues log (.ai/issues.md), tool health, and outputs a diagnostic summary + prompt for AI refactoring."""
    settings = _get_settings()
    base_dir = settings.munin_db_path.parent.resolve()
    issues_file = base_dir / ".ai" / "issues.md"

    known_issues = ""
    if issues_file.exists():
        try:
            known_issues = issues_file.read_text(encoding="utf-8")
        except Exception:
            pass

    import shutil
    binaries = {
        "nmap": shutil.which("nmap") is not None,
        "feroxbuster": shutil.which("feroxbuster") is not None,
        "nuclei": shutil.which("nuclei") is not None,
        "ffuf": shutil.which("ffuf") is not None,
        "sqlmap": shutil.which("sqlmap") is not None,
    }

    env_checks = {
        "LLM_BASE_URL": bool(settings.llm_base_url),
        "LLM_API_KEY": bool(settings.llm_api_key),
        "TAVILY_API_KEY": bool(settings.tavily_api_key),
    }

    ai_refactor_prompt = (
        "PROMPT REFACTORING MASTER PARA OTRA IA:\n\n"
        "Eres un desarrollador Senior especializado en Python/Starlette/MCP y Next.js.\n"
        "Tu objetivo es solucionar todos los problemas de herramientas, esquemas y tipos en Munin:\n\n"
        "1. LDAP: Mapear atributos AD (sAMAccountName, objectClass=group, container) a equivalentes OpenLDAP (uid/cn, posixGroup/groupOfNames, organizationalUnit).\n"
        "2. OPSEC: Limpiar descripciones artificiales ('Kerberoastable', 'AS-REP Roastable') en scripts/ldap_mock.ldif.\n"
        "3. TYPE BUGS: En munin/mcp/tools/, corregir comparaciones '<' entre int y str en memory_list, episodic_query y query_shared_intel castigando parametros limit/severity a int.\n"
        "4. SYSTEM BINARIES: Asegurar fallback limpio cuando nmap/feroxbuster no estan en PATH.\n"
        "5. FRONTEND: Solucionar validaciones de campos obligatorios en el Tool Explorer para fetch_agent_messages y soul_read.\n"
    )

    return {
        "ok": True,
        "tool": "munin_self_diagnose",
        "mode": "sync",
        "summary": "Munin Self-Diagnostic Complete",
        "data": {
            "binaries_installed": binaries,
            "env_configured": env_checks,
            "known_issues_loaded": bool(known_issues),
            "refactor_prompt": ai_refactor_prompt,
        },
    }


# ─────────────────────────────────────────────
# Wake/sleep orchestration
# ─────────────────────────────────────────────

@MCP.tool()
@audited_tool("munin_wake", "admin", lambda *a, **k: "sync")
def munin_wake(subagent: str, task_json: str = "{}", priority: int = 0, run_id: str = "") -> dict[str, Any]:
    """Enqueue a wake request for a subagent. The subagent's runner (a subprocess) claims and executes it."""
    if not subagent.strip():
        return {"ok": False, "tool": "munin_wake", "mode": "sync", "summary": "empty subagent", "error": {"code": "bad_input", "message": "subagent name required"}}
    try:
        task = json.loads(task_json or "{}")
        if not isinstance(task, dict):
            raise ValueError("task_json must be an object")
    except Exception as exc:
        return {"ok": False, "tool": "munin_wake", "mode": "sync", "summary": "bad task_json", "error": {"code": "bad_input", "message": str(exc)}}
    wake_id = STATE.enqueue_wake(target_agent=subagent.strip(), task=task, priority=priority)
    return {"ok": True, "tool": "munin_wake", "mode": "sync", "summary": f"wake queued for {subagent}", "data": {"wake_id": wake_id, "target_agent": subagent, "task": task}}


@MCP.tool()
@audited_tool("munin_wake_claim", "admin", lambda *a, **k: "sync")
def munin_wake_claim(subagent: str, claimer_pid: int, run_id: str = "") -> dict[str, Any]:
    """Called by a subagent's runner to claim the next wake item addressed to it."""
    item = STATE.claim_wake_item(target_agent=subagent.strip(), claimer_pid=claimer_pid)
    if not item:
        return {"ok": True, "tool": "munin_wake_claim", "mode": "sync", "summary": "no work", "data": {"claimed": False}}
    return {"ok": True, "tool": "munin_wake_claim", "mode": "sync", "summary": f"claimed wake {item['id']}", "data": {"claimed": True, **item}}


@MCP.tool()
@audited_tool("munin_wake_list", "passive", lambda *a, **k: "sync")
def munin_wake_list(subagent: str = "", include_claimed: bool = False, run_id: str = "") -> dict[str, Any]:
    """List pending (and optionally claimed) wake items."""
    items = STATE.list_wake_queue(target_agent=subagent, include_claimed=include_claimed)
    return {"ok": True, "tool": "munin_wake_list", "mode": "sync", "summary": f"{len(items)} wake items", "data": {"items": items, "count": len(items)}}
