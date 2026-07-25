"""Subagent runner — invoked as ``python -m munin.subagents.runner <name>``.

The Orchestrator spawns this as a subprocess. It:
1. Boots settings + shared state.
2. Registers the subagent's presence as RUNNING.
3. Claims wake items from ``agent_wake_queue`` addressed to ``<name>``.
4. Dispatches each task to the concrete subagent class (each runs a full ReAct loop).
5. Sleeps (exits) after ``sleep_after_idle_seconds`` with an empty queue.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from typing import Any

from ..mcp.config import get_settings
from ..mcp.shared_state import SharedStateStore
from .base import ReActSubagentBase

logging.basicConfig(level=logging.INFO, format="[munin-runner] %(levelname)s %(message)s")
logger = logging.getLogger("munin.runner")


def _load_subagent(name: str, state: SharedStateStore) -> Any:
    """Return an instance of the concrete subagent class for `name`.

    Native subagents live under ``munin.subagents.<name>``; forged graphs are looked
    up in the ``generated_graphs`` table.
    """
    if name == "ldap_agent":
        from .ldap_agent import LDAPSubagent

        return LDAPSubagent(state)
    if name == "tool_forge":
        from .tool_forge import ToolForgeSubagent

        return _WrappedToolForge(ToolForgeSubagent(state))
    if name == "graph_forge":
        from .graph_forge import GraphForgeSubagent

        return _WrappedGraphForge(GraphForgeSubagent(state))

    graph = state.graph_get(name)
    if graph:
        return _ForgedGraphRunner(state=state, graph=graph)

    raise SystemExit(f"unknown subagent: {name}")


class _WrappedToolForge:
    name = "tool_forge"
    role = "tool_forge"

    def __init__(self, forger: Any) -> None:
        self.forger = forger

    def handle_task(self, task: dict[str, Any]) -> dict[str, Any]:
        spec = str(task.get("spec", "")).strip()
        max_iters = int(task.get("max_iterations", 5))
        self.forger.max_iterations = max_iters
        return self.forger.forge(spec)


class _WrappedGraphForge:
    name = "graph_forge"
    role = "graph_forge"

    def __init__(self, forger: Any) -> None:
        self.forger = forger

    def handle_task(self, task: dict[str, Any]) -> dict[str, Any]:
        return self.forger.forge(
            name=task["name"],
            purpose=task["purpose"],
            hints=task.get("hints", []),
            tool_whitelist=task.get("tool_whitelist", []),
        )


class _ForgedGraphRunner(ReActSubagentBase):
    """Executes a forged-graph subagent as a full ReAct loop.

    The graph config provides the system prompt and tool whitelist.
    All tools in the whitelist are resolved against the master catalog in
    base.py — so forged graphs can use LDAP, intel, memory, and messaging
    tools just like any native subagent.
    """

    role = "forged_graph"

    def __init__(self, *, state: SharedStateStore, graph: dict[str, Any]) -> None:
        self.name = graph["name"]
        self.system_prompt = graph.get("system_prompt", "Complete the task using your tools.")
        self.allowed_tools = set(graph.get("tool_whitelist") or [])
        # Always give forged graphs messaging tools so they can talk to munin
        self.allowed_tools |= {"post_agent_message", "fetch_agent_messages", "memory_remember", "memory_recall"}
        super().__init__(state)


def main() -> None:
    parser = argparse.ArgumentParser(description="Munin subagent runner")
    parser.add_argument("name", help="Subagent name (ldap_agent, tool_forge, graph_forge, or a forged-graph name)")
    parser.add_argument("--sleep-after-idle", type=int, default=120, help="Seconds of empty queue before exiting")
    parser.add_argument("--poll-interval", type=int, default=2, help="Seconds between queue polls")
    args = parser.parse_args()

    settings = get_settings()
    state = SharedStateStore(settings)
    subagent = _load_subagent(args.name, state)
    pid = os.getpid()
    state.upsert_presence(agent_name=args.name, role=getattr(subagent, "role", ""), status="RUNNING", current_task_id=None, metadata_json=json.dumps({"pid": pid}, ensure_ascii=True))
    logger.info("subagent %s started pid=%d", args.name, pid)

    idle_since: float | None = None
    while True:
        item = state.claim_wake_item(target_agent=args.name, claimer_pid=pid)
        if not item:
            if idle_since is None:
                idle_since = time.monotonic()
            elif time.monotonic() - idle_since > args.sleep_after_idle:
                logger.info("subagent %s idle for %ds — sleeping", args.name, args.sleep_after_idle)
                break
            time.sleep(args.poll_interval)
            continue
        idle_since = None
        wake_id = item["id"]
        task = item["task"]
        logger.info("subagent %s handling wake %d task=%s", args.name, wake_id, json.dumps(task)[:200])
        try:
            result = subagent.handle_task(task)
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "summary": "handler crashed", "error": {"code": "handler_crashed", "message": str(exc)}}
        state.episodic_record(
            agent=args.name,
            action="wake_handled",
            input_data={"wake_id": wake_id, "task": task},
            output_data={"ok": result.get("ok"), "summary": result.get("summary")},
            tags=["wake"],
        )
        # Broadcast the result to `munin` (the parent) so the core agent can consume it.
        state.post_message(
            sender_agent=args.name,
            recipient_agent="munin",
            subject=f"wake_{wake_id} result",
            message_type="RESULT" if result.get("ok") else "ERROR",
            body=json.dumps(result, ensure_ascii=True, default=str)[:6000],
            related_task_id=None,
            related_target_ip="",
            metadata_json=json.dumps({"wake_id": wake_id}, ensure_ascii=True),
        )

    state.upsert_presence(agent_name=args.name, role=getattr(subagent, "role", ""), status="IDLE", current_task_id=None, metadata_json="{}")


if __name__ == "__main__":
    main()
