"""
Autonomy per-invocation context (contextvars).

The supervisor graph is built once per fingerprint and cached process-wide
(``munin.core.supervisor``).  Autonomy middleware instances are therefore
SHARED across runs, exactly like ``ProgressEmitMiddleware`` and
``OperatorGuidanceMiddleware``.  ``runtime_adapter.supervisor_runner`` sets
these contextvars for every invocation so the middleware can recover the
live store, operation mode, goal and plan snapshot for *this* run.
"""
from __future__ import annotations

import contextvars
from collections.abc import Callable
from typing import Any

ACTIVE_STORE: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "munin_autonomy_active_store", default=None
)
ACTIVE_MODE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "munin_autonomy_active_mode", default=None
)
ACTIVE_GOAL: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "munin_autonomy_active_goal", default=None
)
ACTIVE_PLAN_SNAPSHOT: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "munin_autonomy_active_plan_snapshot", default=None
)
ACTIVE_EMITTER: contextvars.ContextVar[Callable[[dict], None] | None] = contextvars.ContextVar(
    "munin_autonomy_active_emitter", default=None
)

__all__ = [
    "ACTIVE_STORE",
    "ACTIVE_MODE",
    "ACTIVE_GOAL",
    "ACTIVE_PLAN_SNAPSHOT",
    "ACTIVE_EMITTER",
]
