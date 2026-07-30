"""Orchestrator — Munin's wake/sleep and graph lifecycle controller.

Design:
- Wake a subagent by enqueueing a task in ``agent_wake_queue``.
- Immediately spawn a subprocess `python -m munin.subagents.runner <name>`; the
  subprocess claims the wake item and executes it, publishing progress via
  `shared_intel` / `agent_messages` and finishing with `complete_shared_task`.
- Sleep is implicit: the subprocess ends when the wake queue is empty for that agent
  and after `sleep_after_idle_seconds` of inactivity.
- Forged graphs (built by `graph_forge`) are stored in ``generated_graphs`` and
  woken the same way — the runner reads that table and builds a `create_react_agent`
  from the stored spec on the fly.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from typing import Any

from ..mcp.shared_state import SharedStateStore

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
        wake_id = self.state.enqueue_wake(target_agent=subagent_name.strip(), task=task, priority=priority)
        pid = self._spawn_runner(subagent_name, detached=detached)
        self.state.upsert_presence(
            agent_name=subagent_name,
            role=task.get("role", ""),
            status="RUNNING",
            current_task_id=None,
            metadata_json=json.dumps({"wake_id": wake_id, "pid": pid}, ensure_ascii=True),
        )
        return {"wake_id": wake_id, "target_agent": subagent_name, "pid": pid}

    def sleep(self, subagent_name: str) -> dict[str, Any]:
        """Mark presence as IDLE. The runner subprocess exits on its own when the
        wake queue empties; we don't kill it here (that would race against in-flight
        work)."""
        self.state.upsert_presence(
            agent_name=subagent_name,
            role="",
            status="IDLE",
            current_task_id=None,
            metadata_json="{}",
        )
        return {"target_agent": subagent_name, "status": "IDLE"}

    def _spawn_runner(self, subagent_name: str, *, detached: bool) -> int:
        cmd = [sys.executable, "-m", "munin.subagents.runner", subagent_name]
        popen_kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL}
        if detached:
            popen_kwargs["stdout"] = subprocess.DEVNULL
            popen_kwargs["stderr"] = subprocess.DEVNULL
            if os.name == "posix":
                popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(cmd, **popen_kwargs)
        logger.info("spawned subagent runner %s pid=%d", subagent_name, proc.pid)
        return proc.pid

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    def presence(self) -> list[dict[str, Any]]:
        return self.state.list_presence(stale_after_seconds=1800)

    def queue(self, *, subagent_name: str = "", include_claimed: bool = False) -> list[dict[str, Any]]:
        return self.state.list_wake_queue(target_agent=subagent_name, include_claimed=include_claimed)

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
    ) -> dict[str, Any]:
        return self.state.graph_register(
            name=name,
            purpose=purpose,
            system_prompt=system_prompt,
            tool_whitelist=tool_whitelist,
            reset_policy=reset_policy,
            created_by_agent=created_by_agent,
        )

    def list_graphs(self, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        return self.state.graph_list(include_inactive=include_inactive)

    def drop_graph(self, name: str) -> bool:
        return self.state.graph_drop(name)
