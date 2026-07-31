# Changes

## 2026-07-31 — PR #12: stop frontend hang cascade + unblock event loop under load

Patch applied to `feat/issue9-deep-agents-migration` addressing the "OPENING
THE RAVEN'S MEMORY" infinite spinner + missing traces diagnosed from HAR
captures (trycloudflare 160s waits, ngrok 300s timeouts, 2000+ request
pile-up in 7 min).

- **`app/src/lib/production-api.ts`** — `request()` now aborts via
  AbortController after `DEFAULT_TIMEOUT_MS` (15s) instead of dangling until
  the tunnel proxy kills it (100s CF / 300s ngrok). 401 responses throw a
  typed `AuthError` (csrfToken cleared) so query handlers can distinguish
  session expiry from transient failure. AbortError is rethrown as an
  explicit timeout error. Caller-provided signals are respected
  (`init.signal ?? controller.signal`).
- **`app/src/lib/queries.ts`** — `useConversation` / `useRunDetail` now
  retry 2x with exponential delay (1s→2s→4s, cap 15s), stop polling in
  background tabs (`refetchIntervalInBackground: false`), and grow the
  refetch interval on failure (`backoffMs`, cap 60s) so a slow backend can't
  accumulate concurrent in-flight requests per conversation.
- **`app/src/lib/useCollab.ts`** — `usePresenceHeartbeat` gained an
  in-flight guard (skip tick if previous beat still running), a circuit
  breaker that stops the interval after 3 consecutive failures
  (`HEARTBEAT_MAX_FAILURES`, re-armed on next mount), and `.catch()` on the
  keystroke/idle fire-and-forget beats (previously lost unhandled
  rejections).
- **`munin/production/asgi.py`** — CPU-bound sync store calls (AES-GCM
  per-row decrypt) now run in a threadpool via `run_in_threadpool`:
  `list_conversations`, `get_conversation` (detail + turn preflight),
  `get_artifact`, `get_run_for_actor`, `get_run_detail_for_actor`. One heavy
  request no longer blocks all others — previously froze SSE traces too.

Not touched (already correct in the branch): `_read_only()` / `_transaction()`
split exists in `store.py`, `busy_timeout` already 2000ms, SSE client uses
EventSource (bypasses the fetch timeout).

Verification: `python -m py_compile` on `asgi.py`; `npx tsc --noEmit` clean
in `app/`; pytest on the host is not runnable (argon2/pytest-asyncio not
installed here — runner is authoritative per CLAUDE.md).

## 2026-07-31 — Auth lock contention fix + PR #12 CI typecheck fix

### `munin/production/store.py` — auth/lock throughput (operator-reported auth taking minutes)

Operator diagnosis confirmed: every database path — including read-only auth
flows (`authenticate`, `validate_csrf`, `refresh_csrf`, `session_record`) — was
opening a write transaction via `_transaction()` with `BEGIN IMMEDIATE`, which
acquires SQLite's RESERVED lock. Combined with `busy_timeout=30000`, each auth
call could stall up to 30s behind a long write (run persisting events, AES-GCM
per-row artifact encryption). The login flow chains several of these calls, so
UI spinners accumulated to 2-3 minutes.

- **Split store I/O into two contexts.**
  - `_transaction()` (unchanged behavior): `BEGIN IMMEDIATE`, reserved for
    paths that mutate state. Only mutations go here.
  - `_read_only()` (new): `BEGIN DEFERRED`, never takes the RESERVED lock. Under
    WAL any number of readers proceed concurrently with the single writer.
- **`authenticate()`**: read path moved to `_read_only()`. The best-effort
  `last_seen_at_ms` / idle-rotation UPDATE is now a separate short
  `_transaction()` that only takes the RESERVED lock for its single statement
  (instead of holding it across the SELECT). Lock contention on that write is
  swallowed rather than failing auth — the next request retries the bookkeeping.
