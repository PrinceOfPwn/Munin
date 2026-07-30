# Implementation Roadmap — Migration to Deep Agents + LangGraph + Vercel AI SDK

*Issue: PrinceOfPwn/Munin#9 | Branch: `raven-mind/migration-issue9` | Generated: 2026-07-30*

This roadmap operationalises the 8-step migration sequence in issue §12. Each step maps to
one or more Pull Requests. The 8 steps are a strong prior — deviations are documented inline
with rationale (issue §12 authorises reordering / splitting / merging based on repository
evidence).

For per-row detail (current glue, target primitive, Munin invariants preserved, evidence,
ranking columns, risk/cost), see `IMPROVEMENT_BACKLOG.md`. For the raw inventory of custom
code being replaced, see `GLUE_INVENTORY.md`.

## Discovery findings that shape the sequence

Discovery (steps 1.a through 1.g of the brief) produced three load-bearing conclusions:

1. **deepagents replaces a large swath of existing glue** — verified against the deepagents
   source via DeepWiki, not against memory. The default `create_deep_agent` middleware stack
   (TodoListMiddleware, FilesystemMiddleware, SubAgentMiddleware, AsyncSubAgentMiddleware,
   SkillsMiddleware, MemoryMiddleware, SummarizationMiddleware, HumanInTheLoopMiddleware)
   maps 1:1 onto behaviour Munin reimplements by hand today. `SubAgent` (sync, dict),
   `CompiledSubAgent` (sync, LangGraph runnable) and `AsyncSubAgent` (background, remote)
   give a clean three-run selection surface for the Subagent Factory.

2. **Async subagents require a LangGraph Platform server, by design** (DeepWiki confirmed).
   `AsyncSubAgentMiddleware` talks to an Agent Protocol endpoint via `langgraph-sdk`; it has
   no in-process bypass. To keep async subagents working inside the GitHub Actions runner
   ephemeral environment (our canonical runtime), the sequencing requires a self-hosted
   LangGraph server. Verified path: `langgraph build && langgraph up` (Docker, port 8123)
   with `langgraph-checkpoint-sqlite` for local-disk persistence (survives container restart
   when the file is on a mounted volume). This is the reason step 7 includes a deployment
   change in the live-session workflow.

3. **Tool/Agent/Workflow registries do not ship with deepagents** — confirmed. Munin must
   own these (issue §2 Autonomy Kernel + §3/§5/§6). The current `procedural` and
   `generated_graphs` tables are the seed of the Tool and (limited) Subagent registries;
   the Workflow Registry is new.

Non-trivial constraint surfaced: front-end has **no** Vercel AI SDK dependency currently
(verified from `app/package.json` + lockfile). Step 2 starts from zero — the migration adds
`ai` + `@ai-sdk/react` and adapts the chat-block components. This is a known net new layer,
not a config tweak.

## Sequence at a glance

| Step | Title | Exit criterion (parity proven) | PRs (planned) | Risk |
|---|---|---|---|---|
| 1 | Characterization & parity tests | All new parity tests green on `main`; no production change | 1 (`feat/parity-baseline`) | L |
| 2 | Vercel AI SDK transport / UI | UI messages + tool render + reconnect work over new protocol; Python backend authoritative and unchanged | 2 (`feat/ai-sdk-transport`) | M |
| 3 | Deep Agent coordinator | New supervisor behind adapter produces tool_calls_log + progress events byte-identical to legacy `respond()` for the same inputs | 3 (`feat/deep-agent-supervisor`) + 4 (`feat/supervisor-parity` delete `respond()` callers) | H |
| 4 | Tool Gateway + Tool Factory | `invoke_registered_tool` works same-run after `create_tool`; persistent tool rehydates across runs | 5 (`feat/tool-gateway`) + 6 (`feat/tool-factory-evolution`) | M |
| 5 | Subagent Factory + Agent Registry | Dynamic specialist forged, invoked same-run, returns traceable result; persistent specialist re-invoked across sessions; `MUNIN_MAX_NESTED_SUBAGENTS` removed | 7 (`feat/subagent-factory`) + 8 (`feat/agent-registry`) | H |
| 6 | Workflow Factory / CompiledSubAgent | Multi-node workflow with ≥1 deterministic node + ≥1 agent node compiles; invoked as CompiledSubAgent by supervisor; result `structured_response` | 9 (`feat/workflow-factory`) + 10 (`feat/workflow-registry`) | H |
| 7 | Native coordination (Send, Command, handoffs, checkpointers, langgraph-up server) | Parallel workers (Send) execute + aggregate deterministically with per-worker UI identity; Cancel interrupt + LangGraph SqliteSaver persists across restart; peer handoff works | 11 (`feat/langgraph-server-deployment`) + 12 (`feat/send-workers`) + 13 (`feat/native-coordination`) | H |
| 8 | Remove obsolete glue | Wake queue, runner subprocess, dispatcher duplicate events, store_v3_1 monkey-patch, old SSE adapters deleted; only stubs remain for MCP compat | 14 (`chore/remove-legacy-orchestration`) + 15 (`chore/remove-legacy-store-v3-1-overlay`) + 16 (`chore/remove-legacy-frontend-stream-adapters`) | M (post-parity) |

