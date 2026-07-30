# PR-06 — Tool Factory evolution (Autonomy Kernel meta-tools + provenance expansion + cap removal)

- **Head**: `raven-mind/migration-issue9/pr-06-tool-factory-evolution`
- **Base**: `raven-mind/migration-issue9/pr-05-tool-gateway`
- **Open architectural questions**: None. PR-05 verified StructuredTool pattern + same-run invocation through registry. `register_state_only` semantics preserved per GLUE_INVENTORY §3.

---

## Goal

Evolve the current `tool_forge` MCP capability into the Autonomy Kernel Tool Factory backend:
1. Any authorized agent or subagent can create a new tool during execution.
2. The tool can be invoked in the same LangGraph run through `invoke_registered_tool(tool_id, args)`.
3. Persistent tools are versioned and discoverable in later runs.
4. Generated tools retain provenance (creator_agent, parent_run, spec, source, deps, validation_results, timestamps, exec_history).
5. Remove the `forge_tool.py max_iterations[1,12]` clamp — issue §4 "no arbitrary hard caps".
6. Reuse `ToolForgeSubagent` LLM generation + AST guard + sandbox from `munin/subagents/tool_forge.py` & `munin/subagents/sandbox.py`.

Issue acceptance #5 (`Generated tools are callable in the same run through the Tool Registry/Gateway`) + #6 (`The main agent and generated subagents can create additional tools, agents, and workflows`) + #14 ("No arbitrary product-level hard caps").

## Acceptance title (one line)

Supervisor's `create_tool(spec="echo text")` writes a `gen__echo_text` row in `procedural`, retains the callable, and a subsequent `invoke_registered_tool("gen__echo_text", {"text":"hi"})` from the same LangGraph run returns the spec's behaviour.

## Issue required end-to-end scenarios this PR partially unlocks

