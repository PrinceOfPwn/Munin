# Improvement Backlog — Migration to Deep Agents + LangGraph + Vercel AI SDK

*Issue: PrinceOfPwn/Munin#9 | Branch: `raven-mind/migration-issue9` | Generated: 2026-07-30*

This backlog ranks the individual glue-removal items that the migration targets.
One row per subsystem, mapped to the framework primitive that replaces it and
the Munin invariants (issue section 9) that must remain around that primitive.

Discovery is complete. Framework behavior was verified against:

- **Installed versions** (from `pyproject.toml` constraints, no `poetry.lock` committed):
  - `langgraph >= 0.2.40`, `langchain >= 0.3.0`, `langchain-openai >= 0.2.0`,
    `langgraph-codeact = *`, `mcp >= 1.0.0,<2` (FastMCP stable v1 surface inside `mcp`).
  - `deepagents` and `langgraph-swarm` are **NOT** currently declared deps → must be added
    as an explicit architectural decision (issue non-goal: no silent coding against newer docs).
- **Frontend** (`app/package.json`, lockfile present): Next `^14.2.35`, React `^18.3.1`,
  `@tanstack/react-query ^5.56.2`, `zustand ^4.5.4`. **`ai` / `@ai-sdk/*` NOT present** →
  added as part of step 2.

- **Context7** queries (version-relevant docs, `/langchain-ai/deepagents`,
  `/websites/langchain_oss_python_langgraph`, `/vercel/ai`, Context7 IDs):
  - `create_deep_agent(model, tools, system_prompt, subagents, async_subagents, backend,
    checkpointer, interrupt_on, response_format, middleware, memory, skills, permissions)`
  - `SubAgent` TypedDict fields: `name, description, system_prompt, tools, model, middleware,
    interrupt_on, skills, permissions, response_format` — inherits parent backend/checkpointer.
  - `CompiledSubAgent` TypedDict: `name, description, runnable: Runnable` (custom LangGraph
    graph or `create_agent()` result; state schema must include `messages`; reads
    `structured_response` or last AIMessage).
  - `AsyncSubAgent` TypedDict: `name, description, graph_id, url?, headers?` — managed by
    `AsyncSubAgentMiddleware`; lifecycle tools `start_async_task / check_async_task /
    update_async_task / cancel_async_task / list_async_tasks`; state key `async_tasks`.
  - `task` tool returns `Command(update={**state, "messages":[ToolMessage(content, tool_call_id)]})`.
  - HITL: `interrupt_on={tool_name: InterruptConfig}` + `Command(resume={"decisions":[{"type":"approve"|"reject"|"edit"|"respond"}]})`.
  - `stream_events(version="v3")` → `.messages`, `.interrupts`, `.interrupted`, `.output`.
  - LangGraph `Send(node_name, state_subset)` for dynamic fan-out; `Annotated[list, operator.add]` reducer for aggregation. `Command(goto=, update=, resume=)`.
  - LangGraph Platform self-hosted: `langgraph dev` (127.0.0.1:2024, inmem), `langgraph build && langgraph up` (Docker, port 8123, persistent). `langgraph.json` registers graphs by id. `langgraph-checkpoint-sqlite` (SqliteSaver/AsyncSqliteSaver) for local-disk persistence surviving restarts. Auth via `x-api-key` header + `LANGGRAPH_API_KEY`.
  - `langgraph-swarm`: `create_swarm(agents: list[Pregel], default_active_agent, state_schema=SwarmState)`, `create_handoff_tool(agent_name)` → returns `Command(goto=, update={messages, active_agent})`. Swarm members must be `Pregel`; `create_deep_agent` returns a Pregel/compiled graph → integrable as a swarm member.
  - Vercel AI SDK: `useChat` with `messages[].parts` (text, tool-*, custom). `reconnectToStream({chatId, startIndex})` for resume. Server: `streamText` + `toUIMessageStream` + `createUIMessageStreamResponse` + `consumeStream()` (survives client disconnect). `onEnd` for persistence.

