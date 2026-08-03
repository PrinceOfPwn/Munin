---
name: valravn
tags: [valravn, recon, intel, dast, burp-suite, osint, scanning, cti, mesh-valravn, capability-surface]
---

# Valravn intelligence & DAST mesh

Valravn is Munin's **intelligence & DAST mesh**. Two layers that share scope,
audit log and target intel - one passive, the other active, both subordinate
to server-side policy and HITL gates.

## The two layers

### CTI layer - passive reconnaissance

Covers the `valravn_*` tools (Python gateway in `munin/valravn/`).
Passive: they send no traffic to the target, they only consult OSINT sources.

- `valravn_status` - availability check (with `probe=true` live check).
- `valravn_investigate_ioc` - IP, domain, URL, hash, email or CVE indicator.
- `valravn_investigate_organization` - ransomware, breach, exposed
  infrastructure, historical web evidence.
- `valravn_search_assets` - asset search. Operator owns index breadth.
- `valravn_investigate_cve` - KEV, EPSS, affected products, exploit refs,
  exposure context. Finding exploit refs is **intelligence** - using them
  depends on the campaign, it is not an already-acquired capability.
- `valravn_investigate_network` - ASN, prefix, BGP, RPKI, outages, routing
  anomalies.
- `valravn_search_historical_web` - recover URL archives, JavaScript, deleted
  endpoints.
- `valravn_investigate_url` - investigateBeforeSubmit; strictly passive.
- `valravn_submit_url` - active URL submit - only when the operator explicitly
  enables submission.
- `valravn_validate_asset` - cross-check when corroborations are missing.
- `valravn_search_darkweb` - onion index. `*.onion.pet` is a read-only gateway
  - it is **not** anonymous Tor.
- `valravn_capture_web_evidence` - passive screenshot + bounded extraction.
- `valravn_translate` - preserve original source and language metadata.

### DAST layer - active Burp testing

Covers the **Burp MCP server** tools (`valravn/mcp-server/`, package
`burpsuite_mcp`) and the **Munin->Burp HTTP wrapper**
(`munin/mcp/tools/burp_tool.py`). Active: they send traffic to the target via
the Burp proxy, index in the Logger, require explicit operator authorization
and pass through the extension's scope gate.

In Munin they are accessed two ways:

1. **Via `burp_invoke(endpoint, method, json_body)`** - generic unrouted
   dispatcher. The operator or agent assembles the JSON call and targets the
   extension's `/api/<group>/<action>` route; the wrapper routes it via HTTP to
   `127.0.0.1:8111` and returns a structured envelope. **Errors never crash the
   runtime** - Burp unreachable, timeout or HTTP 5xx become an `ok=False` dict
   with `code`/`error`/`hint` and the Munin run continues.
2. **Via the typed wrappers:**
   - `burp_status(probe=False)` - load state + reachable endpoints.
   - `burp_health_check()` - cheap boolean.
   - `burp_check_scope(url)` - wrapper to `POST /api/scope/check`.
   - `burp_get_proxy_count(host="")` - sub-ms read of Proxy history size.

The Burp MCP server tools (`scan_url`, `auto_probe`, `test_csrf`, `test_ssrf`,
`test_ssti`, `test_xxe`, `test_websocket` (`CSWSH`), `test_prototype_pollution`,
`forge_jwt`, `crack_jwt_secret`, `test_login_bypass`, `test_mfa_bypass`,
`test_session_lifecycle`, `analyze_reset_tokens`, `test_auth_matrix`,
`compare_auth_states`, `audit_crawled_artifacts`, `run_opengrep_source`,
`run_gitleaks`, `dump_exposed_git`, `ai_prompt_injection`, `mcp_server_attacks`,
`mcp_tool_poisoning`, `vector_db_injection`, `query_crtsh`, `analyze_dns`,
`run_nuclei`, `run_sqlmap`, `run_katana`, `generate_report`, `save_finding`,
`assess_finding`, `generate_collaborator_payload`, `auto_collaborator_test`,
...) are listed in `valravn/skill.json` capabilities and exposed via
`burp_invoke`. They are **not** wrapped one-by-one in Munin - the extension
REST API is their contract and `burp_invoke` drives them with the same
resilience envelope.

