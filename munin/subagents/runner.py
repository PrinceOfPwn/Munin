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
import re
import threading
import time
from typing import Any

from ..mcp.audit import redact_secrets
from ..mcp.config import get_settings
from ..mcp.shared_state import (
    PRESENCE_LEASE_SECONDS,
    SharedStateStore,
    presence_metadata,
)
from ..mcp.tools import hugin_tool
from .base import ReActSubagentBase

logging.basicConfig(level=logging.INFO, format="[munin-runner] %(levelname)s %(message)s")
logger = logging.getLogger("munin.runner")


# Native subagents baked into the codebase. Anything else must live in the
# `generated_graphs` table (created via graph_forge). Exposed as a module-level
# constant so `munin_wake` can validate the target before spawning a subprocess.
_NATIVE_SUBAGENTS: frozenset[str] = frozenset({"ldap_agent", "tool_forge", "graph_forge"})


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

        return _WrappedToolForge(ToolForgeSubagent(state), state)
    if name == "graph_forge":
        from .graph_forge import GraphForgeSubagent

        return _WrappedGraphForge(GraphForgeSubagent(state), state)

    graph = state.graph_get(name)
    if graph:
        return _ForgedGraphRunner(state=state, graph=graph)

    raise SystemExit(f"unknown subagent: {name}")


class _WrappedToolForge:
    """Wrap ToolForgeSubagent for the runner loop.

    Runs the forge, then persists the tool via ``registry.register`` so it's
    hot-loaded into MCP and survives server restarts. Without this final step
    (the bug it fixes) wake-based forging produced a script on disk that was
    never registered — the tool was effectively invisible.

    The parent (`munin`) picks up the outcome via an ``agent_messages`` RESULT
    post that the runner main-loop sends after ``handle_task`` returns.
    """

    name = "tool_forge"
    role = "tool_forge"

    def __init__(self, forger: Any, state: SharedStateStore) -> None:
        self.forger = forger
        self.state = state

    def handle_task(self, task: dict[str, Any]) -> dict[str, Any]:
        spec = str(task.get("spec", "")).strip()
        max_iters = int(task.get("max_iterations", 5))
        self.forger.max_iterations = max_iters
        outcome = self.forger.forge(spec)
        if not outcome.get("ok"):
            return outcome

        # We register on the STATE table (persistence + hot-load on next start).
        # We can't hot-load into the *this* subprocess's MCP because the MCP
        # server is a separate process; the parent server picks it up on the
        # next `registry.rehydrate()` (called at startup) or when tool_forge
        # is called via MCP tool. For a live registration we post a special
        # message to munin so it can trigger reload if it wants.
        try:
            from ..mcp import registry  # noqa: TID252,PLC0415
            registered = registry.register_state_only(
                self.state,
                slug=outcome["slug"],
                description=outcome.get("description", spec),
                script_path=outcome["script_path"],
                function_name=outcome["function_name"],
                signature=outcome.get("signature", {}),
                tags=outcome.get("tags", []),
                created_by_agent="tool_forge",
            )
            outcome["registered"] = registered
        except Exception as exc:  # pragma: no cover — best effort
            outcome["registration_error"] = str(exc)

        # Persist the generated .py to the repo (runner mode only).
        try:
            from ..mcp import git_persist  # noqa: TID252,PLC0415
            git_persist.commit_forged_tool(
                script_path=outcome["script_path"],
                tool_name=outcome.get("registered", {}).get("name") or outcome.get("slug", "unknown"),
                description=outcome.get("description", spec)[:200],
            )
        except Exception as exc:  # pragma: no cover
            outcome.setdefault("registration_error", str(exc))
        return outcome


