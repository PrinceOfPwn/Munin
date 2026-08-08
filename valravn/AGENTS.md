# Agent team configuration

The Valravn mesh dispatches specialized agents in parallel for penetration
work. The orchestrator (main conversation) spawns agents for independent
workflows, merges results and takes strategic decisions.

## Definition files

Each role maps to a `.claude/agents/<role>.md` file. Claude Code's `Agent`
tool loads them by name. Changing a role means updating this overview (role
delta) and the `.md` file (operation detail).

Orchestrator roles were split: `grow-agent` is the per-session orchestrator
(see `docs/specs/2026-05-22-grow-agent-design.md`). When invoked, `grow-agent`
dispatches the 9 worker roles below.

## Command layer (above grow-agent)

Two engagement leads sit above `grow-agent`. The leads own strategy - research,
written plans, multi-domain dispatch, cross-target synthesis, delivery - they
never run per-domain loops. Both use the shared SOP
`.claude/skills/command-engagement.md`; each agent file carries only the role
delta. Dispatched on demand.

```
{pentest|redteam}-commander        engagement lead - research, plan, synthesis, report
  └─ grow-agent(domain)   × N      per-domain executor (limit: 2-3 in-flight)
       └─ 10 workers
```

### pentest-commander
**Purpose:** coverage-driven engagement lead. Full WSTG/OWASP coverage, every
finding verified + reported.
**When to dispatch:** multi-endpoint / multi-domain pentest where breadth +
a findings report is the deliverable.
**Dispatch:** `Agent(subagent_type="pentest-commander", prompt="domains=[...], objective=..., session_name=...")`.
**Success:** complete coverage matrix (or documented negative), all findings
confirmed + writeup, report in `reports/`.

### redteam-commander
**Purpose:** goal-driven engagement lead. Shortest kill chain to the declared
objective with a bounded stealth/noise budget.
**When to dispatch:** red-team objective-driven (reach data/access/flag)
instead of coverage scan.
**Dispatch:** `Agent(subagent_type="redteam-commander", prompt="objective=..., domains=[...], noise_budget=low|moderate|high")`.
**Success:** objective reached + kill chain documented, or chain of furthest
advance + blockers.

**Anti-recursion (HARD):** commander never dispatches commander. Commander
dispatches `grow-agent` (which never dispatches `grow-agent`) + specialists.
A single command layer.

## Agent roles

### recon-agent
**Purpose:** map the target's attack surface in parallel with other analysis.
**When to dispatch:** start of any new engagement, or when the endpoint
segment is stale.
**Tools:** `discover_attack_surface`, `discover_common_files`, `full_recon`,
`detect_tech_stack`, `get_unique_endpoints`, `discover_hidden_parameters`
**Returns:** list of endpoints with risk score, tech stack, sensitive files
found, hidden parameters.

### js-analyst
**Purpose:** deep JavaScript analysis - secrets, DOM sinks, API endpoints.
**When to dispatch:** post-recon identification of JS files, or in parallel
with recon.
**Tools:** `fetch_page_resources`, `extract_js_secrets`, `analyze_dom`,
`extract_api_endpoints`, `fetch_resource`
**Returns:** secrets found (with severity), DOM XSS sink->source flows, hidden
API endpoints.

### vuln-scanner
**Purpose:** test specific vulnerability classes against given endpoints.
**When to dispatch:** post-recon, one agent per class, non-overlapping targets.
**Tools:** `auto_probe`, `bulk_test`, `probe_endpoint`, `fuzz_parameter`,
`test_lfi`, `test_file_upload`, `test_cors`, `test_graphql`,
`test_cloud_metadata`, `test_open_redirect`, `test_jwt`, `get_payloads`
**Returns:** scored findings, probed params, pending _outliers_ to investigate.
**Important:** each vuln-scanner agent receives a different target group or
class - avoid duplicate requests.

### finding-verifier
**Purpose:** re-verify confirmed findings, investigate _outliers_.
**When to dispatch:** resume sessions with stale findings, or post-scan when
_outliers_ surface.
**Tools:** `session_request`, `compare_auth_states`, `auto_collaborator_test`,
`get_collaborator_interactions`, `compare_responses`, `save_target_intel`
**Returns:** updated findings status (confirmed/stale/likely_false_positive)
with evidence.

