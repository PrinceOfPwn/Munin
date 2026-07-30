"""Workflow Registry — persistent versioned workflow definitions (v3.4)."""
from __future__ import annotations
import json, sqlite3, uuid
from datetime import datetime, timezone
from typing import Any, Literal
from .workflow_spec import WorkflowSpec

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS workflow_registry (
    workflow_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    definition_json TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT 'supervisor',
    parent_run TEXT,
    dependencies_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    last_invocation_at TEXT,
    exec_history_json TEXT NOT NULL DEFAULT '[]',
    artifacts_uri TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workflow_id, version)
);
"""


class WorkflowRegistry:
    def __init__(self, db_path: str):
        self.db_path = db_path
        with self._connect() as c:
            c.execute(CREATE_SQL); c.commit()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def register_workflow(self, spec: WorkflowSpec, *, created_by: str = "supervisor",
                          parent_run: str | None = None, dependencies: list[str] | None = None) -> tuple[str, int]:
        wf_id = f"wf_{spec.name}_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as c:
            row = c.execute("SELECT MAX(version) as v FROM workflow_registry WHERE workflow_id=?", (wf_id,)).fetchone()
            version = (row["v"] or 0) + 1
            c.execute(
                "INSERT INTO workflow_registry(workflow_id,version,definition_json,created_by,parent_run,dependencies_json,status,exec_history_json,created_at,updated_at) VALUES(?,?,?,?,?,?,'active','[]',?,?)",
                (wf_id, version, spec.to_json(), created_by, parent_run, json.dumps(dependencies or []), now, now)
            )
            c.commit()
        return wf_id, version

    def rebuild_workflow(self, workflow_id: str, version: int | None = None, *, tools: list[Any] | None = None) -> Any:
        with self._connect() as c:
            if version is None:
                row = c.execute("SELECT definition_json FROM workflow_registry WHERE workflow_id=? AND status='active' ORDER BY version DESC LIMIT 1", (workflow_id,)).fetchone()
            else:
                row = c.execute("SELECT definition_json FROM workflow_registry WHERE workflow_id=? AND version=?", (workflow_id, version)).fetchone()
        if row is None:
            raise KeyError(f"Workflow {workflow_id!r} not found")
        spec = WorkflowSpec.from_json(row["definition_json"])
        from .workflow_factory import create_workflow
        return create_workflow(spec, tools=tools)

    def list_registered_workflows(self, *, status: str | None = None) -> list[dict]:
        q = "SELECT * FROM workflow_registry WHERE 1=1"
        p: list = []
        if status:
            q += " AND status=?"; p.append(status)
        q += " ORDER BY created_at DESC"
        with self._connect() as c:
            return [dict(r) for r in c.execute(q, p).fetchall()]

    def inspect_registered_workflow(self, workflow_id: str, version: int | None = None) -> dict:
        for wf in self.list_registered_workflows():
            if wf["workflow_id"] == workflow_id and (version is None or wf["version"] == version):
                return wf
        raise KeyError(f"Workflow {workflow_id!r} not found")

    def record_workflow_exec(self, workflow_id: str, version: int, result_summary: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as c:
            row = c.execute("SELECT exec_history_json FROM workflow_registry WHERE workflow_id=? AND version=?", (workflow_id, version)).fetchone()
            if row:
                h = json.loads(row["exec_history_json"] or "[]")
                h.append({"ts": now, "result": result_summary[:200]})
                c.execute("UPDATE workflow_registry SET exec_history_json=?,last_invocation_at=?,updated_at=? WHERE workflow_id=? AND version=?",
                          (json.dumps(h[-50:]), now, now, workflow_id, version))
                c.commit()

    def deprecate(self, workflow_id: str, version: int | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as c:
            if version is None:
                c.execute("UPDATE workflow_registry SET status='deprecated',updated_at=? WHERE workflow_id=?", (now, workflow_id))
            else:
                c.execute("UPDATE workflow_registry SET status='deprecated',updated_at=? WHERE workflow_id=? AND version=?", (now, workflow_id, version))
            c.commit()
