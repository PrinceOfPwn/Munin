"""
Tool Gateway — exposes every *registered* Munin MCP tool, state-bound domain
tool, and generated ``gen__*`` tool as LangChain ``StructuredTool`` instances
consumable by the Deep Agents supervisor and any generated subagent.

Design notes (issue #9, PR-C):

* The native capability source is FastMCP's live ``ToolManager``.  A tool that
  is registered with the running MCP server (including an extension) is
  discovered here without another static name list.  The legacy builder only
  supplies state-bound adapters and generated ``gen__*`` callables.
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
from collections.abc import Callable, Iterable
from functools import wraps
from typing import Any

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


def _registered_mcp_tools() -> dict[str, Callable[..., Any]]:
    """Read callable tools from FastMCP's live registry.

    FastMCP is the public source of truth for Munin-native capabilities.  Its
    registered ``Tool.fn`` values are the audited handlers used by the MCP
    transport, so invoking them in the agent graph preserves the existing
    scope, OPSEC, and audit boundary.  The guarded fallback keeps unit tests
    that intentionally omit the optional MCP runtime usable.
    """
    try:
        from ..mcp.main import MCP  # noqa: TID252, PLC0415

        records = getattr(getattr(MCP, "_tool_manager", None), "_tools", {})
        return {
            str(name): handler
            for name, tool in dict(records).items()
            if callable(handler := getattr(tool, "fn", None))
        }
    except Exception:  # pragma: no cover - optional import / malformed extension
        logger.exception("tool_gateway: failed to inspect the FastMCP registry")
        return {}


def catalog_names(state: Any, *, include_generated: bool = True) -> set[str]:
    """Universe of tool names the gateway can expose for ``state``."""
    names = set(_registered_mcp_tools()) | set(_STATE_BOUND_TOOL_NAMES)
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


def _bind_runtime_run_id(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Inject the graph run id only when a handler declares ``run_id``.

    The model never receives this parameter. Direct FastMCP calls keep an
    empty run id and therefore cannot impersonate an approved graph run.
    """
    try:
        if "run_id" not in inspect.signature(fn).parameters:
            return fn
    except (TypeError, ValueError):
        return fn

    def active_run_id() -> str:
        try:
            from .middleware.operator_guidance import ACTIVE_RUN_ID  # noqa: PLC0415

            return str(ACTIVE_RUN_ID.get() or "")
        except Exception:  # pragma: no cover - direct MCP / optional middleware
            return ""

    if inspect.iscoroutinefunction(fn):
        @wraps(fn)
        async def async_bound(*args: Any, **kwargs: Any) -> Any:
            kwargs.setdefault("run_id", active_run_id())
            return await fn(*args, **kwargs)

        return async_bound

    @wraps(fn)
    def bound(*args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("run_id", active_run_id())
        return fn(*args, **kwargs)

    return bound


def to_structured_tool(name: str, fn: Callable[..., Any]) -> Any:
    """Convert one catalog callable into a LangChain ``StructuredTool``."""
    from langchain_core.tools import StructuredTool  # noqa: PLC0415

    fn = _strip_hidden_params(_bind_runtime_run_id(fn))
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


def lookup_catalog_handler(state: Any, name: str) -> Callable[..., Any] | None:
    """Resolve a capability from the same live source as the gateway.

    A FastMCP extension registered after graph compilation remains callable
    through the generic ``invoke_registered_tool`` meta-tool in the current
    run; a newly compiled graph will see it directly in its tool list.
    """
    native = _registered_mcp_tools().get(name)
    if native is not None:
        return native
    try:
        from ..subagents.base import build_tool_catalog  # noqa: TID252, PLC0415

        return build_tool_catalog(state, {name}).get(name)
    except Exception:  # pragma: no cover - compatibility boundary
        logger.exception("tool_gateway: failed to resolve %r", name)
        return None


def approval_policy_for_tools(
    tools: Iterable[Any],
    mode: Any = None,
) -> dict[str, dict[str, Any]]:
    """Derive Deep Agents' HITL rules from audited live tool metadata.

    Active/admin operations and generated code require an approve-or-reject
    interrupt.  Passive/state-bound tools stay unblocked.  The policy has no
    provider reasoning or model-generated explanation.

    ``mode`` (``OperationMode``/str/None) selects the *product* guardrail:
    which additional audit levels pause for approval on top of the immutable
    ``critical`` floor (which ALWAYS interrupts, in every mode — see
    ``munin.core.autonomy.modes``).  Standard mode keeps today's behavior
    exactly (active + admin + critical).
    """
    from .autonomy.modes import parse_mode_policy  # noqa: TID252, PLC0415

    policy_mode = parse_mode_policy(mode)
    policy: dict[str, dict[str, Any]] = {}
    for tool in tools:
        name = str(getattr(tool, "name", "") or "")
        handler = getattr(tool, "func", None) or getattr(tool, "coroutine", None)
        level = str(getattr(handler, "__munin_audit_level__", "") or "").lower()
        needs_review = policy_mode.approval_required_for(level) or name.startswith("gen__")
        # A PR is a privileged external write. This narrow fallback protects
        # registrations created before audit metadata was attached.
        needs_review = needs_review or name == "extension_open_pr"
        if needs_review and name:
            policy[name] = {
                "allowed_decisions": ["approve", "reject"],
                "description": "Operator approval is required before this authorized action runs.",
            }
    return policy


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
    # Start from FastMCP's *live* native registry, then overlay the legacy
    # builder for state-bound adapters.  The overlay intentionally wins for
    # memory/task/presence tools because it binds them to the supervisor's
    # supplied state (important for isolated tests and subgraphs); native
    # FastMCP-only tools such as nmap_scan/httpx_probe remain present.
    catalog = {
        name: fn for name, fn in _registered_mcp_tools().items() if name in names
    }
    catalog.update(build_tool_catalog(state, names))

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
