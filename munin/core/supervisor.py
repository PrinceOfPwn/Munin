"""
Munin Supervisor — Deep Agents coordinator (issue #9 §1).

The supervisor is a ``deepagents.create_deep_agent`` graph:

* ``system_prompt`` = Munin Soul (identity layer, unchanged) + runtime policy
  + Autonomy Kernel usage instructions — see ``compose_munin_prompt``.
* ``tools`` = Tool Gateway (every fixed MCP / domain / gen__* tool as
  LangChain StructuredTools) + Autonomy Kernel meta-tools.
* ``middleware`` = operator guidance, repetition guard, progress emission —
  real LangChain 1.x AgentMiddleware hooks.
* ``checkpointer`` = durable AsyncSqliteSaver on ``MUNIN_CHECKPOINT_DB`` so
  threads survive runner restarts (resume / HITL interrupts).

The module-level ``supervisor`` object (referenced by ``langgraph.json``) is
built lazily via PEP 562 ``__getattr__`` so importing this module never
performs I/O or requires credentials.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

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

    Historically Munin tried to ship a durable ``AsyncSqliteSaver`` here, but
    ``AsyncSqliteSaver.from_conn_string`` returns an async context manager,
    not a ``BaseCheckpointSaver`` instance, and the synchronous variant has
    the same shape. ``create_deep_agent(checkpointer=...)`` needs an actual
    saver before the graph runs, so the durable path would require wrapping
    the entire build+invoke lifecycle in an ``async with`` — which conflicts
    with Munin's build-once / invoke-many-times model.

    For now we default to ``MemorySaver``: per-thread state inside a single
    supervisor process (enough for HITL interrupts and resume within one
    session — the use case issue #9 §3 actually calls out). Truly durable
    cross-session checkpointing belongs to a follow-up PR (see
    IMPLEMENTATION_ROADMAP.md) and will wrap the supervisor invocation in an
    ``async with AsyncSqliteSaver.from_conn_string(MUNIN_CHECKPOINT_DB)``.
    """
    from langgraph.checkpoint.memory import MemorySaver  # noqa: PLC0415

    return MemorySaver()


def build_supervisor(
    tools: list[Any],
    *,
    model: Any = None,
    system_prompt: str = "",
    middleware: list[Any] | None = None,
    meta_tools: list[Any] | None = None,
    checkpointer: Any = None,
    subagents: list[Any] | None = None,
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
        checkpointer=checkpointer if checkpointer is not None else make_checkpointer(),
    )


def build_munin_supervisor(
    *,
    state: Any,
    model: Any = None,
    run_id: str = "",
    progress_sink: Any = None,
    include_generated: bool = True,
) -> Any:
    """Full production assembly: gateway + kernel + middleware + soul.

    This is the one authoritative builder used by the runtime adapter, the
    CLI and ``munin_chat`` — no second runtime path exists.
    """
    from .autonomy.kernel import AutonomyKernel  # noqa: PLC0415
    from .middleware import (  # noqa: PLC0415
        OperatorGuidanceMiddleware,
        ProgressEmitMiddleware,
        RepetitionGuardMiddleware,
    )
    from .tool_gateway import gateway_tools  # noqa: PLC0415

    soul_prompt = ""
    try:
        from .soul import SoulManager  # noqa: PLC0415

        soul_prompt = SoulManager(
            state.settings.munin_soul_path, state.settings.munin_data_path
        ).as_system_prompt()
    except Exception as exc:  # noqa: BLE001
        logger.warning("supervisor: soul unavailable (%s); using fallback identity", exc)

    middleware: list[Any] = [
        OperatorGuidanceMiddleware(run_id=run_id, store=state),
        RepetitionGuardMiddleware(),
    ]
    if progress_sink is not None:
        middleware.append(ProgressEmitMiddleware(progress_sink=progress_sink, run_id=run_id))

    kernel = AutonomyKernel(
        state,
        model=model,
        run_id=run_id,
        tools_provider=lambda: gateway_tools(state, include_generated=include_generated),
    )

    return build_supervisor(
        tools=gateway_tools(state, include_generated=include_generated),
        model=model,
        system_prompt=compose_munin_prompt(soul_prompt=soul_prompt),
        middleware=middleware,
        meta_tools=kernel.meta_tools(),
    )


# ---------------------------------------------------------------------------
# langgraph.json entrypoint — lazy module attribute (PEP 562).
# ---------------------------------------------------------------------------


def __getattr__(name: str) -> Any:  # pragma: no cover - exercised by langgraph dev
    if name == "supervisor":
        from ..mcp.config import get_settings  # noqa: TID252, PLC0415
        from ..mcp.shared_state import SharedStateStore  # noqa: TID252, PLC0415

        state = SharedStateStore(get_settings())
        graph = build_munin_supervisor(state=state, run_id="langgraph-server")
        globals()["supervisor"] = graph
        return graph
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
