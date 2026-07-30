from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .utils import ensure_parent, json_dumps, utc_now_iso


# Redact anything that looks like a secret before it reaches the audit trail on disk.
# The audit trail persists command strings and params from every tool call, so a naive
# subprocess wrapper can spill LLM_API_KEY / TAVILY_API_KEY / GITHUB_TOKEN / Bearer headers
# into events.jsonl. Regexes below are best-effort; each is anchored to a common shape.
_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(Authorization\s*[:=]\s*Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE), r"\1***REDACTED***"),
    (re.compile(r"(api[_-]?key\s*[:=]\s*)['\"]?[A-Za-z0-9._\-]{16,}", re.IGNORECASE), r"\1***REDACTED***"),
    (re.compile(r"(token\s*[:=]\s*)['\"]?[A-Za-z0-9._\-]{16,}", re.IGNORECASE), r"\1***REDACTED***"),
    (re.compile(r"(password\s*[:=]\s*)['\"]?[^\s'\"]{4,}", re.IGNORECASE), r"\1***REDACTED***"),
    (re.compile(r"nvapi-[A-Za-z0-9._\-]+"), "nvapi-***REDACTED***"),
    (re.compile(r"tvly-[A-Za-z0-9._\-]+"), "tvly-***REDACTED***"),
    (re.compile(r"sk-[A-Za-z0-9]{32,}"), "sk-***REDACTED***"),
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "ghp-***REDACTED***"),
    (re.compile(r"gho_[A-Za-z0-9]{20,}"), "gho-***REDACTED***"),
]


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        out = value
        for pattern, replacement in _SECRET_PATTERNS:
            out = pattern.sub(replacement, out)
        return out
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact(val) for key, val in value.items()}
    return value


class AuditTrailLogger:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root

    def timeline_dir(self, run_id: str) -> Path:
        return self.workspace_root / "runs" / run_id / "timeline"

    def record(
        self,
        *,
        run_id: str,
        tool: str,
        level: str,
        mode: str,
        status: str,
        target: str,
        source_context: str,
        command_or_params: Any,
        job_id: str = "",
        artifacts: list[str] | None = None,
        opsec_preflight: dict[str, Any] | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        summary: str = "",
    ) -> dict[str, Any]:
        event = {
            "event_id": f"{tool}-{utc_now_iso().replace(':', '').replace('-', '')}",
            "timestamp_start_utc": started_at or utc_now_iso(),
            "timestamp_end_utc": finished_at or utc_now_iso(),
            "tool": tool,
            "level": level,
            "mode": mode,
            "status": status,
            "source_context": source_context,
            "target": target,
            "command_or_params": _redact(command_or_params),
            "job_id": job_id,
            "artifacts": artifacts or [],
            "opsec_preflight": opsec_preflight or {},
            "summary": _redact(summary) if isinstance(summary, str) else summary,
        }
        self._append_jsonl(run_id, event)
        self._append_markdown(run_id, event)
        return event

    def _append_jsonl(self, run_id: str, event: dict[str, Any]) -> None:
        path = self.timeline_dir(run_id) / "events.jsonl"
        ensure_parent(path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json_dumps(event))
            handle.write("\n")

    def _append_markdown(self, run_id: str, event: dict[str, Any]) -> None:
        path = self.timeline_dir(run_id) / "timeline.md"
        ensure_parent(path)
        if not path.exists():
            path.write_text("# Timeline\n\n", encoding="utf-8")
        line = (
            f"- `{event['timestamp_start_utc']}` `{event['level']}` `{event['tool']}` "
            f"`{event['mode']}` `{event['status']}` target=`{event['target'] or '-'}` "
            f"job=`{event['job_id'] or '-'}` summary={event['summary'] or '-'}\n"
        )
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