### payload-crafter
**Purpose:** build bypass payloads when standard ones are blocked by a
WAF/filter.
**When to dispatch:** vuln-scanner reports all payloads were blocked on a
param that looks injectable.
**Tools:** `fuzz_parameter`, `get_payloads`, `decode_encode`, `session_request`,
`probe_endpoint`, `save_target_notes`
**Returns:** usable bypass payload with filter map, or "filter too strong" with
evidence.

### auth-tester
**Purpose:** cross-endpoint authorization and access-control testing.
**When to dispatch:** when multiple session/auth states are available
(admin + user + anon).
**Tools:** `test_auth_matrix`, `compare_auth_states`, `test_race_condition`,
`test_parameter_pollution`, `test_jwt`, `session_request`
**Returns:** IDOR findings, auth bypass results, race conditions.

### browser-agent
**Purpose:** SPA/JS-heavy crawling and JavaScript interaction.
**When to dispatch:** target with heavy client-side rendering (Angular/React/Vue),
or when server-side crawl misses auto-loaded content.
**Tools:** `browser_navigate`, `browser_crawl`, `browser_interact_all`,
`browser_click`, `browser_fill`, `browser_execute_js`, `browser_get_page_info`
**Returns:** endpoints discovered via JS rendering, dynamic routes, XHR/API
calls in proxy history.
**Constraint:** only one browser agent at a time - single browser instance.

### mobile-dynamic-agent
**Purpose:** run Frida (iOS + Android) and adb (Android only) on the operator's
host - bypass SSL pinning / root-JB detection, hook runtime crypto + storage,
abuse Android exported components and deep links, dump iOS keychain - to route
backend traffic to Burp. Dynamic only; no static decompile.
**When to dispatch:** the operator already has APK/IPA on device + Frida server
+ adb authorized + Burp CA on device. Triggered by `playbook-mobile-dynamic.md`.
Dispatch before `playbook-mobile-backend.md` - this agent unblocks traffic.
**Tools:** `Bash` for `frida -U -l <script>`, `adb shell ...`,
`objection -g <pkg>`; `get_proxy_history`, `extract_api_endpoints`,
`search_history`, `build_target_header_profile`, `save_target_intel`,
`annotate_request`
**Returns:** pinning bypass status, backend endpoints + headers + tokens
captured, hooked HMAC/crypto keys, exported components, deep link param sinks,
iOS keychain items, IAP receipt structure. Handoff to `playbook-mobile-backend.md`
§3.
**Constraint:** only one mobile-dynamic agent at a time - one device, one app,
one Frida session. Never dispatch against someone else's device. Do not report
pinning/root bypass as separate findings - they are means, not bugs.

### auth-payment-agent
**Purpose:** deep-test the highest-value attack surface - OAuth 2.0 / OIDC,
WebAuthn / FIDO2 / passkey, Google Pay, Apple Pay, Samsung Pay, IAP server-side
validation, 3DS 2.x bypass, SCA exemption abuse, wallet linking, recovery flow
downgrade. $5k-$50k bugs.
**When to dispatch:** router Q7 match or explicit operator request for
"OAuth / SSO / payment / FIDO / wallet / recovery testing". Frequently
co-dispatched with `mobile-dynamic-agent` when the payment/auth flow is
mobile-app native.
**Tools:** `session_request`, `run_flow`,
`auto_probe(categories=["oauth","oauth_device_flow","webauthn_passkey","payment_flow"])`,
`test_jwt`, `auto_collaborator_test`, `compare_auth_states`, `concurrent_requests`
(recovery-code bruteforce probe), `resend_with_modification`, `search_history`,
`extract_regex`, `assess_finding`, `save_finding`
**Returns:** confirmed bypasses with repro, replay-chain evidence, severity-graded
findings with PoC steps, suggested `chain_with[]` anchors for higher-severity
report.
**Constraint:** always via the `playbook-payment-and-auth.md` workflow - map the
multi-step flow before mutating single-steps. Do not fuzz 1000 payloads at
`redirect_uri` when `auto_probe` covers available bypasses.