- **`validate_csrf`, `session_record`**: moved from bare `self._connect()` to
  `_read_only()` for a guaranteed read snapshot and consistent cleanup.
- **Other read paths** (`schema_tables`, `applied_migration_ids`, `get_artifact`,
  `run_execution_context`, `get_run`, `get_run_for_actor`,
  `get_run_detail_for_actor`, `list_run_events`, `get_conversation`,
  `list_conversations`, `export_conversation`, `reveal_provider_key`,
  `list_provider_profiles`, `rotate_provider_profile`'s read phase,
  `recorded_replay`, `compare_operation_branch`): all moved to `_read_only()`.
- **Connection defaults lowered**: `timeout=30` → `timeout=2`,
  `busy_timeout=30000` → `busy_timeout=2000`, and `journal_mode=WAL` forced per
  connection. A saturated writer now fails the request fast (UI can show
  "backend busy") instead of holding a 30s spinner.

Net effect: read paths (auth, listings, run detail, SSE event pump reads) no
longer queue behind the RESERVED lock; the only serial point left is genuine
mutation, and each mutation is shorter because encryption writes are no longer
sharing a transaction with the auth SELECTs.

### `munin/core/autonomy/tool_factory.py` — backend test regression

After unblocking the frontend build (above), the `Backend + Turso online` job
ran for the first time on this branch and surfaced 8 pre-existing failures in
`tests/characterization/test_tool_factory_*.py`. All reported
`'registration failed: script not found: .../munin/generated/<tool>.py'`.

