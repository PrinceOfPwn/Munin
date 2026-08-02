# Operator guide

This guide is for running Munin in an authorised environment. A working server
does not create permission to inspect a target.

## Start a local session

1. Copy `.env.example` to `.env` and set an LLM endpoint, strong
   `MUNIN_MASTER_KEY`, MCP bearer token and allowed local origin.
2. Start the unified service:

   ```bash
   poetry install
   poetry run munin serve --host 127.0.0.1 --port 8787
   ```

3. Start the web interface:

   ```bash
   cd app
   npm ci
   npm run dev
   ```

4. Confirm `http://127.0.0.1:8787/health`, then sign in at
   `http://localhost:3000`.

MCP clients use `http://127.0.0.1:8787/mcp/` with the configured bearer token.
Keep the trailing slash and do not expose the endpoint publicly without a
protected deployment boundary.

## Operate a conversation

1. State the objective, authorised target/scope, exclusions and expected
   evidence.
2. Select the correct conversation or create a new one for a distinct
   operation.
3. Watch the timeline for activity, tool intent, output, results, artifacts
   and approval requests.
4. Use the live capability view when you need to know what is currently
   available; do not rely on a copied list.
5. At the end, verify the result against the tool evidence and write down the
   blockers and safe next step.

## Review a human request

Before approving, verify the exact:

- target and authorised scope;
- tool or action;
- arguments and expected effect;
- evidence leading to the request;
- operator identity and expiry; and
- reason this step cannot be safely replaced by a lower-impact action.

Reject a request when any part is ambiguous. Rejection closes that proposed
action; it must not be repurposed as approval for a different action.

## Long-running sessions and recovery

The browser is a viewer, not the worker. If it disconnects, reopen the same
conversation and let the replay stream restore the timeline. Do not resend the
same message to “unstick” an active run.

Running leases and checkpoints make a process loss recoverable when persistent
storage is configured. A run that is `waiting_for_human` stays paused after a
restart; it needs a fresh authorised decision. Inspect server logs and
checkpoint/storage health before assuming a failed run can resume.

## Discord

Discord is optional and uses the same server-side run and policy path as the
web UI. Configure `MUNIN_DISCORD_BOT_TOKEN` plus explicit channel and user
allow lists before enabling it. Use it for remote continuity; use the web UI
when you need the full timeline, artifacts or detailed approval review.

## Production checklist

- Bind to loopback behind a protected reverse proxy, or enforce TLS, origin and
  network policy directly.
- Store LLM credentials, master key, MCP token and durable-store token in a
  secret manager.
- Persist the hot SQLite and LangGraph checkpoint paths.
- Limit MCP clients and Discord users/channels to known identities.
- Test provider tool calls, a replay after reconnect, an approval pause and a
  checkpoint recovery before an operational engagement.
- Keep written authorisation and escalation contacts with the operation.

## Troubleshooting

| Symptom | First check |
| --- | --- |
| MCP client gets a path error | Use `/mcp/` and confirm bearer token. |
| Timeline appears to stop after refresh | Reopen the same conversation and inspect replay; do not duplicate the request. |
| A run stays `running` after a crash | Check lease/recovery interval, hot store and checkpoint availability. |
| A run waits forever | Inspect the pending human request; recovery will not execute it. |
| A capability is missing | Inspect its live registry state; source files alone do not register it. |
| A Hugin skill looks relevant | Treat it as provenance-linked research; validate against the target and policy before action. |

## Safe shutdown

Allow active terminal events to persist before stopping the service. If a
restart is necessary, leave the hot and checkpoint databases intact and reopen
the conversation afterward. Do not delete local state as a substitute for
cancelling or resolving active work.
