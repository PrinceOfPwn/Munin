---
name: burp-workflow
description: Efficient Burp Suite orchestration through Valravn Talons and Burp MCP Ultimate
---

# Burp workflow — Ultimate-backed Valravn

This skill preserves the operational workflow from the original Valravn MCP while moving execution to `burp-mcp-ultimate` through Munin's compact Talons gateway.

The rule is simple: **Valravn decides; Ultimate drives Burp.** Do not recreate mature Montoya operations in Python or a second Java REST API.

## First move

Before assuming a tool name or schema:

1. `valravn_talons_status(refresh=false)`
2. `valravn_talons_tools(provider="valravn-ultimate", query="<intent>", include_schema=false)`
3. request the selected schema with `include_schema=true`
4. execute with `valravn_talons_call(..., provider="valravn-ultimate", authorized=true)`

For passive Burp state, prefer `valravn_talons_read` over an active call.

## Evidence-first workflow

When traffic already exists in Burp, reuse it instead of rebuilding requests from memory.

- Read `burp://proxy/history` to locate captured traffic.
- Read `burp://sitemap` for target structure.
- Use provider discovery with queries such as `proxy`, `history`, `repeater`, `organizer`, or `annotation` to find the smallest operation needed.
- Preserve a stable request/handle/index whenever the provider returns one.
- Record the exact provider/tool used with evidence.

A captured authenticated request usually contains more truth than a freshly reconstructed curl command: cookies, headers, anti-CSRF state, content type, routing headers, and client quirks.

## Creating traffic

### Precise raw request

Discover and call `http_send_raw` when byte-level control matters:

```text
valravn_talons_tools(query="http_send_raw", include_schema=true)
valravn_talons_call(
  tool_name="http_send_raw",
  arguments={...schema-derived arguments...},
  provider="valravn-ultimate",
  authorized=true
)
```

Use it for controlled malformed-input tests, unusual headers, request-target variants, and cases where a high-level client would normalize the request.

### Session-aware HTTP

Use `http_send_with_session_handling` when Burp's session handling should participate.

### Parallel / race traffic

Use `http_send_batch` or `http_send_race` when the test requires concurrency. Do not emulate races with a slow sequence of independent model tool calls.

### Repeater / Intruder

Discover `repeater` or `intruder` tools when the operator should retain a visible Burp artifact or when Burp-native placement/configuration is the right abstraction.

## Proxy workflow

Burp Proxy remains an evidence surface even though Munin communicates with Ultimate on MCP port `9444`.

- Burp's ordinary Proxy listener remains separate (normally `127.0.0.1:8080`).
- Ultimate's MCP endpoint is normally `http://127.0.0.1:9444/mcp`.
- There is no Valravn REST listener on `8111` anymore.

Useful passive resource:

```text
valravn_talons_read(
  uri="burp://proxy/history",
  provider="valravn-ultimate"
)
```

For intercept state, discover `intercept_*`. In unattended automation use `intercept_set_mode` with the provider-supported non-blocking mode (`observe` in the pinned CI provider) before depending on Proxy traffic.

## Scope

Use `scope_is_in_scope` for a Burp-side scope check when that is the question. Scope enforcement for a Munin campaign still belongs above the provider: a successful MCP call is not authorization.

Always keep the engagement's explicit authorized target set in Munin/Valravn policy. Provider scope is defense in depth, not the grant of authority.

## Target understanding

Combine Valravn knowledge with Burp state rather than expecting one giant bespoke tool to do everything:

1. Observe target state (`burp://sitemap`, `burp://proxy/history`, `burp://target_summary`).
2. Load the relevant Valravn skill / KB category.
3. Form a narrow hypothesis.
4. Discover one Ultimate tool that can validate it.
5. Capture the resulting request/response evidence.
6. Re-evaluate before escalating.

This replaces legacy mega-tools such as `auto_probe`, `smart_analyze`, and `get_hunt_plan` when their implementation merely duplicated orchestration that the agent can perform with better provider primitives and Valravn knowledge.

## HTTP / web testing map

Use discovery rather than hard-coding every provider schema. Typical intent -> Ultimate surface:

