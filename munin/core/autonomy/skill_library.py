"""Curated, read-only skills for native Deep Agents runtimes.

This module intentionally does *not* scan arbitrary prompt folders.  Skills
are executable guidance, so making an unreviewed corpus visible to an
autonomous agent is a capability grant.  Munin ships a small, versioned set of
reviewed skill packages and uses Deep Agents' native ``skills`` argument plus
``FilesystemBackend`` to expose them on demand.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DeepAgentSkillBinding:
    """The native Deep Agents arguments needed to expose a skill library."""

    names: tuple[str, ...]
    sources: list[str]
    backend: Any
    permissions: list[Any]


class BundledSkillLibrary:
    """Resolve the reviewed skill packages distributed with Munin.

    Every skill is a direct child of ``root`` because ``SkillsMiddleware``
    discovers ``<source>/<skill-name>/SKILL.md`` one level at a time.  The
    backend is virtual and read-only: the agent can read a selected skill, but
    cannot edit the library or escape its root directory.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(__file__).resolve().parents[2] / "agent_skills"

    def available(self) -> tuple[str, ...]:
        """Return direct-child skill packages shipped with the application.

        A reviewed skill is added by committing
        ``munin/agent_skills/<skill-name>/SKILL.md``.  Keeping discovery to one
        directory level matches ``SkillsMiddleware`` and avoids recursively
        mounting arbitrary folders as agent instructions.
        """
        if not self.root.is_dir():
            return ()
        return tuple(
            sorted(
                path.name
                for path in self.root.iterdir()
                if path.is_dir() and (path / "SKILL.md").is_file()
            )
        )

    def bind(self, requested: Iterable[str]) -> DeepAgentSkillBinding | None:
        """Create a read-only Deep Agents binding for explicit skill names.

        ``None`` means that the caller deliberately requested no skills.  An
        unknown name is rejected instead of being treated as a filesystem path,
        preventing a generated agent from mounting arbitrary local content.
        """
        names = tuple(dict.fromkeys(name.strip() for name in requested if name.strip()))
        if not names:
            return None

        unknown = sorted(set(names) - set(self.available()))
        if unknown:
            raise ValueError(f"Unknown bundled skill(s): {', '.join(unknown)}")

        missing = [name for name in names if not (self.root / name / "SKILL.md").is_file()]
        if missing:
            raise RuntimeError(f"Bundled skill file missing: {', '.join(missing)}")

        # Imports stay lazy so metadata/registry code remains cheap to import.
        from deepagents.backends.filesystem import FilesystemBackend  # noqa: PLC0415
        from deepagents.middleware.filesystem import FilesystemPermission  # noqa: PLC0415

        return DeepAgentSkillBinding(
            names=names,
            sources=["/"],
            backend=FilesystemBackend(root_dir=self.root, virtual_mode=True),
            permissions=[FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")],
        )

    def bind_all(self) -> DeepAgentSkillBinding:
        """Bind every reviewed package for the primary Munin supervisor."""
        binding = self.bind(self.available())
        assert binding is not None
        return binding


def bundled_skill_library() -> BundledSkillLibrary:
    """Return the default reviewed Munin skill library."""
    return BundledSkillLibrary()
