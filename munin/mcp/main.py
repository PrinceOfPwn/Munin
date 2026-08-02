from __future__ import annotations

import argparse
import functools
import json
import logging
import os
import re
import shlex
import signal
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

# ``python -m munin.mcp.main`` executes this file as ``__main__``.  Extension
# modules import ``munin.mcp.main`` to obtain the shared FastMCP singleton;
# without this alias Python constructs a second module (and its second MCP
# instance), leaving those tools absent from the running server.  The CLI uses
# the canonical import already, while this makes direct module execution safe
# too.
if __name__ == "__main__" and __package__:
    sys.modules.setdefault(f"{__package__}.main", sys.modules[__name__])

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from munin.mcp.audit import AuditTrailLogger
    from munin.mcp.config import get_settings, safe_slug
    from munin.mcp.intel import VulnIntelService
    from munin.mcp.jobs import JobManager
    from munin.mcp.opsec import ExecutionEngine, OpsecError, command_exists
    from munin.mcp.shared_state import SharedStateStore
    from munin.mcp.syncer import WikiGitSyncer
    from munin.mcp.utils import parse_targets, shell_join, split_extra_args, utc_now_iso
else:
    from .audit import AuditTrailLogger
    from .config import get_settings, safe_slug
    from .intel import VulnIntelService
    from .jobs import JobManager
    from .opsec import ExecutionEngine, OpsecError, command_exists
    from .shared_state import SharedStateStore
    from .syncer import WikiGitSyncer
    from .utils import parse_targets, shell_join, split_extra_args, utc_now_iso

# Structured logging — every line is prefixed with [munin-mcp] LEVEL + tool trace id.
# Set MUNIN_LOG_LEVEL=DEBUG in .env for verbose call tracing.
_LOG_LEVEL = os.environ.get("MUNIN_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, _LOG_LEVEL, logging.INFO),
    format="[munin-mcp] %(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("munin-mcp")


# ---- Secret redaction for tool argument logging ----
# Any kwarg NAME matching one of these keywords gets its VALUE redacted before
# the audit log records it. Additional per-value pattern scrubbing runs on
# free-form strings (Bearer tokens, api keys) to catch cases where a secret
# ends up embedded inside a compound argument (e.g. an entire curl command).
_SECRET_KEY_PATTERNS = re.compile(r"(pass|passwd|password|token|api[_-]?key|secret|bearer|authorization)", re.IGNORECASE)
_SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]+"),
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"gsk_[A-Za-z0-9]{16,}"),
    re.compile(r"nvapi-[A-Za-z0-9]{16,}"),
    re.compile(r"tvly-[A-Za-z0-9]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{16,}"),
    re.compile(r"\"api_key\"\s*:\s*\"[^\"]+\""),
)


