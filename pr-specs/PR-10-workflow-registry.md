# PR-10 — Workflow Registry (persistent versioned workflows, rebuild from definition)

- **Head**: `raven-mind/migration-issue9/pr-10-workflow-registry`
- **Base**: `raven-mind/migration-issue9/pr-09-workflow-factory`
- **Open architectural questions**: None.

---

## Goal

Persist workflow definitions in a new `workflow_registry` table so a workflow authored in run N rebuilds into a fresh compiled `Pregel` in run N+1 (without re-derivation or unsafe in-memory serialization). Mirrors PR-08 Agent Registry shape exactly. Issue §5 explicit ("Agent Registry" includes workflow registries by extension; here, §6 "Workflow Factory and CompiledSubAgents").

## Acceptance title (one line)

`registry.register_workflow(spec)` writes a row; after supervisor restart, `registry.rebuild_workflow(id, version=None)` reconstructs an identical `Pregel` via `workflow_factory.create_workflow`, installable as a `CompiledSubAgent` in a fresh supervisor.

## Issue required end-to-end scenarios this PR partially unlocks

**Dynamic workflow** (issue E2E #4): full cross-session cycle proven.
Persistent specialist extension.

---

## Files added

| Path | What |
|---|---|
| `munin/core/autonomy/workflow_registry.py` | `register_workflow(spec, *, creator_agent_id, parent_run_id=None) -> dict`, `rebuild_workflow(workflow_id, version=None) -> Pregel`, `list_registered_workflows`, `inspect_registered_workflow(workflow_id, version=None)`, `record_workflow_exec(workflow_id, version, run_id, ok, elapsed_ms)`. |
| `tests/characterization/test_workflow_registry_persistence.py` | Register workflow, restart supervisor, invoke via `invoke_registered_workflow` → identical output structured shape.
- `tests/characterization/test_workflow_registry_versioning.py` | Register v1; bump spec for v2; rebuild workflows: latest → v2's structure_response; explicit version → v1's preserved-too.- `tests/characterization/test_workflow_registry_dependencies.py` | `dependencies_json` checked against both `procedural` table (tools) and `agent_registry` table (subagents); rebuild raises with `"deps missing"` if any undefined. |

## Files modified

| Path | What changes |
|---|---|
| `munin/production/store.py` | Forward-only v3.4 checksum-guarded migration: add `workflow_registry` table:
  ```sql
  CREATE TABLE workflow_registry (
    workflow_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    definition_json TEXT NOT NULL,    -- full WorkflowSpec serialized
    created_by TEXT NOT NULL,
    parent_run TEXT,
    dependencies_json TEXT NOT NULL,  -- JSON array of tool/agent/workflow ids
    status TEXT NOT NULL,             -- active|deprecated|retired|draft
    last_invocation_at TEXT,
    exec_history_json TEXT,
    artifacts_uri TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workflow_id, version)
  );
  ```
  `MIGRATION_ID` → `v3.4`; `_EXPECTED_SHA256` recalculated. |
| `munin/core/autonomy/workflow_factory.py` | After PR-09's `create_workflow` compiles + returns the `Pregel`, the `register_workflow` meta-tool calls `workflow_registry.register_workflow(spec, ...)` to persist; respects `persistence_policy=ephemeral` skip. |
| `munin/mcp/tools/munin_tools.py` | Add 3 new MCP entries: `invoke_registered_workflow`, `list_registered_workflows`, `inspect_registered_workflow`. |
| `munin/mcp/tools/graph_forge_tool.py` | (PR-09 engaged the route through; PR-10 finalizes) — drop the `legacy_format=True` param by default; legacy compatibility only for `generated_graphs` READING, no further writes there. |

## Files deleted

None (`generated_graphs` table deprecated but retained for read-only compat until PR-15).

---

## Per-function behavior

### `register_workflow`

```python
def register_workflow(spec: WorkflowSpec, *, creator_agent_id="munin", parent_run_id=None) -> dict:
    """Insert row into workflow_registry. version = max(existing) +1 if same id; 1 if new.
    Verify all spec.dependencies exist (tool via procedural active=1; subagent via agent_registry status='active'; workflow via workflow_registry status='active' for nested workflows).
    Raise if any dep missing.
    Return {"workflow_id":..., "version":..., "status":"active"}.
    """
```

### `rebuild_workflow`

```python
def rebuild_workflow(workflow_id: str, version: int|None=None) -> CompiledStateGraph:
    """Read row from workflow_registry (DEFAULT: latest version WHERE status='active').
    Deserialize the WorkflowSpec; route through workflow_factory.create_workflow(spec)
    → re-compiles a fresh Pregel from the DEFINITION (NOT pickle).
    """
```

### `invoke_registered_workflow`

```python
def invoke_registered_workflow(workflow_id: str, input_args: dict, version: int|None=None) -> dict:
    """Rebuild via rebuild_workflow; invoke via .ainvoke(input_args); record exec_history
    AFTER successful return with run_id, ok, elapsed_ms."""
```

## Tests added

| Path | Assertion contract |
|---|---|
| `test_workflow_registry_persistence.py` | (1) `register_workflow(Spec_v1)` → close + reopen supervisor + store. `invoke_registered_workflow("krb5_flow", input_args={"hosts": [...]}) → identical output structure to the initial run.
- (2) `workflow_registry` row exists with `exec_history_json` array growing by 1. |
| `test_workflow_registry_versioning.py` | `register_workflow(Spec_v1)`: edges `[a → b → END]`. `register_workflow(Spec_v2)`: edges `[a → b → c → END]` with same workflow_id. `rebuild_workflow("krb5_flow")` (no version) → edges include node c. `inspect_registered_workflow("krb5_flow", version=1)` → returns v1's spec edges (2 nodes). |
| `test_workflow_registry_dependencies.py` | Register workflow depending on `kerberoast_specialist` (registered via PR-08). Succeeds. Mark `kerberoast_specialist` as `status="deprecated"` in `agent_registry` — re-register attempt raises with `"deps missing"` (we interpret deprecated as not active = missing). |

## Parity bar (PR-01 preserved)

All 7 PR-01 + prior characterization tests green. The compatibility with legacy `test_graph_persist.py` is maintained via the trace-routed `legacy_format=True` read path in `graph_forge_tool.py`.

## Deps bumped / added

None.

## Rollback plan

Revert removes `workflow_registry.py` + the v3.4 migration + 3 tests; restores PR-09's `graph_forge_tool` to write to `generated_graphs`. Standalone — no subclass-side back-references.

## Validation plan

1. Characterization tests: PR-01 through PR-09 + 3 PR-10 tests green.
2. CI green.
3. Live-session workflow: chrome-devtools MCP — Run 1 instructs Munin to register a multi-host recon workflow; Run 2 instructs Munin to invoke that workflow on a different host batch. Verify `invoke_registered_workflow` succeeds with persisted definition; no re-derivation.
4. Artifact inspection: `data/shared_state.sqlite.workflow_registry` row exists; `exec_history_json` first entry pointing to Run 1, second pointing to Run 2.
5. Parity manual check: `pytest tests/characterization/test_workflow_registry_*.py -v` after merge.

## Issue §9 invariants preserved

| Invariant | Status |
|---|---|
| All preserved | Workflow provenance fully manifests via `workflow_registry`: creator, parent_run, deps, status, exec_history (issue §5 expanded to workflows via §6 explicit list) |
| Sandboxed Python compatibility | When `WorkflowSpec` falls back to sandboxed-Python for nodes, the validator passes through existing `munin/subagents/sandbox.py` AST guard — unchanged. Invariant 8th of issue §9 "Tool provenance" extended to workflow provenance. |

## Framework verification provenance

- PR-09 record (Workflow Factory compiles via LangGraph StateGraph API).
- PR-08 pattern reuse (registry shape, persist + rebuild).
- `langgraph>=0.2.40` provides CompiledStateGraph.

Uncertainty remaining: zero.