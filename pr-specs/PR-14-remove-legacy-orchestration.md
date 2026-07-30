# PR-14 — Remove legacy orchestration (subprocess runner + wake queue + dispatcher dup)

- **Head**: `raven-mind/migration-issue9/pr-14-remove-legacy-orchestration`
- **Base**: `raven-mind/migration-issue9/pr-13-native-coordination`
- **Open architectural questions**: None. PR-11/12/13 verified all native-coordination parity paths; this PR deletes only the legacy paths whose parity has been demonstrated.

---

## Goal

Delete the legacy orchestration paths now that Deep Agents + LangGraph native + Tool Gateway + Autonomy Kernel + Agent Registry + Workflow Registry + swarm + Send + AsyncSubAgent proven as full replacement. Per issue §12 step 8 explicit ("Remove obsolete glue — Delete old loops, schema builders, subprocess orchestration, duplicate event plumbing, and duplicate persistence only after tests prove parity").

## Acceptance title (one line)

`MuninAgent` symbol removed entirely; `munin/subagents/runner.py` subprocess entry point removed; `agent_wake_queue` table writes only via internal `start_async_task` shim wrapper; dispatcher's assistant-placeholder event adapter + `tool_call_id` correlation removed (LangGraph stream_events covers).

## Issue required end-to-end scenarios this PR partially unlocks

NONE new — this PR completes the migration by deleting parity-proven legacy glue. Required E2E scenarios (issue list) proven live by previous PRs.

---

## Files deleted

