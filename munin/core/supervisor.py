"""
Munin Supervisor — Deep Agents coordinator (issue #9 §1).

The supervisor is a ``deepagents.create_deep_agent`` graph:

* ``system_prompt`` = Munin Soul (identity layer, unchanged) + runtime policy
  + Autonomy Kernel usage instructions — see ``compose_munin_prompt``.
* ``tools`` = Tool Gateway (every fixed MCP / domain / gen__* tool as
  LangChain StructuredTools) + Autonomy Kernel meta-tools.
* ``middleware`` = operator guidance, standard LangChain call-limit guards,
  and progress emission.
* ``checkpointer`` = the application-lifetime AsyncSqliteSaver on
  ``MUNIN_CHECKPOINT_DB`` when the ASGI server provides it.

The module-level ``supervisor`` object (referenced by ``langgraph.json``) is
built lazily via PEP 562 ``__getattr__`` so importing this module never
performs I/O or requires credentials.
"""
from __future__ import annotations

import hashlib
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Process-wide caches (issue #9 §3 fix: build once, reuse across requests).
#
# ``_CHECKPOINTER_CACHE`` is only a development fallback. The ASGI lifespan
# enters one ``AsyncSqliteSaver`` and exposes it on ``SharedStateStore`` for
# durable cross-request checkpoints, HITL and replay.
#
# ``_GRAPH_CACHE`` holds at most one compiled supervisor graph per fingerprint.
# The fingerprint captures everything that, when changed, requires a rebuild:
# model identity, the active ``gen__*`` tool set + their signatures, the soul
# prompt, and the SharedStateStore instance identity. Per-run state that the
# middleware needs at invoke time (``run_id``, ``progress_sink``) is *not* in
# the fingerprint — it is delivered per-invocation via contextvars from the
# middleware modules (see ``munin.core.middleware.progress_emit`` /
# ``operator_guidance``), so one cached graph serves many runs.
# ---------------------------------------------------------------------------
_GRAPH_CACHE: dict[str, Any] = {}
_CHECKPOINTER_CACHE: dict[str, Any] = {}
_CACHE_LOCK = threading.Lock()
_CHECKPOINTER_LOCK = threading.Lock()


def _model_identity(model: Any) -> str:
    """Stable identity string for a chat model across reconstructs."""
    for attr in ("model_name", "model", "deployment"):
        value = getattr(model, attr, None)
        if value:
            return str(value)
    return repr(model) if model is not None else ""

def _get_checkpointer() -> Any:
    """Return the process-local fallback saver used outside ASGI lifespan."""
    with _CHECKPOINTER_LOCK:
        saver = _CHECKPOINTER_CACHE.get("saver")
        if saver is None:
            if "unavailable" in _CHECKPOINTER_CACHE:
                return None
            try:
                from langgraph.checkpoint.memory import MemorySaver  # noqa: PLC0415
            except ImportError:  # pragma: no cover - optional dep
                logger.warning("MemorySaver not importable: supervisor runs uncheckpointed")
                _CHECKPOINTER_CACHE["unavailable"] = True
                return None
            saver = MemorySaver()
            _CHECKPOINTER_CACHE["saver"] = saver
        return saver


def _gen_fingerprint(state: Any) -> frozenset[tuple[str, str, str]]:
    """Cheap, content-sensitive fingerprint of the active ``gen__*`` tool set.

    Tuple = ``(name, signature_json, created_at)`` per active procedural row.
    ``created_at`` makes any newly created / re-activated tool a cache miss
    without hashing source_code. Failure is non-fatal (treated as empty set):
    the catalog must never explode the build.
    """
    try:
        from ..mcp import registry  # noqa: TID252, PLC0415

        rows = registry.list_generated(state)
    except Exception:  # noqa: BLE001
        logger.debug("supervisor: gen fingerprint failed", exc_info=True)
        return frozenset()
    out: set[tuple[str, str, str]] = set()
    for row in rows:
        if not row.get("active", True):
            continue
        name = str(row.get("name") or "")
        sig = str(row.get("signature") or "")
        created = str(row.get("created_at") or "")
        out.add((name, sig, created))
    return frozenset(out)


def _soul_hash(prompt: str) -> str:
    return hashlib.sha1(prompt.encode("utf-8", "replace")).hexdigest() if prompt else ""


def _supervisor_fingerprint(
    *,
    model: Any,
    state: Any,
    soul_prompt: str,
    include_generated: bool,
    mode: Any = None,
) -> str:
    """Build the cache key for the compiled supervisor graph."""
    parts = [
        f"model={_model_identity(model)}",
        f"state={id(state)}",
        f"checkpointer={id(getattr(state, 'langgraph_checkpointer', None))}",
        f"gen={_gen_fingerprint(state) if include_generated else frozenset()}",
        f"soul={_soul_hash(soul_prompt)}",
        f"incgen={int(bool(include_generated))}",
        f"mode={str(mode or 'standard').lower()}",
        f"model_limit={getattr(getattr(state, 'settings', None), 'agent_model_call_limit', 24)}",
        f"tool_limit={getattr(getattr(state, 'settings', None), 'agent_tool_call_limit', 64)}",
    ]
    return "|".join(parts)


def invalidate_supervisor_cache() -> None:
    """Drop the cached supervisor graph so the next build recompiles.

    Call this whenever the Supervisor tool set changes structurally — i.e. when
    a new ``gen__*`` tool is created, activated, deactivated or purged, or
    when the soul prompt is edited. The checkpointer is intentionally NOT
    cleared: it owns per-``thread_id`` conversation checkpoints that must
    survive across rebuilds (HITL / resume).
    """
    with _CACHE_LOCK:
        _GRAPH_CACHE.clear()
    logger.info("supervisor: graph cache invalidated (next build recompiles)")

_KERNEL_INSTRUCTIONS = """
## Autonomy Kernel

You can create and use new capabilities at runtime:

- `create_tool` + `invoke_registered_tool`: author Python tools and call them
  in the SAME run. Persistent tools are discoverable via `list_registered_tools`.
- `create_subagent` + `invoke_registered_agent`: invent a specialist for an
  isolated task, run it, and (persist=true) keep it in the Agent Registry for
  future runs. Generated agents may themselves use these factory tools.
- `create_workflow` + `invoke_registered_workflow`: compile multi-node
  LangGraph workflows (deterministic, agent and tool nodes; static,
  conditional and Send fan-out edges) and run them as compiled subagents.
- `schedule_workers`: fan out N parallel Send workers (one per host/URL/CVE);
  individual failures do not abort the batch.

Prefer existing catalog tools before forging new ones. Every real side effect
still passes through Munin's scope/OPSEC/audit boundary — autonomy never
widens the authorized scope.
""".strip()


def compose_munin_prompt(*, soul_prompt: str = "", extra: str = "") -> str:
    """Compose the supervisor system prompt: Soul first, then runtime policy."""
    parts: list[str] = []
    if soul_prompt.strip():
        parts.append(soul_prompt.strip())
    else:
        parts.append(
            "You are Munin, an advanced offensive-security AI agent. Proceed "
            "methodically, document findings, and respect the authorized scope."
        )
    parts.append(_KERNEL_INSTRUCTIONS)
    if extra.strip():
        parts.append(extra.strip())
    return "\n\n".join(parts)


def make_checkpointer() -> Any:
    """Default checkpointer for the supervisor.

    ASGI production supplies a durable ``AsyncSqliteSaver`` directly. This
    fallback keeps CLI and isolated tests usable with a process-local saver.
    """
    return _get_checkpointer()


def build_supervisor(
    tools: list[Any],
    *,
    model: Any = None,
    system_prompt: str = "",
    middleware: list[Any] | None = None,
    meta_tools: list[Any] | None = None,
    checkpointer: Any = None,
    subagents: list[Any] | None = None,
    interrupt_on: dict[str, Any] | None = None,
) -> Any:
    """Build and compile the Munin supervisor graph.

    Args:
        tools: Gateway StructuredTools.
        model: BaseChatModel (or model string resolved by the framework).
        system_prompt: composed prompt (``compose_munin_prompt`` output) —
            when empty, the default Munin policy prompt is composed.
        middleware: LangChain 1.x AgentMiddleware instances.
        meta_tools: Autonomy Kernel meta-tools (appended to ``tools``).
        checkpointer: LangGraph checkpointer; defaults to durable sqlite.
        subagents: native Deep Agents subagents (registry-loaded persistent
            agents may be supplied here at build time).
    """
    from deepagents import create_deep_agent  # noqa: PLC0415

    prompt = system_prompt.strip() or compose_munin_prompt()

    return create_deep_agent(
        name="munin",
        model=model,
        tools=[*tools, *(meta_tools or [])],
        system_prompt=prompt,
        middleware=list(middleware or ()),
        subagents=subagents or None,
        interrupt_on=interrupt_on or None,
        checkpointer=checkpointer if checkpointer is not None else make_checkpointer(),
    )


def build_munin_supervisor(
    *,
    state: Any,
    model: Any = None,
    run_id: str = "",
    progress_sink: Any = None,
    include_generated: bool = True,
    mode: Any = None,
) -> Any:
    """Full production assembly: gateway + kernel + middleware + soul.

    This is the one authoritative builder used by the runtime adapter, the
    CLI and ``munin_chat`` — no second runtime path exists.

    ``mode`` (``OperationMode``/str/None) changes the *product* guardrails
    only: approval levels (the ``critical`` floor is immutable), the
    anti-runaway call budgets, and the planning/goal middleware presence.
    The compiled graph is **cached per process** keyed by a fingerprint of
    (model identity, active ``gen__*`` tool set + signatures, soul prompt,
    SharedStateStore identity, mode). Per-run state (``run_id``,
    ``progress_sink``, the live goal + plan snapshot) is delivered at invoke
    time via the ``ACTIVE_*`` contextvars, so one cached graph per mode
    serves every conversation. The ASGI lifespan supplies a durable saver
    keyed by ``thread_id`` for HITL/resume. The ``run_id``/``progress_sink``
    arguments here are construction-time fallbacks (used by the single-build
    langgraph dev-server path where contextvars are never set).
    """
    from langchain.agents.middleware import (  # noqa: PLC0415
        ModelCallLimitMiddleware,
        ToolCallLimitMiddleware,
    )

    from .autonomy.goals import GoalMiddleware  # noqa: PLC0415
    from .autonomy.kernel import AutonomyKernel  # noqa: PLC0415
    from .autonomy.modes import OperationMode, parse_mode_policy  # noqa: PLC0415
    from .autonomy.planning import TodoPlanMiddleware  # noqa: PLC0415
    from .middleware import (  # noqa: PLC0415
        OperatorGuidanceMiddleware,
        ProgressEmitMiddleware,
    )
    from .tool_gateway import (
        approval_policy_for_tools,  # noqa: PLC0415
        gateway_tools,  # noqa: PLC0415
    )

    policy = parse_mode_policy(mode)
    mode_enum: OperationMode = policy.mode

    soul_prompt = ""
    try:
        from .soul import SoulManager  # noqa: PLC0415

        soul_prompt = SoulManager(
            state.settings.munin_soul_path, state.settings.munin_data_path
        ).as_system_prompt()
    except Exception as exc:  # noqa: BLE001
        logger.warning("supervisor: soul unavailable (%s); using fallback identity", exc)

    fingerprint = _supervisor_fingerprint(
        model=model,
        state=state,
        soul_prompt=soul_prompt,
        include_generated=include_generated,
        mode=mode_enum,
    )
    with _CACHE_LOCK:
        cached = _GRAPH_CACHE.get(fingerprint)
    if cached is not None:
        logger.debug("supervisor: cache hit fingerprint=%s", fingerprint[:64])
        return cached

    # Build-time fallbacks; the live per-invocation values come from the
    # middleware contextvars set by ``runtime_adapter.supervisor_runner``.
    def _noop_sink(_: dict) -> None:
        return None

    middleware: list[Any] = [
        OperatorGuidanceMiddleware(run_id=run_id, store=state),
        ProgressEmitMiddleware(
            progress_sink=progress_sink if progress_sink is not None else _noop_sink,
            run_id=run_id,
        ),
    ]
    if policy.planning_enabled:
        middleware.append(TodoPlanMiddleware(store=state, mode=mode_enum.value))
    middleware.append(GoalMiddleware(goal=None))
    # Deep Agents composes standard LangChain middleware at graph-build time.
    # These caps count actual model/tool executions, unlike the retired
    # content-only repetition guard which treated distinct tool calls with
    # empty assistant text as a loop.  Modes may raise the anti-runaway nets
    # within env-configurable bounds (never below the server defaults).
    settings_model_limit = max(0, int(getattr(state.settings, "agent_model_call_limit", 24)))
    settings_tool_limit = max(0, int(getattr(state.settings, "agent_tool_call_limit", 64)))
    model_limit = policy.model_call_limit if policy.model_call_limit is not None else settings_model_limit
    tool_limit = policy.tool_call_limit if policy.tool_call_limit is not None else settings_tool_limit
    if model_limit:
        middleware.insert(1, ModelCallLimitMiddleware(run_limit=model_limit, exit_behavior="end"))
    if tool_limit:
        middleware.insert(2 if model_limit else 1, ToolCallLimitMiddleware(run_limit=tool_limit))

    tools = gateway_tools(state, include_generated=include_generated)
    durable_checkpointer = getattr(state, "langgraph_checkpointer", None) or _get_checkpointer()
    kernel_ref: dict[str, AutonomyKernel] = {}

    def generated_agent_tools() -> list[Any]:
        # Factory tools are inherited only by children produced through this
        # kernel; the ordinary gateway still governs each real side effect.
        generated = gateway_tools(state, include_generated=include_generated)
        kernel_instance = kernel_ref.get("kernel")
        return [*generated, *(kernel_instance.meta_tools() if kernel_instance else [])]

    kernel = AutonomyKernel(
        state,
        model=model,
        run_id=run_id,
        tools_provider=generated_agent_tools,
        checkpointer=durable_checkpointer,
    )
    kernel_ref["kernel"] = kernel

    from .autonomy.modes import mode_contract  # noqa: PLC0415

    graph = build_supervisor(
        tools=tools,
        model=model,
        system_prompt=compose_munin_prompt(
            soul_prompt=soul_prompt,
            extra=mode_contract(mode_enum),
        ),
        middleware=middleware,
        meta_tools=kernel.meta_tools(),
        interrupt_on=approval_policy_for_tools(tools, mode=mode_enum),
        checkpointer=durable_checkpointer,
    )
    with _CACHE_LOCK:
        # Last-writer-wins is fine: identical fingerprint → identical build.
        _GRAPH_CACHE[fingerprint] = graph
    logger.info("supervisor: built and cached graph fingerprint=%s", fingerprint[:64])
    return graph


# ---------------------------------------------------------------------------
# langgraph.json entrypoint — lazy module attribute (PEP 562).
# ---------------------------------------------------------------------------


def __getattr__(name: str) -> Any:  # pragma: no cover - exercised by langgraph dev
    if name == "supervisor":
        from ..mcp.config import get_settings  # noqa: TID252, PLC0415
        from ..mcp.shared_state import SharedStateStore  # noqa: TID252, PLC0415
        from .llm_client import LLMClient  # noqa: PLC0415

        settings = get_settings()
        state = SharedStateStore(settings)
        model = LLMClient(settings).make_langchain()
        graph = build_munin_supervisor(state=state, model=model, run_id="langgraph-server")
        globals()["supervisor"] = graph
        return graph
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
