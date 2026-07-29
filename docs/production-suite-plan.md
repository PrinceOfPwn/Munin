# Munin Production Suite — Implementation Plan

This is the implementation contract for PR #3. It turns the audit in
[`production-suite-audit.md`](production-suite-audit.md) into deliverable,
testable increments. Decisions marked **accepted** are binding for this PR;
review feedback is recorded below before runtime code changes begin.

## Guiding decisions

1. **Turso is authoritative.** Local SQLite remains only for explicit local
   development/testing and is rejected by production conversation APIs.
2. **One application contract.** GUI, MCP, and Discord adapt to the same
   authenticated services, repositories, run states, permissions, artifacts,
   and events; no channel has a private transcript.
3. **Event source plus projections.** Immutable `run_events` are the replay
   source. Message, tool, reasoning, artifact, run, and summary rows are
   queryable projections with stable ids and versions.
4. **Safe observability.** Native provider reasoning is stored only when the
   provider and user/profile setting permit it. Otherwise the model may supply
   a clearly labeled operational action summary. Secrets are redacted before
   persistence and display.
5. **No browser trust boundary.** Browser persistence is limited to UI
   preferences and resume cursors. It holds neither a shared MCP bearer token
   nor the sole copy of any operation state.

## Workstream A — schema and repositories

### Migration model

- Add `munin/production/` with a migration runner that records immutable,
  idempotent migration ids in `schema_migrations`.
- Use portable SQLite/libSQL DDL and explicit repositories rather than
  direct schema mutations scattered through tools.
- New rows carry UUIDs, UTC timestamps, `version`, actor attribution,
  `test_run_id`, and test TTL where applicable. Indexed search is server-side;
  an FTS5 projection is used only when supported by the connected Turso
  database, with a safe LIKE/token fallback.

### Tables

| Aggregate | Key columns and invariants |
| --- | --- |
| `users` | unique normalized username/email, Argon2id password hash, role, disabled state, bootstrap flags |
| `auth_sessions` | token hash, CSRF secret hash, issued/idle/absolute expiry, rotation/revocation, user agent/IP audit metadata |
| `conversations` | owner, title, summary pointer, status, tags/scope, last activity, archive/soft-delete, optimistic version |
| `conversation_participants` | conversation/user or service participant role with uniqueness and removal state |
| `messages` / `message_revisions` | sequence, author, run link, idempotency key, placeholder status, redacted content, versioned edits |
| `agent_runs` | conversation/message parentage, attempt, state, lease owner/expiry, idempotency key, model/profile, budgets, checkpoint |
| `run_events` | immutable sequence, kind, redacted payload, causation/correlation, actor, timestamp |
| `reasoning_events` | run/event link, kind/provenance/step, persistence consent, redacted content |
| `tool_calls` / `subagent_runs` | parent run/event, safe args/output, state, duration, agent/profile/scope and retry lineage |
| `human_requests` | action/risk/evidence/scope, allowed choices, answer, actor, expiry/state |
| `conversation_artifacts` | storage metadata, hash, provenance, message/run, preview/download authorization |
| `conversation_summaries` | source range/hash/ids, model/prompt/confidence/entities/findings/decisions/open tasks |
| `provider_profiles` | owner/use/provider/label/model/base URL, fingerprint, ciphertext/envelope key id, active/revoked/rotation state |
| `audit_events` | append-only actor/action/resource/outcome/redacted metadata |
| `operation_snapshots` / `operation_branches` | immutable run checkpoint, fork event, replay mode, parent branch, provenance/diff/promotion approval |

### Transactional turn API

`POST /api/conversations/:id/turns` accepts an idempotency key and produces
the user message, run, assistant placeholder, and first `run_events` row in
one transaction. A unique `(conversation_id, idempotency_key)` constraint
returns the original resources on retry. Updates use version CAS and an active
lease predicate. The only valid run transitions are `queued`, `running`,
`waiting_for_human`, `completed`, `failed`, `interrupted`, and `cancelled`.

## Workstream B — identity and authorization

- Implement bootstrap-admin, login/logout/session introspection, password
  recovery token handling, session rotation/revocation, and roles `admin`,
  `operator`, `viewer`.
