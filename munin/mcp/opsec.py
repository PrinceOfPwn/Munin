from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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
        stdout_raw = ""
        stderr_raw = ""
        while True:
            if job is not None and getattr(job, "cancel_requested", False):
                cancelled = True
                self._terminate_process(process)
                try:
                    stdout_raw, stderr_raw = process.communicate(timeout=5)
                except Exception:
                    stdout_raw = stdout_raw or ""
                    stderr_raw = stderr_raw or ""
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                self._terminate_process(process)
                try:
                    stdout_raw, stderr_raw = process.communicate(timeout=5)
                except Exception:
                    stdout_raw = stdout_raw or ""
                    stderr_raw = stderr_raw or ""
                break
            try:
                stdout_raw, stderr_raw = process.communicate(timeout=min(2, remaining))
                break
            except subprocess.TimeoutExpired:
                continue

        postflight = self._preflight_gated(level)
        return_code = process.returncode if process.returncode is not None else -9
        ok = (return_code == 0) and not cancelled and not timed_out
        stdout = truncate_text(stdout_raw or "", self.settings.max_output_chars)
        stderr = truncate_text(stderr_raw or "", self.settings.max_output_chars)
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
        return {
            "ok": False,
            "tool": tool,
            "mode": "sync",
            "summary": f"missing dependency: {dependency}",
            "error": {"code": "missing_dependency", "message": f"{dependency} is required"},
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
