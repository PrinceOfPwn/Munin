# Persistence and recovery

Munin persists three different kinds of continuity. They solve different
problems and should be operated together in production.

| Store | Responsibility | Why it matters |
| --- | --- | --- |
| Hot application store | Accounts, conversations, runs, messages, events, tool calls, artifacts and human requests | Fast transactional state while the server is running |
| LangGraph checkpoint store | Executable state for a conversation's stable thread | Resume the agent without recreating its path |
| Durable archive | Mirrored long-lived records through libSQL/Turso when configured | Continuity beyond a local or ephemeral runner |
| Capability registry | Generated capability and graph metadata | Rebuild the live catalogue after restart |

## Write path

1. A message creates or reuses an idempotent run.
2. The server claims a fenced lease and records run state.
3. Runtime envelopes append assistant, activity, tool, artifact and approval
   events. A graph step creates a checkpoint when LangGraph reaches one.
4. Terminal state and durable synchronisation are written after completion,
   failure or cancellation.

The event log is append-oriented. It is the source for replay; no reconnect
needs a second provider invocation to reconstruct the timeline.

## Replay and recovery

`GET /api/chat/{conversation_id}/stream` reads the conversation's canonical
event log and emits the same timeline parts a live browser receives. It can
follow an active run as new events arrive.

When a process disappears, a running lease is allowed to expire. The recovery
worker fences that executor, queues the candidate and starts it using the same
conversation thread. A usable checkpoint resumes graph execution; without one,
the system only takes the safe recovery path available for the recorded state.

Pending human approval is never resumed automatically. The operator must
review the persisted exact request and send a new authorised decision.

## Checkpoints are not compaction

Context compaction keeps model input small. A checkpoint retains executable
graph state. Durable events retain the operator-visible story. Do not rely on a
summary to replace original tool output, artifacts or human decisions.

## Operational configuration

Typical persistent paths are:

```dotenv
MUNIN_HOT_DB_PATH=/var/lib/munin/hot.sqlite
MUNIN_CHECKPOINT_DB=/var/lib/munin/checkpoints.sqlite
```

Use a persistent volume for both paths. Configure the durable archive according
to `.env.example` when a libSQL/Turso mirror is required. Secrets and access
tokens belong in the platform's secret manager, not in the repository or browser
storage.

## Recovery test

Before relying on a deployment:

1. Start a run and verify that it has a conversation thread and checkpoint.
2. Confirm tool events and any streamed output appear in the timeline.
3. Reconnect the browser and confirm replay restores those records once.
4. Restart the server after the lease is eligible for recovery.
5. Confirm the run continues from its checkpoint without duplicating the
   completed result.
6. Repeat with a `waiting_for_human` run and verify it stays paused.

If any store is unavailable, treat recovery guarantees as degraded and inspect
the corresponding service before approving more work.