**Dynamic tool** (issue required E2E #1): unlocks the same-run side (`create_tool` + `invoke_registered_tool` in same run). The cross-run "persistent tool rehydrates and is callable by name" side needs a separate supervisor restart, which the test suite proves.

---

## Files added

| Path | What |
|---|---|
| `munin/core/autonomy/__init__.py` | Autonomy Kernel package marker. |
| `munin/core/autonomy/tool_factory.py` | `create_tool(spec, *, ephemeral=False, parent_run_id=None, creator_agent_id="munin") -> dict`, `invoke_registered_tool(tool_id, args) -> dict`, `list_registered_tools() -> list`, `inspect_registered_tool(tool_id) -> dict`. Reuses the existing `ToolForgeSubagent` LLM→AST guard→sandbox pipeline. |
| `tests/characterization/test_tool_factory_same_run.py` | E2E: `create_tool → invoke_registered_tool` in the same supervisor.run; `procedural` row written with full provenance. |
| `tests/characterization/test_tool_factory_persistence.py` | Two supervisor processes (simulated by re-instantiating supervisor from same settings + same DB): one creates `gen__echo_text`; second `invoke_registered_tool("gen__echo_text", {"text":"hi"})` returns the same result via rehydrate path. |
| `tests/characterization/test_tool_factory_provenance.py` | All required provenance columns populates with realistic values; spec echoed back via `inspect_registered_tool`. |
| `tests/characterization/test_tool_factory_no_cap.py` | `forge_tool` with `max_iterations=50` is honoured (no silent abort at 12); same call to legacy MCP `tool_forge` already removed clamp — assert via direct `ToolFactorySubagent` (NOT the MCP wrapper, which is being phased out in this PR by repointing). |

## Files modified

| Path | What changes |
|---|---|
| `munin/mcp/registry.py` | Add method `record_provenance(name, *, creator_agent, parent_run, spec, source, deps, validation_results, exec_history)` updating the `procedural` row's provenance columns. |
| `munin/production/store.py` | New checksum-guarded forward-only migration: add columns `creator_agent TEXT`, `parent_run TEXT`, `spec TEXT`, `source TEXT`, `deps TEXT` (JSON), `validation_results TEXT` (JSON), `exec_history TEXT` (JSON array) to `procedural` table. Update `MIGRATION_ID` to `v3.2`. |
| `munin/mcp/tools/forge_tool.py` | Repoint MCP `tool_forge` to `tool_factory.create_tool`. Remove the `max_iterations[1,12]` clamp (lines 81-99). The kwarg stays (operator-tunable), default unchanged (5). |
| `munin/mcp/tools/munin_tools.py` | Add three new MCP tools (NOT MCP-fixed; these are Autonomy Kernel meta-tools): `invoke_registered_tool`, `list_registered_tools`, `inspect_registered_tool`. Per issue §2/§3. |
| `pyproject.toml` | No new deps; Murmur was already added (`langchain` + langgraph + deepagents prerequisites established). |

## Files deleted

| Path | Why |
|---|---|
| The old `_WrappedToolForge.handle_task` code in `munin/subagents/runner.py` (lines 87-90 in GLUE_INVENTORY §2). | Wake-based tool forge subprocess dispatch was only wired to the runner subprocess; with the new Tool Factory being invoked in-graph by the supervisor, this wrap-delegate path is obsolete. (Deque PR-14 for the runner as a whole, but this dispatcher-slice can go now to reduce confusion.) Prudent subagent verifies by grep + dependency walk at PR-06 time. |

---

## Per-function behavior

### `munin/core/autonomy/tool_factory.py::create_tool()`

```python
def create_tool(
    spec: str,
    *,
    ephemeral: bool = False,
    parent_run_id: str | None = None,
    creator_agent_id: str = "munin",
    max_iterations: int = 5,
    allowed_imports: list[str] | None = None,
) -> dict:
    """Forge a Python tool from natural-language spec.

    Pipeline (unchanged from current ToolForgeSubagent):
        LLM gen → AST guard (sandbox._ASTSandboxGuard) → sandbox exec (RestrictedExec)
        → write to munin/generated/gen__<slug>.py if persistent
        → registry.register_state_only(name, ...) → registry.record_provenance(...)
        → if ephemeral: keep callable in-memory only, do NOT insert into procedural
    Returns {"tool_id": "gen__<slug>", "callable": callable, ...}
    Behaviour parity: same error-path leaks + last-iteration guard + exhaustion message.
    """
```

### `invoke_registered_tool()`

```python
def invoke_registered_tool(tool_id: str, args: dict) -> dict:
    """Resolve `tool_id` (e.g. 'gen__echo_text') from the live callable cache;
    fall back to procedural table reload if needed. Invoke via OPSEC preflight
    (every tool boundary preserves scope/authorization). Returns
    {"ok", "result", "elapsed_ms"}."""
```

### `list_registered_tools()`, `inspect_registered_tool()`

Read-side helpers over the `procedural` table; simple SQL selects with the same `active=1` filter. Issue #3 calls for these as Autonomy Kernel surface.

### `procedural` schema expansion

```sql
ALTER TABLE procedural ADD COLUMN creator_agent TEXT;
ALTER TABLE procedural ADD COLUMN parent_run TEXT;
ALTER TABLE procedural ADD COLUMN spec TEXT;
ALTER TABLE procedural ADD COLUMN source TEXT;
ALTER TABLE procedural ADD COLUMN deps TEXT;  -- JSON: ["tool_a", "skill_b"]
ALTER TABLE procedural ADD COLUMN validation_results TEXT;  -- JSON
ALTER TABLE procedural ADD COLUMN exec_history TEXT;  -- JSON array of {ts, ok, elapsed_ms, args_hash}
```

`production/store.py` `MIGRATION_ID` bumps from `v3.1` to `v3.2`; `_EXPECTED_SHA256` recalculated; migration forward-only.

### `forge_tool.py max_iterations[1,12]` clamp removal

Before:
```python
max_iterations = clamp(min(max_iterations, 12), 1, 5)  # roughly
```

After:
```python
# Issue §4: no arbitrary hard caps. Operator-tunable, no product constant.
# Backpressure surfaces as LangGraph RecursionLimit observable + cancel_async_task (PR-11/PR-13).
max_iterations = max(1, max_iterations)  # only sanity floor
```

Default unchanged (`5`) when kwarg omitted.

## Tests added

| Path | Assertion contract |
|---|---|
| `test_tool_factory_same_run.py` | Supervisor invokes `create_tool(spec="Return the string 'echo: ' concatenated with input text.", tool_name="echo_text")` → then `invoke_registered_tool("gen__echo_text", {"text":"hi"})` → assert result == `"echo: hi"`. Assert procedural row exists with `active=1`. |
| `test_tool_factory_persistence.py` | Restart supervisor: fresh supervisor instance with same settings + same DB → `invoke_registered_tool("gen__echo_text", {"text":"hello"})` returns `"echo: hello"` directly from the rehydrated callable (no re-forge). Assert `exec_history` row appended. |
| `test_tool_factory_provenance.py` | `create_tool(spec=..., creator_agent_id="munin", parent_run_id="R1")` → `inspect_registered_tool("gen__echo_text")` returns dict with `creator_agent="munin"`, `parent_run="R1"`, `spec=full_spec_text`, `source=<generated .py path>`, `deps=[]`, `validation_results={"ast_ok": True, "sandbox_ok": True, "lint": True}`. |
| `test_tool_factory_no_cap.py` | Call `create_tool(spec=..., max_iterations=20)`; assert the forge loop attempts iterations up to 20 (mock LLM returns failures on first 15 attempts then submits valid code at iter 16 → tool created successfully). Confirms cap removal. |

## Parity bar (PR-01 preserved)

| PR-01 test | Status |
|---|---|
| `test_tool_catalog_parity.py` assertion 5 (max_iterations clamp `[1,12]` default 5) | **Modified assertion**: in turn, the characterisation spec was `clamp(50)==12, clamp(0)==1, default==5`. New behaviour asserts the absence of clamp entirely: `max_iterations=20 → respected (no clamp); max_iterations=0 → floored to 1 (sanity), default still 5`. Per project rule: amend the characterisation (with reason documented in changes.md). Reason: "Issue §4 explicit no-arbitrary-caps; sanity floor preserved against zero/NaN, no product constant." |
| Other 6 PR-01 tests | Unchanged green |

## Deps bumped / added in this PR

No new deps; existing deepagents, langchain, langgraph suffice. Pyproject may need a minor update if pydantic needs `create_model` hint (it's in pydantic>=2, already required by langchain), but no new line.

## Rollback plan

Revert removes `munin/core/autonomy/tool_factory.py` + the 4 new tests; restores `forge_tool.py` clamp (lines 81-99); reverts store.py migration to `v3.1`; removes the 3 new MCP entry points; re-enables the `_WrappedToolForge.handle_task` runner-past slice. Standalone; in chain it sits between PR-05 head and PR-07 base.

Note about the v3.2 migration reversal: if PR-06's run created any v3.2-row entries, the schema won't lose them on re-run if reverted — the migration is forward-only. Subsequent re-application of PR-06 won't pre-create columns. Acceptable trade-off: revert without data loss because columns added via ALTER TABLE remain; only the `_EXPECTED_SHA256` check reverses. Subagent's pr-description should note this nuance.

---

## Validation plan

1. Characterization tests: all 7 PR-01 tests (with modified `test_tool_catalog_parity.py` assertion 5) + 5 new PR-03 + 2 PR-05 + 4 PR-06 tests green.
2. CI green: ci.yml backend + e2e_lab passes — including the v3.2 forward-only migration on a clean Turso DB.
3. Live-session workflow: chrome-devtools MCP asks Munin "create a tool that echoes text, then call it with 'hello'" → asserts tool forge stages emit (custom part `forge-stage`, not yet wired UI-side — emit them anyway; UI shows raw when part type unknown) and the invoke returns the right value.
4. Artifact inspection: `data/shared_state.sqlite` `procedural` table row exists with `creator_agent='munin'`, spec column populated, validation_results validated (ast_ok + sandbox_ok + lint).
5. Parity manual check: `pytest tests/characterization/test_tool_factory_*.py -v` green after merge; manual `sqlite3 data/shared_state.sqlite "SELECT name, creator_agent FROM procedural WHERE active=1 ORDER BY name"` shows the gen__ row.

## Issue §9 invariants preserved

| Invariant | Status |
|---|---|
| FastMCP tools | Untouched (forge returns new callable that gets registered via `register_state_only` + retained by supervisor catalog via ToolGateway) |
| Scope/OPSEC in tool boundary | Preserved — `invoke_registered_tool` calls OPSEC preflight before invoking the generated tool body. New test should assert scope error raised for restricted target. |
| Audit redaction contract | Preserved — `audit.py` flows for every tool call; `exec_history` records invocation without leaking args (only `args_hash` SHA-256 short prefix) |
| Soul human-editable | Untouched |
| **Tool provenance** | **Expanded** per issue §3 explicit: adds 7 new columns; tool factory populates them; persisted. |
| Cross-session artifact pattern | Untouched — `munin-state` artifact continues rolling `data/shared_state.sqlite` between sessions; `procedural` table travels within it. |

## Framework verification provenance

- **StructuredTool + callable retention**: PR-05 record (DeepWiki `langchain-ai/deepagents` `_build_cached_mcp_tool` recipe).
- **Same-run invocation through catalog**: PR-05's gateway catalog merges Fixed + gen__; same-run `create_tool` adds a new callable to the catalog (no graph recompilation), supervisor's next tool_call round resolves to it. Framework PR-05 verified; this PR just wires the autonomy kernel surface.

Uncertainty remaining: LangGraph's `ToolNode` may rebuild its tool dict on every call vs once at compile. If it caches per-call resolution, our newly-created tool appears in the same run as long as we re-pass the catalog; if it doesn't, the supervisor must be re-explosively re-built when the catalog grows. **Spec delegates this concern to PR-06 implementation**: subagent MUST verify by writing a focused test (`test_tool_factory_same_run.py` IS that test) that exercises same-run create+invoke through the supervisor directly (NOT just registry directly). If fails, subagent reports back; Raven Mind re-architects with dynamic `tools` predicate (LangGraph supports `tools=[callable returning list]`). Document specific resolution in changes.md.