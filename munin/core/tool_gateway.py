"""
Tool Gateway — exposes every Munin tool (fixed MCP, state-bound domain tools,
and generated ``gen__*`` tools) as LangChain ``StructuredTool`` instances
consumable by the Deep Agents supervisor and any generated subagent.

Design notes (issue #9, PR-C):

* The authoritative catalog is ``munin.subagents.base.build_tool_catalog`` —
  the same registry the legacy ReAct loop used, so the new runtime inherits
  identical behavior (LDAP, intel, memory, messaging, tasks, wake, gen__*).
  No second hand-written catalog is maintained here.
* Schema generation is owned by LangChain (``StructuredTool.from_function``),
  not by Munin's old ``_signature_to_openai`` glue.  We only strip the
  operator-injected ``run_id`` parameter from the exposed signature.
* OPSEC/scope/audit stay inside the tool handlers themselves
  (``ExecutionEngine._preflight_gated`` runs pre/post-flight for active
  tools, ``wrap_generated_callable`` audits gen__* calls), so wrapping them
  here does not weaken the capability boundary.
"""
from __future__ import annotations

import inspect
import logging
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

# Parameters injected by the operator/runtime, never by the model.
_HIDDEN_PARAMS = frozenset({"run_id"})

# Names of the state-bound tools produced by the ``_make_*`` factories in
# ``munin.subagents.base``.  Kept in sync with that module — it is the same
# surface the legacy runtime exposed to subagents.
_STATE_BOUND_TOOL_NAMES = frozenset(
    {
        # memory
        "memory_remember",
        "memory_recall",
        "memory_list",
        "episodic_query",
        # messaging / presence
        "post_agent_message",
        "fetch_agent_messages",
        "ack_agent_message",
        "list_agent_presence",
        "upsert_agent_presence",
        # shared intel
        "publish_shared_intel",
        "query_shared_intel",
        "shared_state_overview",
        # shared tasks
        "claim_shared_task",
        "complete_shared_task",
        "heartbeat_shared_task",
        "list_shared_tasks",
        # wake queue (compat during migration)
        "munin_wake",
        "munin_wake_list",
    }
)


def _active_generated_names(state: Any) -> set[str]:
    """Names of active gen__* tools persisted in the procedural table."""
    try:
        from ..mcp import registry  # noqa: TID252, PLC0415

        return {
            row["name"]
            for row in registry.list_generated(state)
            if row.get("active", True)
        }
    except Exception:  # pragma: no cover - catalog must never explode
        logger.exception("tool_gateway: failed to list generated tools")
        return set()


def catalog_names(state: Any, *, include_generated: bool = True) -> set[str]:
    """Universe of tool names the gateway can expose for ``state``."""
    from ..subagents import base as subagents_base  # noqa: TID252, PLC0415

    names = set(subagents_base._STATIC_TOOLS) | set(_STATE_BOUND_TOOL_NAMES)
    if include_generated:
        names |= _active_generated_names(state)
    return names


def _strip_hidden_params(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Hide operator-injected params (``run_id``) from the LLM-facing schema."""
    try:
        sig = inspect.signature(fn)
        hidden = [p for p in sig.parameters.values() if p.name in _HIDDEN_PARAMS]
        if not hidden:
            return fn
        kept = [p for p in sig.parameters.values() if p.name not in _HIDDEN_PARAMS]
        fn.__signature__ = sig.replace(parameters=kept)  # type: ignore[attr-defined]
    except (TypeError, ValueError, AttributeError):
        pass
    return fn


def to_structured_tool(name: str, fn: Callable[..., Any]) -> Any:
    """Convert one catalog callable into a LangChain ``StructuredTool``."""
    from langchain_core.tools import StructuredTool  # noqa: PLC0415

    fn = _strip_hidden_params(fn)
    description = (inspect.getdoc(fn) or "").strip().split("\n")[0] or f"Munin tool {name}"
    if inspect.iscoroutinefunction(fn):
        return StructuredTool.from_function(
            coroutine=fn,
            name=name,
            description=description[:1024],
        )
    return StructuredTool.from_function(
        func=fn,
        name=name,
        description=description[:1024],
    )


def gateway_tools(
    state: Any,
    *,
    allowed: Iterable[str] | None = None,
    include_generated: bool = True,
) -> list[Any]:
    """Build the LangChain tool list for the supervisor/subagents.

    Args:
        state: ``SharedStateStore`` the domain tools bind to.
        allowed: optional explicit name filter; defaults to the full catalog.
        include_generated: include active ``gen__*`` tools from the registry.

    Returns:
        list of ``StructuredTool``.  Individual tools that fail conversion are
        logged and skipped — one bad callable must not sink the whole catalog.
    """
    from ..subagents.base import build_tool_catalog  # noqa: TID252, PLC0415

    names = set(allowed) if allowed is not None else catalog_names(
        state, include_generated=include_generated
    )
    catalog = build_tool_catalog(state, names)

    tools: list[Any] = []
    for name, fn in catalog.items():
        try:
            tools.append(to_structured_tool(name, fn))
        except Exception as exc:  # noqa: BLE001
            logger.warning("tool_gateway: failed to wrap %r: %s", name, exc)
    return tools


# ---------------------------------------------------------------------------
# Backwards-compatible shims for the PR-05 scaffolding API.  Kept so existing
# imports don't break; new code should use ``gateway_tools``.
# ---------------------------------------------------------------------------


def wrap_mcp_tool(name: str, description: str, signature: dict, handler: Callable) -> Any:
    """Legacy shim: wrap a single handler (schema arg ignored — LangChain owns it)."""
    return to_structured_tool(name, handler)


def wrap_all_tools(registry: Any) -> list[Any]:  # pragma: no cover - legacy path
    """Legacy shim retained for import compatibility (returns empty list)."""
    logger.warning("tool_gateway.wrap_all_tools is deprecated; use gateway_tools(state)")
    return []
