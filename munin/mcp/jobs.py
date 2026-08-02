# tags: [mcp, parallel, supervisor, runtime, orchestrator, JobManager, JobRecord, ThreadPoolExecutor, progress_for_run, add_progress, has_active_run, job_status, job_cancel, MAX_PENDING_PROGRESS_EVENTS, _compact_result]
from __future__ import annotations

import logging
import os

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Condition, Lock
from typing import Any
from uuid import uuid4

from .utils import stderr_tail, truncate_text, utc_now_iso

LOCK_TIMEOUT = 2.0
MAX_PENDING_PROGRESS_EVENTS = max(
    128, int(os.environ.get("MUNIN_MAX_PENDING_PROGRESS_EVENTS", "1024"))
)
logger = logging.getLogger(__name__)


@dataclass
class JobRecord:
    job_id: str
    tool: str
    level: str
    target: str
    command_preview: str
    created_at: str
    run_id: str = ""
    tool_call_id: str = ""
    status: str = "queued"
    started_at: str = ""
    finished_at: str = ""
    result: dict[str, Any] | None = None
    error: str = ""
    future: Future | None = None
    process_pid: int = 0
    process_handle: Any = None
    cancel_requested: bool = False
    # Small, operator-safe milestones emitted while a long job is running.
    # They make polling useful without retaining hidden model reasoning.
    progress: list[dict[str, Any]] | None = None
    progress_sequence: int = 0
    progress_sink: Callable[[dict[str, Any]], None] | None = None


class JobManager:
    def __init__(self, workers: int) -> None:
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="offx-mcp")
        self.records: dict[str, JobRecord] = {}
        self.lock = Lock()
        self.progress_changed = Condition(self.lock)
        self.is_shutdown = False

    def shutdown(self) -> None:
        """Release worker threads during server/test shutdown.

        A job manager is an execution detail, never the durable source of
        operation truth. Closing it prevents interpreter hangs after tests and
        avoids retaining queued work when a server process exits; the durable
        run dispatcher safely recovers the corresponding Turso lease.
        """
        with self.progress_changed:
            if self.is_shutdown:
                return
            self.is_shutdown = True
            self.progress_changed.notify_all()
        self.executor.shutdown(wait=False, cancel_futures=True)

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
        run_id: str = "",
        tool_call_id: str = "",
        fn: Callable[[JobRecord], dict[str, Any]],
        on_finish: Callable[[JobRecord], None] | None = None,
    ) -> JobRecord:
        if self.is_shutdown:
            raise RuntimeError("job manager is shutting down")
        job = JobRecord(
            job_id=uuid4().hex,
            tool=tool,
            level=level,
            target=target,
            command_preview=command_preview,
            created_at=utc_now_iso(),
            run_id=run_id,
            tool_call_id=tool_call_id,
            progress=[],
        )
        with self.lock:
            self.records[job.job_id] = job
        job.progress_sink = lambda event, job_id=job.job_id: self.add_progress(job_id, event)
        future = self.executor.submit(self._run_job, job.job_id, fn, on_finish)
        job.future = future
        return job

    def add_progress(self, job_id: str, event: dict[str, Any]) -> None:
        """Append a bounded execution milestone to a job.

        This intentionally records observable lifecycle events (LLM request,
        tool start/result), not private chain-of-thought text.
        """
        with self.progress_changed:
            job = self.records.get(job_id)
            if not job:
                return
            if job.progress is None:
                job.progress = []
            # A run-scoped consumer acknowledges events in progress_for_run.
            # Backpressure here preserves every unread output chunk instead of
            # silently truncating the first burst beyond an arbitrary 100 rows.
            while (
                job.run_id
                and len(job.progress) >= MAX_PENDING_PROGRESS_EVENTS
                and not self.is_shutdown
            ):
                self.progress_changed.wait(timeout=0.25)
            if self.is_shutdown:
                return
            job.progress_sequence += 1
            payload = dict(event)
            if "sequence" in payload:
                payload.setdefault("source_sequence", payload["sequence"])
            job.progress.append(
                {
                    "at": utc_now_iso(),
                    **payload,
                    # Manager-owned identity and ordering; never caller supplied.
                    "sequence": job.progress_sequence,
                    "run_id": job.run_id,
                    "job_id": job.job_id,
                    "tool_name": job.tool,
                    "tool_call_id": job.tool_call_id,
                }
            )
            # Direct MCP jobs have no run stream to acknowledge progress. Keep
            # their polling payload compact without affecting run-scoped data.
            if not job.run_id and len(job.progress) > 100:
                del job.progress[:-100]

    def progress_for_run(
        self,
        run_id: str,
        cursors: dict[str, int] | None = None,
    ) -> list[dict[str, Any]]:
        """Return new live command events for one supervisor run.

        Jobs are intentionally an in-memory execution detail.  The caller
        persists the returned events through the durable run-event log, while
        this cursor prevents duplicate UIMessage chunks during one stream.
        """
        if not run_id:
            return []
        cursors = cursors if cursors is not None else {}
        if not self._acquire_lock():
            return []
        try:
            events: list[dict[str, Any]] = []
            consumed_any = False
            for job in self.records.values():
                if job.run_id != run_id:
                    continue
                after = int(cursors.get(job.job_id, 0))
                for event in (job.progress or []):
                    sequence = int(event.get("sequence") or 0)
                    if sequence <= after:
                        continue
                    if event.get("kind") in {"tool_output", "tool_heartbeat"}:
                        events.append(dict(event))
                    cursors[job.job_id] = max(cursors.get(job.job_id, 0), sequence)
                acknowledged = int(cursors.get(job.job_id, 0))
                if acknowledged and job.progress:
                    before = len(job.progress)
                    job.progress[:] = [
                        event
                        for event in job.progress
                        if int(event.get("sequence") or 0) > acknowledged
                    ]
                    consumed_any = consumed_any or len(job.progress) != before
            if consumed_any:
                self.progress_changed.notify_all()
            events.sort(key=lambda item: (str(item.get("at") or ""), int(item.get("sequence") or 0)))
            return events
        finally:
            self.lock.release()

    def has_active_run(self, run_id: str) -> bool:
        """Return whether a command belonging to ``run_id`` is still running.

        The supervisor stream may reach its terminal graph event while an
        asynchronous command is flushing its final stdout/stderr lines.  The
        runtime uses this small read-only signal to keep the UI stream open
        until those already-authorized output chunks have been delivered.
        """
        if not run_id:
            return False
        if not self._acquire_lock():
            # Contention is not proof of completion. Keep the stream open and
            # retry on the next poll rather than truncating final output.
            return True
        try:
            return any(
                job.run_id == run_id and job.status in {"queued", "running"}
                for job in self.records.values()
            )
        finally:
            self.lock.release()

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
                "run_id": job.run_id,
                "tool_call_id": job.tool_call_id,
                "level": job.level,
                "target": job.target,
                "command_preview": truncate_text(job.command_preview, 180),
                "status": job.status,
                "created_at": job.created_at,
                "started_at": job.started_at,
                "finished_at": job.finished_at,
                "process_pid": job.process_pid,
                "cancel_requested": job.cancel_requested,
                "progress": list(job.progress or []),
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
