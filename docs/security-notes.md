# Security boundaries and operating controls

Munin helps an operator carry out authorised work with durable state and
visibility. It does not determine whether a target is authorised, whether a
finding is true, or whether an action is appropriate outside the supplied
scope.

## Controls enforced by the system

- authenticated web and MCP entry points;
- one shared policy and capability boundary for web, MCP and Discord;
- live registry checks for enabled capabilities;
- durable human requests for approval-worthy actions;
- exact-action approval resumption rather than blanket grants;
- run leases and checkpoints that separate recovery from uncontrolled retry;
- durable events for review and replay; and
- source/provenance handling for evidence and skill retrieval.

## Important limits

### Approval is not a blanket permit

An approved request authorises only the stored action in its recorded context.
It is not a reusable approval for another tool, target, operator or run.

### A generated capability is not automatically trusted

Source code, a generated graph or a skill document does not become safe merely
because it exists. Generated extensions need a narrow contract, validation,
registration and invocation-time policy. Treat any sandbox boundary as a layer
to test, not a reason to skip review.

### Event visibility is not hidden-state export

The timeline can show explicit provider reasoning when that provider emits it,
alongside tool results and activity. Munin must not reconstruct hidden model
deliberation from internal graph state, logs or tool calls.

### Remote deployment changes the threat model

Binding outside loopback, allowing broad browser origins, sharing tokens or
enabling Discord without narrow allow lists expands who can request work and
observe results. Use TLS, network controls, explicit origins, separate
identities and managed secrets.

### Hugin is knowledge, not permission

Hugin research, graph relationships and extracted skills can inform a
hypothesis. They do not establish target behaviour, authorise a scan, provide
credentials or invoke a capability. Preserve source identifiers and obtain
target-specific, authorised evidence before action.

## Operator practices

- Keep written scope, exclusions and escalation contacts with the operation.
- Use the smallest capability that can collect the required evidence.
- Review the exact tool target and arguments before approval.
- Protect model keys, MCP tokens, durable-store tokens and the master key with
  a secret manager.
- Persist hot and checkpoint storage when recovery matters.
- Review tool output and artifacts, not only the agent's summary.
- Treat provider output and passive research as inputs to verification, not
  substitute proof.

## Incident response

If you suspect a scope, credential or deployment problem:

1. cancel or allow the relevant run to stop at its next safe boundary;
2. do not approve pending high-impact requests;
3. preserve run IDs, events and artifacts for review;
4. rotate exposed secrets and restrict affected identities or origins;
5. inspect the live registry and deployment configuration; and
6. document the outcome through the normal incident process.

## Review checklist for a new capability

1. Is its purpose narrow and tied to a repeatable, authorised need?
2. Are inputs, outputs, target boundaries and failure modes explicit?
3. Is validation reproducible in an appropriate isolated environment?
4. Is its source and provenance clear, including any Hugin or external material?
5. Is it registered with an appropriate policy and visibility level?
6. Does it require HITL, and is the exact approval request understandable?
7. Does its result become a durable evidence record rather than untraceable prose?
