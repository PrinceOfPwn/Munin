# GLUE_INVENTORY.md — Munin Custom Agent-Runtime Glue Code

*Generated: 2026-07-30 | Branch: raven-mind/migration-issue9*

---

## 1. Coordinator — `MuninAgent` ReAct Loop

| File | Line(s) | What |
|------|---------|------|
| `munin/core/munin_agent.py` | 293 | `MuninAgent.respond()` signature — accepts `prompt`, `conversation_id`, `run_id`, `max_iterations`, `harness` |
| `munin/core/munin_agent.py` | 278-331 | `max_iterations` resolution: caller arg → env `MUNIN_MAX_ITERATIONS` → `_HARD_CEILING` |
| `munin/core/munin_agent.py` | 287 | `_HARD_CEILING = 10_000` |
| `munin/core/munin_agent.py` | 379 | Main ReAct `for step in range(max_iterations)` loop |
| `munin/core/munin_agent.py` | 613 | `stop_reason = "max_iterations"` — terminal when budget spent |
| `munin/core/munin_agent.py` | (varies) | Tool catalog: `_NATIVE_TOOLS` dict (fixed set) + `gen__*` tools from `ToolRegistry.rehydrate()` |
| `munin/core/munin_agent.py` | (varies) | `pre_iteration_hook` called before each LLM step (for operator guidance injection) |
| `munin/core/conversations.py` | full | `ConversationService` — history, summary, artifact management |
| `munin/core/prompting.py` | full | `model_family()`, `LANGUAGE_CONTRACT`, `CAMPAIGN_DISCIPLINE`, `subagent_runtime_prompt()` |

---

## 2. Subagent Runner — `python -m munin.subagents.runner <name>`

| File | Line(s) | What |
|------|---------|------|
| `munin/subagents/runner.py` | 3 | Docstring: "The Orchestrator spawns this as a subprocess" |
| `munin/subagents/runner.py` | 38 | `_NATIVE_SUBAGENTS` frozenset — valid subagent names |
| `munin/subagents/runner.py` | 61-65 | `_load_subagent(name)` — dispatches to `LDAPSubagent`, `ToolForgeSubagent`, `GraphForgeSubagent`, or forged graph from `generated_graphs` table |
| `munin/subagents/runner.py` | 87-90 | `_WrappedToolForge.handle_task()` — reads `max_iterations` from task JSON, defaults to 5 |
| `munin/subagents/runner.py` | 195 | Graph config: `"termination": {"max_iterations": 8}` |
| `munin/subagents/runner.py` | 268 | `--sleep-after-idle` arg, **default=120** seconds |
| `munin/subagents/runner.py` | 280-291 | `set_presence()` — posts RUNNING/IDLE/EXITING to `agent_presence` table |
| `munin/subagents/runner.py` | 296-320 | Main poll loop: `claim_wake_item` → `handle_task` → `post_agent_message("munin", result)` → idle-exit |
| `munin/subagents/runner.py` | 303 | Idle timeout: `time.monotonic() - idle_since > args.sleep_after_idle` |
| `munin/subagents/runner.py` | 363-371 | RESULT overflow: `MAX_INLINE_BODY = 12000` bytes; bodies exceeding this go to `data/wake_artifacts/wake_<id>.json` |

### Runner base class

| File | Line(s) | What |
|------|---------|------|
| `munin/subagents/base.py` | 650-657 | `ReActSubagentBase` config dataclass — `max_iterations: int = 8` |
| `munin/subagents/base.py` | 764 | ReAct loop: `for step in range(self.max_iterations)` |
| `munin/subagents/base.py` | 924 | `stop_reason = "max_iterations"` |
| `munin/subagents/base.py` | (varies) | `iteration_token_budget=2000` — per-step truncation |
| `munin/subagents/base.py` | (varies) | `_already_called` repetition guard — skips tools called with same args |
| `munin/subagents/base.py` | (varies) | `_emit_tool_progress()` — posts PROGRESS messages to `agent_messages` before each tool call |
| `munin/subagents/base.py` | (varies) | `build_tool_catalog(state, allowed_tools)` — loads `gen__*` from `procedural` table |
| `munin/subagents/base.py` | (varies) | `_signature_to_openai()` — converts stored JSON signature to OpenAI tool format |
| `munin/subagents/base.py` | (varies) | `_collect_native_tools()` / `_make_wake_tools()` — builds tool list for subagent |

