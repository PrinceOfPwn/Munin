# tags: [mcp, mcp-tool, soul, memory, episodic-memory, subagent, orchestrator, list_generated_tools, munin_chat, munin_wake, soul_propose_edit, munin_read_source, munin_self_diagnose, read_wake_artifact, turso_conversation]
"""Munin-native MCP tools: wake/sleep orchestration, soul I/O, memory helpers, and
the catalog of generated tools (`list_generated_tools`) that Munin queries every ReAct
step before invoking `tool_forge`."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from ...core.conversations import ConversationService
from .. import registry  # noqa: TID252
from ..main import JOBS, MCP, STATE, audited_tool  # noqa: TID252
from ..shared_state import _coerce_int  # noqa: TID252,PLC2701

logger = logging.getLogger("munin-mcp.munin_tools")


def _get_settings() -> Any:
    from ..config import get_settings  # noqa: TID252 - re-read env each call so tests can monkeypatch

    return get_settings()


def _conversation_backend_error(settings: Any) -> dict[str, Any] | None:
    """Conversation history is intentionally remote-only.

    A local SQLite fallback would make a GUI look stateful during a runner's
    lifetime and then silently lose the operator's work. Reject it explicitly
    instead: durable conversations require the configured Turso/libsql URL.
    """
    url = str(getattr(settings, "db_url", "") or "").strip().lower()
    if url.startswith(("libsql://", "libsqls://")):
        return None
    return {
        "code": "turso_required",
        "message": "Persistent conversations require MUNIN_DB_URL to point to Turso (libsql:// or libsqls://); local SQLite and runner artifacts are disabled for chat history.",
    }


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
            script_path = registry.resolve_script_path(STATE.settings, row["script_path"])
            row = {**row, "script": script_path.read_text(encoding="utf-8")}
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
        callable_fn = registry._load_callable(
            registry.resolve_script_path(STATE.settings, row["script_path"]),
            row["signature"].get("function_name") or row["name"].replace("gen__", ""),
        )
        execution = registry.wrap_generated_callable(
            callable_fn,
            tool_name=row["name"],
            state=STATE,
        )(**args)
    except Exception as exc:
        return {"ok": False, "tool": "run_generated_tool", "mode": "sync", "summary": "exec failed", "error": {"code": "generated_tool_failed", "message": str(exc)}}
    if not execution.get("ok", False):
        return {
            "ok": False,
            "tool": "run_generated_tool",
            "mode": "sync",
            "summary": execution.get("summary", "exec failed"),
            "error": execution.get("error", {"code": "generated_tool_failed", "message": row["name"]}),
        }
    return {"ok": True, "tool": "run_generated_tool", "mode": "sync", "summary": row["name"], "data": execution.get("data", {})}


@MCP.tool()
@audited_tool("deactivate_generated_tool", "admin", lambda *a, **k: "sync")
def deactivate_generated_tool(name: str, run_id: str = "") -> dict[str, Any]:
    """Soft-delete a generated tool (marks active=0). Use `munin reset` for hard purge.

    Returns ``not_found`` explicitly when the name doesn't exist — before this
    the response was ``ok=False`` with a generic summary, which the frontend
    couldn't distinguish from a real deactivation failure.
    """
    ok = registry.deactivate(MCP, STATE, name)
    if not ok:
        return {
            "ok": False,
            "tool": "deactivate_generated_tool",
            "mode": "sync",
            "summary": f"no active tool named {name}",
            "error": {"code": "not_found", "message": name},
            "data": {"name": name, "deactivated": False},
        }
    return {
        "ok": True,
        "tool": "deactivate_generated_tool",
        "mode": "sync",
        "summary": f"deactivated {name}",
        "data": {"name": name, "deactivated": True},
    }


@MCP.tool()
@audited_tool("read_wake_artifact", "passive", lambda *a, **k: "sync")
def read_wake_artifact(
    wake_id: int,
    offset: int = 0,
    max_chars: int = 12000,
    run_id: str = "",
) -> dict[str, Any]:
    """Read a bounded chunk of a large subagent result by wake id."""
    normalized_wake_id = _coerce_int(wake_id, -1)
    normalized_offset = max(0, _coerce_int(offset, 0))
    normalized_limit = max(1, min(_coerce_int(max_chars, 12000), 50000))
    if normalized_wake_id < 1:
        return {
            "ok": False,
            "tool": "read_wake_artifact",
            "mode": "sync",
            "summary": "invalid wake id",
            "error": {"code": "bad_input", "message": "wake_id must be a positive integer"},
        }

    path = _get_settings().munin_data_path / "wake_artifacts" / f"wake_{normalized_wake_id}.json"
    if not path.is_file():
        return {
            "ok": False,
            "tool": "read_wake_artifact",
            "mode": "sync",
            "summary": "artifact not found",
            "error": {"code": "not_found", "message": f"wake {normalized_wake_id}"},
        }

    content = path.read_text(encoding="utf-8")
    chunk = content[normalized_offset:normalized_offset + normalized_limit]
    next_offset = normalized_offset + len(chunk)
    return {
        "ok": True,
        "tool": "read_wake_artifact",
        "mode": "sync",
        "summary": f"read wake {normalized_wake_id} artifact",
        "data": {
            "wake_id": normalized_wake_id,
            "content": chunk,
            "offset": normalized_offset,
            "next_offset": next_offset,
            "eof": next_offset >= len(content),
            "total_chars": len(content),
        },
    }


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
    """Propose a soul edit — human-in-the-loop.

    Always writes to ``data/soul_pending/`` (local file review). When Munin runs
    in a repo with ``MUNIN_AUTO_PR=1`` and the ``gh`` CLI is authenticated,
    ALSO opens a pull request tagged ``soul-proposal`` so the human operator
    can review, approve, and merge. That's the mechanism by which Munin's
    identity evolves session by session: it can propose, but never apply.

    Munin CAN'T rewrite its own identity in runtime. Both the local file drop
    and the PR are proposals; a human still has to merge.
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
    meta.write_text(
        json.dumps({"target_path": path, "rationale": rationale, "sha256": digest}, ensure_ascii=True),
        encoding="utf-8",
    )

    pr_info: dict[str, Any] = {"attempted": False}
    import os as _os  # noqa: PLC0415
    if _os.environ.get("MUNIN_AUTO_PR", "").strip() in ("1", "true", "yes", "on"):
        try:
            import shutil as _shutil  # noqa: PLC0415
            import subprocess as _subprocess  # noqa: PLC0415

            from ..git_persist import _ensure_git_identity, _repo_root, _run_git  # noqa: TID252,PLC0415
            repo = _repo_root()
            if repo is None:
                pr_info = {"attempted": True, "ok": False, "error": "not_a_git_repo"}
            elif _shutil.which("gh") is None:
                pr_info = {"attempted": True, "ok": False, "error": "gh_cli_not_installed"}
            else:
                branch = f"soul-proposal/{Path(path).stem}-{digest}"
                base_branch = _os.environ.get("MUNIN_PR_BASE_BRANCH", "main").strip() or "main"
                import tempfile as _tempfile  # noqa: PLC0415

                # Build the proposal in a disposable worktree. The live process
                # rereads soul/ on every turn, so its checkout must stay untouched.
                with _tempfile.TemporaryDirectory(prefix="munin-soul-proposal-") as temporary_dir:
                    worktree = Path(temporary_dir) / "worktree"
                    _run_git(["check-ref-format", "--branch", base_branch], cwd=repo)
                    _run_git(["fetch", "origin", base_branch], cwd=repo)
                    _run_git(
                        ["worktree", "add", "--detach", str(worktree), f"origin/{base_branch}"],
                        cwd=repo,
                    )
                    try:
                        _ensure_git_identity(worktree)
                        _run_git(["checkout", "-B", branch], cwd=worktree)
                        target = (worktree / "soul" / path).resolve()
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(new_content, encoding="utf-8")
                        _run_git(["add", "--", str(target)], cwd=worktree)
                        commit_msg = f"[munin][soul-proposal] {path}\n\n{rationale or '(no rationale supplied)'}\n\nsha256: {digest}"
                        commit = _run_git(["commit", "-m", commit_msg], cwd=worktree, check=False)
                        if commit.returncode != 0:
                            raise RuntimeError((commit.stderr or commit.stdout).strip()[-400:])
                        pushed = _run_git(["push", "-u", "origin", branch], cwd=worktree, check=False)
                        if pushed.returncode != 0:
                            raise RuntimeError((pushed.stderr or pushed.stdout).strip()[-400:])
                        pr_body = (
                            f"### Soul edit proposal by Munin\n\n"
                            f"**File:** `soul/{path}`\n"
                            f"**sha256:** `{digest}`\n"
                            f"**Rationale:** {rationale or '(no rationale supplied)'}\n\n"
                            "Merge to update Munin's identity. This branch is auto-generated — do not commit further work here."
                        )
                        pr = _subprocess.run(
                            ["gh", "pr", "create",
                             "--head", branch,
                             "--base", base_branch,
                             "--title", f"soul: {path} update",
                             "--body", pr_body,
                             "--label", "soul-proposal"],
                            cwd=worktree, capture_output=True, text=True, timeout=60, check=False,
                        )
                        if pr.returncode == 0:
                            pr_info = {"attempted": True, "ok": True, "branch": branch, "output": pr.stdout.strip()}
                        else:
                            pr_info = {"attempted": True, "ok": False, "error": (pr.stderr or pr.stdout).strip()[-400:]}
                    finally:
                        _run_git(["worktree", "remove", "--force", str(worktree)], cwd=repo, check=False)
        except Exception as exc:  # pragma: no cover — best effort
            pr_info = {"attempted": True, "ok": False, "error": f"exception: {exc}"}

    return {
        "ok": True,
        "tool": "soul_propose_edit",
        "mode": "sync",
        "summary": f"proposal queued at {proposal.name}"
                   + (f"; PR opened on {pr_info.get('branch', '?')}" if pr_info.get("ok") else ""),
        "data": {
            "proposal_path": str(proposal),
            "meta_path": str(meta),
            "sha256": digest,
            "pr": pr_info,
        },
    }


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
    # Coerce limit at tool boundary. Some MCP clients ship integer-typed params as
    # strings; propagating a string into the store previously triggered a TypeError
    # on `min(int, str)`. See _coerce_int docstring for detail.
    rows = STATE.semantic_list(prefix=prefix, limit=_coerce_int(limit, 100))
    return {"ok": True, "tool": "memory_list", "mode": "sync", "summary": f"{len(rows)} facts", "data": {"facts": rows, "count": len(rows)}}