15–16 PRs is an estimate, not a target. The implementation (Raven Mind) retains authority
to split or merge based on:
- regression risk (split if touching the same file twice breaks bisect),
- reviewability (merge if splitting leaves the system in an invalid intermediate state),
- rollback (each PR must be revertible without breaking later PRs).

## Step 1 — Characterization & parity tests

### Goal
Capture current coordinator, subagent, tool, conversation, persistence, approval, and
streaming behaviour in executable tests. These tests become the safety net that authorises
every later deletion (issue §12 step 1, "not optional preamble").

### Source areas (issue §12 step 1 explicit list)

| Area | Files | What is captured |
|---|---|---|
| Coordinator | `munin/core/munin_agent.py`, `munin/core/conversations.py`, `munin/core/prompting.py` | `respond()` returns shape, stop_reason map (`final_answer`/`max_iterations`/`repetition_detected`), `tool_calls_log` entries, `progress` event sequence (`reasoning`/`provider_reasoning`/`tool_start`/`tool_result`/`completed`/`llm_retry`), repetition guard trip + nudge text, `<operator_guidance>` system block format, memory.log_step params |
| Subagent | `munin/subagents/runner.py`, `munin/subagents/base.py`, `munin/subagents/tool_forge.py`, `munin/subagents/graph_forge.py`, `munin/subagents/sandbox.py`, `munin/subagents/process_control.py` | wake-claim atomicity (EXCLUSIVE lock); `MAX_INLINE_BODY=12000` overflow → `wake_artifacts/wake_<id>.json`; `ReActSubagentBase.max_iterations=8` exit calibration; PROGRESS messages before each tool call; tool catalog construction (`build_tool_catalog` loads `gen__*` from `procedural`); AST guard banned modules; child runner exit-after-idle 120s |
| Tool | `munin/mcp/registry.py`, `munin/mcp/tools/forge_tool.py`, `munin/mcp/tools/graph_forge_tool.py` | `gen__` prefix; `rehydrate()` reloads `active=1`; `register_state_only` (no MCP hot-load); signature → OpenAI tool schema conversion shape; `max_iterations` clamp `[1,12]` default 5 |
| Conversation | `munin/production/store.py` + `store_v3_1.py`, `munin/mcp/core/conversations.py` (if present — find via grep) | `MIGRATION_ID` forward-only checksum; `RUN_STATES` enum; collaborator/note/presence/guidance rows from v3.1 extension; assistant-placeholder timeline row appears on run start |
| Persistence | `munin/mcp/shared_state.py`, `munin/mcp/persistence.py` | All 9 MCP-side tables (shared_intel, tasks, agent_messages, agent_presence, episodic, semantic, procedural, generated_graphs, agent_wake_queue); `ConnectionProxy` rowcount + comment-safe splitter; `MUNIN_DB_URL` empty = local file, libsql:// = Turso |
| Approval | `munin/production/dispatcher.py`, `munin/production/page_agent.py`, `app/src/components/chat/blocks/HitlRequest.tsx` | run pause → `waiting_for_human` state; nonce+justification; approve forwards approved tool args; reject injects rationale at next step; PageAgent `validate_page_action` gates by `MUNIN_PAGE_AGENT_ENABLED` |
| Streaming | `app/src/lib/useRunEvents.ts`, `app/src/lib/useConversationEvents.ts`, `app/src/app/api/production/[[...path]]/route.ts` | EventSource payload schemas; silence detector transitions (`connecting`/`live`/`stale`/`closed`); `Last-Event-ID` resume; SSE passthrough `maxDuration=14400`; 45s silence → stale |