### Concrete subagents

| File | Line(s) | What |
|------|---------|------|
| `munin/subagents/ldap_agent.py` | full | `LDAPSubagent(ReActSubagentBase)` — AD/OpenLDAP specialist, 14 allowed tools |
| `munin/subagents/tool_forge.py` | 166-175 | `ToolForgeSubagent.__init__` — `max_iterations: int = 5` |
| `munin/subagents/tool_forge.py` | 242-256 | Forge loop: LLM → code → AST guard → sandbox exec |
| `munin/subagents/tool_forge.py` | 379 | Last-iteration guard: `if iteration >= max(1, self.max_iterations - 1)` |
| `munin/subagents/tool_forge.py` | 446 | Exhaustion: `"Exhausted {self.max_iterations} iterations without a valid tool"` |
| `munin/subagents/graph_forge.py` | 94 | Graph config termination: `"max_iterations": 8` |
| `munin/subagents/sandbox.py` | full | `_ASTSandboxGuard`, `_RestrictedExec`, `safe_exec_script()` — hard-banned modules, restricted builtins |
| `munin/subagents/process_control.py` | full | `discover_runner_pids()`, `stop_detached_runners()`, `_signal_runner()` — SIGTERM/SIGKILL with grace period |

---

## 3. Tool Catalog

| File | Line(s) | What |
|------|---------|------|
| `munin/mcp/registry.py` | (varies) | `GENERATED_PREFIX = "gen__"` |
| `munin/mcp/registry.py` | (varies) | `ToolRegistry.rehydrate(mcp, state, settings)` — loads all `active=1` rows from `procedural` table at startup |
| `munin/mcp/registry.py` | (varies) | `register()` — `importlib.util.spec_from_file_location` → `mcp.tool()(handler)` |
| `munin/mcp/registry.py` | (varies) | `register_state_only()` — persists to `procedural` table without MCP hot-load (used by subprocess runners) |
| `munin/mcp/registry.py` | (varies) | `_CALLABLE_CACHE` — in-memory cache of loaded callables |
| `munin/mcp/registry.py` | (varies) | `_ATTACHED_RUNTIME` + `_sync_runtime_thread()` — daemon thread syncs subprocess-forged tools into live MCP |
| `munin/mcp/registry.py` | 323 | `register_state_only` docstring: "Called from subprocess contexts (the subagent runner)" |
| `munin/mcp/registry.py` | (varies) | `_enforce_tool_limits()` — caps total tool count |
| `munin/mcp/registry.py` | (varies) | `_make_tool_schema()` — generates OpenAI-compatible tool schema |

---

## 4. Subagent Factories (Forge Tools)

| File | Line(s) | What |
|------|---------|------|
| `munin/mcp/tools/forge_tool.py` | 81-99 | MCP `tool_forge` entry — `max_iterations` clamped to `[1, 12]`, default 5 |
| `munin/mcp/tools/forge_tool.py` | 128 | Delegates to `ToolForgeSubagent.forge(spec)` |
| `munin/mcp/tools/graph_forge_tool.py` | full | MCP `graph_forge` entry — delegates to `GraphForgeSubagent`, persists to `generated_graphs` table |
| `munin/subagents/tool_forge.py` | 73 | LLM prompt constraint: "only import `allowed_imports`; banned: `os`, `subprocess`, `socket`, `ctypes`" |
| `munin/subagents/graph_forge.py` | full | `GraphForgeSubagent` — forges ReAct subagent config (JSON → `generated_graphs` table), system prompt in Chinese |
| `munin/forge/extension_forge.py` | 121-122 | `_run()` wrapper — `subprocess.run()` for extension validation |
| `munin/forge/extension_forge.py` | 129 | `py_compile` validation — `subprocess.run([python, "-m", "py_compile", ...])` |
| `munin/forge/extension_guard.py` | 17 | Extension guard regex: blocks `import subprocess`, `import ctypes`, `import socket` |