@MCP.tool()
@audited_tool("episodic_query", "passive", lambda *a, **k: "sync")
def episodic_query(agent: str = "", action: str = "", limit: int = 100, run_id: str = "") -> dict[str, Any]:
    """Recent episodic events (tool calls, ReAct steps, orchestrator decisions)."""
    rows = STATE.episodic_query(agent=agent, action=action, limit=_coerce_int(limit, 100))
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
    # Pass STATE so the base function can attach the "forged" category with
    # every currently-registered gen__* tool. Without it Munin sees natives
    # only and never puts its own creations in whitelists it forges.
    return _list(category=category, state=STATE)


# ─────────────────────────────────────────────
# Conversational interface — munin_chat
# ─────────────────────────────────────────────

@MCP.tool()
@audited_tool("conversation_list", "passive", lambda *a, **k: "sync")
def conversation_list(limit: int = 50, include_archived: bool = False, run_id: str = "") -> dict[str, Any]:
    """List durable operator conversations stored exclusively in Turso."""
    settings = _get_settings()
    error = _conversation_backend_error(settings)
    if error:
        return {"ok": False, "tool": "conversation_list", "mode": "sync", "summary": "Turso required", "error": error}
    rows = STATE.conversation_list(limit=_coerce_int(limit, 50), include_archived=bool(include_archived))
    return {
        "ok": True,
        "tool": "conversation_list",
        "mode": "sync",
        "summary": f"{len(rows)} conversations",
        "data": {"conversations": rows, "count": len(rows)},
    }