### Why before any code change

Per issue §12 step 1: this is the safety net that makes deleting legacy glue later defensible.
These tests are written against the current behavior — initial green is "current behaviour
works". Any later change that breaks them signals a regression, not a fix.

### PR — `feat/parity-baseline`

- Branch off `main`: `raven-mind/migration-issue9/step1-parity-baseline`.
- Adds ~7 characterization test files under `tests/characterization/`:
  - `test_coord_respond_loop_parity.py`
  - `test_subagent_runner_parity.py`
  - `test_tool_catalog_parity.py`
  - `test_conversation_persistence_parity.py`
  - `test_shared_state_persistence_parity.py`
  - `test_hitl_parity.py`
  - `test_sse_event_contract_parity.py`
- All pass on the current `main` HEAD (no behaviour introduced).
- CI: pytest runs on push (already wired in `ci.yml`). Live-session workflow unchanged.
- Expected mergeability: low risk; only test additions.

### Exit criterion

All seven parity files green on CI run on `main`. No production code edited. This step is
the gate for steps 8 and the "delete legacy glue" rows; without these tests nothing in step
8 is defensive.

## Step 2 — Vercel AI SDK transport / UI (frontend-only)

### Goal
Introduce the new frontend protocol without changing the Python backend runtime. The Python
backend stays authoritative (issue §10: "Do not move core orchestration into TypeScript").
A thin Python-side adapter layer translates run-event payloads into the AI SDK UI message
stream format; the Next.js app switches from bespoke SSE/Chat state to `useChat` + parts.

### Deviations from issue §12 prescriptive order

None. The issue's step 2 is explicit ("Introduce the new frontend protocol without changing
the backend runtime first"). The repository evidence confirms this is a clean split: the
conversation event source is already behind a stable `/api/production/runs/:id/events`
contract and a proxy on `app/src/app/api/production/[[...path]]/route.ts`. Swapping the
client adapter is mechanically separable from backend runtime changes.

### Sub-steps within this PR

1. Add deps: `ai @ai-sdk/react @ai-sdk/openai-provider` (provider is the Munin backend
   itself — we implement the protocol, not call a third-party LLM directly from client).
2. Create a Next.js route (or BFF route) that hits the existing production ASGI `/events`
   endpoint and re-emits payload as AI SDK data stream parts (`text`, `tool-invocation`
   with `input`/`output`/`state`, `reasoning`, custom parts for `forge-stage`,
   `subagent-presence`, `worker-progress`, `hitl-request`).
3. Replace `useRunEvents.ts` + the chat-state portion of `muninStore.ts` with `useChat`
   against the new route. Keep `useConversationEvents.ts` collab/presence code intact
   (those are genuinely separate from chat — collab presence semantics are not Chat parts).
4. Rewrite the 9 `chat/blocks/*` components as message-part renderers using `part.type` and
   `part.state` (input/streaming/output/available) — visual parity is asserted by a
   component regression test per block, not eyeballing.
5. Wire `reconnectToStream` against the production ASGI's `Last-Event-ID` resume head.
6. Add `onEnd` persistence hook that calls `productionApi.persistConversation(chatId, msgs)`
   so the existing `conversations`/`timeline_messages` rows remain authoritative.

### Exit criterion (parity proven)

The 7 characterization tests:
- Coordinator + subagent + tool still produce identical event payloads (the adapter is
  strictly a re-emitter — the backend is unchanged).
- Live: send chat, host reload, verify identical persisted message list and tool parts.
- Live: open chat during long run, kill backend, restart backend, verify reconnect shows
  the same tool calls the second client saw (no duplicates, no missing tool results).