---

## 5. Production Dispatcher + Stores

| File | Line(s) | What |
|------|---------|------|
| `munin/production/dispatcher.py` | full | `ProductionDispatcher` — `run_once()`, claim run with lease, streaming assistant text |
| `munin/production/dispatcher.py` | (varies) | HITL approval: `update_run_state("waiting_for_human")` → polls `human_request_approvals` |
| `munin/production/dispatcher.py` | (varies) | `pre_iteration_hook` drains `run_guidance_queue` — injects operator guidance into context |
| `munin/production/dispatcher.py` | (varies) | `tool_call_ids` correlation — links tool calls to reasoning events |
| `munin/production/store.py` | full | `ProductionStore` — `MIGRATION_ID`, forward-only checksum-guarded migration |
| `munin/production/store.py` | (varies) | `_MIGRATION_SQL` + `_EXPECTED_SHA256` — schema validation |
| `munin/production/store.py` | (varies) | `RUN_STATES` — queued, running, waiting_for_human, completed, failed, interrupted, cancelled |
| `munin/production/store_v3_1.py` | full | `install_v3_1_extensions()` via `types.MethodType` — no UUID helper regression |
| `munin/production/store_v3_1.py` | (varies) | New tables: `conversation_collaborators`, `conversation_notes`, `conversation_presence`, `run_guidance_queue` |
| `munin/production/store_v3_1.py` | (varies) | New columns: `tool_calls.parallel_group_id`, `tool_calls.tool_use_id`, `human_requests.requested_by_actor_id`, `timeline_messages.actor_id` |
| `munin/production/store_v3_1.py` | (varies) | New methods: `upsert_collaborator`, `list_collaborators`, `add_note`, `list_notes`, `drain_guidance_queue`, `upsert_presence`, `list_presence` |
| `munin/production/asgi.py` | full | Starlette ASGI app — authenticated HTTP boundary |
| `munin/production/asgi.py` | 42-43 | `TERMINAL_STATES = {"completed", "failed", "cancelled", "interrupted"}`, `NON_TERMINAL_RUN_STATES = {"queued", "running", "waiting_for_human"}` |
| `munin/production/asgi.py` | 240-256 | `page_agent_action` route — validates via `validate_page_action()`, gated by `MUNIN_PAGE_AGENT_ENABLED` |
| `munin/production/asgi.py` | 887 | Full route table — auth, conversations, runs, events, HITL, artifacts, providers |
| `munin/production/parallel.py` | full | `execute_tool_batch()` — parallel tool dispatch |
| `munin/production/parallel.py` | (varies) | `PARALLEL_SAFE_TOOLS` set — whitelist of tools safe for parallel execution |
| `munin/production/parallel.py` | (varies) | `MUNIN_MAX_PARALLEL_TOOLS = 6` |
| `munin/production/parallel.py` | (varies) | `parallel_safe()` decorator |
| `munin/production/forge_progress.py` | full | `emit_forge_stage()` — emits forge lifecycle events |
| `munin/production/forge_progress.py` | (varies) | `FORGE_STAGES` frozenset — 12 stages |
| `munin/production/page_agent.py` | 25 | `validate_page_action()` — role, feature_enabled, action, target, parameters |
| `munin/production/page_agent.py` | (varies) | `ALLOWED_ACTIONS`, `SENSITIVE_ACTIONS` — action whitelists |
| `munin/production/page_agent.py` | (varies) | `PageAction` dataclass |

---

## 6. Persistence Tables

### MCP-side (ephemeral/Turso shared_state.sqlite)

