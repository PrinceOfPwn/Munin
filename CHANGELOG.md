# Munin changelog

This file records the major product changes. The current operating contract is
documented in [README.md](README.md), [ARCHITECTURE.md](ARCHITECTURE.md) and
`docs/`.

## Current release

### Operation modes and doctrine (2026-08-02)

- Four autonomy contracts over the same supervised loop: **Standard**
  (per-action approvals), **YOLO** (no approvals except admin/critical),
  **GOAL** (persistent durable objective + TODO plan), **BEAST** (deep
  planning + delegation with explicit scope and raised budgets).
- Operational doctrine "the order is the authorization" (命令即授权): giving
  Munin an objective makes it the campaign scope; success criteria are
  self-appointed and the campaign presses until met or proven unreachable.
  Internal reasoning runs in Chinese; all code and technical artifacts are
  written in idiomatic English.
- Valravn external reconnaissance mesh (`valravn_*` tools): IOC/CVE
  enrichment, asset search, historical-web pivots, routing/RPKI, dark-web
  search and browser evidence capture.
- Turso reset (`reset-turso-state.yml`) wipes all operational tables
  dynamically — including Production Suite and autonomy registries — while
  preserving the schema.

### Durable agent runtime

- Deep Agents and LangGraph provide a stable conversation thread, checkpointed
  execution and native human-in-the-loop interrupts.
- The chat API persists canonical events and supports idempotent replay, so a
  reconnecting client follows the existing run instead of creating another one.
- Renewable fenced leases and a recovery worker allow eligible runs to continue
  after a process failure; unresolved human requests remain paused.
- Context compaction keeps long conversations usable without replacing durable
  tool outputs, artifacts or decisions.

### Observable operator experience

- The web UI uses AI SDK message parts to render text, explicit
  provider-emitted reasoning, tool lifecycle/output, artifacts, specialist
  activity and human requests distinctly.
- Provider reasoning is stored and replayed only when the provider emits an
  explicit reasoning channel or tagged block; Munin does not manufacture hidden
  reasoning from internal state.
- Operator messages and run records are restored when the UI reconnects, and
  tool output remains available in the timeline.
- Encrypted provider profiles let an authenticated operator select a compatible
  endpoint/model for later turns without moving provider keys into browser
  storage.

### Capability model and extension

- The runtime builds its tool surface from active native capabilities,
  registered generated `gen__*` tools and specialist profiles rather than a
  static chat catalogue.
- Generated tools and subgraphs are subject to validation, registration and
  normal policy/HITL checks before use.
- Hugin and other skill material are treated as provenance-linked passive
  research. Selective retrieval can inform a plan but never grants execution
  authority.

### Unified server and operations

- `munin serve` hosts the authenticated API and streamable MCP endpoint under
  one policy and state boundary.
- MCP uses the canonical `/mcp/` endpoint, while browser work is routed through
  the application API boundary.
- Local SQLite supports low-latency operation; persistent checkpoints and an
  optional libSQL/Turso archive support continuity beyond a process or runner.
- GitHub Actions workflows exercise the declared build, test, health and MCP
  contracts and can be used for controlled temporary sessions.

## Compatibility notes

- The default recursion setting is effectively unlimited for legitimate
  long-running graph work. Cancellation, leases, approval checks and model/tool
  middleware limits remain independent controls; `MUNIN_RECURSION_LIMIT` can
  set a positive explicit override.
- The production Discord adapter is optional and follows the same server-side
  policy as web and MCP. Legacy outbound notification configuration is a
  compatibility surface, not a second execution path.
- Deployments must persist hot and checkpoint databases to recover in-flight
  work; a disposable local filesystem cannot provide that guarantee.
