# Runtime architecture

This document describes the execution path behind Munin. The higher-level
system boundary is in [the architecture overview](../ARCHITECTURE.md).

## Server composition

`munin serve` hosts the HTTP API, the streamable MCP endpoint and optional
Discord adapter in one ASGI process. The server shares one authentication,
policy, capability and persistence boundary across those surfaces.

| Surface | Purpose | Authority |
| --- | --- | --- |
| `/api/*` | Web and programmatic conversations, artifacts and approvals | Authenticated operator API |
| `/api/chat/{conversation_id}/stream` | Reattach a viewer to persisted activity | Durable event replay |
| `/api/human-requests/{request_id}/resolve` | Decide one waiting action | Exact server-issued request |
| `/mcp/` | Discover and call the live MCP surface | MCP bearer authentication plus server policy |

## Execution path

1. The chat endpoint validates the actor and conversation, creates or finds an
   idempotent run, and claims a renewable lease.
2. The supervisor creates a per-run Deep Agents/LangGraph context from the
   stable conversation thread, relevant messages, evidence and active registry.
3. LangGraph streams provider deltas and middleware emits tool lifecycle
   envelopes. The production adapter persists these as Munin events.
4. The same event envelopes are streamed to the connected browser and remain
   available through the replay endpoint.
5. Completion, failure, cancellation or a human interrupt updates durable run
   state. A lease heartbeat stops any executor that loses its fenced claim.

## Threads, checkpoints and compaction

The conversation ID is the stable LangGraph `thread_id`. Checkpoints let the
runtime resume its executable state after an interruption or recoverable
process loss. They do not replace conversation events or artifacts.

Context compaction is a model-context strategy: it reduces the material sent to
the model while retaining the durable event and evidence record outside the
prompt. It enables long conversations without treating a compacted summary as
the complete audit trail.

## Live capability registry

The registry is assembled from current server-side capability state at run
time. It contains enabled native tools, registered generated `gen__*`
capabilities and bounded specialist profiles. An MCP schema discovery call is
the authoritative client view; a static screenshot or documentation table is
not.

Skills and Hugin material are a separate, passive research layer. Selective
metadata retrieval and sandboxed reading can provide provenance-labelled
context for a bounded subtask. Material retrieved from a skill never becomes a
tool call, target authorisation or evidence of target behaviour by itself.

## Run event contract

The runtime normalises events into an operator-facing timeline. Important kinds
include:

- assistant text and explicit provider reasoning;
- planning and operational activity;
- tool intent, output, completion and failure;
- specialist/subagent creation and result;
- artifacts;
- human request and decision; and
- terminal run state.

Explicit provider fields such as `reasoning_content`, `thinking` and tagged
`<think>` blocks are emitted separately from final assistant text. Munin does
not derive private reasoning from graph nodes, tool calls or server logs.

## Approval interrupts

Deep Agents HITL interrupts are translated into durable human requests. A
request is bound to its run, action, arguments, participant and expiry. An
approval resumes the saved command at the checkpoint; a rejection or expiry
does not fall through to a different action.

## Failure handling

A disconnected browser leaves the server-owned run alone. A failed executor
eventually loses its lease; the recovery worker may requeue the run and resume
it from a usable checkpoint. An unresolved human request is intentionally
excluded from automatic recovery.

Use [the persistence guide](architecture-persistence.md) for storage and
recovery tests, and [the operator guide](operator-guide.md) for practical
triage.
