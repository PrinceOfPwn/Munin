"""Agent Registry — persistent, versioned generated agents (issue #9 §5).

Stores definitions in the SAME database as the rest of Munin's domain state
(``SharedStateStore`` connection: local SQLite artifact or Turso/libSQL), so
registered agents survive runner death exactly like generated tools do.

Versioning model: ``agent_id`` is a stable slug derived from the agent name;
each re-registration of the same name increments ``version``.  Definitions —
never in-memory runnables — are persisted; ``rebuild_agent`` materializes the
runnable from its definition at invocation time.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from .spec import SubagentSpec

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS agent_registry (
    agent_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    definition_json TEXT NOT NULL,
    runtime_type TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT 'supervisor',
    parent_run TEXT,
    dependencies_json TEXT NOT NULL DEFAULT '[]',
    model_config_json TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    last_invocation_at TEXT,
    exec_history_json TEXT NOT NULL DEFAULT '[]',
    artifacts_uri TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (agent_id, version)
);
"""


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_]", "_", name.strip().lower().replace("-", "_").replace(" ", "_"))
    return re.sub(r"_+", "_", slug).strip("_") or "unnamed"


class AgentRegistry:
    """Versioned persistent agent definitions backed by Munin's shared DB."""

    def __init__(self, state: Any = None, db_path: str | None = None):
        # Back-compat: AgentRegistry("path/to.db") positional form.
        if isinstance(state, (str, bytes)) and db_path is None:
            db_path, state = str(state), None
        if state is None and db_path is None:
            raise ValueError("AgentRegistry requires state (SharedStateStore) or db_path")
        self._state = state
        self._db_path = db_path
        with self._connect() as c:
            c.execute(CREATE_SQL)

    def _connect(self):
        if self._state is not None:
            return self._state._connect()  # noqa: SLF001 - same store, domain tables
        import sqlite3

        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ------------------------------------------------------------------

    def _validate_dependencies(self, spec: SubagentSpec, dependencies: list[str] | None = None) -> None:
        """Validate that all tool/agent dependencies are active."""
        if self._state is None:
            return

        from ..tool_gateway import catalog_names  # noqa: PLC0415

        available_tools = catalog_names(self._state, include_generated=True)

        missing_tools = []
        for tool_name in spec.tools:
            if tool_name not in available_tools:
                missing_tools.append(tool_name)

        if dependencies:
            for dep in dependencies:
                if dep.startswith("agent_"):
                    try:
                        self._definition_row(dep, None)
                    except KeyError:
                        missing_tools.append(dep)
                elif dep not in available_tools:
                    missing_tools.append(dep)

        if missing_tools:
            raise ValueError(f"Missing or inactive dependencies: {', '.join(missing_tools)}")

    def register_agent(
        self,
        spec: SubagentSpec,
        *,
        created_by: str = "supervisor",
        parent_run: str | None = None,
        dependencies: list[str] | None = None,
        model_config: dict | None = None,
        peer_handoffs: list[str] | None = None,
    ) -> tuple[str, int]:
        self._validate_dependencies(spec, dependencies)
        agent_id = f"agent_{_slugify(spec.name)}"
        now = datetime.now(timezone.utc).isoformat()
        defn = json.loads(spec.to_json())
        if peer_handoffs:
            defn["peer_handoffs"] = peer_handoffs
        with self._connect() as c:
            row = c.execute(
                "SELECT MAX(version) AS v FROM agent_registry WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
            version = int(row["v"] or 0) + 1
            c.execute(
                "INSERT INTO agent_registry(agent_id,version,definition_json,runtime_type,"
                "created_by,parent_run,dependencies_json,model_config_json,status,"
                "exec_history_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,'active','[]',?,?)",
                (
                    agent_id,
                    version,
                    json.dumps(defn),
                    spec.runtime_type,
                    created_by,
                    parent_run,
                    json.dumps(dependencies or []),
                    json.dumps(model_config) if model_config else None,
                    now,
                    now,
                ),
            )
        return agent_id, version

    def rebuild_agent(
        self,
        agent_id: str,
        version: int | None = None,
        *,
        factory: Any = None,
        tools: list[Any] | None = None,
    ) -> Any:
        """Materialize the runnable from its persisted definition.

        ``factory`` is a ``SubagentFactory`` (carries model + tools context).
        Back-compat: callers may pass ``tools=[...]`` and one is built.
        """
        row = self._definition_row(agent_id, version)
        spec = SubagentSpec.from_json(row["definition_json"])
        dependencies = json.loads(row["dependencies_json"] or "[]")
        self._validate_dependencies(spec, dependencies)
        if factory is None:
            from .subagent_factory import SubagentFactory  # noqa: PLC0415

            factory = SubagentFactory(tools=tools or [])
        return factory.create_subagent(spec)

    def get_spec(self, agent_id: str, version: int | None = None) -> SubagentSpec:
        row = self._definition_row(agent_id, version)
        return SubagentSpec.from_json(row["definition_json"])

    def _definition_row(self, agent_id: str, version: int | None) -> Any:
        with self._connect() as c:
            if version is None:
                row = c.execute(
                    "SELECT * FROM agent_registry WHERE agent_id = ? AND status = 'active' "
                    "ORDER BY version DESC LIMIT 1",
                    (agent_id,),
                ).fetchone()
            else:
                row = c.execute(
                    "SELECT * FROM agent_registry WHERE agent_id = ? AND version = ?",
                    (agent_id, version),
                ).fetchone()
        if row is None:
            raise KeyError(f"Agent {agent_id!r} not found")
        return row

    def list_registered_agents(
        self, *, status: str | None = None, created_by: str | None = None
    ) -> list[dict]:
        q, p = "SELECT * FROM agent_registry WHERE 1=1", []
        if status:
            q += " AND status = ?"
            p.append(status)
        if created_by:
            q += " AND created_by = ?"
            p.append(created_by)
        q += " ORDER BY created_at DESC"
        with self._connect() as c:
            return [dict(r) for r in c.execute(q, p).fetchall()]

    def inspect_registered_agent(self, agent_id: str, version: int | None = None) -> dict:
        row = self._definition_row(agent_id, version)
        return dict(row)

    def record_invocation(self, agent_id: str, version: int, result_summary: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as c:
            row = c.execute(
                "SELECT exec_history_json FROM agent_registry WHERE agent_id = ? AND version = ?",
                (agent_id, version),
            ).fetchone()
            if row:
                history = json.loads(row["exec_history_json"] or "[]")
                history.append({"ts": now, "result": result_summary[:500]})
                c.execute(
                    "UPDATE agent_registry SET exec_history_json = ?, last_invocation_at = ?,"
                    "updated_at = ? WHERE agent_id = ? AND version = ?",
                    (json.dumps(history[-50:]), now, now, agent_id, version),
                )

    def deprecate(self, agent_id: str, version: int | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as c:
            if version is None:
                c.execute(
                    "UPDATE agent_registry SET status = 'deprecated', updated_at = ? WHERE agent_id = ?",
                    (now, agent_id),
                )
            else:
                c.execute(
                    "UPDATE agent_registry SET status = 'deprecated', updated_at = ?"
                    " WHERE agent_id = ? AND version = ?",
                    (now, agent_id, version),
                )
