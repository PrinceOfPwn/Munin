from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable
from uuid import uuid4

from .utils import stderr_tail, truncate_text, utc_now_iso

LOCK_TIMEOUT = 2.0


@dataclass
class JobRecord:
    job_id: str
    tool: str
    level: str
    target: str
    command_preview: str
    created_at: str
    status: str = "queued"
    started_at: str = ""
    finished_at: str = ""
    result: dict[str, Any] | None = None
    error: str = ""
    future: Future | None = None
    process_pid: int = 0
    process_handle: Any = None
    cancel_requested: bool = False


class JobManager:
    def __init__(self, workers: int) -> None:
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="offx-mcp")
        self.records: dict[str, JobRecord] = {}
        self.lock = Lock()

    def _acquire_lock(self, timeout: float = LOCK_TIMEOUT) -> bool:
        return self.lock.acquire(timeout=timeout)

    def _lock_error(self, tool: str) -> dict[str, Any]:
        return {
            "ok": False,
            "tool": tool,
            "mode": "sync",
            "summary": f"{tool} timed out waiting for manager lock",
            "error": {"code": "lock_timeout", "message": f"{tool} timed out waiting for manager lock"},
        }

    def submit(
        self,
        *,
        tool: str,
        level: str,
        target: str,
        command_preview: str,
        fn: Callable[[JobRecord], dict[str, Any]],
        on_finish: Callable[[JobRecord], None] | None = None,
    ) -> JobRecord:
        job = JobRecord(
            job_id=uuid4().hex,
            tool=tool,
            level=level,
            target=target,
            command_preview=command_preview,
            created_at=utc_now_iso(),
        )
        with self.lock:
            self.records[job.job_id] = job
        future = self.executor.submit(self._run_job, job.job_id, fn, on_finish)
        job.future = future
        return job

    def _run_job(
        self,
        job_id: str,
        fn: Callable[[JobRecord], dict[str, Any]],
        on_finish: Callable[[JobRecord], None] | None,
    ) -> dict[str, Any]:
        with self.lock:
            job = self.records[job_id]
            job.status = "running"
            job.started_at = utc_now_iso()
        try:
            result = fn(job)
            with self.lock:
                job = self.records[job_id]
                job.result = result
                job.finished_at = utc_now_iso()
                if job.status != "cancelled":
                    job.status = "succeeded" if result.get("ok", False) else "failed"
                job.process_handle = None
                job.process_pid = 0
            if on_finish:
                on_finish(job)
            return result
        except Exception as exc:  # pragma: no cover - safety net
            with self.lock:
                job = self.records[job_id]
                job.error = str(exc)
                job.finished_at = utc_now_iso()
                job.status = "failed"
                job.process_handle = None
                job.process_pid = 0
                job.result = {
                    "ok": False,
                    "tool": job.tool,
                    "mode": "async",
                    "summary": f"job failed: {exc}",
                    "error": {"code": "job_failed", "message": str(exc)},
                }
            if on_finish:
                on_finish(job)
            return job.result

    def _compact_result(self, result: dict[str, Any] | None) -> dict[str, Any]:
        if not result:
            return {}
        data = result.get("data", {}) if isinstance(result.get("data", {}), dict) else {}
        stdout = str(data.get("stdout") or result.get("stdout") or "")
        stderr = str(data.get("stderr") or result.get("stderr") or "")
        artifacts = result.get("artifacts", []) or data.get("artifacts", []) or []
        return {
            "ok": result.get("ok"),
            "summary": truncate_text(str(result.get("summary", "")), 300),
            "return_code": data.get("return_code"),
            "cancelled": data.get("cancelled", False),
            "timed_out": data.get("timed_out", False),
            "stdout_tail": truncate_text("\n".join(stdout.strip().splitlines()[-25:]), 3000),
            "stderr_tail": truncate_text("\n".join(stderr.strip().splitlines()[-20:]), 2000),
            "artifacts": artifacts[:25] if isinstance(artifacts, list) else artifacts,
            "opsec": {
                "egress_ip": (data.get("opsec_post") or data.get("opsec_preflight") or {}).get("egress_ip"),
                "route_ok": (data.get("opsec_post") or data.get("opsec_preflight") or {}).get("route_ok"),
                "egress_ok": (data.get("opsec_post") or data.get("opsec_preflight") or {}).get("egress_ok"),
            },
        }

    def status(self, job_id: str, include_result: bool = False) -> dict[str, Any]:
        if not self._acquire_lock():
            return self._lock_error("job_status")
        try:
            job = self.records.get(job_id)
            if not job:
                return {
                    "ok": False,
                    "tool": "job_status",
                    "mode": "sync",
                    "summary": f"job {job_id} not found",
                    "error": {"code": "job_not_found", "message": f"job {job_id} not found"},
                }
            result_compact = self._compact_result(job.result)
            data = {
                "job_id": job.job_id,
                "tool": job.tool,
                "level": job.level,
                "target": job.target,
                "command_preview": truncate_text(job.command_preview, 180),
                "status": job.status,
                "created_at": job.created_at,
                "started_at": job.started_at,
                "finished_at": job.finished_at,
                "process_pid": job.process_pid,
                "cancel_requested": job.cancel_requested,
                "result_compact": result_compact,
                "stderr_tail": result_compact.get("stderr_tail") or stderr_tail(job.error, 10),
            }
            if include_result:
                data["result"] = job.result
            return {
                "ok": True,
                "tool": "job_status",
                "mode": "sync",
                "job_id": job.job_id,
                "summary": f"{job.tool} is {job.status}",
                "data": data,
            }
        finally:
            self.lock.release()

    def cancel(self, job_id: str) -> dict[str, Any]:
        if not self._acquire_lock():
            return self._lock_error("job_cancel")
        try:
            job = self.records.get(job_id)
            if not job:
                return {
                    "ok": False,
                    "tool": "job_cancel",
                    "mode": "sync",
                    "summary": f"job {job_id} not found",
                    "error": {"code": "job_not_found", "message": f"job {job_id} not found"},
                }
            cancelled = False
            if job.future and job.future.cancel():
                cancelled = True
                job.cancel_requested = True
                job.status = "cancelled"
                job.finished_at = utc_now_iso()
            elif job.status == "running":
                job.cancel_requested = True
                process = job.process_handle
                if process is not None:
                    try:
                        process.terminate()
                        cancelled = True
                    except Exception:
                        cancelled = False
                else:
                    cancelled = True
                if cancelled:
                    job.status = "cancelled"
                    job.finished_at = utc_now_iso()
            return {
                "ok": cancelled,
                "tool": "job_cancel",
                "mode": "sync",
                "job_id": job_id,
                "summary": "job cancelled" if cancelled else "job could not be cancelled",
                "data": {"job_id": job_id, "cancelled": cancelled},
                "error": None if cancelled else {"code": "cancel_failed", "message": "job already running or finished"},
            }
        finally:
            self.lock.release()