| Table | Source | File:Line |
|-------|--------|-----------|
| `shared_intel` | OFFX-MCP | `munin/mcp/shared_state.py` |
| `tasks` | OFFX-MCP | `munin/mcp/shared_state.py` |
| `agent_messages` | OFFX-MCP | `munin/mcp/shared_state.py` |
| `agent_presence` | OFFX-MCP | `munin/mcp/shared_state.py` |
| `episodic` | Munin | `munin/mcp/shared_state.py` |
| `semantic` | Munin | `munin/mcp/shared_state.py` |
| `procedural` | Munin | `munin/mcp/shared_state.py` — forged tools registry |
| `generated_graphs` | Munin | `munin/mcp/shared_state.py` — forged subagent configs |
| `agent_wake_queue` | Munin | `munin/mcp/shared_state.py` — wake request queue, `try_claim_spawn_slot` at line 698 |

### Production-side (durable Turso/SQLite)

| Table | File:Line |
|-------|-----------|
| `conversations` | `munin/production/store.py` |
| `conversation_participants` | `munin/production/store.py` |
| `runs` | `munin/production/store.py` |
| `conversation_broadcasts` | `munin/production/store.py` |
| `timeline_messages` | `munin/production/store.py` |
| `tool_calls` | `munin/production/store.py` |
| `reasoning_events` | `munin/production/store.py` |
| `reasoning_scrub_queue` | `munin/production/store.py` |
| `human_requests` | `munin/production/store.py` |
| `human_request_approvals` | `munin/production/store.py` |
| `artifacts` | `munin/production/store.py` |
| `conversation_run_links` | `munin/production/store.py` |
| `operator_identities` | `munin/production/store.py` |
| `operator_sessions` | `munin/production/store.py` |
| `conversation_search` | `munin/production/store.py` |
| `generated_tools` | `munin/production/store.py` |
| `heartbeats` | `munin/production/store.py` |
| `conversation_collaborators` | `munin/production/store_v3_1.py` |
| `conversation_notes` | `munin/production/store_v3_1.py` |
| `conversation_presence` | `munin/production/store_v3_1.py` |
| `run_guidance_queue` | `munin/production/store_v3_1.py` |

### Persistence abstraction

| File | Line(s) | What |
|------|---------|------|
| `munin/mcp/persistence.py` | full | `open_connection()`, `ConnectionProxy` — local SQLite or Turso libsql backend |
| `munin/mcp/persistence.py` | (varies) | `MUNIN_DB_URL` env var — empty = local file; `libsql://...` = Turso |

---

## 7. Frontend Event/Stream/Cache

### SSE streams

| File | Line(s) | What |
|------|---------|------|
| `app/src/lib/useRunEvents.ts` | full | `EventSource` → `/api/production/runs/:id/events`; silence detector at 45s; status types: `connecting`/`live`/`stale`/`closed`; React Query merge |
| `app/src/lib/useConversationEvents.ts` | full | `EventSource` → `/api/production/conversations/:id/events`; events: `note-appended`, `presence-changed`, `run-transition`, `guidance-delivered`, `heartbeat`, `warning`, `close`; stale timer 45s |

### API proxies

| File | Line(s) | What |
|------|---------|------|
| `app/src/app/api/production/[[...path]]/route.ts` | full | Proxy to `MUNIN_PRODUCTION_API_URL` (default `http://127.0.0.1:8787`); `maxDuration=14400`; SSE passthrough with `Last-Event-ID` |
| `app/src/app/mcp/[[...path]]/route.ts` | full | Proxy to `http://127.0.0.1:8890`; MCP header allowlist; optional server-side auth |

### Query/state management

| File | Line(s) | What |
|------|---------|------|
| `app/src/lib/queries.ts` | 37-44 | `useConversations(query)` — staleTime 30s |
| `app/src/lib/queries.ts` | 53-66 | `useConversation(id, sseHealthy)` — polling: 5s if no data, 10s if SSE live + non-terminal, 5s if SSE unhealthy |
| `app/src/lib/queries.ts` | 69-81 | `useRunDetail(id)` — polling: 5s while non-terminal, stops on terminal |
| `app/src/lib/queries.ts` | 84-91 | `useRunGuidance(id)` — staleTime 30s |
| `app/src/lib/mcp.ts` | full | `McpClient` — JSON-RPC 2.0 transport, retry with exponential backoff, circuit breaker, in-flight dedup |
| `app/src/lib/production-api.ts` | full | `productionApi` singleton — all API calls, type definitions |

