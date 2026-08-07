# tags: [core, autonomy, compaction, cti, deepagents, summarization, context-management, summary_prompt, soul, red-team, campaign, checkpoint, ioc, provenance]
"""CTI-aware compaction prompt composer for the Deep Agents SummarizationMiddleware.

The framework default ``DEEPAGENTS_DEFAULT_SUMMARY_PROMPT`` is a load-bearing
contract: it splices media-reference information just before the ``{messages}``
placeholder that the runtime fills with the conversation to summarize.  We must
NOT replace that prompt wholesale or we lose the deepagents/langchain internal
contract (media handling, message insertion, argument truncation hooks).

Instead we *insert* a Munin-specific ``<cti_compaction_rules>`` block right
before the ``<messages>`` sentinel so the summarizer treats each compaction as
a durable red-team investigation checkpoint.  The rules live in a plain-text
file (``cti_compaction_rules.txt`` next to this module) so an operator can
tune the compaction contract per-operation or per-campaign WITHOUT editing
Python — re-read from disk on every supervisor build (not hot-reloaded mid
run; a new conversation picks up the new rules).

Stability guarantees:
- If ``DEEPAGENTS_DEFAULT_SUMMARY_PROMPT`` cannot be imported (older
  deepagents, missing middleware), we return ``None`` and the supervisor
  falls back to the framework default rather than breaking the build.
- If the ``\\n<messages>\\n`` splice-point is not found in the default prompt
  (deepagents changed the template), we do an append-only fallback so the
  rules still take effect, just after the default body.  We never silently
  drop the rules.
- The ``{messages}`` placeholder is preserved verbatim in every branch —
  the runtime requires it.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_RULES_FILE = Path(__file__).with_name("cti_compaction_rules.txt")
_MESSAGES_SENTINEL = "\n<messages>\n"


def _load_rules_text() -> str:
    """Read the CTI compaction rules block from the plain-text file.

    Returns the raw file contents (including the wrapping
    ``<cti_compaction_rules>`` element).  On any read error returns an empty
    string so the composer degrades to the framework default prompt rather
    than crashing the supervisor build.
    """
    try:
        return _RULES_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        logger.debug(
            "compaction: could not read CTI rules file %s; using framework default",
            _RULES_FILE,
            exc_info=True,
        )
        return ""


def compose_cti_summary_prompt() -> str | None:
    """Build the Munin CTI compaction prompt by inserting the operator's
    ``<cti_compaction_rules>`` block into ``DEEPAGENTS_DEFAULT_SUMMARY_PROMPT``
    just before the ``<messages>`` sentinel.

    Returns:
        The composed prompt string with ``{messages}`` preserved, or ``None``
        when the framework default cannot be imported (older deepagents /
        missing middleware) so the supervisor falls back gracefully.
    """
    try:
        from deepagents.middleware.summarization import (  # noqa: PLC0415
            DEEPAGENTS_DEFAULT_SUMMARY_PROMPT,
        )
    except Exception:  # noqa: BLE001
        logger.debug(
            "compaction: DEEPAGENTS_DEFAULT_SUMMARY_PROMPT unavailable; "
            "framework default summary prompt will be used",
            exc_info=True,
        )
        return None

    rules = _load_rules_text()
    if not rules:
        # File missing/empty — honour the operator's "no surprises" rule:
        # return None so the supervisor keeps the framework default rather
        # than silently no-op'ing the customization.
        return None

    base: str = DEEPAGENTS_DEFAULT_SUMMARY_PROMPT

    # Preferred path: splice the rules right before <messages> so they sit
    # inside the default body (after the media-reference block) and the
    # runtime's {messages} substitution still works.
    if _MESSAGES_SENTINEL in base:
        return base.replace(_MESSAGES_SENTINEL, f"\n{rules}\n{_MESSAGES_SENTINEL}", 1)

    # Fallback: the template changed and the sentinel moved/disappeared.
    # Append the rules at the end so they still take effect; we keep any
    # existing {messages} placeholder untouched.  This is safer than
    # guessing a new splice-point.
    logger.warning(
        "compaction: <messages> sentinel not found in "
        "DEEPAGENTS_DEFAULT_SUMMARY_PROMPT (template changed?); "
        "appending CTI rules at end as a fallback",
    )
    return f"{base}\n\n{rules}\n"


def _self_check() -> int:
    """Tiny CLI entry point for operators/devs to dry-run the composer and
    inspect the composed prompt without booting the supervisor.

    Run with: ``python -m munin.core.autonomy.compaction``  (or
    ``python munin/core/autonomy/compaction.py``).
    Prints the composed prompt to stdout and exits 0 on success, 1 if the
    framework default is unavailable.
    """
    prompt = compose_cti_summary_prompt()
    if prompt is None:
        print("[compaction] DEEPAGENTS_DEFAULT_SUMMARY_PROMPT unavailable")
        return 1
    print(prompt)
    has_messages = "{messages}" in prompt
    has_rules = "<cti_compaction_rules>" in prompt
    print(
        f"\n[compaction] composed OK: has_messages={has_messages} "
        f"has_rules={has_rules} len={len(prompt)}",
        file=sys.stderr,
    )
    return 0 if (has_messages and has_rules) else 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_self_check())


__all__: list[str] = ["compose_cti_summary_prompt"]