### PR — `feat/ai-sdk-transport` (`raven-mind/migration-issue9/step2-ai-sdk-transport`)

Started from scratch (no prior `ai` dep). Touches `app/` only. CI adds `next lint` + a
new component-visual-parity check. Backend `ci.yml` test set unchanged.

## Step 3 — Deep Agent coordinator

### Goal
Replace `MuninAgent.respond()` and the dispatcher's invocation of it with a Deep Agent
supervisor assembled by `create_deep_agent`. The supervisor runs behind a compatibility
adapter that preserves the exact `progress` event stream + `tool_calls_log` shape so the
frontend (still on the protocol from step 2) sees no difference.

### Sub-steps

1. Build `munin/core/supervisor.py` exposing `build_supervisor(memory, skills, tools,
   subagents, checkpointer, interrupt_on, middleware)` → returns the compiled LangGraph
   Pregel from `create_deep_agent(...)` with Munin-specific middleware:
   - OperatorGuidanceMiddleware (new) —— reads `run_guidance_queue`, injects `<operator_guidance>` at iteration boundary, sets `delivered_at_step`. Replaces `pre_iteration_hook`.
   - RepetitionGuardMiddleware (new) — implements current WINDOW_SIZE=6/MIN_UNIQUE=3 with nudge-once + trip-on-second-loop. No deepagents native equivalent; pure Munin middleware.
   - ProgressEmitMiddleware (new) — translates LangGraph `stream_events(version="v3")` into the existing `progress(channel)` event dict shape (so PR #2 frontend adapter keeps working).
2. `LLMClient` unchanged wiring: the supervisor takes `model=make_langchain(...)` returned by the Munin-validated client. `_validate_base_url`, EMA adaptive timeout, VPN enforcement stay in `llm_client.py`.
3. Adapter side-by-side: `munin/production/dispatcher.py` keeps routing through `respond()` temporarily; add a `MUNIN_RUNTIME=supervisor` env flag (default `legacy`) that swaps `respond()` → `supervisor.invoke(...)` behind a thin shim. **NOT a permanent flag** — issue non-goal: "Do not keep legacy orchestration and the new runtime permanently active as equal authorities". The flag exists only for the parity window between this PR and PR #4.
4. Add Agent Registry schema v1 columns (minimal: `id, version, definition_json, runtime_type, created_by, parent_run, status, last_invocation_at, exec_history_json`) — table created but not yet populated by the factory; this is just the storage slot for step 5.

### Parity bar (must cross before merge)

Coordinator parity test (`test_coord_respond_loop_parity.py` from step 1) must pass
bit-identically for:
- identical system_prompt content (soul/ files merged in same order)
- identical `progress` event sequence for a fixed prompt + fake LLM fixture
- identical `tool_calls_log` order and content
- identical `stop_reason` values across `{final_answer, max_iterations, repetition_detected}`
- identical `<operator_guidance>` injection timing and format with `pre_iteration_hook` shim

### PRs

- `feat/deep-agent-supervisor` (PR #3) — introduces `supervisor.py`, middleware, env-flag adapter, Agent Registry schema. LANDS behind the flag.
- `feat/supervisor-parity-delete-respond` (PR #4) — removes the flag and removes `respond()` from paths reachable by dispatcher + Discord bridge + `munin_chat` tool. Only after at least one live-session run with `MUNIN_RUNTIME=supervisor` produces identical traces in production. **This PR is the first "delete obsolete glue" mini-PR — deliberate separation per issue §12.**

## Step 4 — Tool Gateway + Tool Factory

### Goal
Make every FastMCP/MCP/native/generated tool consumable by the supervisor through one
execution path that is not bespoke per generator. Demonstrate same-run tool creation +
invocation.

### Sub-steps

1. `munin/core/tool_gateway.py` — wraps FastMCP-registered tools as LangChain `BaseTool`
   subclasses. Generated tools (`gen__` prefix) wired through the existing
   `registry.rehydrate()` path now feed `invoke_registered_tool(tool_id, args)`.
2. Autonomy Kernel meta-tools registered alongside other tools on the supervisor:
   - `create_tool(spec, *, ephemeral=False)` — reuses `ToolForgeSubagent` (LLM gen + AST guard + sandbox) but takes the result through `register_state_only` plus immediate callable retention so the supervisor can call `invoke_registered_tool(gen__<slug>, args)` in the same LangGraph run without graph recompilation.
   - `invoke_registered_tool(tool_id, args)` — registry lookup → callable invocation with OPSEC preflight (tool-boundary enforcement preserved per issue §9).
   - `list_registered_tools()`, `inspect_registered_tool(tool_id)` — read-side tools surfaced to the LLM.
3. Provenance expansion in `procedural` table: add `creator_agent`, `parent_run`, `spec`, `source`, `deps`, `validation_results`, `exec_history` columns (new migration in `production/store.py` checksum-guarded schema). The current `script_path, signature, tags, active` columns preserved.
4. `forge_tool.py max_iterations` clamp `[1,12]` removed — field still accepted, default unchanged. Issue §4 "no arbitrary hard caps". Backpressure surfaces as LangGraph RecursionLimit + UI cancel via `cancel_async_task` (wired at step 7).

### Exit criterion (parity proven)

Characterization tests:
- `test_tool_catalog_parity.py` continues to pass (manifest identical).
- New E2E test `test_dynamic_tool_same_run.py`: supervisor calls `create_tool(spec="echo text")` → registry row written → `invoke_registered_tool(gen__echo_text, {text:"hi"})` returns within the same supervisor run.
- New E2E test `test_persistent_tool_cross_run.py`: restart supervisor (new process), `gen__echo_text` rehydrates from `procedural` and is callable by name.

### PRs

- `feat/tool-gateway` (PR #5) — wrap FastMCP as LangChain BaseTool; supervisor sees all 65 fixed + N forged tools uniformly.
- `feat/tool-factory-evolution` (PR #6) — Autonomy Kernel meta-tools, provenance expansion, `max_iterations` cap removal.

## Step 5 — Subagent Factory + Agent Registry

### Goal
Support ephemeral and persistent dynamic specialists. The Subagent Factory selects the
lightest correct runtime per spec; the Agent Registry persists versioned specialists that
can be rebuilt into runnables in a different session without regenerating.

### Sub-steps

1. `munin/core/subagent_factory.py` — `create_subagent(spec: SubagentSpec) → Runnable`
   routing:
   - simple ephemeral → `SubAgent` dict (reuses `create_agent`)
   - long-horizon → recursive `create_deep_agent`
   - complex deterministic/agentic → `CompiledSubAgent` (custom LangGraph graph compiled by munin with `add_node`, `add_conditional_edges`, `add_edge`, etc.; state schema includes `messages` per TypedDict contract)
   - peer collaboration → entry into `langgraph-swarm` graph (`create_swarm(...)`)
   - background → `AsyncSubAgent` with `graph_id` pointing at a workflow registered on the LangGraph server (step 7)
2. `munin/core/agent_registry.py` — PR #7 introduces the registry read/write APIs:
   - `registry.register_agent(spec)` → writes a row with `id, version, definition_json, runtime_type, created_by, parent_run, dependencies_json, model_config_json, status, last_invocation_at, exec_history_json, artifacts_uri`
   - `registry.rebuild_agent(agent_id, version)` → reads row → routes spec back through `subagent_factory.create_subagent` → returns a fresh `Runnable`. **This must rebuild from the definition JSON, not serialize in-memory compiled runnables** (issue §5: "rebuilding the runnable from its definition rather than serializing unsafe in-memory objects").
3. Forge path reintegrates: `munin/subagents/runner.py` `_load_subagent` becomes a shell that calls `agent_registry.rebuild_agent` if spec exists, else routes through `subagent_factory.create_subagent`. The runner subprocess path itself is preserved here — only deprecated fully in step 7/step 8.
4. Autonomy Kernel meta-tools: `create_subagent(spec)`, `invoke_registered_agent(id, task)`, `list_registered_agents()`, `inspect_registered_agent(id)`.
5. Remove `MUNIN_MAX_NESTED_SUBAGENTS=5` from `munin/subagents/base.py`. Depth now bounded by LangGraph RecursionLimit (observable, configurable via env). Backpressure middleware surface remaining-depth via `stream_events`.

### Deviation from issue §12

Issue lists step 5 then step 7 (Native coordination) — I am keeping the order strictly but
naïve. The Subagent Factory's `AsyncSubAgent` choice *references* the LangGraph server from
step 7; if step 7 lands first the factory can already test fully. **Resolved by landing step
5 with AsyncSubAgent support stubbed to raise NotImplementedError if no LangGraph server url
is configured; step 7 unlocks it. This is explicit, written, and reviewable — not a hidden
ordering assumption.**

### Exit criterion (parity proven)

Required end-to-end scenarios (issue §required-scenarios):
- **Dynamic specialist** — Munin invents specialist from NL need; created + invoked same-run; uses existing tools and factory; returns traceable result; SUBAGENT spec logged to Agent Registry.
- **Persistent specialist** — written to Agent Registry; new conversation discovers + reuses; definition + dependencies versioned.

### PRs

- `feat/subagent-factory` (PR #7) — `subagent_factory.py` + 4 meta-tools + removal of `MAX_NESTED_SUBAGENTS`.
- `feat/agent-registry` (PR #8) — `agent_registry.py` + `agent_registry` table + `rebuild_agent` semantics; persisted specialist reuse E2E test.

## Step 6 — Workflow Factory / CompiledSubAgent

### Goal
Evolve `graph_forge` from "writes JSON that gets re-executed through ReAct" into a real
LangGraph Workflow Factory that emits a compiled runnable usable as a `CompiledSubAgent`.

### Sub-steps

1. `munin/core/workflow_factory.py` — `create_workflow(spec: WorkflowSpec) → Pregel`:
   - declarative graph DSL (deterministic + agent + tool + conditional + loops + Send + Command + handoffs + subgraphs + interrupts + custom state + reducers)
   - sandboxed-Python fallback when declarative is insufficient (reuses `sandbox.py` AST guard — invariant preserved).
2. `munin/core/workflow_registry.py` + new `workflow_registry` production table (parallel to `agent_registry`).
3. Autonomy Kernel meta-tools: `create_workflow(spec)`, `invoke_registered_workflow(id, input)`, `list_registered_workflows()`, `inspect_registered_workflow(id)`.
4. `graph_forge_tool.py` repointed at `workflow_factory.create_workflow`; old `generated_graphs` table preserved-read-only for backward compatibility (read from but not written to) — final deletion in step 8.

### Exit criterion (parity proven)

Required end-to-end scenario (issue §required-scenarios "Dynamic workflow"):
- Munin generates a multi-node workflow.
- Compiles successfully.
- Invoked as a CompiledSubAgent by the supervisor.
- Contains at least one deterministic node and one agent node.
- Result's `structured_response` returned via the `task` tool as a `ToolMessage`.

Characterization tests for the old `graph_forge` path must continue to pass against any
schemas still present in the `generated_graphs` table from prior runs (read compatibility).

### PRs

- `feat/workflow-factory` (PR #9) — `workflow_factory.py`, declarative DSL, sandbox fallback, meta-tools.
- `feat/workflow-registry` (PR #10) — `workflow_registry.py` + table; persisted workflow reuse E2E.

## Step 7 — Native coordination (deployment + Send + Command + handoffs + checkpointers)

### Goal
Adopt LangGraph native coordination primitives and deploy the self-hosted LangGraph server
that unlocks `AsyncSubAgent` on the runner.

### Sub-steps

1. **Deployment change** in `.github/workflows/live-session.yml`:
   - Before MCP server start, run `langgraph build` against a new `langgraph.json` registering `./munin/core/supervisor.py:supervisor` as graph id `munin_supervisor`.
   - `langgraph up` (port 8123) with `checkpointer: sqlite` config pointing at `data/langgraph_checkpoints.sqlite`.
   - Upload this file as part of the existing `munin-state` artifact (or a new `munin-langgraph-state` artifact) so thread state survives runner death — **this is the existing free-tier artifact pattern, applied to the new persistence layer**.
   - Set `LANGGRAPH_API_KEY` (new repo secret or runner-local generated UUID).
   - AsyncSubAgent instances target `url="http://127.0.0.1:8123"`, `graph_id=<subagent_runtime_id>`.
2. **Send fan-out** — replace `munin/production/parallel.py execute_tool_batch` parallel-tool path with Send-based workers when the fan-out is dynamic (N hosts / CVEs / URLs). Static single-iteration multi-tool still uses LangGraph natural multi-tool-call support (no change needed).
3. **Command handoffs** — for peer collaboration scenarios in the Subagent Factory's `swarm` branch, use `langgraph-swarm` `create_handoff_tool(agent_name)` → `Command(goto=, update={messages, active_agent})`. `SwarmState.messages` shared by default; isolate via custom state schema when subagent needs its own namespace.
4. **Checkpointer** — supervisor built with `checkpointer=SqliteSaver(conn_from_MUNIN_DB_URL_or_local)`. Persisted runs survive worker restart.
5. **Inter-agent messaging** — `post_agent_message` MCP tool remains for compatibility with existing `shared_intel` semantics; new direct subagent permissions via `.sendFilesystemPermission` rules on subagent specs (DeepAgents native). Parent↔child result flow uses `Command(update)` from the `task` tool (the native path).

### Exit criterion (parity proven)

Required end-to-end scenarios:
- **Parallel workers** — Send propagates N workers (one per host in a fixture recon sweep); execute concurrently; aggregate deterministically (`operator.add` reducer); UI shows per-worker identity + progress + outcome; one failure does not abort the batch.
- **Communication** — parent delegation via `task` returns structured response to parent's messages; two specialists hand off via `create_handoff_tool`; both publish to `shared_intel` and both can read each other's findings.
- **Long-running execution** — Kill MCP server mid-stream; restart; checkpointer resumes thread with same `thread_id`; LangGraph-recorded interrupts persist; UI reconnects via `reconnectToStream`; operator guidance injected mid-run appears at next iteration boundary.

### PRs

- `feat/langgraph-server-deployment` (PR #11) — workflow change + `langgraph.json` + new artifact upload.
- `feat/send-workers` (PR #12) — fan-out replacement + reducer affinity; per-worker UI identity streaming.
- `feat/native-coordination` (PR #13) — swarm handoffs, Command goto/update test coverage, AsyncSubAgent pressure test.

## Step 8 — Remove obsolete glue (only after parity demonstrated)

### Goal
Delete the legacy paths the previous steps replaced. This step is sequenced strictly AFTER
parity is proven on production traces — per issue §12 step 8 and the explicit PR strategy
rule ("Do not merge 'remove obsolete glue' work into the same PR that introduces its
replacement").

### Sub-steps

1. Delete `MuninAgent.respond()` after PR #4 verified no remaining caller (Discord bridge,
   `munin_chat` tool, dispatcher all repointed to supervisor.invoke).
2. Delete `munin/subagents/runner.py` subprocess entry + `process_control.py` after PR #11
   `start_async_task` proved equivalent. `munin_wake` MCP tool kept as a thin compat shim
   routing internally to `start_async_task`.
3. Delete `agent_wake_queue` table writes (after `start_async_task` is the only source).
4. Delete `munin_production/dispatcher.py`'s assistant-placeholder event adapter and
   `tool_call_id` correlation. LangGraph stream_events covers these. Run model remains in
   ProductionStore (conversations, timeline_messages, tool_calls, reasoning_events).
5. Migrate `store_v3_1.py` monkey-patch into a proper `store_v3_2.py` forward-only checksum
   migration that absorbs the v3.1 tables as base schema. UUID-helper regression test stays
   as coverage. Stored monkey-patch file removed.
6. Delete `useRunEvents.ts` and the chat-state portion of `muninStore.ts` after the Vercel
   AI SDK transport proves silence-detector + Last-Event-ID parity. Keep `useConversationEvents.ts`
   (collab/presence semantics are not chat message parts).

### Exit criterion

- Every characterization test from step 1 still passes (modulo explicit semantics renames,
  which must be enumerated in the PR descriptions).
- Live-session traces from runs with the new stack contain no references to deleted
  symbols (`MuninAgent`, `respond`, `agent_wake_queue`, `store_v3_1.install`).
- Free-tier artifact now contains `data/shared_state.sqlite` (existing) +
  `data/langgraph_checkpoints.sqlite` (new) + `data/soul_pending/` (existing) +
  `data/wake_artifacts/` (deprecated but kept for a release cycle as read-only).

### PRs

- `chore/remove-legacy-orchestration` (PR #14) — `respond()`, runner entry, dispatcher adapter.
- `chore/remove-legacy-store-v3-1-overlay` (PR #15) — v3.2 migration; UUID helper retained.
- `chore/remove-legacy-frontend-stream-adapters` (PR #16) — `useRunEvents`, `muninStore` chat portion.

## Validation strategy common to every PR (issue "validation authority" section)

For each PR in this migration, none of these is optional:

1. Run the step 1 characterization tests relevant to the PR's subsystem. If any of those
   that assert current behavior break, that is a parity regression — investigate before
   proceeding; do not weaken the test to let the PR through.
2. Run the live-session workflow (`.github/workflows/live-session.yml`). Exercise the
   end-to-end scenarios relevant to that PR against the deployed tunnel (chrome-devtools MCP
   to inspect, NOT eyeballing screenshots). Collect before/after trace + log evidence.
3. Verify framework-dependent behavior against the installed dependency versions, the Context7
   documentation findings recorded above in `IMPROVEMENT_BACKLOG.md` and at the start of
   this document. If a behaviour contradicts the documented contract, stop and resolve.
4. Only after parity is demonstrated is the legacy glue the PR targets eligible for deletion
   — and deletion lives in step 8 PRs, not in the introducing PR.

## Execution-agent hand-off conventions

Raven Mind (this agent, GLM-5.2) owns:
- every architectural decision documented here
- every PR's atomic spec (exact file, function, expected behavior)
- the review + sign-off on everything the second agent produces
- Context7/DeepWiki consultation before any framework-dependent decision

The implementation agent (general-purpose, may be Deepseek-v3-Flash or similar low-cost):
- writes ONLY atomic, fully-specified tasks delegated by Raven Mind
-Formatting tables, scaffold boilerplate, simple adapters following a fixed schema,
commit messages from Raven Mind's notes, GH Actions logs summaries, and narrowly-scoped
Context7/DeepWiki retrieval — all OK.
- Never chooses what to migrate, never decides an architectural question, never interprets
  ambiguous Context7/DeepWiki findings.

Every hand-off is logged in `changes.md` with: what was delegated, the exact spec given,
the review outcome.

## Open architectural questions still to resolve mid-migration

These are confirmable mid-flight, not blockers:

1. **`langgraph up` Docker image build cost on the runner** — build time budget vs GitHub
   Actions 6h limit; if build per-run is too costly, pre-build in a nightly + push to GHCR
   + pull at session start. Resolved by measuring in PR #11.
2. **`langgraph-checkpoint-sqlite` file size growth** — the SQLite file may grow large
   after many runs; artifact upload cost. Resolved by a checkpoint retention strategy
   added to `reset-turso-state.yml` analog.
3. **Vercel AI SDK `custom` parts for `forge-stage`, `subagent-presence`, `worker-progress`**
   — the issue explicitly allows custom part types; the exact part-id surface needs locking
   down in PR #2 design. Will record the locked-down part-id list in the PR description.
4. **SwarmState.messages vs `shared_intel` for_secops find publishing** — both mechanisms
   coexist by issue §8 rule. Document whether each finding goes to both or only shared_intel.
5. **Sandbox-vs-CompilableWorkflow gradation** — exact threshold for "declarative is
   insufficient" is a job for PR #9; the test suite must exercise both paths.

---

Generated end-to-end plan complete. Evidence for every consequential decision resides in:
- `GLUE_INVENTORY.md` — what custom glue exists (file:line).
- `IMPROVEMENT_BACKLOG.md` — what each piece of glue maps to (primitive + invariants +
  ranking).
- This document — the sequence to replace, in what PR, with what parity bar.