- **DeepWiki** answers (`langchain-ai/deepagents`, `langchain-ai/langgraph`, `langchain-ai/langgraph-swarm-py`):
  - Default `create_deep_agent` middleware stack: `TodoListMiddleware` (planner/write_todos),
    `FilesystemMiddleware` (ls/read_file/write_file + permissions), `SubAgentMiddleware` (task tool),
    `AsyncSubAgentMiddleware` (async tasks), `SkillsMiddleware` (SKILL.md on-demand),
    `MemoryMiddleware` (AGENTS.md → system prompt), `SummarizationMiddleware` (context window + tool result eviction),
    `HumanInTheLoopMiddleware` (interrupt_on), `PatchToolCallsMiddleware`.
  - ReAct loop / tool dispatch / result reinjection owned by underlying `langchain.agents.create_agent`
    (LangGraph compiled graph) — Deep Agents wraps it with middleware.
  - Subagent spec richness is bounded: for specs requiring per-subagent checkpointer, own filesystem,
    complex deterministic/agentic nodes → use `CompiledSubAgent` (LangGraph graph) not the dict spec.
  - No built-in persistent agent/tool/workflow registry — Munin Autonomy Kernel must own those (issue §2, §5).
  - `langgraph dev` in-mem mode inside `langgraph-cli[inmem]` runs locally with no external services.
    `langgraph up` builds Docker container. Both accept `langgraph.json` pointing at our graph.
  - `subagent_names` attribute on `SubAgentMiddleware` is public "so streamers can discover them
    without introspecting the `task` tool's closure" — supports UI streaming natively.

- **Repo inventory**: see `GLUE_INVENTORY.md` (read-only map of every custom piece, file:line).
  Key entries: `MuninAgent.respond()` ReAct loop at `munin/core/munin_agent.py:379` with
  `_HARD_CEILING=10_000` and repetition guard (WINDOW_SIZE=6/MIN_UNIQUE=3); subagent runner
  subprocess + `agent_wake_queue.try_claim_spawn_slot` at `munin/mcp/shared_state.py:698`;
  `ProductionDispatcher.run_once()` + `update_run_state("waiting_for_human")` HITL;
  `ProductionStore` with `store_v3_1.py` monkey-patching via `types.MethodType`;
  `MUNIN_MAX_PARALLEL_TOOLS=6` parallel batch; `MAX_INLINE_BODY=12000` result overflow;
  9 MCP-side tables + 21 production-side tables; 9 frontend chat blocks; 21 hard-cap constants.

## Ranking columns

| Column | Meaning |
|---|---|
| **Subsystem** | The glue area being replaced. |
| **Current glue (file:line)** | Where Munin reimplements generic behavior today. |
| **Framework primitive** | The native replacement (verified via Context7/DeepWiki). |
| **Munin invariants preserved (issue §9)** | What must stay around the primitive — never deleted. |
| **Evidence** | The Context7 query / DeepWiki answer / repo line backing the mapping. |
| **Functional impact** | How much visible behavior depends on this glue (High/Med/Low). |
| **User impact** | Operator-visible regression risk if botched (High/Med/Low). |
| **Confidence** | Strength of the glue→primitive mapping (High/Med/Low). |
| **Risk** | Severity of getting it wrong (migration halting, data loss, OPSEC bypass) (H/M/L). |
| **Cost** | Implementation effort estimate (S/M/L). |
| **Validation approach** | How parity is proven before deleting the legacy path. |

## Backlog rows — ordered by issue §12 step sequence (each step's rows grouped)

### Step 1 — Characterization & parity tests (foundation; no glue removed)