class _WrappedGraphForge:
    """Wrap GraphForgeSubagent for the runner loop.

    Runs the forge and persists the graph config via ``state.graph_register``.
    Without this final step, wake-based graph forging produced JSON that was
    never stored — the graph name was unresolvable at wake time.
    """

    name = "graph_forge"
    role = "graph_forge"

    def __init__(self, forger: Any, state: SharedStateStore) -> None:
        self.forger = forger
        self.state = state

    def handle_task(self, task: dict[str, Any]) -> dict[str, Any]:
        outcome = self.forger.forge(
            name=task["name"],
            purpose=task["purpose"],
            hints=task.get("hints", []),
            tool_whitelist=task.get("tool_whitelist", []),
        )
        if not outcome.get("ok"):
            return outcome
        try:
            record = self.state.graph_register(
                name=outcome["name"],
                purpose=outcome["purpose"],
                system_prompt=outcome["system_prompt"],
                tool_whitelist=outcome["tool_whitelist"],
                reset_policy=task.get("reset_policy", "on_reset"),
                created_by_agent=task.get("created_by_agent", "graph_forge"),
                execution_contract=outcome.get("execution_contract", {}),
            )
            outcome["registered"] = record
            from ..mcp.graph_persist import persist_graph_manifest  # noqa: PLC0415,TID252

            graph = self.state.graph_get(outcome["name"])
            outcome["manifest_path"] = str(
                persist_graph_manifest(self.state.settings, graph or outcome)
            )
        except Exception as exc:  # pragma: no cover — best effort
            outcome["registration_error"] = str(exc)
        return outcome


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
        self.execution_contract = graph.get("execution_contract") or {
            "version": 1,
            "mode": "evidence_mesh",
            "context_sources": ["semantic_memory", "shared_intel"],
            "human_checkpoints": ["scope_unclear", "before_active_or_irreversible_action"],
            "delivery": {"format": "markdown", "sections": ["Summary", "Evidence", "Next steps"]},
            "termination": {"max_iterations": 8},
        }
        delivery = self.execution_contract.get("delivery", {}) if isinstance(self.execution_contract, dict) else {}
        sections = delivery.get("sections", ["Summary", "Evidence", "Next steps"]) if isinstance(delivery, dict) else []
        self.system_prompt = (
            graph.get("system_prompt", "Complete the task using your tools.")
            + "\n\n## Evidence Mesh 执行合同\n"
            + "任务开始时提供的 evidence capsule 只是带来源的线索，不是无需验证的事实。"
            + "在交接中引用相关 evidence。若范围不清楚，或需要 active/irreversible step，"
            + "用简体中文向父代理请求批准并等待。完成后按以下语义章节交付："
            + ", ".join(str(section) for section in sections)
            + "。交付 evidence-backed result 或精确 blocker 后立即停止，不得无限轮询。"
        )
        self.allowed_tools = set(graph.get("tool_whitelist") or [])
        # Always give forged graphs messaging tools so they can talk to munin
        self.allowed_tools |= {"post_agent_message", "fetch_agent_messages", "memory_remember", "memory_recall"}
        super().__init__(state)

    def _task_context(self, task: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Build the compact, source-labelled context packet for a graph run."""
        contract = self.execution_contract if isinstance(self.execution_contract, dict) else {}
        sources = [str(item) for item in contract.get("context_sources", [])]
        prompt = str(task.get("prompt") or json.dumps(task, ensure_ascii=True, default=str))
        packet: dict[str, Any] = {
            "graph": self.name,
            "context_sources": sources,
            "facts": [],
            "shared_intel": [],
            "hugin": [],
        }
        if "semantic_memory" in sources:
            packet["facts"] = redact_secrets(self.state.semantic_list(limit=12))
        if "shared_intel" in sources:
            packet["shared_intel"] = redact_secrets(self.state.query_intel(limit=12))
        if "hugin_cached_graph" in sources:
            try:
                bundle, age, stale = hugin_tool._load_cached(allow_stale=True)  # noqa: SLF001 - no hidden network refresh
                tokens = {token.lower() for token in re.findall(r"[A-Za-z0-9_-]{4,}", prompt)}
                for entity in (bundle or {}).get("entities", []):
                    rendered = json.dumps(entity, ensure_ascii=False, default=str).lower()
                    if tokens and not any(token in rendered for token in tokens):
                        continue
                    packet["hugin"].append(
                        {
                            "id": entity.get("id"),
                            "label": entity.get("label") or entity.get("title"),
                            "category": entity.get("category"),
                            "tags": entity.get("tags", []),
                            "source_url": entity.get("source_url") or entity.get("url"),
                        }
                    )
                    if len(packet["hugin"]) >= 6:
                        break
                packet["hugin_cache"] = {"age_seconds": age, "is_stale": stale}
            except Exception as exc:
                packet["hugin_cache"] = {"error": str(exc)}

        meta = {
            "context_sources": sources,
            "fact_count": len(packet["facts"]),
            "intel_count": len(packet["shared_intel"]),
            "hugin_count": len(packet["hugin"]),
        }
        return (
            "## Evidence Mesh context / 上下文（已标注来源；行动前验证）\n"
            + json.dumps(packet, ensure_ascii=False, default=str)[:12_000],
            meta,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Munin subagent runner")
    parser.add_argument("name", help="Subagent name (ldap_agent, tool_forge, graph_forge, or a forged-graph name)")
    parser.add_argument("--sleep-after-idle", type=int, default=120, help="Seconds of empty queue before exiting")
    parser.add_argument("--poll-interval", type=int, default=2, help="Seconds between queue polls")
    args = parser.parse_args()

    settings = get_settings()
    state = SharedStateStore(settings)
    from ..mcp.graph_persist import rehydrate_graph_manifests  # noqa: PLC0415,TID252

    rehydrate_graph_manifests(state, settings)
    subagent = _load_subagent(args.name, state)
    pid = os.getpid()
    role = getattr(subagent, "role", "")

    def set_presence(status: str, *, lease_seconds: int = PRESENCE_LEASE_SECONDS) -> None:
        state.upsert_presence(
            agent_name=args.name,
            role=role,
            status=status,
            current_task_id=None,
            metadata_json=json.dumps(
                presence_metadata(pid, lease_seconds=lease_seconds),
                ensure_ascii=True,
            ),
        )

    set_presence("RUNNING")
    logger.info("subagent %s started pid=%d", args.name, pid)

    idle_since: float | None = None
    while True:
        item = state.claim_wake_item(target_agent=args.name, claimer_pid=pid)
        if not item:
            set_presence("IDLE")
            if idle_since is None:
                idle_since = time.monotonic()
            elif time.monotonic() - idle_since > args.sleep_after_idle:
                # About to exit. Race window: between our last claim and now,
                # `munin_wake` may have enqueued an item AND observed our
                # presence row still RUNNING → decided NOT to spawn a fresh
                # runner. That item would sit orphaned forever. Close the
                # window in three steps:
                #   1. Mark presence as EXITING so any wake happening RIGHT
                #      NOW sees a "not-alive" slot and spawns fresh.
                #   2. Do one last claim attempt to sweep anything enqueued
                #      during (1) or the previous poll.
                #   3. Only then break.
                set_presence("EXITING")
                # Small pause so any concurrent `munin_wake` finishes its
                # presence read before we make our final sweep.
                time.sleep(0.1)
                item = state.claim_wake_item(target_agent=args.name, claimer_pid=pid)
                if not item:
                    logger.info("subagent %s idle for %ds — sleeping", args.name, args.sleep_after_idle)
                    break
                # Fell into a last-second wake — reset the idle clock and process it.
                logger.info("subagent %s caught a wake at exit-time — processing it", args.name)
                idle_since = None
                # Presence is now EXITING; upgrade back to RUNNING while we work.
                set_presence("RUNNING")
            else:
                time.sleep(args.poll_interval)
                continue
        idle_since = None
        wake_id = item["id"]
        task = item["task"]
        logger.info("subagent %s handling wake %d task=%s", args.name, wake_id, json.dumps(task)[:200])
        set_presence("RUNNING")
        heartbeat_stop = threading.Event()

        def heartbeat(stop_event: threading.Event = heartbeat_stop) -> None:
            while not stop_event.wait(max(1, PRESENCE_LEASE_SECONDS // 3)):
                set_presence("RUNNING")

        heartbeat_thread = threading.Thread(
            target=heartbeat,
            name=f"munin-presence-{args.name}",
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            result = subagent.handle_task(task)
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "summary": "handler crashed", "error": {"code": "handler_crashed", "message": str(exc)}}
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2)
            set_presence("IDLE")
        state.episodic_record(
            agent=args.name,
            action="wake_handled",
            input_data={"wake_id": wake_id, "task": task},
            output_data={"ok": result.get("ok"), "summary": result.get("summary")},
            tags=["wake"],
        )
        # Broadcast the result to `munin` (the parent) so the core agent can consume it.
        # Previously we sliced the body to 6000 bytes — that chopped mid-JSON and made
        # `json.loads` on the receiver fail silently. Now we serialize the full result,
        # and only if it's too big for a single message do we spill to an artifact file
        # and send a pointer instead. Munin can `munin_read_source` (path-restricted)
        # or just consume the summary.
        body_full = json.dumps(result, ensure_ascii=True, default=str)
        MAX_INLINE_BODY = 12000  # SQLite TEXT can hold much more; this is comfort for MCP UIs
        artifact_ref: str = ""
        if len(body_full) > MAX_INLINE_BODY:
            artifacts_dir = settings.munin_data_path / "wake_artifacts"
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            artifact_path = artifacts_dir / f"wake_{wake_id}.json"
            artifact_path.write_text(body_full, encoding="utf-8")
            artifact_ref = str(artifact_path)
            # Compose a body that is itself valid JSON — Munin can safely json.loads it.
            body_for_msg = json.dumps(
                {
                    "ok": result.get("ok"),
                    "summary": result.get("summary", ""),
                    "wake_id": wake_id,
                    "artifact_path": artifact_ref,
                    "artifact_size_bytes": len(body_full),
                    "note": f"full result too large; call read_wake_artifact(wake_id={wake_id})",
                },
                ensure_ascii=True,
            )
        else:
            body_for_msg = body_full

        state.post_message(
            sender_agent=args.name,
            recipient_agent="munin",
            subject=f"wake_{wake_id} result",
            message_type="RESULT" if result.get("ok") else "ERROR",
            body=body_for_msg,
            related_task_id=None,
            related_target_ip="",
            metadata_json=json.dumps(
                {"wake_id": wake_id, "artifact_path": artifact_ref} if artifact_ref else {"wake_id": wake_id},
                ensure_ascii=True,
            ),
        )

    set_presence("IDLE", lease_seconds=0)


if __name__ == "__main__":
    main()
