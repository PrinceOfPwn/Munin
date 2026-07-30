"""Versioned JSON manifests for self-forged Munin agent graphs."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from .config import Settings, safe_slug
from .shared_state import SharedStateStore

logger = logging.getLogger("munin.graph_persist")


def persist_graph_manifest(
    settings: Settings,
    graph: dict[str, Any],
    *,
    queue_git: bool = True,
) -> Path:
    """Atomically export a graph spec and optionally queue a runner commit."""
    name = str(graph.get("name", "")).strip()
    purpose = str(graph.get("purpose", "")).strip()
    if not name or not purpose:
        raise ValueError("graph manifest requires name and purpose")
    payload = {
        "schema_version": 1,
        "name": name,
        "purpose": purpose,
        "system_prompt": str(graph.get("system_prompt", "")),
        "tool_whitelist": list(graph.get("tool_whitelist") or []),
        "reset_policy": str(graph.get("reset_policy", "on_reset")),
        "created_by_agent": str(graph.get("created_by_agent", "munin")),
        "active": bool(graph.get("active", True)),
    }
    name_digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
    path = settings.generated_graphs_dir / f"{safe_slug([name])}-{name_digest}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)

    if queue_git:
        try:
            from . import git_persist  # noqa: PLC0415

            git_persist.commit_forged_graph(
                manifest_path=str(path),
                name=name,
                purpose=purpose,
            )
        except Exception as exc:  # pragma: no cover - best-effort runner feature
            logger.warning("could not queue graph manifest commit for %s: %s", name, exc)
    return path


def rehydrate_graph_manifests(
    state: SharedStateStore,
    settings: Settings,
) -> dict[str, Any]:
    """Load committed graph manifests into the active persistence backend."""
    loaded = 0
    errors: list[dict[str, str]] = []
    for path in sorted(settings.generated_graphs_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not payload.get("active", True):
                continue
            state.graph_register(
                name=str(payload["name"]),
                purpose=str(payload["purpose"]),
                system_prompt=str(payload.get("system_prompt", "")),
                tool_whitelist=[str(item) for item in payload.get("tool_whitelist", [])],
                reset_policy=str(payload.get("reset_policy", "on_reset")),
                created_by_agent=str(payload.get("created_by_agent", "manifest")),
            )
            loaded += 1
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append({"path": str(path), "error": str(exc)})
            logger.warning("invalid graph manifest %s: %s", path, exc)
    return {"loaded": loaded, "errors": errors}


def purge_resettable_graph_manifests(settings: Settings) -> dict[str, Any]:
    """Delete manifests whose graph contract says they should disappear on reset."""
    removed = 0
    errors: list[dict[str, str]] = []
    for path in sorted(settings.generated_graphs_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if str(payload.get("reset_policy", "on_reset")) != "on_reset":
                continue
            path.unlink()
            removed += 1
        except (OSError, ValueError, TypeError) as exc:
            errors.append({"path": str(path), "error": str(exc)})
            logger.warning("could not purge resettable graph manifest %s: %s", path, exc)
    return {"removed": removed, "errors": errors}
