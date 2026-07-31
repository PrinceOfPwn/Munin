"""Human-governed self-extension proposals and explicit PR publication."""

from __future__ import annotations

import os
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..mcp.config import Settings
from .extension_guard import GuardReport, validate_extension_diff
from .extension_manifest import ExtensionManifest, list_manifests


@dataclass(frozen=True)
class ExtensionResult:
    ok: bool
    status: str
    slug: str
    summary: str
    guard: GuardReport | None = None
    pr_url: str = ""
    branch: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok, "status": self.status, "slug": self.slug, "summary": self.summary,
            "guard": self.guard.to_dict() if self.guard else None, "pr_url": self.pr_url, "branch": self.branch,
        }


class ExtensionForge:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.repo_root = settings.workspace_root
        self.directory = self.repo_root / "munin" / "extensions"

    def propose(self, *, slug: str, kind: str, rationale: str, target_paths: list[str], diff: str, tests: list[str]) -> ExtensionResult:
        try:
            manifest = ExtensionManifest(slug=slug, kind=kind, rationale=rationale, target_paths=target_paths, diff=diff, tests=tests)
        except ValueError as exc:
            return ExtensionResult(False, "rejected", slug, str(exc))
        guard = validate_extension_diff(self.repo_root, manifest.diff, manifest.target_paths)
        if not guard.ok:
            manifest.status = "rejected"
            manifest.save(self.directory)
            return ExtensionResult(False, "rejected", slug, "proposal rejected by extension guard", guard=guard)
        manifest.status = "validated"
        manifest.save(self.directory)
        return ExtensionResult(True, "validated", slug, "proposal validated; operator approval is required before opening a PR", guard=guard)

    def list(self) -> list[ExtensionManifest]:
        return list_manifests(self.directory)

    def describe(self, slug: str) -> ExtensionManifest | None:
        path = self.directory / f"{slug}.json"
        try:
            return ExtensionManifest.from_file(path)
        except (OSError, ValueError):
            return None

    def open_pr(self, slug: str, *, operator_approved: bool) -> ExtensionResult:
        manifest = self.describe(slug)
        if manifest is None:
            return ExtensionResult(False, "rejected", slug, "extension manifest was not found")
        if not operator_approved:
            return ExtensionResult(False, manifest.status, slug, "operator_approved=true is required to open a self-extension PR")
        if manifest.status != "validated":
            return ExtensionResult(False, manifest.status, slug, "only a validated proposal can open a PR")
        guard = validate_extension_diff(self.repo_root, manifest.diff, manifest.target_paths)
        if not guard.ok:
            manifest.status = "rejected"
            manifest.save(self.directory)
            return ExtensionResult(False, "rejected", slug, "proposal changed or no longer applies", guard=guard)
        branch = f"munin/extension-{slug}"
        base = os.environ.get("MUNIN_PR_BASE_BRANCH", "main").strip() or "main"
        temp_dir = Path(tempfile.mkdtemp(prefix="munin-extension-"))
        added_worktree = False
        try:
            add = self._run(["git", "worktree", "add", "-b", branch, str(temp_dir), base], cwd=self.repo_root, timeout=60)
            if add.returncode:
                return ExtensionResult(False, "validated", slug, f"could not create isolated worktree: {(add.stderr or add.stdout).strip()[:500]}", guard=guard, branch=branch)
            added_worktree = True
            applied = self._run(["git", "apply", "--whitespace=nowarn", "-"], cwd=temp_dir, input_text=manifest.diff, timeout=30)
            if applied.returncode:
                return ExtensionResult(False, "validated", slug, f"could not apply proposal: {(applied.stderr or applied.stdout).strip()[:500]}", guard=guard, branch=branch)
            manifest_copy = temp_dir / "munin" / "extensions" / f"{slug}.json"
            manifest_copy.parent.mkdir(parents=True, exist_ok=True)
            manifest_copy.write_text(json.dumps(manifest.to_dict(), indent=2) + "\n", encoding="utf-8")
            compiled = self._compile_python_targets(temp_dir, manifest.target_paths)
            if compiled:
                return ExtensionResult(False, "validated", slug, compiled, guard=guard, branch=branch)
            staged = self._run(["git", "add", "--", *manifest.target_paths, str(manifest_copy.relative_to(temp_dir))], cwd=temp_dir, timeout=30)
            if staged.returncode:
                return ExtensionResult(False, "validated", slug, f"could not stage proposal: {(staged.stderr or staged.stdout).strip()[:500]}", guard=guard, branch=branch)
            self._run(["git", "config", "user.name", os.environ.get("MUNIN_GIT_USER", "munin-bot")], cwd=temp_dir, timeout=15)
            self._run(["git", "config", "user.email", os.environ.get("MUNIN_GIT_EMAIL", "munin-bot@users.noreply.github.com")], cwd=temp_dir, timeout=15)
            committed = self._run(["git", "commit", "-m", f"[munin-extension] {slug}"], cwd=temp_dir, timeout=60)
            if committed.returncode:
                return ExtensionResult(False, "validated", slug, f"could not commit proposal: {(committed.stderr or committed.stdout).strip()[:500]}", guard=guard, branch=branch)
            pushed = self._run(["git", "push", "-u", "origin", branch], cwd=temp_dir, timeout=120)
            if pushed.returncode:
                return ExtensionResult(False, "validated", slug, f"could not push proposal: {(pushed.stderr or pushed.stdout).strip()[:500]}", guard=guard, branch=branch)
            body = f"## Munin self-extension\n\n**Rationale:** {manifest.rationale}\n\n**Targets:**\n```\n" + "\n".join(manifest.target_paths) + "\n```\n\nThis proposal passed Munin's structural guard and was explicitly approved by an operator. Review before merge."
            pr = self._run(["gh", "pr", "create", "--title", f"[munin-extension] {slug}", "--body", body, "--base", base, "--head", branch], cwd=temp_dir, timeout=60)
            if pr.returncode:
                return ExtensionResult(False, "validated", slug, f"branch pushed but PR creation failed: {(pr.stderr or pr.stdout).strip()[:500]}", guard=guard, branch=branch)
            pr_url = pr.stdout.strip().splitlines()[-1]
            manifest.status, manifest.pr_url, manifest.branch = "pr_opened", pr_url, branch
            manifest.save(self.directory)
            return ExtensionResult(True, "pr_opened", slug, "self-extension PR opened", guard=guard, pr_url=pr_url, branch=branch)
        finally:
            if added_worktree:
                self._run(["git", "worktree", "remove", "--force", str(temp_dir)], cwd=self.repo_root, timeout=60)
            shutil.rmtree(temp_dir, ignore_errors=True)

    @staticmethod
    def _run(args: list[str], *, cwd: Path, timeout: int, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, cwd=cwd, input=input_text, text=True, capture_output=True, timeout=timeout, check=False)

    @staticmethod
    def _compile_python_targets(worktree: Path, targets: list[str]) -> str:
        python_targets = [str(worktree / target) for target in targets if target.endswith(".py")]
        if not python_targets:
            return ""
        result = subprocess.run([os.sys.executable, "-m", "py_compile", *python_targets], capture_output=True, text=True, timeout=30, check=False)
        if result.returncode:
            return f"Python syntax check failed: {(result.stderr or result.stdout).strip()[:500]}"
        return ""
