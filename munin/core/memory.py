# tags: [memory, episodic-memory, persistence, store, sqlite, Memory, log_step, semantic_remember, summarize_for_prompt, semantic-memory, procedural-memory, recall, known_facts, known_tools, known_graphs]
"""Munin memory — convenience API over :class:`SharedStateStore`.

The store itself is the single source of truth (same SQLite file the OFFX bus uses).
This class only adds ergonomic wrappers that name things in Munin's cognitive terms
(remember/recall/log_step) so agent code reads naturally.
"""

from __future__ import annotations

import logging
from typing import Any

from ..mcp.shared_state import SharedStateStore

logger = logging.getLogger("munin.memory")


class Memory:
    def __init__(self, state: SharedStateStore) -> None:
        self.state = state

    # --- episodic ---------------------------------------------------------

    def log_step(
        self,
        *,
        agent: str,
        action: str,
        input_data: Any = None,
        output_data: Any = None,
        tags: list[str] | None = None,
    ) -> int:
        return self.state.episodic_record(
            agent=agent,
            action=action,
            input_data=input_data,
            output_data=output_data,
            tags=tags or [],
        )

    def recent(self, *, agent: str = "", action: str = "", limit: int = 50) -> list[dict[str, Any]]:
        return self.state.episodic_query(agent=agent, action=action, limit=limit)

    # --- semantic ---------------------------------------------------------

    def remember(self, key: str, value: Any) -> dict[str, Any]:
        return self.state.semantic_remember(key, value)

    def recall(self, key: str, default: Any = None) -> Any:
        value = self.state.semantic_recall(key)
        return default if value is None else value

    def known_facts(self, *, prefix: str = "", limit: int = 200) -> list[dict[str, Any]]:
        return self.state.semantic_list(prefix=prefix, limit=limit)

    # --- procedural (generated tools index) --------------------------------

    def known_tools(self, *, tag: str = "") -> list[dict[str, Any]]:
        return self.state.procedural_list(tag=tag, include_inactive=False)

    def known_graphs(self) -> list[dict[str, Any]]:
        return self.state.graph_list(include_inactive=False)

    # --- discovery --------------------------------------------------------

    def summarize_for_prompt(self, *, generated_tools_limit: int = 20) -> str:
        """Render a compact human-readable block for the system prompt.

        Includes the top-N most recently generated tools plus any forged graphs.
        Consumed by :class:`MuninAgent` each iteration so Munin can see its own
        toolbox growing.
        """
        tools = self.known_tools()[:generated_tools_limit]
        graphs = self.known_graphs()
        lines: list[str] = []
        if tools:
            lines.append("## Generated tools (available via MCP as `<name>`)")
            for t in tools:
                sig = t.get("signature", {}) or {}
                props = sig.get("properties", {}) or {}
                params = ", ".join(f"{k}: {v.get('type', 'any')}" for k, v in props.items())
                lines.append(f"- `{t['name']}({params})` — {t.get('description', '')[:120]}  tags={t.get('tags', [])}")
        if graphs:
            lines.append("\n## Forged ReAct subagents (invoke via `munin_wake(name, task_json)`)")
            for g in graphs:
                contract = g.get("execution_contract", {}) or {}
                contexts = contract.get("context_sources", []) if isinstance(contract, dict) else []
                lines.append(
                    f"- `{g['name']}` — {g.get('purpose', '')[:120]}  "
                    f"tools={g.get('tool_whitelist', [])} evidence_context={contexts}"
                )
        if not lines:
            return "## Toolbox\n(no generated tools or forged graphs yet — you may need to forge some)"
        return "\n".join(lines)
