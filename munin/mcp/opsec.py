# tags: [mcp, core, evasion, supervisor, runtime, ExecutionEngine, OpsecError, preflight, _detect_egress_ip, _INSTALL_HINTS, _REQUIRED_SERVICES, _REQUIRED_NFT_TABLES, execute_sync, execute_job, dependency_result]
from __future__ import annotations

import codecs
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from contextvars import copy_context
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .audit import redact_secrets
from .config import Settings
from .utils import ensure_parent, stderr_tail, truncate_text

logger = logging.getLogger("munin-mcp")

_VPN_INTERFACE = os.environ.get("OFFX_VPN_INTERFACE", "vpn0")

_DEFAULT_REQUIRED_SERVICES = [
    "vpn-client.service",
    "vpn-route.service",
    "vpn-leak-watchdog.service",
    "container-vpn-guard.service",
    "opsec-strict-nft.service",
]
_REQUIRED_SERVICES: list[str] = [
    s.strip() for s in os.environ.get("OFFX_REQUIRED_SERVICES", "").split(",") if s.strip()
] or _DEFAULT_REQUIRED_SERVICES

_REQUIRED_NFT_TABLES: list[str] = [
    t.strip()
    for t in os.environ.get("OFFX_REQUIRED_NFT_TABLES", "opsec_strict,container_vpn_guard").split(",")
    if t.strip()
]

_PROCESS_HEARTBEAT_SECONDS = float(os.environ.get("MUNIN_PROCESS_HEARTBEAT_SECONDS", "5"))
_PROCESS_OUTPUT_CHUNK_CHARS = max(256, int(os.environ.get("MUNIN_PROCESS_OUTPUT_CHUNK_CHARS", "4096")))
_PROCESS_OUTPUT_QUEUE_SIZE = max(8, int(os.environ.get("MUNIN_PROCESS_OUTPUT_QUEUE_SIZE", "256")))


# Install hints for offensive binaries commonly missing on generic runners / dev boxes.
# Consumed by ExecutionEngine.dependency_result to give the LLM (and the human) an
# actionable next step instead of a bare "missing_dependency" error.
_INSTALL_HINTS: dict[str, str] = {
    "nmap":         "apt install nmap  |  brew install nmap",
    "nuclei":       "go install -v github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest  |  brew install nuclei",
    "feroxbuster":  "cargo install feroxbuster  |  apt install feroxbuster  |  brew install feroxbuster",
    "ffuf":         "go install github.com/ffuf/ffuf/v2@latest  |  apt install ffuf  |  brew install ffuf",
    "sqlmap":       "apt install sqlmap  |  brew install sqlmap  |  pip install sqlmap",
    "httpx":        "go install -v github.com/projectdiscovery/httpx/cmd/httpx@latest",
    "pd-httpx":     "go install -v github.com/projectdiscovery/httpx/cmd/httpx@latest  (aliased as pd-httpx to avoid conflict with python-httpx)",
    "katana":       "go install github.com/projectdiscovery/katana/cmd/katana@latest",
    "hydra":        "apt install hydra  |  brew install hydra",
    "smbmap":       "pip install smbmap  |  apt install smbmap",
    "netexec":      "pip install netexec  |  https://www.netexec.wiki/getting-started/installation",
    "EyeWitness":   "apt install eyewitness  |  git clone https://github.com/RedSiege/EyeWitness && ./Python/setup/setup.sh",
    "searchsploit": "apt install exploitdb",
}


class OpsecError(RuntimeError):
    pass


class ExecutionEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.opsec_cache_path = settings.workspace_root / "intel" / "opsec_cache.json"
        self.host_snapshot_path = settings.workspace_root / "intel" / "host_opsec_snapshot.json"
        if settings.preflight_policy == "off":
            logger.warning(
                "PREFLIGHT_POLICY=off — OPSEC checks are DISABLED. Egress/VPN state will NOT be validated. "
                "This is a debug-only mode; never enable in the field.",
            )

    # ------------------------------------------------------------------
    # Preflight
    # ------------------------------------------------------------------
    def preflight(self) -> dict[str, Any]:
        """Full OPSEC preflight — raises OpsecError on any failed check."""
        route = self._run_text(["sh", "-lc", f"ip route get {self.settings.route_probe_ip}"], timeout=10)
        egress = self._detect_egress_ip()
        nft_status = {
            "opsec_strict": self._check_command(["sh", "-lc", "nft list table inet opsec_strict >/dev/null 2>&1"]),
            "container_vpn_guard": self._check_command(
                ["sh", "-lc", "nft list table inet container_vpn_guard >/dev/null 2>&1"]
            ),
        }
        host_snapshot = self._load_host_snapshot()
        host_summary = self._evaluate_host_snapshot(host_snapshot)

        expected = self.settings.expected_egress_ip
        forbidden = self.settings.forbidden_egress_ip

        allowed_route = (f"dev {_VPN_INTERFACE}" in route) or ("dev eth0" in route and egress == expected)
        egress_ok = bool(egress.strip()) and egress.strip() == expected
        if forbidden:
            egress_ok = egress_ok and egress.strip() != forbidden

        result = {
            "route": route.strip(),
            "egress_ip": egress.strip(),
            "expected_egress_ip": expected,
            "forbidden_egress_ip": forbidden,
            "nft": nft_status,
            "host_snapshot": host_summary,
            "route_ok": allowed_route,
            "egress_ok": egress_ok,
        }
        if not result["route_ok"]:
            raise OpsecError(f"route check failed: {route.strip()}")
        if not result["egress_ok"]:
            raise OpsecError(f"egress check failed: {egress.strip() or 'empty'}")
        if not host_summary["ok"]:
            raise OpsecError(f"host opsec snapshot failed: {host_summary['reason']}")
        return result

    def _preflight_gated(self, level: str) -> dict[str, Any]:
        """Return preflight result or a policy-skipped marker.

        Policy resolution:
          - always      → always call preflight() (raises on failure).
          - active_only → call preflight() only when level == 'active'; otherwise skip.
          - off         → never call preflight().
        """
        policy = self.settings.preflight_policy
        if policy == "off":
            return {"skipped": True, "reason": "policy_off"}
        if policy == "active_only" and level != "active":
            return {"skipped": True, "reason": "policy_active_only", "level": level}
        # default (unknown policy string) falls back to always
        return self.preflight()

    # ------------------------------------------------------------------
    # Egress detection
    # ------------------------------------------------------------------
    def _detect_egress_ip(self) -> str:
        ipv4_pattern = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
        probes = [
            "curl -4 -sS --max-time 5 https://api.ipify.org",
            "curl -4 -sS --max-time 5 https://ifconfig.me/ip",
            "curl -4 -sS --max-time 5 https://api64.ipify.org",
            "curl -4 -sS --max-time 5 https://icanhazip.com",
            "curl -4 -sS --max-time 5 https://ident.me",
            "curl -4 -sS --max-time 5 https://checkip.amazonaws.com",
            "curl -4 -sS --max-time 5 https://ipinfo.io/ip",
            "curl -4 -sS --max-time 5 https://myexternalip.com/raw",
            "curl -4 -sS --max-time 5 https://ipecho.net/plain",
            "curl -4 -sS --max-time 5 https://wtfismyip.com/text",
            "curl -4 -sS --max-time 5 https://ipv4.icanhazip.com",
            "curl -4 -sS --max-time 5 https://v4.ident.me",
            "curl -4 -sS --max-time 5 https://ifconfig.co",
            "curl -4 -sS --max-time 5 https://eth0.me",
            "curl -4 -sS --max-time 5 https://ip.tyk.nu",
            "curl -4 -sS --max-time 5 https://l2.io/ip",
        ]
        for probe in probes:
            value = self._run_text(["sh", "-lc", probe], timeout=6).strip()
            if value and ipv4_pattern.match(value):
                self._write_opsec_cache(value)
                return value
        return self._read_recent_opsec_cache()

    # ------------------------------------------------------------------
    # Sync / async execution
    # ------------------------------------------------------------------
    def execute_sync(
        self,
        *,
        tool: str,
        level: str,
        command: str,
        timeout: int,
        target: str = "",
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        artifacts: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._execute_process(
            tool=tool,
            level=level,
            command=command,
            timeout=timeout,
            target=target,
            cwd=cwd,
            env=env,
            artifacts=artifacts,
            mode="sync",
            job=None,
        )

    def execute_job(
        self,
        *,
        job: Any,
        tool: str,
        level: str,
        command: str,
        timeout: int,
        target: str = "",
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        artifacts: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._execute_process(
            tool=tool,
            level=level,
            command=command,
            timeout=timeout,
            target=target,
            cwd=cwd,
            env=env,
            artifacts=artifacts,
            mode="async",
            job=job,
        )

    def _execute_process(
        self,
        *,
        tool: str,
        level: str,
        command: str,
        timeout: int,
        target: str,
        cwd: Path | None,
        env: dict[str, str] | None,
        artifacts: list[str] | None,
        mode: str,
        job: Any,
    ) -> dict[str, Any]:
        preflight = self._preflight_gated(level)
        process = subprocess.Popen(
            command,
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd) if cwd else None,
            env=env,
        )
        if job is not None:
            job.process_handle = process
            job.process_pid = process.pid

        deadline = time.monotonic() + timeout
        cancelled = False
        timed_out = False
        # Bound the producer/consumer gap. A verbose scanner now applies
        # backpressure to its pipe reader instead of allocating an unbounded
        # number of Python strings and downstream events.
        output_queue: queue.Queue[tuple[str, str | None]] = queue.Queue(
            maxsize=_PROCESS_OUTPUT_QUEUE_SIZE
        )
        output_buffers: dict[str, list[str]] = {"stdout": [], "stderr": []}
        output_sizes = {"stdout": 0, "stderr": 0}
        reader_threads: list[threading.Thread] = []

        def read_stream(stream_name: str, pipe: Any) -> None:
            """Read available bytes in bounded chunks without waiting for newlines."""
            encoding = getattr(pipe, "encoding", None) or "utf-8"
            errors = getattr(pipe, "errors", None) or "replace"
            decoder = codecs.getincrementaldecoder(encoding)(errors=errors)
            try:
                while True:
                    raw = os.read(pipe.fileno(), _PROCESS_OUTPUT_CHUNK_CHARS)
                    if not raw:
                        break
                    text = decoder.decode(raw)
                    if text:
                        output_queue.put((stream_name, text))
                tail = decoder.decode(b"", final=True)
                if tail:
                    output_queue.put((stream_name, tail))
            finally:
                output_queue.put((stream_name, None))

        for stream_name, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
            if pipe is None:
                output_queue.put((stream_name, None))
                continue
            thread = threading.Thread(
                target=lambda name=stream_name, source=pipe, context=copy_context(): context.run(
                    read_stream, name, source
                ),
                name=f"munin-command-{stream_name}",
                daemon=True,
            )
            thread.start()
            reader_threads.append(thread)

        started_at = time.monotonic()
        last_activity = started_at
        last_heartbeat = started_at
        output_sequence = 0
        open_streams = 2
        termination_started_at: float | None = None

        def emit_process_event(event: dict[str, Any]) -> None:
            """Publish to both the active graph stream and an async job buffer."""
            if job is not None:
                sink = getattr(job, "progress_sink", None)
                if sink is not None:
                    try:
                        sink(event)
                    except Exception:  # pragma: no cover - telemetry must not fail a command
                        logger.debug("job progress sink failed", exc_info=True)
            try:
                from ..core.execution_progress import emit_tool_progress  # noqa: PLC0415

                emit_tool_progress(event)
            except Exception:  # pragma: no cover - direct MCP execution has no graph sink
                logger.debug("live process progress emission failed", exc_info=True)

        def terminate_once() -> None:
            if process.poll() is None:
                self._terminate_process(process)

        while open_streams > 0 or process.poll() is None:
            now = time.monotonic()
            if job is not None and getattr(job, "cancel_requested", False):
                cancelled = True
                terminate_once()
                termination_started_at = termination_started_at or now
            elif now >= deadline:
                timed_out = True
                terminate_once()
                termination_started_at = termination_started_at or now

            if termination_started_at is not None and now - termination_started_at > 5:
                break

            try:
                stream_name, text = output_queue.get(timeout=0.25)
            except queue.Empty:
                stream_name, text = "", ""

            if text is None:
                if stream_name:
                    open_streams = max(0, open_streams - 1)
                continue
            if text:
                last_activity = now
                safe_text = str(redact_secrets(text))
                max_chars = max(1, int(self.settings.max_output_chars))
                # os.read already coalesces newline-heavy output. Chunk again
                # defensively because decoded text length is not a byte count.
                for start in range(0, len(safe_text), _PROCESS_OUTPUT_CHUNK_CHARS):
                    chunk = safe_text[start : start + _PROCESS_OUTPUT_CHUNK_CHARS]
                    if not chunk:
                        continue
                    output_sequence += 1
                    output_buffers[stream_name].append(chunk)
                    output_sizes[stream_name] += len(chunk)
                    # Keep an exact bounded tail for the final tool result.
                    excess = output_sizes[stream_name] - max_chars
                    while excess > 0 and output_buffers[stream_name]:
                        first = output_buffers[stream_name][0]
                        if len(first) <= excess:
                            output_buffers[stream_name].pop(0)
                            output_sizes[stream_name] -= len(first)
                            excess -= len(first)
                        else:
                            output_buffers[stream_name][0] = first[excess:]
                            output_sizes[stream_name] -= excess
                            excess = 0
                    emit_process_event(
                        {
                            "kind": "tool_output",
                            "stream": stream_name,
                            "text": chunk,
                            "sequence": output_sequence,
                            "elapsed_ms": int((now - started_at) * 1000),
                            "final": False,
                        }
                    )

            now = time.monotonic()
            if now - last_heartbeat >= max(1.0, _PROCESS_HEARTBEAT_SECONDS) and process.poll() is None:
                last_heartbeat = now
                emit_process_event(
                    {
                        "kind": "tool_heartbeat",
                        "stream": "meta",
                        "text": "command still running",
                        "elapsed_ms": int((now - started_at) * 1000),
                        "last_output_ms": int((now - last_activity) * 1000),
                        "transient": True,
                    }
                )

            if (cancelled or timed_out) and process.poll() is not None and open_streams == 0:
                break

        for thread in reader_threads:
            thread.join(timeout=1)
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            terminate_once()

        postflight = self._preflight_gated(level)
        return_code = process.returncode if process.returncode is not None else -9
        ok = (return_code == 0) and not cancelled and not timed_out
        stdout = truncate_text("".join(output_buffers["stdout"]), self.settings.max_output_chars)
        stderr = truncate_text("".join(output_buffers["stderr"]), self.settings.max_output_chars)
        error = None
        summary = f"{tool} {'completed' if ok else 'failed'}"
        if cancelled:
            summary = f"{tool} cancelled"
            error = {"code": "cancelled", "message": "job was cancelled"}
        elif timed_out:
            summary = f"{tool} timed out"
            error = {"code": "timeout", "message": f"command exceeded {timeout}s"}
        elif not ok:
            error = {"code": "command_failed", "message": stderr_tail(stderr) or f"return code {return_code}"}
        return {
            "ok": ok,
            "tool": tool,
            "mode": mode,
            "summary": summary,
            "data": {
                "target": target,
                "command": command,
                "return_code": return_code,
                "stdout": stdout,
                "stderr": stderr,
                "stderr_tail": stderr_tail(stderr),
                "opsec_preflight": preflight,
                "opsec_postflight": postflight,
                "cwd": str(cwd) if cwd else "",
                "level": level,
                "cancelled": cancelled,
                "timed_out": timed_out,
                "preflight_policy": self.settings.preflight_policy,
            },
            "artifacts": artifacts or [],
            "error": error,
        }

    def dependency_result(self, tool: str, dependency: str) -> dict[str, Any]:
        """Return a structured error when a required system binary is not in PATH.

        Includes an install hint so the LLM (and the human operator) know what to
        do next. Previously this returned a bare "missing_dependency" which the
        LLM often retried into a loop.
        """
        return {
            "ok": False,
            "tool": tool,
            "mode": "sync",
            "summary": f"{tool} unavailable — required binary '{dependency}' not on PATH",
            "error": {
                "code": "missing_dependency",
                "message": f"{dependency} is required for {tool} but was not found on PATH",
                "dependency": dependency,
                "path": os.environ.get("PATH", ""),
                "install_hint": _INSTALL_HINTS.get(dependency, f"install {dependency} from your OS package manager or its upstream release page"),
            },
            "data": {
                "dependency": dependency,
                "found": False,
                "path_searched": os.environ.get("PATH", ""),
            },
        }

    def _run_text(self, args: list[str], timeout: int) -> str:
        try:
            completed = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
            return (completed.stdout or completed.stderr or "").strip()
        except Exception as exc:  # pragma: no cover - safety net
            raise OpsecError(str(exc)) from exc

    def _check_command(self, args: list[str]) -> str:
        try:
            completed = subprocess.run(args, capture_output=True, text=True, timeout=10, check=False)
        except Exception:
            return "unknown"
        return "present" if completed.returncode == 0 else "missing"

    def _terminate_process(self, process: subprocess.Popen[str]) -> None:
        try:
            process.terminate()
            process.wait(timeout=3)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def _write_opsec_cache(self, egress_ip: str) -> None:
        ensure_parent(self.opsec_cache_path)
        payload = {
            "egress_ip": egress_ip,
            "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        }
        self.opsec_cache_path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")

    def _read_recent_opsec_cache(self) -> str:
        try:
            payload = json.loads(self.opsec_cache_path.read_text(encoding="utf-8"))
            updated_at = datetime.fromisoformat(str(payload.get("updated_at", "")).replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - updated_at <= timedelta(minutes=10):
                return str(payload.get("egress_ip", "")).strip()
        except Exception:
            return ""
        return ""

    def _load_host_snapshot(self) -> dict[str, Any]:
        if not self.host_snapshot_path.exists():
            return {}
        try:
            return json.loads(self.host_snapshot_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _evaluate_host_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        required_services = _REQUIRED_SERVICES
        required_tables = _REQUIRED_NFT_TABLES
        if not snapshot:
            return {"ok": False, "reason": "snapshot_missing", "updated_at": "", "services": {}, "nft": {}}
        updated_at = str(snapshot.get("updated_at", "")).strip()
        try:
            parsed = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        except Exception:
            return {
                "ok": False,
                "reason": "snapshot_invalid_timestamp",
                "updated_at": updated_at,
                "services": snapshot.get("services", {}),
                "nft": snapshot.get("nft", {}),
            }
        age = datetime.now(timezone.utc) - parsed
        services = snapshot.get("services", {}) or {}
        tables = snapshot.get("nft", {}) or {}
        missing_services = [name for name in required_services if services.get(name) != "active"]
        missing_tables = [name for name in required_tables if tables.get(name) != "present"]
        if age > timedelta(minutes=10):
            return {
                "ok": False,
                "reason": "snapshot_stale",
                "updated_at": updated_at,
                "services": services,
                "nft": tables,
                "age_seconds": int(age.total_seconds()),
            }
        if missing_services:
            return {
                "ok": False,
                "reason": f"services_inactive:{','.join(missing_services)}",
                "updated_at": updated_at,
                "services": services,
                "nft": tables,
            }
        if missing_tables:
            return {
                "ok": False,
                "reason": f"nft_missing:{','.join(missing_tables)}",
                "updated_at": updated_at,
                "services": services,
                "nft": tables,
            }
        return {
            "ok": True,
            "reason": "ok",
            "updated_at": updated_at,
            "services": services,
            "nft": tables,
            "age_seconds": int(age.total_seconds()),
        }


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None