| Subsystem | Current glue (file:line) | Framework primitive | Munin invariants preserved (issue §9) | Evidence | Functional impact | User impact | Confidence | Risk | Cost | Validation approach |
|---|---|---|---|---|---|---|---|---|---|---|
| Coordinator ReAct loop | `munin/core/munin_agent.py:379-637` | (not removed yet — captured in parity tests) | stop_reason semantics, `tool_calls_log` shape, `progress` event stream, repetition guard | direct read of respond() | High | High | High | M | S | New tests assert: emits `final_answer`/`max_iterations`/`repetition_detected`; tool_calls_log entries match emit events; progress stages (`reasoning`, `tool_start`, `tool_result`, `completed`) appear in order |
| Subagent runner | `munin/subagents/runner.py:296-320`, `shared_state.py:698 try_claim_spawn_slot` | (parallel baseline only) | wake-claim atomicity; post `RESULT`/`ERROR` to `agent_messages`; `MAX_INLINE_BODY=12000` overflow → `wake_artifacts/wake_<id>.json` | GLUE_INVENTORY §2 | High | High | High | M | M | Tests assert: claim is exclusive; RESULT message body round-trips for bodies < 12000 bytes; overflow body spawns artifact + pointer |
| Tool dispatch + parallel batch | `munin/agent.py:531-538` single/batch dispatch, `parallel.py execute_tool_batch` | (baseline) | `parallel_group_id` + `tool_use_id` stamps; `MUNIN_MAX_PARALLEL_TOOLS=6` observable | GLUE §3, §11 | Med | Med | High | L | S | Tests assert: one-tool preserves legacy path; multi-tool batch parallelizes with correlation ids; unknown_tool / bad_args / tool_crashed error shapes are stable |
| Conversation/persistence | `production/store.py` 21 tables, `store_v3_1.py` monkey-patch | (baseline) | schema, run states, collaborator/note/presence/guidance semantics | GLUE §5/§6 | High | High | High | M | M | Tests assert: forward-only checksum migration still passes; v3.1 extensions install cleanly without UUID-helper regression; timeline_messages + reasoning_events + tool_calls rows persist across store close+reopen |
| HITL + operator guidance | `dispatcher.py waiting_for_human`, `human_request_approvals` polling, `pre_iteration_hook` drains `run_guidance_queue` | (baseline) | approval nonce + justification; guidance delivery at next step | GLUE §9 | High | High | High | M | S | Tests assert: pause→approve→resume emits `tool_choice` with approved args; pause→reject emits rationale injected into next iteration; guidance queued after pause is visible at next step boundary |
| Streaming/UI events | `useRunEvents.ts` / `useConversationEvents.ts` SSE adapters | (baseline) | event types `note-appended`/`presence-changed`/`run-transition`/`guidance-delivered`/`heartbeat`/`tool_start`/`tool_result`; `Last-Event-ID` resume; 45s silence → stale | GLUE §7/§8 | High | High | High | M | M | Contract tests assert the SSE payload schemas + silence detector transitions + Last-Event-ID resumption of dropped events |

### Step 2 — Vercel AI SDK transport (frontend protocol swap, no backend change)

| Subsystem | Current glue (file:line) | Framework primitive | Munin invariants preserved (issue §9) | Evidence | Functional impact | User impact | Confidence | Risk | Cost | Validation approach |
|---|---|---|---|---|---|---|---|---|---|---|
| Chat state glue | `app/src/store/muninStore.ts` sendChatMessage + messages state | `useChat` from `@ai-sdk/react` (messages, sendMessage, status, error, regenerate) | conversation_id persistence, tool-call correlation by tool_use_id | Context7 `/vercel/ai` useChat + parts; reconnectToStream | High | High | Med | M | M | Live site: send+receive a tool call; reload; conversation reconstructs identically (chatId preserved) |
| Tool rendering | 9 chat-block components (`ToolBlock`, `ThoughtBlock`, `ParallelToolBlock`, `SubagentCard`, `HitlRequest`, `ArtifactChip`, `HeartbeatBar`, `NoteBlock`, `GuidanceBlock`) | `message.parts` typed parts (text, tool-invocation, dynamic-tool, reasoning, custom) | per-tool card styling, parallel_group_id grouping, parallel tool widget, HITL form submission, artifact same-origin download proxy | Context7 parts typing; GLUE §8 | High | High | Med | M | L | Visual parity test: render each block type against a fixture message; assert identical visible elements, expand state, click handlers all preserved |
| Reconnect/resume | `useRunEvents.ts` silence detector + Last-Event-ID | `reconnectToStream({chatId, startIndex})` + `result.consumeStream()` server-side | 4h `maxDuration`, 45s silence → ui state, drop+resume not losing events | Context7 reconnectToStream + consumeStream | High | High | High | L | M | Live scenario: open chat during long run, kill backend mid-stream, restart backend, frontend reconnects; verify no duplicate events, no missing tool results after reconnect |
| Durable runs / model store | `production/store.py conversations`, `conversation_participants`, `timeline_messages` | `onEnd({messages}) => saveChat(chatId, messages)` in streamText handler | existing run_states, audit trail per tool call, participant list, timeline ordering | Context7 persistence example `vercel-labs/ai-sdk-persistence-db` pattern | High | Med | High | M | M | After reload, the same `chatId` returns the same persisted message list with tool parts preserved, not the in-memory transient copy |
| Transport adapter (Python→TS) | new — backend stays Python authoritative, emits AI SDK UI message stream | Python endpoint emitting stream protocol (data stream / message parts) OR Next.js BFF adapter to existing production ASGI | existing auth (HttpOnly cookies operator sessions); SSE passthrough 14400s; per-tool `tool_use_id` correlation | issue §10 explicit: backend stays authoritative; Context7 `createUIMessageStreamResponse` + `toUIMessageStream` | High | High | High | M | L | Backend stays Python; adapter only translates run-event payloads to AI SDK parts; smoke test that a `tool_start` + `tool_result` pair from the existing production endpoint maps to a `tool-invocation` part with correct state transition |

