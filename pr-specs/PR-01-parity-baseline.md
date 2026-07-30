# PR-01 — Parity baseline (characterization tests, no production change)

- **Head**: `raven-mind/migration-issue9/pr-01-parity-baseline`
- **Base**: `raven-mind/issue9` (renamed from `raven-mind/migration-issue9` to allow nested-ref heads `raven-mind/migration-issue9/pr-NN-*` — git rejects a ref being both a leaf and a directory prefix in the same path)
- **Open architectural questions**: None — Stage 0 verification complete (pytest-asyncio `asyncio_mode=auto` contract confirmed against pyproject.toml; existing `tests/conftest.py` reuses `isolated_workspace` + `store` fixtures; existing `tests/test_human_in_loop.py` shows the `_CapturingLLM` deterministic-LLM pattern to extend).

---

## Goal

Capture current coordinator, subagent, tool, conversation, persistence, approval, and streaming behaviour in executable tests. These tests are the safety net that authorises every later deletion (issue §12 step 1, "not optional preamble"). No production code edited, no deps bumped.

## Acceptance title (one line)

All 7 characterization test files green on `raven-mind/migration-issue9` HEAD against unchanged production code.

## Issue required end-to-end scenario this PR partially unlocks

**None directly** — enabling step. Without these tests nothing in step 8 (PR-14/15/16 deletions) is defensive (issue §12 step 1 exit criterion).

---

## Files added

All under `tests/characterization/`:

| Path | What it characterizes |
|---|---|
| `tests/characterization/__init__.py` | empty package marker |
| `tests/characterization/conftest.py` | shared fixtures (deterministic LLM client, fake tool catalog) — pure Munin shape, no prod imports |
| `tests/characterization/test_coord_respond_loop_parity.py` | `MuninAgent.respond()` event stream + stop_reason + tool_calls_log |
| `tests/characterization/test_subagent_runner_parity.py` | wake-claim atomicity + RESULT overflow → `wake_artifacts/wake_<id>.json` |
| `tests/characterization/test_tool_catalog_parity.py` | `gen__` prefix; `rehydrate` active=1; `register_state_only`; signature→OpenAI schema shape; `max_iterations` clamp `[1,12]` default 5 |
| `tests/characterization/test_conversation_persistence_parity.py` | `MIGRATION_ID` forward-only checksum; `RUN_STATES` enum; v3.1 extension tables install via `types.MethodType`; timeline/reasoning/tool_calls rows persist close+reopen |
| `tests/characterization/test_shared_state_persistence_parity.py` | All 9 MCP-side tables round-trip; `ConnectionProxy` rowcount + comment-safe splitter; `MUNIN_DB_URL` empty = local file switch |
| `tests/characterization/test_hitl_parity.py` | pause→`waiting_for_human`; approve forwards approved tool args; reject injects rationale next step; guidance queued after pause visible at next step boundary |
| `tests/characterization/test_sse_event_contract_parity.py` | `EventSource` payload schemas; silence detector transitions (`connecting`/`live`/`stale`/`closed`); `Last-Event-ID` resume; 45s silence → stale |

## Files modified

None.

## Files deleted

None.

## Deps added / bumped in this PR

None. Pyproject and app/package.json unchanged.

---

## Current glue being characterized (read-only — not yet replaced)

Mapped to `GLUE_INVENTORY.md` rows:

| Spec subsystem | Current glue file:line | What's characterized |
|---|---|---|
| Coordinator | `munin/core/munin_agent.py:289-637` (`respond()` + `_HARD_CEILING=10_000` + repetition guard WINDOW_SIZE=6/MIN_UNIQUE=3) | stop_reason map (`final_answer`/`max_iterations`/`repetition_detected`), `tool_calls_log` entries match emitted progress events, progress stages (`reasoning`/`provider_reasoning`/`tool_start`/`tool_result`/`completed`/`llm_retry`) in order, `<operator_guidance>` block format via `pre_iteration_hook` |
| Subagent | `munin/subagents/runner.py:296-320`, `munin/mcp/shared_state.py:698 try_claim_spawn_slot` | claim exclusivity (BEGIN IMMEDIATE), RESULT body round-trips <12000 bytes, `MAX_INLINE_BODY=12000` overflow → `data/wake_artifacts/wake_<id>.json` + pointer message |
| Tool | `munin/mcp/registry.py` (rehydrate/register/register_state_only), `munin/mcp/tools/forge_tool.py:81-99` | `gen__` prefix, `rehydrate()` loads active=1, `register_state_only` persists without MCP hot-load, signature→schema conversion shape, max_iterations clamp |
| Conversation/Persistence (prod store) | `munin/production/store.py` (full), `munin/production/store_v3_1.py` (full via `types.MethodType`) | forward-only checksum, v3.1 extensions install without UUID helper regression, timeline/reasoning/tool_calls rows persist |
| HITL | `munin/production/dispatcher.py`, `munin/production/page_agent.py:25`, `app/src/components/chat/blocks/HitlRequest.tsx:36-88` | pause→approve resumes with chosen args; pause→reject injects rationale at next iteration; guidance queued after pause delivered at next step |
| Streaming/UI events | `app/src/lib/useRunEvents.ts`, `app/src/lib/useConversationEvents.ts`, `app/src/app/api/production/[[...path]]/route.ts` | EventSource payload schema, 45s silence detector, Last-Event-ID resume |