### Zustand stores

| File | Line(s) | What |
|------|---------|------|
| `app/src/store/muninStore.ts` | full | Zustand store: `mcpUrl`, `mcpToken`, `settingsOpen`, `view`, `tools`, `messages`, `chatInput`, `conversations`, `activeConversationId`, `live` state |
| `app/src/store/muninStore.ts` | (varies) | `sendChatMessage()` — direct JSON-RPC call |
| `app/src/store/muninStore.ts` | (varies) | `updateToolCallResult()` — optimistic rollback |
| `app/src/store/floatingWindows.ts` | full | `useSyncExternalStore`-based floating window registry; localStorage persistence; kind = `"forge"` |

### Collab

| File | Line(s) | What |
|------|---------|------|
| `app/src/lib/useCollab.ts` | full | TanStack Query wrappers: `useNotes`, `usePostNote`, `usePresence`, `usePresenceHeartbeat`, `useCollaborators`, `useUpsertCollaborator` |

---

## 8. Chat Block Components (Timeline Rendering)

| File | What |
|------|------|
| `app/src/components/chat/blocks/ToolBlock.tsx` | Single tool call — name, state chip (running/completed/failed), expandable args+result |
| `app/src/components/chat/blocks/ThoughtBlock.tsx` | Reasoning/thought event — compact chip, expandable body, auto-expands while running |
| `app/src/components/chat/blocks/ParallelToolBlock.tsx` | Grouped parallel tools — `parallel_group_id` → single "Running N tools in parallel" widget |
| `app/src/components/chat/blocks/SubagentCard.tsx` | Nested subagent card — filtered reasoning+tools by `agent_name`, "Open window" button for forge agents |
| `app/src/components/chat/blocks/HitlRequest.tsx` | HITL decision — Approve/Deny/custom choices, justification textarea, nonce-signed |
| `app/src/components/chat/blocks/ArtifactChip.tsx` | File download chip — filename, size, language badge, download via same-origin proxy |
| `app/src/components/chat/blocks/HeartbeatBar.tsx` | Connection liveness — SSE status, phase display, elapsed timer |
| `app/src/components/chat/blocks/NoteBlock.tsx` | Operator note — "not sent to Munin" badge, avatar, timestamp |
| `app/src/components/chat/blocks/GuidanceBlock.tsx` | Operator guidance — dashed border, delivery status (queued/delivered @ step N), target agent routing |
| `app/src/components/chat/ForgeFloatingChat.tsx` | Floating forge window — live trace, mini composer for subagent-scoped guidance, "Extend budget +5min" |

---

## 9. HITL Paths

| File | Line(s) | What |
|------|---------|------|
| `munin/production/dispatcher.py` | (varies) | `update_run_state("waiting_for_human")` — run pauses, `human_requests` row inserted |
| `munin/production/dispatcher.py` | (varies) | `pre_iteration_hook` drains `run_guidance_queue` — injects operator text into next LLM context |
| `munin/production/dispatcher.py` | (varies) | Polls `human_request_approvals` for nonce match → resumes run with `choice` + `guidance` |
| `munin/production/asgi.py` | 887 | Routes: `/api/hitl/requests`, `/api/hitl/requests/{id}/resolve` |
| `munin/production/asgi.py` | (varies) | `page_agent_action` route — validates action, requires confirmation for sensitive actions |
| `app/src/components/chat/blocks/HitlRequest.tsx` | 36-88 | Frontend HITL: `useResolveHumanRequest()` → `productionApi.resolveHumanRequest(id, choice, nonce, guidance)` |
| `app/src/lib/queries.ts` | (varies) | `useResolveHumanRequest()` mutation — calls `productionApi.resolveHumanRequest()` |
| `munin/production/page_agent.py` | 25 | `validate_page_action()` — gates by `feature_enabled` (env `MUNIN_PAGE_AGENT_ENABLED`) |

