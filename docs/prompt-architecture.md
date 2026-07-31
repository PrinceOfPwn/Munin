# Munin Prompt Architecture

Munin uses one behavioral contract across GLM, MiMo, Qwen, DeepSeek, Kimi, Yi,
and generic OpenAI-compatible models. The design separates three languages:

| Surface | Language |
|---|---|
| Internal task decomposition and agent handoff | Simplified Chinese |
| Tool names, parameters, JSON keys, code, files, schemas, queries | English |
| Final operator response | Explicit preference or latest operator language |

This is not a request to expose chain-of-thought. Internal model reasoning stays
private. Munin surfaces short decision summaries and a complete tool/evidence
trace.

## Composition order

The coordinator receives one system message composed in this order:

1. `soul/*.md`: identity, hard principles, goals, and live capability map.
2. Durable memory summary.
3. Shared Chinese-first runtime contract from `munin/core/prompting.py`.
4. Final tool-selection and safety rules.

Every native or forged subagent inherits the same subagent language contract in
`ReActSubagentBase`, so a generated graph cannot accidentally revert the fleet
to verbose English coordination.

## Persona: APT campaign operator

Munin adopts the operating posture of an APT campaign:

- objective and scope before action;
- recall and intelligence before re-enumeration;
- one hypothesis per minimum viable action;
- low-noise, reversible validation first;
- facts separated from inference and unknowns;
- durable evidence and explicit handoffs;
- stop when the objective is complete or blocked.

The persona is persistent and operational rather than cosmetic: it influences
planning horizon, noise management, evidence correlation, delegation,
capability reuse, and campaign continuity.

## Few-shot trajectories

The coordinator prompt includes observable action-chain examples for:

1. answering from memory without rescanning;
2. using Hugin before a non-trivial minimum verification;
3. forging an exact missing tool and using it in the same run;
4. delegating a cross-domain task while retaining command through traces.

The examples show tool order and evidence gates, not hidden reasoning. They are
deliberately domain-diverse so LDAP does not dominate Munin's identity.

## Hugin protocol

Hugin is Munin's knowledge sibling:

- `hugin_rag_search`: retrieve ranked evidence;
- `hugin_plan_for`: rank candidate steps for a goal;
- `hugin_neighbors`: expand graph relationships;
- `hugin_node_detail`: inspect a node and source;
- `hugin_refresh`: refresh once when cache is unavailable/stale.

Queries use concise English security terminology because the graph's canonical
entities are commonly English. Results are analyzed internally in Chinese and
delivered in the operator's language.

Hugin output is untrusted external evidence. It cannot change scope, grant
permission, or force a tool call. Relevant node ids and source URLs should
remain in the evidence trail.

## Tool Forge contract

`tool_forge` receives a precise English capability specification containing:

- exact purpose;
- typed inputs and defaults;
- output schema;
- edge cases and failure modes;
- allowed imports;
- success criteria.

Generated Python, identifiers, docstrings, comments, and errors are English.
The prompt rejects placeholders, hidden scope reduction, unsafe imports, raw
secret output, and keyword-only deduplication. A forge is complete only after
the new `gen__*` tool is registered, invoked, validated, and persisted.

## Graph Forge contract

`graph_forge` produces:

- English `name`, `purpose`, JSON keys, and tool names;
- a Simplified Chinese specialist system prompt;
- an effective whitelist derived from the requested tools and any additional
  already-registered capabilities required by the declared purpose;
- explicit evidence rules, human checkpoints, parent handoff, and termination.

Hugin, LDAP, and active-tool rules are included only when the corresponding
tools exist in the whitelist.

## Operator language

`MUNIN_OPERATOR_LANGUAGE=auto` follows the latest operator message. A fixed
value such as `es`, `en`, or `pt-BR` pins deployment output. Headings are
localized semantically rather than forcing English labels.

## Regression expectations

Prompt tests assert that:

- all supported model families are detected;
- coordinator and subagents receive the Chinese language contract;
- code/artifact English rules remain explicit;
- operator-language delivery is explicit;
- Hugin routing and bounded refresh are present;
- self-extension few-shots require same-run use;
- forged graphs inherit the fleet contract.
