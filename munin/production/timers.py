# tags: [timers, core, runtime, orchestrator, coordination, subagent, hitl-approval, TimerScheduler, _emit_tick, TIMER_POLL_SECONDS, TIMER_WAKEUP_ENABLED, fencing-epoch, goal-evaluation, timer-ticks, lease-fencing]
"""
Durable server-side timers (autonomous modes, issue #14).

A timer is a durable alarm row in the production store, fenced with the same
lease/fencing-epoch discipline as ``agent_runs``.  The scheduler loop below is
the ONLY timer consumer: it claims due timers atomically (``fencing_epoch``
bump + fresh ``lease_token``), records the tick, and — for GOAL-mode
wake-up timers with ``payload.wakeup`` — launches an evaluation run through
the SAME execution path as a normal operator turn (``_launch_chat_run``), so
every side effect still passes the mode's approval gates, scope, preflight
and audit.  A timer never executes a sensitive action by itself; at most it
resumes a governed evaluation turn.

Env knobs (observable, anti-runaway only):
* ``MUNIN_TIMER_POLL_SECONDS`` (default 5) — scheduler poll cadence.
* ``MUNIN_TIMER_WAKEUP_ENABLED`` (default 1) — master switch for GOAL
  wake-up evaluation runs (ticks still fire + notify).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

TIMER_POLL_SECONDS: float = max(0.5, float(os.environ.get("MUNIN_TIMER_POLL_SECONDS", "5")))
TIMER_WAKEUP_ENABLED: bool = os.environ.get("MUNIN_TIMER_WAKEUP_ENABLED", "1") not in {"", "0", "false", "no"}
TIMER_LEASE_MS: int = max(10_000, int(os.environ.get("MUNIN_TIMER_LEASE_SECONDS", "60")) * 1000)

NON_TERMINAL_RUN_STATES = frozenset({"queued", "running", "waiting_for_human"})

_WAKEUP_MESSAGE = (
    "[munin:timer] Goal evaluation tick #{tick} — the persistent goal is still "
    "active. Review the durable plan, update it, and continue toward the goal. "
    "If the goal is complete or unreachable, say so explicitly."
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _emit_tick(timer: dict[str, Any]) -> dict[str, Any]:
    """Build the observable ``timer_tick`` event (no side effects)."""
    return {
        "kind": "timer_tick",
        "run_id": "",
        "timer_id": timer["id"],
        "goal_id": timer.get("goal_id"),
        "conversation_id": timer["conversation_id"],
        "timer_kind": timer["kind"],
        "tick_count": int(timer.get("tick_count") or 0),
        "due_at_ms": int(timer.get("due_at_ms") or 0),
        "last_tick_at_ms": int(timer.get("last_tick_at_ms") or 0),
        "state": "active",
    }


async def _dispatch_tick(*, store: Any, shared_state: Any, timer: dict[str, Any]) -> None:
    """Handle one claimed timer tick: notify + optional GOAL wake-up."""
    goal = None
    try:
        goal = store.get_goal_for_conversation(conversation_id=timer["conversation_id"])
    except Exception:  # noqa: BLE001
        logger.debug("timer: goal hydration failed timer_id=%s", timer["id"], exc_info=True)

    should_wake = (
        TIMER_WAKEUP_ENABLED
        and timer["kind"] == "goal_eval"
        and bool((timer.get("payload") or {}).get("wakeup", True))
        and goal is not None
        and str(goal.get("state") or "") == "active"
    )
    if not should_wake:
        return

    # Never start a second run while another turn is still active in the
    # conversation — the wake-up only fills idle time.
    try:
        aggregate = store.get_conversation(
            actor_id=str(goal["actor_id"]), conversation_id=timer["conversation_id"]
        )
        for run in aggregate.get("runs", []):
            if run.get("state") in NON_TERMINAL_RUN_STATES:
                return
    except Exception:  # noqa: BLE001 - fail safe: skip the wake-up
        logger.debug("timer: wake-up conversation check failed timer_id=%s", timer["id"], exc_info=True)
        return

    # The wake-up is a normal governed turn: same create_turn/claim/launch
    # path as an operator message, with a unique per-tick idempotency key.
    tick = int(timer.get("tick_count") or 0)
    idempotency_key = f"timer:{timer['id']}:{tick}"
    content = _WAKEUP_MESSAGE.format(tick=tick)
    try:
        turn = store.create_turn(
            actor_id=str(goal["actor_id"]),
            conversation_id=timer["conversation_id"],
            content=content,
            idempotency_key=idempotency_key,
            mode=str(goal.get("mode") or "goal"),
            goal_id=goal["id"],
        )
        if turn.get("idempotent_replay"):
            return
        from .chat import _claim_direct, _launch_chat_run  # noqa: PLC0415

        run_id = str(turn["run"]["id"])
        lease_token, _assistant_id = _claim_direct(store, run_id=run_id)
        from ..core.autonomy.modes import OperationMode  # noqa: PLC0415

        _launch_chat_run(
            store=store,
            shared_state=shared_state,
            actor_info={"id": str(goal["actor_id"])},
            run_id=run_id,
            conversation_id=timer["conversation_id"],
            prompt=content,
            conversation_history=[],
            assistant_message_id=str(turn["assistant_message_id"]),
            lease_token=lease_token,
            mode=OperationMode.parse(goal.get("mode")),
            goal=goal,
        )
        logger.info(
            "timer: goal wake-up launched timer_id=%s tick=%d run_id=%s",
            timer["id"], tick, run_id,
        )
    except Exception:  # noqa: BLE001 - one bad wake-up must not stop the loop
        logger.warning("timer: goal wake-up failed timer_id=%s", timer["id"], exc_info=True)


async def timer_tick_loop(*, store: Any, shared_state: Any) -> None:
    """Poll due timers, tick them durably, dispatch GOAL wake-ups."""
    while True:
        try:
            claimed = store.claim_due_timers(
                worker_id=f"timer-{os.getpid()}",
                lease_ms=TIMER_LEASE_MS,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one failed scan must not stop the loop
            logger.exception("timer: claim scan failed")
            claimed = []

        for timer in claimed:
            now = _now_ms()
            tick = int(timer.get("tick_count") or 0) + 1
            next_due = now + max(5_000, int(timer.get("cadence_ms") or 5_000))
            try:
                ok = store.complete_timer_tick(
                    timer_id=timer["id"],
                    fencing_epoch=int(timer.get("fencing_epoch") or 0),
                    last_tick_at_ms=now,
                    next_due_at_ms=next_due,
                    tick_count=tick,
                )
                if not ok:
                    logger.debug("timer: tick lost fencing race timer_id=%s", timer["id"])
                    continue
            except Exception:  # noqa: BLE001
                logger.exception("timer: tick persist failed timer_id=%s", timer["id"])
                continue

            timer["tick_count"] = tick
            timer["last_tick_at_ms"] = now
            timer["due_at_ms"] = next_due
            logger.info("timer: tick timer_id=%s kind=%s tick=%d", timer["id"], timer.get("kind"), tick)
            try:
                await _dispatch_tick(store=store, shared_state=shared_state, timer=timer)
            except Exception:  # noqa: BLE001 - dispatch must never kill the loop
                logger.warning("timer: dispatch failed timer_id=%s", timer["id"], exc_info=True)

        await asyncio.sleep(TIMER_POLL_SECONDS)


def start_timer_worker(*, store: Any, shared_state: Any) -> asyncio.Task[None]:
    """Create the process-local timer scheduler task."""
    return asyncio.create_task(
        timer_tick_loop(store=store, shared_state=shared_state),
        name="munin-timers",
    )


__all__ = [
    "timer_tick_loop",
    "start_timer_worker",
    "TIMER_POLL_SECONDS",
    "TIMER_WAKEUP_ENABLED",
    "TIMER_LEASE_MS",
]
