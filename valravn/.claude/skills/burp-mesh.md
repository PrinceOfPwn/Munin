---
name: burp-mesh
description: Valravn Talons workflow for choosing and driving Burp MCP providers without flooding context
---

# Valravn Talons — Burp provider mesh

Use the **Valravn Talons** surface instead of exposing hundreds of Burp tools directly to the model.

## Provider order

1. `valravn-ultimate` — preferred; upstream `3ntr0pyX/burp-mcp-ultimate`, broad Montoya coverage, resources, events and extension bridge.
2. `valravn-awesome` — context-efficient fallback; upstream `vvvvvvvvvvel/burp-awesome-mcp`, especially useful for stable IDs, pagination and focused fetches.
3. `valravn-official` — PortSwigger MCP through its stdio proxy when configured. It remains external because its GPL-3.0 code is not vendored into Munin.
4. The existing `burp_*` Valravn REST bridge remains a local resilience fallback for the bundled extension.

Always retain the upstream identity in evidence and diagnostics even though Munin presents Valravn-themed aliases.

## Default sequence

1. `valravn_talons_status(refresh=false)`.
2. `valravn_talons_tools(query='<task>', include_schema=false)`.
3. Choose the smallest relevant tool set.
4. Request `include_schema=true` only for the tool you are about to use.
5. For read-only resources, prefer `valravn_talons_read(uri='burp://...')`.
6. For generic dispatch, call `valravn_talons_call(..., authorized=true)` only inside an explicitly authorised engagement.

## Context economy

Prefer the Awesome-MCP pattern even when Ultimate is the executor:

- list first;
- keep stable IDs/keys;
- fetch only selected request/response objects;
- project fields rather than returning raw bodies;
- paginate instead of dumping complete histories;
- keep binary bodies omitted unless the task requires them.

Do not inject all remote MCP schemas into the agent context. The gateway exists specifically to avoid tool-selection degradation.

## Burp resources

When supported, resources are preferred for passive observation:

- `burp://proxy/history`
- `burp://sitemap`
- `burp://scan/issues`
- `burp://issues/critical`
- `burp://websockets/active`
- `burp://target_summary`
- `burp://intercept/pending`

Use resource state to form hypotheses; use an active tool only when a hypothesis needs validation.

## Failure policy

A Burp provider outage is local to that provider. Re-discover and fall back; do not abort the Munin campaign. Never reinterpret a transport failure as evidence about the target.