@MCP.tool()
@audited_tool("conversation_get", "passive", lambda *a, **k: "sync")
def conversation_get(conversation_id: str, message_limit: int = 500, run_id: str = "") -> dict[str, Any]:
    """Load a persistent conversation and its downloadable artifacts from Turso."""
    settings = _get_settings()
    error = _conversation_backend_error(settings)
    if error:
        return {"ok": False, "tool": "conversation_get", "mode": "sync", "summary": "Turso required", "error": error}
    try:
        record = STATE.conversation_get(
            conversation_id=conversation_id,
            message_limit=_coerce_int(message_limit, 500),
        )
    except ValueError as exc:
        return {"ok": False, "tool": "conversation_get", "mode": "sync", "summary": "invalid conversation id", "error": {"code": "bad_input", "message": str(exc)}}
    if record is None:
        return {"ok": False, "tool": "conversation_get", "mode": "sync", "summary": "conversation not found", "error": {"code": "not_found", "message": conversation_id}}
    return {
        "ok": True,
        "tool": "conversation_get",
        "mode": "sync",
        "summary": record["conversation"]["title"] or conversation_id,
        "data": record,
    }


@MCP.tool()
@audited_tool("conversation_create", "passive", lambda *a, **k: "sync")
def conversation_create(conversation_id: str = "", title: str = "", run_id: str = "") -> dict[str, Any]:
    """Create a durable Turso conversation. Existing ids are idempotent."""
    settings = _get_settings()
    error = _conversation_backend_error(settings)
    if error:
        return {"ok": False, "tool": "conversation_create", "mode": "sync", "summary": "Turso required", "error": error}
    service = ConversationService(STATE)
    try:
        conversation = STATE.conversation_create(
            conversation_id=conversation_id.strip() or service.new_id(),
            title=title,
        )
    except ValueError as exc:
        return {"ok": False, "tool": "conversation_create", "mode": "sync", "summary": "invalid conversation id", "error": {"code": "bad_input", "message": str(exc)}}
    return {"ok": True, "tool": "conversation_create", "mode": "sync", "summary": conversation["title"] or "New conversation", "data": {"conversation": conversation}}