def _redact_scalar(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                pass
            else:
                return _redact_payload(parsed)
        redacted = value
        for pat in _SECRET_VALUE_PATTERNS:
            redacted = pat.sub("[REDACTED]", redacted)
        return redacted
    return value


def _redact_args(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of kwargs with secrets redacted. Never mutates input."""
    redacted: dict[str, Any] = {}
    for key, value in kwargs.items():
        if _SECRET_KEY_PATTERNS.search(key):
            redacted[key] = "[REDACTED]" if value else ""
        else:
            redacted[key] = _redact_payload(value)
    return redacted


def _redact_payload(value: Any) -> Any:
    """Recursively redact secrets before durable tool traces reach SQLite/Turso."""
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _SECRET_KEY_PATTERNS.search(str(key)) and item else _redact_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_payload(item) for item in value]
    return _redact_scalar(value)


def _bounded_trace_payload(value: Any) -> Any:
    redacted = _redact_payload(value)

    rendered = json.dumps(redacted, ensure_ascii=True, default=str)
    if len(rendered) <= SETTINGS.max_output_chars:
        return redacted
    return {
        "truncated": True,
        "original_chars": len(rendered),
        "preview": rendered[: SETTINGS.max_output_chars],
    }


SETTINGS = get_settings()
ENGINE = ExecutionEngine(SETTINGS)
AUDIT = AuditTrailLogger(SETTINGS.workspace_root)
JOBS = JobManager(SETTINGS.job_workers)
INTEL = VulnIntelService(SETTINGS)
SYNCER = WikiGitSyncer(SETTINGS)
STATE = SharedStateStore(SETTINGS)
MCP = FastMCP("munin-mcp")
ORPHAN_TTL_SECONDS = 600
HOST_OPSEC_SNAPSHOT = SETTINGS.workspace_root / "intel" / "host_opsec_snapshot.json"


def _kill_orphaned_stdio_processes() -> int:
    """Kill stdio processes older than ORPHAN_TTL. Portable across macOS + Linux.

    Uses `ps -eo pid,etime,args` (POSIX) without the GNU-only --no-headers flag and
    parses etime manually. Silently no-ops on Windows or if ps is missing.
    """
    my_pid = os.getpid()
    killed = 0
    try:
        import subprocess

        if os.name == "nt":
            return 0
        out = subprocess.run(
            ["ps", "-eo", "pid,etime,args"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        for line in (out.stdout or "").strip().splitlines()[1:]:
            parts = line.strip().split(None, 2)
            if len(parts) < 3:
                continue
            pid_str, etime_str, args_str = parts
            if "munin/mcp/main.py --transport stdio" not in args_str and "munin.mcp.main --transport stdio" not in args_str:
                continue
            try:
                pid = int(pid_str)
                seconds = _etime_to_seconds(etime_str)
            except ValueError:
                continue
            if pid == my_pid or seconds < ORPHAN_TTL_SECONDS:
                continue
            try:
                os.kill(pid, signal.SIGTERM)
                killed += 1
            except (ProcessLookupError, PermissionError):
                pass
    except Exception:  # pragma: no cover - guardrail
        pass
    return killed


def _etime_to_seconds(etime: str) -> int:
    """`ps` etime: `[[DD-]HH:]MM:SS`."""
    days = 0
    if "-" in etime:
        d, etime = etime.split("-", 1)
        days = int(d)
    parts = [int(p) for p in etime.split(":")]
    if len(parts) == 3:
        h, m, s = parts
    elif len(parts) == 2:
        h, m, s = 0, parts[0], parts[1]
    else:
        return 0
    return days * 86400 + h * 3600 + m * 60 + s


# Orphan cleanup moved into main() — running it at import time SIGTERM'd any
# long-lived stdio Munin process on the host every time this module was imported,
# including tests, `poetry run munin --help`, IDE static analysis, etc.
ORPHANS_KILLED = 0


def _artifact_path(run_id: str, *parts: str) -> Path:
    path = SETTINGS.workspace_root / "runs" / run_id
    for part in parts:
        path = path / part
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_host_opsec_snapshot() -> dict[str, Any]:
    if not HOST_OPSEC_SNAPSHOT.exists():
        return {}
    try:
        import json

        return json.loads(HOST_OPSEC_SNAPSHOT.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _httpx_binary() -> str:
    # Deployments which install the ProjectDiscovery binary in a dedicated
    # location can make the selection deterministic.  This also prevents an
    # unrelated ``httpx``/``pd-httpx`` already present on PATH from silently
    # becoming the active reconnaissance tool.
    configured = os.environ.get("MUNIN_HTTPX_BINARY", "").strip()
    if configured:
        return configured
    for candidate in ("pd-httpx", "/usr/local/bin/pd-httpx"):
        if command_exists(candidate) or Path(candidate).exists():
            return candidate
    return "httpx"


def _result_from_exception(tool: str, exc: Exception, mode: str = "sync") -> dict[str, Any]:
    code = "opsec_failed" if isinstance(exc, OpsecError) else "tool_failed"
    return {
        "ok": False,
        "tool": tool,
        "mode": mode,
        "summary": f"{tool} failed: {exc}",
        "error": {"code": code, "message": str(exc)},
    }


def audited_tool(
    tool_name: str,
    level: str,
    mode_resolver: Callable[..., str] | None = None,
) -> Callable[[Callable[..., dict[str, Any]]], Callable[..., dict[str, Any]]]:
    """Wrap a tool function with audit logging, structured console logging, and a
    per-call trace id (visible in server logs so a slow/broken tool call can be
    matched against the corresponding audit event).

    Structured console log — one line at INFO on entry, one line at INFO on exit:

        munin-mcp INFO [<trace_id>] → ldap_search args={...}
        munin-mcp INFO [<trace_id>] ← ldap_search ok=True 42ms summary="ldap_search: 14 entries"

    On error we also DEBUG the traceback with the trace_id. Exceptions thrown by
    the wrapped function no longer bubble up — they're captured, converted to a
    structured error result (see ``_result_from_exception``), and the exception
    is logged at ERROR level with the trace_id so the operator can grep for it.
    """
    def decorator(func: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            run_id = kwargs.get("run_id", "").strip() or safe_slug([utc_now_iso(), tool_name])
            kwargs["run_id"] = run_id
            started_at = utc_now_iso()
            mode = mode_resolver(*args, **kwargs) if mode_resolver else str(kwargs.get("mode", "sync"))
            target = str(
                kwargs.get("target")
                or kwargs.get("url")
                or kwargs.get("targets")
                or kwargs.get("cve_id")
                or kwargs.get("indicator")
                or kwargs.get("organization")
                or kwargs.get("query")
                or kwargs.get("resource")
                or kwargs.get("domain")
                or kwargs.get("cve_or_product")
                or ""
            )
            source_context = "munin-mcp"
            trace_id = uuid.uuid4().hex[:8]
            t0 = time.monotonic()
            log_args = _redact_args({k: v for k, v in kwargs.items() if k != "run_id"})
            logger.info("[%s] → %s args=%s", trace_id, tool_name, log_args)

            try:
                result = func(*args, **kwargs)
            except Exception as exc:  # pragma: no cover - guardrail
                logger.exception("[%s] ✗ %s raised: %s", trace_id, tool_name, exc)
                result = _result_from_exception(tool_name, exc, mode=mode)

            elapsed_ms = int((time.monotonic() - t0) * 1000)
            ok = bool(result.get("ok", False))
            summary = result.get("summary", "")
            if ok:
                logger.info(
                    "[%s] ← %s ok=True %dms summary=%r", trace_id, tool_name, elapsed_ms, summary[:200]
                )
            else:
                err = result.get("error") or {}
                logger.warning(
                    "[%s] ← %s ok=False %dms code=%s summary=%r",
                    trace_id, tool_name, elapsed_ms, err.get("code", "?"), summary[:200],
                )

            # Stash the trace_id in the result so callers (frontend, CLI) can quote it
            # when reporting bugs. Never overwrite an existing key.
            result.setdefault("trace_id", trace_id)
            result.setdefault("elapsed_ms", elapsed_ms)

            artifacts = result.get("artifacts", []) or []
            data = result.get("data", {}) or {}
            try:
                AUDIT.record(
                    run_id=run_id,
                    tool=tool_name,
                    level=level,
                    mode=mode,
                    status="ok" if ok else "error",
                    target=target,
                    source_context=source_context,
                    command_or_params=log_args,  # redacted
                    job_id=result.get("job_id", ""),
                    artifacts=artifacts,
                    opsec_preflight=data.get("opsec_preflight", {}),
                    started_at=started_at,
                    finished_at=utc_now_iso(),
                    summary=summary,
                )
            except Exception as audit_exc:
                logger.warning("[%s] AUDIT.record failed for %s: %s", trace_id, tool_name, audit_exc)
            try:
                STATE.episodic_record(
                    agent=str(
                        kwargs.get("source_agent")
                        or kwargs.get("created_by_agent")
                        or kwargs.get("sender_agent")
                        or "munin-mcp"
                    ),
                    action=f"tool:{tool_name}",
                    input_data=log_args,
                    output_data=_bounded_trace_payload(result),
                    tags=["tool", level, mode],
                )
            except Exception as persistence_exc:
                logger.warning(
                    "[%s] persistent trace failed for %s: %s",
                    trace_id,
                    tool_name,
                    persistence_exc,
                )
            return result

        # Preserve the audited risk level on the FastMCP callable.  The
        # Deep Agents builder derives its native HITL policy from this live
        # registry metadata rather than from a stale second name list.
        wrapper.__munin_audit_level__ = level  # type: ignore[attr-defined]
        wrapper.__munin_tool_name__ = tool_name  # type: ignore[attr-defined]
        return wrapper

    return decorator


def _submit_command_job(
    *,
    tool: str,
    level: str,
    target: str,
    command: str,
    timeout: int,
    run_id: str,
    artifacts: list[str] | None = None,
) -> dict[str, Any]:
    started_at = utc_now_iso()

    def on_finish(job: Any) -> None:
        result = job.result or {}
        data = result.get("data", {}) or {}
        try:
            AUDIT.record(
                run_id=run_id,
                tool=tool,
                level=level,
                mode="async",
                status=job.status,
                target=target,
                source_context="munin-mcp",
                command_or_params={"command": command, "timeout": timeout},
                job_id=job.job_id,
                artifacts=result.get("artifacts", []) or (artifacts or []),
                opsec_preflight=data.get("opsec_preflight", {}),
                started_at=job.started_at or started_at,
                finished_at=job.finished_at or utc_now_iso(),
                summary=result.get("summary", ""),
            )
        except Exception as audit_exc:
            logger.warning("AUDIT.record failed in on_finish for %s: %s", tool, audit_exc)

    job = JOBS.submit(
        tool=tool,
        level=level,
        target=target,
        command_preview=command[:200],
        fn=lambda current_job: ENGINE.execute_job(
            job=current_job,
            tool=tool,
            level=level,
            command=command,
            timeout=timeout,
            target=target,
            artifacts=artifacts,
        ),
        on_finish=on_finish,
    )
    return {
        "ok": True,
        "tool": tool,
        "mode": "async",
        "job_id": job.job_id,
        "summary": f"{tool} job submitted",
        "data": {
            "job_id": job.job_id,
            "status": job.status,
            "started_at": job.started_at,
            "created_at": job.created_at,
            "target": target,
        },
        "artifacts": artifacts or [],
    }


def _run_command(
    *,
    tool: str,
    level: str,
    command: str,
    timeout: int,
    target: str,
    mode: str,
    run_id: str,
    artifacts: list[str] | None = None,
) -> dict[str, Any]:
    if mode == "async":
        return _submit_command_job(
            tool=tool,
            level=level,
            target=target,
            command=command,
            timeout=timeout,
            run_id=run_id,
            artifacts=artifacts,
        )
    return ENGINE.execute_sync(
        tool=tool,
        level=level,
        command=command,
        timeout=timeout,
        target=target,
        artifacts=artifacts,
    )


def _write_target_file(run_id: str, name: str, targets: list[str]) -> Path:
    path = _artifact_path(run_id, "input", name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(targets) + "\n", encoding="utf-8")
    return path


def _dependency_or_continue(tool: str, dependencies: list[str]) -> dict[str, Any] | None:
    for dependency in dependencies:
        if not command_exists(dependency):
            return ENGINE.dependency_result(tool, dependency)
    return None


# ─────────────────────────────────────────────
# ADMIN / STATUS TOOLS
# ─────────────────────────────────────────────


@MCP.tool()
@audited_tool("health_check", "admin", lambda *a, **k: "sync")
def health_check(run_id: str = "") -> dict[str, Any]:
    """Return overall MCP health: egress IP, VPN route, binary availability, and host OPSEC snapshot."""
    preflight = ENGINE.preflight()
    binaries = {
        "nmap": command_exists("nmap"),
        "httpx": command_exists(_httpx_binary()),
        "nuclei": command_exists("nuclei"),
        "sqlmap": command_exists("sqlmap"),
        "EyeWitness": command_exists("EyeWitness"),
        "ffuf": command_exists("ffuf"),
        "feroxbuster": command_exists("feroxbuster"),
        "katana": command_exists("katana"),
        "hydra": command_exists("hydra"),
        "searchsploit": command_exists("searchsploit"),
    }
    host_snapshot = _load_host_opsec_snapshot()
    return {
        "ok": True,
        "tool": "health_check",
        "mode": "sync",
        "summary": "munin mcp healthy",
        "data": {
            "workspace_root": str(SETTINGS.workspace_root),
            "shared_state_db": str(STATE.db_path),
            "egress_ip": preflight["egress_ip"],
            "route": preflight["route"],
            "expected_egress_ip": SETTINGS.expected_egress_ip,
            "forbidden_egress_ip": SETTINGS.forbidden_egress_ip,
            "binary_status": binaries,
            "host_opsec_snapshot": host_snapshot,
            "opsec_preflight": preflight,
            "orphans_killed": ORPHANS_KILLED,
            "preflight_policy": SETTINGS.preflight_policy,
        },
    }


@MCP.tool()
@audited_tool("vpn_status", "admin", lambda *a, **k: "sync")
def vpn_status(run_id: str = "") -> dict[str, Any]:
    """Check current egress IP against expected VPN exit node."""
    preflight = ENGINE.preflight()
    return {
        "ok": True,
        "tool": "vpn_status",
        "mode": "sync",
        "summary": f"egress {preflight['egress_ip']}",
        "data": {**preflight, "host_opsec_snapshot": _load_host_opsec_snapshot()},
    }


@MCP.tool()
def job_status(job_id: str, include_result: bool = False, run_id: str = "") -> dict[str, Any]:
    """Get the current status of an async job. Use include_result=true to get full stdout/stderr."""
    return JOBS.status(job_id, include_result=include_result)


@MCP.tool()
def job_cancel(job_id: str, run_id: str = "") -> dict[str, Any]:
    """Cancel a running or queued async job."""
    return JOBS.cancel(job_id)


@MCP.tool()
@audited_tool("execute_command", "active", lambda *a, **k: k.get("mode", "async"))
def execute_command(
    command: str,
    mode: str = "async",
    timeout: int = SETTINGS.default_timeout,
    target: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    """Execute an operator-authorized command with active OPSEC pre/postflight."""
    return _run_command(
        tool="execute_command",
        level="active",
        command=command,
        timeout=timeout,
        target=target,
        mode=mode,
        run_id=run_id,
    )


# ─────────────────────────────────────────────
# ACTIVE SCAN TOOLS
# ─────────────────────────────────────────────


def _nmap_command(target: str, scan_type: str, ports: str, extra: str) -> str:
    parts: list[str] = ["nmap", scan_type]
    if ports.strip():
        parts.extend(["-p", ports.strip()])
    parts.extend(split_extra_args(extra))
    parts.append(target.strip())
    return shell_join(parts)


@MCP.tool()
@audited_tool("nmap_scan", "active", lambda *a, **k: k.get("mode", "async"))
def nmap_scan(
    target: str,
    scan_type: str = "-sV",
    ports: str = "",
    additional_args: str = "",
    mode: str = "async",
    timeout: int = 600,
    run_id: str = "",
) -> dict[str, Any]:
    """Run an nmap scan against a target. Defaults to service version detection (-sV)."""
    dep = _dependency_or_continue("nmap_scan", ["nmap"])
    if dep:
        return dep
    return _run_command(
        tool="nmap_scan",
        level="active",
        command=_nmap_command(target, scan_type, ports, additional_args),
        timeout=timeout,
        target=target,
        mode=mode,
        run_id=run_id,
    )


@MCP.tool()
@audited_tool("nmap_advanced_scan", "active", lambda *a, **k: k.get("mode", "async"))
def nmap_advanced_scan(
    target: str,
    scan_type: str = "-sS",
    ports: str = "",
    timing: str = "T4",
    nse_scripts: str = "",
    os_detection: bool = False,
    version_detection: bool = False,
    aggressive: bool = False,
    stealth: bool = False,
    additional_args: str = "",
    mode: str = "async",
    timeout: int = 900,
    run_id: str = "",
) -> dict[str, Any]:
    """Advanced nmap scan with NSE scripts, OS detection, stealth options, and timing control."""
    dep = _dependency_or_continue("nmap_advanced_scan", ["nmap"])
    if dep:
        return dep
    parts: list[str] = ["nmap", scan_type, f"-{timing}"]
    if ports.strip():
        parts.extend(["-p", ports.strip()])
    if nse_scripts.strip():
        parts.extend(["--script", nse_scripts.strip()])
    if os_detection:
        parts.append("-O")
    if version_detection:
        parts.append("-sV")
    if aggressive:
        parts.append("-A")
    if stealth:
        parts.extend(["--defeat-rst-ratelimit", "--max-retries", "2"])
    parts.extend(split_extra_args(additional_args))
    parts.append(target.strip())
    return _run_command(
        tool="nmap_advanced_scan",
        level="active",
        command=shell_join(parts),
        timeout=timeout,
        target=target,
        mode=mode,
        run_id=run_id,
    )


@MCP.tool()
@audited_tool("httpx_probe", "active", lambda *a, **k: k.get("mode", "async"))
def httpx_probe(
    targets: str = "",
    targets_file: str = "",
    ports: str = "",
    additional_args: str = "",
    mode: str = "async",
    timeout: int = 300,
    run_id: str = "",
) -> dict[str, Any]:
    """Probe HTTP/HTTPS services on targets. Outputs JSON lines with status, title, tech stack, etc."""
    dep = _dependency_or_continue("httpx_probe", [_httpx_binary()])
    if dep:
        return dep
    parsed_targets = parse_targets(targets)
    target_file_path = (
        Path(targets_file) if targets_file.strip() else _write_target_file(run_id, "httpx_targets.txt", parsed_targets)
    )
    output_path = _artifact_path(run_id, "evidence", "http", "httpx_probe.jsonl")
    parts: list[str] = [_httpx_binary(), "-silent", "-json", "-l", str(target_file_path), "-o", str(output_path)]
    if ports.strip():
        parts.extend(["-ports", ports.strip()])
    parts.extend(split_extra_args(additional_args))
    return _run_command(
        tool="httpx_probe",
        level="active",
        command=shell_join(parts),
        timeout=timeout,
        target=",".join(parsed_targets) if parsed_targets else str(target_file_path),
        mode=mode,
        run_id=run_id,
        artifacts=[str(output_path)],
    )


@MCP.tool()
@audited_tool("netexec_scan", "active", lambda *a, **k: k.get("mode", "async"))
def netexec_scan(
    protocol: str,
    target: str,
    username: str = "",
    password: str = "",
    additional_args: str = "",
    mode: str = "async",
    timeout: int = 600,
    run_id: str = "",
) -> dict[str, Any]:
    """Run NetExec against a target for SMB, LDAP, RDP, SSH, WMI or other supported protocols."""
    dep = _dependency_or_continue("netexec_scan", ["netexec"])
    if dep:
        return dep
    parts: list[str] = ["netexec", protocol.strip(), target.strip()]
    if username.strip():
        parts.extend(["-u", username.strip()])
    if password.strip():
        parts.extend(["-p", password.strip()])
    parts.extend(split_extra_args(additional_args))
    return _run_command(
        tool="netexec_scan",
        level="active",
        command=shell_join(parts),
        timeout=timeout,
        target=target,
        mode=mode,
        run_id=run_id,
    )


@MCP.tool()
@audited_tool("feroxbuster_scan", "active", lambda *a, **k: k.get("mode", "async"))
def feroxbuster_scan(
    url: str,
    wordlist: str = "",
    additional_args: str = "",
    mode: str = "async",
    timeout: int = 900,
    run_id: str = "",
) -> dict[str, Any]:
    """Directory and file brute-force with feroxbuster."""
    dep = _dependency_or_continue("feroxbuster_scan", ["feroxbuster"])
    if dep:
        return dep
    parts: list[str] = ["feroxbuster", "-u", url.strip()]
    if wordlist.strip():
        parts.extend(["-w", wordlist.strip()])
    parts.extend(split_extra_args(additional_args))
    return _run_command(
        tool="feroxbuster_scan",
        level="active",
        command=shell_join(parts),
        timeout=timeout,
        target=url,
        mode=mode,
        run_id=run_id,
    )


@MCP.tool()
@audited_tool("ffuf_scan", "active", lambda *a, **k: k.get("mode", "async"))
def ffuf_scan(
    url: str,
    wordlist: str,
    matcher: str = "",
    filter_expr: str = "",
    additional_args: str = "",
    mode: str = "async",
    timeout: int = 900,
    run_id: str = "",
) -> dict[str, Any]:
    """Fast web fuzzer (ffuf). Use FUZZ keyword in URL for injection points."""
    dep = _dependency_or_continue("ffuf_scan", ["ffuf"])
    if dep:
        return dep
    parts: list[str] = ["ffuf", "-u", url.strip(), "-w", wordlist.strip()]
    if matcher.strip():
        parts.extend(["-mc", matcher.strip()])
    if filter_expr.strip():
        parts.extend(["-fc", filter_expr.strip()])
    parts.extend(split_extra_args(additional_args))
    return _run_command(
        tool="ffuf_scan",
        level="active",
        command=shell_join(parts),
        timeout=timeout,
        target=url,
        mode=mode,
        run_id=run_id,
    )


@MCP.tool()
@audited_tool("katana_crawl", "active", lambda *a, **k: k.get("mode", "async"))
def katana_crawl(
    url: str,
    depth: int = 3,
    additional_args: str = "",
    mode: str = "async",
    timeout: int = 900,
    run_id: str = "",
) -> dict[str, Any]:
    """Crawl a web application with katana to discover endpoints and JavaScript links."""
    dep = _dependency_or_continue("katana_crawl", ["katana"])
    if dep:
        return dep
    parts: list[str] = ["katana", "-u", url.strip(), "-d", str(depth)]
    parts.extend(split_extra_args(additional_args))
    return _run_command(
        tool="katana_crawl",
        level="active",
        command=shell_join(parts),
        timeout=timeout,
        target=url,
        mode=mode,
        run_id=run_id,
    )


@MCP.tool()
@audited_tool("hydra_attack", "active", lambda *a, **k: k.get("mode", "async"))
def hydra_attack(
    target: str,
    service: str,
    username: str = "",
    username_file: str = "",
    password: str = "",
    password_file: str = "",
    additional_args: str = "",
    mode: str = "async",
    timeout: int = 1800,
    run_id: str = "",
) -> dict[str, Any]:
    """Credential brute-force with Hydra. Supports ssh, ftp, http-post-form, rdp, smb, etc."""
    dep = _dependency_or_continue("hydra_attack", ["hydra"])
    if dep:
        return dep
    parts: list[str] = ["hydra"]
    if username.strip():
        parts.extend(["-l", username.strip()])
    if username_file.strip():
        parts.extend(["-L", username_file.strip()])
    if password.strip():
        parts.extend(["-p", password.strip()])
    if password_file.strip():
        parts.extend(["-P", password_file.strip()])
    parts.extend(split_extra_args(additional_args))
    parts.extend([target.strip(), service.strip()])
    return _run_command(
        tool="hydra_attack",
        level="active",
        command=shell_join(parts),
        timeout=timeout,
        target=target,
        mode=mode,
        run_id=run_id,
    )


@MCP.tool()
@audited_tool("sqlmap_scan", "active", lambda *a, **k: k.get("mode", "async"))
def sqlmap_scan(
    url: str,
    data: str = "",
    additional_args: str = "",
    mode: str = "async",
    timeout: int = 1800,
    run_id: str = "",
) -> dict[str, Any]:
    """SQL injection detection and exploitation with sqlmap."""
    dep = _dependency_or_continue("sqlmap_scan", ["sqlmap"])
    if dep:
        return dep
    parts: list[str] = ["sqlmap", "-u", url.strip(), "--batch"]
    if data.strip():
        parts.extend(["--data", data.strip()])
    parts.extend(split_extra_args(additional_args))
    return _run_command(
        tool="sqlmap_scan",
        level="active",
        command=shell_join(parts),
        timeout=timeout,
        target=url,
        mode=mode,
        run_id=run_id,
    )


@MCP.tool()
@audited_tool("smbmap_scan", "active", lambda *a, **k: k.get("mode", "async"))
def smbmap_scan(
    target: str,
    username: str = "",
    password: str = "",
    domain: str = "",
    additional_args: str = "",
    mode: str = "async",
    timeout: int = 900,
    run_id: str = "",
) -> dict[str, Any]:
    """Enumerate SMB shares, permissions, and accessible files with smbmap."""
    dep = _dependency_or_continue("smbmap_scan", ["smbmap"])
    if dep:
        return dep
    parts: list[str] = ["smbmap", "-H", target.strip()]
    if username.strip():
        parts.extend(["-u", username.strip()])
    if password.strip():
        parts.extend(["-p", password.strip()])
    if domain.strip():
        parts.extend(["-d", domain.strip()])
    parts.extend(split_extra_args(additional_args))
    return _run_command(
        tool="smbmap_scan",
        level="active",
        command=shell_join(parts),
        timeout=timeout,
        target=target,
        mode=mode,
        run_id=run_id,
    )


@MCP.tool()
@audited_tool("nuclei_scan", "active", lambda *a, **k: k.get("mode", "async"))
def nuclei_scan(
    target: str,
    severity: str = "",
    tags: str = "",
    template: str = "",
    additional_args: str = "",
    mode: str = "async",
    timeout: int = 1200,
    run_id: str = "",
) -> dict[str, Any]:
    """Vulnerability scanner using Nuclei templates. Filter by severity or tags."""
    dep = _dependency_or_continue("nuclei_scan", ["nuclei"])
    if dep:
        return dep
    parts: list[str] = ["nuclei", "-u", target.strip()]
    if severity.strip():
        parts.extend(["-severity", severity.strip()])
    if tags.strip():
        parts.extend(["-tags", tags.strip()])
    if template.strip():
        parts.extend(["-t", template.strip()])
    parts.extend(split_extra_args(additional_args))
    return _run_command(
        tool="nuclei_scan",
        level="active",
        command=shell_join(parts),
        timeout=timeout,
        target=target,
        mode=mode,
        run_id=run_id,
    )


@MCP.tool()
@audited_tool("web_evidence_screenshotter", "documentation", lambda *a, **k: "async")
def web_evidence_screenshotter(
    targets: str = "",
    targets_file: str = "",
    run_id: str = "",
    ports: str = "",
    schemes: str = "http,https",
    timeout: int = 1800,
) -> dict[str, Any]:
    """Take screenshots of live web services using httpx + EyeWitness. Produces an HTML report."""
    dep = _dependency_or_continue("web_evidence_screenshotter", [_httpx_binary(), "EyeWitness"])
    if dep:
        return dep
    parsed_targets = parse_targets(targets)
    target_file_path = (
        Path(targets_file) if targets_file.strip() else _write_target_file(run_id, "web_targets.txt", parsed_targets)
    )
    evidence_dir = _artifact_path(run_id, "evidence", "web")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    input_dir = _artifact_path(run_id, "input", "web")
    input_dir.mkdir(parents=True, exist_ok=True)
    httpx_output = input_dir / "httpx_live.jsonl"
    urls_path = input_dir / "urls.txt"
    filter_script = "\n".join(
        [
            "import json, pathlib",
            f"src = pathlib.Path({str(httpx_output)!r})",
            f"dst = pathlib.Path({str(urls_path)!r})",
            f"allowed = {{item.strip() for item in {schemes.split(',')!r} if item.strip()}}",
            "urls = []",
            "for line in src.read_text(encoding='utf-8', errors='ignore').splitlines():",
            "    if not line.strip():",
            "        continue",
            "    row = json.loads(line)",
            "    scheme = row.get('scheme', '')",
            "    url = row.get('url', '')",
            "    if url and (not allowed or scheme in allowed):",
            "        urls.append(url)",
            "dst.write_text('\\n'.join(urls) + '\\n', encoding='utf-8')",
            "print(len(urls))",
        ]
    )
    command = " && ".join(
        [
            f"{shlex.quote(_httpx_binary())} -silent -json -l {shlex.quote(str(target_file_path))} -o {shlex.quote(str(httpx_output))}"
            + (f" -ports {ports.strip()}" if ports.strip() else ""),
            f"python3 -c {shlex.quote(filter_script)}",
            f"EyeWitness --web -f {shlex.quote(str(urls_path))} --no-prompt -d {shlex.quote(str(evidence_dir))}",
        ]
    )
    artifacts = [str(httpx_output), str(urls_path), str(evidence_dir / "report.html")]
    return _submit_command_job(
        tool="web_evidence_screenshotter",
        level="documentation",
        target=",".join(parsed_targets) if parsed_targets else str(target_file_path),
        command=command,
        timeout=timeout,
        run_id=run_id,
        artifacts=artifacts,
    )


# ─────────────────────────────────────────────
# PASSIVE INTEL TOOLS
# ─────────────────────────────────────────────


@MCP.tool()
@audited_tool("cve_lookup", "passive", lambda *a, **k: "sync")
def cve_lookup(cve_id: str, run_id: str = "") -> dict[str, Any]:
    """Look up a CVE from NVD, CIRCL, and MITRE. Returns CVSS, EPSS, KEV status, and known exploits."""
    data = INTEL.cve_lookup(cve_id)
    return {"ok": True, "tool": "cve_lookup", "mode": "sync", "summary": f"looked up {cve_id.upper()}", "data": data}


@MCP.tool()
@audited_tool("cve_search", "passive", lambda *a, **k: "sync")
def cve_search(query: str, limit: int = 10, run_id: str = "") -> dict[str, Any]:
    """Search CVEs by keyword using the NVD API."""
    return INTEL.cve_search(query, limit)


@MCP.tool()
@audited_tool("cve_enrich", "passive", lambda *a, **k: "sync")
def cve_enrich(cve_id: str, run_id: str = "") -> dict[str, Any]:
    """Enrich a CVE with full multi-source data: NVD + CIRCL + MITRE + EPSS + KEV + exploits."""
    return INTEL.cve_enrich(cve_id)


@MCP.tool()
@audited_tool("exploit_search", "passive", lambda *a, **k: "sync")
def exploit_search(cve_id: str = "", query: str = "", run_id: str = "") -> dict[str, Any]:
    """Search for public exploits via SearchSploit, GitHub, and local Nuclei templates."""
    return INTEL.exploit_search(cve_id=cve_id, query=query)


@MCP.tool()
@audited_tool("package_vuln_lookup", "passive", lambda *a, **k: "sync")
def package_vuln_lookup(ecosystem: str, package_name: str, version: str, run_id: str = "") -> dict[str, Any]:
    """Look up known vulnerabilities for a package/version via OSV (PyPI, npm, Go, Maven, etc.)."""
    return INTEL.package_vuln_lookup(ecosystem, package_name, version)


# ─────────────────────────────────────────────
# DOCUMENTATION / SYNC TOOLS
# ─────────────────────────────────────────────


@MCP.tool()
@audited_tool("wiki_git_syncer", "documentation", lambda *a, **k: "sync")
def wiki_git_syncer(run_id: str, mode: str = "prepare", destination: str = "obsidian") -> dict[str, Any]:
    """Sync run artifacts to a knowledge base. Modes: prepare, commit, push."""
    return SYNCER.run(run_id=run_id, mode=mode, destination=destination)


# ─────────────────────────────────────────────
# SHARED STATE TOOLS (Multi-Agent Coordination)
# ─────────────────────────────────────────────


@MCP.tool()
@audited_tool("shared_state_overview", "admin", lambda *a, **k: "sync")
def shared_state_overview(run_id: str = "") -> dict[str, Any]:
    """Overview of the shared SQLite state: intel count, running tasks, agents, and unread messages."""
    data = STATE.overview()
    return {
        "ok": True,
        "tool": "shared_state_overview",
        "mode": "sync",
        "summary": f"shared state has {data['intel_total']} intel rows and {data['tasks_running']} running tasks",
        "data": data,
    }


@MCP.tool()
@audited_tool("publish_shared_intel", "documentation", lambda *a, **k: "sync")
def publish_shared_intel(
    target_ip: str,
    finding_type: str,
    details_json: str,
    source_agent: str,
    port: int = 0,
    service: str = "",
    severity: str = "INFO",
    status: str = "NEW",
    tags: str = "",
    fingerprint: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    """Publish a finding to the shared intel SQLite store. Accessible by all agents."""
    record = STATE.publish_intel(
        target_ip=target_ip,
        port=port or None,
        service=service,
        finding_type=finding_type,
        severity=severity,
        details_json=details_json,
        source_agent=source_agent,
        status=status,
        tags=tags,
        fingerprint=fingerprint,
    )
    return {
        "ok": True,
        "tool": "publish_shared_intel",
        "mode": "sync",
        "summary": f"intel stored for {record['target_ip']}",
        "data": record,
    }


@MCP.tool()
@audited_tool("query_shared_intel", "passive", lambda *a, **k: "sync")
def query_shared_intel(
    target_ip: str = "",
    service: str = "",
    finding_type: str = "",
    severity: str = "",
    status: str = "",
    limit: int = 50,
    run_id: str = "",
) -> dict[str, Any]:
    """Query the shared intel store. Filter by IP, service, finding type, severity, or status."""
    matches = STATE.query_intel(
        target_ip=target_ip,
        service=service,
        finding_type=finding_type,
        severity=severity,
        status=status,
        limit=limit,
    )
    return {
        "ok": True,
        "tool": "query_shared_intel",
        "mode": "sync",
        "summary": f"found {len(matches)} shared intel rows",
        "data": {"matches": matches, "count": len(matches)},
    }


@MCP.tool()
@audited_tool("claim_shared_task", "admin", lambda *a, **k: "sync")
def claim_shared_task(
    target_ip: str,
    action: str,
    assigned_agent: str,
    lease_seconds: int = 1800,
    metadata_json: str = "{}",
    allow_steal_stale: bool = True,
    run_id: str = "",
) -> dict[str, Any]:
    """Claim an exclusive task lock for a (target_ip, action) pair."""
    decision = STATE.claim_task(
        target_ip=target_ip,
        action=action,
        assigned_agent=assigned_agent,
        lease_seconds=lease_seconds,
        metadata_json=metadata_json,
        allow_steal_stale=allow_steal_stale,
    )
    return {
        "ok": decision.success,
        "tool": "claim_shared_task",
        "mode": "sync",
        "summary": decision.message,
        "data": {
            "task_id": decision.task_id,
            "stolen_stale_task_id": decision.stolen_stale_task_id,
            "target_ip": target_ip,
            "action": action,
            "assigned_agent": assigned_agent,
        },
        "error": None if decision.success else {"code": "task_claim_failed", "message": decision.message},
    }


@MCP.tool()
@audited_tool("heartbeat_shared_task", "admin", lambda *a, **k: "sync")
def heartbeat_shared_task(task_id: int, assigned_agent: str, lease_seconds: int = 1800, run_id: str = "") -> dict[str, Any]:
    """Renew the lease on a claimed task."""
    result = STATE.heartbeat_task(task_id=task_id, assigned_agent=assigned_agent, lease_seconds=lease_seconds)
    return {
        "ok": bool(result.get("success")),
        "tool": "heartbeat_shared_task",
        "mode": "sync",
        "summary": result["message"],
        "data": result,
        "error": None if result.get("success") else {"code": "task_heartbeat_failed", "message": result["message"]},
    }


@MCP.tool()
@audited_tool("complete_shared_task", "admin", lambda *a, **k: "sync")
def complete_shared_task(
    task_id: int,
    assigned_agent: str,
    status: str = "COMPLETED",
    result_json: str = "{}",
    run_id: str = "",
) -> dict[str, Any]:
    """Mark a task as COMPLETED, FAILED, or CANCELLED and store the result JSON."""
    result = STATE.complete_task(task_id=task_id, assigned_agent=assigned_agent, status=status, result_json=result_json)
    return {
        "ok": bool(result.get("success")),
        "tool": "complete_shared_task",
        "mode": "sync",
        "summary": result["message"],
        "data": result,
        "error": None if result.get("success") else {"code": "task_complete_failed", "message": result["message"]},
    }


@MCP.tool()
@audited_tool("list_shared_tasks", "passive", lambda *a, **k: "sync")
def list_shared_tasks(status: str = "", assigned_agent: str = "", target_ip: str = "", limit: int = 100, run_id: str = "") -> dict[str, Any]:
    """List shared tasks. Filter by status, agent, or target IP."""
    tasks = STATE.list_tasks(status=status, assigned_agent=assigned_agent, target_ip=target_ip, limit=limit)
    return {
        "ok": True,
        "tool": "list_shared_tasks",
        "mode": "sync",
        "summary": f"found {len(tasks)} shared tasks",
        "data": {"matches": tasks, "count": len(tasks)},
    }


@MCP.tool()
@audited_tool("upsert_agent_presence", "admin", lambda *a, **k: "sync")
def upsert_agent_presence(
    agent_name: str,
    role: str = "",
    status: str = "IDLE",
    current_task_id: int = 0,
    metadata_json: str = "{}",
    run_id: str = "",
) -> dict[str, Any]:
    """Register or update an agent's presence in the shared coordination store."""
    record = STATE.upsert_presence(
        agent_name=agent_name,
        role=role,
        status=status,
        current_task_id=current_task_id or None,
        metadata_json=metadata_json,
    )
    return {
        "ok": True,
        "tool": "upsert_agent_presence",
        "mode": "sync",
        "summary": f"presence updated for {agent_name}",
        "data": record,
    }


@MCP.tool()
@audited_tool("list_agent_presence", "passive", lambda *a, **k: "sync")
def list_agent_presence(stale_after_seconds: int = 3600, run_id: str = "") -> dict[str, Any]:
    """List all known agents and their last-seen time."""
    matches = STATE.list_presence(stale_after_seconds=stale_after_seconds)
    return {
        "ok": True,
        "tool": "list_agent_presence",
        "mode": "sync",
        "summary": f"found {len(matches)} agent presence rows",
        "data": {"matches": matches, "count": len(matches)},
    }


@MCP.tool()
@audited_tool("post_agent_message", "documentation", lambda *a, **k: "sync")
def post_agent_message(
    sender_agent: str,
    recipient_agent: str,
    body: str,
    subject: str = "",
    message_type: str = "INFO",
    related_task_id: int = 0,
    related_target_ip: str = "",
    metadata_json: str = "{}",
    run_id: str = "",
) -> dict[str, Any]:
    """Send a message from one agent to another via the shared message queue."""
    record = STATE.post_message(
        sender_agent=sender_agent,
        recipient_agent=recipient_agent,
        subject=subject,
        message_type=message_type,
        body=body,
        related_task_id=related_task_id or None,
        related_target_ip=related_target_ip,
        metadata_json=metadata_json,
    )
    return {
        "ok": True,
        "tool": "post_agent_message",
        "mode": "sync",
        "summary": f"message queued for {recipient_agent}",
        "data": record,
    }


@MCP.tool()
@audited_tool("fetch_agent_messages", "passive", lambda *a, **k: "sync")
def fetch_agent_messages(
    recipient_agent: str,
    status: str = "",
    message_type: str = "",
    mark_read: bool = False,
    limit: int = 50,
    run_id: str = "",
) -> dict[str, Any]:
    """Fetch messages for an agent. Optionally mark them as READ in one call."""
    matches = STATE.fetch_messages(
        recipient_agent=recipient_agent,
        status=status,
        message_type=message_type,
        mark_read=mark_read,
        limit=limit,
    )
    return {
        "ok": True,
        "tool": "fetch_agent_messages",
        "mode": "sync",
        "summary": f"found {len(matches)} messages for {recipient_agent}",
        "data": {"matches": matches, "count": len(matches)},
    }


@MCP.tool()
@audited_tool("ack_agent_message", "admin", lambda *a, **k: "sync")
def ack_agent_message(message_id: int, recipient_agent: str, status: str = "ACKED", run_id: str = "") -> dict[str, Any]:
    """Acknowledge or mark a message as READ, ACKED, or DONE."""
    result = STATE.ack_message(message_id=message_id, recipient_agent=recipient_agent, status=status)
    return {
        "ok": bool(result.get("success")),
        "tool": "ack_agent_message",
        "mode": "sync",
        "summary": result["message"],
        "data": result,
        "error": None if result.get("success") else {"code": "message_ack_failed", "message": result["message"]},
    }


# ─────────────────────────────────────────────
# MUNIN EXTENSIONS — LDAP, Tavily, Hugin, forge, registry, munin_tools
# Registered on the MCP instance defined above.
# ─────────────────────────────────────────────

from . import registry  # noqa: E402
from .tools import (  # noqa: E402
    capabilities_tool,  # noqa: E402,F401
    diagnostics_tool,  # noqa: E402,F401
    discord_tool,  # noqa: E402,F401
    extension_forge_tool,  # noqa: E402,F401
    forge_tool,  # noqa: E402,F401
    graph_forge_tool,  # noqa: E402,F401
    hugin_rag_tool,  # noqa: E402,F401
    hugin_tool,  # noqa: E402,F401
    ldap_tools,  # noqa: E402,F401
    munin_tools,  # noqa: E402,F401
    tavily_tool,  # noqa: E402,F401
)

# Modules with state-free functions register explicitly so the same functions
# are also available to Munin's in-process ReAct catalog.
discord_tool.register(MCP)
extension_forge_tool.register(MCP)
hugin_rag_tool.register(MCP)

# Rebuild the DB catalog from versioned graph manifests before runners resolve names.
try:
    from .graph_persist import rehydrate_graph_manifests  # noqa: E402

    rehydrate_graph_manifests(STATE, SETTINGS)
except Exception as exc:  # pragma: no cover - guardrail; log and keep going
    logger.warning("graph manifest rehydrate failed: %s", exc)

# Hot-load any tools previously forged by tool_forge, so they're available immediately.
try:
    registry.rehydrate(MCP, STATE, SETTINGS)
    # Wake-based forging runs in a subprocess. Keep the live FastMCP catalog in
    # step with its durable registry rows so a successful forge appears without
    # restarting the server.
    registry.start_runtime_sync(MCP, STATE, SETTINGS)
except Exception as exc:  # pragma: no cover - guardrail; log and keep going
    logger.warning("registry.rehydrate failed: %s", exc)


def _start_discord_operator_bridge() -> None:
    """Start the optional allowlisted Discord control plane without blocking MCP."""
    try:
        from ..integrations.discord_bridge import get_bridge, post_to_discord
        from ..integrations.discord_config import get_discord_config

        config = get_discord_config()
        if not config.outbound_enabled:
            return

        def handle_message(author_id: int, author: str, prompt: str, channel_id: int) -> None:
            # Fase 2 (issue #9): both dispatch paths this branch used
            # (``DurableDiscordAdapter`` → ``ProductionDispatcher``, and the
            # in-process ``MuninAgent.respond``) were deleted along with the
            # rest of Arch A.  A Discord operator bridge over ``supervisor_runner``
            # is planned for a follow-up phase (issue #9 Fase 3).  For now inbound
            # Discord control is disabled at the handler level so the outbound-only
            # path keeps working without dragging the legacy ReAct loop back in.
            del author_id, prompt  # unused until the supervisor bridge lands
            try:
                post_to_discord(
                    f"Munin — {author}\nInbound Discord control is temporarily disabled "
                    "while the supervisor bridge is rewired (issue #9 Fase 2).",
                    channel_id=channel_id,
                )
            except Exception:  # pragma: no cover - external provider / Discord failure
                logger.exception("Discord operator task failed")

        bridge = get_bridge(handle_message)
        if bridge and not config.inbound_enabled:
            logger.warning("Discord outbound notifications enabled, but inbound control is disabled until MUNIN_DISCORD_ALLOWED_USER_IDS is set")
    except Exception as exc:  # pragma: no cover - optional integration must not block MCP
        logger.warning("Discord bridge initialization failed: %s", exc)


_start_discord_operator_bridge()


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────


def _make_auth_middleware(expected_token: str) -> Callable[..., Any]:
    """ASGI middleware that enforces Bearer token auth on every HTTP request.

    Returns a middleware factory. Rejects requests whose ``Authorization`` header
    doesn't match ``Bearer <expected_token>``. Uses a constant-time comparison
    (``hmac.compare_digest``) so brute forcing over the wire is not viable.

    Requests without a token get 401. Requests with a wrong token get 403. Both
    responses are minimal JSON to avoid leaking anything. WebSocket / SSE frames
    piggyback on the initial handshake — once accepted the middleware doesn't
    re-check every message (FastMCP handles session correlation).

    This closes a real gap: FastMCP has NO built-in auth. Before this middleware,
    setting MUNIN_MCP_AUTH_TOKEN only affected a startup warning; the port was
    open to anyone on the network.
    """
    import hmac

    async def _reject(send: Any, status: int, msg: str) -> None:
        body = f'{{"error":"{msg}","status":{status}}}'.encode()
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})

    def middleware_factory(app: Callable[..., Any]) -> Callable[..., Any]:
        async def middleware(scope: dict[str, Any], receive: Any, send: Any) -> None:
            # Only guard HTTP (and WebSocket handshakes); pass everything else through.
            if scope.get("type") not in ("http", "websocket"):
                await app(scope, receive, send)
                return

            headers = dict(scope.get("headers") or [])
            origin_raw = headers.get(b"origin", b"")
            origin = origin_raw.decode("latin-1", "replace")
            valid_origin = (
                origin.startswith(("http://", "https://"))
                and "\r" not in origin
                and "\n" not in origin
            )
            cors_headers: list[tuple[bytes, bytes]] = []
            if valid_origin:
                cors_headers = [
                    (b"access-control-allow-origin", origin.encode("latin-1")),
                    (b"access-control-allow-methods", b"GET, POST, DELETE, OPTIONS"),
                    (
                        b"access-control-allow-headers",
                        b"authorization, content-type, mcp-protocol-version, mcp-session-id, last-event-id",
                    ),
                    (b"access-control-expose-headers", b"mcp-session-id, mcp-protocol-version"),
                    (b"access-control-max-age", b"600"),
                    (b"vary", b"Origin"),
                ]

            async def send_with_cors(message: dict[str, Any]) -> None:
                if cors_headers and message.get("type") == "http.response.start":
                    message = {
                        **message,
                        "headers": [*(message.get("headers") or []), *cors_headers],
                    }
                await send(message)

            # Browsers send an unauthenticated OPTIONS request before the
            # bearer-authenticated POST. Answer valid CORS preflights here and
            # keep bearer validation for the actual MCP operation.
            if (
                scope.get("type") == "http"
                and str(scope.get("method", "")).upper() == "OPTIONS"
                and valid_origin
                and headers.get(b"access-control-request-method", b"").upper()
                in {b"GET", b"POST", b"DELETE"}
            ):
                await send_with_cors({
                    "type": "http.response.start",
                    "status": 204,
                    "headers": [(b"content-length", b"0")],
                })
                await send_with_cors({"type": "http.response.body", "body": b""})
                return

            auth = headers.get(b"authorization", b"").decode("latin-1", "replace")
            if not auth:
                logger.warning("auth: request %s without Authorization header", scope.get("path", "?"))
                await _reject(send_with_cors, 401, "authorization required")
                return
            if not auth.startswith("Bearer "):
                await _reject(send_with_cors, 401, "bearer scheme required")
                return
            provided = auth[len("Bearer "):].strip()
            if not hmac.compare_digest(provided, expected_token):
                logger.warning("auth: bearer token mismatch on %s", scope.get("path", "?"))
                await _reject(send_with_cors, 403, "invalid bearer token")
                return
            await app(scope, receive, send_with_cors)

        return middleware

    return middleware_factory


