# tags: [tool-forge, capabilities, orchestrator, workflow, supervisor, ExtensionManifest, ALLOWED_ROOTS, ALLOWED_KINDS, ALLOWED_STATUSES, _FORBIDDEN_PATHS, is_allowed_target, normalise_path, list_manifests, unified-diff-validation, slug-validation]
"""Durable, reviewable proposals for changes to Munin itself."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ALLOWED_KINDS = frozenset({"python", "frontend", "docs", "soul"})
ALLOWED_ROOTS = (
    "munin/mcp/tools",
    "munin/forge",
    "munin/rag",
    "munin/integrations",
    "munin/subagents",
    "app/src",
    "app/public",
    "docs",
    "soul",
)
_FORBIDDEN_PATHS = frozenset({
    "munin/mcp/audit.py",
    "munin/mcp/config.py",
    "munin/mcp/git_persist.py",
    "munin/mcp/main.py",
    "munin/mcp/opsec.py",
    "munin/mcp/persistence.py",
    "munin/mcp/registry.py",
    "munin/mcp/shared_state.py",
    "munin/forge/extension_manifest.py",
})
ALLOWED_STATUSES = frozenset({"proposed", "validated", "pr_opened", "rejected", "superseded"})


def normalise_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def is_allowed_target(path: str) -> bool:
    normalized = normalise_path(path)
    if normalized in _FORBIDDEN_PATHS or normalized.startswith(".github/"):
        return False
    return any(normalized == root or normalized.startswith(f"{root}/") for root in ALLOWED_ROOTS)


@dataclass
class ExtensionManifest:
    """A proposal is inert until an operator explicitly opens its PR."""

    slug: str
    kind: str
    rationale: str
    target_paths: list[str]
    diff: str
    tests: list[str] = field(default_factory=list)
    proposed_by: str = "munin"
    proposed_at: str = ""
    pr_url: str = ""
    branch: str = ""
    status: str = "proposed"

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,79}", self.slug):
            raise ValueError("slug must be 3-80 lowercase letters, numbers, '-' or '_'")
        if self.kind not in ALLOWED_KINDS:
            raise ValueError(f"kind must be one of {sorted(ALLOWED_KINDS)}")
        if self.status not in ALLOWED_STATUSES:
            raise ValueError(f"invalid status {self.status!r}")
        if not self.rationale.strip():
            raise ValueError("rationale is required")
        if not self.target_paths or len(self.target_paths) > 3:
            raise ValueError("a proposal must target between 1 and 3 files")
        normalised = [normalise_path(path) for path in self.target_paths]
        if len(set(normalised)) != len(normalised):
            raise ValueError("target_paths contains duplicates")
        if any(not is_allowed_target(path) for path in normalised):
            raise ValueError("one or more target paths are outside the self-extension allowlist")
        self.target_paths = normalised
        if not self.diff.startswith("diff --git ") or len(self.diff) > 80_000:
            raise ValueError("diff must be a git unified diff no larger than 80KB")
        if not self.proposed_at:
            self.proposed_at = datetime.now(UTC).replace(microsecond=0).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.slug}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return path

    @classmethod
    def from_file(cls, path: Path) -> "ExtensionManifest":
        return cls(**json.loads(path.read_text(encoding="utf-8")))


def list_manifests(directory: Path) -> list[ExtensionManifest]:
    manifests: list[ExtensionManifest] = []
    for path in sorted(directory.glob("*.json")) if directory.exists() else []:
        try:
            manifests.append(ExtensionManifest.from_file(path))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return manifests
