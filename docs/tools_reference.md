# Capability and skill reference

Munin exposes a live capability surface. This document explains how to reason
about it; the server or MCP discovery response is the authoritative list for a
particular deployment.

## Discover the current surface

Use the web capability view or authenticated MCP discovery to inspect enabled
tools, schemas and specialist profiles. A tool may exist in source control yet
be disabled, unregistered, unavailable in the current deployment or prohibited
for the current operation.

## Capability groups

| Group | Typical role | Operator question |
| --- | --- | --- |
| Diagnostics and administration | Service health, environment checks and controlled local work | Is this required and within the host boundary? |
| Passive knowledge | CVE/package research, documentation, Hugin retrieval and evidence lookup | Is the source current and traceable? |
| Directory services | Authorised LDAP/identity review | Are credentials and directory scope approved? |
| Active assessment | Scoped discovery and validation tools | Is the exact target and impact approved? |
| Evidence and coordination | Artifacts, memory, delegation and reporting | Does the result preserve provenance? |
| Generated capabilities | Registered `gen__*` tools and subgraphs | Has this extension been validated and enabled? |

## Call lifecycle

The timeline records the lifecycle rather than reducing it to a single chat
message:

1. **Intent** — what tool is proposed or started.
2. **Output** — streamed command or tool output when available.
3. **Result or failure** — the terminal returned evidence or error.
4. **Interpretation** — the agent's conclusion, which remains distinct from
   the raw result.

Always inspect the result before treating an assistant statement as established
fact. A tool intent without terminal result is not a completed call.

## Generated capabilities and subgraphs

A generated extension is governed like any other capability. The safe path is
to define a narrow contract, validate it in its intended sandbox, register it
with explicit metadata and check policy at each invocation. The `gen__*`
namespace makes generated origin visible; it does not make an extension safe or
automatically authorised.

## Skills and Hugin research

Skills are instruction and research documents, not server-side tool schemas.
The Hugin-derived material has source identifiers and graph cross-references so
an agent or operator can trace a technique back to its evidence context.

Use this retrieval model:

1. search metadata for the bounded task;
2. select a small relevant subset;
3. inspect the content through the controlled reader;
4. retain its source/provenance with notes; and
5. validate the resulting hypothesis with authorised target-specific evidence.

Do not inject an entire skill tree into a run, treat a skill as proof of a
vulnerability, or invoke actions from a skill without the normal capability and
approval path.

## Support and diagnosis

When a capability behaves unexpectedly, record the run ID, tool name,
arguments, timeline events and any artifact reference. Then check live registry
state, deployment configuration and server logs. This makes a missing tool,
policy rejection and execution failure distinguishable.