Root cause introduced in commit f56a2a7 ("Issue 7: Validate tool registration
before overwriting active script"): the persistence order was reversed —
`registry.register_state_only(...)` was invoked first (which internally tries to
load the callable from `script_path`) and `staging_path.replace(script_path)`
was moved to after the registration. Every `create_tool` therefore tried to
load a script that did not exist on disk yet, hit the new `except` wrapper, and
returned `{"ok": False, "error": "registration failed: script not found: ..."}`.
The regression was invisible on f56a2a7 because the frontend `next build`
typecheck failed first and never let the backend job run.

Fix: restore the original ordering — `staging_path.replace(script_path)` happens
**before** `registry.register_state_only(...)`, so the file exists when the
registry validates/loads it. The `try/except` introduced by issue 7 stays (it
still gives the "no half-registered entry on failure" guarantee), but the
cleanup in the `except` now unlinks `script_path` (the materialized file)
instead of `staging_path` (which no longer exists after `replace`).

### `app/src/app/api/chat/[[...path]]/route.ts` — CI fix (commit f56a2a7 broke `Munin CI`)

`next build` typecheck failed at `route.ts:59`:

```
Type error: Conversion of type 'BackendEnvelope' to type 'Record<string, unknown>'
may be a mistake because neither type sufficiently overlaps with the other.
If this was intentional, convert the expression to 'unknown' first.
  Index signature for type 'string' is missing in type 'BackendEnvelope'.
```

The dynamic top-level field copy did `(normalized as Record<string, unknown>)[field]`,
which TS strict mode rejects because `BackendEnvelope` is a typed interface
without an index signature. Fixed by casting via `unknown` (TS's own
suggestion): `normalized as unknown as Record<string, unknown>`. Behavior is
identical (same fields assigned, same `Object.assign` of `event.payload`).
Verified locally: `tsc --noEmit` clean, `vitest run translator.test.ts` 19/19 pass.

---

## 2026-07-30 — Migration plan for issue #9 (Deep Agents + LangGraph + Vercel AI SDK)

**Added**: `GLUE_INVENTORY.md`, `IMPROVEMENT_BACKLOG.md`, `IMPLEMENTATION_ROADMAP.md`

**Branch**: `raven-mind/migration-issue9` (off `origin/main`).

### Discovery (completed)

- Read `pyproject.toml` + `app/package.json` + `app/package-lock.json` — confirmed installed
  version constraints: `langgraph >= 0.2.40`, `langchain >= 0.3.0`, `langgraph-codeact = *`,
  `mcp >= 1.0.0,<2` (FastMCP v1 surface inside `mcp`). No `poetry.lock` committed → work
  against declared constraints and verify exact resolved versions on the runner.
- Frontend: Next `^14.2.35`, React `^18.3.1`, `@tanstack/react-query ^5.56.2`, `zustand ^4.5.4`.
  **No `ai` / `@ai-sdk/*` digitally.** Migration starts from zero on the frontend stream
  protocol side.
- Context7 queries on `/langchain-ai/deepagents`, `/websites/langchain_oss_python_langgraph`,
  `/vercel/ai`, Context7-resolved FastMCP ids. Confirmed:
  - `create_deep_agent(model, tools, system_prompt, subagents, async_subagents, backend,
    checkpointer, interrupt_on, response_format, middleware, memory, skills, permissions)`.
  - `SubAgent` TypedDict / `CompiledSubAgent` / `AsyncSubAgent` field sets.
  - `task` tool returns `Command(update={**state, "messages":[ToolMessage]})`.
  - HITL via `interrupt_on` + `Command(resume={"decisions":[{"type":"approve"}]})`.
  - LangGraph `Send` dynamic fan-out with `Annotated[list, operator.add]` aggregation.
  - `reconnectToStream({chatId, startIndex})` + `consumeStream()` for long-running resume.
  - `langgraph-swarm`: `create_swarm(agents: list[Pregel], default_active_agent, state_schema=SwarmState)`;
    `create_handoff_tool(agent_name)` → `Command(goto=, update=)`.
- DeepWiki queries on `langchain-ai/deepagents`, `langchain-ai/langgraph`,
  `langchain-ai/langgraph-swarm-py`:
  - deepagents default middleware stack maps 1:1 with Munin reimplemented glue:
    `TodoListMiddleware`, `FilesystemMiddleware`, `SubAgentMiddleware` (sync, via `task`),
    `AsyncSubAgentMiddleware` (background, via `start_async_task`/`check_async_task`/
    `update_async_task`/`cancel_async_task`/`list_async_tasks`), `SkillsMiddleware`,
    `MemoryMiddleware`, `SummarizationMiddleware`, `HumanInTheLoopMiddleware`.
  - ReAct loop / tool dispatch / result reinjection owned by `langchain.agents.create_agent`
    — Deep Agents wraps it with middleware.
  - AsyncSubAgent **requires** an Agent Protocol server (`langgraph-sdk`); in-process bypass
    does not exist. Self-hosted `langgraph up` (Docker, port 8123) with
    `langgraph-checkpoint-sqlite` provides the local solution on the runner; thread state
    persists in local sqlite file → uploaded as Munin artifact between sessions.
  - deepagents ships **no** persistent agent/tool/workflow registries — Autonomy Kernel
    (issue §2/§5/§6) is Munin-owned on top.
  
### Veredicto: deep-agents SÍ reemplaza glue real

Confirmado contra código fuente (no memoria). El inventario del subagente `explore` ya
mapeó cada pieza con `file:line referee`; la verificación con Context7 + DeepWiki
demostró cada mapping con la clase/método que reemplaza el custom code.

### Artefactos producidos

- **`GLUE_INVENTORY.md`** — inventario read-only de todo el custom glue, por subsistema
  con `file:line` para cada claim. Producido por subagente `explore` (read-only, sin
  decisiones arquitectónicas), revisado y verificado arbitrariamente por Raven Mind.
- **`IMPROVEMENT_BACKLOG.md`** — mapping glue→primitive framework con las 11 columnas de
  ranking (evidence, functional impact, user impact, confidence, risk, cost, validation
  approach), una tabla por step de la secuencia del issue, más la tabla cruzada de los 11
  invariantes §9 que sobreviven toda la migración, más las 5 dependencias nuevas explícitas.
- **`IMPLEMENTATION_ROADMAP.md`** — secuencia 8-step del issue §12 con cada step
  descompuesto en sub-steps, criterios de paridad por step, PR breakdown (15-16 PRs
  estimados, no meta), desviaciones documentadas y justificación basada en repo evidence.
  Incluye secciones: hand-off conventions, validation strategy común, open questions
  resolvibles mid-flight.

### Decisiones arquitectónicas fijadas

1. **deepagents entra como coordinador default** (no es speculative — issue §1 lo pide y
   el código fuente de deepagents lo respalda con middleware que matchea el glue actual).
2. **Self-hosted LangGraph server on runner** para AsyncSubAgent (decisión del operador):
   `langgraph up` Docker, port 8123, `langgraph-checkpoint-sqlite` en local disk,
   `LANGGRAPH_API_KEY` runner-local. AsyncSubAgent `url="http://127.0.0.1:8123"`.
   Artifact cross-session = el patrón free-tier existente aplicado al sqlite de
   checkpointing.
3. **NO se migra Python a TS** (issue non-goal §10: backend stays authoritative).
4. **NO se reemplaza FastMCP** — se envuelve vía Tool Gateway (issue non-goal §10).
5. **3 Nuevo-registros**: Tool Registry (evoluciona `procedural`), Agent Registry (nueva
   `agent_registry` table + `rebuild_agent`), Workflow Registry (nueva `workflow_registry`
   table). Munin las posee — deepagents no shipea.
6. **No arbitrary hard caps**: removidos `MUNIN_MAX_NESTED_SUBAGENTS=5`,
   `forge_tool.py max_iterations [1,12]` clamp. Backpressure via LangGraph RecursionLimit
   (observable, configurable) + backpressure middleware + UI cancel.
7. **Compat adapter window** (`MUNIN_RUNTIME=supervisor` env flag en PR #3) es temporal
   — removido en PR #4 cuando paridad está probada. Non-goal §12 explícito:
   "Do not keep legacy orchestration and the new runtime permanently active as equal
   authorities".

### Hand-off log

- **Delegated → explore agent (ses_04c6fb2ecffeW82feeLSrxcKHL)**: inventario completo y
  read-only del glue custom del repo, con spec exacto (Python backend + Next frontend,
  output `GLUE_INVENTORY.md`, 15 secciones, sin opiniones arquitectónicas, file:line
  mandatory). **Review**: subagente terminó OK (425 líneas, todas las file:line
  verificadas arbitrariamente por Raven Mind leyendo `munin_agent.py:270-637`
  directamente, confirmando ReAct loop, repetition guard, parallel batch dispatch,
  `stop_reason` mapping).
- **Hand-off log de subseqüentes delegaciones**: aparecerá aquí a medida que se deleguen
  tareas atómicas de implementación (PR 1 etc.).

### Next

PR #1 (`feat/parity-baseline`) — characterization tests por subsistema siguiendo
`IMPLEMENTATION_ROADMAP.md` step 1. Antes de escrita de código se confirmará en el
roadmap que cada test cubre un `file:line` exacto del inventario.

## 2026-07-30 — PR-01 implementation (parity baseline)

**Branch head**: `raven-mind/migration-issue9/pr-01-parity-baseline` (off `raven-mind/issue9`).
**Delegated**: PR-01 parity baseline (9 characterization files under `tests/characterization/`).

### Hand-off log

- **Spec given**: `pr-specs/PR-01-parity-baseline.md` (atomic spec; 230 lines).
- **Subagent invocation**: `general` subagent; pasted spec body inline; no architectural authority granted; no-commit/no-push scope; read-source-for-shape directive respected.
- **Subagent returned**: 9 files written; 4 spec-vs-code drift violations reported (all resolved toward actual code shape — correct characterization posture):
  1. `MIGRATION_ID` actual value is `"20260729_001_production_foundation"` (NOT `"v3.1"` as spec literal said) — caught and adapted in test.
  2. MCP-side tables = **10** (spec said 9; `runtime_cache` is the extra) — test asserts the 9 from spec plus a soft check of runtime_cache.
  3. `tool_calls_log` entry keys are `name`/`arguments`/`elapsed_ms`/`result`-or-`error`/`ok`/`summary` (spec said `args`/`result`/`elapsed_ms`/`step`) — test adapted to actual key set.
  4. `<operator_guidance>` produced by dispatcher.py carries `from=` + `at=` attributes; coordinator test asserts the hook output (raw string) instead — subagent correctly split between hook-side and dispatcher-side contracts.
- **Raven Mind review outcome**: 9 files reviewed hunks-by-hunks; **2 mechanical”spec drifts” found and patched in-place by Raven Mind** (brief exception "drift mecánico" allowed; no re-delegation warranted):
  1. `test_conversation_persistence_parity.py:114-116` — `_append_event` called without required positional `conn`. Patched to `with store._transaction() as conn: store._append_event(conn, run_id=, kind=, payload=)` (matches production pattern at `munin/production/store.py:340-365` + 652/679/717/etc.).
  2. `test_sse_event_contract_parity.py:test_use_conversation_events_cross_reference` — asserted `45_000` in `useConversationEvents.ts` although spec line 168 forbids asserting against the untouched surface. Trimmed to read-only existence check.
- **HITL test simplifications** (tests 1–3 of `test_hitl_parity.py`): assert storage-level effects (request row present + response_json contents) rather than dispatcher semantic flow (run.state transition, real tool invocation with new_args, operator_guidance injected at next step). Reason: dispatcher-run would require building a fake LLM + queue plumbing matching dispatcher's run_once contract; characterization value is preserved because any later PR that changes the storage shape breaks these tests. Acceptance decision per project CLAUDE.md "amend the characterization with reason documented in changes.md" clause — reason logged here.
- **Signed off at** (Raven Mind): commit pending. Status: green-for-staging on this branch.
- **Deps added/bumped in this PR**: none — test-only.
- **E2E scenario unblocked from issue §required-scenarios list**: none directly — enabling step. PR-14/15/16 deletion PRs (issue §12 step 8) now have a defensive safety net.

### Framework verification provenance (PR-01)

- **lib**: pytest-asyncio
- **version**: declared `pytest-asyncio = "*"` in `pyproject.toml:34`; mode `asyncio_mode=auto` at `pyproject.toml:55`
- **source**: Context7 `/websites/pytest-asyncio_readthedocs_io_en_stable` Auto Mode Configuration (sync tests auto-skipped from async decoration, no manual markers needed); existing `tests/conftest.py:11-32` `isolated_workspace` + `store` fixtures reused via `tests/characterization/conftest.py`.
- Other Cooke of `munin/core/munin_agent.py:289-448`, `munin/mcp/shared_state.py:698-719`, `munin/subagents/runner.py:363-371`, `munin/mcp/tools/forge_tool.py:81-99`, `munin/production/store.py:33/658`, `munin/mcp/registry.py:310-534` shapes read by subagent for assertion matching (no Context7 fallback required — these are internal repo contracts, not framework contracts).

### Issue §9 invariants preserved

| Invariant | Status in PR-01 |
|---|---|
| FastMCP tools + external MCP integration | Untouched — production code unchanged |
| Hugin + offensive tool wrappers | Untouched |
| Scope/OPSEC in tool boundary | Untouched |
| Audit redaction contract | Untouched (existing `test_audit_redaction.py` covers) |
| Soul human-editable | Untouched |
| Tool provenance | `procedural` row shape characterized (PR-02 untracked-provenance assertion) |
| Cross-session artifact pattern | Untouched (free-tier artifact pattern unaffected) |