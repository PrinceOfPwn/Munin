"""Graph-forge subagent — refines a natural-language spec into a ReAct subagent config.

Produces (name, purpose, system_prompt, tool_whitelist) that gets persisted in
``generated_graphs``. The subagent runner reads that row at wake-time and builds a
``create_react_agent`` on the fly using the operator's LLM and the whitelisted MCP tools.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..core.llm_client import LLMClient
from ..mcp.shared_state import SharedStateStore

logger = logging.getLogger("munin.graph_forge")

_SYSTEM_PROMPT = """You are the Graph-Forge subagent of Munin. Given a NAME, a PURPOSE
and optional HINTS + TOOL WHITELIST, you refine them into a production-ready ReAct
system prompt for a subagent that will be spawned on demand.

Rules for the system prompt you produce:
- Second-person imperative, concise, actionable.
- Cite the tools this subagent is allowed to use (the whitelist provided).
- Include hard rules: "publish findings via publish_shared_intel", "never call tools
  outside your whitelist", "escape LDAP filter parameters with escape_filter_chars",
  and "if uncertain about scope, post a message to the parent agent via post_agent_message".

Reply with a single JSON object:
{
  "name": "<name>",
  "purpose": "<one-line purpose>",
  "system_prompt": "<multiline system prompt>",
  "tool_whitelist": ["<tool1>", "<tool2>", ...]
}
No prose outside the JSON. No markdown fences.
"""


class GraphForgeSubagent:
    def __init__(self, state: SharedStateStore, llm: LLMClient | None = None) -> None:
        self.state = state
        self.llm = llm or LLMClient(state.settings)

    def forge(
        self,
        *,
        name: str,
        purpose: str,
        hints: list[str],
        tool_whitelist: list[str],
    ) -> dict[str, Any]:
        user_prompt = json.dumps(
            {"name": name, "purpose": purpose, "hints": hints, "tool_whitelist": tool_whitelist},
            ensure_ascii=True,
        )
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        try:
            completion = self.llm.chat(messages=messages, temperature=0.2)
        except Exception as exc:
            return {"ok": False, "summary": "LLM failed", "error": {"code": "llm_failed", "message": str(exc)}}
        content = completion["choices"][0]["message"]["content"] or ""
        try:
            payload = json.loads(content.strip().strip("`").replace("json\n", "", 1))
        except Exception:
            return {"ok": False, "summary": "bad JSON reply", "error": {"code": "bad_json", "message": content[:400]}}
        if not payload.get("system_prompt"):
            return {"ok": False, "summary": "missing system_prompt", "error": {"code": "bad_reply", "message": "no system_prompt"}}
        self.state.episodic_record(
            agent="graph_forge",
            action="forge_success",
            input_data={"name": name, "purpose": purpose, "hints": hints, "tool_whitelist": tool_whitelist},
            output_data={"name": payload["name"], "tool_whitelist": payload["tool_whitelist"]},
            tags=["forge", "graph"],
        )
        return {
            "ok": True,
            "summary": f"graph forged: {payload['name']}",
            "name": payload["name"],
            "purpose": payload["purpose"],
            "system_prompt": payload["system_prompt"],
            "tool_whitelist": payload.get("tool_whitelist") or tool_whitelist,
        }