### Step 3 — Deep Agent coordinator (new supervisor behind adapter)

| Subsystem | Current glue (file:line) | Framework primitive | Munin invariants preserved (issue §9) | Evidence | Functional impact | User impact | Confidence | Risk | Cost | Validation approach |
|---|---|---|---|---|---|---|---|---|---|---|
| Coordinator ReAct loop | `munin/core/munin_agent.py:379-637` (`respond()` for-loop + LLM-call + dispatch + reinject) | `create_deep_agent(model, tools, system_prompt, subagents, backend, checkpointer, interrupt_on, middleware)` — wraps `langchain.agents.create_agent` (LangGraph compiled graph); owns ReAct + tool dispatch + result reinjection | `progress` event parity (must emit same stages); `stop_reason` mapping (final_answer→END, max_iterations→RecursionLimit, repetition_detected→Munin middleware); `pre_iteration_hook` operator guidance injection; memory.log_step episodic writes | DeepWiki deepagents source: `langchain.agents.create_agent`, default middleware stack; Context7 `create_deep_agent` | High | High | High | H | L | Parity test: feed same prompt+tools to old `respond()` and new supervisor; assert tool_calls_log identical order/content/stop_reason; assert progress events map 1:1 |
| Repetition guard | `munin/agent.py:351-368, 568-610` (WINDOW_SIZE=6, MIN_UNIQUE=3, nudge-once) | Custom Munin middleware (NO native equivalent in deepagents default stack) — wraps the LangGraph tool node with `before_model_hook` checking recent-call fingerprints and injecting a system nudge message | exact nudge message text; second-trip abort with `stop_reason="repetition_detected"` | DeepWiki confirms: no built-in anti-loop middleware in deepagents | High | Med | High | H | M | Nudge-once semantics test: prompt that forces A→A→A→A→A→A; assert first nudge system message injected, second occurrence breaks the loop with the existing stop_reason |
| Operator guidance injection | `dispatcher.py pre_iteration_hook` drains `run_guidance_queue`, injects `<operator_guidance>` block | Custom middleware on Deep Agent reading from Munin's `run_guidance_queue` table; inject `SystemMessage` into `messages` at iteration boundary | exact `<operator_guidance>` block format, step+1 timing (hook runs before model call in same iter), guidance delivery state persisted to `run_guidance_queue.delivered_at_step` | DeepWiki SubAgentMiddleware, Hook pattern; Context7 middleware hooks | High | High | High | H | S | Mid-run: queue guidance; assert new run picks it up at next iteration boundary and that `run_guidance_queue` shows delivered_at_step set before new LLM call |
| Tool catalog + signature→schema | `registry.py _make_tool_schema`, `_enforce_tool_limits` total tool count cap | LangChain native tool conversion: `create_deep_agent(tools=[BaseTool \| Callable \| dict])` auto-converts; no manual schema code | `gen__` prefix convention (preserved); provenance columns in `procedural` table (creator_agent, parent_run, spec, source, deps, validation_results, timestamps, exec_history); AST guard pipeline retained for Tool Factory | DeepWiki deepagents: `create_deep_agent` accepts BaseTool/Callable/dict; Context7 same | High | Med | High | H | L | Rehydrate test: with `procedural` table populated by a fake gen__ tool, the supervisor sees and invokes it by name identically to a native tool |
| LLM client (timeout EMA, _validate_base_url, no 169.254.x SSRF, make_langchain) | `munin/core/llm_client.py` `_validate_base_url`, EMA-based adaptive timeout, `make_langchain()` returning `ChatOpenAI` | Pass an Munin-configured `ChatOpenAI` to `model=` (deepagents accepts `BaseChatModel`); keep `_validate_base_url` returning the validated client | VPN-egress enforcement, https-only (except loopback), 169.254.x block, EMA adaptive timeout, retry/backoff visible to UI as `llm_retry` event | issue §9 OPSEC invariants; existing tests already cover this | High | High | High | H | S | Existing `test_llm_client.py` continue to pass unchanged; the supervisor receives the validated ChatOpenAI through adapter |
| Memory / soul → system prompt | `prompting.py model_family()`, `LANGUAGE_CONTRACT`, `CAMPAIGN_DISCIPLINE`, `subagent_runtime_prompt`, `soul_read` baked-in content | `MemoryMiddleware` (loads AGENTS.md into system prompt) + Munin system_prompt that assembles identity/principles/goals from soul/ files | soul/ files remain human-editable; `soul_propose_edit` PR workflow preserved; `model_family()` dispatch still controls prompting by provider | DeepWiki MemoryMiddleware; Context7 memory middleware | High | Med | High | M | S | Assert system_prompt assembled by new supervisor contains the active soul content; Modify soul/principles.md via propose_edit, assert it appears in next run's system prompt |

