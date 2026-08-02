# tags: [core, subagent, capabilities, orchestrator, runtime, BundledSkillLibrary, DeepAgentSkillBinding, SKILL.md, frontmatter-parser, read-only-skills, agent_skills, SkillsMiddleware, FilesystemBackend, package-discovery, executable-guidance]
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

    @staticmethod
    def _frontmatter_name(path: Path) -> str | None:
        """Read the package identity from the small YAML frontmatter block.

        Skill discovery must not rely on a second name registry: a directory
        and its ``SKILL.md`` are one package.  We only need the scalar
        ``name`` field here, so a tiny parser is safer than making discovery
        depend on an optional YAML package.
        """
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        if not lines or lines[0].strip() != "---":
            return None
        declared: str | None = None
        closed = False
        for line in lines[1:]:
            if line.strip() == "---":
                closed = True
                break
            if line.startswith("name:"):
                value = line[len("name:") :].strip()
                declared = value.strip("\"'") or None
        return declared if closed else None

    def validation_errors(self) -> tuple[str, ...]:
        """Return deterministic errors for malformed or misnamed packages."""
        if not self.root.is_dir():
            return ()
        errors: list[str] = []
        for path in sorted(self.root.iterdir(), key=lambda item: item.name):
            if not path.is_dir():
                continue
            skill_file = path / "SKILL.md"
            if not skill_file.is_file():
                errors.append(f"{path.name}: missing SKILL.md")
                continue
            declared = self._frontmatter_name(skill_file)
            if declared != path.name:
                errors.append(
                    f"{path.name}: frontmatter name must equal folder name (got {declared!r})"
                )
        return tuple(errors)

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
                if path.is_dir()
                and (path / "SKILL.md").is_file()
                and self._frontmatter_name(path / "SKILL.md") == path.name
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
