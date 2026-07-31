"""Parallel-safe tool dispatch (no monkey-patch, declared not inferred).

This module replaces the earlier v3.1 heuristic — which decided whether a tool
was safe to run concurrently based on **name suffixes** — with an explicit,
declarative registry keyed by tool name.  Declaring safety by name is
brittle: renaming ``memory_write`` to ``memory_store`` silently promotes it to
"safe" and lets a batch race against itself.

Two ways for a tool to declare it is safe to run concurrently with siblings:

1. A **decorator** at definition time — attach ``__munin_parallel_safe__`` /
   ``__munin_read_only__`` to the callable::

       @parallel_safe(read_only=True)
       def ldap_search(...): ...

   This is the preferred form for new tools — the safety marker travels with
   the function so refactors can't strand it.

2. The **explicit name-registry** below.  This exists because the current
   tool catalog is built inline from a hand-rolled ``_NATIVE_TOOLS`` mapping
   (see ``munin/core/munin_agent.py``) — there is no metaclass to hook, and
   editing every callable site is out of scope for this hardening pass.
   Add a tool name here **only if you have verified it is idempotent, has
   no external side effects, and does not depend on shared mutable state**.

   The default is ``False`` — any tool NOT in the registry and NOT decorated
   runs serially.  Serializing extra tools is a slowdown; incorrectly marking
   a mutating tool safe is a correctness bug.

Everything else — partitioning, ThreadPoolExecutor lifecycle, per-call
observation — is a plain function you call from ``MuninAgent.respond`` when
the model returns more than one tool_use block in a single assistant message.
No import-time monkey-patching, no reliance on progress-callable attributes.
"""

from __future__ import annotations

import concurrent.futures
import contextvars
import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("munin.production.parallel")


# ---------------------------------------------------------------------------
# Explicit parallel-safe registry.
# ---------------------------------------------------------------------------
#
# Every entry has been reviewed against the tool implementation in the base
# codebase and confirmed to be:
#   - read-only against shared state (no writes to SQLite, memory, filesystem,
#     git, external services);
#   - idempotent for repeated calls with the same arguments;
#   - free of order-sensitive side effects (no shared counters, no lease
#     acquisition, no HITL prompts).
#
# When in doubt: leave it OUT of this set.  A missing tool serialises — a
# false positive here corrupts state under concurrency.

PARALLEL_SAFE_TOOLS: frozenset[str] = frozenset(
    {
        # ── LDAP — all read paths ──────────────────────────────────────────
        "ldap_who_am_i",
        "get_current_user_info",
        "get_user_groups",
        "ldap_search",
        "find_kerberoastable_users",
        "find_asrep_roastable_users",
        "find_domain_admins",
        "dump_domain_structure",
        # ── External intel — read paths only ──────────────────────────────
        "tavily_search",
        "hugin_search",
        # hugin_refresh mutates the cache — intentionally NOT included.
        "hugin_neighbors",
        "hugin_rag_search",
        "hugin_plan_for",
        "hugin_node_detail",
        # ── Munin self-inspection ─────────────────────────────────────────
        "munin_capabilities",
        "munin_self_diagnose",
        "munin_diagnostics",
        "munin_read_source",
        "read_wake_artifact",
        # ── Read-only catalog inspection ──────────────────────────────────
        "list_generated_tools",
        "describe_generated_tool",
        "soul_read",
        "soul_list",
        "memory_recall",
        "memory_list",
        "episodic_query",
        "munin_wake_list",
        "subagent_trace",
        "discord_status",
        "list_generated_graphs",
        "describe_generated_graph",
        "extension_list",
        "extension_describe",
        "list_subagent_tools",
        # ── Passive HTTP surface probes ───────────────────────────────────
        "httpx_probe",
        # ── Shared-state readers ──────────────────────────────────────────
        "shared_state_overview",
        "list_agent_presence",
        "fetch_agent_messages",
        "query_shared_intel",
        "list_shared_tasks",
    }
)


# ---------------------------------------------------------------------------
# Concurrency bound
# ---------------------------------------------------------------------------


def max_parallel_tools() -> int:
    """Cap concurrent tool invocations per batch (env-tunable)."""
    try:
        return max(1, int(os.environ.get("MUNIN_MAX_PARALLEL_TOOLS", "6")))
    except ValueError:
        return 6


MAX_PARALLEL_TOOLS = max_parallel_tools()


# ---------------------------------------------------------------------------
# Decorator + descriptor accessors
# ---------------------------------------------------------------------------