---

## 10. Hard Caps

| Constant | Value | Location |
|----------|-------|----------|
| `MuninAgent._HARD_CEILING` | `10_000` | `munin/core/munin_agent.py:287` |
| `MuninAgent.respond()` default `max_iterations` | env `MUNIN_MAX_ITERATIONS` or `_HARD_CEILING` (10k) | `munin/core/munin_agent.py:323-331` |
| `ReActSubagentBase.max_iterations` default | `8` | `munin/subagents/base.py:657` |
| `ToolForgeSubagent.max_iterations` default | `5` | `munin/subagents/tool_forge.py:166` |
| `tool_forge` MCP tool `max_iterations` clamp | `[1, 12]`, default 5 | `munin/mcp/tools/forge_tool.py:99` |
| `munin_wake` task `max_iterations` | default 5 | `munin/subagents/runner.py:89` |
| Graph forge config `termination.max_iterations` | `8` | `munin/subagents/graph_forge.py:94`, `munin/subagents/runner.py:195` |
| `MUNIN_MAX_NESTED_SUBAGENTS` | `5` | `munin/subagents/base.py` |
| `MUNIN_MAX_PARALLEL_TOOLS` | `6` | `munin/production/parallel.py` |
| `iteration_token_budget` | `2000` tokens per step | `munin/subagents/base.py` |
| `MAX_INLINE_BODY` (result overflow) | `12_000` bytes | `munin/subagents/runner.py:369` |
| `PRESENCE_LEASE_SECONDS` | `30` | `munin/mcp/shared_state.py` |
| `ORPHAN_TTL_SECONDS` | `1800` (30 min) | `munin/mcp/main.py` |
| `sleep_after_idle` (runner CLI default) | `120` seconds | `munin/subagents/runner.py:268` |
| `sleep_after_idle` (CLI `munin subagent`) | `120` seconds | `munin/cli.py:117` |
| Discord `max_iterations` | `60` (env `MUNIN_DISCORD_MAX_ITERATIONS`, capped at 60) | `munin/integrations/discord_config.py:43` |
| `munin_chat` tool `max_iterations` clamp | `[1, 10_000]`, default from caller | `munin/mcp/tools/munin_tools.py:514` |
| `diagnostics` paranoid `max_iterations` | `3` | `munin/mcp/tools/diagnostics_tool.py:332` |
| `collapseThreshold` (frontend reasoning) | `60` events → show last 20 | `app/src/components/FlightDeck.tsx:895` |
| `MAX_INLINE` (GuidanceBlock body) | `320` chars | `app/src/components/chat/blocks/GuidanceBlock.tsx:31` |
| SSE silence detector | `45_000` ms | `app/src/lib/useRunEvents.ts` |
| `MUNIN_SSE_MAX_SECONDS` default | `4h` (14400s) | `munin/production/asgi.py:9-10` |
| `MUNIN_SSE_HEARTBEAT_SECONDS` default | `20s` | `munin/production/asgi.py:14` |
| `maxDuration` (Next.js route) | `14400` | `app/src/app/api/production/[[...path]]/route.ts` |

---

## 11. Subprocess Usage