def create_mcp_app(*, auth_token: str | None = None) -> Any:
    """Return the FastMCP streamable-http ASGI sub-app, ready to mount.

    Fase 3 (issue #9): :mod:`munin.server` mounts this under ``/mcp`` on the
    unified backend port.  When ``auth_token`` is falsy the caller is
    responsible for gating access (either via ``MUNIN_MCP_ALLOW_ANON=1`` or
    an outer middleware); otherwise we wrap the transport with the same
    bearer-token check used by the standalone ``munin mcp`` binary.

    Issue #9 fix: the sub-app's internal ``Route`` is set to ``/`` (via
    `MCP.settings.streamable_http_path`) so that mounting it under
    ``Mount("/mcp", ...)`` produces the public path ``/mcp`` — not the
    double-prefixed ``/mcp/mcp`` that the FastMCP default ``/mcp`` caused.
    The host :mod:`munin.server` lifespan is then responsible for running
    ``MCP.session_manager`` (Starlette does not propagate lifespans to
    mounted sub-apps, so without it the session manager raises "Task group
    is not initialized" on the first request).
    """
    token = (auth_token if auth_token is not None else SETTINGS.mcp_auth_token) or ""
    MCP.settings.streamable_http_path = "/"
    app = MCP.streamable_http_app()
    if token:
        middleware = _make_auth_middleware(token)
        app = middleware(app)
    elif os.environ.get("MUNIN_MCP_ALLOW_ANON", "0") != "1":
        logger.warning(
            "create_mcp_app: MUNIN_MCP_AUTH_TOKEN is empty and MUNIN_MCP_ALLOW_ANON is not 1 — "
            "the mounted /mcp sub-app is UNAUTHENTICATED.  Configure a token before exposing this "
            "process to a public tunnel."
        )
    return app


