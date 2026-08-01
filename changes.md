# Changes

Living changelog and hand-off log for Munin. Newest entries first. Entries
record the engineering timeline; use `ARCHITECTURE.md` and the operator guides
for the current runtime contract.

## 2026-08-01 - CI gates, canonical MCP endpoints, and provider reasoning replay

This follow-up closes the remaining CI failures without adding a second
application-specific agent loop:

- The supervisor removes the custom repetition guard that aborted a healthy
  live provider run after repeated output. It now uses LangChain's standard
  model and tool call limit middleware, controlled by
  `MUNIN_MODEL_CALL_LIMIT` and `MUNIN_TOOL_CALL_LIMIT` for a visible,
  safety-only budget.
- An explicitly emitted provider field (`reasoning_content`, `thinking`, or a
  typed reasoning block) and explicit `<think>` blocks become separate
  `provider_reasoning` envelopes. They are redacted and persisted with their
  provider and model-step metadata, replayed from `reasoning_events`, and
  translated to separate `reasoning-start`/`reasoning-delta`/`reasoning-end`
  UI parts. They are never concatenated into the final assistant answer or
  fabricated from graph/tool activity.
- The Actions MCP probe addresses FastMCP directly at `/mcp/` and the Next BFF
  at `/mcp`, refusing redirects with an actionable error. This fixes the
  308 retry loop found by the E2E lab.
- The E2E jobs pin `MUNIN_HTTPX_BINARY` to the exact ProjectDiscovery binary
  installed in that job, so an image-provided command cannot shadow it. Smoke
  failures now report a bounded stderr tail and return code without logging
  command arguments or secrets.
- CI installs from the committed Poetry lock, runs compile, fatal Ruff, the
  backend suite, TypeScript, Vitest, configured Next lint and the production
  frontend build. The real-provider smoke is now a post-merge/manual canary,
  requires `ldap_search` and `httpx_probe`, and validates a non-empty final
  answer. Its cleanup uses the shared fixture janitor instead of a broken
  shell heredoc.
- The web boundary now uses Next `15.5.21`, AI SDK `7.0.47` and
  `@ai-sdk/react` `4.0.50`. The formerly interactive `next lint` command is
  replaced by the standard ESLint CLI. The lockfile overrides Next's affected
  `postcss` and `sharp` transitive packages to patched versions; the production
  dependency audit reports zero known vulnerabilities.
- Vitest now uses the supported 4.x release under Node 22, removing the
  remaining known critical/high development-server vulnerabilities inherited
  through its old Vite stack. The test suite is rerun after the major upgrade.
- The live canary now exercises native HITL end-to-end: it resolves the
  authenticated request with its one-time nonce, resumes the durable
  LangGraph checkpoint through the replay route, and only passes on a final
  completed answer. LDAP search accepts both safely escaped named and
  positional JSON parameter shapes emitted by providers. Async generator
  cleanup also handles cross-task ContextVar finalization without leaving
  unhandled task exceptions.
- Replay polling now tolerates the intentional `204 No Content` hand-off
  window immediately after HITL approval, so a detached runner can resume
  before the smoke evaluates its terminal state. The prompt names
  `httpx_probe` explicitly to make the live tool assertion deterministic.
- The standalone `Munin Live Session` workflow now points its MCP smoke at
  the unified `:8787` server instead of the retired `:8890` listener. The
  generated canary password is also masked before it is exported to Actions.
- Split-store replay now overlays hot `agent_runs` and assistant-placeholder
  status on the durable conversation aggregate. This prevents the HITL
  approval hand-off from returning a false `204` while the detached run is
  queued/running, so AI SDK `resumeStream()` can reattach and the agent keeps
  acting autonomously after the approved checkpoint.
- The runtime no longer applies the small LangGraph recursion cap by default.
  `MUNIN_RECURSION_LIMIT` accepts an explicit positive override; omitted,
  `0`, or `unlimited` uses the framework-compatible unlimited sentinel while
  leases, cancellation, approval, and standard model/tool middleware remain
  the independent safety controls.
