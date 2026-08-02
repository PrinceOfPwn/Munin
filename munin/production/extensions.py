# tags: [mcp-tool, capabilities, registry, web-ui, component, runtime, core, ExtensionManifest, parse_manifest, VALID_SLOTS, VALID_PERMISSIONS, flight-deck-widgets, manifest-validation, isolated-extensions, feature-flags]
"""Typed extension manifest validation for isolated Flight Deck widgets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

VALID_SLOTS = frozenset({"command_center", "conversation_inspector", "run_timeline", "settings"})
VALID_PERMISSIONS = frozenset({"read:conversation", "read:run", "read:artifact", "propose:diff"})


@dataclass(frozen=True)
class ExtensionManifest:
    id: str
    version: str
    slots: tuple[str, ...]
    permissions: tuple[str, ...]
    feature_flag: str
    entrypoint: str


def parse_manifest(value: dict[str, Any]) -> ExtensionManifest:
    identifier = str(value.get("id", ""))
    slots = tuple(str(slot) for slot in value.get("slots", []))
    permissions = tuple(str(permission) for permission in value.get("permissions", []))
    if not identifier.replace("-", "").replace("_", "").isalnum() or not slots or not set(slots) <= VALID_SLOTS or not set(permissions) <= VALID_PERMISSIONS:
        raise ValueError("invalid extension manifest")
    entrypoint = str(value.get("entrypoint", ""))
    if not entrypoint.startswith("/extensions/"):
        raise ValueError("extension entrypoint must be an isolated extension path")
    return ExtensionManifest(id=identifier, version=str(value.get("version", "0")), slots=slots, permissions=permissions, feature_flag=str(value.get("feature_flag", "")), entrypoint=entrypoint)