def _install_signal_handlers() -> None:
    """Log SIGTERM/SIGINT clearly so operators can distinguish a clean shutdown from a crash.

    FastMCP handles the actual shutdown; we just log so the operator sees the reason
    in the server logs, which matters when the process is being managed by systemd,
    Docker, or a GitHub Actions timeout that will SIGTERM us.
    """
    def _handler(signum: int, _frame: Any) -> None:
        name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
        logger.info("received %s — shutting down cleanly", name)
        # Give the git-persist worker a chance to ship queued commits BEFORE the
        # process dies. Local dev with MUNIN_AUTO_COMMIT off returns immediately.
        try:
            from .git_persist import flush as _flush_git  # noqa: PLC0415
            _flush_git(timeout=20.0)
        except Exception as exc:  # pragma: no cover
            logger.warning("git_persist.flush failed during shutdown: %s", exc)
        JOBS.shutdown()
        # Let the default handler proceed. FastMCP + uvicorn handle the graceful stop.
        # For stdio transport, raise KeyboardInterrupt so MCP.run() returns.
        raise SystemExit(0)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            # Non-main thread or unsupported signal — skip silently.
            pass


def main() -> None:
    global ORPHANS_KILLED  # noqa: PLW0603 — module-level state exposed to callers

    parser = argparse.ArgumentParser(description="Munin MCP — OFFX + Munin extensions")
    parser.add_argument("--transport", default="stdio", choices=("stdio", "sse", "streamable-http"))
    parser.add_argument("--host", default=SETTINGS.mcp_host)
    parser.add_argument("--port", type=int, default=SETTINGS.mcp_port)
    args = parser.parse_args()

    _install_signal_handlers()

    # Orphan cleanup runs ONLY when we're actually starting the server, not on
    # every import. Skip for stdio transport in the common IDE-attach case
    # (Claude Code / Cursor spawn stdio Munin processes; we'd kill ourselves).
    ORPHANS_KILLED = _kill_orphaned_stdio_processes()
    if ORPHANS_KILLED:
        logger.info("killed %d orphaned stdio processes at startup", ORPHANS_KILLED)

    from .persistence import describe_backend  # noqa: PLC0415 — lazy to avoid load-time cost
    logger.info(
        "starting munin-mcp transport=%s workspace=%s preflight_policy=%s log_level=%s db=%s",
        args.transport,
        SETTINGS.workspace_root,
        SETTINGS.preflight_policy,
        _LOG_LEVEL,
        describe_backend(SETTINGS.db_url) if SETTINGS.db_url else f"sqlite({SETTINGS.shared_state_db})",
    )
    if args.transport == "stdio":
        try:
            MCP.run()
        except SystemExit:
            pass
        finally:
            JOBS.shutdown()
        return

    MCP.settings.host = args.host
    MCP.settings.port = args.port

    # Enforce Bearer token auth on every HTTP request when a token is set. If
    # the token is empty we refuse to start unless MUNIN_MCP_ALLOW_ANON=1 is
    # exported — an anonymous MCP server on a public tunnel is a real
    # exposure and we don't want it happening by accident.
    if args.transport != "stdio":
        if not SETTINGS.mcp_auth_token:
            if os.environ.get("MUNIN_MCP_ALLOW_ANON", "0") != "1":
                logger.error(
                    "MUNIN_MCP_AUTH_TOKEN is empty. Refusing to start HTTP transport on %s:%s "
                    "without a token. Set MUNIN_MCP_AUTH_TOKEN in .env, or override with "
                    "MUNIN_MCP_ALLOW_ANON=1 if you really want anonymous access (dev only).",
                    args.host, args.port,
                )
                raise SystemExit(2)
            logger.warning(
                "MUNIN_MCP_ALLOW_ANON=1 — starting with NO authentication on %s:%s.",
                args.host, args.port,
            )
        else:
            # Wrap FastMCP's ASGI app with our bearer-check middleware.
            # Capture the wrapped app ONCE — a naive `lambda: middleware(original())`
            # would re-instantiate the app (and its lifespan / session manager)
            # every time FastMCP internally accesses the property.
            middleware = _make_auth_middleware(SETTINGS.mcp_auth_token)
            if args.transport == "streamable-http":
                wrapped_app = middleware(MCP.streamable_http_app())
                MCP.streamable_http_app = lambda: wrapped_app  # type: ignore[method-assign]
            elif args.transport == "sse":
                wrapped_app = middleware(MCP.sse_app())
                MCP.sse_app = lambda: wrapped_app  # type: ignore[method-assign]
            logger.info("bearer-token auth middleware installed on %s transport", args.transport)

    try:
        MCP.run(args.transport)
    except SystemExit:
        pass
    except Exception:
        logger.exception("munin-mcp crashed")
        raise
    finally:
        JOBS.shutdown()


if __name__ == "__main__":
    main()