---

## Per-file test contracts

### `tests/characterization/conftest.py`

```python
# Shared deterministic LLM fixture (extends existing tests/test_human_in_loop.py pattern)
class _ScriptedLLM:
    """Plays back a pre-written list of OpenAI-shape chat completions in order.

    Reusable across coord / subagent / hitl parity tests so the agent loop runs
    end-to-end without a real provider. Asserts shape:
      chat(messages: list[dict], tools: list[dict], temperature: float) -> dict
    """
    def __init__(self, responses: list[dict]): self._responses = responses; self.calls = 0
    def chat(self, *, messages, tools, temperature, **kwargs):
        self.calls += 1
        return self._responses[(self.calls - 1) % len(self._responses)]

@pytest.fixture
def scripted_llm_factory():   # returns a factory; tests inject their own script
    return lambda responses: _ScriptedLLM(responses)
```

Rationale: existing `tests/conftest.py` already provides `isolated_workspace` (tmp_path + env vars) + `store` (SharedStateStore). The new fixture extends, doesn't duplicate.

### `tests/characterization/test_coord_respond_loop_parity.py`

Framework provenance: pytest-asyncio `asyncio_mode=auto` (pyproject.toml line 55) auto-marks async tests; `MuninAgent.respond()` is sync, so tests are sync. Provenance: Context7 `/websites/pytest-asyncio_readthedocs_io_en_stable` "Auto Mode Configuration" + repo `conftest.py:11-32`.

Assertion contracts:

1. **stop_reason final_answer**: feed `scripted_llm_factory([{"choices":[{"message":{"role":"assistant","content":"done"}}]}])` → `MuninAgent(llm=llm).respond("hello")` → `assert result["stop_reason"] == "final_answer"`.
2. **stop_reason max_iterations**: feed 5 scripted responses with `tool_calls=[{...}]` (scripted tool returns nothing) → `respond("hello", max_iterations=2)` → `assert result["stop_reason"] == "max_iterations"`.
3. **stop_reason repetition_detected**: feed `scripted_llm_factory([tool_call_A_response]*7)` (same tool, same args) → `respond(prompt, max_iterations=10)` → `assert result["stop_reason"] == "repetition_detected"`; assert that the nudge SystemMessage appears exactly once in the run's trace before the abort.
4. **tool_calls_log ordering**: feed 2 tool calls then a final text response → `assert [c["name"] for c in result["tool_calls_log"]] == ["tool_alpha", "tool_beta"]` and each entry has `args`, `result`, `elapsed_ms`, `step` keys.
5. **progress event sequence**: collect emitted progress events into a list via `progress=` callback → assert specific stages appear in order: `reasoning` (every step), `tool_start` before each tool call, `tool_result` after, `completed` exactly once at the end. `provider_reasoning` only when the message carries `reasoning_content`/`reasoning`/`thinking`/`reasoning_summary`.
6. **operator_guidance format**: pass `pre_iteration_hook=lambda step: "<operator_guidance>Prioritize LDAP.</operator_guidance>" if step == 2 else None` → assert it appears only at step 2, as a `system` message wrapping the block, anchored by `<operator_guidance>` open and close tags.

### `tests/characterization/test_subagent_runner_parity.py`

Framework provenance: sqlite exclusive-claim atomicity is the actual production contract (`munin/mcp/shared_state.py:698 try_claim_spawn_slot` uses `BEGIN IMMEDIATE`); reproduce it against the `store` fixture. Provenance: GLUE_INVENTORY §2 + read of `shared_state.py:695-719`.