def parallel_safe(*, read_only: bool = False) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Attach declarative safety markers to a tool callable.

    ``read_only`` is metadata for HITL heuristics — a read-only tool is one
    that never emits side effects, so it can also be surfaced to the operator
    as safe to auto-approve.  It does not gate parallelism on its own; the
    tool is marked parallel-safe by virtue of being decorated at all.
    """

    def _wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
        setattr(fn, "__munin_parallel_safe__", True)
        setattr(fn, "__munin_read_only__", bool(read_only))
        return fn

    return _wrap


def is_parallel_safe(tool_name: str, fn: Callable[..., Any] | None = None) -> bool:
    """Return True iff the tool has explicitly declared parallel safety.

    Preference order:
      1. The callable is decorated with ``@parallel_safe``.
      2. The tool name is in ``PARALLEL_SAFE_TOOLS``.
      3. Anything else — False.
    """
    if fn is not None and bool(getattr(fn, "__munin_parallel_safe__", False)):
        return True
    return tool_name in PARALLEL_SAFE_TOOLS


def is_read_only(tool_name: str, fn: Callable[..., Any] | None = None) -> bool:
    """Metadata used by future HITL/approval heuristics.

    A read-only tool is a superset of parallel-safe: it never mutates state,
    which makes it a safe candidate for automatic operator approval on runs
    that require HITL.  Parallel safety and read-only are tracked separately
    because a tool may be safe to run in parallel (an idempotent write that
    always converges) but still worth surfacing to the operator.
    """
    if fn is not None and bool(getattr(fn, "__munin_read_only__", False)):
        return True
    # All entries in the current PARALLEL_SAFE_TOOLS set are also read-only.
    return tool_name in PARALLEL_SAFE_TOOLS


# ---------------------------------------------------------------------------
# Partition + batch execution
# ---------------------------------------------------------------------------


def partition_by_parallel_safety(
    tool_calls: list[dict[str, Any]],
    catalog: dict[str, Callable[..., Any]] | None = None,
) -> tuple[list[int], list[int]]:
    """Split tool_call indices into (parallel-safe, serial) buckets.

    The buckets contain *indices into the original ``tool_calls`` list* so the
    caller can reassemble results in the model's declared order — Anthropic /
    OpenAI tool_use messages expect strict order preservation between the
    assistant message and the tool response messages that follow.
    """
    parallel: list[int] = []
    serial: list[int] = []
    catalog = catalog or {}
    for idx, call in enumerate(tool_calls):
        name = str(call.get("function", {}).get("name", ""))
        fn = catalog.get(name)
        (parallel if is_parallel_safe(name, fn) else serial).append(idx)
    return parallel, serial


def execute_tool_batch(
    tool_calls: list[dict[str, Any]],
    *,
    invoker: Callable[[dict[str, Any], str | None], dict[str, Any]],
    catalog: dict[str, Callable[..., Any]] | None = None,
    max_workers: int | None = None,
) -> list[dict[str, Any]]:
    """Run a batch of tool_use blocks with parallel-safe subset concurrent.

    Parameters
    ----------
    tool_calls:
        The list of tool_use blocks the model produced this iteration.  Each
        entry has the OpenAI tool_call shape ``{"id", "function": {"name",
        "arguments"}}``.
    invoker:
        The caller-supplied worker that actually executes ONE tool call.  It
        receives ``(call, parallel_group_id)`` and returns the tool result
        dict.  The invoker is the natural home for the caller's emit /
        logging / catalog resolution — this module stays purely mechanical.
    catalog:
        Optional catalog reference used only for decorator-attribute lookup
        during partitioning.  When omitted, only the name registry is
        consulted.
    max_workers:
        Override for the ThreadPoolExecutor width.  Defaults to
        ``MAX_PARALLEL_TOOLS`` (env-tunable).

    Returns
    -------
    Ordered list of tool result dicts — one entry per input tool_call, in
    the original order.  Failures are converted to
    ``{"ok": False, "error": {"code": "tool_crashed", ...}}`` so the caller
    never has to handle a raised exception.
    """
    if not tool_calls:
        return []
    if len(tool_calls) == 1:
        return [invoker(tool_calls[0], None)]

    parallel_indices, serial_indices = partition_by_parallel_safety(tool_calls, catalog)
    # A single parallel-safe call amid serial calls is not worth a shared
    # group_id: the UI would render a "batch of one" which is confusing.
    group_id = uuid.uuid4().hex if len(parallel_indices) > 1 else None
    results: dict[int, dict[str, Any]] = {}

    # ── parallel phase ─────────────────────────────────────────────────
    if parallel_indices:
        workers = min(len(parallel_indices), max_workers or MAX_PARALLEL_TOOLS)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            # Each worker gets its own contextvars snapshot so the scheduler's
            # tracing / logging context follows the call.  A single Context is
            # not safe to run concurrently, so we take a fresh copy per submit.
            future_to_idx = {
                pool.submit(
                    contextvars.copy_context().run,
                    _safe_invoke,
                    invoker,
                    tool_calls[i],
                    group_id,
                ): i
                for i in parallel_indices
            }
            for future in concurrent.futures.as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    results[idx] = future.result()
                except Exception as exc:  # noqa: BLE001
                    logger.exception("parallel tool worker crashed")
                    results[idx] = {
                        "ok": False,
                        "error": {"code": "tool_crashed", "message": str(exc)},
                    }

    # ── serial phase ───────────────────────────────────────────────────
    for idx in serial_indices:
        results[idx] = _safe_invoke(invoker, tool_calls[idx], None)

    return [results[i] for i in range(len(tool_calls))]


def _safe_invoke(
    invoker: Callable[[dict[str, Any], str | None], dict[str, Any]],
    call: dict[str, Any],
    group_id: str | None,
) -> dict[str, Any]:
    """Never let a worker crash unwind the executor."""
    try:
        return invoker(call, group_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("tool invoker crashed")
        return {
            "ok": False,
            "error": {"code": "tool_crashed", "message": str(exc)},
        }


# ---------------------------------------------------------------------------
# Small helpers kept here so callers don't reinvent them.
# ---------------------------------------------------------------------------


def parse_tool_args(call: dict[str, Any]) -> dict[str, Any]:
    """Best-effort tool_call arguments parser (OpenAI / Anthropic compatible)."""
    raw = call.get("function", {}).get("arguments") or "{}"
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def tool_call_name(call: dict[str, Any]) -> str:
    return str(call.get("function", {}).get("name", ""))


def elapsed_ms_since(started_at: float) -> int:
    return int((time.monotonic() - started_at) * 1000)


__all__ = [
    "PARALLEL_SAFE_TOOLS",
    "MAX_PARALLEL_TOOLS",
    "max_parallel_tools",
    "parallel_safe",
    "is_parallel_safe",
    "is_read_only",
    "partition_by_parallel_safety",
    "execute_tool_batch",
    "parse_tool_args",
    "tool_call_name",
    "elapsed_ms_since",
]
