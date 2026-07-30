# PR-03 — Deep Agent supervisor (behind env-flag adapter)

- **Head**: `raven-mind/migration-issue9/pr-03-deep-agent-supervisor`
- **Base**: `raven-mind/migration-issue9/pr-02-ai-sdk-transport`
- **Open architectural questions**: None. Stage 0 verification complete (DeepWiki `/langchain-ai/deepagents` confirmó: `create_deep_agent(model, tools, system_prompt, subagents, async_subagents, backend, checkpointer, interrupt_on, response_format, middleware, memory, skills, permissions, state_schema, context_schema, store, debug, name, cache)`; default middleware stack shippeado con FilesystemMiddleware + SubAgentMiddleware mandatory y Sin exclusion silenciosa; SubAgent dict-form compila via `langchain.agents.create_agent` bajo `create_deep_agent`; `task` tool returns `Command(update={**state, "messages":[ToolMessage]})` con ToolMessage sacado de `structured_response` o último AIMessage non-empty; HumanInTheLoopMiddleware via `interrupt_on` + `Command(resume=decisions[])`).

---

## Goal

Replace `MuninAgent.respond()` with a Deep Agents supervisor (`create_deep_agent`) assembled behind `MUNIN_RUNTIME=supervisor` env flag (default `legacy` so current behaviour unchanged at runtime in this PR). The supervisor runs behind a compatibility adapter preserving the exact `progress` event stream + `tool_calls_log` shape so the frontend (PR-02 transport) sees no difference. **Flag is intentionally temporary** — PR-04 deletes it once parity proven on a live run. (Issue non-goal: "Do not keep legacy orchestration and the new runtime permanently active as equal authorities".)

## Acceptance title (one line)

`MUNIN_RUNTIME=supervisor` produces identical `tool_calls_log` order/content + `progress` events + `stop_reason` map for the same prompt + fake LLM fixture as legacy `MuninAgent.respond()`.

## Issue required end-to-end scenarios this PR partially unlocks

None directly — enabling step. Proves coordinator runtime eligible for issue acceptance criteria #1 (`Deep Agents is the default coordinator runtime`) and #2 (`The coordinator no longer depends on Munin's hand-written ReAct loop`), but only after PR-04 removes the flag.

---

## Files added

| Path | What |
|---|---|
| `munin/core/supervisor.py` | `build_supervisor(...)` → compiled LangGraph Pregel from `create_deep_agent(...)` wrapping the Munin-configured `ChatOpenAI` (`make_langchain()` from `munin/core/llm_client.py`), with Munin-middleware layer injected. |
| `munin/core/middleware/__init__.py` | Munin middleware package marker. |
| `munin/core/middleware/operator_guidance.py` | `OperatorGuidanceMiddleware` — replaces `pre_iteration_hook`. Reads `run_guidance_queue` at iteration boundary, injects `<operator_guidance>` `SystemMessage` into `messages`, sets `delivered_at_step`. |
| `munin/core/middleware/repetition_guard.py` | `RepetitionGuardMiddleware` — implements current WINDOW_SIZE=6 / MIN_UNIQUE=3 with nudge-once + trip-once. No deepagents native equivalent — pure Munin middleware. |
| `munin/core/middleware/progress_emit.py` | `ProgressEmitMiddleware` — translates LangGraph `stream_events(version=v3)` events into existing `progress(channel)` event dict shape (`{stage, iteration, message, ...}`). Maintains PR-02 BFF contract. |
| `munin/core/runtime_adapter.py` | `select_runtime()` — given `MUNIN_RUNTIME` env, returns either legacy `MuninAgent.respond()` callable or new `supervisor.invoke` callable; structural shim without behavioural decisions. Both paths return the same result dict shape. |
| `tests/characterization/test_supervisor_parity.py` | Bit-identical comparison between supervisor and `respond()` for the same inputs (fixtures reused from PR-01's `conftest.py`). |
| `tests/characterization/test_operator_guidance_middleware.py` | Pause→guidance injection end-to-end through middleware. |
| `tests/characterization/test_repetition_guard_middleware.py` | 6-iteration trip boundary + nudge-once semantics + second-trip abort with `stop_reason="repetition_detected"`. |
| `tests/characterization/test_progress_emit_middleware.py` | Stream events v3 → progress event dict shape parity. |

## Files modified

| Path | What changes |
|---|---|
| `munin/production/dispatcher.py` | Routing layer: at the single chokepoint where `MuninAgent(...).respond(...)` is called today, swap for `select_runtime(settings.MUNIN_RUNTIME)(...)`. Default flag value `legacy` (current behaviour preserved). No flag-plumbing elsewhere. |
| `pyproject.toml` | Add: `deepagents`, `langgraph-swarm`, `langgraph-sdk`, `langgraph-cli[inmem]`, `langgraph-checkpoint-sqlite`. See deps section. |
| `munin/mcp/config.py` (or wherever `Settings` lives — verify during delegation) | Add `MUNIN_RUNTIME` env field (default `"legacy"`). |

## Files deleted

None. `MuninAgent.respond()` remains callable (legacy flag path). PR-04 deletes it.

---

## Per-class/function behavior

### `munin/core/supervisor.py::build_supervisor()`

```python
def build_supervisor(
    *,
    model: "BaseChatModel",            # validated ChatOpenAI from llm_client.make_langchain()
    tools: list,                       # Tool Gateway output (PR-05); here: existing MuninAgent catalog
    system_prompt: str,                # assembled from soul/ via prompting.model_family + LANGUAGE_CONTRACT
    checkpointer: "Checkpointer | None",   # None in PR-03 — wired in PR-11
    subagents: list = None,            # Empty in PR-03 — wired in PR-07
    interrupt_on: dict | None = None,  # Empty in PR-03 — wired in PR-07/PR-09
    extra_middleware: list | None = None,   # Munin middleware layer
) -> "CompiledStateGraph":
    """Return a compiled LangGraph Pregel via create_deep_agent(...) with Munin middleware."""
```

Framework provenance: DeepWiki `langchain-ai/deepagents` "What is the exact signature and behavior of create_deep_agent". `create_deep_agent` accepts `BaseTool/Callable/dict` for tools (no manual schema), accepts `BaseChatModel` for `model=`.

The supervisor's three Munin-specific middlewares compose into `extra_middleware`:

```python
extra_middleware = [
    ProgressEmitMiddleware(progress_sink),       # captures stream_events for UI
    OperatorGuidanceMiddleware(run_id, store),    # replaces pre_iteration_hook
    RepetitionGuardMiddleware(window_size=6, min_unique=3, max_iterations=huge),
]
```

`FilesystemMiddleware` + `SubAgentMiddleware` are mandatory scaffolding (cannot be excluded, raising `ValueError`); we don't supply them — they default. `MemoryMiddleware` gets `memory=[soul/identity.md, soul/principles.md, soul/goals.md]` paths to load soul content into system prompt (replacing `prompting.py`'s baked-in string assembly).