| File | Line(s) | What |
|------|---------|------|
| `munin/core/orchestrator.py` | 114-123 | `subprocess.Popen(cmd, stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL)` — spawns `python -m munin.subagents.runner <name>` |
| `munin/subagents/runner.py` | 3 | Docstring: "The Orchestrator spawns this as a subprocess" |
| `munin/subagents/runner.py` | 38 | `_NATIVE_SUBAGENTS` frozenset — validated before spawn |
| `munin/subagents/process_control.py` | full | `discover_runner_pids()` — finds runner processes by name; `stop_detached_runners()` — SIGTERM then SIGKILL |
| `munin/cli.py` | 120-121 | `subprocess.run([python, "-m", "munin.subagents.runner", name, "--sleep-after-idle", ...])` |
| `munin/cli.py` | 191-239 | `subprocess.run(["bash", ...])` for tunnel, ldap up/down/status/logs |
| `munin/mcp/main.py` | 151-155 | `subprocess.run(["git", ...])` — git operations |
| `munin/mcp/git_persist.py` | 65-67 | `_run_git()` — `subprocess.run(["git", ...])` |
| `munin/mcp/git_persist.py` | 121-149 | `subprocess.run()` / `subprocess.TimeoutExpired` for push operations |
| `munin/mcp/opsec.py` | 231-236 | `subprocess.Popen(args, stdout=PIPE, stderr=PIPE)` — security preflight scans |
| `munin/mcp/opsec.py` | 342-354 | `subprocess.run(args, capture_output=True, timeout=timeout)` — security scans |
| `munin/mcp/intel.py` | 274 | `subprocess.run()` — intel tools |
| `munin/mcp/syncer.py` | 72-77 | `subprocess.run(["git", ...])` — wiki sync |
| `munin/forge/extension_forge.py` | 121-129 | `subprocess.run()` — extension validation, `py_compile` |
| `munin/forge/extension_guard.py` | 62 | `subprocess.run()` — extension guard |
| `munin/subagents/sandbox.py` | 39 | `subprocess` in banned modules list for generated tools |

---

## 12. Generated Tool Surface

| Item | Detail |
|------|--------|
| `munin/generated/` directory | **No generated files currently on disk** (glob `munin/generated/gen__*.py` returned empty) |
| `procedural` table | Stores generated tool metadata: `script_path`, `signature`, `tags`, `active` flag |
| `generated_graphs` table | Stores forged subagent configs: `name`, `purpose`, `system_prompt`, `tool_whitelist`, `active` flag |
| `registry.rehydrate()` | Called at MCP startup (`munin/mcp/main.py:1380`) — reloads all `active=1` tools from `procedural` |
| `registry.start_runtime_sync()` | Called at MCP startup (`munin/mcp/main.py:1384`) — daemon thread syncs subprocess-forged tools into live MCP |
| `ToolRegistry.register()` | Full path: `importlib.util.spec_from_file_location` → `mcp.tool()(handler)` |
| `ToolRegistry.register_state_only()` | Persists to `procedural` table without MCP hot-load (used by subprocess runners) |
| AST guard | `munin/subagents/sandbox.py` — blocks `import subprocess`, `import ctypes`, `import socket`; blocks `__class__`, `__subclasses__`, `__mro__`, `exec`, `eval` |
| Extension guard | `munin/forge/extension_guard.py` — regex blocks `import subprocess`, `from subprocess import`, `import ctypes`, `import socket` |

---

## 13. Frontend Dependencies Check

| Package | Version | In `app/package.json`? |
|---------|---------|----------------------|
| `ai` | — | **NOT PRESENT** |
| `@ai-sdk/*` | — | **NOT PRESENT** |
| `@tanstack/react-query` | `^5.56.2` | Yes |
| `zustand` | `^4.5.4` | Yes |
| `next` | `^14.2.35` | Yes |
| `react` | `^18.3.1` | Yes |
| `react-markdown` | present | Yes |
| `uuid` | `^14.0.1` | Yes |
| `sonner` | present | Yes |
| `lucide-react` | present | Yes |

---

## 14. MCP Server Entry Point

| File | Line(s) | What |
|------|---------|------|
| `munin/mcp/main.py` | (varies) | `FastMCP` singleton (`MCP`) — stdio/sse/streamable-http transports |
| `munin/mcp/main.py` | 1433-1479 | `_make_auth_middleware(expected_token)` — ASGI Bearer token auth, constant-time comparison |
| `munin/mcp/main.py` | (varies) | SIGTERM handler (`_handle_sigterm`) — graceful shutdown |
| `munin/mcp/main.py` | (varies) | `_kill_stale_stdio_orphans(ORPHAN_TTL_SECONDS=1800)` — cleanup orphaned runner processes |
| `munin/mcp/main.py` | 1380-1384 | Module-level: `registry.rehydrate()` + `registry.start_runtime_sync()` |
| `munin/mcp/main.py` | 1389-1425 | `_start_discord_operator_bridge()` — optional Discord integration |
| `munin/mcp/main.py` | 1411 | Discord handler: `MuninAgent(SETTINGS).respond(prompt, max_iterations=config.max_iterations)` |

