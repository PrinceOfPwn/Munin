# Engineering hand-off

This is a concise hand-off for the current Munin runtime. It intentionally
describes active contracts rather than preserving superseded implementation
details. See [ARCHITECTURE.md](ARCHITECTURE.md) and the guides in `docs/` for
the complete operating model.

## Runtime and durability

- The production chat path is a direct, durable Deep Agents/LangGraph
  supervisor. A conversation owns a stable `thread_id`; a run owns a renewable
  fenced lease.
- Events are persisted as the canonical timeline and delivered through the
  chat stream. Reattachment is idempotent and replays existing work rather than
  submitting the same operator turn again.
- A recovery loop can requeue an expired running lease and resume an eligible
  graph checkpoint. It never auto-runs an unresolved `waiting_for_human`
  request.
- Context compaction is used for model context management; checkpoints and
  durable events retain their separate roles.

## Human approval

- Native Deep Agents HITL interrupts become server-owned human requests.
- A request is tied to its exact action, arguments, actor and expiry. Approval
  resumes that checkpoint; rejection and expiry do not become another action.
- Web and Discord surface the same request but do not create alternative policy
  paths.

## Timeline and frontend contract

- The UI uses AI SDK message parts for text, explicit provider reasoning,
  tool state/output, subagent activity, artifacts and human requests.
- `reasoning_content`, `thinking`, typed reasoning blocks and explicit
  `<think>` output are separated from final assistant text when emitted by the
  provider. No reasoning is inferred from internal runtime activity.
- Tool output and the original operator message are restored with the durable
  conversation timeline after reconnect.
- The stream bridge drains asynchronous command output before closing, flushes
  an unterminated final SSE frame, and includes terminal content so the last
  words cannot disappear at the UI boundary.
- Stop is a viewer disconnect: a subsequent turn is forwarded as guidance to
  the active durable run and reattaches its replay stream instead of returning
  a dead-end 409. Tool results resolve by stable call id during replay.
- Conversation titles can be renamed, exports can be downloaded, and image
  artifacts have an inline preview through the authenticated artifact route.
- Provider profiles are managed by the authenticated backend. Changing a
  compatible profile affects later turns while keeping the same conversation
  and durable history.

## Capability and research contract

- The capability registry is live: native tools, enabled generated `gen__*`
  tools and specialist profiles are discovered at run time.
- Generated extensions need a narrow contract, validation, registration and
  normal invocation policy. A file on disk is not a registered tool.
- Bundled Deep Agents skills are mounted only when `SKILL.md` frontmatter
  `name` exactly matches its package directory; malformed packages stay out of
  the agent's read-only filesystem.
- Hugin and skills provide passive, provenance-linked research context. Use
  metadata selection and controlled reading for a bounded subtask; do not
  automatically load the corpus or treat it as authority to execute.

## Deployment and CI

- `munin serve` exposes the production API and MCP surface in one process.
  The canonical streamable MCP path is `/mcp/`.
- CI checks the backend, frontend type/build contract and relevant integration
  paths. A real-provider smoke is a controlled canary, not the only proof of
  correctness.
- Persistent production deployments require durable hot and checkpoint paths;
  a libSQL/Turso archive can provide long-lived mirrored records when enabled.

## Validation expectations

Run the checks appropriate to the modified area:

```bash
poetry run pytest
cd app && npm run build
```

For a full operational acceptance test, also verify an authenticated MCP
discovery call, a scoped tool round trip, event replay, an approval pause and
checkpoint recovery using isolated fixtures.
