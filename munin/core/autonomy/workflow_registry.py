# tags: [workflow, registry, persistence, sqlite, core, WorkflowRegistry, workflow_registry_table, register_workflow, rebuild_workflow, workflow_id, WorkflowSpec, turso, definition_json, version-history, list_registered_workflows]
"""Workflow Registry — persistent versioned workflow definitions (issue #9 §6).

Same storage discipline as ``AgentRegistry``: lives in Munin's shared domain
DB (artifact / Turso), stable ``workflow_id`` slugged from the workflow name,
monotonic versions, definitions-not-runnables.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

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


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_]", "_", name.strip().lower().replace("-", "_").replace(" ", "_"))
    return re.sub(r"_+", "_", slug).strip("_") or "unnamed"


class WorkflowRegistry:
    def __init__(self, state: Any = None, db_path: str | None = None):
        # Back-compat: WorkflowRegistry("path/to.db") positional form.
        if isinstance(state, (str, bytes)) and db_path is None:
            db_path, state = str(state), None
        if state is None and db_path is None:
            raise ValueError("WorkflowRegistry requires state (SharedStateStore) or db_path")
        self._state = state
        self._db_path = db_path
        with self._connect() as c:
            c.execute(CREATE_SQL)

    def _connect(self):
        if self._state is not None:
            return self._state._connect()  # noqa: SLF001
        import sqlite3

        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ------------------------------------------------------------------

    def register_workflow(
        self,
        spec: WorkflowSpec,
        *,
        created_by: str = "supervisor",
        parent_run: str | None = None,
        dependencies: list[str] | None = None,
    ) -> tuple[str, int]:
        workflow_id = f"wf_{_slugify(spec.name)}"
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as c:
            row = c.execute(
                "SELECT MAX(version) AS v FROM workflow_registry WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()
            version = int(row["v"] or 0) + 1
            c.execute(
                "INSERT INTO workflow_registry(workflow_id,version,definition_json,created_by,"
                "parent_run,dependencies_json,status,exec_history_json,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,'active','[]',?,?)",
                (
                    workflow_id,
                    version,
                    spec.to_json(),
                    created_by,
                    parent_run,
                    json.dumps(dependencies or []),
                    now,
                    now,
                ),
            )
        return workflow_id, version

    def rebuild_workflow(
        self,
        workflow_id: str,
        version: int | None = None,
        *,
        tools: list[Any] | None = None,
        model: Any = None,
        checkpointer: Any = None,
    ) -> Any:
        row = self._definition_row(workflow_id, version)
        spec = WorkflowSpec.from_json(row["definition_json"])
        from .workflow_factory import create_workflow  # noqa: PLC0415

        return create_workflow(spec, tools=tools, model=model, checkpointer=checkpointer)

    def get_spec(self, workflow_id: str, version: int | None = None) -> WorkflowSpec:
        row = self._definition_row(workflow_id, version)
        return WorkflowSpec.from_json(row["definition_json"])

    def _definition_row(self, workflow_id: str, version: int | None) -> Any:
        with self._connect() as c:
            if version is None:
                row = c.execute(
                    "SELECT * FROM workflow_registry WHERE workflow_id = ? AND status = 'active' "
                    "ORDER BY version DESC LIMIT 1",
                    (workflow_id,),
                ).fetchone()
            else:
                row = c.execute(
                    "SELECT * FROM workflow_registry WHERE workflow_id = ? AND version = ?",
                    (workflow_id, version),
                ).fetchone()
        if row is None:
            raise KeyError(f"Workflow {workflow_id!r} not found")
        return row

    def list_registered_workflows(self, *, status: str | None = None) -> list[dict]:
        q = "SELECT * FROM workflow_registry WHERE 1=1"
        p: list = []
        if status:
            q += " AND status = ?"
            p.append(status)
        q += " ORDER BY created_at DESC"
        with self._connect() as c:
            return [dict(r) for r in c.execute(q, p).fetchall()]

    def inspect_registered_workflow(self, workflow_id: str, version: int | None = None) -> dict:
        return dict(self._definition_row(workflow_id, version))

    def record_workflow_exec(self, workflow_id: str, version: int, result_summary: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as c:
            row = c.execute(
                "SELECT exec_history_json FROM workflow_registry WHERE workflow_id = ? AND version = ?",
                (workflow_id, version),
            ).fetchone()
            if row:
                history = json.loads(row["exec_history_json"] or "[]")
                history.append({"ts": now, "result": result_summary[:500]})
                c.execute(
                    "UPDATE workflow_registry SET exec_history_json = ?, last_invocation_at = ?,"
                    "updated_at = ? WHERE workflow_id = ? AND version = ?",
                    (json.dumps(history[-50:]), now, now, workflow_id, version),
                )

    def deprecate(self, workflow_id: str, version: int | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as c:
            if version is None:
                c.execute(
                    "UPDATE workflow_registry SET status = 'deprecated', updated_at = ?"
                    " WHERE workflow_id = ?",
                    (now, workflow_id),
                )
            else:
                c.execute(
                    "UPDATE workflow_registry SET status = 'deprecated', updated_at = ?"
                    " WHERE workflow_id = ? AND version = ?",
                    (now, workflow_id, version),
                )
