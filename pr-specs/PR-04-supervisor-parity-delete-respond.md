# PR-04 — Supervisor parity: delete `respond()` callers (flag removal)

- **Head**: `raven-mind/migration-issue9/pr-04-supervisor-parity-delete-respond`
- **Base**: `raven-mind/migration-issue9/pr-03-deep-agent-supervisor`
- **Open architectural questions**: None. PR-03 verified all framework contracts (deepagents, middleware, stream_events, task_tool). This PR's sole responsibility is verifying "no remaining caller of `respond()` once the flag is flipped", which is repo-introspection not framework-dependent.

---

## Goal

After at least one live-session run with `MUNIN_RUNTIME=supervisor` (validation gate from PR-03) produced identical progress events + tool_calls_log to a comparable `legacy` run, remove the flag and remove `MuninAgent.respond()` from every reachable path: dispatcher, Discord bridge, `munin_chat` MCP tool. PR-04 is the first explicit "delete obsolete glue" mini-PR in this migration — deliberate separation per issue §12 ("Do not merge 'remove obsolete glue' work into the same PR that introduces its replacement").

## Acceptance title (one line)

`MuninAgent.respond()` has zero call sites; `MUNIN_RUNTIME` env flag removed; `runtime_adapter.select_runtime` collapses to a single `supervisor` path.

## Issue required end-to-end scenarios this PR partially unlocks

Confirms issue acceptance criteria #1 (`Deep Agents is the default coordinator runtime`) + #2 (`The coordinator no longer depends on Munin's hand-written ReAct loop for normal execution`) + #3 (`Duplicated subagent ReAct loops are removed or isolated behind a temporary compatibility adapter`) — partial: coordinator only; subagent ReAct loop deletion is PR-14.

---

## Files modified

| Path | What changes |
|---|---|
| `munin/production/dispatcher.py` | Replace `runner = select_runtime(settings.MUNIN_RUNTIME)(...)` with direct `supervisor_runner(prompt, kwargs)` call. Remove the dual-path. |
| `munin/integrations/discord_config.py` | Replace `MuninAgent(SETTINGS).respond(prompt, max_iterations=config.max_iterations)` call with `supervisor_runner(prompt, {"max_iterations": config.max_iterations})`. Confirm at line ~43. |
| `munin/mcp/tools/munin_tools.py` | The `munin_chat` MCP tool (line ~514) currently calls `MuninAgent`'s ReAct loop internally; repoint to `supervisor_runner`. Subagent ReAct path stays for now (deleted PR-14). |
| `munin/mcp/config.py` (or wherever Settings lived after PR-03) | Remove `MUNIN_RUNTIME` env field entirely — default-only path. |
| `munin/core/runtime_adapter.py` | Collapse `select_runtime()` to direct supervisor invocation; the legacy callable `_legacy_runner` removed. File shrinks to ~1 function. |
| `changes.md` | Append hand-off row referencing PR-04 with provenance per global rule. |

## Files deleted

| Path | Why |
|---|---|
| `munin/core/munin_agent.py::MuninAgent.respond()` (and the now-orphan `MuninAgent.__init__` / `_system_prompt` / `_current_catalog` / repetition guard inlined in `respond()`) — file shrinking to just `_system_prompt` assembly if reused by supervisor builder, else deleted | Coordinator ReAct loop obsolete after supervisor parity proven. Issue §12 step 8: delete after parity. Evidenced by PR-03's `test_supervisor_parity.py` byte-identical assertion. |
| `raven-mind/migration-issue9` ReAct-loop helper files only if orphaned (covered in PR-08 store_v3_1 etc.) — DEFER detail review to PR-04 delegation: any helpers exclusively trusting `MuninAgent` shape and not used by supervisor_builder get deleted here. |

## Files added

| Path | What |
|---|---|
| `tests/characterization/test_no_respond_call_sites.py` | AST-walks the `munin/` tree asserting there are zero call sites of `MuninAgent.respond` / `MuninAgent(` followed by `.respond(`; locks deletion against silent reintroduction. |

---

## Per-class behavior

### `supervisor_runner` collapse

After PR-03's `runtime_adapter.select_runtime` returned two paths, PR-04 collapses:

```python
def supervisor_runner(prompt: str, kwargs: dict) -> dict:
    """Single coordinator runtime. Builds the supervisor once per process via
    build_supervisor(...); invokes with kwargs (run_id, max_iterations, progress, ...)."""
```

Behaviour unchanged vs PR-03's supervisor mode; the PR-04 sweeping deletion just removes the now-redundant `legacy` branch.

---

## Tests added

