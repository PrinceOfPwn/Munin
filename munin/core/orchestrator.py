# tags: [orchestrator, core, subagent, presence, worker-fanout, Orchestrator, enqueue_wake, _spawn_runner, wake, sleep, agent_wake_queue, supervisor-v2, SharedStateStore, presence_metadata]
"""Orchestrator — Munin's wake/sleep and graph lifecycle controller.

Design:
- Wake a subagent by enqueueing a durable task in ``agent_wake_queue``.
- ``supervisor_v2`` owns execution; it does not spawn ``munin.subagents.runner``.
  Presence is best-effort observability and must never invalidate a successful
  queue insert.
- Sleep marks presence IDLE; queued work remains durable until a supervisor
  claims it.
- Forged graphs (built by ``graph_forge``) are stored in ``generated_graphs``
  and consumed by the same supervisor path.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from typing import Any

from ..mcp.shared_state import SharedStateStore, get_instance_id, presence_metadata

logger = logging.getLogger("munin.orchestrator")


class Orchestrator:
    def __init__(self, state: SharedStateStore) -> None:
        self.state = state

    # ------------------------------------------------------------------
    # Wake / sleep
    # ------------------------------------------------------------------
    def wake(
        self,
        subagent_name: str,
        task: dict[str, Any],
        *,
        priority: int = 0,
        detached: bool = True,
    ) -> dict[str, Any]:
        """Queue one supervisor_v2 wake without claiming a legacy spawn slot.

        ``enqueue_wake`` is the authoritative operation. Once it returns a
        ``wake_id`` the task exists, so a later presence refresh failure must
        not bubble up as ``wake_enqueue_failed`` and tempt callers to enqueue a
        duplicate. ``detached`` remains accepted for API compatibility with
        the supervisor_v1 caller but has no effect in supervisor_v2.
        """
        del detached
        normalized_name = subagent_name.strip()
        wake_id = self.state.enqueue_wake(
            target_agent=normalized_name,
            task=task,
            priority=priority,
        )
        result: dict[str, Any] = {
            "wake_id": wake_id,
            "target_agent": normalized_name,
            "pid": None,
            "spawned": False,
            "queued": True,
            "presence_updated": True,
            "reason": "supervisor_v2_wake_path",
        }
        try:
            self.state.upsert_presence(
                agent_name=normalized_name,
                role="",
                status="IDLE",
                current_task_id=None,
                metadata_json=json.dumps(
                    presence_metadata(os.getpid(), lease_seconds=0)
                ),
            )
        except Exception as exc:  # noqa: BLE001 - queue success is authoritative
            logger.warning(
                "wake %s queued as %s but presence refresh failed: %s",
                normalized_name,
                wake_id,
                exc,
            )
            result["presence_updated"] = False
            result["warning"] = {
                "code": "presence_update_failed",
                "message": str(exc),
            }
        return result

    def sleep(self, subagent_name: str) -> dict[str, Any]:
        """Mark presence IDLE without affecting durable queued work."""
        self.state.upsert_presence(
            agent_name=subagent_name,
            role="",
            status="IDLE",
            current_task_id=None,
            metadata_json=json.dumps(presence_metadata(os.getpid(), lease_seconds=0)),
        )
        return {"target_agent": subagent_name, "status": "IDLE"}

    def _spawn_runner(self, subagent_name: str, *, detached: bool) -> int:
        """Legacy supervisor_v1 runner spawner retained for compatibility."""
        cmd = [sys.executable, "-m", "munin.subagents.runner", subagent_name]
        popen_kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL}
        if detached:
            popen_kwargs["stdout"] = subprocess.DEVNULL
            popen_kwargs["stderr"] = subprocess.DEVNULL
            if os.name == "posix":
                popen_kwargs["start_new_session"] = True
        child_env = os.environ.copy()
        child_env["MUNIN_INSTANCE_ID"] = get_instance_id()
        popen_kwargs["env"] = child_env
        proc = subprocess.Popen(cmd, **popen_kwargs)
        logger.info("spawned subagent runner %s pid=%d", subagent_name, proc.pid)
        return proc.pid

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    def presence(self) -> list[dict[str, Any]]:
        return self.state.list_presence(stale_after_seconds=1800)

    def queue(
        self, *, subagent_name: str = "", include_claimed: bool = False
    ) -> list[dict[str, Any]]:
        return self.state.list_wake_queue(
            target_agent=subagent_name, include_claimed=include_claimed
        )

    # ------------------------------------------------------------------
    # Graph lifecycle (delegates to state, kept here for API symmetry)
    # ------------------------------------------------------------------
    def forge_graph(
        self,
        *,
        name: str,
        purpose: str,
        system_prompt: str,
        tool_whitelist: list[str],
        reset_policy: str = "on_reset",
        created_by_agent: str = "munin",
        execution_contract: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.state.graph_register(
            name=name,
            purpose=purpose,
            system_prompt=system_prompt,
            tool_whitelist=tool_whitelist,
            reset_policy=reset_policy,
            created_by_agent=created_by_agent,
            execution_contract=execution_contract,
        )

    def list_graphs(self, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        return self.state.graph_list(include_inactive=include_inactive)

    def drop_graph(self, name: str) -> bool:
        return self.state.graph_drop(name)