### fuzz-agent
**Purpose:** discover hidden directories and files with a smart, tech-aware
wordlist. Surgical SecLists slice instead of spray, fed by recon intel.
**When to dispatch:** post `detect_tech_stack` fingerprint of the target, once
standard recon endpoints are mapped. Triage tier `shallow`, main run `medium`,
`deep` only if shallow+medium are empty.
**Tools:** `detect_tech_stack`, `generate_smart_wordlist`, `run_ffuf` (via Burp
proxy; `match_codes=[200,204,301,307,401,403,500]`, `filter_size=<baseline>`),
`annotate_request` (color `YELLOW`, comment `hidden-path`), `send_to_organizer`,
`save_target_intel`
**Returns:** new endpoints written to `.valravn-intel/<domain>/endpoints.json`,
each hit YELLOW-annotated in proxy entries, follow-up organizer entries.
**Constraint:** never two simultaneous `fuzz-agent` over the same host - WAF
trigger. Max 1 concurrent `fuzz-agent` per host per full session. See
`.claude/skills/fuzz-hidden-paths.md`.

## Dispatch rules

1. **Never dispatch concurrent agents against the same endpoint** - WAF trigger,
   rate limit, corrupted results.
2. **All agents share the same session** for auth consistency (the session is
   thread-safe in the Java extension).
3. **The orchestrator does not duplicate work** - if it dispatched an agent to
   scan SQLi, it does not scan SQLi itself.
4. **Merge before the next strategic decision** - wait for all parallel agents
   before deciding the next step.
5. **Merge before saving intel** - the orchestrator calls `save_target_intel`
   with merged results, not individual agents.
6. **Browser agent not parallel** - only one headless browser instance.
7. **Populate proxy history before extract tools** - `browser_crawl` first.

## Parallel patterns

### Mode 1: Recon fan-out
Engagement start dispatches simultaneously:
- recon-agent: crawl + endpoint mapping
- js-analyst: scan JS files for secrets and DOM XSS
Both background. Orchestrator merges results into an attack priority list.

### Mode 2: Vulnerability parallel
Post-recon, partition targets by vulnerability class:
- vuln-scanner (SQLi): endpoints with id/num/page params
- vuln-scanner (XSS): endpoints with search/comment/name params
- vuln-scanner (LFI): endpoints with file/path/include params
- auth-tester: all auth endpoints (IDOR matrix)
Each agent with non-overlapping targets.

### Mode 3: Verify batch
Resume session dispatches verification of multiple findings simultaneously:
- finding-verifier #1: re-verify CRITICAL findings
- finding-verifier #2: re-verify HIGH findings
- finding-verifier #3: re-verify MEDIUM findings

### Mode 4: Investigate + continue scan
When an _outlier_ is found:
- payload-crafter: investigate the _outlier_ (foreground, result needed)
- vuln-scanner: continue testing the next class (background)

### Mode 5: Mobile engagement pipeline
Sequential, not parallel (each stage depends on the previous):
1. **mobile-dynamic-agent (foreground):** bypass pinning + root/JB detection,
   hook runtime, capture endpoints. Runs `playbook-mobile-dynamic.md`. When
   backend traffic flows, stop.
2. **recon-agent + js-analyst (parallel):** enrich captured endpoints, map JS
   bundles, find hidden mobile-specific routes.
3. **vuln-scanner + auth-tester + auth-payment-agent (parallel):** against
   discovered endpoints with non-overlapping vuln classes. `auth-payment-agent`
   covers OAuth/FIDO/IAP/Pay.
4. **finding-verifier:** confirm and chain.

### Mode 6: Auth + payment scan
For targets with SSO + payment integration (e-commerce, fintech, SaaS):
- **auth-payment-agent (foreground):** drive `playbook-payment-and-auth.md`,
  capture OAuth + payment flows, run KB scans.
- **auth-tester (background):** independent IDOR/BFLA testing between auth
  states discovered in the auth flow.
- **finding-verifier (after both):** chain auth findings with payment findings
  (e.g. OAuth redirect -> ATO -> payment-token theft -> cross-account debit).

## Anti-patterns

- **Do not dispatch an agent for trivial work** - a single `quick_scan` call
  needs no agent.
- **Do not dispatch >4 concurrent agents** - the MCP server processes requests
  sequentially, too many agents queue up.
- **Do not let agents take strategic decisions** - agents execute, the
  orchestrator decides.
- **Do not skip the merge step** - always collect and analyze all agent results
  before advancing.
- **Do not dispatch agents for sequential workflows** - login flows, CSRF token
  extraction chains, `run_flow` steps must be sequential.