| Test path | Assertion contract |
|---|---|
| `tests/characterization/test_no_respond_call_sites.py` | Walks `munin/` source tree via `ast.parse`; asserts zero occurrences of `Attribute(value=Name(id="MuninAgent"), attr="respond")` or `Call(func=Attribute(attr="respond"))` where callee is `MuninAgent`. Locks the deletion. |
| (PR-03 `test_supervisor_parity.py` relaxed) | Existing PR-03 test asserted equivalency between modes by running both; with the legacy mode deleted, the test is rewritten as a one-sided assertion "supervisor output matches fixture X" compared against a single golden fixture committed in the PR-04 branch. The fixture was captured during PR-03's live-session parity run + committed to `tests/characterization/fixtures/supervisor_parity_v1.json` so future drift is visible dr-vs fixture. |

## Parity bar (PR-01 preserved)

PR-01's `test_coord_respond_loop_parity.py` originally asserted behaviours of `MuninAgent.respond()`. Once `respond()` is deleted, those assertions must relocate from "test asserts respond() does X" to "test asserts supervisor emits the same X". **Decision Policy for this PR**: the PR-01 characterization tests are MUTUALLY acceptable to weaken here — the existing assertion language ("`respond()` returns stop_reason == final_answer") becomes ("supervisor emit stop_reason == final_answer"); the assertions themselves are preserved byte-for-byte because the supervisor shield emits identical shapes (PR-03 verified). Per project CLAUDE.md: "amend the characterisation (with reason documented in changes.md) or accept the new behaviour (with reason documented)". PR-04 takes the former path: assertions repoint to supervisor while exercising identical observable semantics — a documented rename, not a weaken. changes.md row records this.

The other 6 PR-01 test files (`test_subagent_runner_parity.py`, `test_tool_catalog_parity.py`, `test_conversation_persistence_parity.py`, `test_shared_state_persistence_parity.py`, `test_hitl_parity.py`, `test_sse_event_contract_parity.py`) remain green unmodified.

---

## Deps bumped / added in this PR

None. PR-03 already added all required deps. PR-04 only deletes lines + adds one AST-walk test.

---

## Rollback plan

Revert restores `MuninAgent.respond()` and all three call sites (dispatcher, discord_config, munin_chat); re-adds `MUNIN_RUNTIME` env flag + dual-path `select_runtime`; removes the AST test. Standalone revert does not affect PR-05 onwards (PR-05 Tool Gateway assumes supervisor runtime is the default — but it's a transitive parent so reverting PR-04 cascades; in chain the base of PR-05 IS PR-04 head so revert of PR-04 == rebuild PR-05 base. The spec explicit "head branch of PR #04 is the base of PR #05" sequence rules this).

---

## Validation plan

1. Characterization tests: `pytest tests/characterization/ -v` → PR-04's `test_no_respond_call_sites.py` passes; the other 7 PR-01 tests (with the repointed `test_coord_respond_loop_parity.py`) all green against supervisor output.
2. CI green necessary: `.github/workflows/ci.yml` backend + e2e_lab jobs pass. (Tests are the primary authority here because deleting `respond()` is a static + test assertion.)
3. Live-session workflow: trigger once with no env var (`MUNIN_RUNTIME` removed). chrome-devtools MCP: send prompt, confirm routing goes through supervisor + tool calls succeed. Screenshots in `evidence/PR-04/`.
4. Artifact inspection: `munin-state` artifact rows: `episodic` table action `react_step` entries still appear (supervisor logs them via ProgressEmitMiddleware) — confirms the existing audit/observability surface remains authoritative.
5. Parity manual check: `git grep "MuninAgent" --source munin/` should return zero results after PR-04 merge.

## Issue §9 invariants preserved

| Invariant | Status |
|---|---|
| FastMCP tools | Untouched |
| Scope/OPSEC in tool boundary | Untouched — tool dispatch via Munin catalog preserves OPSEC preflight |
| Audit redaction contract | Untouched |
| Soul human-editable | Untouched — supervisor wired to soul/ paths via memory= kwarg |
| Tool provenance | Untouched |
| Cross-session artifact pattern | Untouched |

## Framework verification provenance

PR-04 is a deletion + caller-repoint PR with no new framework contracts. The relevant proof lies with PR-03's verification record (DeepWiki `langchain-ai/deepagents` confirmations). For completeness: PR-03's record links to deepagents source paths for `create_deep_agent` + middleware + SubAgent via `_compile_spec → langchain.agents.create_agent`; PR-04 modifies zero new framework contracts.

Uncertainty remaining: the subagent ReAct path stays in this PR (deleted PR-14). Issue acceptance #3 allows temporary isolation behind adapter; the subagent ReAct runner continues running because the runner subprocess still spawns it. PR-07 replaces spawn-flavoured forge with Subagent Factory + LangGraph native. Until then, assert in PR-04's pr body: "subagent ReAct loop preserved per issue acceptance #3 'temporary compatibility adapter' clause; deletion committed in PR-14."