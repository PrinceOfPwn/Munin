# PR-08 — Agent Registry (persistent versioned specialists)

- **Head**: `raven-mind/migration-issue9/pr-08-agent-registry`
- **Base**: `raven-mind/migration-issue9/pr-07-subagent-factory`
- **Open architectural questions**: None. PR-07 established `SubagentSpec` shape and routing; PR-08 persists + rebuilds using JSON-definition (NOT in-memory Pregel serialization — issue §5 explicit: "rebuild from definition rather than serialize unsafe in-memory objects").

---

## Goal

Build a persistent Agent Registry for dynamic specialists so a subagent created in run N survives to run N+1 and rebulids into a fresh runnable without regeneration. PR-07's `create_subagent` returns `Runtime`; PR-08 adds the persistence layer underneath.

## Acceptance title (one line)

`registry.register_agent(spec)` writes a row; in a separate supervisor lifecycle, `registry.rebuild_agent(agent_id, version=None)` reconstructs the subagent by reading the JSON definition + re-routing via `subagent_factory.create_subagent`; result returned by `invoke_registered_agent` identical to the original run.

## Issue required end-to-end scenarios this PR partially unlocks

**Persistent specialist** (issue E2E #3): unlocks fully — `agent_registry` table persists `definition_json` + dependencies + version; new conversation discovers + rebuilds + reuses; versioning enforced via unique constraint on `(agent_id, version)`.

---

## Files added

| Path | What |
|---|---|
| `munin/core/autonomy/agent_registry.py` | `register_agent(spec: SubagentSpec, *, creator_agent_id="munin", parent_run_id=None) -> dict`, `rebuild_agent(agent_id: str, version: int|None=None) -> Runnable`, `list_registered_agents() -> list`, `inspect_registered_agent(agent_id, version=None) -> dict`. |
| `tests/characterization/test_agent_registry_persistence.py` | Register subagent, restart supervisor, invoke via `invoke_registered_agent` → identical result. |
| `tests/characterization/test_agent_registry_versioning.py` | Bump spec; new row at higher version; old version still read-back-able; default `rebuild_agent(id)` returns latest. |
| `tests/characterization/test_agent_registry_dependencies.py` | Subagent depends on `gen__echo_text` (created by PR-06 test) — `register_agent` asserts all `deps` exist (active=1 in procedural); `rebuild_agent(id)` after a `gen__echo_text` deactivation raises (`deps="missing" in error.reason`). |

## Files modified

| Path | What changes |
|---|---|
| `munin/production/store.py` | Forward-only v3.3 checksum-guarded migration: add `agent_registry` table. Schema:
  ```sql
  CREATE TABLE agent_registry (
    agent_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    definition_json TEXT NOT NULL,   -- full SubagentSpec serialized
    runtime_type TEXT NOT NULL,      -- persisted_subagent_dict | deep_agent | compiled_langgraph | async_langgraph | swarm_member
    created_by TEXT NOT NULL,        -- creator_agent_id
    parent_run TEXT,
    dependencies_json TEXT NOT NULL, -- JSON array of tool/agent/workflow ids this subagent depends on
    model_config_json TEXT,          -- model+temperature+provider config
    status TEXT NOT NULL,            -- active | deprecated | retired | draft
    last_invocation_at TEXT,
    exec_history_json TEXT,          -- JSON array of {ts, run_id, ok, elapsed_ms}
    artifacts_uri TEXT,              -- path to traces/artifacts (LangGraph checkpoint ref later)
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (agent_id, version)
  );
  ```
  `MIGRATION_ID` → `v3.3`; `_EXPECTED_SHA256` recalculated. |
| `munin/core/autonomy/subagent_factory.py` | After PR-07's `create_subagent(spec)` returns Runnable, the factory caller (`meta-tool create_subagent`) ALSO calls `agent_registry.register_agent(...)` to persist. PR-08 introduces this hookup; if `spec.persistence_policy == "ephemeral"`, skip persistence. |
| `munin/subagents/runner.py::_load_subagent` | Becomes a shim: if `agent_registry` has a row matching `name`, call `agent_registry.rebuild_agent(name)`. Else fall back to PR-07's `subagent_factory.create_subagent`. PR-07 kept runner.py alive for partial-backward compatibility; PR-08 offers graceful degrade.
  **Nothing here is DELETED** — the legacy subprocess path stays until PR-14. |
| `munin/mcp/tools/munin_tools.py` | Replace PR-07's stub `invoke_registered_agent` with a full implementation that: (a) checks `agent_registry` for `(agent_id, latest_version)` → `rebuild_agent` → invoke via the supervisor's `task` tool OR direct LangGraph invoke if compiled; (b) records `exec_history` entry post-run. |

## Files deleted

None — registry is additive; runner compat shim retained.

---

## Per-function behavior

### `register_agent`

```python
def register_agent(spec: SubagentSpec, *, creator_agent_id="munin", parent_run_id=None) -> dict:
    """Insert row into agent_registry. Set version = max(existing) +1 if same agent_id;
    if first registration, version=1. Verify all spec.dependencies exist (tools in procedural
    WHERE active=1; agents in agent_registry WHERE status='active'). Raise if any dep missing.
    Return {"agent_id":..., "version":..., "status":"active"}.
    """
```

### `rebuild_agent`

```python
def rebuild_agent(agent_id: str, version: int|None=None) -> Runnable:
    """Read row from agent_registry (DEFAULT: latest version WHERE status='active'); re-route
    the JSON definition through subagent_factory.create_subagent(...) → Runnable.

    Issue §5: "rebuild readable from definition rather than serialize unsafe in-memory
    objects". We do NOT pickle Pregel; we store definition_json + decide runtime_type.

    For runtime_type='compiled_langgraph', the definition includes user_compiled_pregel_code
    as embedded source — re-exec via sandbox.py safe_exec (existing munin/subagents/sandbox.py).
    For runtime_type='async_langgraph', raise NotImplementedError unless MUNIN_LANGGRAPH_URL set.
    """
```

### `invoke_registered_agent` (full impl)

```python
def invoke_registered_agent(agent_id: str, task: dict, version: int|None=None) -> dict:
    """Rebuild via rebuild_agent, invoke via .ainvoke(...) when deep_agent/compiled;
    via start_async_task when async_langgraph (PR-11 wires it). Record exec_history row
    AFTER successful return with: ts, run_id (passed via task), ok, elapsed_ms."""
```

| Test | Assertion contract |
|---|---|
| `test_agent_registry_persistence.py` | (1) Register subagent spec X at version 1 → close + reopen store + supervisor. Invoke `invoke_registered_agent("X", task={"prompt":"hello"})` → assert response identical structurally to the recorded original (traceable_result.keys identical, content floats similar spec-defined shape). (2) `agent_registry` row exists; `exec_history_json` length grows by 1 with new invocation record. |
| `test_agent_registry_versioning.py` | Register spec X.v1 with system_prompt="Original"; bump spec → X.v2 with system_prompt="Revised"; rebuild_agent(X) with no version → returns v2's runnable; inspect_registered_agent(X, version=1) returns v1's definition non-destructively. |
| `test_agent_registry_dependencies.py` | Register subagent depending on `gen__echo_text` (created via PR-06 test setup). Succeeds. Deactivate `gen__echo_text` (set active=0 in procedural). Register attempt raises with `"deps missing: gen__echo_text"`. Rebuild attempt raises with same. |

## Parity bar (PR-01 preserved)

All 7 PR-01 characterization tests remain green:
- Legacy `runner.py:_load_subagent` decision path remains available as fallback (PR-08 adds a smarter layer above it; no deletion yet).
- New tests cover the migration's SERIAL forward-only behaviour; old tests unchanged.

## Deps bumped / added in this PR

No new deps.

## Rollback plan

Revert removes `agent_registry.py`, the v3.3 migration (drop the table), 3 tests; restores the PR-07 stub `invoke_registered_agent` (raises "not implemented in PR-07"); runs the legacy `_load_subagent` path again. Standalone — runner.py shim stays intact, simply uses the fallback path always after revert. PR-09's Workflow Registry depends on the schema bump to v3.4; the revert of v3.3 doesn't immediately break PR-09 because PR-09 introduces its own v3.4 migration that doesn't assume v3.3 exists. Subagent verifying `test SQL`'s forward-only checksum correctly handles both v3.2 → v3.3 → v3.4 sequences.

## Validation plan

1. Characterization tests: PR-01 through PR-07 + 3 new PR-08 tests all green.
2. CI green: backend + e2e_lab.
3. Live-session workflow: chrome-devtools MCP — Run 1: "create a subagent named 'krb5_specialist' that uses find_kerberoastable_users. Then register it persistently with ' unforgettable' as description". Run 2: "wake krb5_specialist on Test task" — assert subagent rehydrates and runs successfully even though process 1 ended and supervisor restarted; both runs traceable back to the agent_registry row.
4. Artifact inspection: `data/shared_state.sqlite` `agent_registry` rows appear with the created subagent's definition_json populated; `exec_history_json` grows with each invocation.
5. Parity manual check: `pytest tests/characterization/test_agent_registry_*.py -v` after merge.

## Issue §9 invariants preserved

| Invariant | Status |
|---|---|
| All preserved | Untouched FastMCP tools, scope/OPSEC at tool boundary (subagent tools pass through ToolGateway), audit, soul, cross-session artifact pattern. **Provenance expanded** by `agent_registry`: creator + parent_run + dependencies + version — fully per-issue §5. |

## Framework verification provenance

- **SubAgent dict shape + create_agent**: PR-03 + PR-07 record (DeepWiki deepagents).
- **CompiledSubAgent rebuild from definition**: DeepWiki `langchain-ai/deepagents` confirmed the `runnable` field is read at supervisor-side; we re-instantiate the LangGraph graph from source via sandbox.safe_exec — preserving thesandbox AST guard from `munin/subagents/sandbox.py`. Issue §9 invariant "scope/OPSEC at tool boundary" preserved because the sandbox path runs the existing AST guard; generated code already passes through same local-file import allowlist.
- **Persistent exec history**: per issue §5 explicit "execution history".

Uncertainty remaining: serializing LangGraph `Pregel` directly is unsafe (issue §5); we sidestep by serializing the `definition_json` and rebuilding — `compile_langgraph` runtime re-executes user's deterministic node functions at build time. Tractable; verified by `test_agent_registry_persistence.py`.