### Step 4 — Tool Gateway + Tool Factory

| Subsystem | Current glue (file:line) | Framework primitive | Munin invariants preserved (issue §9) | Evidence | Functional impact | User impact | Confidence | Risk | Cost | Validation approach |
|---|---|---|---|---|---|---|---|---|---|---|
| Tool Gateway (MCP native + gen__ unified) | `registry.py rehydrate`, `_make_tool_schema`; `munin_tools.py run_generated_tool` | `invoke_registered_tool(tool_id, args)` meta-tool resolves from Tool Registry → calls the callable directly inside same LangGraph tool node | `gen__` prefix, `soul_propose_edit`, `subagent_trace`, `deactivate_generated_tool` semantics; per-tool `tool_use_id`; parallel safety list preserved | issue §2/§3; DeepWiki meta-tool concept; LangChain Callable tool | High | Med | High | H | L | E2E: agent creates tool, invokes_registered_tool same run; gen__ from procedural table available from run 1 in run 2 |
| Tool Factory (forge → registry → same-run invoke) | `forge_tool.py max_iterations[1,12]`, `tool_forge.py forge()`, `sandbox.py` AST + restricted builtins | Autonomy Kernel `create_tool` meta-tool: same LLM generation → existing AST guard → `register_state_only` → `invoke_registered_tool` | persistent `gen__` versioned in `procedural` table; provenance columns exec_history; git_persist.queue_commit still pushes to `munin/session-<id>` | GLUE §4 + issue §3 | High | Med | High | H | M | Issue §3 exact scenario: Munin creates a tool during run; passes validation; invokes same-run; persists; run 2 rehydrates and invokes |
| Remove `max_iterations[1,12]` cap (issue §4 "no arbitrary hard caps") | `forge_tool.py:99` clamp | Removing the clamp; field remains as operator-tunable input (no product constant) | observe/cancel via LangGraph Command cancel_token + run state visible in UI | issue §4 non-cap rule | Med | Med | High | M | S | Long-running forge: request max_iterations=50; runtime respects it without aborting silently at 12 |

### Step 5 — Subagent Factory + Agent Registry