- Tool results are expanded by default in the live console, and the console
  now hydrates the original user timeline from IndexedDB or the authoritative
  conversation aggregate before replay. For an explicitly trusted lab where
  exact credential-shaped output is required, `MUNIN_REDACTION_MODE=off`
  disables the shared persistence/audit redaction policy; redaction remains
  the default when the variable is absent.
- The console now exposes encrypted BYOK provider profiles: operators can add
  an HTTPS OpenAI-compatible endpoint/model, switch the active profile, and
  return to environment defaults or continue the same conversation without
  exposing the API key to the browser. The selected profile applies to the
  next turn; the conversation id and durable history remain unchanged.

## 2026-08-01 — Durable chat recovery after process restart

The AI SDK replay endpoint already persisted operator-visible run events, but
the detached executor itself was process-local: a crash left a `running` row
until its old four-hour lease expired, with no worker to resume the LangGraph
checkpoint. The production chat path now uses a short renewable fenced lease
and an ASGI-lifespan recovery scanner:

- `ProductionStore.requeue_expired_runs_for_resume()` atomically changes only
  expired `running` rows to `queued`, clears their owner token, and records
  `run.recovery_queued`. It deliberately never selects `waiting_for_human` or
  `cancelled` rows. `recover_expired_runs()` retains its legacy terminal
  `interrupted` contract for dispatcher callers.
- The scanner claims queued rows through the existing fenced direct-claim
  transition. A run that has a LangGraph checkpoint continues with the same
  conversation `thread_id` and `None` input; a run whose process died before
  any checkpoint starts its original prompt once. A resolved native HITL row
  resumes only with persisted `Command(resume={"decisions": [...]})`; an
  unresolved HITL request is never auto-executed.
- A renewal heartbeat means long-running work remains owned, while a dead
  process becomes recoverable after `MUNIN_CHAT_LEASE_SECONDS` (120 seconds by
  default) without another server stealing an active worker. Lease loss or an
  operator cancellation prevents the stale executor from finalising output.
- `human_request.resolved` now records a stable request id, approved/rejected
  display state, approved tool name and sanitized action args for UIMessage
  replay. It never includes the one-time nonce or provider/private reasoning.

This follows the LangGraph persistence and Deep Agents HITL contracts: use a
persistent checkpointer, retain the same `thread_id`, and resume an interrupt
with `Command`. `tests/test_chat_recovery.py` covers fenced crash recovery,
HITL non-autostart and approved-command recovery; the focused backend suite is
green (19 passed) and the full backend suite is green (222 passed, 4 skipped).

## 2026-08-01 — CI repair Part 2: fix double `/mcp` mount prefix + session-manager lifespan

The Fase 3 unification (`munin serve` mounting the FastMCP streamable-http
sub-app under `Mount("/mcp")`) shipped two latent bugs that made every
`POST /mcp` return **404**, breaking both the e2e_lab MCP exercise and the
live-LLM MCP catalog sanity check on CI. Verified against the FastMCP
official docs (gofastmcp.com/deployment/http) and Context7 over the
`mcp` Python SDK (modelcontextprotocol/python-sdk):