Burp Edition degrades gracefully in Community: scanner/collaborator fall, they
are replaced with `auto_probe` / an operator callback (`interact.sh`,
`webhook.site`, `requestcatcher.com`). See the `valravn-diagnostic` skill for
troubleshooting and free-tier API keys.

## Authorization

The DAST layer is offensive. Bring written authorization for each target: an
in-scope bug bounty, a contract pentest, a red-team ROE, an internal lab, a
CTF. The extension scope (in `valravn/burp-extension/.../ScopeHandler`) is the
final word - the prompt does not override it. Rules 1-4 (scope) and 5-9
(destructive, OOB, egress) stay HARD regardless of scope mode (`operator` or
`strict`).

## Investigation depth

Triage with `depth="quick"`; when there are context constraints, insufficient
evidence, conflict or high impact, escalate to `depth="deep"` - it consumes
more free-tier providers, **at most one extra scarce source**. Do not stack
multiple deep calls in one investigation.

## Evidence discipline

Every investigation preserves: **provider attribution**, **retrieval time**,
**original URL**, **first/last-seen**, **confidence**, **contradictions**,
**failed source records**.

- Distinguish **observation** (provider says X) vs **inference** (Munin
  concludes Y). Do not use a single opaque score.
- Failed sources are also recorded - "provider X returned empty" is
  informative negative evidence.
- Complement with **Hugin** (knowledge layer - malware, low-level, evasion,
  persistence; candidate paths + node metadata) while Valravn covers the
  observation layer (assets, exposure, network, history).
- Critical findings keep a `node_id` / source URL / retrieval timestamp so the
  operator can review.

## Engagement loop

Valravn sits in the `principles.md` §2 campaign loop between steps 2-3:

- Recall (memory + shared intel) -> **Valravn CTI** covers external observation
  -> **Hugin** covers specialized knowledge -> observable hypothesis ->
  minimal action to validate -> **Valravn DAST** executes the active probe via
  Burp -> validated finding -> `publish_shared_intel` or `memory_remember`.
- Do not data-hoard in Valravn - whether CTI observation or DAST probe, what to
  do with the output is Munin's decision.
- Findings that pass Munin's validation AND change downstream decisions go to
  `publish_shared_intel` (`principles.md` §8); common enumeration goes to
  `memory_remember`.

## HITL gates

Active Burp tools (`scan_url`, `test_*`, `forge_jwt`, `crack_jwt_secret`,
`run_sqlmap`, `dump_exposed_git`, `send_to_intruder_*`, `auto_collaborator_test`,
`browser_*` stealth, etc.) cross the Munin runtime's graph interrupt when the
conversation policy requires it - they generate a `waiting_for_human` request.
Approval is bound to the **exact action and argument set**, not reusable for
another tool/args/run. The `burp_invoke` wrapper is `active` in the audit trail
by default; the typed wrappers (status/health/scope_check/get_proxy_count) are
`passive` so they don't parent a HITL but are still audit-logged.

## Failure modes (no-debug summary)

Burp DAST failure == Munin runtime continues. Expected errors:
- `code=extension_unreachable` - Burp not running / extension not loaded / wrong
  port. CI runners without Burp get this and degrade.
- `code=http_*` - extension responds with an HTTP status. The Java-side
  envelope is preserved.
- `code=client_exception` - timeout, connect error, etc. `hint` is actionable.
- `code=bad_args` / `bad_method` - incorrect wrapper invocation.

For an exhaustive diagnosis: the `valravn-diagnostic` skill
(`.opencode/skills/valravn-diagnostic/SKILL.md`) with free-tier API keys and
fixes.