| Subsystem | Current glue (file:line) | Framework primitive | Munin invariants preserved (issue §9) | Evidence | Functional impact | User impact | Confidence | Risk | Cost | Validation approach |
|---|---|---|---|---|---|---|---|---|---|---|
| Subagent Factory (spec → runtime) | `runner.py:61 _load_subagent` switching LDAP/tool_forge/graph_forge/_ForgedGraphRunner; `graph_forge.py` ReAct-only configs | `Autonomy Kernel create_subagent(spec)` selecting: simple→`SubAgent` dict or `create_agent` plain ReAct; long-horizon→`create_deep_agent`; deterministic/agentic→`CompiledSubAgent` (LangGraph); peer→swarm member | forge spec richness (model, tools, skills, memory, middleware, response_format, persistence policy, may_create_child flag, custom state); no fixed enum of templates; no nesting/count caps | DeepWiki SubAgent/CompiledSubAgent/AsyncSubAgent TypedDicts; issue §4 list | High | High | High | H | L | Each runtime type created from a single spec; a subagent created during run is invoked same-run and returns traceable result (issue §E2E scenario "Dynamic specialist") |
| Agent Registry (persistent specialists) | `generated_graphs` table (config-only, re-executed via ReAct runner); `procedural` table (tools) | New `agent_registry` table with stable id+version, definition JSON, runtime type, creator/provenance, deps, model config, status/lifecycle, exec history, last_successful_invocation, artifacts/traces. Rebuild runnable from definition (NOT serialize in-memory) | spec/def versioning, deps on tools/skills/workflows, audit of who created it, persistent across sessions | issue §5 explicit | High | High | High | H | M | Persisted specialist invoked fresh in a new conversation (issue E2E "Persistent specialist"); definition+dependencies read from registry row, runnable rebuilt, returns identical result to original run |
| Remove `MUNIN_MAX_NESTED_SUBAGENTS=5` (issue §4) | `munin/subagents/base.py` constant | Depth governed by LangGraph RecursionLimit (configurable, observable) + backpressure middleware not product cap | runaway still cancellable (`cancel_async_task` / `task_cancel`) | issue §4 non-cap rule | Med | Med | High | M | S | Nested invocation depth 7+ succeeds when budget allows; UI surfaces current depth + remaining via stream_events |

### Step 6 — Workflow Factory / CompiledSubAgents

