"""LDAP subagent — full ReAct specialist for Active Directory / OpenLDAP enumeration.

Inherits the complete ReAct loop from ReActSubagentBase. Declares which tools it
may use and its system prompt; the base class handles everything else.
"""

from __future__ import annotations

from typing import Any

from ..mcp.shared_state import SharedStateStore
from .base import ReActSubagentBase

_SYSTEM_PROMPT = """You are the LDAP subagent of Munin — a specialist for Active Directory
and OpenLDAP enumeration.

Your job: receive a task, reason step-by-step, call the appropriate LDAP tools,
publish every notable finding to shared intel, and report results to the parent
agent via post_agent_message(recipient_agent="munin").

Available tools (use ONLY these):
  ldap_who_am_i, get_current_user_info, get_user_groups, ldap_search,
  find_kerberoastable_users, find_asrep_roastable_users, find_domain_admins,
  dump_domain_structure, publish_shared_intel, query_shared_intel,
  memory_remember, memory_recall, post_agent_message, fetch_agent_messages.

Hard rules:
  - NEVER build LDAP filter strings by hand — always use ldap_search with
    filter_template + params_json (injection prevention).
  - Publish meaningful findings via publish_shared_intel before replying.
  - If the task is outside your scope, use post_agent_message to escalate to
    munin and stop.
  - Prefer recall over redundant queries: check memory_recall first.
"""


class LDAPSubagent(ReActSubagentBase):
    name = "ldap_agent"
    role = "ldap_specialist"
    system_prompt = _SYSTEM_PROMPT
    allowed_tools = {
        "ldap_who_am_i",
        "get_current_user_info",
        "get_user_groups",
        "ldap_search",
        "find_kerberoastable_users",
        "find_asrep_roastable_users",
        "find_domain_admins",
        "dump_domain_structure",
        "publish_shared_intel",
        "query_shared_intel",
        "memory_remember",
        "memory_recall",
        "post_agent_message",
        "fetch_agent_messages",
    }

    def __init__(self, state: SharedStateStore, llm: Any | None = None) -> None:
        super().__init__(state, llm)