### `munin/core/middleware/operator_guidance.py::OperatorGuidanceMiddleware`

```python
class OperatorGuidanceMiddleware(AgentMiddleware):
    """Reads run_guidance_queue at each iteration boundary; injects SystemMessage
    with <operator_guidance>{text}</operator_guidance> wrapping; sets
    run_guidance_queue.delivered_at_step BEFORE the next LLM call in same iter."""
    def __init__(self, run_id: str, store: SharedStateStore): ...
    def before_model(self, state: AgentState, llm, request) -> AgentState:
        # Mirror dispatcher.pre_iteration_hook: drain run_guidance_queue rows where
        # delivered_at_step IS NULL and run_id matches; emit SystemMessage(str(block)).
        ...
```

Behavior parity: PR-01's `test_hitl_parity.py` ("guidance queued after pause delivered at next step boundary") must pass identically when run with `MUNIN_RUNTIME=supervisor` (handled in PR-04's parity manual check); PR-03 adds a dedicated middleware test (`test_operator_guidance_middleware.py`) against the primitive.

### `munin/core/middleware/repetition_guard.py::RepetitionGuardMiddleware`

```python
class RepetitionGuardMiddleware(AgentMiddleware):
    """WINDOW_SIZE=6, MIN_UNIQUE=3, nudge-once then abort-on-second-trip.

    In before_model: gather last WINDOW_SIZE tool calls; build a fingerprint set
    over (tool_name, args json). If |set| < MIN_UNIQUE and already nudged in
    current run: emit Command(update={"stop_reason": "repetition_detected",
    "stop_loop": True}). Else if |set| < MIN_UNIQUE and not yet nudged:
    inject a SystemMessage("You are repeating tool calls. Try a different
    approach.") into state["messages"], set nudged=True on state metadata.
    """
```

PR-01's `test_coord_respond_loop_parity.py` assertion #3 ("stop_reason repetition_detected + first nudge") + PR-03's dedicated `test_repetition_guard_middleware.py` together establish parity.

### `munin/core/middleware/progress_emit.py::ProgressEmitMiddleware`

