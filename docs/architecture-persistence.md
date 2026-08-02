# Persistence and recovery

Munin uses separate mechanisms for transactional state, executable graph state
and long-lived archives. They solve different problems and should be operated
together.

| Store | Responsibility |
| --- | --- |
| Hot application store | Accounts, conversations, runs, events, calls, artifacts and approvals |
| LangGraph checkpoint store | Executable state for a stable conversation thread |
| Optional durable archive | Mirrored long-lived records through libSQL/Turso |
| Capability metadata | Rehydrate generated capabilities and specialist definitions |

## Write and replay path

1. A message creates or reuses an idempotent run.
2. The server claims a fenced lease and records run state.
3. Runtime events are appended as activity occurs.
4. LangGraph checkpoints executable state at graph boundaries.
5. Reconnecting clients replay events; they do not invoke the model again.

## Recovery rules

- Expired leases fence lost executors.
- Recoverable runs reuse the same conversation thread.
- Completed calls and terminal events must not be duplicated.
- `waiting_for_human` runs remain paused until an authenticated decision.
- Missing hot storage degrades replay; missing checkpoints degrade execution
  recovery; missing archives degrade long-term continuity.

## Deployment

Persist both hot and checkpoint databases on durable storage. GitHub Actions
runners are ephemeral, so continuity requires artifacts or approved remote
storage. Keep provider tokens in repository or environment secrets, never in
the database, browser storage or committed files.

## Validation

Test replay after reconnect, process loss after checkpoint creation, lease
expiry, recovery without duplicate results and a pending approval that remains
paused across restart.
