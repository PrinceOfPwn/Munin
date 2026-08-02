# tags: [tool-forge, evasion, hitl-approval, supervisor, runtime, GuardReport, validate_extension_diff, dangerous-additions-regex, git-apply-check, paths-in-diff, _DANGEROUS_ADDITIONS, is_allowed_target, normalise_path, binary-patch-forbidden, proposal-guard]
"""Structural guard for reviewable self-extension diffs.

This is deliberately a proposal guard, not an execution sandbox.  Every
extension still goes through a GitHub PR and never auto-merges.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .extension_manifest import is_allowed_target, normalise_path

_DANGEROUS_ADDITIONS = (
    re.compile(r"^\+\s*(?:from\s+subprocess\b|import\s+subprocess\b|import\s+ctypes\b|import\s+socket\b)"),
    re.compile(r"^\+.*\b(?:eval|exec|__import__|globals|locals|vars)\s*\("),
    re.compile(r"^\+.*\bos\.(?:system|popen|exec|fork)\s*\("),
    re.compile(r"^\+.*\b(?:pickle|marshal|shelve)\.(?:load|loads)\s*\("),
)


@dataclass(frozen=True)
class GuardReport:
    ok: bool
    errors: list[str]
    touched_paths: list[str]

    def to_dict(self) -> dict[str, object]:
        return {"ok": self.ok, "errors": self.errors, "touched_paths": self.touched_paths}


def _paths_in_diff(diff: str) -> list[str]:
    paths: list[str] = []
    for line in diff.splitlines():
        if not line.startswith("+++ b/"):
            continue
        paths.append(normalise_path(line.removeprefix("+++ b/").strip()))
    return paths


def validate_extension_diff(repo_root: Path, diff: str, target_paths: list[str]) -> GuardReport:
    """Reject unsafe targets/additions and require ``git apply --check`` success."""
    errors: list[str] = []
    touched = _paths_in_diff(diff)
    expected = {normalise_path(path) for path in target_paths}
    if not touched:
        errors.append("diff has no +++ b/<path> file headers")
    if set(touched) != expected:
        errors.append("diff paths must exactly match target_paths")
    for path in touched:
        if not is_allowed_target(path):
            errors.append(f"path is protected: {path}")
    if "GIT binary patch" in diff or "\x00" in diff:
        errors.append("binary patches are forbidden")
    for line_no, line in enumerate(diff.splitlines(), start=1):
        if any(pattern.search(line) for pattern in _DANGEROUS_ADDITIONS):
            errors.append(f"dangerous added code at diff line {line_no}")
    if not errors:
        try:
            checked = subprocess.run(
                ["git", "apply", "--check", "--whitespace=nowarn", "-"],
                cwd=repo_root,
                input=diff,
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            errors.append(f"could not validate patch application: {exc}")
        else:
            if checked.returncode:
                errors.append((checked.stderr or checked.stdout or "git apply --check failed").strip()[:500])
    return GuardReport(ok=not errors, errors=errors, touched_paths=touched)
