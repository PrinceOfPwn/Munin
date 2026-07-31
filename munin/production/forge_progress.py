"""Standardised progress emission for forge-style subagents.

The dispatcher and any forge subagent (tool-forge, graph-forge, extension-forge)
must call :func:`emit_forge_stage` at each lifecycle transition so the UI can:

* Route the event into the correct ``ForgeFloatingChat`` window (via
  ``agent_name`` — the profile id of the subagent).
* Render the event as a stage chip (proposing, typechecking, sandbox…) with a
  live tail of stdout/stderr for ``forge_typecheck_output`` /
  ``forge_sandbox_output`` events.
* Surface an "Extend budget +5min" affordance when the initial budget is up
  but the agent is still making forward progress.

The set of ``stage`` values is closed on purpose — the frontend switch on
``metadata.stage`` needs to know every case.  Add new stages here first, then
teach the frontend to handle them.
"""

from __future__ import annotations

import json
from typing import Any

# Explicit closed set: catching a typo at emit time is cheaper than a mystery
# stage the UI silently ignores.  When adding a new stage: add here, then
# handle it in ``app/src/components/chat/ForgeFloatingChat.tsx``.
FORGE_STAGES = frozenset(
    {
        "forge_propose",
        "forge_diff_ready",
        "forge_typecheck_start",
        "forge_typecheck_output",
        "forge_typecheck_done",
        "forge_sandbox_start",
        "forge_sandbox_output",
        "forge_sandbox_done",
        "forge_awaiting_approval",
        "forge_budget_extension_available",
        "forge_completed",
        "forge_failed",
    }
)


def emit_forge_stage(
    store: Any,
    *,
    run_id: str,
    agent_name: str,
    stage: str,
    message: str = "",
    step: int = 0,
    persistence_enabled: bool = True,
    **extra: Any,
) -> dict[str, Any]:
    """Append a redacted reasoning event tagged as forge progress.

    Parameters
    ----------
    store:
        A ``ProductionStore`` instance (or a compatible test double).  The
        store may optionally accept a ``metadata`` keyword; if it does, this
        function passes the ``{stage, message, ...extra}`` payload through so
        the UI can differentiate stages without inspecting the free-form
        content string.  If ``append_reasoning_event`` does not yet accept
        ``metadata`` the extras are folded into the content prefix so the
        information is not lost.
    run_id, agent_name, stage, message, step:
        Standard reasoning-event coordinates.
    persistence_enabled:
        Same semantics as the base ``append_reasoning_event`` — when False the
        content is replaced by a redaction sentinel.
    extra:
        Free-form JSON-serialisable extras (e.g. ``diff_summary``,
        ``sandbox_elapsed_ms``, ``budget_extension_seconds``).
    """
    if stage not in FORGE_STAGES:
        raise ValueError(f"unknown forge stage: {stage!r}")
    metadata = {"stage": stage, "message": message, **extra}
    try:
        return store.append_reasoning_event(
            run_id=run_id,
            kind="operational_summary",
            content=message or stage,
            provider="forge",
            persistence_enabled=persistence_enabled,
            agent_name=agent_name,
            step=step,
            metadata=metadata,  # type: ignore[call-arg]
        )
    except TypeError:
        # Fallback for the base ``append_reasoning_event`` shipped in v3 that
        # doesn't yet accept ``metadata``.  Prefix the content with a JSON
        # payload the frontend can parse.
        prefix = json.dumps({"forge": metadata}, separators=(",", ":"))
        content = f"{prefix}\n{message or stage}"
        return store.append_reasoning_event(
            run_id=run_id,
            kind="operational_summary",
            content=content,
            provider="forge",
            persistence_enabled=persistence_enabled,
            agent_name=agent_name,
            step=step,
        )


__all__ = ["emit_forge_stage", "FORGE_STAGES"]
