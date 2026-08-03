# Valravn - Burp DAST surface

[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Version](https://img.shields.io/badge/version-1.0.0-blue)](#versioning)
[![Java](https://img.shields.io/badge/java-21%2B-blue)](https://adoptium.net/temurin/releases/?version=21)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-stdio-blue)](https://modelcontextprotocol.io/)
[![Platforms](https://img.shields.io/badge/platforms-linux%20%7C%20macos%20%7C%20windows%20%7C%20wsl-blue)](#platforms)

> **v1.0** - Distributions: `valravn-mcp` (MCP server) and
> `valravn-burp-ext-<version>.jar` (Burp extension). The Python package is still
> imported as `burpsuite_mcp` for historical compatibility; the rename is
> planned for v1.1.

Valravn is the **agentic DAST surface fused with Burp Suite** inside the
homonymous intelligence mesh. It is not "yet another tool layered on Burp" - it
is the layer that turns an LLM wired over MCP into a native Burp pentester:
every request goes through Burp, every finding comes back indexed in the
Logger, every decision honors the server-side policy of Munin.

## Why it exists

The Valravn mesh already covers passive intelligence (`munin/valravn/` - CTI
gateway, AS lookups, Wayback, dark-web indexing). This delivery closes the
other half - active work under Burp with an LLM brain - so the two layers
share evidence, scope and audit log without friction. What is proved here,
Valravn remembers; what Valravn remembers, is validated here before a finding
is declared.

## Authorization

This is an **offensive tool**. Use it only against systems you are authorized
to test: in-scope bug bounty, contracted pentest, red team with signed ROE,
your own lab, CTF. Munin runtime policy and HITL gates sit **above** any prompt
instruction - the prompt does not grant authority.

## Architecture

```
LLM client  <- stdio MCP ->  Python MCP server  <- HTTP ->  Java Burp extension  <- Montoya API ->  Burp Suite
                                            127.0.0.1:8111
```

- The Java extension exposes REST on `127.0.0.1:8111` and routes HTTP through
  Burp's proxy listener (`127.0.0.1:8080`), so every probe shows up in Proxy
  history.
- The Python MCP server is a thin stdio client. It talks to the LLM and to the
  extension, with no knowledge of Burp internals.
- Target intel persists in `.valravn-intel/<domain>/` (gitignored). It is the
  same directory used by the CTI mesh - single source of truth per domain.

## Features

- **MCP tool surface** - ~370 tools covering recon, scan, exploit, browser,
  auth, research, reporting. See [tool surface](#tool-surface).
- **HTTP via Burp** - `curl_request`, `send_raw_request`, `concurrent_requests`,
  Repeater, Intruder-style - all indexed in Logger.
- **Adaptive scan with KB** - 138+ JSON matchers in `knowledge/` mapping OWASP
  Top 10 (Web / API / LLM / Mobile), WSTG, PayloadsAllTheThings, HackTricks. See
  [coverage](#coverage).
- **Native vulnerability classes** - `test_csrf`, `test_ssrf`, `test_ssti`
  (multi-stage polyglot -> engine differential -> capability probe -> blind),
  `test_xxe`, `test_websocket` (CSWSH), `test_prototype_pollution`.
- **Zero-dep auth tooling** - `forge_jwt` (8 modes), `crack_jwt_secret`,
  `test_login_bypass`, `test_mfa_bypass`, `test_session_lifecycle`,
  `analyze_reset_tokens` (entropy + sequence).
- **Third-party wrappers via proxy** - sqlmap, dalfox, commix, nuclei, ffuf,
  katana, subfinder, amass, wafw00f, arjun, gau, waybackurls, wpscan, nikto.
- **SAST + secrets (v1.0)** - `audit_crawled_artifacts` (opengrep DOM),
  `run_opengrep_source`, `run_gitleaks`, `run_trufflehog` (live verification =
  second HIGH), `dump_exposed_git` + `discover_common_files` (rebuild `.git/`).
  Noir OpenAPI ingest via `import_scope --format noir_json`.
- **Active LLM/MCP probes (v1.0)** - `ai_prompt_injection`, `rag_injection`,
  `mcp_server_attacks`, `mcp_tool_poisoning`, `vector_db_injection`, `echoleak`
  (CVE-2025-32711). Declarative guardrails via `inspect_for_prompt_injection`.
- **CI ready (v1.0)** - SARIF 2.1.0 + JUnit XML, compliance tags (OWASP /
  PCI-DSS / HIPAA / SOC2 / GDPR / CWE), `intensity=safe|normal|aggressive`
  flag, per-engagement cost cap (`set_engagement_cost_cap`), auto-PoC
  `generate_repro_script` curl from `logger_index`.
- **Save-finding pipeline** - 3 phases: verify (replay >=3x) ->
  `assess_finding` (7-Q gate) -> `save_finding`. Q3 is the real impact gate.
- **Defended false positives** - live reflection (not "safe-encoded"
  reflection), double baseline access-control (public -> IDOR), forced OOB
  for blind classes, language-aware XSS context decoding, WAF differential
  timing.
- **CloakBrowser** - headless Chromium with binary-level patched fingerprints
  (not a JS shim). Traffic goes through Burp's proxy. See
  [CloakBrowser](https://github.com/CloakHQ/CloakBrowser).
- **Persistent target memory** - staleness detection, reusable cross-target
  patterns. Vital in the mesh: what you learned in a previous engagement is
  not rediscovered.
- **Operator override surface** - severity floor, scope filter, NEVER-SUBMIT
  list, confidence floor, program policy.

## Requirements

- Burp Suite Professional **or** Community Edition
- Java 21+
- Python 3.11+ with [uv](https://docs.astral.sh/uv/)
- An MCP client (Claude Code, Claude Desktop, etc.)

Optional:

- Go for ProjectDiscovery tools (`subfinder`, `nuclei`, `katana`)
- Burp Pro for active scanner and Collaborator (with graceful Community fallback)

### Burp Edition compatibility

**Professional** - full. The default environment.

**Community** - supported with manual setup. Most features work, because the
extension and MCP server use the Montoya API for HTTP / proxy / scope, not the
scanner pipeline. Graceful degradation:

| Pro feature | Affected tools | Community alternative |
|---|---|---|
| Active scanner | `scan_url`, `crawl_target`, `get_scan_status`, `cancel_scan`, `get_scanner_findings`, `get_new_findings`, `get_issues_dashboard` | `auto_probe` (KB-driven), `fuzz_parameter`, `fuzz_with_feedback`, native `test_*` |
| Collaborator | `generate_collaborator_payload`, `auto_collaborator_test`, `get_collaborator_interactions`, `collaborator_pool_status` | Operator callback - interact.sh / webhook.site / requestcatcher.com. Rule 9a forbids invented domains |
| Intruder full | `send_to_intruder_configured` | `concurrent_requests` (Python-side concurrency via Burp proxy) |

`check_pro_features()` at session start reports which Pro capabilities are
available - runtime detection, never a silent hang.

## Installation

### Quick - `uvx` (no repo clone)

The MCP server runs directly from the source tree. The Burp extension JAR
still requires a checkout - see Manual.

```sh
uvx --from "git+https://github.com/PrinceOfPwn/Munin.git#subdirectory=valravn/mcp-server" \
    valravn-mcp
```

Or in `.mcp.json` (gitignored by convention):

```json
{
  "mcpServers": {
    "burpsuite": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/PrinceOfPwn/Munin.git#subdirectory=valravn/mcp-server",
        "valravn-mcp"
      ]
    }
  }
}
```

### Automated (extension + server)

```sh
./setup.sh        # Linux / macOS
./setup.ps1       # Windows PowerShell
./setup.bat       # Windows double click
```

The script installs anything missing (Java 21+, Maven, Python 3.11+, uv, Go),
builds the extension, installs the MCP server (includes CloakBrowser warm-up),
optionally installs ProjectDiscovery tools, writes `.mcp.json`.

Run `./doctor.sh` for post-install validation.

### Manual

```sh
# 1. Build the Burp extension
cd burp-extension
mvn package
# Load target/valravn-burp-ext-1.0.0.jar in Burp: Extensions -> Add -> Java

# 2. Install the MCP server
cd ../mcp-server
uv venv
uv sync

# 3. Configure your MCP client (see below)
```

### `pipx`

```sh
pipx install "git+https://github.com/PrinceOfPwn/Munin.git#subdirectory=valravn/mcp-server"
valravn-mcp
```

## Configuration

`.mcp.json` at project root. Gitignored; each operator maintains their own.

```json
{
  "mcpServers": {
    "burpsuite": {
      "command": "/absolute/path/to/valravn/mcp-server/.venv/bin/python",
      "args": ["-m", "burpsuite_mcp"]
    }
  }
}
```

## MCP Prompts

Reusable workflows exposed by the server:

| Prompt | Args | Use |
|---|---|---|
| `hunt-target` | `target` | Standard hunt loop: scope -> recon -> probe -> verify -> save |
| `verify-finding` | `vuln_type`, `endpoint`, `evidence` | 7-Q gate pre-save |
| `triage-program` | `program` | Configure program policy, scope, overrides |
| `chain-findings` | `domain` | Propose A->B->C chains upgrading severity |
| `save-finding-checklist` | `vuln_type`, `endpoint` | Pre-save checklist forcing replay -> assess -> save |

## MCP Resources

Read-only context mountable without spending tool budget:

| URI | Returns |
|---|---|
| `burp://rules/hunting` | 28 permanent hunting rules (HARD/DEFAULT/ADVISORY) |
| `burp://rules/engineering` | 4 engineering rules |
| `burp://skills/{name}` | Skill markdown by stem |
| `burp://knowledge/index` | KB category listing with counts |
| `burp://knowledge/{category}` | Raw JSON of a category (probes + matchers + craft) |
| `burp://intel/{domain}/{kind}` | Target intel: `profile`, `endpoints`, `coverage`, `findings`, `fingerprint`, `patterns`, `notes` |
| `burp://findings/{domain}` | Aliased from `burp://intel/{domain}/findings` |

## Coverage

KB at `mcp-server/src/burpsuite_mcp/knowledge/`. Each JSON declares contexts,
server-side matchers and optional craft guidance. Adding `.json` extends
coverage; `auto_probe` picks it up at runtime. Per-category index at
[`mcp-server/src/burpsuite_mcp/knowledge/_INDEX.md`](mcp-server/src/burpsuite_mcp/knowledge/_INDEX.md).

| Framework | Status |
|---|---|
| OWASP Web Top 10 (2021) | 10/10 |
| OWASP API Security Top 10 (2023) | 10/10 |
| OWASP LLM Top 10 (2025) | 9/10 (LLM09 misinformation out-of-scope) |
| OWASP Mobile Top 10 (2024) | Surface coverage applied (deep-link, WebView, mobile API, payments). M5 via `mobile-dynamic-agent` Frida pinning bypass; M7 binary protections out-of-scope |
| OWASP WSTG | Full coverage - information gathering, config, identity, authn, authz, session, input val, error handling, crypto, business logic, client-side, API |
| PayloadsAllTheThings | Each injection/abuse class mapped - Zip Slip, parameter injection, GraphQL engine-specific |
| HackTricks Web | Path traversal, SSRF, SSTI, deserialization, prototype pollution, request smuggling, cache poisoning, CSPP, OAuth, SAML, WebDAV, file upload |
| HackTricks Cloud | Anonymous external surface - object storage misconfig (S3/GCS/Azure Blob/R2/B2/Spaces/OCI/MinIO), function URL (Lambda/Cloud Run/Cloud Functions/Azure/OpenFaaS), API gateway (AWS/GCP/Azure APIM/Kong/KrakenD/Tyk), Kubernetes (kubelet/kube-apiserver/etcd/dashboard/ArgoCD/Tekton/Rancher/Portainer/registries). Creds-based privesc (Paci-class) out-of-scope per operator policy |

Perimeter appliance CVE packs covered: Citrix NetScaler, F5 BIG-IP, Ivanti
Connect Secure, PAN-OS GlobalProtect, MOVEit, SonicWall SSLVPN, CrushFTP,
Exchange, Confluence, TeamCity, GeoServer, Log4Shell.

### Scope and non-goals

Valravn is a **DAST orchestrator over Burp** - web / API / cloud / LLM. The lane
is intentional: tools, memory model and finding pipeline are optimized for
that. Out of scope by design:

- **Intranet / Active Directory** - no BloodHound, NetExec, impacket, Kerberos
  abuse, SMB. `probe_kerberos_spnego_auth` only detects (sends
  `WWW-Authenticate: Negotiate`); full GSSAPI and AD lateral not covered. Use
  a dedicated AD toolbox.
- **Thick client / native desktop binary** - Electron IPC/ASAR as KB reference
  (`desktop_electron`) + skill; native Windows/macOS apps without
  binary-instrumentation automation.
- **Non-HTTP / binary fuzzing** - fuzz is HTTP/parameter-targeted; no
  boofuzz/AFL grammar or network protocol fuzzing.
- **Mail infra security** - DNS analysis only annotates SPF/DMARC *existence*;
  no DKIM selector enum, SMTP/STARTTLS/open-relay, BEC/spoofing.
- **Container runtime / eBPF detection** - image scanning (Trivy/Grype/Hadolint)
  covered; runtime (Falco-class) not.
- **Autonomous destructive/RCE exploitation** - RCE is a detection gate;
  Metasploit integration is operator-supervised. Valravn uses benign markers
  for impact, never data destruction (Rules 5-8).

For those cases, use a **dedicated tool alongside** Valravn - do not expect
Valravn to absorb them.

## Save-finding pipeline

Three phases enforced by a gate:

1. **Replay.** `resend_with_modification(index)` confirms the anomaly and
   records the `logger_index`.
2. **Assess.** `assess_finding(...)` runs the 7-Q gate (scope, reproducibility,
   impact, dedup, evidence, NEVER-SUBMIT, triager) -> `REPORT` /
   `NEEDS MORE EVIDENCE` / `DO NOT REPORT` + suggested confidence.
3. **Save.** If the gate passes, `save_finding(...)` persists. The Java
   extension rejects findings without parseable evidence, without
   NEVER-SUBMIT `chain_with[]`, without `reproductions[]` with timing/blind.

Operator override: `overrides=["q5_evidence:<reason>", ...]` (audit log),
`human_verified=True`, or `set_program_policy`. See
`.claude/skills/user-override.md`.

## Skills

`.claude/skills/` holds behavioral skills:

- `hunt.md` - systematic hunt workflow
- `verify-finding.md` - evidence thresholds per class + 7-Q gate
- `resume.md` - continue a previous session, re-verify findings
- `chain-findings.md` - low findings to a chain with reportable impact
- `report-templates.md` - per-platform format
- `autopilot.md` - autonomous hunt loop with circuit breaker
- `dispatch-agents.md` - parallel agent orchestration
- `burp-workflow.md`, `investigate.md`, `craft-payload.md`,
  `static-dynamic-analysis.md`
- `user-override.md` - operator override surface when defaults block
- `operational-discipline.md` - cross-role discipline (pentester / BBH / red
  team / researcher)
- `security-research.md` - deep-dive via `research_attack_vector` + WebFetch

`.claude/rules/` permanent:

- `engineering.md` - 4 rules (think first, simplicity, surgical, goal-driven)
- `hunting.md` - 32 rules in tiers HARD (1-10) / DEFAULT (11-21) / ADVISORY
  (22-32)

## Agents

`.claude/agents/` defines sub-agents auto-loaded by Claude Code at startup.
Orchestrator + specialists:

- `grow-agent` - per-session orchestrator, one active domain at a time
- `recon-agent` - attack surface mapping
- `js-analyst` - JS secrets and DOM source->sink flows
- `vuln-scanner` - per-class probing, one instance per class
- `finding-verifier` - re-verification with evidence thresholds
- `payload-crafter` - WAF/filter bypass
- `auth-tester` - authz matrix, IDOR/BFLA, JWT
- `browser-agent` - SPA and JS-heavy targets
- `auth-payment-agent` - OAuth, FIDO2/passkey, Apple/Google/Samsung Pay, IAP, 3DS
- `fuzz-agent` - tech-aware wordlist generation and ffuf
- `mobile-dynamic-agent` - Frida and adb (pinning bypass, runtime hook, deep-link sinks)

Roles and parallel modes in [AGENTS.md](AGENTS.md).

## Platforms

- Linux
- macOS
- Windows (in `.mcp.json` use `.venv\Scripts\python.exe`)
- WSL (Burp on Windows host - mirrored preferred; NAT fallback)

The Java extension and the Python server use platform-agnostic libs.

## Diagnosis

When something falls over - see the [`valravn-diagnostic` skill](../.opencode/skills/valravn-diagnostic/SKILL.md)
at the Munin repo root. It covers frequent failure modes, free-tier API keys
and fixes.

## Versioning

`v1.0.0` - `valravn-mcp` (server + `burpsuite_mcp` Python package),
`valravn-burp-ext` (Java extension). The Python package rename to
`valravn_mcp` is planned for v1.1 - the `burpsuite_mcp.*` import path remains
the binding contract for this release.

Versioning follows SemVer. Patch: bug fixes. Minor: backwards-compatible tools.
Major: breaking schema or tool signature changes.

## Contributing

Issues and pull requests welcome. Please:

- Open an issue before non-trivial PRs.
- Before committing, run the full Python suite
  (`cd mcp-server && uv run python -m unittest discover -s tests -v`) and
  `cd burp-extension && mvn package`. CI is the authoritative environment.
- Match existing style (Java: camelCase methods, snake_case JSON keys; Python:
  PEP 8, async tools).
- Do not add external Java dependencies - the extension uses only the Montoya
  API and the JDK.

**Trailer conventions**: `--trailer "Reported-by:<name>"` for bugs/features by
name; `--trailer "Github-Issue:#<number>"` for issues. Never `co-authored-by`
or AI tool references in commits/PRs.

## License

[Apache License 2.0](LICENSE) - includes `NOTICE` with upstream attribution
required by Apache-2.0 §4(d).

The project integrates with Burp Suite (a product of PortSwigger Ltd). Burp
Suite is a registered trademark of PortSwigger Ltd. Not affiliated with or
endorsed by PortSwigger.

Uses the Model Context Protocol (MCP), developed by Anthropic.