- Add a server-side auth service and route guard. Cookies use `HttpOnly`,
  `Secure` in production, strict SameSite policy, CSRF double-submit/origin
  validation, inactivity plus absolute expiry, login throttling/backoff, CSP,
  and security headers.
- Enforce a policy object at every conversation/run/tool/provider/agent/branch
  action. High-impact tools require matching scope and a persisted approval.

## Workstream C — durable dispatcher and live rehydration

- Replace process-only `JobManager.records` as the source of truth with a
  Turso-backed queue/lease dispatcher. Process futures become disposable
  executors for leased work only.
- Persist provider chunks, operational summaries, tool intent, observations,
  decisions, subagent transitions, HITL, artifacts, errors, and final result
  as monotonically sequenced events.
- Expose cursor-based SSE with `Last-Event-ID` and a polling fallback. On
  reconnect the client loads the complete aggregate then requests events after
  its durable cursor, deduping by event id.
- A recovery sweep marks expired leased runs `interrupted`; it never leaves a
  permanent `running` state. Retry creates a child attempt linked to the
  original rather than rewriting history.

## Workstream D — memory, agents, and operations

- Build source-cited compaction jobs. Context selection scores recency,
  relevance, importance, scope, entities, semantic/episodic/shared intel,
  Hugin skills, and token budget. Raw events remain retained.
- Register enterprise Red Team profiles with declarative tools, scope, risk,
  budgets, timeout, completion/HITL criteria, preferred model, and fallback.
- Add durable objective plans/checkpoints/heartbeats, bounded retries/backoff,
  loop detection, pause/cancel/guidance, final report, and Shadow Council
  recommendations that never bypass authorization or HITL.

## Workstream E — governed intelligence and extensibility

- Hugin imports a versioned catalog. Strix is fetched as metadata-first source
  after license review; selection retrieves only top 1–5 relevant skills.
  External skill text is untrusted data and cannot alter scope or policy.
- Page Agent is frontend contextual assistance only, with an authenticated
  backend LLM proxy, DOM/data sanitization, allowlists, confirmations for
  sensitive actions, feature flag, audit events, and deny-by-default endpoint
  access.
- Introduce typed extension manifest/slots/permissions/schema validation,
  lazy widgets, error boundaries, isolated preview and rollback. An extension
  can propose a PR but cannot silently mutate production or auth.

## Workstream F — Intelligence Flight Deck and Raven Replay

- Replace the MCP-workbench composition with login, briefing Command Center,
  Conversations, Agents, Operations, Memory, Graph, Artifacts, HITL Inbox,
  Settings, and right inspector.
- Apply the repository raven mark and the dark editorial tokens defined by the
  mission. Use semantic HTML, keyboard navigation, visible focus, AA contrast,
  reduced-motion support, and deliberate 360/768/1024/1440/1920 compositions.
- Build Raven Replay from immutable redacted events and snapshots. Recorded
  mode never executes a tool. Forks track parent/fork event, sandbox/replay
  mode, diff, and promotion approval.

## Verification plan

1. Add failing characterization tests for lost process-local jobs, duplicate
   send, reload during run, stale lease recovery, full aggregate retrieval,
   and unauthorized cross-user access.
2. Unit-test repositories, migrations, CAS/idempotency, redaction, auth,
   profiles, policy, compaction provenance, agents, replay, and extensions.
3. Integration-test against a run-scoped Turso namespace or a strict local
   libSQL test endpoint. Every fixture carries `test_run_id`, owner, creation
   time and TTL; `finally` plus Actions `always()` delete only matching ids and
   assert zero residual rows. A janitor removes expired fixture groups.
4. E2E-test bootstrap/login, multiturm conversation, tools, subagent,
   reasoning, navigation/reload/restart, search, artifacts, compaction, HITL,
   Page Agent allow/deny, replay/branch diff, logout, mobile, accessibility,
   and cleanup isolation.
5. Require typecheck, lint, Python tests, integration tests, build,
   accessibility/responsive screenshots, workflow validation, and no tracked
   secret/artifact/database/log files.

## Independent review log

Before implementation, a no-edit `gpt-5.6-sol` review will assess this audit
and plan in the roles of Product Engineer, Distributed Systems Engineer,
Security Architect, and Design Systems Lead. Its accepted and rejected
recommendations will be appended here with rationale before implementation
commits begin.
