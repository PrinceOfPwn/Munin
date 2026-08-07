---
name: bounty-orchestrator
description: Hypothesis-driven bug bounty loop combining Valravn CTI, Talons, Arsenal and durable evidence
---

# Valravn Bounty Orchestrator

Use this skill only for assets covered by an explicit bug-bounty or testing scope. The objective is not to run every scanner; it is to convert passive evidence into ranked hypotheses and use the smallest active test that can confirm or reject each hypothesis.

## Campaign loop

1. **Scope** — persist in-scope hosts, exclusions, prohibited actions, rate constraints and program-specific rules.
2. **Passive map** — use Valravn CTI/history first: certificates, archives, external exposure, technology and historical URLs.
3. **Surface graph** — connect hosts, APIs, JavaScript, auth boundaries, historical versions and shared infrastructure.
4. **Hypotheses** — create explicit candidate findings with evidence, confidence and a falsifiable next test.
5. **Select executor** — Burp traffic/state goes through Talons; external scanners and specialist tools go through Arsenal.
6. **Controlled validation** — run one bounded test, record the differential, then update/reject the hypothesis.
7. **Chain** — look for relations between individually weak findings only when evidence supports the relation.
8. **Report** — preserve requests/responses, timestamps, source attribution, reproduction steps and impact evidence.

## Tool economy

Prefer this progression:

`passive evidence -> compact list -> focused schema -> one tool call -> evidence -> next hypothesis`

Avoid:

`load 500 schemas -> run every scanner -> summarize noise`

## Useful pivots

- historical endpoint vs current endpoint;
- mobile API vs web API;
- staging/legacy vs production authorization;
- two authenticated roles against the same object operation;
- JS-discovered endpoint absent from the visible UI;
- old Swagger/OpenAPI definition vs current traffic;
- inconsistent validation across equivalent transports or API versions.

## State labels

Each hypothesis should be one of:

- `unexplored`
- `testing`
- `rejected`
- `needs_evidence`
- `confirmed`
- `duplicate_or_known`

Never promote a scanner alert directly to `confirmed`. Confirmation requires evidence tied to the target behavior.
