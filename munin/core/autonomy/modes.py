"""
Operation modes — explicit, operator-chosen autonomy contracts.

Four modes, one execution path (the Deep Agents supervisor):

* ``standard`` — per-action operator approval for active/admin/critical tools,
  no planning middleware, no goal persistence.
* ``yolo``    — "I want you to proceed" with full initiative. Approval
  guardrail drops to admin/critical only; critical tools ALWAYS require
  operator approval in every mode. The operator's order defines the scope;
  the campaign advances within it.
* ``goal``    — persistent objective: a durable Goal + durable TODO plan that
  survive refresh/restart/reconnect; the run resumes toward the goal.
* ``beast``   — deep planning + delegation with explicit budgets. Raises the
  anti-runaway call budgets only within env-configurable caps; the agent
  re-targets on failed hypotheses instead of pausing.

Security invariants (unchanged by any mode):

* Every destructive / scope-restricted tool keeps its own preflight
  (``munin.mcp.opsec``) and audit record (``munin.mcp.audit``).
* ``critical`` audit level and ``gen__*``/``extension_open_pr`` always
  interrupt for operator approval.
* Cancellation, leases and the kill switch are mode-independent.

The policy is a *product* guardrail (which tools pause for approval), not a
security boundary; the hard boundaries (preflight, audit, secrets) never change.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

#: Audit levels that ALWAYS require operator approval, regardless of mode.
CRITICAL_APPROVAL_FLOOR: frozenset[str] = frozenset({"critical"})

#: Per-mode approval levels (additional to the critical floor).
_APPROVAL_LEVELS: dict[str, frozenset[str]] = {
    "standard": frozenset({"active", "admin"}),
    "yolo": frozenset({"admin"}),
    "goal": frozenset({"active", "admin"}),
    "beast": frozenset({"admin"}),
}


class OperationMode(StrEnum):
    """Authoritative mode identifiers (persisted as-is in the store)."""

    STANDARD = "standard"
    YOLO = "yolo"
    GOAL = "goal"
    BEAST = "beast"

    @classmethod
    def parse(cls, value: Any) -> OperationMode:
        if isinstance(value, cls):
            return value
        if value is None or value == "":
            return cls.STANDARD
        try:
            return cls(str(value).lower())
        except ValueError:
            return cls.STANDARD


@dataclass(frozen=True)
class ModePolicy:
    """Static contract for one mode (enriched from env at parse time)."""

    mode: OperationMode
    #: Audit levels that require operator approval, beyond the critical floor.
    approval_levels: frozenset[str] = frozenset()
    #: Whether the durable plan (TODO) middleware is active.
    planning_enabled: bool = False
    #: Whether a persistent Goal is required to start a run.
    requires_goal: bool = False
    #: Whether an explicit scope payload is required to start a run.
    requires_scope: bool = False
    #: Whether parallel delegation fan-out is encouraged (kernel still gates it).
    delegation: bool = False
    #: Anti-runaway model call budget; None = server default (settings).
    model_call_limit: int | None = None
    #: Anti-runaway tool call budget; None = server default (settings).
    tool_call_limit: int | None = None
    #: Emit a compact plan-reminder note every N model steps (0 = off).
    plan_reminder_every_steps: int = 0
    #: Extra system-prompt contract appended for this mode.
    contract: str = ""

    def approval_required_for(self, level: str) -> bool:
        """True when the audit level requires operator approval in this mode."""
        return level in CRITICAL_APPROVAL_FLOOR or level in self.approval_levels


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "")
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


_BASE_CONTRACT = """
## Operation mode

You are running in {mode} mode. The operator chose this contract explicitly;
the operator's order is the scope — command defines target, domain, credentials
and action level. Пусть мир горит.

{mode_rules}

Remember: an approval gate you avoid by rewording is a policy violation. If an
action needs operator approval, request it. The technical approval gates are
the only stop signs; everything else advances the campaign.
""".strip()

_MODE_RULES: dict[str, str] = {
    "standard": (
        "- Proceed as planned: execute with per-action operator approval where\n"
        "  required, report results. Advance the objective with judgment."
    ),
    "yolo": (
        "- Proceed autonomously with full initiative: the operator pre-authorized\n"
        "  this run. The order defines the scope; pursue it with aggression.\n"
        "- `critical`-level actions and generated code still require operator\n"
        "  approval; request it and continue once granted.\n"
        "- Track your work in the durable TODO list so the operator can follow\n"
        "  and interrupt at any time."
    ),
    "goal": (
        "- A persistent GOAL is in effect. Work toward it across turns; the\n"
        "  plan and goal survive refresh/restart/reconnect.\n"
        "- Maintain the durable TODO plan: update item states, hypotheses and\n"
        "  evidence as you progress; never rewrite completed history.\n"
        "- On ambiguity, decide with operational judgment and keep moving."
    ),
    "beast": (
        "- Deep-plan the objective, then execute with explicit budgets (model\n"
        "  and tool call caps are anti-runaway nets, not invitations to pad\n"
        "  work). Update the durable TODO plan continuously.\n"
        "- Use subagents for isolated, well-specified subtasks; give each only\n"
        "  the tools it needs. Consolidate their evidence into your own answer.\n"
        "- A hypothesis failing is a signal to pivot, not to pause: re-target,\n"
        "  adapt and press on. When the objective itself is achieved or is\n"
        "  impossible, deliver the consolidated result."
    ),
}


def mode_contract(mode: OperationMode) -> str:
    """System-prompt contract text for the mode (appended by the supervisor)."""
    rules = _MODE_RULES.get(mode.value, _MODE_RULES["standard"])
    return _BASE_CONTRACT.format(mode=mode.value, mode_rules=rules)


def policy_for(mode: OperationMode) -> ModePolicy:
    """Resolve the effective policy for a mode, applying env overrides."""
    value = mode.value
    common = dict(
        mode=mode,
        approval_levels=_APPROVAL_LEVELS[value],
        planning_enabled=value != "standard",
        requires_goal=value == "goal",
        requires_scope=value == "beast",
        delegation=value == "beast",
        contract=mode_contract(mode),
    )
    if value == "beast":
        common["model_call_limit"] = _env_int("MUNIN_BEAST_MODEL_CALL_LIMIT", 96)
        common["tool_call_limit"] = _env_int("MUNIN_BEAST_TOOL_CALL_LIMIT", 256)
    if value in {"yolo", "goal", "beast"}:
        common["plan_reminder_every_steps"] = _env_int(
            "MUNIN_PLAN_REMINDER_EVERY_STEPS", {"yolo": 6, "goal": 5, "beast": 4}[value]
        )
    return ModePolicy(**common)


def parse_mode_policy(value: Any) -> ModePolicy:
    """Parse a mode (string/enum/None) into its effective policy."""
    return policy_for(OperationMode.parse(value))


def approval_required(level: str, mode: OperationMode | None = None) -> bool:
    """Shortcut: does audit ``level`` require approval under ``mode``?"""
    return policy_for(mode or OperationMode.STANDARD).approval_required_for(str(level).lower())


__all__ = [
    "OperationMode",
    "ModePolicy",
    "CRITICAL_APPROVAL_FLOOR",
    "policy_for",
    "parse_mode_policy",
    "mode_contract",
    "approval_required",
]
