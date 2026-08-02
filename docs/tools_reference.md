# Capability and skill reference

Munin exposes a live capability surface. Runtime discovery through MCP and the
server registry is authoritative; documentation describes capability families,
not a guaranteed static list.

## Capability families

| Family | Purpose |
| --- | --- |
| Conversation and production | Durable conversations, runs, events, approvals and artifacts |
| Reconnaissance | Network, web and service observation under OPSEC controls |
| Intelligence | CVE, package, source and campaign enrichment |
| Valravn | IOC, organization, asset, historical-web, network and browser evidence |
| Hugin | Passive source-linked knowledge retrieval and planning context |
| Memory and coordination | Semantic memory, shared intelligence, tasks and messages |
| Autonomy kernel | Create, inspect and invoke tools, agents and workflows |
| Generated capabilities | Reviewed `gen__*` tools and bounded specialist graphs |
| Soul | List, read and propose human-reviewed profile changes |
| Diagnostics | Health, registry discovery and operational status |

## Authority model

Discovery is not permission. Every invocation remains subject to actor identity,
authorized scope, server policy, approval class and run state. Skills, Soul
files and Hugin material provide context; they do not automatically gain tool
access or become executable authority.

## Generated capabilities

A generated capability requires a narrow contract, validation, visible
provenance, registration and the same policy checks as a native capability.
The `gen__*` namespace makes its origin explicit. Generation alone does not
establish correctness; the capability must be invoked and its output validated.

## Specialists

Specialists are bounded by an explicit task, capability allowlist, evidence
contract and stop condition. Delegation does not create new scope. Their
activity and results remain visible in the durable event timeline.

## Soul tools

`soul_list` and `soul_read` inspect profiles. `soul_propose_edit` creates a
reviewable proposal. The bundled Soul is specialized for CTF/lab scenarios and
is not the recommended default for production or defensive use.