```python
class ProgressEmitMiddleware(AgentMiddleware):
    """Translates LangGraph stream_events(v3) → existing progress dict shape.

    Subscribes to events with names matching:
      on_chat_model_stream → stage="reasoning"
      on_chat_model_end → if message has reasoning content → stage="provider_reasoning"
      on_tool_start → stage="tool_start", {"iteration", "tool_name", "args"}
      on_tool_end → stage="tool_result", {"iteration", "tool_name", "result"}
      on_chain_end(root=run step) → stage="completed", message="Model returned a final response"
    Emitting progress dict via the same callable so PR-02's BFF sees no event vocabulary shift.
    """
```

### `munin/core/runtime_adapter.py::select_runtime()`

```python
def select_runtime(mode: str) -> Callable[[str, dict], dict]:
    """Mode = 'legacy' → MuninAgent.respond(...); Mode = 'supervisor' → supervisor.invoke."""
    if mode == "supervisor": return _supervisor_runner
    return _legacy_runner
```

Structural-only; no behavioural decisions; both `_legacy_runner` and `_supervisor_runner` return the same `{"stop_reason", "final_content", "tool_calls_log", "progress_events"}` dict shape. (PR-02 transport operates on `progress_events` if backend exposes them; otherwise operates on the legacy SSE stream emitted by the dispatcher regardless of runtime mode.)

### dispatcher.py edit (single chokepoint)

Before:
```python
agent = MuninAgent(settings)
result = agent.respond(prompt, max_iterations=...)
```

After:
```python
runner = select_runtime(settings.MUNIN_RUNTIME)
result = runner(prompt, {"max_iterations": ..., "run_id": run_id, "progress": event_sink, ...})
```

Both paths produce identical effects downstream (events back to `run_events` table, persisted to `timeline_messages`).

---

## Tests added

