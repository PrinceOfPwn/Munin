# tags: [core, runtime, shared-intel, coordination, memory, web-ui, persistence, redact_text, redact_payload, MUNIN_REDACTION_MODE, _VALUE_PATTERNS, _KEY_NAME, credential-redaction, bearer-tokens, api-key-filtering, sanitize_artifact_content, sanitize_sandboxed_html, PR-5B, sandboxed-html]
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

# ---------------------------------------------------------------------------
# PR-5B — sandboxed-html artifact guard.
#
# ``artifact/sandboxed-html`` artifacts are rendered in the Web GUI inside a
# hardened iframe (``sandbox="allow-scripts"`` + a strict CSP).  The sandbox
# and CSP neutralise execution at render time, but the durable artifact body
# must still never carry an *external* script import: a re-renderer, archive
# exporter or future surface outside the iframe would otherwise inherit an
# untrusted network load.  The pre-check below therefore REJECTS external
# ``<script src>`` (http/https/protocol-relative) at storage time and strips
# external ``src`` attributes from other elements (img/iframe/audio/video/
# source) before the body is written.  This is a security contract, so it is
# NOT bypassed by ``MUNIN_REDACTION_MODE=off`` — that switch only relaxes
# credential redaction, never the artifact guard.
# ---------------------------------------------------------------------------

SANDBOXED_HTML_MEDIA_TYPE = "artifact/sandboxed-html"
_EXTERNAL_SCRIPT_SRC = re.compile(
    r"<script\b[^>]*(\ssrc\s*=\s*)(['\"]?)(?:https?:)?//", re.IGNORECASE
)
_EXTERNAL_SRC_ATTR = re.compile(
    r"(\ssrc\s*=\s*)(['\"])(?:https?:)?//[^'\"]*\2", re.IGNORECASE
)


def precheck_sandboxed_html(content: str) -> None:
    """Raise ``ValueError`` when the payload imports an external script.

    Matches ``<script ... src="http(s)://...">`` and protocol-relative
    ``src="//host/..."`` forms, quoted or unquoted.  Inline ``<script>``
    bodies and ``data:`` sources pass — they are handled by the iframe CSP
    (``script-src 'unsafe-inline'``, ``img-src data:``).
    """
    if _EXTERNAL_SCRIPT_SRC.search(content):
        raise ValueError("External script source forbidden in sandboxed HTML artifact")


def sanitize_sandboxed_html(content: str) -> str:
    """Validate + sanitize a sandboxed-html artifact body.

    Rejects external ``<script src>`` (ValueError) and strips external
    ``src`` attributes from every other element, so the stored body can only
    reference same-document or ``data:`` resources.  Idempotent: re-running
    on its own output is a byte-identical no-op (safe for replay reads).
    """
    precheck_sandboxed_html(content)
    return _EXTERNAL_SRC_ATTR.sub(lambda match: match.group(1), content)


def sanitize_artifact_content(media_type: str, content: str) -> str:
    """Media-type-aware artifact content guard, applied before storage.

    Non-sandboxed media types pass through untouched (credential redaction
    still applies downstream via ``redact_text``); sandboxed-html bodies get
    the PR-5B pre-check + external-``src`` strip.
    """
    if str(media_type or "") == SANDBOXED_HTML_MEDIA_TYPE:
        return sanitize_sandboxed_html(str(content or ""))
    return content


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
