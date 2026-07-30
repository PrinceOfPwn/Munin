"""Agent Registry — persistent versioned subagents (v3.3 + peer_handoffs)."""
from __future__ import annotations
import json, sqlite3, uuid
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


class AgentRegistry:
    def __init__(self, db_path: str):
        self.db_path = db_path
        with self._connect() as c:
            c.execute(CREATE_SQL); c.commit()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def register_agent(
        self, spec: SubagentSpec, *, created_by: str = "supervisor",
        parent_run: str | None = None, dependencies: list[str] | None = None,
        model_config: dict | None = None, peer_handoffs: list[str] | None = None,
    ) -> tuple[str, int]:
        agent_id = f"agent_{spec.name}_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        defn = json.loads(spec.to_json())
        if peer_handoffs:
            defn["peer_handoffs"] = peer_handoffs
        with self._connect() as c:
            row = c.execute("SELECT MAX(version) as v FROM agent_registry WHERE agent_id=?", (agent_id,)).fetchone()
            version = (row["v"] or 0) + 1
            c.execute(
                "INSERT INTO agent_registry(agent_id,version,definition_json,runtime_type,created_by,parent_run,dependencies_json,model_config_json,status,exec_history_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,'active','[]',?,?)",
                (agent_id, version, json.dumps(defn), spec.runtime_type, created_by, parent_run,
                 json.dumps(dependencies or []), json.dumps(model_config) if model_config else None, now, now)
            )
            c.commit()
        return agent_id, version

    def rebuild_agent(self, agent_id: str, version: int | None = None, *, tools: list[Any]) -> Any:
        with self._connect() as c:
            if version is None:
                row = c.execute("SELECT definition_json FROM agent_registry WHERE agent_id=? AND status='active' ORDER BY version DESC LIMIT 1", (agent_id,)).fetchone()
            else:
                row = c.execute("SELECT definition_json FROM agent_registry WHERE agent_id=? AND version=?", (agent_id, version)).fetchone()
        if row is None:
            raise KeyError(f"Agent {agent_id!r} not found")
        spec = SubagentSpec.from_json(row["definition_json"])
        from .subagent_factory import SubagentFactory
        return SubagentFactory(tools=tools).create_subagent(spec)

    def list_registered_agents(self, *, status: str | None = None, created_by: str | None = None) -> list[dict]:
        q, p = "SELECT * FROM agent_registry WHERE 1=1", []
        if status: q += " AND status=?"; p.append(status)
        if created_by: q += " AND created_by=?"; p.append(created_by)
        q += " ORDER BY created_at DESC"
        with self._connect() as c:
            return [dict(r) for r in c.execute(q, p).fetchall()]

    def inspect_registered_agent(self, agent_id: str, version: int | None = None) -> dict:
        for a in self.list_registered_agents():
            if a["agent_id"] == agent_id and (version is None or a["version"] == version):
                return a
        raise KeyError(f"Agent {agent_id!r} not found")

    def record_invocation(self, agent_id: str, version: int, result_summary: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as c:
            row = c.execute("SELECT exec_history_json FROM agent_registry WHERE agent_id=? AND version=?", (agent_id, version)).fetchone()
            if row:
                h = json.loads(row["exec_history_json"] or "[]")
                h.append({"ts": now, "result": result_summary[:200]})
                c.execute("UPDATE agent_registry SET exec_history_json=?,last_invocation_at=?,updated_at=? WHERE agent_id=? AND version=?",
                          (json.dumps(h[-50:]), now, now, agent_id, version))
                c.commit()

    def deprecate(self, agent_id: str, version: int | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as c:
            if version is None:
                c.execute("UPDATE agent_registry SET status='deprecated',updated_at=? WHERE agent_id=?", (now, agent_id))
            else:
                c.execute("UPDATE agent_registry SET status='deprecated',updated_at=? WHERE agent_id=? AND version=?", (now, agent_id, version))
            c.commit()
