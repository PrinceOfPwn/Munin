"""Base class for Munin ReAct subagents.

Every concrete subagent declares:
  - ``name``          — identity used in SQLite presence + messages
  - ``role``          — human-readable role label
  - ``system_prompt`` — injected as the system message in every ReAct loop
  - ``allowed_tools`` — whitelist enforced at catalog-build time

The base class provides a complete ReAct loop (LLM → tool call → feed back →
repeat) over a filtered tool catalog built from SQLite-bound wrappers — no HTTP
roundtrips, no global singletons, safe to run in subprocess.

Tool categories available:
  - LDAP (8 tools)
  - External intel: Tavily, Hugin (3 tools)
  - Memory: semantic + episodic (4 tools)
  - Agent messaging: post/fetch/ack + presence (4 tools)
  - Shared intel: publish/query (2 tools)
  - Shared tasks: claim/heartbeat/complete/list (4 tools)
  - Wake queue: munin_wake / munin_wake_list (2 tools)
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import time
from typing import Any, Callable, TYPE_CHECKING

from ..mcp.shared_state import SharedStateStore
from ..mcp.tools import hugin_tool, ldap_tools, tavily_tool

if TYPE_CHECKING:
    from ..core.llm_client import LLMClient

logger = logging.getLogger("munin.subagent")


# ─────────────────────────────────────────────────────────────────────────────
# OpenAI tool-spec helpers  (also used by MuninAgent)
# ─────────────────────────────────────────────────────────────────────────────

def _signature_to_openai(fn: Callable[..., Any]) -> dict[str, Any]:
    sig = inspect.signature(fn)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in sig.parameters.items():
        if name == "run_id":
            continue
        annotation = param.annotation if param.annotation is not inspect._empty else str
        json_type = "string"
        if annotation in (int,):
            json_type = "integer"
        elif annotation in (float,):
            json_type = "number"
        elif annotation in (bool,):
            json_type = "boolean"
        elif annotation in (list, tuple):
            json_type = "array"
        elif annotation in (dict,):
            json_type = "object"
        prop: dict[str, Any] = {"type": json_type}
        if param.default is inspect._empty:
            required.append(name)
        else:
            try:
                json.dumps(param.default)
                prop["default"] = param.default
            except TypeError:
                pass
        properties[name] = prop
    return {"type": "object", "properties": properties, "required": required}


def _tool_specs(catalog: dict[str, Callable[..., Any]]) -> list[dict[str, Any]]:
    specs = []
    for name, fn in catalog.items():
        description = (fn.__doc__ or "").strip().split("\n")[0][:300]
        specs.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description or f"Tool {name}",
                "parameters": _signature_to_openai(fn),
            },
        })
    return specs


# ─────────────────────────────────────────────────────────────────────────────
# State-bound tool factories
# ─────────────────────────────────────────────────────────────────────────────

def _make_memory_tools(state: SharedStateStore) -> dict[str, Callable]:
    def memory_remember(key: str, value_json: str, run_id: str = "") -> dict[str, Any]:
        """Persist a semantic fact to shared_state.sqlite."""
        try:
            value = json.loads(value_json)
        except Exception as exc:
            return {"ok": False, "tool": "memory_remember", "error": {"code": "bad_input", "message": str(exc)}}
        row = state.semantic_remember(key, value)
        return {"ok": True, "tool": "memory_remember", "summary": f"remembered {key}", "data": row}

    def memory_recall(key: str, run_id: str = "") -> dict[str, Any]:
        """Recall a semantic fact by key."""
        value = state.semantic_recall(key)
        if value is None:
            return {"ok": False, "tool": "memory_recall", "error": {"code": "not_found", "message": key}}
        return {"ok": True, "tool": "memory_recall", "summary": f"recalled {key}", "data": {"key": key, "value": value}}

    def memory_list(prefix: str = "", limit: int = 100, run_id: str = "") -> dict[str, Any]:
        """List semantic facts, optionally filtered by key prefix."""
        rows = state.semantic_list(prefix=prefix, limit=limit)
        return {"ok": True, "tool": "memory_list", "summary": f"{len(rows)} facts", "data": {"facts": rows, "count": len(rows)}}

    def episodic_query(agent: str = "", action: str = "", limit: int = 100, run_id: str = "") -> dict[str, Any]:
        """Recent episodic events (tool calls, ReAct steps, agent decisions)."""
        rows = state.episodic_query(agent=agent, action=action, limit=limit)
        return {"ok": True, "tool": "episodic_query", "summary": f"{len(rows)} events", "data": {"events": rows, "count": len(rows)}}

    return {
        "memory_remember": memory_remember,
        "memory_recall": memory_recall,
        "memory_list": memory_list,
        "episodic_query": episodic_query,
    }


def _make_messaging_tools(state: SharedStateStore) -> dict[str, Callable]:
    def post_agent_message(
        sender_agent: str,
        recipient_agent: str,
        body: str,
        subject: str = "",
        message_type: str = "INFO",
        related_task_id: int = 0,
        related_target_ip: str = "",
        metadata_json: str = "{}",
        run_id: str = "",
    ) -> dict[str, Any]:
        """Send a message from this agent to another via the shared SQLite queue."""
        record = state.post_message(
            sender_agent=sender_agent,
            recipient_agent=recipient_agent,
            subject=subject,
            message_type=message_type,
            body=body,
            related_task_id=related_task_id or None,
            related_target_ip=related_target_ip,
            metadata_json=metadata_json,
        )
        return {"ok": True, "tool": "post_agent_message", "summary": f"message queued for {recipient_agent}", "data": record}

    def fetch_agent_messages(
        recipient_agent: str,
        status: str = "",
        message_type: str = "",
        mark_read: bool = False,
        limit: int = 50,
        run_id: str = "",
    ) -> dict[str, Any]:
        """Fetch messages for an agent from the shared SQLite queue."""
        matches = state.fetch_messages(
            recipient_agent=recipient_agent,
            status=status,
            message_type=message_type,
            mark_read=mark_read,
            limit=limit,
        )
        return {
            "ok": True,
            "tool": "fetch_agent_messages",
            "summary": f"{len(matches)} messages for {recipient_agent}",
            "data": {"matches": matches, "count": len(matches)},
        }

    def ack_agent_message(message_id: int, recipient_agent: str, status: str = "ACKED", run_id: str = "") -> dict[str, Any]:
        """Acknowledge or mark a message as READ, ACKED, or DONE."""
        result = state.ack_message(message_id=message_id, recipient_agent=recipient_agent, status=status)
        return {
            "ok": bool(result.get("success")),
            "tool": "ack_agent_message",
            "summary": result["message"],
            "data": result,
        }

    def list_agent_presence(stale_after_seconds: int = 3600, run_id: str = "") -> dict[str, Any]:
        """List running/idle agents and their current status."""
        matches = state.list_presence(stale_after_seconds=stale_after_seconds)
        return {"ok": True, "tool": "list_agent_presence", "summary": f"{len(matches)} agents", "data": {"agents": matches, "count": len(matches)}}

    return {
        "post_agent_message": post_agent_message,
        "fetch_agent_messages": fetch_agent_messages,
        "ack_agent_message": ack_agent_message,
        "list_agent_presence": list_agent_presence,
    }


def _make_intel_tools(state: SharedStateStore) -> dict[str, Callable]:
    def publish_shared_intel(
        target_ip: str,
        finding_type: str,
        details_json: str,
        source_agent: str,
        port: int = 0,
        service: str = "",
        severity: str = "INFO",
        status: str = "NEW",
        tags: str = "",
        fingerprint: str = "",
        run_id: str = "",
    ) -> dict[str, Any]:
        """Publish a finding to the shared intel SQLite store. Accessible by all agents."""
        record = state.publish_intel(
            target_ip=target_ip,
            port=port or None,
            service=service,
            finding_type=finding_type,
            severity=severity,
            details_json=details_json,
            source_agent=source_agent,
            status=status,
            tags=tags,
            fingerprint=fingerprint,
        )
        return {"ok": True, "tool": "publish_shared_intel", "summary": f"intel stored for {record['target_ip']}", "data": record}

    def query_shared_intel(
        target_ip: str = "",
        service: str = "",
        finding_type: str = "",
        severity: str = "",
        status: str = "",
        limit: int = 50,
        run_id: str = "",
    ) -> dict[str, Any]:
        """Query the shared intel store. Filter by IP, service, finding type, severity, or status."""
        matches = state.query_intel(
            target_ip=target_ip,
            service=service,
            finding_type=finding_type,
            severity=severity,
            status=status,
            limit=limit,
        )
        return {"ok": True, "tool": "query_shared_intel", "summary": f"{len(matches)} intel rows", "data": {"matches": matches, "count": len(matches)}}

    return {
        "publish_shared_intel": publish_shared_intel,
        "query_shared_intel": query_shared_intel,
    }


def _make_task_tools(state: SharedStateStore) -> dict[str, Callable]:
    def claim_shared_task(
        target_ip: str,
        action: str,
        assigned_agent: str,
        lease_seconds: int = 1800,
        metadata_json: str = "{}",
        allow_steal_stale: bool = True,
        run_id: str = "",
    ) -> dict[str, Any]:
        """Claim an exclusive task lock for a (target_ip, action) pair."""
        decision = state.claim_task(
            target_ip=target_ip,
            action=action,
            assigned_agent=assigned_agent,
            lease_seconds=lease_seconds,
            metadata_json=metadata_json,
            allow_steal_stale=allow_steal_stale,
        )
        return {
            "ok": decision.success,
            "tool": "claim_shared_task",
            "summary": decision.message,
            "data": {
                "task_id": decision.task_id,
                "stolen_stale_task_id": decision.stolen_stale_task_id,
                "target_ip": target_ip,
                "action": action,
                "assigned_agent": assigned_agent,
            },
            "error": None if decision.success else {"code": "task_claim_failed", "message": decision.message},
        }

    def heartbeat_shared_task(task_id: int, assigned_agent: str, lease_seconds: int = 1800, run_id: str = "") -> dict[str, Any]:
        """Renew the lease on a claimed task to prevent it from being stolen."""
        result = state.heartbeat_task(task_id=task_id, assigned_agent=assigned_agent, lease_seconds=lease_seconds)
        return {
            "ok": bool(result.get("success")),
            "tool": "heartbeat_shared_task",
            "summary": result["message"],
            "data": result,
        }

    def complete_shared_task(
        task_id: int,
        assigned_agent: str,
        status: str = "COMPLETED",
        result_json: str = "{}",
        run_id: str = "",
    ) -> dict[str, Any]:
        """Mark a task as COMPLETED, FAILED, or CANCELLED and store the result JSON."""
        result = state.complete_task(task_id=task_id, assigned_agent=assigned_agent, status=status, result_json=result_json)
        return {
            "ok": bool(result.get("success")),
            "tool": "complete_shared_task",
            "summary": result["message"],
            "data": result,
        }

    def list_shared_tasks(status: str = "", assigned_agent: str = "", target_ip: str = "", limit: int = 100, run_id: str = "") -> dict[str, Any]:
        """List shared tasks. Filter by status, agent, or target IP."""
        rows = state.list_tasks(status=status, assigned_agent=assigned_agent, target_ip=target_ip, limit=limit)
        return {"ok": True, "tool": "list_shared_tasks", "summary": f"{len(rows)} tasks", "data": {"tasks": rows, "count": len(rows)}}

    return {
        "claim_shared_task": claim_shared_task,
        "heartbeat_shared_task": heartbeat_shared_task,
        "complete_shared_task": complete_shared_task,
        "list_shared_tasks": list_shared_tasks,
    }


def _make_wake_tools(state: SharedStateStore) -> dict[str, Callable]:
    def munin_wake(subagent: str, task_json: str = "{}", priority: int = 0, run_id: str = "") -> dict[str, Any]:
        """Enqueue a wake request for another subagent."""
        if not subagent.strip():
            return {"ok": False, "tool": "munin_wake", "error": {"code": "bad_input", "message": "subagent name required"}}
        try:
            task = json.loads(task_json or "{}")
        except Exception as exc:
            return {"ok": False, "tool": "munin_wake", "error": {"code": "bad_input", "message": str(exc)}}
        wake_id = state.enqueue_wake(target_agent=subagent.strip(), task=task, priority=priority)
        return {"ok": True, "tool": "munin_wake", "summary": f"wake queued for {subagent}", "data": {"wake_id": wake_id, "target_agent": subagent, "task": task}}

    def munin_wake_list(subagent: str = "", include_claimed: bool = False, run_id: str = "") -> dict[str, Any]:
        """List pending (and optionally claimed) wake items."""
        items = state.list_wake_queue(target_agent=subagent, include_claimed=include_claimed)
        return {"ok": True, "tool": "munin_wake_list", "summary": f"{len(items)} wake items", "data": {"items": items, "count": len(items)}}

    return {"munin_wake": munin_wake, "munin_wake_list": munin_wake_list}


# ─────────────────────────────────────────────────────────────────────────────
# Subagent tool registry — metadata for graph design conversations
# ─────────────────────────────────────────────────────────────────────────────

SUBAGENT_TOOL_REGISTRY: list[dict[str, Any]] = [
    {
        "category": "ldap",
        "label": "Active Directory / LDAP enumeration",
        "tools": [
            {"name": "ldap_who_am_i",              "desc": "Bind + whoami — verify credentials and current user"},
            {"name": "get_current_user_info",       "desc": "Fetch all attributes for the bound user"},
            {"name": "get_user_groups",             "desc": "List all groups for a given user"},
            {"name": "ldap_search",                 "desc": "Parametric LDAP search (injection-safe)"},
            {"name": "find_kerberoastable_users",   "desc": "Users with servicePrincipalName — Kerberoasting targets"},
            {"name": "find_asrep_roastable_users",  "desc": "Users with DONT_REQ_PREAUTH — AS-REP targets"},
            {"name": "find_domain_admins",          "desc": "Members of a privileged group (Domain Admins, etc.)"},
            {"name": "dump_domain_structure",       "desc": "List all OUs and containers in the domain"},
        ],
    },
    {
        "category": "intel",
        "label": "External threat intelligence",
        "tools": [
            {"name": "tavily_search",  "desc": "Web search via Tavily API — general OSINT"},
            {"name": "hugin_search",   "desc": "CVE / exploit search against cached NVD + EPSS + CISA"},
            {"name": "hugin_refresh",  "desc": "Refresh the Hugin CVE cache from upstream feeds"},
        ],
    },
    {
        "category": "memory",
        "label": "Shared semantic + episodic memory (SQLite)",
        "tools": [
            {"name": "memory_remember",  "desc": "Persist a key→value fact across sessions"},
            {"name": "memory_recall",    "desc": "Recall a fact by key"},
            {"name": "memory_list",      "desc": "List all facts (optional prefix filter)"},
            {"name": "episodic_query",   "desc": "Query the episodic event log (tool calls, decisions)"},
        ],
    },
    {
        "category": "messaging",
        "label": "Inter-agent communication (SQLite queue)",
        "tools": [
            {"name": "post_agent_message",    "desc": "Send a message to another agent"},
            {"name": "fetch_agent_messages",  "desc": "Read unread messages addressed to this agent"},
            {"name": "ack_agent_message",     "desc": "Mark a message as READ / ACKED / DONE"},
            {"name": "list_agent_presence",   "desc": "Check which agents are RUNNING / IDLE"},
        ],
    },
    {
        "category": "shared_intel",
        "label": "Publish findings across agents",
        "tools": [
            {"name": "publish_shared_intel",  "desc": "Publish a CVE/port/service finding to the shared store"},
            {"name": "query_shared_intel",    "desc": "Query findings published by any agent"},
        ],
    },
    {
        "category": "tasks",
        "label": "Distributed task coordination",
        "tools": [
            {"name": "claim_shared_task",      "desc": "Claim exclusive ownership of a (target_ip, action) task"},
            {"name": "heartbeat_shared_task",  "desc": "Renew lease on a claimed task"},
            {"name": "complete_shared_task",   "desc": "Mark task COMPLETED / FAILED / CANCELLED"},
            {"name": "list_shared_tasks",      "desc": "List all tasks (filter by status, agent, IP)"},
        ],
    },
    {
        "category": "orchestration",
        "label": "Spawn and coordinate subagents",
        "tools": [
            {"name": "munin_wake",       "desc": "Enqueue a wake request for a subagent with a task payload"},
            {"name": "munin_wake_list",  "desc": "List pending / claimed wake items in the queue"},
        ],
    },
]

# Flat set of all valid subagent tool names (for whitelist validation)
ALL_SUBAGENT_TOOL_NAMES: set[str] = {
    t["name"]
    for category in SUBAGENT_TOOL_REGISTRY
    for t in category["tools"]
}


def list_subagent_tools(category: str = "") -> dict[str, Any]:
    """Return the full catalog of tools available to subagents, organized by category.

    Use this before calling graph_forge to choose an appropriate tool_whitelist_csv.
    Pass category= to filter (e.g. 'ldap', 'intel', 'memory', 'messaging').
    """
    entries = SUBAGENT_TOOL_REGISTRY
    if category.strip():
        entries = [e for e in entries if e["category"] == category.strip()]
    total = sum(len(e["tools"]) for e in entries)
    return {
        "ok": True,
        "tool": "list_subagent_tools",
        "summary": f"{total} subagent tools across {len(entries)} categories",
        "data": {
            "categories": entries,
            "total_tools": total,
            "note": (
                "Pass tool names from this list as tool_whitelist_csv when calling graph_forge. "
                "Always include post_agent_message so the subagent can report back to munin."
            ),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Master catalog builder
# ─────────────────────────────────────────────────────────────────────────────

_STATIC_TOOLS: dict[str, Callable[..., Any]] = {
    # LDAP — stateless, use env config
    "ldap_who_am_i": ldap_tools.ldap_who_am_i,
    "get_current_user_info": ldap_tools.get_current_user_info,
    "get_user_groups": ldap_tools.get_user_groups,
    "ldap_search": ldap_tools.ldap_search,
    "find_kerberoastable_users": ldap_tools.find_kerberoastable_users,
    "find_asrep_roastable_users": ldap_tools.find_asrep_roastable_users,
    "find_domain_admins": ldap_tools.find_domain_admins,
    "dump_domain_structure": ldap_tools.dump_domain_structure,
    # External intel — stateless, use env config
    "tavily_search": tavily_tool.tavily_search,
    "hugin_search": hugin_tool.hugin_search,
    "hugin_refresh": hugin_tool.hugin_refresh,
}


def build_tool_catalog(state: SharedStateStore, allowed_tools: set[str]) -> dict[str, Callable[..., Any]]:
    """Build a tool catalog filtered to `allowed_tools`, bound to `state` for SQLite tools."""
    all_tools: dict[str, Callable[..., Any]] = dict(_STATIC_TOOLS)
    all_tools.update(_make_memory_tools(state))
    all_tools.update(_make_messaging_tools(state))
    all_tools.update(_make_intel_tools(state))
    all_tools.update(_make_task_tools(state))
    all_tools.update(_make_wake_tools(state))
    return {k: v for k, v in all_tools.items() if k in allowed_tools}


# ─────────────────────────────────────────────────────────────────────────────
# ReAct base class
# ─────────────────────────────────────────────────────────────────────────────

class ReActSubagentBase:
    """Full ReAct loop subagent.

    Subclasses declare:
        name            = "my_agent"
        role            = "specialist description"
        system_prompt   = "You are ..."
        allowed_tools   = {"tool_a", "tool_b", ...}
        max_iterations  = 8  # optional override
    """

    name: str = "base"
    role: str = ""
    system_prompt: str = "You are a Munin subagent. Complete the task using your tools."
    allowed_tools: set[str] = set()
    max_iterations: int = 8

    def __init__(self, state: SharedStateStore, llm: Any | None = None) -> None:
        self.state = state
        if llm is not None:
            self.llm = llm
        else:
            from ..core.llm_client import LLMClient  # noqa: PLC0415
            self.llm = LLMClient(state.settings)
        self.pid = os.getpid()

    # ------------------------------------------------------------------
    # Presence helpers
    # ------------------------------------------------------------------
    def _set_presence(self, status: str, task_id: int | None = None) -> None:
        self.state.upsert_presence(
            agent_name=self.name,
            role=self.role,
            status=status,
            current_task_id=task_id,
            metadata_json=json.dumps({"pid": self.pid}, ensure_ascii=True),
        )

    # ------------------------------------------------------------------
    # Main entry point — called by runner for each claimed wake item
    # ------------------------------------------------------------------
    def handle_task(self, task: dict[str, Any]) -> dict[str, Any]:
        """Run a full ReAct loop for the given task.

        ``task`` can include a ``prompt`` key for free-text instructions, or
        any other keys which are serialised and used as the user message.
        The LLM drives the loop autonomously until it produces a final
        text response or ``max_iterations`` is exhausted.
        """
        prompt = task.get("prompt") or json.dumps(task, ensure_ascii=True, default=str)
        catalog = build_tool_catalog(self.state, self.allowed_tools)
        specs = _tool_specs(catalog)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": prompt.strip()},
        ]

        self._set_presence("RUNNING")
        self.state.episodic_record(
            agent=self.name,
            action="task_start",
            input_data={"task": task},
            tags=["react"],
        )

        final_content = ""
        tool_calls_log: list[dict[str, Any]] = []

        for step in range(self.max_iterations):
            try:
                completion = self.llm.chat(messages=messages, tools=specs, temperature=0.2)
            except Exception as exc:
                logger.error("%s: LLM call failed at step %d: %s", self.name, step, exc)
                final_content = f"(LLM error: {exc})"
                break

            message = completion["choices"][0]["message"]
            messages.append(message)

            self.state.episodic_record(
                agent=self.name,
                action="react_step",
                input_data={"step": step},
                output_data={
                    "tool_calls": len(message.get("tool_calls") or []),
                    "content_snippet": (message.get("content") or "")[:400],
                },
                tags=["react"],
            )

            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                final_content = message.get("content", "") or ""
                break

            for call in tool_calls:
                tool_name = call["function"]["name"]
                try:
                    args = json.loads(call["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}

                fn = catalog.get(tool_name)
                t0 = time.monotonic()
                if not fn:
                    tool_result: dict[str, Any] = {
                        "ok": False,
                        "error": {"code": "unknown_tool", "message": f"{tool_name} not in allowed_tools for {self.name}"},
                    }
                else:
                    try:
                        tool_result = fn(**args)
                    except TypeError as exc:
                        tool_result = {"ok": False, "error": {"code": "bad_args", "message": str(exc)}}
                    except Exception as exc:  # noqa: BLE001
                        tool_result = {"ok": False, "error": {"code": "tool_crashed", "message": str(exc)}}
                elapsed_ms = int((time.monotonic() - t0) * 1000)

                self.state.episodic_record(
                    agent=self.name,
                    action=f"tool:{tool_name}",
                    input_data=args,
                    output_data={"ok": tool_result.get("ok"), "summary": tool_result.get("summary"), "elapsed_ms": elapsed_ms},
                    tags=["tool"],
                )

                entry: dict[str, Any] = {
                    "name": tool_name,
                    "arguments": args,
                    "elapsed_ms": elapsed_ms,
                    "ok": tool_result.get("ok", True),
                    "summary": tool_result.get("summary", ""),
                }
                if tool_result.get("ok", True):
                    entry["result"] = tool_result.get("data", tool_result)
                else:
                    entry["error"] = tool_result.get("error", {"code": "error", "message": str(tool_result)})
                tool_calls_log.append(entry)

                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": tool_name,
                    "content": json.dumps(tool_result, ensure_ascii=True, default=str)[:8000],
                })
        else:
            final_content = "(max iterations reached)"

        self.state.episodic_record(
            agent=self.name,
            action="task_done",
            output_data={"content": final_content[:2000], "steps": step + 1},
            tags=["react"],
        )
        self._set_presence("IDLE")

        return {
            "ok": True,
            "summary": final_content[:120] if final_content else "(no response)",
            "data": {
                "content": final_content,
                "tool_calls": tool_calls_log,
                "iterations": step + 1,
            },
        }


# Legacy alias — keeps existing code that subclasses ReActSubagent working
ReActSubagent = ReActSubagentBase
