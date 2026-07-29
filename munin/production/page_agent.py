"""Allowlisted, audited Page Agent action planning.

The browser implementation may assist navigation, but it receives a typed plan
from this module and cannot make raw privileged API calls or treat DOM content
as instructions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ALLOWED_ACTIONS = frozenset({"navigate", "find", "open_run", "set_filter", "prepare_form", "explain_ui"})
SENSITIVE_ACTIONS = frozenset({"prepare_form"})


@dataclass(frozen=True)
class PageAction:
    action: str
    target: str
    parameters: dict[str, Any]
    requires_confirmation: bool


def validate_page_action(*, role: str, feature_enabled: bool, action: str, target: str, parameters: dict[str, Any] | None = None) -> PageAction:
    if not feature_enabled:
        raise PermissionError("Page Agent is disabled by policy")
    if role not in {"admin", "operator", "viewer"}:
        raise PermissionError("unknown actor role")
    if action not in ALLOWED_ACTIONS or not target.startswith("/") or target.startswith("//"):
        raise PermissionError("Page Agent action is outside the UI allowlist")
    if role == "viewer" and action in SENSITIVE_ACTIONS:
        raise PermissionError("viewer cannot prepare a mutable form")
    return PageAction(action=action, target=target, parameters=parameters or {}, requires_confirmation=action in SENSITIVE_ACTIONS)
