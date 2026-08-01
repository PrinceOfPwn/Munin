"""Shared, conservative redaction before durable storage or rendering."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

_KEY_NAME = re.compile(r"(?:pass(?:word|wd)?|token|secret|api[_-]?key|authorization|cookie|session|private[_-]?key)", re.I)
_VALUE_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)([^\s;,]+)"),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~+/=-]{12,})"),
    re.compile(r"\b(sk|gsk|ghp|github_pat|nvapi|tvly|xox[baprs])[-_][A-Za-z0-9._-]{12,}\b", re.I),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)((?:api[_-]?key|password|passwd|secret|token)\s*[=:]\s*)([^\s;,]+)"),
)
_REDACTED = "[REDACTED]"


def redaction_disabled() -> bool:
    """Return whether an explicitly trusted operator disabled redaction.

    Redaction stays enabled by default.  A controlled lab can set
    ``MUNIN_REDACTION_MODE=off`` to inspect exact tool output in the GUI and
    replay archive; this is intentionally an environment-level switch rather
    than a browser-controlled flag.
    """
    return os.environ.get("MUNIN_REDACTION_MODE", "on").strip().lower() in {
        "off",
        "none",
        "disabled",
        "false",
        "0",
    }


def redact_text(value: str) -> str:
    """Redact known credential forms without pretending arbitrary text is safe."""
    text = str(value or "")
    if redaction_disabled():
        return text
    for pattern in _VALUE_PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda match: f"{match.group(1)}{_REDACTED}", text)
        else:
            text = pattern.sub(_REDACTED, text)
    return text


def redact_payload(value: Any) -> Any:
    """Return a redacted JSON-compatible copy of a potentially nested payload."""
    if redaction_disabled():
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _REDACTED if _KEY_NAME.search(str(key)) else redact_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, tuple):
        return [redact_payload(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
