# Changes

Living changelog and hand-off log for Munin. Newest entries first.

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