| Intent | Discover around |
|---|---|
| Send raw HTTP | `http_send_raw` |
| Send with Burp session handling | `http_send_with_session_handling` |
| Batch requests | `http_send_batch` |
| Race requests | `http_send_race` |
| Proxy evidence | `proxy`, `burp://proxy/history` |
| Sitemap | `sitemap`, `burp://sitemap` |
| Repeater | `repeater` |
| Intruder | `intruder` |
| Interception | `intercept_*` |
| Scope check | `scope_is_in_scope` |
| Scanner (Pro) | `scanner`, `burp://scan/issues` |
| Collaborator (Pro) | `collaborator` |
| WebSockets | `websocket`, `burp://websockets/active` |
| JWT | `jwt_`, `util_jwt_decode` |
| OAuth/OIDC | `oauth_`, `oidc_` |
| GraphQL | `graphql_` |
| JS endpoints / secrets | `js_extract_endpoints`, `js_scan_secrets`, `js_scan_response` |
| 403 differential | `bypass_403` |
| CORS probe | `cors_misconfig_probe` |
| Parameter discovery | `param_miner_lite` |
| Fingerprinting | `fingerprint_target` |
| Open redirect probe | `open_redirect_probe` |
| Raw Montoya capability not wrapped | `montoya_inspect`, then `montoya_invoke` |
| Another Burp extension | `bridge_list_extensions`, then bridge tools |

## Knowledge-driven testing

The retained Valravn knowledge base is a **reasoning asset**, not an MCP implementation requirement.

When a skill references a legacy Valravn tool that no longer exists:

1. identify the intent of the legacy call;
2. discover the equivalent Ultimate primitive;
3. if the capability belongs to an external security tool (Nuclei, Semgrep, BloodHound, etc.), route through Valravn Arsenal instead of rebuilding it in Burp;
4. if no direct primitive exists, use `montoya_inspect` / `montoya_invoke` only after checking that the operation genuinely belongs in Burp;
5. preserve the evidence and validation gates from the skill even when the executor changes.

Never resurrect `/api/*` REST calls to make an old skill work.

## Arsenal handoff

Burp is not the right home for every security operation. For capabilities supplied by FuzzingLabs Security Hub, use:

1. `valravn_arsenal_list`
2. `valravn_arsenal_tools`
3. `valravn_arsenal_call(..., authorized=true)`

This is especially useful for scanners, SAST, binary tooling, AD tooling, and other ecosystems that are independent of Burp.

## Context economy

- Do not load all 150+ Ultimate schemas.
- Search by task and inspect one schema at a time.
- Prefer MCP resources for passive state.
- Prefer handles/stable IDs to embedding full bodies.
- Paginate large histories.
- Keep binary data out of context unless necessary.
- Summarize repetitive responses locally before returning them to the model.

## Professional vs Community

Ultimate supports both Burp editions, but Burp-native Scanner and Collaborator capabilities still depend on edition availability.

- Community: HTTP, Proxy, Repeater-style operations, many utilities, agent-native probes, resources, and most Montoya interactions remain useful.
- Professional: Scanner / Collaborator and other Pro-only surfaces become available.

Discover capability and handle absence explicitly. Do not replace a missing Pro feature by silently changing the test semantics.

## Failure policy

Provider errors are not target evidence.

- Ultimate unreachable -> report provider failure, optionally discover a configured fallback.
- tool absent -> rediscover; do not guess a renamed tool.
- schema mismatch -> request the current schema and retry with the actual contract.
- Burp process absent -> use the unattended launcher/runbook; do not ask the operator to manually install the extension in CI.
- active call denied by Munin -> respect the authorization gate.

## CI contract

The authoritative live lab is `.github/workflows/valravn-mesh-e2e.yml`.

It must prove all of the following:

- pinned Ultimate builds and its upstream tests pass;
- Burp downloads from PortSwigger and passes checksum/JAR validation;
- Burp starts without manual interaction and preloads Ultimate;
- MCP initialize and `tools/list` work;
- Munin Talons invokes a real Ultimate HTTP tool against OWASP Juice Shop;
- a request traverses the real Burp Proxy listener;
- that request is visible again through `burp://proxy/history`.

If that gate fails, fix the real integration rather than adding a mock-only exception.
