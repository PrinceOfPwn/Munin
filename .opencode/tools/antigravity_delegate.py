from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from google.antigravity import Agent, CapabilitiesConfig, LocalAgentConfig

MAX_CAPTURE_CHARS = 80_000


@dataclass
class CommandResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str


def _truncate(value: str, limit: int = MAX_CAPTURE_CHARS) -> str:
    if len(value) <= limit:
        return value
    return "[truncated]\n" + value[-limit:]


def _run(
    argv: list[str],
    *,
    cwd: Path,
    timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
        env=os.environ.copy(),
    )


def _git(workspace: Path, *args: str) -> str:
    result = _run(["git", *args], cwd=workspace)
    return result.stdout if result.returncode == 0 else ""


def _status_files(workspace: Path) -> list[str]:
    files: list[str] = []
    for line in _git(workspace, "status", "--short", "--untracked-files=all").splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        files.append(path)
    return sorted(set(files))


def _is_tracked(workspace: Path, relative_path: str) -> bool:
    result = _run(
        ["git", "ls-files", "--error-unmatch", "--", relative_path],
        cwd=workspace,
    )
    return result.returncode == 0


def _review_diff(workspace: Path) -> str:
    sections = [
        _git(
            workspace,
            "diff",
            "--no-ext-diff",
            "--src-prefix=a/",
            "--dst-prefix=b/",
        )
    ]

    for relative_path in _status_files(workspace):
        if _is_tracked(workspace, relative_path):
            continue

        path = workspace / relative_path
        if not path.is_file():
            continue

        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            sections.append(
                f"\n--- /dev/null\n+++ b/{relative_path}\n"
                "[new binary or unreadable file]\n"
            )
            continue

        lines = content.splitlines()
        added = "\n".join(f"+{line}" for line in lines)
        sections.append(
            f"\n--- /dev/null\n+++ b/{relative_path}\n"
            f"@@ -0,0 +1,{len(lines)} @@\n{added}\n"
        )

    return _truncate("".join(sections))


def _run_validation(command: str, workspace: Path, timeout: int) -> CommandResult:
    try:
        result = subprocess.run(
            command,
            cwd=workspace,
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=os.environ.copy(),
        )
        return CommandResult(
            command=command,
            exit_code=result.returncode,
            stdout=_truncate(result.stdout),
            stderr=_truncate(result.stderr),
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return CommandResult(
            command=command,
            exit_code=124,
            stdout=_truncate(stdout),
            stderr=_truncate(stderr + f"\nTimed out after {timeout} seconds."),
        )


async def delegate(request: dict[str, Any]) -> dict[str, Any]:
    workspace = Path(request["workspace"]).resolve()
    task = str(request["task"]).strip()
    allowed_paths = [str(item) for item in request.get("allowed_paths", [])]
    validations = [str(item) for item in request.get("validation", [])]
    validation_timeout = int(request.get("validation_timeout", 300))

    if not workspace.is_dir() or not (workspace / ".git").exists():
        raise ValueError(f"Not a Git worktree: {workspace}")
    if not task:
        raise ValueError("Task must not be empty")
    if not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY is not configured")

    dirty_before = _status_files(workspace)
    before_diff = _git(workspace, "diff", "--no-ext-diff")

    scope = "\n".join(f"- {path}" for path in allowed_paths) or (
        "No explicit path allowlist was supplied. Keep the patch minimal."
    )
    requested_checks = "\n".join(f"- {cmd}" for cmd in validations) or (
        "Discover the smallest relevant check, but report exactly what ran."
    )

    instructions = f"""
You are a delegated coding worker inside the Munin repository.

Workspace: {workspace}

Rules:
- Inspect repository instructions and relevant code before editing.
- Implement only the requested behavior and keep the patch minimal.
- Preserve existing architecture and public behavior unless explicitly requested.
- Add or update tests when behavior changes.
- Never stage, commit, push, stash, reset, restore, checkout, clean, or alter Git history.
- Never print or persist secrets.
- Never modify files outside the workspace.
- Do not claim a command passed unless you observed its result.
- Finish with a factual summary of files changed, checks run, and unresolved risks.

Expected modification scope:
{scope}

Requested validation:
{requested_checks}
""".strip()

    previous_cwd = Path.cwd()
    try:
        os.chdir(workspace)
        config = LocalAgentConfig(
            system_instructions=instructions,
            capabilities=CapabilitiesConfig(),
        )
        async with Agent(config) as agent:
            response = await agent.chat(
                "Complete this coding task by modifying the current worktree:\n\n"
                + task
            )
            worker_report = await response.text()
    finally:
        os.chdir(previous_cwd)

    validation_results = [
        _run_validation(command, workspace, validation_timeout)
        for command in validations
    ]
    dirty_after = _status_files(workspace)
    after_diff = _git(workspace, "diff", "--no-ext-diff")

    return {
        "status": "changed" if before_diff != after_diff or dirty_before != dirty_after else "unchanged",
        "workspace": str(workspace),
        "worker_report": _truncate(worker_report),
        "dirty_before": dirty_before,
        "dirty_after": dirty_after,
        "changed_files": dirty_after,
        "newly_dirty_files": sorted(set(dirty_after) - set(dirty_before)),
        "diff_stat": _git(workspace, "diff", "--stat"),
        "diff": _review_diff(workspace),
        "validation": [asdict(result) for result in validation_results],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True, help="JSON request payload")
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    try:
        result = await delegate(json.loads(args.request))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001 - the wrapper must return structured errors
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": type(exc).__name__,
                    "message": str(exc),
                },
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
