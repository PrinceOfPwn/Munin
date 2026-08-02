# Runtime architecture

This document describes the current execution path behind Munin. The broader
system boundary is documented in [ARCHITECTURE.md](../ARCHITECTURE.md).

## Server composition

`munin serve` hosts the authenticated HTTP API, streamable MCP endpoint and
optional Discord adapter in one ASGI process. Every surface shares one identity,
policy, capability and persistence boundary.

| Surface | Purpose | Authority |
| --- | --- | --- |
| `/api/*` | GUI and programmatic conversations, artifacts and approvals | Authenticated server policy |
| `/api/chat/{conversation_id}/stream` | Reattach to persisted activity | Durable event replay |
| `/api/human-requests/{request_id}/resolve` | Decide one waiting action | Exact server-issued request |
| `/mcp/` | Discover and invoke live capabilities | MCP authentication plus server policy |

## Execution path

1. The server validates the actor, conversation and request idempotency.
2. It creates or resumes a run and claims a renewable fenced lease.
3. The supervisor loads the stable LangGraph thread, relevant evidence and the
   live capability registry.
4. Model, activity, tool, specialist, artifact and approval events are persisted
   before or while they are streamed to clients.
5. Completion, failure, cancellation or a human interrupt updates durable state.
6. Reconnecting clients replay the event log without repeating provider work.

## Threads, checkpoints and events

The conversation ID maps to a stable LangGraph thread. Checkpoints preserve
executable graph state. Events preserve the operator-visible and auditable
history. Context compaction only reduces model input and replaces neither one.

## Capability registry

The registry is assembled from current server-side state at run time. It may
contain native tools, Valravn capabilities, registered `gen__*` tools and
bounded specialists. Static screenshots and prompt lists are not authoritative.

Skills, Hugin material and Soul files provide context. They do not become tools,
authorization or evidence of target behavior merely by being present.

## Operation modes

Standard, YOLO, GOAL and BEAST modify autonomy budgets and which noncritical
actions pause for approval. They do not remove server policy, audit, secret
redaction, preflight checks or the critical approval floor.

## Approval interrupts

A sensitive action produces a durable request bound to the actor, conversation,
run, capability, arguments and expiry. Approval resumes that exact action.
Rejection or expiry closes it. Decisions cannot be reused for changed calls.

## Recovery

A disconnected GUI does not cancel a server-owned run. If an executor loses its
lease, a recovery worker may resume a recoverable run from the same thread and
checkpoint. A run waiting for human approval remains paused.

## Soul clarification

The bundled `soul/` prompts are a specific CTF/lab characterization, not the
recommended default for production, defensive or enterprise deployments. Soul
never expands scope or bypasses server controls.
