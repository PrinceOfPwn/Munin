"""
Durable plan (TODO) middleware — real LangChain 1.x ``AgentMiddleware``.

The plan is authoritative in the **store** (append-only ``todo_events``,
replayed to SSE clients and re-rendered on every model call), NOT in the
graph state.  This keeps one source of truth, survives graph rebuilds and
checkpoint loss, and never pollutes the visible message history with plan
noise.

Model-facing surface (registered as middleware tools, exactly like the
native ``TodoListMiddleware`` pattern):

* ``todo_update(ops)`` — one or more typed ops: create / edit / set_state /
  set_priority / link_hypothesis / attach_evidence / discard / replan.
  Each op writes a durable event, emits a ``todo`` envelope (or ``replan``
  for the replan op) and returns a compact plan summary.
* ``hypothesis(statement, status, evidence)`` — record a hypothesis with
  validation state; emits a ``hypothesis`` envelope.

Every model call re-injects a compact rendering of the *current* durable
plan + goal from ``ACTIVE_PLAN_SNAPSHOT`` (set per invocation by the runtime
adapter), so the plan is always live without history pollution.  A compact
``note`` reminder is emitted every ``plan_reminder_every_steps`` model steps
as a pure UI affordance.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Annotated, Any, Literal

from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import Command
from pydantic import BaseModel, Field

from ...mcp.audit import redact_secrets  # noqa: TID252
from .context import (  # noqa: TID252
    ACTIVE_EMITTER,
    ACTIVE_GOAL,
    ACTIVE_MODE,
    ACTIVE_PLAN_SNAPSHOT,
    ACTIVE_STORE,
)
from .modes import OperationMode, parse_mode_policy  # noqa: TID252

logger = logging.getLogger(__name__)

try:  # LangChain 1.x middleware surface
    from langchain.agents.middleware import AgentMiddleware
except ImportError:  # pragma: no cover - older langchain
    class AgentMiddleware:  # type: ignore[no-redef]
        pass

PLAN_TOOL_NAME = "todo_update"
HYPOTHESIS_TOOL_NAME = "hypothesis"


# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------

_TODO_STATUS = Literal["pending", "in_progress", "blocked", "done", "discarded"]
_TODO_PRIORITY = Literal["low", "normal", "high", "critical"]
_HYPOTHESIS_STATUS = Literal["proposed", "confirmed", "rejected"]


class TodoItem(BaseModel):
    """One durable plan item (mirrored 1:1 into the store)."""

    id: str = ""
    title: str = Field(min_length=1, max_length=500)
    status: _TODO_STATUS = "pending"
    priority: _TODO_PRIORITY = "normal"
    dependencies: list[str] = Field(default_factory=list)
    hypothesis: str = ""
    evidence: str = ""
    owner: Literal["agent", "operator"] = "agent"
    created_at_ms: int = 0
    updated_at_ms: int = 0
    change_reason: str = ""


class TodoOp(BaseModel):
    """One typed mutation against the plan."""

    op: Literal[
        "create",
        "edit",
        "set_state",
        "set_priority",
        "link_hypothesis",
        "attach_evidence",
        "discard",
        "replan",
    ]
    item_id: str = ""
    title: str = ""
    state: _TODO_STATUS | None = None
    priority: _TODO_PRIORITY | None = None
    dependencies: list[str] | None = None
    hypothesis: str | None = None
    evidence: str | None = None
    owner: Literal["agent", "operator"] | None = None
    reason: str = ""


class HypothesisInput(BaseModel):
    """Input schema for the ``hypothesis`` tool."""

    statement: str = Field(min_length=1, max_length=1_000)
    status: _HYPOTHESIS_STATUS = "proposed"
    evidence: str = ""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _plan_summary(items: list[dict], goal: dict | None = None) -> str:
    """Compact plan rendering for tool results / system prompt blocks."""
    if not items and not goal:
        return "The durable plan is empty. Create items with todo_update as you start work."
    goal_line = ""
    if goal:
        goal_line = (
            f"GOAL: {goal.get('objective', '')}\n"
            f"  state={goal.get('state', '')} success={goal.get('success_criteria', [])}"
        )
    rows = []
    for item in items:
        deps = f" deps={item['dependencies']}" if item.get("dependencies") else ""
        hyp = f" hypothesis={item['hypothesis']!r}" if item.get("hypothesis") else ""
        rows.append(
            f"- [{item.get('status', 'pending')}] ({item.get('priority', 'normal')}) {item.get('id', '?')}: {item.get('title', '')}{deps}{hyp}"
        )
    body = "\n".join(rows)
    return f"{goal_line}\n{body}" if goal_line else body


def render_plan_block(plan: dict | None, goal: dict | None = None) -> str:
    """System-prompt block for one model call: current durable plan + goal."""
    if not plan and not goal:
        return ""
    snapshot_goal = (plan or {}).get("goal") or goal
    items = (plan or {}).get("items") or []
    return "## Current durable plan (live)\n" + _plan_summary(items, snapshot_goal)


# ---------------------------------------------------------------------------
# Middleware tools (durable, auditable, envelope-emitting)
# ---------------------------------------------------------------------------


def _emit(envelope: dict) -> None:
    sink = ACTIVE_EMITTER.get()
    if sink is None:
        return
    try:
        sink(envelope)
    except Exception:  # noqa: BLE001 - observability must never sink a run
        logger.debug("plan emitter raised", exc_info=True)


def _active_run_id() -> str:
    from ..middleware.progress_emit import ACTIVE_RUN_ID  # noqa: TID252, PLC0415

    return ACTIVE_RUN_ID.get() or ""


def _apply_ops(ops: list[TodoOp], *, actor: str, run_id: str) -> tuple[list[dict], list[dict]]:
    """Apply ops against the store's current plan.

    Returns ``(items, events)`` where ``events`` are the durable envelopes
    already persisted.  Raises ``ValueError`` with an actionable message on
    the first invalid op.
    """
    store = ACTIVE_STORE.get()
    if store is None:
        raise RuntimeError("plan store unavailable (ACTIVE_STORE context not set)")

    goal = ACTIVE_GOAL.get()
    conversation_id = str((goal or {}).get("conversation_id") or "")
    if not conversation_id:
        raise RuntimeError("goal context missing conversation_id (ACTIVE_GOAL not set)")

    now = _now_ms()
    current = store.plan_items(conversation_id=conversation_id)
    by_id = {item["id"]: item for item in current}
    events: list[dict] = []

    for op in ops:
        op_id = op.item_id
        if op.op == "create":
            if not op.title.strip():
                raise ValueError("todo_update: create requires a non-empty title")
            item_id = op_id or uuid.uuid4().hex[:12]
            if item_id in by_id:
                raise ValueError(f"todo_update: item {item_id} already exists; use edit/set_state")
            item = {
                "id": item_id,
                "title": op.title.strip(),
                "status": "pending",
                "priority": op.priority or "normal",
                "dependencies": list(op.dependencies or []),
                "hypothesis": op.hypothesis or "",
                "evidence": op.evidence or "",
                "owner": op.owner or "agent",
                "created_at_ms": now,
                "updated_at_ms": now,
                "change_reason": op.reason or "",
            }
            by_id[item_id] = item
            events.append({"kind": "todo", "op": "create", "item": item, "reason": op.reason or ""})
            continue
        if op.op == "replan":
            reset = [i for i in by_id.values() if i["status"] not in {"done", "discarded"}]
            for item in reset:
                item["status"] = "pending"
                item["updated_at_ms"] = now
            events.append({"kind": "replan", "reason": op.reason or "", "reset_ids": [i["id"] for i in reset]})
            continue
        if op_id not in by_id:
            raise ValueError(f"todo_update: unknown item_id {op_id!r}")
        item = by_id[op_id]
        if op.op == "edit":
            if not op.title.strip():
                raise ValueError("todo_update: edit requires a non-empty title")
            item["title"] = op.title.strip()
        elif op.op == "set_state":
            assert op.state is not None
            item["status"] = op.state
        elif op.op == "set_priority":
            assert op.priority is not None
            item["priority"] = op.priority
        elif op.op == "link_hypothesis":
            item["hypothesis"] = op.hypothesis or ""
        elif op.op == "attach_evidence":
            item["evidence"] = op.evidence or ""
        elif op.op == "discard":
            item["status"] = "discarded"
        item["change_reason"] = op.reason or ""
        item["updated_at_ms"] = now
        events.append(
            {
                "kind": "todo" if op.op != "discard" else "todo",
                "op": op.op,
                "item": item,
                "reason": op.reason or "",
            }
        )

    for event in events:
        store.append_todo_event(
            run_id=run_id,
            conversation_id=conversation_id,
            goal_id=str((goal or {}).get("id") or ""),
            item_id=str(event.get("item", {}).get("id") or ""),
            op=str(event["op"]),
            item_json=json.dumps(event.get("item", {})),
            reason=str(event.get("reason") or ""),
            actor=actor,
        )
        _emit(redact_secrets(event))
    return list(by_id.values()), events


@tool(description=(
    "Maintain the durable TODO plan. Pass a list of typed ops "
    "(create/edit/set_state/set_priority/link_hypothesis/attach_evidence/discard/replan). "
    "Use it for any multi-step objective: create items BEFORE starting work, mark "
    "set_state=in_progress before executing, attach evidence as you go, and use "
    "replan when a hypothesis fails or the plan needs restructuring. Never rewrite "
    "completed history: update items in place."
))
def todo_update(
    ops: list[TodoOp],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command[Any]:
    """Apply plan ops durably; returns a compact plan summary."""
    try:
        items, _events = _apply_ops(ops, actor="agent", run_id=_active_run_id())
        goal = ACTIVE_GOAL.get()
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content="Plan updated.\n" + _plan_summary(items, goal),
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )
    except (ValueError, RuntimeError) as exc:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=f"Plan update failed: {exc}",
                        tool_call_id=tool_call_id,
                        status="error",
                    )
                ]
            }
        )


@tool(description=(
    "Record a working hypothesis about the target/scope with a validation status "
    "(proposed/confirmed/rejected) and evidence. Link it to plan items via "
    "todo_update link_hypothesis. Use rejected hypotheses to drive replan."
))
def hypothesis(
    statement: str,
    status: str = "proposed",
    evidence: str = "",
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> Command[Any]:
    """Record a hypothesis durably; emits a hypothesis envelope."""
    if status not in {"proposed", "confirmed", "rejected"}:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=f"hypothesis: invalid status {status!r}",
                        tool_call_id=tool_call_id,
                        status="error",
                    )
                ]
            }
        )
    store = ACTIVE_STORE.get()
    goal = ACTIVE_GOAL.get()
    if store is None or goal is None:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content="hypothesis: plan store/goal context unavailable",
                        tool_call_id=tool_call_id,
                        status="error",
                    )
                ]
            }
        )
    conversation_id = str(goal.get("conversation_id") or "")
    run_id = _active_run_id()
    store.append_todo_event(
        run_id=run_id,
        conversation_id=conversation_id,
        goal_id=str(goal.get("id") or ""),
        item_id="",
        op="hypothesis",
        item_json=json.dumps(
            {"statement": statement, "status": status, "evidence": evidence, "ts_ms": _now_ms()}
        ),
        reason="",
        actor="agent",
    )
    _emit(
        redact_secrets(
            {
                "kind": "hypothesis",
                "run_id": run_id,
                "statement": statement,
                "status": status,
                "evidence": evidence,
            }
        )
    )
    return Command(
        update={
            "messages": [
                ToolMessage(content=f"Hypothesis recorded ({status}).", tool_call_id=tool_call_id)
            ]
        }
    )


class TodoPlanMiddleware(AgentMiddleware):
    """Store-backed durable plan + hypothesis surface for the supervisor.

    Constructor args are build-time fallbacks; live values come from the
    ``munin.core.autonomy.context`` contextvars set per invocation by
    ``runtime_adapter.supervisor_runner`` (cached-graph-safe).
    """

    def __init__(self, *, store: Any = None, mode: str | OperationMode = "standard", plan_snapshot: dict | None = None):
        super().__init__()
        self._store_fallback = store
        self._mode_fallback = OperationMode.parse(mode).value
        self._snapshot_fallback = plan_snapshot
        self.tools: list[Any] = [todo_update, hypothesis]

    # -- live resolution ------------------------------------------------

    def _resolve_mode(self) -> OperationMode:
        live = ACTIVE_MODE.get()
        return OperationMode.parse(live if live else self._mode_fallback)

    def _resolve_snapshot(self) -> dict | None:
        live = ACTIVE_PLAN_SNAPSHOT.get()
        return live if live is not None else self._snapshot_fallback

    # -- LangChain hooks --------------------------------------------------

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        new_system = self._compose_system_message(request)
        if new_system is None:
            return handler(request)
        return handler(request.override(system_message=new_system))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        new_system = self._compose_system_message(request)
        if new_system is None:
            return await handler(request)
        return await handler(request.override(system_message=new_system))

    def _compose_system_message(self, request: Any) -> SystemMessage | None:
        policy = parse_mode_policy(self._resolve_mode())
        block = render_plan_block(self._resolve_snapshot(), ACTIVE_GOAL.get())
        if not block and not policy.planning_enabled:
            return None
        extra_blocks: list[dict] = []
        if policy.planning_enabled:
            extra_blocks.append(
                {
                    "type": "text",
                    "text": (
                        "\n\n## Durable plan discipline\n"
                        "- Keep the durable TODO plan current with `todo_update` before and"
                        " during work; track hypotheses with `hypothesis`.\n"
                        "- The plan persists across turns and restarts. Resume where the"
                        " operator left you."
                    ),
                }
            )
        if block:
            extra_blocks.append({"type": "text", "text": f"\n\n{block}"})
        system_message = getattr(request, "system_message", None)
        if system_message is not None:
            content = list(getattr(system_message, "content_blocks", None) or [])
            if isinstance(getattr(system_message, "content", None), list):
                content = list(system_message.content)
            elif isinstance(getattr(system_message, "content", None), str):
                content = [{"type": "text", "text": system_message.content}]
            content = [*content, *extra_blocks]
        else:
            content = extra_blocks
        return SystemMessage(content=content)

    def after_model(self, state: dict, runtime: Any) -> dict | None:
        self._maybe_remind(state)
        return None

    async def aafter_model(self, state: dict, runtime: Any) -> dict | None:
        self._maybe_remind(state)
        return None

    def _maybe_remind(self, state: dict) -> None:
        policy = parse_mode_policy(self._resolve_mode())
        cadence = policy.plan_reminder_every_steps
        if cadence <= 0:
            return
        messages = state.get("messages") or []
        step = sum(1 for msg in messages if getattr(msg, "type", "") == "ai")
        if step and step % cadence == 0:
            _emit(
                {
                    "kind": "note",
                    "run_id": _active_run_id(),
                    "text": "Plan reminder: the durable plan is still live — review todos and continue.",
                }
            )


__all__ = [
    "TodoItem",
    "TodoOp",
    "HypothesisInput",
    "TodoPlanMiddleware",
    "todo_update",
    "hypothesis",
    "render_plan_block",
    "PLAN_TOOL_NAME",
    "HYPOTHESIS_TOOL_NAME",
]
