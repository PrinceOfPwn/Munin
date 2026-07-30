# PR-15 — Remove legacy store_v3_1 monkey-patch overlay (real v3.2 migration)

- **Head**: `raven-mind/migration-issue9/pr-15-remove-legacy-store-v3-1-overlay`
- **Base**: `raven-mind/migration-issue9/pr-14-remove-legacy-orchestration`
- **Open architectural questions**: None. Issue §9 + ARCHITECTURE.md "real regression" historical: v3.1's monkey-patch via `types.MethodType` exists because direct migration was abandoned mid-stream. PR-15 absorbs the v3.1 tables into a proper forward-only v3.5 migration.

---

## Goal

Eliminate `munin/production/store_v3_1.py::install_v3_1_extensions` monkey-patch pattern (`types.MethodType`) by promoting the v3.1 extension tables to base schema in a proper forward-only checksum-guarded migration, with bumped ProductionStore `MIGRATION_ID` to `v3.5` (v3.2 + v3.3 + v3.4 already merged via PRs 6/8/10; PR-15 absorbs the v3.1 install_v3_1_extensions pattern as v3 final v3.5). Issue acceptance #16 ("Documentation and architecture diagrams are updated").

## Acceptance title (one line)

`store_v3_1.py::install_v3_1_extensions` no longer monkey-patches; v3.1's tables (`conversation_collaborators`, `conversation_notes`, `conversation_presence`, `run_guidance_queue`) created via PROPER migration; UUID helper regression test preserved; migration forward-only; checksum recomputed; `store_v3_1.py` file removed.

## Issue required end-to-end scenarios this PR partially unlocks

NONE new — final cleanup of legacy persistence pattern. Migration's prior E2E already proven.

---

## Files deleted

| Path | Why |
|---|---|
| `munin/production/store_v3_1.py` | The v3.1 install extensions file is obsolete post v3.5 migration. |

## Files modified

| Path | What changes |
|---|---|
| `munin/production/store.py` | Forward-only migration v3.5 absorbs all 4 v3.1 extension tables as baseline schema:
 - `conversation_collaborators`, `conversation_notes`, `conversation_presence`, `run_guidance_queue`
 - columns `tool_calls.parallel_group_id`, `tool_calls.tool_use_id`, `human_requests.requested_by_actor_id`, `timeline_messages.actor_id`
 - methods `upsert_collaborator`, `list_collaborators`, `add_note`, `list_notes`, `drain_guidance_queue`, `upsert_presence`, `list_presence`
 `_MIGRATION_SQL` rewritten to include all schema in ONE migration file; `_EXPECTED_SHA256` recomputed; `MIGRATION_ID` → `"v3.5"` (sequence continues from v3.4 used by PR-10 workflow registry). Add migration guard: a fresh `_v3_5_setup()` procedure creates the tables IF NOT EXISTS (defensive for forward-only via plausible partial states); a `_migrate_v3_4_to_v3_5` shunt absorbs prior v3.1 install_v3_1_extensions installs (no-op IF tables already exist; CREATE IF NOT EXISTS). |
| `munin/production/dispatcher.py` | Where `install_v3_1_extensions(store)` is invoked at dispatcher startup (line varies — confirmed during discovery), no longer necessary — direct instantiation of `ProductionStore(path)` triggers migrator which creates the tables. |
| `munin/production/asgi.py` | Same: remove the `install_v3_1_extensions` call. |
| `tests/characterization/test_conversation_persistence_parity.py` | The PR-01 assertion "v3.1 extension install via `install_v3_1_extensions(store)`" gets updated:
 - asserted parity test should still pass on the new migration by replacing the install call with `ProductionStore(path)` instantiation (no extension method needed; the tables are created automatically).
 - UUID helper regression test kept intact — confirmed still relevant via asserting a fresh insert with auto-generated UUID and rerunning PR-01 assertion 4 (`tool_calls.parallel_group_id` + `tool_use_id`)." |
| `tests/characterization/test_store_v3_5_migration.py` (NEW) | Fresh deploy on an empty DB → all v3.5 tables + columns present; v3.0 fixture test → migration runs forward → v3.5 tables created; UUID helper usable; v3.1 monkey-patch file import fails ("`store_v3_1` not found"). |

