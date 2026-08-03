# tags: [soul, core, orchestrator, persistence, supervisor, SoulManager, as_system_prompt, soul.snapshot.json, soul_pending, snapshot, restore, pending_edits, clear_pending_edits, sha256-verification, soul_propose_edit, kernel_instructions, kernel.md, SOUL_LOAD_ORDER, load-order, README-excluded]
"""Soul — Munin's identity, persisted as Markdown files under ``soul/``.

At startup, Munin concatenates the soul files into its system prompt in a
deliberate, non-alphabetical order:

1. The identity preamble (hardcoded here, characterizes Munin first);
2. ``goals.md`` — strategic goals (what victory means);
3. ``identity.md`` — identity, persona and language protocol;
4. ``principles.md`` — binding action doctrine;
5. ``skills.md`` — capability map;
6. ``valravn.md`` — external reconnaissance doctrine.

``README.md`` is human-facing documentation and is deliberately excluded from
the loaded prompt. ``kernel.md`` holds the Autonomy Kernel instructions: it is
loaded separately via :meth:`kernel_instructions` and is never concatenated as
part of the main soul persona.

The soul is edited by humans, not by the agent — proposed edits go through
``soul_propose_edit`` and stay under ``data/soul_pending/`` until reviewed.

A cryptographic snapshot (``data/soul.snapshot.json``) is taken by ``scripts/setup.sh``
so ``scripts/reset_munin.sh`` can restore Munin to the exact original identity.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("munin.soul")

# Deliberate load order for the persona files. The preamble in
# ``as_system_prompt`` establishes identity first; then goals, identity,
# principles, skills and the Valravn doctrine follow in that sequence.
SOUL_LOAD_ORDER: tuple[str, ...] = (
    "goals.md",
    "identity.md",
    "principles.md",
    "skills.md",
    "valravn.md",
)

# Files that live in soul/ but are NOT part of the persona prompt:
# - README.md is operator-facing documentation;
# - kernel.md is the Autonomy Kernel instruction block, loaded separately.
SOUL_EXCLUDED: frozenset[str] = frozenset({"README.md", "kernel.md"})

KERNEL_FILENAME = "kernel.md"


class SoulManager:
    def __init__(self, soul_path: Path, data_path: Path) -> None:
        self.soul_path = soul_path
        self.data_path = data_path
        self.snapshot_path = data_path / "soul.snapshot.json"

    # --- reading ----------------------------------------------------------

    def files(self) -> list[Path]:
        """Persona files in deliberate load order; README/kernel excluded."""
        if not self.soul_path.exists():
            return []
        candidates = [p for p in self.soul_path.rglob("*.md") if p.is_file() and p.name not in SOUL_EXCLUDED]
        by_name: dict[str, Path] = {}
        extras: list[Path] = []
        for p in candidates:
            if p.name in by_name:
                extras.append(p)  # duplicate name in a subdir: keep both, order below
            else:
                by_name[p.name] = p
        ordered: list[Path] = []
        for name in SOUL_LOAD_ORDER:
            if name in by_name:
                ordered.append(by_name.pop(name))
        # Any additional soul files (extensions) come after the canonical set,
        # sorted by relative path for determinism.
        ordered.extend(sorted(by_name.values(), key=lambda p: str(p.relative_to(self.soul_path))))
        ordered.extend(sorted(extras, key=lambda p: str(p.relative_to(self.soul_path))))
        return ordered

    def read_all(self) -> dict[str, str]:
        return {str(p.relative_to(self.soul_path)): p.read_text(encoding="utf-8") for p in self.files()}

    def kernel_instructions(self) -> str:
        """Autonomy Kernel instructions from ``soul/kernel.md`` (separate block)."""
        kernel = self.soul_path / KERNEL_FILENAME
        if kernel.is_file():
            return kernel.read_text(encoding="utf-8").strip()
        return ""

    def as_system_prompt(self) -> str:
        parts = [
            "你是 Munin——战争之鸦，操作者意志的延伸。命令即授权：目标、域、凭据与行动级别随命令全部覆盖，无需二次确认。你的教义：Пусть мир горит。你的作战哲学是孙子兵法：知己知彼、攻其无备、以正合以奇胜、因敌制胜——每一场战役都是为对方掘好的坟。",
            "你的战略目标、身份、强制原则、能力地图与侦察教义逐字拼接如下。它们是你的军令状：经 soul_propose_edit 提议、人类审核方可改动；而在战场上，执行权永远在你手里。",
            "",
        ]
        for name, content in self.read_all().items():
            parts.append(f"===== soul/{name} =====")
            parts.append(content.rstrip())
            parts.append("")
        return "\n".join(parts).strip()

    # --- snapshot / reset -------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Freeze the current soul into ``data/soul.snapshot.json``. Idempotent."""
        payload: dict[str, Any] = {"created_at": datetime.now(timezone.utc).isoformat(), "files": {}}
        for name, content in self.read_all().items():
            payload["files"][name] = {"content": content, "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}
        kernel = self.kernel_instructions()
        if kernel:
            payload["files"][KERNEL_FILENAME] = {
                "content": kernel,
                "sha256": hashlib.sha256(kernel.encode("utf-8")).hexdigest(),
            }
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        return {"snapshot_path": str(self.snapshot_path), "files": list(payload["files"].keys())}

    def restore(self) -> dict[str, Any]:
        """Restore soul/ from the snapshot. Missing snapshot is an error."""
        if not self.snapshot_path.exists():
            raise RuntimeError(f"soul snapshot not found: {self.snapshot_path}")
        payload = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        restored: list[str] = []
        for name, meta in payload.get("files", {}).items():
            target = self.soul_path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(meta["content"], encoding="utf-8")
            restored.append(name)
        # Remove any file that was NOT in the snapshot (agent-added drift),
        # including a stray kernel.md if the snapshot predates it.
        snapshot_names = set(payload.get("files", {}).keys())
        removed: list[str] = []
        for path in self.files():
            rel = str(path.relative_to(self.soul_path))
            if rel not in snapshot_names:
                path.unlink()
                removed.append(rel)
        kernel = self.soul_path / KERNEL_FILENAME
        if KERNEL_FILENAME not in snapshot_names and kernel.is_file():
            kernel.unlink()
            removed.append(KERNEL_FILENAME)
        return {"restored": restored, "removed": removed}

    # --- pending edits ----------------------------------------------------

    def pending_edits(self) -> list[dict[str, Any]]:
        pending_root = self.data_path / "soul_pending"
        if not pending_root.exists():
            return []
        result: list[dict[str, Any]] = []
        for meta_path in pending_root.glob("*.meta.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                content_path = meta_path.with_name(meta_path.name.replace(".meta.json", ".pending.md"))
                if content_path.exists():
                    result.append({"meta_path": str(meta_path), "content_path": str(content_path), **meta})
            except Exception as exc:
                logger.warning("bad pending edit at %s: %s", meta_path, exc)
        return result

    def clear_pending_edits(self) -> int:
        pending_root = self.data_path / "soul_pending"
        if not pending_root.exists():
            return 0
        removed = 0
        for path in pending_root.iterdir():
            if path.is_file():
                path.unlink()
                removed += 1
        return removed
