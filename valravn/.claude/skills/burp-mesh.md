---
name: burp-mesh
description: Valravn Talons workflow for driving Burp MCP Ultimate without flooding model context
---

# Valravn Talons — Burp execution mesh

Valravn keeps the reasoning, workflow, evidence, and knowledge layer. Burp execution is delegated to mature upstream MCP providers instead of maintaining a second Burp control plane.

## Provider order

1. `valravn-ultimate` — **primary**. Upstream: `3ntr0pyX/burp-mcp-ultimate`. Streamable HTTP MCP at `http://127.0.0.1:9444/mcp` by default. It exposes broad Montoya coverage, resources, events, reflection, and extension bridging.
2. `valravn-awesome` — optional context-efficient fallback when explicitly available.
3. `valravn-official` — optional PortSwigger MCP through its packaged stdio proxy when configured.

The retired Valravn REST extension on `127.0.0.1:8111` is **not a fallback**. Do not call `/api/*` endpoints and do not look for `burp_status`, `burp_invoke`, or other removed wrapper tools.

Always retain the upstream provider identity in evidence and diagnostics even though Munin exposes Valravn-themed aliases.

## Default sequence

1. `valravn_talons_status(refresh=false)`.
2. `valravn_talons_tools(provider="valravn-ultimate", query="<task>", include_schema=false)`.
3. Select the smallest useful tool set.
4. Request `include_schema=true` only for the tool you intend to call.
5. Prefer `valravn_talons_read(uri="burp://...")` for passive state.
6. Use `valravn_talons_call(..., authorized=true)` only inside an explicitly authorized engagement.

## Why Talons stays

Talons is intentionally small. It provides:

- lazy tool discovery instead of injecting 150+ schemas into every prompt;
- a stable authorization boundary in Munin;
- provider identity and diagnostics;
- MCP resource reads;
- provider fallback without copying upstream implementations;
- a single place to keep token/context economy rules.

It is a router, not another Burp implementation.

## Context economy

- list before calling;
- filter discovery by task (`query="history"`, `query="scanner"`, `query="jwt"`);
- request schemas only for selected tools;
- prefer stable handles/resources over copying full bodies;
- paginate histories when the provider supports it;
- omit binary bodies unless the task needs them;
- never expose the entire remote catalog to the model at once.

## Useful Ultimate resources

Prefer these for observation when available:

- `burp://proxy/history`
- `burp://sitemap`
- `burp://scan/issues`
- `burp://issues/critical`
- `burp://websockets/active`
- `burp://target_summary`
- `burp://handles`
- `burp://intercept/pending`

Use resource state to form hypotheses. Use an active tool only when a hypothesis needs validation.

## Common Ultimate tools

Discover schemas at runtime rather than assuming arguments, but these names are stable entry points in the pinned provider used by CI:

- HTTP: `http_send_raw`, `http_send_with_session_handling`, `http_send_batch`, `http_send_race`, `http_url_to_request`.
- Proxy / state: proxy history tools plus `burp://proxy/history`.
- Intercept: `intercept_status`, `intercept_set_mode`, `intercept_pending_list`, `intercept_resolve`.
- Scope: `scope_is_in_scope`.
- Diagnostics: `burp_version`, `burp_command_line_arguments`, `server_diagnostics`.
- JWT/OAuth: `util_jwt_decode`, `jwt_verify`, `jwt_sign`, `oidc_discover`, `oauth_build_pkce`.
- Agent-native probes: `param_miner_lite`, `bypass_403`, `cors_misconfig_probe`, `fingerprint_target`, `open_redirect_probe`.
- Escape hatch: `montoya_invoke`, `montoya_inspect`, `bridge_list_extensions`.

## Failure policy

A provider outage is local to that provider. Re-discover and, when appropriate, use a configured fallback. Never reinterpret a transport failure as evidence about the target.

If Ultimate is required for a workflow, pin the provider explicitly (`provider="valravn-ultimate"`) so a fallback cannot make a validation gate pass accidentally.