## Files added

| Path | What |
|---|---|
| `tests/characterization/test_store_v3_5_migration.py` | Per Files Modified table assertions. |

## Per-function behavior changes

### `ProductionStore` startup sequence (simplified)

Before PR-15:
```python
store = ProductionStore(path)
install_v3_1_extensions(store)  # monkey-patches new methods + creates tables
```

After PR-15:
```python
store = ProductionStore(path)  # migrator runs v3.5 (includes v3.1 schema by default)
# methods on the class instance directly: store.upsert_collaborator(), store.add_note(), etc.
```

### v3.5 MIGRATION_SQL sketch

```sql
-- Forward-only v3.5 — absorbs v3.1 install_v3_1_extensions schema as base.

CREATE TABLE IF NOT EXISTS conversation_collaborators (
  id INTEGER PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'viewer',
  ...
);
CREATE TABLE IF NOT EXISTS conversation_notes (...);
CREATE TABLE IF NOT EXISTS conversation_presence (...);
CREATE TABLE IF NOT EXISTS run_guidance_queue (...);

-- columns:
ALTER TABLE tool_calls ADD COLUMN parallel_group_id TEXT;
ALTER TABLE tool_calls ADD COLUMN tool_use_id TEXT;
ALTER TABLE human_requests ADD COLUMN requested_by_actor_id TEXT;
ALTER TABLE timeline_messages ADD COLUMN actor_id TEXT;
-- ALTER TABLE ... uses IF NOT EXISTS via PRAGMA user_version guarded check
```

Methods `upsert_collaborator`, `list_collaborators`, `add_note`, `list_notes`, `drain_guidance_queue`, `upsert_presence`, `list_presence` move from `install_v3_1_extensions` direct methods on `ProductionStore` class.

## Parity bar (PR-01 preserved)

`test_conversation_persistence_parity.py` assertions:
1. forward-only checksum: still passes (checks user_version + writes_read_back)
2. `RUN_STATES` enum: unchanged
3. v3.1 extension install: ASSERTS via `ProductionStore.migrator._ensure_tables()` (instead of explicit `install_v3_1_extensions`)
4. UUID helper regression: intact (asserts UUID shape from auto-generated column)
5. timeline/reasoning/tool_calls persistence: intact

## Deps bumped / added

None.

## Rollback plan

Revert restores `store_v3_1.py` + dispatcher install call + asgi.py install call + asserts old `install_v3_1_extensions` works; removes v3.5 migration + test; reverts `_MIGRATION_SQL` + `_EXPECTED_SHA256` to v3.4 state. Standalone revert; PR-16 base is unaffected since PR-16 doesn't depend on the version-level schema.

## Validation plan

1. Characterization tests: all PR-01..PR-14 tests green with updated assertions for v3.5 migration; 1 new PR-15 test green.
2. CI green.
3. Live-session workflow: in the workflow's setup steps, start supervisor — verify v3.5 migrator runs cleanly on the artifact-restored `data/shared_state.sqlite` file from previous run; if it had a v3.1 partial install, the v3.5 stops short an unnecessary reinstall.
4. Artifact inspection: `data/shared_state.sqlite` PRAGMA user_version == 5 (matches v3.5); `conversation_collaborators` + `conversation_notes` + `conversation_presence` + `run_guidance_queue` tables exist.
5. Parity manual check: `git grep store_v3_1` empty after merge; `git grep install_v3_1_extensions` empty.

## Issue §9 invariants preserved

All preserved. The v3.1 schema (collaborators/notes/presence/guidance_queue) lives per issue §9 "Auditability" + "Shared intel and task semantics". Migration absorbs it as baseline, no slots changed in process — file:line authoritative signature columns retained.

## Framework verification provenance

PR-15 is pure legacy-data-migration cleanup; no new framework contracts. ARCHITECTURE.md "real regression" section documents why v3.1 install_v3_1_extensions existed; the absorption into v3.5 follows the documented forward-only checksum pattern of the existing ProductionStore migrator (no external contract learning).

Uncertainty remaining: zero.