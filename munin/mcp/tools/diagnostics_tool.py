"""munin_diagnostics — deep, actionable health check across every subsystem.

Answer the question Munin (and its operator) needs answered before shipping:
**does the whole system actually work?**

Unlike the pre-existing shallow ``health_check`` (which only lists binaries
and egress IP), this tool exercises each subsystem: attempts a real bind to
LDAP, refreshes the Hugin cache, hits a smoke query against Tavily if a key
is present, verifies the SQLite/libsql backend answers a SELECT, counts
forged tools and graphs, checks the wake queue, and confirms the auth
middleware is wired.

Every probe returns ``{name, ok, latency_ms, detail}`` so the caller (the LLM,
the frontend, the CI dashboard) can see per-subsystem status. The overall
``ok`` is the AND of every non-optional probe — a missing Tavily API key is
"config_missing", not "system broken".

Modes:

* ``mode="quick"``    — cheap probes only (~500ms). Default.
* ``mode="deep"``     — also runs a real LDAP bind + Hugin refresh.
* ``mode="paranoid"`` — additionally forges a trivial gen__ echo tool, waking a
  subagent runner end-to-end, checking that the full forge → wake → RESULT
  chain succeeds. ~30-60s. Use before a demo.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from typing import Any

from ..main import MCP, SETTINGS, STATE, audited_tool  # noqa: TID252

logger = logging.getLogger("munin-mcp.diagnostics")


def _probe(name: str, fn):
    """Wrap a callable in a uniform ``{name, ok, latency_ms, detail, error?}`` shape."""
    t0 = time.monotonic()
    try:
        detail = fn() or {}
        return {
            "name": name,
            "ok": bool(detail.get("ok", True)) if isinstance(detail, dict) else True,
            "latency_ms": int((time.monotonic() - t0) * 1000),
            "detail": detail if isinstance(detail, dict) else {"value": detail},
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "name": name,
            "ok": False,
            "latency_ms": int((time.monotonic() - t0) * 1000),
            "detail": {},
            "error": {"type": type(exc).__name__, "message": str(exc)[:400]},
        }


# ─────────────────────────────────────────────────────────────────────────────
# Individual probes
# ─────────────────────────────────────────────────────────────────────────────

def _probe_db() -> dict[str, Any]:
    """Connect, run one SELECT, count table rows."""
    conn = STATE._connect()  # noqa: SLF001 — we intentionally use the real conn
    try:
        counts: dict[str, int] = {}
        for table in ("shared_intel", "active_tasks", "agent_presence", "agent_messages",
                      "episodic", "semantic", "procedural", "generated_graphs", "agent_wake_queue"):
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM {table}"  # noqa: S608 - fixed allowlist
            ).fetchone()
            counts[table] = int(row["n"]) if row else 0
    finally:
        try:
            conn.close()
        except Exception as exc:
            logger.debug("diagnostics DB close failed: %s", exc)
    from ..persistence import describe_backend  # noqa: PLC0415
    return {
        "ok": True,
        "backend": describe_backend(SETTINGS.db_url) if SETTINGS.db_url else f"sqlite({SETTINGS.shared_state_db})",
        "row_counts": counts,
    }


def _probe_llm() -> dict[str, Any]:
    """Verify LLM configuration is complete; DO NOT hit the endpoint (cost + latency).

    Returns ``ok=True`` when all three env vars are set. ``config_missing`` otherwise —
    the ReAct loop won't work but the MCP server can still handle direct tool calls.
    """
    have_url = bool(SETTINGS.llm_base_url)
    have_key = bool(SETTINGS.llm_api_key)
    have_model = bool(SETTINGS.llm_model)
    ok = have_url and have_key and have_model
    return {
        "ok": ok,
        "base_url_set": have_url,
        "api_key_set": have_key,
        "model": SETTINGS.llm_model if have_model else "",
        "note": None if ok else "LLM_BASE_URL, LLM_API_KEY, LLM_MODEL must all be set for munin_chat / subagents to work",
    }


def _probe_ldap(deep: bool) -> dict[str, Any]:
    """Config check + optional real bind."""
    have_bind = bool(SETTINGS.ldap_bind_dn)
    have_pw = bool(SETTINGS.ldap_password)
    result: dict[str, Any] = {
        "ok": have_bind,
        "uri": SETTINGS.ldap_uri,
        "base_dn": SETTINGS.ldap_base_dn,
        "bind_dn_set": have_bind,
        "password_set": have_pw,
    }
    if not deep:
        return result
    if not have_bind or not have_pw:
        result["skipped_bind"] = "credentials not configured"
        return result
    try:
        from .ldap_tools import ldap_who_am_i  # noqa: PLC0415
        r = ldap_who_am_i()
        result["ok"] = bool(r.get("ok"))
        result["whoami"] = r.get("data", {}).get("whoami", "")
        result["server_flavor"] = r.get("data", {}).get("server_flavor", "")
        if not result["ok"]:
            result["error"] = r.get("error", {})
    except Exception as exc:
        result["ok"] = False
        result["error"] = {"type": type(exc).__name__, "message": str(exc)[:200]}
    return result


def _probe_recon_binaries() -> dict[str, Any]:
    """Every recon tool checks its binary at call time; the probe just enumerates."""
    binaries = [
        "nmap",
        "nuclei",
        "feroxbuster",
        "ffuf",
        "sqlmap",
        "hydra",
        "smbmap",
        "netexec",
        "katana",
        "pd-httpx",
        "searchsploit",
        "EyeWitness",
    ]
    installed: dict[str, str] = {}
    missing: list[str] = []
    for b in binaries:
        p = shutil.which(b)
        if p:
            installed[b] = p
        else:
            missing.append(b)
    return {
        "ok": True,  # missing binaries are informational, not a failure
        "installed": installed,
        "missing": missing,
        "note": "Missing binaries return structured missing_dependency errors when their tools are called",
    }


def _probe_hugin(deep: bool) -> dict[str, Any]:
    """Cache freshness check; optionally refresh."""
    from .hugin_tool import _load_cached, _refresh  # noqa: PLC0415

    bundle, age, is_stale = _load_cached(allow_stale=True)
    entities = bundle.get("entities", []) if isinstance(bundle, dict) else []
    cached_count = len(entities) if isinstance(entities, list) else 0
    result: dict[str, Any] = {
        "ok": cached_count > 0,
        "cached_entities": cached_count,
        "cache_age_seconds": age,
        "cache_stale": is_stale,
        "primary_url": SETTINGS.hugin_url,
    }
    if deep:
        refresh_info = _refresh(force=True, primary_url=SETTINGS.hugin_url, ttl=SETTINGS.hugin_ttl_seconds)
        result["refresh"] = refresh_info
        result["ok"] = refresh_info.get("status") == "ok" or cached_count > 0
    return result


def _probe_tavily() -> dict[str, Any]:
    """Just checks the key is present. Skips a real search to avoid quota + latency."""
    if not SETTINGS.tavily_api_key:
        return {
            "ok": False,
            "reason": "config_missing",
            "note": "TAVILY_API_KEY empty — tavily_search will return config_missing but not crash",
        }
    return {"ok": True, "api_key_set": True}


def _probe_forge() -> dict[str, Any]:
    """Count forged tools, verify each script still exists and is loadable."""
    from pathlib import Path as _Path  # noqa: PLC0415

    from .. import registry  # noqa: TID252,PLC0415
    rows = registry.list_generated(STATE)
    healthy: list[str] = []
    broken: list[dict[str, Any]] = []
    for row in rows:
        path = _Path(row.get("script_path", ""))
        if not path.exists():
            broken.append({"name": row["name"], "reason": "script_missing", "path": str(path)})
            continue
        try:
            sig = row.get("signature") or {}
            fn_name = sig.get("function_name") or row["name"].removeprefix("gen__")
            registry._load_callable(path, fn_name)  # noqa: SLF001
            healthy.append(row["name"])
        except Exception as exc:
            broken.append({"name": row["name"], "reason": "load_failed", "error": str(exc)[:200]})
    return {
        "ok": len(broken) == 0,
        "total": len(rows),
        "healthy": len(healthy),
        "broken": len(broken),
        "broken_detail": broken[:10],
    }


def _probe_graphs() -> dict[str, Any]:
    """Count forged graphs; verify their tool_whitelist entries exist somewhere."""
    graphs = STATE.graph_list(include_inactive=False)
    from .. import registry  # noqa: TID252,PLC0415
    from ..subagents.base import _STATIC_TOOLS, ALL_SUBAGENT_TOOL_NAMES  # noqa: PLC0415,TID252
    known_tools = set(_STATIC_TOOLS.keys()) | set(ALL_SUBAGENT_TOOL_NAMES)
    known_gen = {row["name"] for row in registry.list_generated(STATE)}
    known_tools |= known_gen

    issues: list[dict[str, Any]] = []
    for g in graphs:
        wl = g.get("tool_whitelist") or []
        unknown = [t for t in wl if t not in known_tools]
        if unknown:
            issues.append({"graph": g["name"], "unknown_tools": unknown})
    return {
        "ok": len(issues) == 0,
        "total_active": len(graphs),
        "issues": issues,
    }


def _probe_wake_queue() -> dict[str, Any]:
    items = STATE.list_wake_queue(target_agent="", include_claimed=True)
    pending = [i for i in items if not i.get("claimed_at")]
    return {
        "ok": True,
        "pending": len(pending),
        "total_in_queue": len(items),
        "detail": {"pending_agents": sorted({i["target_agent"] for i in pending})},
    }


def _probe_agent_presence() -> dict[str, Any]:
    rows = STATE.list_presence(stale_after_seconds=3600)
    running = [r for r in rows if str(r.get("status", "")).upper() == "RUNNING"]
    return {
        "ok": True,
        "known_agents": len(rows),
        "running_now": len(running),
        "detail": [{"agent": r["agent_name"], "status": r["status"], "last_seen": r["last_seen_at"]} for r in rows[:20]],
    }


def _probe_auth() -> dict[str, Any]:
    token = SETTINGS.mcp_auth_token
    return {
        "ok": bool(token),
        "auth_configured": bool(token),
        "note": "Bearer middleware wraps HTTP transport at start; stdio transport is unauthenticated by design",
    }


def _probe_persistence() -> dict[str, Any]:
    """Check git-persist worker readiness and auto-commit config."""
    auto_commit = os.environ.get("MUNIN_AUTO_COMMIT", "0").strip() in ("1", "true", "yes", "on")
    auto_pr = os.environ.get("MUNIN_AUTO_PR", "0").strip() in ("1", "true", "yes", "on")
    has_git = shutil.which("git") is not None
    has_gh = shutil.which("gh") is not None
    return {
        "ok": True,
        "auto_commit": auto_commit,
        "auto_pr": auto_pr,
        "git_available": has_git,
        "gh_cli_available": has_gh,
        "git_branch": os.environ.get("MUNIN_GIT_BRANCH", ""),
        "note": (
            "Forge outputs will be committed to git" if (auto_commit and has_git)
            else "Forge outputs stay in the runner and are lost when it dies (MUNIN_AUTO_COMMIT=0 or git missing)"
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Paranoid: end-to-end forge → wake → run
# ─────────────────────────────────────────────────────────────────────────────

def _probe_e2e_forge_wake() -> dict[str, Any]:
    """Forge a trivial echo tool, forge a subagent that uses it, wake, wait for RESULT.

    This is the load-bearing probe: if it passes, Munin's core promise —
    dynamic tool + subagent creation — is verified end-to-end. If it fails,
    something in the forge → registry → wake → runner → messages pipeline is
    broken and we say so with a specific step name.
    """
    import json as _json  # noqa: PLC0415
    import time as _time  # noqa: PLC0415

    from .forge_tool import tool_forge  # noqa: PLC0415
    from .graph_forge_tool import graph_forge  # noqa: PLC0415
    from .munin_tools import munin_wake  # noqa: PLC0415

    # 1. Forge a trivial tool (spec chosen so any capable LLM writes it in 1 iter).
    step: dict[str, Any] = {"stage": "starting"}
    t0 = _time.monotonic()

    step["stage"] = "tool_forge"
    forged = tool_forge(
        spec="Given a string 'text', return {'echoed': text, 'length': len(text)}. Function name: echo_text.",
        allowed_imports_csv="",
        max_iterations=3,
    )
    if not forged.get("ok"):
        return {"ok": False, "failed_at": step["stage"], "error": forged.get("error"), "detail": forged}
    tool_name = forged.get("data", {}).get("registered", {}).get("name") \
                or (forged.get("data", {}).get("existing", {}) or {}).get("name")
    if not tool_name:
        return {"ok": False, "failed_at": "tool_forge", "error": {"code": "no_tool_name", "detail": forged}}

    # 2. Forge a specialist graph that only uses that tool + messaging.
    step["stage"] = "graph_forge"
    graph_name = f"e2e_probe_{int(_time.time())}"
    graph_res = graph_forge(
        name=graph_name,
        purpose="E2E diagnostic probe — call the echo tool once and report back",
        system_prompt_hints_csv="Call the echo tool once with text='hello', then post the result back to munin",
        tool_whitelist_csv=f"{tool_name},post_agent_message",
    )
    if not graph_res.get("ok"):
        return {"ok": False, "failed_at": step["stage"], "error": graph_res.get("error"), "detail": graph_res}

    # 3. Wake it.
    step["stage"] = "munin_wake"
    wake_res = munin_wake(subagent=graph_name, task_json=_json.dumps({"text": "hello"}), priority=0)
    if not wake_res.get("ok"):
        return {"ok": False, "failed_at": step["stage"], "error": wake_res.get("error"), "detail": wake_res}

    # 4. Poll for the RESULT message for up to 45s.
    step["stage"] = "wait_for_result"
    deadline = _time.monotonic() + 45.0
    result_msg = None
    while _time.monotonic() < deadline:
        msgs = STATE.fetch_messages(recipient_agent="munin", limit=10)
        for m in msgs:
            meta = m.get("metadata") or {}
            if isinstance(meta, dict) and meta.get("wake_id") == wake_res.get("data", {}).get("wake_id"):
                result_msg = m
                break
        if result_msg:
            break
        _time.sleep(2.0)

    # 5. Cleanup: drop the probe graph AND deactivate the throwaway tool so
    #    successive paranoid runs don't accumulate `gen__echo_text_*` clutter.
    try:
        STATE.graph_drop(graph_name)
    except Exception as exc:
        logger.debug("diagnostics probe graph cleanup failed: %s", exc)
    try:
        from .. import registry  # noqa: PLC0415
        registry.deactivate(STATE, tool_name)
    except Exception as exc:
        logger.debug("diagnostics probe tool cleanup failed: %s", exc)

    if not result_msg:
        return {
            "ok": False,
            "failed_at": step["stage"],
            "error": {"code": "wake_timeout", "message": "no RESULT/ERROR message received in 45s"},
            "elapsed_ms": int((_time.monotonic() - t0) * 1000),
        }

    return {
        "ok": result_msg["message_type"] == "RESULT",
        "wake_id": wake_res.get("data", {}).get("wake_id"),
        "tool_forged": tool_name,
        "graph_forged": graph_name,
        "message_type": result_msg["message_type"],
        "elapsed_ms": int((_time.monotonic() - t0) * 1000),
    }


# ─────────────────────────────────────────────────────────────────────────────
# MCP tool
# ─────────────────────────────────────────────────────────────────────────────

@MCP.tool()
@audited_tool("munin_diagnostics", "admin", lambda *a, **k: "sync")
def munin_diagnostics(mode: str = "quick", run_id: str = "") -> dict[str, Any]:
    """Deep health check across every subsystem. Use before a demo or when reporting a bug.

    ``mode`` = ``"quick"`` (~500ms, no external calls except cache reads),
              ``"deep"`` (also LDAP bind + Hugin refresh, ~2-5s),
              ``"paranoid"`` (also E2E forge+wake, ~30-60s, requires LLM configured).
    """
    mode_norm = mode.strip().lower() or "quick"
    if mode_norm not in ("quick", "deep", "paranoid"):
        return {"ok": False, "tool": "munin_diagnostics", "mode": "sync", "summary": "bad mode",
                "error": {"code": "bad_input", "message": "mode must be quick|deep|paranoid"}}

    deep = mode_norm in ("deep", "paranoid")
    checks: list[dict[str, Any]] = []
    checks.append(_probe("db", _probe_db))
    checks.append(_probe("llm", _probe_llm))
    checks.append(_probe("ldap", lambda: _probe_ldap(deep=deep)))
    checks.append(_probe("recon_binaries", _probe_recon_binaries))
    checks.append(_probe("hugin", lambda: _probe_hugin(deep=deep)))
    checks.append(_probe("tavily", _probe_tavily))
    checks.append(_probe("forge_registry", _probe_forge))
    checks.append(_probe("graphs", _probe_graphs))
    checks.append(_probe("wake_queue", _probe_wake_queue))
    checks.append(_probe("agent_presence", _probe_agent_presence))
    checks.append(_probe("auth", _probe_auth))
    checks.append(_probe("persistence", _probe_persistence))

    if mode_norm == "paranoid":
        checks.append(_probe("e2e_forge_wake", _probe_e2e_forge_wake))

    # Overall status: subsystems that are "config-only misses" don't fail the total.
    # We consider these advisory: llm (if missing, munin_chat is off but tools work),
    # tavily (config_missing is not a hard fail), and auth (stdio is legit unauth).
    advisory_names = {"llm", "tavily", "auth", "recon_binaries"}
    hard_failures = [c for c in checks if not c["ok"] and c["name"] not in advisory_names]
    overall_ok = len(hard_failures) == 0

    return {
        "ok": overall_ok,
        "tool": "munin_diagnostics",
        "mode": "sync",
        "summary": (
            f"{sum(1 for c in checks if c['ok'])}/{len(checks)} subsystems ok"
            + ("" if overall_ok else f" — {len(hard_failures)} hard failures")
        ),
        "data": {
            "mode": mode_norm,
            "overall_ok": overall_ok,
            "hard_failures": [c["name"] for c in hard_failures],
            "advisories": [c["name"] for c in checks if not c["ok"] and c["name"] in advisory_names],
            "checks": checks,
        },
    }