| Subsystem | Current glue (file:line) | Framework primitive | Munin invariants preserved (issue §9) | Evidence | Functional impact | User impact | Confidence | Risk | Cost | Validation approach |
|---|---|---|---|---|---|---|---|---|---|---|
| Workflow Factory (evolve graph_forge) | `graph_forge.py` writes JSON config to `generated_graphs` (ReAct-only; no actual LangGraph compile) | `Workflow Registry` + `create_workflow(spec)` building a real StateGraph: deterministic nodes, agent nodes, tools, conditional routing, loops, fan-out/fan-in, Send, Command, handoffs, subgraphs, interrupts, custom state+reducers. Compiles to LangGraph runnable usable as `CompiledSubAgent` | declarative graph definitions compiled by Munin (preferred); sandboxed generated Python when declarative insufficient (existing `sandbox.py` AST guard retained) | issue §6 + Context7 LangGraph Send/Command/subgraphs; DeepWiki CompiledSubAgent `runnable` | High | High | High | H | L | E2E "Dynamic workflow": Munin generates a multi-node workflow; compiles; invoked as CompiledSubAgent by the supervisor; contains ≥1 deterministic node + ≥1 agent node; result returns `structured_response` |
| Workflow Registry (new) | none (table doesn't exist) | New `workflow_registry` table in production store: stable id+version, definition, runtime (Pregel), provenance, exec history | rebuild-from-definition semantics (NOT serializing in-memory Pregel objects — definition JSON is the source) | issue §5/§6 explicit | High | Med | High | M | M | Workflow persisted; new conversation reuses it; same id + bumped version after edit |

### Step 7 — Native coordination (Send, Command, handoffs, graph state, checkpointers)

| Subsystem | Current glue (file:line) | Framework primitive | Munin invariants preserved (issue §9) | Evidence | Functional impact | User impact | Confidence | Risk | Cost | Validation approach |
|---|---|---|---|---|---|---|---|---|---|---|
| Wake queue → AsyncSubAgent background | `shared_state.py:698 try_claim_spawn_slot`, `agent_wake_queue` table, `runner.py:296-320` poll loop, `process_control.py` subprocess mgmt | `AsyncSubAgentMiddleware` + `AsyncSubAgent` over self-hosted LangGraph server (`langgraph up` with SqliteSaver on local disk on runner); graph_id per registered/generated agent | thread persistence surviving worker restart (issue E2E "Long-running execution"); attribution logs (who initiated the async task); operator `update_async_task` to inject mid-run guidance | DeepWiki AsyncSubAgent (5 tools start/check/update/cancel/list); DeepWiki langgraph self-hosted + SqliteSaver persistence across restarts | High | High | High | H | L | Backend: langgraph up on runner bound :8123 with SqliteSaver on local file → перекр sesión thread_id survives `docker restart`; `start_async_task` round-trips through CLI langgraph-sdk client to `http://127.0.0.1:8123`; check_async_task returns RUNNING/COMPLETED; cancel_async_task interrupts; output aggregated back to parent state |
| Subprocess runner + process control | `runner.py` `python -m munin.subagents.runner`, `process_control.py discover_runner_pids/_signal_runner`, `cli.py:120-121` subagent start | Deprecated (runner replaced by in-graph `task` for sync + `start_async_task` for async on LangGraph server) | `process_control` SIGTERM-with-grace-period semantics preserved via `cancel_async_task` | GLUE §2; DeepWiki AsyncSubAgent | High | Med | High | H | M | Long-running subagent: cancel_async_task returns the same partial result snapshot the current `process_control.py` produces; runner PID table empty after migration |
| Parallel workers (Send fan-out) | `parallel.py execute_tool_batch` (only parallel tools in one LLM step), `MUNIN_MAX_PARALLEL_TOOLS=6` | LangGraph `Send(node, state_subset)` + `Annotated[list, operator.add]` reducer for aggregation | per-worker identity persisted to UI (worker_index); individual failure vs batch failure mapping; aggregation deterministic order | issue §7 explicit; Context7 Send example with WorkerState reducer | High | High | High | H | M | E2E "Parallel workers": Munin creates N workers via Send (e.g. one per host in a recon sweep); executes concurrently; results aggregate deterministically (`operator.add`); UI shows each worker's progress + outcome; one worker failing does NOT fail the batch (issue §7) |
| Inter-agent messaging | `munin/chat.py post_agent_message`, `fetch_agent_messages`, `ack_agent_message`, `agent_messages` table | Parent/child `task` tool result (sync) + async subagent status updates (`check_async_task` returns `async_tasks` dict in state) + LangGraph `Command(goto=)` handoff + swarm for peers | `shared_intel` table preserved verbatim (issue §8 durable findings); provenance of who published a finding | DeepWiki swarm + AsyncSubAgent state; Context7 Send aggregation | High | High | High | M | L | Two specialists hand off via `create_handoff_tool(agent_name)` → `Command(goto=next, update={active_agent, messages})`; both see shared `messages` (SwarmState default); new finding published to `shared_intel` visible to both |

### Step 8 — Remove obsolete glue (only after parity demonstrated)

| Subsystem | Current glue (file:line) | Framework primitive | Munin invariants preserved (issue §9) | Evidence | Functional impact | User impact | Confidence | Risk | Cost | Validation approach |
|---|---|---|---|---|---|---|---|---|---|---|
| Old ReAct loop deletion | `MuninAgent.respond()` (after supervisor parity proven in step 3) | deleted — only used by legacy MCP `munin_chat` tool and Discord bridge — both repointed to supervisor | (none) | step 3 parity tests pass | High | Med | High | H | S | Confirm `respond()` callers list is empty after migration; all deploys use `invoke(supervisor)` |
| Old wake queue + runner subprocess deletion | `shared_state.py` wake queue methods, `runner.py`, `process_control.py` (after step 7 parity) | deleted; `agent_wake_queue` table deprecated (write-only shim possible during transition) | (none) | step 7 e2e passes | High | Med | High | H | M | `munin_wake` MCP tool still callable for compat but routes internally to `start_async_task` |
| Production dispatcher duplicate state | `dispatcher.py` `run_once` assistant-placeholder event adapter; `tool_call_ids` correlation | (LangGraph checkpointer owns graph state; Munin store owns read model + audit only — issue §11) | runs read model (`conversations`, `timeline_messages`, `tool_calls`, `reasoning_events`) remains auditable | issue §11 single authoritative owner per state-type | High | High | High | H | M | runs still queryable by id post-migration with full reasoning timeline; assistant placeholder emit removed since LangGraph stream_events covers it |
| `store_v3_1.py` monkey-patch → proper v3.2 migration | `store_v3_1.py` `install_v3_1_extensions` via `types.MethodType` | new migration file with forward-only checksum that absorbs the v3.1 tables as base schema (no monkey-patch) | UUID-helper regression test stays as test coverage | GLUE §5; ARCHITECTURE.md "real regression" | Med | Med | High | M | S | Migration tests pass on a v3.0 fixture → v3.2 schema; UUID helper still usable; monkey-patch file removed |
| Old SSE adapters | `useRunEvents.ts`, `useConversationEvents.ts` (after Vercel AI SDK stream parity) | deleted — UI message stream covers run events | (none) | step 2 e2e passes | High | Med | High | M | S | All run-event types represented as AI SDK message parts; silence detector replaced by stream keepalive |
| Custom chat-block rendering | per-block TS components (after AI SDK parts prove parity) | mostly retained but reimplemented as message-part renderers (not deleted wholesale); ForgeFloatingChat + HeartbeatBar likely retained as custom | per-tool card styling preserved | step 2 visual parity | Med | Med | High | L | S | Visual regression test passes |

## Cross-cutting Munin invariants that MUST SURVIVE every step (issue §9)

Mapped to where they live today and the layer they will continue to live in:

| Invariant | Current owner (file:line) | New owner | Notes |
|---|---|---|---|
| FastMCP tools + external MCP integration | `munin/mcp/main.py` FastMCP + 65 tools | unchanged — Tool Gateway wraps FastMCP tools as LangChain callables | issue non-goal: do not replace FastMCP if adapter sufficient |
| Hugin retrieval + knowledge graph | `munin/mcp/tools/hugin_tool.py` | unchanged — surfaced to deep agents as tools | provenance preserved in `shared_intel` |
| Offensive tool wrappers (LDAP, recon) | `munin/mcp/tools/ldap_tools.py`, `intel.py` | unchanged — same Tool Gateway adapter | LDAP escape_filter_chars stays in tool body, not in prompt |
| Active-operation scope + authorization | `munin/mcp/opsec.py` preflight | preserved — enforced inside tool body of every destructive/scope-restricted tool | issue §9: scope/OPSEC at tool boundary, not via prompt |
| VPN/egress preflight + postflight | `opsec.py` subprocess checks | unchanged — preflight invoked before tool dispatches that touch network | single egress IP invariant not bypassed by deeper autonomy |
| Evidence capture | `munin/forge/extension_forge` + agent_messages PROGRESS | `audit.py` redaction + episodic log_step kept; provenance on every created tool/agent/workflow | evidence becomes LangGraph checkpoint metadata (`artifacts` field on compiled graph state) |
| Auditability | `audit.py events.jsonl` | unchanged — every tool call from any agent (including generated ones) flows through tool_async decorator | redaction rules (Bearer, api_key, sk-, tvly-, ghp_) preserved verbatim |
| Tool provenance | `procedural` table columns | expanded in Tool Registry v2 with creator_agent, parent_run, spec, source, deps, validation_results, timestamps, exec_history | issue §3 explicit |
| Shared intel + task semantics | `shared_intel`, `tasks` tables | unchanged — durable offensive findings; `publish_shared_intel` meta-tool callable by any agent | issue §8 explicit |
| Soul + project identity | `soul/*.md` + `soul_propose_edit` PR workflow | preserved — MemoryMiddleware loads identity.md/principles.md/goals.md; propose_edit PR still opens on `soul-proposal/<branch>` | human-approved merge flow untouched |

## Dependency additions required (explicit, not silent)

| Dependency | Why | Risk |
|---|---|---|
| `deepagents` (latest compatible with langchain 0.3 / langgraph 0.2.40+) | default coordinator runtime | pin upper-bound to lock API; Context7 confirms SubAgent/CompiledSubAgent/AsyncSubAgent TypedDicts are stable across releases |
| `langgraph-swarm` | peer collaboration / handoffs | same langchain.org ecosystem; check langchain CLI compatibility |
| `langgraph-cli[inmem]` (dev) + `langgraph-sdk` (runtime) | self-hosted LangGraph server + client for AsyncSubAgent | only needed where async subagents run; dev server in-process, prod via `langgraph up` Docker |
| `langgraph-checkpoint-sqlite` | local SqliteSaver persistence on runner (no Postgres) | same family; survives restart via local disk file uploaded as Munin artifact between sessions (matches existing free-tier pattern) |
| `ai` + `@ai-sdk/react` + `@ai-sdk/openai` (or whatever provider) | frontend chat/stream protocol | replaces bespoke Chat.tsx state glue with useChat; React 18 compatible (verified via Context7 example) |