Assertion contracts:

1. **claim is exclusive**: spawn two `try_claim_spawn_slot` calls in parallel for the same agent name → `assert (r1["claimed"], r2["claimed"]) in {(True, False), (False, True)}`; assert exactly one `spawned=False` row with `existing_pid` pointing at the winner.
2. **RESULT body round-trip**: post a body of 8000 bytes via `post_agent_message` → `fetch_agent_messages` → assert bytes preserved verbatim.
3. **RESULT overflow → artifact**: post a body of 13_500 bytes → assert `fetch_agent_messages` returns a pointer message of shape `{"overflow": true, "artifact_path": "data/wake_artifacts/wake_<id>.json"}`; assert file exists at `isolated_workspace/data/wake_artifacts/wake_<id>.json` and contains the original body.
4. **PROGRESS message before each tool call**: reuse `_CapturingLLM` pattern; assert each tool step emits a PROGRESS message with `tool_name` and `args` and `step` keys before the tool runs.

### `tests/characterization/test_tool_catalog_parity.py`

Framework provenance: `ToolRegistry.rehydrate()` + `register_state_only()` contract (GLUE_INVENTORY §3); max_iterations clamp at `forge_tool.py:99`.

Assertion contracts:

1. **gen__ prefix**: insert a procedural row with `script_path="gen__foo.py"`, `active=1` → `rehydrate()` resolves handler and returns it under key `"gen__foo"`.
2. **active=1 filter**: same insert but `active=0` → `rehydrate()` does not return it.
3. **register_state_only semantics**: from a subprocess-simulated context, call `register_state_only({name, script_path, signature, tags})` → assert a `procedural` row exists with matching columns; assert no entry appears in the live MCP callable cache (only `register()` does that).
4. **signature → OpenAI tool schema shape**: feed signature `{"name": "echo", "description": "...", "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}` → assert converted schema has `name`, `description`, `parameters.type == "object"`, `parameters.properties.text.type == "string"`.
5. **max_iterations clamp `[1,12]` default 5**: occasional fixture asserts the bound by invoking `tool_forge` MCP entry with: (a) missing kwarg → defaults to 5; (b) `max_iterations=50` → clamped to 12; (c) `max_iterations=0` → clamped to 1. (Read-only assertion against the clamp function — no actual LLM call; the MCP handler is exercised with a stubbed LLM downstream, or the clamp helper is exported and called directly.)

### `tests/characterization/test_conversation_persistence_parity.py`

Framework provenance: ProductionStore forward-only checksum + v3.1 monkey-patch extension (GLUE_INVENTORY §5).

Assertion contracts:

1. **forward-only checksum**: instantiate `ProductionStore(path)` on an empty dir → assert `MIGRATION_ID == "v3.1"` (or current value); assert resql `PRAGMA user_version` matches after close+reopen.
2. **RUN_STATES enum**: assert set `== {"queued", "running", "waiting_for_human", "completed", "failed", "interrupted", "cancelled"}`.
3. **v3.1 extension install**: call `install_v3_1_extensions(store)` → assert tables `conversation_collaborators`, `conversation_notes`, `conversation_presence`, `run_guidance_queue` exist; assert columns on `tool_calls` include `parallel_group_id`, `tool_use_id`.
4. **UUID helper regression**: previously install_v3_1 broke UUID generation — insert a row with an auto-generated UUID column, assert the UUID is RFC 4122 v4-shaped (or repo's current shape), proving the regression is gone.
5. **timeline/reasoning/tool_calls persistence**: insert 1 run + 2 timeline rows + 1 reasoning event + 2 tool_calls rows → `store.close()` + `store = ProductionStore(same path)` → assert all 5 rows round-trip with identical content.

### `tests/characterization/test_shared_state_persistence_parity.py`

Framework provenance: `shared_state.py` 9 MCP-side tables + `persistence.py` ConnectionProxy.

Assertion contracts:

1. **9 MCP-side tables present** on `SharedStateStore(path).init_schema()`: `shared_intel`, `tasks`, `agent_messages`, `agent_presence`, `episodic`, `semantic`, `procedural`, `generated_graphs`, `agent_wake_queue` (enum via `sqlite_master`).
2. **ConnectionProxy.rowcount** reflects last INSERT/UPDATE/DELETE — write a row, `assert proxy.rowcount == 1`, write another, assert == 1 again.
3. **comment-safe splitter**: execute a multi-statement SQL string with a `-- comment line` between two statements → assert both run without syntax error (regression coverage for the comment-breaking-statement bug).
4. **MUNIN_DB_URL empty = local file**: set `monkeypatch.delenv("MUNIN_DB_URL")` → `open_connection()` returns a local Sqlite-backed `ConnectionProxy`; assert the file appears at `MUNIN_DATA_PATH/shared_state.sqlite`.
5. **(libsql mode NOT tested here)** — Turso requires network; defer to live-session workflow. Document this gap in changes.md.

### `tests/characterization/test_hitl_parity.py`

Framework provenance: dispatcher + page_agent + HitlRequest frontend block (GLUE_INVENTORY §9). Frontend assertion is component-level (jsdom) since live browser is reserved for chrome-devtools MCP checks.

Assertion contracts:

1. **pause→waiting_for_human**: from a fresh run with a HITL-flagged tool queued, advance dispatcher one step → assert `store.get_run(run_id).state == "waiting_for_human"`; assert a `human_requests` row exists with `tool_name`, `args`, `nonce`.
2. **approve forwards approved args**: insert `human_request_approvals` row with `choice="approve"`, `new_args={...}` → resume dispatcher → assert the executed tool call used `new_args`, not the original proposed args.
3. **reject injects rationale**: insert approval with `choice="reject"`, `justification="too aggressive"` → resume dispatcher → assert next iteration's system message contains `"too aggressive"` in the `<operator_guidance>` block.
4. **guidance queued after pause**: insert a `run_guidance_queue` row while run is `waiting_for_human` → resume → assert `delivered_at_step` is set BEFORE the next LLM call (assert via `episodic_log` action `"guidance_delivered"` preceding next `react_step` log).
5. **PageAgent `validate_page_action`**: with `MUNIN_PAGE_AGENT_ENABLED` unset, every `page_action` raises `PermissionError` (or returns False). With env set, `validate_page_action(role, feature, action, params)` returns True iff action is in `ALLOWED_ACTIONS` and `SENSITIVE_ACTIONS` requires confirmation.
6. **Frontend `HitlRequest.tsx` jsdom smoke**: render `<HitlRequest request={fixture} />` from a fixture matching production API shape → assert Approve + Deny buttons present; click Approve → assert `useResolveHumanRequest().mutate` called with `(id, "approve", nonce, undefined)`.

### `tests/characterization/test_sse_event_contract_parity.py`

Framework provenance: useRunEvents + useConversationEvents + production ASGI route (GLUE_INVENTORY §7). Tested against the actual response payloads from `production/asgi.py` SSE passthrough — need a node test runner for the .ts file (or node:test with tsx if app permits). Verify that the next dev environment can execute; if not, fallback: pure SQLite-backed SSE stream consumer in Python that reads the documented event types and asserts the silence detector + Last-Event-ID resume from JS source as a string regex contract.

Assertion contracts:

1. **EventSource payload schema**: `useRunEvents` consumes `{type: "run-transition", data: {run_id, state, ...}}`, `{type: "tool-start", ...}`, `{type: "tool-result", ...}`, `{type: "heartbeat", ts}`, `{type: "warning", ...}`, `{type: "close", ...}` — each event type asserted present in a scripted SSE stream fixture consumed by the production hook, asserting that the parsed event shape matches the TypeScript interface in `useRunEvents.ts`. (If a real node/jsdom test runner for app/src/lib is too costly for the Python-first CI, fold this into a TypeScript string-contract test: parse `useRunEvents.ts` text via regex to extract the `event.type === "..."` literals and assert they match the documented set.)
2. **silence detector transitions**: feed a stream that goes live→no events for 45s001ms→stale; assert status transitions are `connecting → live → stale`. Fixture time controlled via injected timer mock.
3. **Last-Event-ID resume**: client fetches stream with `Last-Event-ID: 42`, server should only emit events with `id > 42`; assert received list excludes ids <= 42.
4. **SSE passthrough maxDuration=14400**: assert Next.js route file declares `export const maxDuration = 14400` (string-contract test).
5. **45s silence → stale constant**: assert the constant in `useRunEvents.ts` equals `45_000` (string-contract test).

---

## Parity bar (does not apply to PR-01 itself)

This PR **owns** the parity tests. Subsequent PRs assert that the test files added here continue green on their head branch. List here for reference, copy into each later spec:

- `tests/characterization/test_coord_respond_loop_parity.py`
- `tests/characterization/test_subagent_runner_parity.py`
- `tests/characterization/test_tool_catalog_parity.py`
- `tests/characterization/test_conversation_persistence_parity.py`
- `tests/characterization/test_shared_state_persistence_parity.py`
- `tests/characterization/test_hitl_parity.py`
- `tests/characterization/test_sse_event_contract_parity.py`

PR-01 self-asserts: all 7 green when run against `(base: raven-mind/migration-issue9)`.

---

## Rollback plan

Revert is clean: deleting `tests/characterization/` + the directory has zero effect on production code or on later PR code (later PRs add their own NEW tests but don't depend on this directory existing beyond pyproject's `testpaths=["tests"]` glob, which auto-extends). No later PR will reference a deleted file path so a standalone revert won't break PR chain.

## Validation plan

1. **Characterization tests (necessary + sufficient here)**: `pytest tests/characterization/ -v` expected to produce 7 files / ~35-45 test cases, all green. Run via `make` or `poetry run pytest` (the runner in `ci.yml` already invokes pytest in `testpaths`).
2. **CI green (necessary)**: `.github/workflows/ci.yml` backend job must pass on `raven-mind/migration-issue9/pr-01-parity-baseline`.
3. **Live-session workflow (skip)**: parity tests are unit/contract tests; no live-scenario claim in this PR. Workflow unchanged.
4. **chrome-devtools MCP (skip)**: no UI behaviour change to inspect yet.
5. **Artifact inspection (skip)**: no Munin-state or checkpoint state change to verify.
6. **Parity manual check**: After CI green, re-run the full repo test suite `pytest tests/ tests/characterization/ -q` to confirm zero collisions with the existing 23 test files (regression risk: shared `isolated_workspace` env mutation between a new test and an old one — caught here, not in PR-04 later).

## Issue §9 invariants preserved

| Invariant | Status in this PR |
|---|---|
| FastMCP tools + external MCP integration | Untouched — production code unchanged |
| Hugin + offensive tool wrappers | Untouched |
| Scope/OPSEC in tool boundary | Untouched |
| Audit redaction contract | Untouched (redaction rules asserted by existing `test_audit_redaction.py`; not duplicated here) |
| Soul human-editable | Untouched |
| Tool provenance | Characterized `procedural` row shape (test 3 in `test_tool_catalog_parity.py`) — preserves shape as a contract |
| Cross-session artifact pattern | Untouched (free-tier artifact pattern unaffected) |

## Framework verification provenance

- **pytest**: Context7 `/websites/pytest-asyncio_readthedocs_io_en_stable` query "asyncio_mode auto fixture scope event_loop session module reuse_db tmp_path_factory monkeypatch isolation" → confirmed `asyncio_mode=auto` auto-marks async tests; sync tests skip marker. Source line `pyproject.toml:55`.
- **conftest fixtures**: Repo `tests/conftest.py:11-32` — `isolated_workspace` + `store` existing; new `scripted_llm_factory` extends, doesn't replace.
- **MuninAgent.respond() contract**: Repo `munin/core/munin_agent.py:289-448` — signature `(user_input, max_iterations=None, progress=None, conversation_id="", conversation_history=None, pre_iteration_hook=None)`, LLM shape `chat(messages, tools, temperature=0.2, on_retry=...)`, returns `{"stop_reason", "final_content", "tool_calls_log", ...}`. Sync (not async).
- **try_claim_spawn_slot atomicity**: Repo `munin/mcp/shared_state.py:698-719` — BEGIN IMMEDIATE + optimistic CAS via primary key + previous row values, returning `{"claimed": bool, "existing_pid": int|None, "reason": str}`.
- **RESULT overflow**: Repo `munin/subagents/runner.py:363-371` — `MAX_INLINE_BODY = 12_000` bytes; oversized result → `data/wake_artifacts/wake_<id>.json` + pointer message in `agent_messages`.
- **forge max_iterations clamp**: Repo `munin/mcp/tools/forge_tool.py:81-99` — default 5, clamped to `[1, 12]`.

Uncertainty remaining: the SSE contract test for `useRunEvents.ts` may need a node/tsx test runner; if `ci.yml` frontend job doesn't already run one (verify during PR-01 delegation), fall back to TypeScript string-contract tests (parse `useRunEvents.ts` text, extract event-type literals) to get coverage without new runner infra. This will be raised explicitly in the PR description if fallback is needed. Characterization value preserved either way.