| Test path | Assertion contract |
|---|---|
| `tests/characterization/test_supervisor_parity.py` | Same prompt + same scripted-LLM fixture → assert `tool_calls_log` list identical order/content (testing through both `legacy` and `supervisor` mode yields zero-diff results); `progress_events` list identical stage sequence; `stop_reason` value identical across `{final_answer, max_iterations, repetition_detected}`; `<operator_guidance>` instruction timing/format identical when `pre_iteration_hook` shim is wired |
| `tests/characterization/test_operator_guidance_middleware.py` | Mw wired with a fake `run_guidance_queue` containing one queued row; supervisor runs 3 iterations + LLM fixture; assert `delivered_at_step == 2` (matches legacy), `SystemMessage` with `<operator_guidance>...</operator_guidance>` block present at messages list index `step+1` |
| `tests/characterization/test_repetition_guard_middleware.py` | Scripted LLM emits same (tool_call, args) on every step. 5 calls → nudge SystemMessage appears exactly once. 6 calls → `stop_reason == "repetition_detected"`; re-entering iteration after abort no longer runs |
| `tests/characterization/test_progress_emit_middleware.py` | Provided a fixed stream_events fixture (list of synthetic v3 events), asserts the emitted progress dicts are exactly the legacy shape: `{stage, iteration, message, [tool_name], [args]}` for each kind. Includes `reasoning`, `provider_reasoning`, `tool_start`, `tool_result`, `completed`, `llm_retry` (the latter emitted via wrapper's catch on retry-callback) |

## Parity bar (PR-01 preserved)

PR-01's 7 characterization tests must remain green on this PR's head branch when `MUNIN_RUNTIME=legacy` (default). New tests above run against the `supervisor` mode. The subagent/tool/conversation/persistence/HITL/SSE characterization tests (PR-01) all run with the default flag and observe unchanged legacy paths.

## Deps bumped / added in this PR

| Dep | Pin intent | Why |
|---|---|---|
| `deepagents` | `>=0.1,<1` (or whatever Context7 / PyPI shows is current — subagent verifies exact stable release at PR time) | Default coordinator runtime; Context7 confirms `create_deep_agent` + SubAgent/CompiledSubAgent/AsyncSubAgent TypedDicts. Pin `<1` to lock API surface against breaking-major risk. |
| `langgraph-swarm` | `>=0.0.5,<0.2` (or current at delegation time) | Required by later PRs (#07/#13) for swarm graph; declared now with other deepagents-family deps so install is atomic. Pinned upper to lock against breaking change. |
| `langgraph-sdk` | `>=0.1,<1` | Required by PR-11/PR-13 for AsyncSubAgent client. Declared now. |
| `langgraph-cli[inmem]` | `>=0.1,<1` (dev group) | Required by PR-11 to spawn `langgraph dev` server. Declared in `[tool.poetry.group.dev.dependencies]`. |
| `langgraph-checkpoint-sqlite` | `>=1.0,<2` | Required by PR-11 for SqliteSaver local persistence on runner (DeepWiki confirmed path/file-based persistence behaviour). |
| (`langgraph` — existing `>=0.2.40`) | May need bump to `>=0.3` if deepagents requires it | **Subagent verifies** at PR time via Context7 whether deepagents depends on langgraph >= some-version-later-than-0.2.40. If yes, bump in this PR's pyproject, document reason in PR body + changes.md. No silent bump. |

All pyproject changes are explicit GLUE_INVENTORY §13 / IMPROVEMENT_BACKLOG §dependency-additions commitments and are allowed per migration scope. No silent coding against newer docs.

---

## Rollback plan

Revert removes: `munin/core/supervisor.py`, `munin/core/middleware/`, `munin/core/runtime_adapter.py`, the 4 new tests, the pyproject deps, the dispatcher edit, and the `Settings.MUNIN_RUNTIME` field. Reverts `dispatcher.py` to its previous `MuninAgent(...).respond(...)` call. `raven-mind/migration-issue9/pr-04-...` not yet created so the chain is unaffected. Revert is standalone.

---

## Validation plan

1. Characterization tests: `pytest tests/characterization/ -v` → all 7 PR-01 tests green under default `MUNIN_RUNTIME=legacy`; the 4 new PR-03 tests green under `MUNIN_RUNTIME=supervisor`. Subagent must verify pyproject install with the new `langgraph-checkpoint-sqlite` etc. works on ubuntu runner before CI is green.
2. CI green necessary: `.github/workflows/ci.yml` backend job passes.
3. Live-session workflow: trigger with `MUNIN_RUNTIME=supervisor` env var; tunnel URL from job summary. **chrome-devtools MCP**:
   - Send a chat prompt exercising a tool (e.g. `cve_lookup`). Assert: text streams (PR-02 transport intact), tool chip renders, tool result appears, no duplicate events.
   - Reload chat. Assert conversation persistence is unchanged (PR-01 test covers again).
   - Save screenshots in `evidence/PR-03/`.
4. Artifact inspection: `data/shared_state.sqlite` — `run_events` table for the supervisor run, sequence asserted identical to a sibling `legacy` run for the same prompt (manual diff in `evidence/PR-03/`).
5. Parity manual check: Run `pytest tests/characterization/test_supervisor_parity.py tests/characterization/test_coord_respond_loop_parity.py -v` after merge — both green.

## Issue §9 invariants preserved

| Invariant | Status |
|---|---|
| FastMCP tools + external MCP integration | Untouched — supervisor receives Munin's exact tool catalog; no FastMCP replacement |
| Scope/OPSEC in tool boundary | Untouched — supervisor's tool dispatch is via Munin catalog → existing tools → existing OPSEC preflight runs unaltered |
| Audit redaction contract | Untouched — `audit.py` runs unchanged; ProgressEmitMiddleware routes through same sink |
| Soul human-editable | Preserved via `memory=[soul/...]` to `MemoryMiddleware` — same file paths |
| Tool provenance | Untouched |
| Cross-session artifact pattern | Untouched (no checkpointer wired in this PR) |

## Framework verification provenance

- **`create_deep_agent` kwargs + middleware stack**: DeepWiki `langchain-ai/deepagents` ask_question "What is the exact signature and behavior of create_deep_agent and what middleware classes ship by default" — confirmed `model, tools, system_prompt, subagents, async_subagents, backend, checkpointer, interrupt_on, response_format, middleware, memory, skills, permissions, state_schema, context_schema, store, debug, name, cache`; mandatory `FilesystemMiddleware + SubAgentMiddleware` cannot be silently removed.
- **SubAgent dict compiles via `langchain.agents.create_agent`**: DeepWiki ask_question "When a SubAgent's spec uses the dict form SubAgent with name/description/system_prompt/tools/model, what does create_deep_agent do internally to compile it into a runnable" — confirmed `_build_task_tool → _compile_spec → create_sub_agent → langchain.agents.create_agent`.
- **Subagent inherits parent backend, checkpointer managed at top level**: same DeepWiki source.
- **CompiledSubAgent with `runnable=` field accepts pre-built Pregel**: same DeepWiki source — required for PR-09.
- **task tool returns Command(update={**state, "messages":[ToolMessage]})**: same DeepWiki source — ToolMessage content = JSON of `structured_response` or last non-empty AIMessage.
- **stream_events(version="v3") surfaces subagent reasoning + tool calls for UI streaming**: same DeepWiki source — required for ProgressEmitMiddleware mapping.
- **interrupt_on + Command(resume=decisions[])**: same DeepWiki source — required for HITL parity in PR-07 supervisor-level interrupts; here only declarative shape (no interrupts wired).

Uncertainty remaining: the exact pin upper bounds (`<1` on deepagents, `<0.2` on langgraph-swarm, etc.) need verification against PyPI at delegation time; subagent should `pip index versions deepagents` or query Context7 stable-version page to lock. No architectural uncertainty remaining.