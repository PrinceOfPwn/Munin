# tags: [mcp, persistence, cicd, workflow, store, WikiGitSyncer, knowledge_sync_root, _copy_content, _write_index, _git_commit, _result, index.md, prepare, commit, push]
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from .config import Settings
from .utils import ensure_parent, utc_now_iso


class WikiGitSyncer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run(self, *, run_id: str, mode: str, destination: str) -> dict[str, Any]:
        source_root = self.settings.workspace_root
        target_root = self.settings.knowledge_sync_root / run_id
        target_root.mkdir(parents=True, exist_ok=True)
        copied = self._copy_content(source_root, target_root)
        index_path = self._write_index(run_id, destination, target_root, copied)
        if mode == "prepare":
            return self._result(run_id, mode, destination, target_root, index_path, copied, True, "")
        if mode in {"commit", "push"}:
            commit_message = f"offx sync {run_id} {utc_now_iso()}"
            repo_ok, detail = self._git_commit(target_root, commit_message, push=(mode == "push"))
            return self._result(run_id, mode, destination, target_root, index_path, copied, repo_ok, detail)
        return self._result(run_id, mode, destination, target_root, index_path, copied, False, f"unsupported mode: {mode}")

    def _copy_content(self, source_root: Path, target_root: Path) -> int:
        copied = 0
        include = ["runs", "reports", "evidence", "intel", "templates", "prompts", "specs"]
        for name in include:
            src = source_root / name
            if not src.exists():
                continue
            dst = target_root / name
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            copied += sum(1 for p in dst.rglob("*") if p.is_file())
        return copied

    def _write_index(self, run_id: str, destination: str, target_root: Path, copied: int) -> Path:
        index_path = target_root / "index.md"
        lines = [
            f"# OFFX Knowledge Sync {run_id}",
            "",
            f"- destination: `{destination}`",
            f"- generated_at_utc: `{utc_now_iso()}`",
            f"- copied_files: `{copied}`",
            "",
            "## Included Trees",
            "",
            "- runs/",
            "- reports/",
            "- evidence/",
            "- intel/",
            "- templates/",
            "- prompts/",
            "- specs/",
        ]
        ensure_parent(index_path)
        index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return index_path

    def _git_commit(self, repo_root: Path, message: str, *, push: bool) -> tuple[bool, str]:
        if not (repo_root / ".git").exists():
            return False, "target is not a git repository"
        add_cmd = ["git", "-C", str(repo_root), "add", "."]
        commit_cmd = ["git", "-C", str(repo_root), "commit", "-m", message]
        subprocess.run(add_cmd, check=False, capture_output=True, text=True, timeout=60)
        commit = subprocess.run(commit_cmd, check=False, capture_output=True, text=True, timeout=60)
        if commit.returncode not in {0, 1}:
            return False, commit.stderr.strip() or commit.stdout.strip()
        if push:
            pushed = subprocess.run(["git", "-C", str(repo_root), "push"], check=False, capture_output=True, text=True, timeout=120)
            if pushed.returncode != 0:
                return False, pushed.stderr.strip() or pushed.stdout.strip()
            return True, "commit and push succeeded"
        return True, "prepare and commit succeeded"

    def _result(
        self,
        run_id: str,
        mode: str,
        destination: str,
        target_root: Path,
        index_path: Path,
        copied: int,
        ok: bool,
        detail: str,
    ) -> dict[str, Any]:
        return {
            "ok": ok,
            "tool": "wiki_git_syncer",
            "mode": "sync",
            "summary": f"{mode} {'completed' if ok else 'failed'} for {run_id}",
            "data": {
                "run_id": run_id,
                "mode": mode,
                "destination": destination,
                "target_root": str(target_root),
                "index_path": str(index_path),
                "copied_files": copied,
                "detail": detail,
            },
            "artifacts": [str(index_path)],
            "error": None if ok else {"code": "sync_failed", "message": detail},
        }