1. **Double prefix `/mcp/mcp`.** `FastMCP("munin-mcp")` defaults
   `streamable_http_path="/mcp"`, so its sub-app registers `Route("/mcp")`.
   `Mount("/mcp", app=sub)` strips the `/mcp` prefix before delegating, so
   the only public path that matched the inner route was `/mcp/mcp`. The
   canonical fix (per Context7 quote: *"Setting `streamable_http_path` to
   `/` makes the mount prefix the complete public path"*) is to set the
   inner route to `/` so the public path becomes `/mcp/`.
   - `munin/mcp/main.py:1543` `create_mcp_app` now sets
     `MCP.settings.streamable_http_path = "/"` before building the app.

2. **Session manager not initialized.** Starlette does not propagate
   `startup`/`shutdown` lifespans to sub-apps mounted via `Mount`. Without
   explicitly entering `MCP.session_manager.run()`, the first request to
   the sub-app raised `RuntimeError("Task group is not initialized. Make
   sure to use run().")`. The MCP Python SDK exposes
   `mcp.session_manager` (lazily created after `streamable_http_app()`)
   whose `run()` is an async context manager; the host Starlette app must
   own it in its lifespan.
   - `munin/server.py` `_lifespan` now `__aenter__`/`__aexit__`es the
     session manager around the existing Discord + pool-shutdown hooks.

3. **`/mcp` without trailing slash.** Even with the inner route at `/`,
   a bare `POST /mcp` leaves the sub-app with an empty path that
   `Route("/")` does not match (Starlette `Mount` only redirects
   `/mcp` -> `/mcp/` when no inner route consumes it, AND the outer
   `Mount("/", http_app)` would intercept the normalised request first).
   Added an explicit `Route("/mcp", RedirectResponse("/mcp/", 307),
   methods=[GET,POST,DELETE])` before the `Mount("/mcp")` so bare-path
   clients are bumped cleanly; clients that follow 307 (fetch, curl -L,
   the GUI same-origin proxy) work transparently.

   `scripts/ci_live_smoke.py` `_endpoint()` now always returns the
   trailing-slash form (`{base}/mcp/`), and the `live-session.yml`
   "Verify Munin MCP is answering" verifier (which uses urllib and does
   NOT follow 307 on POST) now POSTs to `http://127.0.0.1:8787/mcp/`.

Validation: `python -m munin.server.create_app` builds; a uvicorn run on
`127.0.0.1:8787` serves `POST /mcp/` -> 200 with `mcp-session-id` + SSE
`event: message` JSON-RPC, `GET /health` -> 200; `tests/test_production_foundation.py`
11/11 green.

## 2026-08-01 — CI repair: tests + smoke + workflow aligned with the Fase 2-4 contract

The migration (issue #9) removed `claim_next_run` (replaced by the direct
claim in `POST /api/chat`) and the `/turns` + `/api/runs/*` two-hop, and
unified the two-process launch into `munin serve` — but tests, the live-LLM
smoke and `ci.yml` still exercised the old contract, so CI ran red on
`feat/issue9-deep-agents-migration` (3 jobs: backend tests, live LLM smoke,
E2E GUI MCP proxy).

### Backend tests (`tests/test_production_foundation.py`)
- `test_run_claim_is_direct_exclusive_and_lease_expiry_recovers` (renamed from
  `test_leased_run_rejects_late_worker_and_recovers_expired_claim`): claims
  via `_claim_direct` (chat.py) instead of the removed `claim_next_run`;
  asserts a second direct claim is rejected (`RuntimeError`) and the
  lease-expiry → `recover_expired_runs` → `interrupted` path still works.
- `test_human_gate_tools_subagents_retry_and_recorded_branch`: uses
  `_claim_direct` and its `lease_token` for `complete_run`.
- `test_asgi_login_uses_cookie_session_and_csrf_for_turns`: now drives
  `POST /api/chat` (SSE, `X-Munin-Run-Id` header, run claimed to `running`)
  with a monkeypatched `_stream_chat`, and asserts a missing CSRF token is
  rejected with 403.
- New `test_fixture_user_can_be_created_and_deleted_by_test` for the new
  `delete_user_for_test` store method.

### Production store (`munin/production/store.py`)
- Added `delete_user_for_test(username)` (ProductionStore + MuninStore
  façade): removes a CI fixture user (must start with `llm_smoke_`, refuses
  anything else) plus its sessions, with audit row.

### Live LLM smoke (`scripts/live_llm_smoke.py`)
- Login no longer depends on `bootstrap_admin` (global-once on the shared
  Turso → 401): CI pre-creates a per-run fixture user exported via
  `MUNIN_LIVE_SMOKE_ADMIN` / `MUNIN_LIVE_SMOKE_PASSWORD`.
- Replaced `POST /api/conversations/{id}/turns` + `GET /api/runs/*` polling
  with `POST /api/chat`: reads the SSE stream to `close`, extracts
  `X-Munin-Run-Id`, terminal `run_state` envelope and `tool_intent` count.
- Conversations are tagged with `MUNIN_E2E_TEST_RUN_ID` (tags + scope) so the
  janitor's exact-namespace cleanup can remove them.
- `_classify_failure` reads the `run_state.error` envelope instead of the
  removed run detail endpoint; `OSError` (socket timeouts) now surfaces as a
  classified failure instead of crashing.

### CI workflow (`.github/workflows/ci.yml`)
- `e2e_lab` and `live-llm-smoke` launch the unified `munin serve` on :8787
  (HTTP API at `/`, FastMCP at `/mcp`) instead of the pre-Fase-3 two-process
  launch (`munin mcp` :8890 + `munin production-api` :8787); the GUI proxy
  check now passes because the frontend route forwards to 8787 which actually
  mounts `/mcp`.
- MCP catalog smokes point at `MUNIN_SMOKE_BASE_URL=http://127.0.0.1:8787`.
- `live-llm-smoke` now uses a valid `e2e_<run_id>_deadbeef` test namespace
  (was `llm_smoke_…`, which `cleanup_test_run` rejected), creates the
  fixture user via the store before the run, and deletes it in the `always()`
  cleanup step.

Validation: `tests/test_production_foundation.py` 11/11 pass locally
(Windows venv); full-suite failures elsewhere are local-env artifacts
(stale `langchain` without `create_agent`, LLM-dependent tests). YAML parses.

## 2026-07-31 — Fleet integration: bug fixes, singleton graph, delta sync, browser cache

Hand-off log for the Deep Agents + AI SDK v5 migration follow-up (issue #9).
All changes landed on `feat/issue9-deep-agents-migration`. Validation:
`tsc --noEmit` clean, `next build` OK, backend `py_compile` + `import` OK,
`/health` smoke 200 (86 MCP tools), delta-sync functional smoke (hot→durable
5 rows, outbox trim to 0, idempotent re-flush).

### Bug fixes (from audit fleet)
- `app/src/components/AgentConsole.tsx:125,130` — StatusBadge now uses
  `text-warning` / `text-success` tokens instead of the hardcoded
  `text-yellow-400` / `text-green-400` (art-direction rule: semantic colors
  only via tokens).
- `munin/core/middleware/progress_emit.py` — `tool_result` / `tool_failed`
  envelopes now carry `tool_name` (was dropped after the `_before` → `_after`
  refactor), so the audit trail records the tool for completed/failed calls,
  not "unknown".
- `app/src/app/layout.tsx` + `app/tailwind.config.ts` — loaded Inter and
  JetBrains Mono via `next/font/google` (CSS vars `--font-inter` /
  `--font-geist-mono`); `font-sans` / `font-mono` Tailwind utilities now
  resolve to the actual fonts instead of falling back to system-ui.
- `README.md:43` — stale `soul_reject_proposal` mention corrected to
  `soul_propose_edit → PR (human merge)` (the reject tool never existed).

### Singleton supervisor graph + shared checkpointer (issue #9 §3)
`munin/core/supervisor.py`:
- `_GRAPH_CACHE` keyed by `(model identity, active gen__* tool set +
  signatures, soul prompt hash, SharedStateStore identity)` — the compiled
  Deep Agents graph is now built ONCE per process and reused across requests.
  `build_munin_supervisor` returns the cached graph on a fingerprint hit.
- `_CHECKPOINTER_CACHE` now holds a single process-wide `MemorySaver`
  (`_get_checkpointer`), so `thread_id` checkpoints survive across turns /
  `run_id` changes — HITL interrupts and resume work within one Munin
  process. `invalidate_supervisor_cache()` drops only the graph (keeps the
  checkpointer) for callers to invoke when the procedural table changes.
- Per-run state (`run_id`, `progress_sink`) is no longer build-time: it is
  delivered per-invocation via `ACTIVE_RUN_ID` / `ACTIVE_PROGRESS_SINK`
  contextvars (set/reset by `runtime_adapter.supervisor_runner` around the
  `astream_events` loop) so one cached graph serves many concurrent runs.
- `munin/core/middleware/operator_guidance.py` and `progress_emit.py` —
  `_resolve_run_id` / `_resolve_sink` read the contextvars at hook time with
  build-time fallbacks (keeps the direct-construction contract intact for
  `tests/characterization/*`).

### Local-first Turso delta sync (issue #9 §3 conversation durability)
`munin/production/store.py` + `munin/mcp/config.py`:
- New settings: `MUNIN_HOT_DB_PATH` (default `/tmp/munin-hot.db`),
  `MUNIN_DURABLE_DB_URL` + `MUNIN_DURABLE_DB_AUTH_TOKEN` (fall back to legacy
  `MUNIN_DB_URL` / `MUNIN_DB_AUTH_TOKEN`), `MUNIN_LIBSQL_POOL_SIZE` /
  `MUNIN_LIBSQL_POOL_TIMEOUT_S`, `MUNIN_SYNC_AT_END` (default on),
  `MUNIN_SYNC_INTERVAL` (default 0 = only at run end / shutdown),
  `MUNIN_SYNC_BATCH_SIZE` (default 500).
- `MuninStore` split backend: hot SQLite for churn, durable Turso for long-
  lived rows. `complete_run` already migrates a finished run hot→durable;
  new `flush_pending_syncs()` uploads the REST of the conversation delta
  (messages, participants, summaries, run events, audit) via an outbox.
- `_sync_outbox` table + AFTER INSERT/UPDATE/DELETE triggers on every
  `_SYNC_TABLES` row (incl. `users`, so durable FKs stay satisfiable).
  Installed hot-only via `ProductionStore.install_sync_tracking()` from
  `MuninStore.from_settings`; the durable namespace adapter never sees the
  triggers.
- Flush lifecycle: capture `MAX(seq)` watermark → read referenced rows →
  upsert into durable in ONE transaction (parents before children via
  `_SYNC_TABLES` order) → trim outbox `<= watermark` only after a committed
  durable write → leftover entries replay on the next flush (crash-safe,
  idempotent via `INSERT OR REPLACE` on primary keys).
- Flush points: `close_pools()` (ASGI shutdown, guarded by `sync_at_end`)
  and end of `complete_run`. `sync_due()` enables opportunistic idle syncs
  when `MUNIN_SYNC_INTERVAL > 0`.
- Subagents came pre-built (issue #9 migration patches) for the pool, the
  namespace adapter, the `_mirror_user` / `_mirror_participant` hot mirrors,
  and the Fase-4 split-store routing table.

### Frontend browser cache (issue #9 cache layer)
`app/src/lib/cache/` (new): `db.ts` (hand-rolled IndexedDB wrapper, schema v1
with `conversations` / `messages` / `kv` stores, no new deps) +
`context.tsx` (`BrowserCacheProvider` + `useBrowserCache()` — actor-scoped
cache wipe, schema guard, write-through).
- `app/src/lib/queries.ts` — `useConversations` paints instantly from the
  IndexedDB mirror via v5 `placeholderData` then background-refetches;
  create / rename / archive run the v5 optimistic pattern (`onMutate` →
  `setQueryData` + IndexedDB write-through → server call → `onSuccess` /
  `onError` rollback → `onSettled` invalidate). `keepPreviousData` removed
  (v5 dropped it).
- `app/src/lib/aiChat.ts` — `useMuninChat` now seeds the visible timeline
  from the cache via `setMessages` on mount (cache-first render),
  persists the final message batch via `onFinish`, and sets/clears a run
  marker so the console can surface a "resume streaming?" hint after a
  mid-run refresh.
- `app/src/components/Providers.tsx` — `BrowserCacheProvider` mounted
  between `QueryClientProvider` and the app so queries/mutations can reach
  `useBrowserCache()`.

### Subagent creation wiring (verified, small fix)
`munin/core/autonomy/subagent_factory.py:61-70` — the `invoke_subagent` dict
branch no longer `NotImplementedError`s for `persisted_subagent_dict` runs;
it normalises the `SubAgent`-shaped dict (`description`→`purpose`, tool
objects→names, non-string model dropped) and materialises it as
`compiled_langgraph`. `compiled_langgraph` and `deep_agent` creation paths
were already correctly wired (fresh CompiledStateGraph each call); native
`subagents=` delegation on the supervisor remains unused (documented redesign
target for a follow-up).