---

## 15. Forge Progress + Extension System

| File | Line(s) | What |
|------|---------|------|
| `munin/production/forge_progress.py` | full | `emit_forge_stage()` — emits lifecycle events to `episodic` table |
| `munin/production/forge_progress.py` | (varies) | `FORGE_STAGES` frozenset — 12 stages (planning, coding, linting, typechecking, testing, etc.) |
| `munin/forge/extension_forge.py` | full | Extension forge — `subprocess.run()` for validation, `py_compile` for syntax |
| `munin/forge/extension_guard.py` | full | Extension guard — regex + subprocess-based validation |
| `munin/forge/extension_guard.py` | 17 | Blocked patterns: `import subprocess`, `from subprocess import`, `import ctypes`, `import socket` |
| `munin/mcp/tools/forge_tool.py` | 81 | MCP `tool_forge` — `max_iterations: int = 5` default, clamped `[1, 12]` |
| `munin/subagents/tool_forge.py` | 11 | Forge loop description: "If FAIL: feed the error back to the LLM and iterate" |
| `munin/subagents/tool_forge.py` | 166 | `ToolForgeSubagent.__init__` — `max_iterations: int = 5` |
| `munin/subagents/tool_forge.py` | 242 | `for iteration in range(1, self.max_iterations + 1)` |
| `munin/subagents/tool_forge.py` | 379 | Last-iteration guard |
| `munin/subagents/tool_forge.py` | 446-453 | Exhaustion message |

---

## Summary: Key Glue Connections

```
Frontend (React/Zustand)
  │
  ├── useRunEvents → SSE /api/production/runs/:id/events → ProductionStore.run_events
  ├── useConversationEvents → SSE /api/production/conversations/:id/events
  ├── queries.ts → TanStack Query → productionApi.* → /api/production/*
  ├── muninStore.sendChatMessage → JSON-RPC → MCP server
  └── FloatingWindows → ForgeFloatingChat → useRunGuidance → productionApi.guideRun
        │
        ▼
Next.js Proxy (/api/production/[[...path]])
  │  maxDuration=14400, SSE passthrough, Last-Event-ID
  │
  ▼
Production ASGI (Starlette, :8787)
  │  Auth: HttpOnly cookies (operator sessions)
  │
  ├── ProductionDispatcher.run_once()
  │     ├── claim_run (atomic lease)
  │     ├── pre_iteration_hook → drain run_guidance_queue
  │     ├── MuninAgent.respond() → ReAct loop (max_iterations)
  │     ├── execute_tool_batch (parallel, max 6)
  │     └── HITL → update_run_state("waiting_for_human")
  │
  ├── ProductionStore (21 tables)
  │     └── v3_1 extensions via types.MethodType
  │
  └── Page Agent → validate_page_action()
        └── MUNIN_PAGE_AGENT_ENABLED gate

MCP Server (FastMCP, :8890)
  │  Auth: Bearer token (ASGI middleware)
  │
  ├── 65 fixed tools + N generated (rehydrated from procedural table)
  ├── ToolRegistry.rehydrate() + start_runtime_sync()
  ├── ToolForgeSubagent → AST guard → sandbox → gen__*.py → register
  ├── GraphForgeSubagent → generated_graphs table
  └── Orchestrator.wake() → try_claim_spawn_slot (atomic) → Popen(runner)
        │
        ▼
Subagent Runner (python -m munin.subagents.runner <name>)
  │  Idle timeout: 120s (configurable)
  │  RESULT overflow: 12KB → wake_artifacts/*.json
  │
  ├── ReAct loop (max_iterations=8 default)
  ├── build_tool_catalog() → loads gen__* from procedural
  ├── post_agent_message("munin", RESULT/ERROR)
  └── set_presence(RUNNING/IDLE/EXITING)
```
