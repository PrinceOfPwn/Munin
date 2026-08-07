# tags: [core, subagent, capabilities, orchestrator, runtime, BundledSkillLibrary, DeepAgentSkillBinding, SKILL.md, frontmatter-parser, read-only-skills, agent_skills, SkillsMiddleware, FilesystemBackend, package-discovery, executable-guidance, valravn]
"""Curated, read-only skills for native Deep Agents runtimes.

Skills are executable guidance, so Munin never scans arbitrary prompt folders.
It exposes the reviewed packages under ``munin/agent_skills`` and, when the
Valravn subtree is present, deterministically adapts its reviewed flat
``.claude/skills/*.md`` corpus into the package layout expected by Deep
Agents: ``<source>/<skill-name>/SKILL.md``.
"""
from __future__ import annotations

import re
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any


_VALRAVN_PREFIX = "valravn-"
_SAFE_SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_RENDER_CACHE: dict[tuple[object, ...], Path] = {}
_RENDER_CACHE_LOCK = Lock()


@dataclass(frozen=True)
class DeepAgentSkillBinding:
    """The native Deep Agents arguments needed to expose a skill library."""

    names: tuple[str, ...]
    sources: list[str]
    backend: Any
    permissions: list[Any]


class BundledSkillLibrary:
    """Resolve the reviewed skill packages distributed with Munin.

    Native skills are direct children of ``root``. Valravn's upstream skill
    corpus is intentionally flat, so it is adapted into namespaced packages at
    bind time. The resulting backend remains virtual and read-only.
    """

    def __init__(
        self,
        root: Path | None = None,
        valravn_root: Path | None = None,
    ) -> None:
        default_root = Path(__file__).resolve().parents[2] / "agent_skills"
        self.root = root or default_root

        # Custom roots are primarily used by tests and callers that want an
        # isolated library. Do not silently mix the repository Valravn corpus
        # into them unless the caller explicitly supplies ``valravn_root``.
        if valravn_root is not None:
            self.valravn_root: Path | None = valravn_root
        elif root is None:
            repo_root = Path(__file__).resolve().parents[3]
            self.valravn_root = repo_root / "valravn" / ".claude" / "skills"
        else:
            self.valravn_root = None

    @staticmethod
    def _frontmatter_name(path: Path) -> str | None:
        """Read the package identity from the small YAML frontmatter block."""
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

    @staticmethod
    def _has_closed_frontmatter(path: Path) -> bool:
        """Valravn legacy skills may omit ``name`` but must have a closed header.

        The flat filename is the canonical upstream identity. Several older
        Valravn playbooks intentionally only carry ``description``/``globs`` in
        their Claude frontmatter, so requiring a pre-existing ``name`` silently
        dropped valid skills. The renderer below always inserts/normalizes the
        Deep Agents package name.
        """
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return False
        if not lines or lines[0].strip() != "---":
            return False
        return any(line.strip() == "---" for line in lines[1:])

    def _native_packages(self) -> dict[str, Path]:
        if not self.root.is_dir():
            return {}
        packages: dict[str, Path] = {}
        for path in sorted(self.root.iterdir(), key=lambda item: item.name):
            skill_file = path / "SKILL.md"
            if (
                path.is_dir()
                and skill_file.is_file()
                and self._frontmatter_name(skill_file) == path.name
            ):
                packages[path.name] = path
        return packages

    def _valravn_packages(self) -> dict[str, Path]:
        """Return every valid flat Valravn skill under a collision-safe namespace.

        Filename stems are canonical. Frontmatter ``name`` is treated as stale
        source metadata because the adapter rewrites it to the namespaced Deep
        Agents identity anyway.
        """
        if self.valravn_root is None or not self.valravn_root.is_dir():
            return {}

        packages: dict[str, Path] = {}
        for path in sorted(self.valravn_root.glob("*.md"), key=lambda item: item.name):
            source_name = path.stem
            if not _SAFE_SKILL_NAME.fullmatch(source_name):
                continue
            if not self._has_closed_frontmatter(path):
                continue
            packages[f"{_VALRAVN_PREFIX}{source_name}"] = path
        return packages

    def validation_errors(self) -> tuple[str, ...]:
        """Return deterministic errors for malformed or colliding packages."""
        errors: list[str] = []

        if self.root.is_dir():
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
                        f"{path.name}: frontmatter name must equal folder name "
                        f"(got {declared!r})"
                    )

        native_names = set(self._native_packages())
        if self.valravn_root is not None and self.valravn_root.is_dir():
            seen: set[str] = set()
            for path in sorted(
                self.valravn_root.glob("*.md"), key=lambda item: item.name
            ):
                source_name = path.stem
                label = f"valravn/{path.name}"
                if not _SAFE_SKILL_NAME.fullmatch(source_name):
                    errors.append(f"{label}: unsafe filename-derived skill name {source_name!r}")
                    continue
                if not self._has_closed_frontmatter(path):
                    errors.append(f"{label}: missing or unclosed YAML frontmatter")
                    continue
                adapted = f"{_VALRAVN_PREFIX}{source_name}"
                if adapted in native_names:
                    errors.append(f"{label}: adapted name collides with {adapted}")
                if adapted in seen:
                    errors.append(f"{label}: duplicate adapted name {adapted}")
                seen.add(adapted)

        return tuple(errors)

    def available(self) -> tuple[str, ...]:
        """Return every reviewed native and adapted Valravn skill package."""
        native = self._native_packages()
        valravn = self._valravn_packages()
        return tuple(sorted(set(native) | (set(valravn) - set(native))))

    @staticmethod
    def _rewrite_valravn_skill(
        source: Path,
        adapted_name: str,
        sibling_names: dict[str, str],
    ) -> str:
        """Normalize one flat Claude skill into a Deep Agents package file."""
        text = source.read_text(encoding="utf-8")
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            raise ValueError(f"{source}: missing YAML frontmatter")

        closing = next(
            (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
            None,
        )
        if closing is None:
            raise ValueError(f"{source}: unclosed YAML frontmatter")

        rewritten_header: list[str] = []
        replaced_name = False
        for line in lines[1:closing]:
            if line.startswith("name:"):
                rewritten_header.append(f"name: {adapted_name}")
                replaced_name = True
            else:
                rewritten_header.append(line)
        if not replaced_name:
            rewritten_header.insert(0, f"name: {adapted_name}")

        body = "\n".join(lines[closing + 1 :])
        for original, adapted in sibling_names.items():
            # Valravn's flat files frequently point at sibling ``foo.md``
            # skills. In Munin those siblings are packages, so rewrite only
            # references that match a known source skill.
            body = re.sub(
                rf"(?<![\w/]){re.escape(original)}\.md\b",
                f"/{adapted}/SKILL.md",
                body,
            )

        rendered = "\n".join(
            ["---", *rewritten_header, "---", "", body.rstrip(), ""]
        )
        return rendered

    @staticmethod
    def _fingerprint(paths: Iterable[Path]) -> tuple[tuple[str, int, int], ...]:
        records: list[tuple[str, int, int]] = []
        for path in sorted({item.resolve() for item in paths}, key=str):
            stat = path.stat()
            records.append((str(path), stat.st_mtime_ns, stat.st_size))
        return tuple(records)

    def _rendered_root(self) -> Path:
        """Build or reuse the package view consumed by ``SkillsMiddleware``."""
        native = self._native_packages()
        valravn = {
            name: path
            for name, path in self._valravn_packages().items()
            if name not in native
        }
        if not valravn:
            return self.root

        tracked_files: list[Path] = list(valravn.values())
        for package in native.values():
            tracked_files.extend(path for path in package.rglob("*") if path.is_file())

        cache_key: tuple[object, ...] = (
            str(self.root.resolve()),
            str(self.valravn_root.resolve()) if self.valravn_root else "",
            self._fingerprint(tracked_files),
        )
        with _RENDER_CACHE_LOCK:
            cached = _RENDER_CACHE.get(cache_key)
            if cached is not None and cached.is_dir():
                return cached

            # The final component remains ``agent_skills`` so existing runtime
            # diagnostics and tests continue to identify the backend correctly.
            rendered = Path(tempfile.mkdtemp(prefix="munin-skills-")) / "agent_skills"
            rendered.mkdir()

            for name, source_dir in native.items():
                shutil.copytree(source_dir, rendered / name)

            sibling_names = {
                path.stem: name for name, path in valravn.items()
            }
            for name, source_file in valravn.items():
                package_dir = rendered / name
                package_dir.mkdir()
                (package_dir / "SKILL.md").write_text(
                    self._rewrite_valravn_skill(
                        source_file,
                        name,
                        sibling_names,
                    ),
                    encoding="utf-8",
                )

            _RENDER_CACHE[cache_key] = rendered
            return rendered

    def bind(self, requested: Iterable[str]) -> DeepAgentSkillBinding | None:
        """Create a read-only Deep Agents binding for explicit skill names."""
        names = tuple(
            dict.fromkeys(name.strip() for name in requested if name.strip())
        )
        if not names:
            return None

        unknown = sorted(set(names) - set(self.available()))
        if unknown:
            raise ValueError(f"Unknown bundled skill(s): {', '.join(unknown)}")

        rendered_root = self._rendered_root()
        missing = [
            name for name in names if not (rendered_root / name / "SKILL.md").is_file()
        ]
        if missing:
            raise RuntimeError(f"Bundled skill file missing: {', '.join(missing)}")

        # Imports stay lazy so metadata/registry code remains cheap to import.
        from deepagents.backends.filesystem import FilesystemBackend  # noqa: PLC0415
        from deepagents.middleware.filesystem import FilesystemPermission  # noqa: PLC0415

        return DeepAgentSkillBinding(
            names=names,
            sources=["/"],
            backend=FilesystemBackend(root_dir=rendered_root, virtual_mode=True),
            permissions=[
                FilesystemPermission(
                    operations=["write"],
                    paths=["/**"],
                    mode="deny",
                )
            ],
        )

    def bind_all(self) -> DeepAgentSkillBinding:
        """Bind every reviewed package for the primary Munin supervisor."""
        binding = self.bind(self.available())
        assert binding is not None
        return binding


def bundled_skill_library() -> BundledSkillLibrary:
    """Return the default reviewed Munin skill library."""
    return BundledSkillLibrary()