@MCP.tool()
@audited_tool("munin_chat", "passive", lambda *a, **k: str(k.get("mode", "sync")))
def munin_chat(
    message: str,
    max_iterations: int | None = None,
    mode: str = "sync",
    conversation_id: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    """Full conversational ReAct interface. Send natural language — Munin reasons,
    calls tools autonomously, and returns a response plus the tool-call log so the
    frontend can render each step as an inline card.
    Requires LLM_BASE_URL / LLM_API_KEY / LLM_MODEL to be configured.
    It runs until natural completion unless ``max_iterations`` is explicitly
    supplied. The runtime retains a 10,000-iteration safety ceiling.
    """
    if not message.strip():
        return {
            "ok": False, "tool": "munin_chat", "mode": "sync",
            "summary": "empty message",
            "error": {"code": "bad_input", "message": "message is required"},
        }
    settings = _get_settings()
    backend_error = _conversation_backend_error(settings)
    if backend_error:
        return {
            "ok": False, "tool": "munin_chat", "mode": "sync",
            "summary": "Turso required for persistent chat",
            "error": backend_error,
        }
    if not settings.llm_base_url or not settings.llm_api_key or not settings.llm_model:
        return {
            "ok": False, "tool": "munin_chat", "mode": "sync",
            "summary": "LLM not configured",
            "error": {
                "code": "config_missing",
                "message": "LLM_BASE_URL, LLM_API_KEY and LLM_MODEL must be set to use munin_chat",
            },
        }
    if mode not in {"sync", "async"}:
        return {
            "ok": False, "tool": "munin_chat", "mode": "sync",
            "summary": "bad mode",
            "error": {"code": "bad_input", "message": "mode must be sync or async"},
        }

    iterations = None if max_iterations is None else max(1, min(_coerce_int(max_iterations, 40), 10_000))
    conversation_service = ConversationService(STATE)
    try:
        prepared = conversation_service.prepare_turn(
            conversation_id=conversation_id,
            user_message=message.strip(),
        )
    except ValueError as exc:
        return {
            "ok": False, "tool": "munin_chat", "mode": "sync",
            "summary": "invalid conversation",
            "error": {"code": "bad_input", "message": str(exc)},
        }

    def execute(progress=None) -> dict[str, Any]:
        try:
            import asyncio  # noqa: PLC0415
            from ...core.runtime_adapter import supervisor_runner  # noqa: PLC0415

            try:
                from ...core.llm_client import LLMClient  # noqa: PLC0415

                llm_model = LLMClient(settings).make_langchain()
            except Exception as exc:
                raise RuntimeError(f"Failed to initialize configured model: {exc}") from exc

            async def _run() -> tuple[str, list, int, str]:
                content = ""
                stop_reason = "final_answer"
                pending: dict[str, dict] = {}
                tool_calls: list = []

                def _sink(event: dict) -> None:
                    if progress is not None:
                        progress(event)

                async for envelope in supervisor_runner(
                    message.strip(),
                    run_id=f"munin_chat-{prepared.conversation_id}",
                    conversation_id=prepared.conversation_id,
                    conversation_history=prepared.history,
                    store=STATE,
                    progress_sink=_sink,
                    model=llm_model,
                    max_iterations=iterations,
                ):
                    kind = envelope.get("kind")
                    if kind == "tool_intent":
                        call = {
                            "id": envelope.get("tool_call_id") or f"call-{len(tool_calls)}",
                            "name": envelope.get("tool_name", "unknown"),
                            "arguments": envelope.get("input", {}),
                            "status": "running",
                        }
                        pending[call["id"]] = call
                        tool_calls.append(call)
                    elif kind in {"tool_result", "tool_failed"}:
                        call = pending.get(envelope.get("tool_call_id", ""))
                        if call is not None:
                            call["status"] = "failed" if kind == "tool_failed" else "completed"
                            call["result"] = envelope.get("output") or envelope.get("error", "")
                    elif kind == "run_state":
                        if envelope.get("state") == "completed":
                            content = envelope.get("content", "") or content
                        elif envelope.get("state") in {"failed", "cancelled", "interrupted"}:
                            stop_reason = envelope.get("state", stop_reason)
                iterations_done = sum(1 for c in tool_calls)
                return content, tool_calls, iterations_done, stop_reason

            content, tool_calls, iterations_done, stop_reason = asyncio.run(_run())
        except Exception as exc:
            logger.exception("munin_chat: agent error")
            error_message = f"Munin could not complete this turn: {exc}"
            try:
                conversation_service.complete_turn(
                    conversation_id=prepared.conversation_id,
                    content=error_message,
                    tool_calls=[],
                    stop_reason="agent_error",
                    iterations=0,
                )
            except Exception:
                logger.exception("munin_chat: unable to persist failed turn")
            return {
                "ok": False, "tool": "munin_chat", "mode": "sync",
                "summary": "agent error",
                "error": {"code": "agent_error", "message": str(exc)},
            }
        try:
            assistant_message, artifacts = conversation_service.complete_turn(
                conversation_id=prepared.conversation_id,
                content=content,
                tool_calls=tool_calls,
                stop_reason=stop_reason,
                iterations=iterations_done,
            )
        except Exception as exc:
            logger.exception("munin_chat: unable to persist completed turn")
            return {
                "ok": False, "tool": "munin_chat", "mode": "sync",
                "summary": "conversation persistence error",
                "error": {"code": "conversation_persistence_error", "message": str(exc)},
            }
        return {
            "ok": True,
            "tool": "munin_chat",
            "mode": "sync",
            "summary": content[:120] if content else "(no response)",
            "data": {
                "conversation_id": prepared.conversation_id,
                "user_message_id": prepared.user_message_id,
                "assistant_message_id": assistant_message["id"],
                "content": content,
                "artifacts": artifacts,
                "tool_calls": tool_calls,
                "iterations": iterations_done,
                "stop_reason": stop_reason,
            },
        }

    if mode == "async":
        job = JOBS.submit(
            tool="munin_chat",
            level="passive",
            target="conversation",
            command_preview="ReAct conversation",
            fn=lambda current_job: execute(
                lambda event: JOBS.add_progress(current_job.job_id, event)
            ),
        )
        JOBS.add_progress(job.job_id, {"stage": "queued", "message": "Conversation queued"})
        return {
            "ok": True,
            "tool": "munin_chat",
            "mode": "async",
            "job_id": job.job_id,
            "summary": "Munin conversation started",
            "data": {
                "job_id": job.job_id,
                "status": job.status,
                "conversation_id": prepared.conversation_id,
                "user_message_id": prepared.user_message_id,
            },
        }

    return execute()


# ─────────────────────────────────────────────
# Code Inspection & Self-Diagnostics for Munin
# ─────────────────────────────────────────────

@MCP.tool()
@audited_tool("munin_read_source", "passive", lambda *a, **k: "sync")
def munin_read_source(rel_path: str = "", action: str = "list", run_id: str = "") -> dict[str, Any]:
    """Allows Munin to inspect its own codebase (source files under munin/ and app/).
    Action 'list': returns directory tree. Action 'read': reads a specific source file."""
    settings = _get_settings()
    base_dir = settings.workspace_root.resolve()  # repository root (Settings.munin_db_path never existed)

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
        # Restrict to the same subdirs that `list` allows. Without this, an
        # attacker-controlled LLM could read `.env` (API keys), `data/*` (SQLite
        # dumps), or `.git/config`. list_allowed_prefixes must match the `list`
        # branch above.
        allowed_prefixes = [(base_dir / d).resolve() for d in ("munin", "app")]
        if not any(candidate == p or p in candidate.parents for p in allowed_prefixes):
            return {
                "ok": False,
                "tool": "munin_read_source",
                "mode": "sync",
                "summary": "path outside allowed roots",
                "error": {
                    "code": "path_denied",
                    "message": "read is only allowed under munin/ or app/; secrets and data are off-limits",
                    "rel_path": rel_path,
                },
            }
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
    """Wake a subagent to handle a task: enqueues the task AND spawns the runner subprocess.

    Previously this only inserted a row into ``agent_wake_queue`` — the corresponding
    ``python -m munin.subagents.runner`` process was never started, so the wake item
    lived forever unclaimed. Now the call goes through :class:`Orchestrator.wake`
    which enqueues the task, spawns a detached runner subprocess, and updates the
    presence table. The runner picks the task up via ``claim_wake_item`` and
    executes it. Result is delivered back via ``agent_messages`` (poll with
    ``fetch_agent_messages``).

    Accepts either a native subagent (``ldap_agent``, ``tool_forge``, ``graph_forge``)
    or the name of a forged graph in ``generated_graphs``.
    """
    if not subagent.strip():
        return {"ok": False, "tool": "munin_wake", "mode": "sync", "summary": "empty subagent", "error": {"code": "bad_input", "message": "subagent name required"}}
    try:
        task = json.loads(task_json or "{}")
        if not isinstance(task, dict):
            raise ValueError("task_json must be an object")
    except Exception as exc:
        return {"ok": False, "tool": "munin_wake", "mode": "sync", "summary": "bad task_json", "error": {"code": "bad_input", "message": str(exc)}}

    priority_int = _coerce_int(priority, 0)

    # Verify the subagent actually exists — either as a native runner or as a
    # forged graph in generated_graphs. Fail early with a clear error otherwise;
    # otherwise the runner subprocess would crash after spawn and the item would
    # sit forever in the wake queue.
    # ``munin.subagents.runner`` (Arch A subprocess shim) was deleted in Fase 2
    # of the issue-#9 migration. Native subagents are now expressed as forged
    # graphs, so the native whitelist is empty and every target must resolve
    # via ``STATE.graph_get(...)``.
    _NATIVE_SUBAGENTS: frozenset[str] = frozenset()
    forged = None
    try:
        forged = STATE.graph_get(subagent.strip())
    except Exception:
        forged = None
    if subagent.strip() not in _NATIVE_SUBAGENTS and not forged:
        return {
            "ok": False,
            "tool": "munin_wake",
            "mode": "sync",
            "summary": f"unknown subagent: {subagent}",
            "error": {
                "code": "unknown_subagent",
                "message": (
                    f"'{subagent}' is neither a native runner "
                    f"({', '.join(sorted(_NATIVE_SUBAGENTS))}) nor a forged graph. "
                    "Use graph_forge first to create a specialist, then wake it."
                ),
            },
        }

    # Delegate to Orchestrator so enqueue + subprocess spawn happen together.
    try:
        from ...core.orchestrator import Orchestrator  # noqa: PLC0415
        orch = Orchestrator(STATE)
        info = orch.wake(subagent.strip(), task, priority=priority_int, detached=True)
    except Exception as exc:
        logger.exception("orchestrator.wake failed for %s", subagent)
        return {
            "ok": False,
            "tool": "munin_wake",
            "mode": "sync",
            "summary": f"failed to spawn runner for {subagent}",
            "error": {"code": "spawn_failed", "message": str(exc)},
        }

    return {
        "ok": True,
        "tool": "munin_wake",
        "mode": "sync",
        "summary": f"wake queued and runner spawned for {subagent} (pid={info.get('pid')})",
        "data": {**info, "task": task},
    }


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


# ─────────────────────────────────────────────
# Live subagent observability
# ─────────────────────────────────────────────

@MCP.tool()
@audited_tool("subagent_trace", "passive", lambda *a, **k: "sync")
def subagent_trace(
    subagent: str,
    since_id: int = 0,
    since_event_id: int | None = None,
    since_message_id: int | None = None,
    include_messages: bool = True,
    limit: int = 200,
    run_id: str = "",
) -> dict[str, Any]:
    """Return every episodic event + outbound message from a subagent, ordered oldest → newest.

    This is the observability channel the frontend uses to render live iteration
    of a running subagent: what tool it called, with what args, how long it took,
    whether the LLM produced a final answer or is still looping. Poll it every
    1-2 seconds with the last ``since_id`` you saw to get incremental updates.

    Parameters
    ----------
    subagent : agent_name to filter by (e.g. 'ldap_agent' or a forged graph name).
    since_id : legacy cursor applied to both streams when stream-specific cursors are omitted.
    since_event_id : return only episodic events with id above this cursor.
    since_message_id : return only agent messages with id above this cursor.
    include_messages : also include agent_messages this subagent posted to munin.
    limit    : maximum number of events + messages returned.

    The response shape is designed so a UI can concatenate every ``since_id``
    poll into a single append-only stream::

        {
          "events":  [{id, ts, action, input, output, tags}, ...],   # ordered by id ASC
          "messages":[{id, created_at, type, subject, body, status}, ...],
          "next_event_id": <last event id seen>,
          "next_message_id": <last message id seen>,
          "presence": {status, last_seen_at, current_task_id},
        }
    """
    if not subagent.strip():
        return {"ok": False, "tool": "subagent_trace", "mode": "sync",
                "summary": "empty subagent", "error": {"code": "bad_input", "message": "subagent required"}}
    since_id_int = _coerce_int(since_id, 0)
    event_since = _coerce_int(since_event_id, since_id_int) if since_event_id is not None else since_id_int
    message_since = _coerce_int(since_message_id, since_id_int) if since_message_id is not None else since_id_int
    limit_int = _coerce_int(limit, 200)

    # Use the incremental helpers — truly append-only, no lost middle for long runs.
    events = STATE.episodic_since(
        agent=subagent.strip(),
        since_id=event_since,
        limit=max(1, min(limit_int, 1000)),
    )

    messages_out: list[dict[str, Any]] = []
    if include_messages:
        # sender-filtered SQL — no window loss on a chatty system.
        messages_out = STATE.messages_from_sender_since(
            sender_agent=subagent.strip(),
            recipient_agent="munin",
            since_id=message_since,
            limit=max(1, min(limit_int, 500)),
        )

    # Presence snapshot for the UI to show status pill / dot
    presence_rows = STATE.list_presence(stale_after_seconds=3600)
    presence = next((p for p in presence_rows if p["agent_name"] == subagent.strip()), None)

    next_event = events[-1]["id"] if events else event_since
    next_message = messages_out[-1]["id"] if messages_out else message_since
    return {
        "ok": True,
        "tool": "subagent_trace",
        "mode": "sync",
        "summary": (
            f"{len(events)} events since {event_since}, "
            f"{len(messages_out)} msgs since {message_since} for {subagent}"
        ),
        "data": {
            "subagent": subagent.strip(),
            "events": events,
            "messages": messages_out,
            "next_event_id": next_event,
            "next_message_id": next_message,
            "next_since_id": next_event,
            "presence": presence,
        },
    }