| Path | Why |
|---|---|
| `munin/core/munin_agent.py` | Obsolete since PR-04 deleted `respond()`. Any remaining `MuninAgent` class members (`__init__`, `_system_prompt`, `_current_catalog`) unused post-supervisor. Final delete entirely: filesystem deletes; imports of `from munin.core.munin_agent import MuninAgent` become import errors if re-introduced — locked via new `test_no_respond_call_sites.py` extension. |
| `munin/subagents/runner.py` | Subprocess entry point no longer needed; subagent forging + returning results all in-graph via `task` tool + `start_async_task` (PR-11). The file's role was orchestrating `python -m munin.subagents.runner <name>` poll loop + claim-wake + IDLE→EXITING gestion. All replaced. |
| `munin/subagents/process_control.py` | SIGTERM/SIGKILL with grace period was for the subprocess runner (PR-14 lineage). `process_control.stop_detached_runners()` becomes orphan after runner deletion. The semantics live on via `cancel_async_task` (PR-11); `cancel_async_task` returns partial result snapshot equivalent. |
| `munin/subagents/base.py::ReActSubagentBase` | Obsolete since supervisor replaced the main ReAct loop in PR-04; subagent forge path in PR-06 uses `tool_factory` directly (no `ReActSubagentBase`); subagent run-time via LangGraph via PR-07 subagent factory. Concrete subagents (`ldap_agent.py`, `tool_forge.py`, `graph_forge.py`) reference `ReActSubagentBase` but have been made redundant by ast autonomy substrate in PRs 6/9/10 — defer to subagent to confirm via grep at PR-14 time. |
| `munin/subagents/ldap_agent.py` | Concrete LDAP subagent declared obsolete; LDAP tool wrapping now occurs via Tool Gateway + supervisor invocation (PR-07 created a `kerberoast_specialist` as a `SubAgent.dict` from spec — old LDAP subagent not invoked. Confirm `runner._load_subagent("ldap")` paths are zero callers post-deletion.) |
| `munin/subagents/tool_forge.py` | Obsolete since PR-06 (Tool Factory replaced with `tool_factory.create_tool`). The BeAct loop is the autonomous ToolFactorySubagent at `runner.py:87-90` — that gets deleted with `runner.py`. BUT: the `ASTSandboxGuard + RestrictedExec + safe_exec_script` (PR-06 reused via `munin/subagents/sandbox.py`) stays. Delete the wasted concrete `ToolForgeSubagent` code body, keep sandbox.py logic. |
| `munin/subagents/graph_forge.py` | Obsolete since PR-09 (Workflow Factory replaced). Old `GraphForgeSubagent` deleted. |
| `munin/forge/extension_forge.py::subprocess.run` extension validation block (lines 121-129) | Per issue §9 OPSEC invariant + issue §10 non-goal of offensive tool wrappers untouched. The validation block (subprocess.run py_compile) is the existing munin/forge/extension_forge path used by extension_forge (not generally tool_forge). Subagent re-evaluate whether deletion is safe in PR-14 or defer to PR-15 (it's not in cluster of removed files unless no callers). Defer-likely: keep `extension_forge.py` as-is if it's used for validation of an extension that's NOT subagent forge. |

## Files modified

| Path | What changes |
|---|---|
| `munin/mcp/tools/munin_tools.py` | `munin_wake` MCP tool repoints to a thin shim: `start_async_task(graph_id=target_agent, input=task_json)`. AsyncSubAgent is the only invocation path post-PR-14. |
| `munin/mcp/shared_state.py` | `agent_wake_queue` table: writes deprecated → comment-only (read-only) plus a `_deprecated_wake_queue` flag at module level. The table file stays (one release cycle) for backward-compat reads; deleted in PR-16. Insert methods become raise `DeprecationWarning("agent_wake_queue deprecated — use start_async_task from munin.core.autonomy.subagent_factory"). |
| `munin/production/dispatcher.py` | Drops the `assistant-placeholder` event adapter + `tool_call_ids` correlation logic. LangGraph `stream_events(version="v3")` covers these. Run model (conversations, timeline_messages, tool_calls, reasoning_events) remains authoritative — the ProductionStore writes continue. The dispatcher becomes "claim run → invoke supervisor via `run_id` → stream events to UI → terminal state written". Subagent dispatch via `start_async_task` already mandatory post-PR-11. |
| `munin/integrations/discord_config.py` | If still references `MuninAgent` post-PR-04 (PR-04 already repointed — verify), no change here. |
| `.github/workflows/live-session.yml` | `git submodule update --init` no-op. Side note: `data/wake_artifacts/` upload step remains (artifact retention cycle, read-only) until PR-16. |
| `tests/characterization/test_no_respond_call_sites.py` | Extended to assert no references to `MuninAgent`, `respond`, `ReActSubagentBase`, `_load_subagent`, `try_claim_spawn_slot`, `process_control.stop_detached_runners`. |

## Files added

| Path | What |
|---|---|
| `tests/characterization/test_legacy_orchestration_deleted.py` | AST-walks `munin/` for forbidden symbols and references (locks deletion). |
| `tests/characterization/test_munin_wake_shim.py` | `munin_wake(name=..., task_json=...)` → succeeded; internally calls `start_async_task(graph_id=name, input=task_json)` → assert task_id returned; assert no row written to `agent_wake_queue` (warnings raised instead). |

---

## Per-class behaviour changes

### Dispatcher post-PR-14

```python
# In munin/production/dispatcher.py
async def run_once(run_id: str):
    run = await store.claim_run(run_id)
    # NO MORE: assistant-placeholder event emission
    # NO MORE: tool_call_id correlation — stream_events covers it via `tool_use_id`
    # INSTEAD: LangGraph stream_events forwarded to UI via PR-02 transport
    supervisor_result = supervisor.ainvoke({"messages": [...], "run_id": run_id})
    # NO MORE: pre_iteration_hook drain — OperatorGuidanceMiddleware handles that
    # NO MORE: state machine `waiting_for_human` polling via tool_call_ids — HITL via LangGraph interrupt_on
    await store.update_run(run_id, state="completed" if supervisor_result["ok"] else "failed")
```

### munin_wake shim

```python
def munin_wake(name: str, task_json: dict) -> dict:
    warnings.warn("munin_wake is deprecated; route via start_async_task directly", DeprecationWarning, stacklevel=2)
    if not settings.MUNIN_LANGGRAPH_URL:
        raise NotImplementedError("AsyncSubAgent tasks require LangGraph server (PR-11)")
    return start_async_task(graph_id=name, input=task_json)
```

## Deps bumped / added

None.

## Rollback plan

Revert restores:
- All deleted files (`munin_agent.py`, `runner.py`, `process_control.py`, `subagents/base.py::ReActSubagentBase`, concrete LDAP/tool_forge/graph_forge agents, etc.)
- `dispatcher.py` assistant-placeholder event adapter + tool_call_id correlation
- `munin_wake` shim replaced with original wake + subprocess.Popen spawn behaviour

Standalone revert, but: PR-15 and PR-16's deletions are downstream; rollback of PR-14 means PR-15/16 bases shift. Acceptable because each PR is reversible only against its own base (PR-13).

BAKED INTO chain: a careful disk inspection confirms no orphan references to deleted import paths. Subagent runs `python -m compileall -q munin tests scripts` as part of validation (mirrors ci.yml line 36-37 step in CI), which catches broken imports.

## Validation plan

1. Characterization tests: All PR-01..PR-13 + 2 PR-14 tests (test_legacy_orchestration_deleted green, munin_wake_shim green). **PR-01's `test_subagent_runner_parity.py`** assertion about wake-claim atomicity + RESULT overflow needs to migrate to assert via `start_async_task`-equivalent properties of cancel_async_task: per project CLAUDE.md "amend the characterization (with reason documented)". The test becomes "AsyncSubAgent starts + cancellation returns partial result snapshot + the equivalent of MAX_INLINE_BODY=12000 overflow → goes to `wake_artifacts/wake_<id>.json`. In graph terms: oversized structured_response persists via the checkpointer at SqliteSaver; assertion that pb overflow boundary still triggers generation of `data/wake_artifacts/wake_<id>.json` (preferred — file stays). Reason logged in changes.md.
2. CI green necessary: ci.yml backend job + e2e_lab + frontend (no `MuninAgent` references).
3. Live-session workflow: chrome-devtools MCP smoke any subagent capability (e.g. swarm handoff from PR-13). All scenarios still functional after deletion.
4. Artifact inspection: `data/shared_state.sqlite.episodic` row exists for the run; `data/langgraph_checkpoints.sqlite` captures thread state from `start_async_task`; legacy wake queue table contains ZERO inserts in this run.
5. Parity manual check: `git grep -E "MuninAgent|try_claim_spawn_slot|stop_detached_runners|_load_subagent" -- munin/` returns empty.

## Issue §9 invariants preserved

| Invariant | Status |
|---|---|
| FastMCP tools + external MCP integration | Untouched — munin_wake shim reuses Tool Gateway; module path != FastMCP surface |
| Scope/OPSEC at tool boundary | Untouched |
| Audit redaction contract | Untouched — `audit.py` still invoked by all tool calls (subagent via start_async_task → supervisor → ToolNode → audit.py entrypoint unchanged) |
| Tool provenance | Untouched |
| Soul human-editable | Untouched |
| Cross-session artifact pattern | **Preserved AND extended** — `data/wake_artifacts/` retained for one release cycle; new state via `data/langgraph_checkpoints.sqlite` already travels |

## Framework verification provenance

PR-14 is pure deletion. No new framework contracts are needed. All references back to the originating PR records:
- Supervisor emit shape: PR-03 (deepagents)
- AsyncSubAgent + SqliteSaver: PR-11
- Swarm: PR-13
- Send workers: PR-12

Uncertainty remaining: PR-14 width is the largest deletion in the migration. Subagent MUST run `python -m compileall -q munin tests scripts` (matches ci.yml line 36-37 step in CI) before asserting green. If an import fails due to missed symbol, surface back to Raven Mind for re-screening — do NOT silently patch via `try/except ImportError: pass`. Document any reasons for keeping legacy symbols in changes.md.