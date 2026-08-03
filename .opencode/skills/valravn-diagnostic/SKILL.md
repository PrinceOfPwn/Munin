---
name: valravn-diagnostic
description: "Diagnose and repair failures in the Valravn mesh (passive CTI + active Burp DAST). How to get free / free-tier API keys for the intelligence providers and how to fix Burp extension unreachable, port 8111 occupied, missing Java 21, uv not installed, Collaborator on Community, Intruder rate-limiting, CloakBrowser and OOB hooks. Use when valravn_* returns failing sources, when burp_* returns extension_unreachable, before a DAST session or to audit provider configuration."
tags: [valravn, diagnostic, troubleshooting, api-keys, free-tier, burp-suite, cloakbrowser, projectdiscovery, osint, dast, resilience, fix, runbook]
---

# Valravn Diagnostic

Runbook to diagnose and repair the Valravn mesh (passive recon + active DAST).
Both surfaces live in Munin: CTI at `munin/valravn/` (Python, passive), DAST at
`valravn/` (Burp extension + MCP server) with an HTTP bridge via
`munin/mcp/tools/burp_tool.py`.

When something fails, this doc tells you why and how to fix it. **Test in CI
only** - we do not install Burp/Java/CloakBrowser locally.

## Quick fallback table

| Symptom | Likely cause | Section |
|---|---|---|
| `burp_status` or `burp_invoke` returns `code=extension_unreachable` | Burp not running or extension not loaded | [Burp unreachable](#1-burp-extension-unreachable) |
| Port `8111` occupied | Another Burp/process holds it | [Port 8111 occupied](#2-port-8111-occupied) |
| `code=client_exception` with `*Timeout*` | Burp too slow or target hung | [Burp timeout](#3-burp-timeout) |
| `code=client_exception` with `*Connect*` (not `unreachable`) | DNS/proxy loopback | [Connect (not unreachable)](#4-connect-errors-not-unreachable) |
| Java complains about version | Missing Java 21+ | [Missing Java 21](#5-missing-java-21) |
| `uv: command not found` | uv missing | [uv not installed](#6-uv-not-installed) |
| `scan_url` fails | No Burp Pro | [Scanner/Collaborator Community fallback](#7-scannercollaborator-community-fallback) |
| `concurrent_requests` slow | Intruder Community throttled | [Intruder rate-limiting (Community)](#8-intruder-rate-limiting-community) |
| `cloakbrowser not installed` | Missing CloakBrowser or license | [CloakBrowser](#9-cloakbrowser) |
| `valravn_search_darkweb` empty | Tor not encouraged; provider offline | [Darkweb provider](#10-darkweb-provider) |
| `valravn_investigate_ioc` returns few sources | Missing API keys | [Free / free-tier API keys](#free--free-tier-api-keys) |
| `*.json` KB finds no matches | Stale KB | [Stale KB](#11-stale-kb) |
| `assess_finding` valid but `save_finding` rejects | Invalid `chain_with[]` anchor | [Save-finding pipeline](#12-save-finding-pipeline) |

---

## 1. Burp extension unreachable

**Symptom**: `burp_status` or `burp_invoke` returns
`{"ok": False, "error": {"code": "extension_unreachable"}}`.

**Causes and fixes**:

1. **Burp Suite is not running.**
   - Open Burp. If it's the first time, complete the Community accept wizard.
2. **The Valravn extension is not loaded.**
   - In Burp: `Extensions` -> `Add` -> `Java` -> select
     `valravn/burp-extension/target/valravn-burp-ext-1.0.0.jar`. If the JAR
     does not exist, see [Build the extension](#build-the-extension).
3. **The default port changed.**
   - The extension listens on `127.0.0.1:8111`. If you changed host/port in
     the Valravn tab, propagate to the Munin process with `BURP_API_HOST` /
     `BURP_API_PORT`. See env vars below.
4. **WSL with Burp on the Windows host.**
   - Mirrored networking: `127.0.0.1` (default) works. On Windows 11 22H2+:
     enable `[wsl2] networkingMode=mirrored` in `%UserProfile%\.wslconfig` and
     `wsl --shutdown`.
   - NAT: `BURP_API_HOST=<windows host IP>` (default route gateway) and the
     extension tab `Host = 0.0.0.0` with JVM flag
     `-Dvalravn.allow_non_loopback_bind=true`.

**Check**: `burp_health_check()` must return
`{"ok": True, "data": {"healthy": True}}`. Alternatively from the Linux host:
`curl -sS http://127.0.0.1:8111/api/health`.

**Env var reminder**:

```sh
export BURP_API_HOST=127.0.0.1
export BURP_API_PORT=8111
export BURP_API_TIMEOUT=30  # optional; default 30
export BURP_MAX_RESPONSE_SIZE=50000  # optional; default 50k chars
```

## 2. Port 8111 occupied

**Symptom**: Burp logs `java.net.BindException: Address already in use` while
loading the extension, or `burp_status` returns `extension_unreachable` even
though Burp is running.

**Diagnosis**:

```sh
# Linux/macOS
lsof -i :8111
ss -tlnp | grep 8111
# Windows
netstat -ano | findstr :8111
```

**Fix**:

- Kill the process holding it (another Burp, an orphan JVM, etc.).
- Or change the port in the Valravn tab and propagate `BURP_API_PORT`.

## 3. Burp timeout

**Symptom**: `code=client_exception` with `hint` mentioning "didn't respond
within 30s".

**Causes**:

- Remote target hung; the Java extension is stuck on a socket read.
- The extension's 24-thread pool is saturated.
- A `test_*` probe running a very long multi-stage series (e.g. `test_ssti`
  polyglot -> engine diff -> engine-specific payload).

**Fix**:

```sh
export BURP_API_TIMEOUT=60  # next invocation
export BURP_API_TIMEOUT=120 # if it still hangs
```

Your timeout should be > than the target's HTTP timeout. If the target answers
in 30s and your probe makes 3 roundtrips, `BURP_API_TIMEOUT=120` is safe.

## 4. Connect errors (not unreachable)

**Symptom**: `code=client_exception` with `cls=ConnectError` but the
reachability health-check passes.

**Causes and fixes**:

- **Proxy loopback.** Your `.mcp.json` or shell has `HTTP_PROXY`/`HTTPS_PROXY`
  pointing at something other than Burp (`127.0.0.1:8080`). For wrapper calls
  to the extension (8111), make sure proxies ignore `127.0.0.1`:
  ```sh
  export NO_PROXY=127.0.0.1,localhost
  ```
- **IPv6 vs IPv4.** `127.0.0.1` is IPv4. If the extension only listens on IPv6
  `::1`, use `BURP_API_HOST=::1` (rare in practice).
- **Local firewall.** Windows Defender / ufw drop loopback. Allow
  `127.0.0.1:8111` in/outbound.

## 5. Missing Java 21

**Symptom**: the extension does not load; Burp throws
`Unsupported class file major version` or `UnsupportedClassVersionError`.

**Check**:

```sh
java -version
# must show 21.x or higher
```

**Install free**:
- Linux/macOS: [Adoptium Temurin 21](https://adoptium.net/temurin/releases/?version=21)
- Windows: [adoptium.net download](https://adoptium.net/temurin/releases/?version=21)
- Package managers: `apt install temurin-21-jdk` (with
  [Adoptium APT repo](https://adoptium.net/installation/linux.html)),
  `brew install --cask temurin@21`, `scoop install temurin21-jdk`,
  `choco install temurin21`.

After install, restart Burp and reload the extension.

## 6. uv not installed

**Symptom**: `uv: command not found` when running the Burp MCP server or
`./setup.sh`.

**Install**:

```sh
# Linux/macOS
curl -LsSf https://astral.sh/uv/install.sh | sh
# Windows PowerShell
irm https://astral.sh/uv/install.ps1 | iex
# Or via pip
pip install uv
```

`uv` is free and open source (Astral). No login, no API key.

**Verify**: `uv --version`.

## 7. Scanner/Collaborator Community fallback

**Symptom**: tools `scan_url`, `crawl_target`, `get_scan_status`, `cancel_scan`,
`get_scanner_findings`, `get_new_findings`, `get_issues_dashboard`,
`generate_collaborator_payload`, `auto_collaborator_test`,
`get_collaborator_interactions`, `collaborator_pool_status` fail with "Pro
required" or 412 errors from the extension.

**Cause**: they are Pro-only; Community does not have them.

**Community alternatives** (no Pro license needed):

- **Active scanner** -> `auto_probe` (KB-driven, runs against the extension
  HTTP API without Burp's scanner pipeline), `fuzz_parameter`,
  `fuzz_with_feedback`, native `test_*` (`test_csrf`, `test_ssrf`, `test_ssti`,
  `test_xxe`, `test_websocket`, `test_prototype_pollution`, `test_login_bypass`,
  `test_mfa_bypass`).
- **Collaborator OOB** -> operator-supplied callback URL:
  - [interact.sh](https://interact.sh) - ProjectDiscovery, free
  - [webhook.site](https://webhook.site) - free, no signup
  - [requestcatcher.com](https://requestcatcher.com) - free, no signup
  - Your own domain with a wildcard DNS hostname for production-grade usage.
  Pass the explicit callback URL in the payload. **Rule 9a forbids invented
  domains** - use one of these concrete ones or your own.
- **Intruder full speed** -> `concurrent_requests` (Python-side concurrency via
  the Burp proxy, not rate-limited).

**Pro availability check**: call `check_pro_features()` at session start -
returns `{pro_features: [...]}`.

## 8. Intruder rate-limiting Community

**Symptom**: `send_to_intruder_configured` is slow or throttled (Community cap
~1 req/s after ~200 reqs/min).

**Fix**: use `concurrent_requests(url, count, method, headers=...)` instead of
Intruder. Bypass Community throttling via Python-side concurrency through the
Burp proxy (not the Intruder internal loop).

```python
# Example via burp_invoke
burp_invoke(
    endpoint="/api/http/concurrent",
    method="POST",
    json_body={
        "method": "GET",
        "url": "https://target.example/path",
        "count": 50,
        "headers": {"X-Custom": "value"},
        "match_codes": [200, 302, 403]
    }
)
```

`match_codes` filter server-side reduces noise.

## 9. CloakBrowser

**Symptom**: `browser_crawl` / `browser_navigate` /
`browser_capture_web_evidence` fail with `"error": "CloakBrowser not
installed", "hint": "Run: uv pip install cloakbrowser"`.

**Cause**: CloakBrowser is a patched Chromium (binary-level stealth
fingerprints, not a JS shim). It requires install.

**Install**:

```sh
uv pip install cloakbrowser
# Or with CDN download included:
uv pip install "cloakbrowser>=0.3.28"
```

CloakBrowser downloads a patched Chromium (~200MB) on the first run (warm-up).
It stays cached in `~/.cache/cloakbrowser/` (Linux/macOS) or
`%LOCALAPPDATA%\cloakbrowser\` (Windows).

**License**: CloakBrowser is OSS but requires a **license key** for some
premium fingerprint features. The license is set with the
`CLOAKBROWSER_LICENSE_KEY` env var. Without a license, stealth features work
for basic fingerprints; advanced ones degrade to vanilla Chromium. Not a
blocker for everyday tooling.

**Without license**: the `browser_*` tools still work - they only lose some
premium anti-fingerprinting features. For a CTF / pentest against a target
with fingerprint-aware WAF, getting a license is worth it.

License info: [cloakhq.com](https://github.com/CloakHQ/CloakBrowser) for
current details.

## 10. Darkweb provider

**Symptom**: `valravn_search_darkweb` returns
`{"count": 0, "onions": []}` and `sources: {onion_pet: failed, ahmia:
failed}`.

**Cause**: `*.onion.pet` (a read-only gateway to `.onion` without Tor) and
[ahmia.fi](https://ahmia.fi) are the default providers; they go down often.

**Fix**: this is expected behavior - it does not break other tools. Retry
later. Do not install Tor locally; **Rule**: `*.onion.pet` is NOT Tor
anonymity, it is a read-only gateway. For deep darkweb research, run from a
physical Tails/Whonix machine - **outside the Munin host** (global opsec
rule).

## 11. Stale KB

**Symptom**: `auto_probe` finds no matchers for a vuln class you know exists,
or `get_payloads` returns no payloads for that category.

**Cause**: KB JSON under
`valravn/mcp-server/src/burpsuite_mcp/knowledge/` is stale.

**Quick diagnosis**:

```python
# In the Burp MCP server runtime (via burp_invoke / resources)
burp_invoke(endpoint="/api/kb/index", method="GET")
# Look at the per-category count and freshness
```

**Refresh**: the KB evolves upstream. If you find a new class, add JSON to
`knowledge/` with `contexts` / `matchers` design. See `valravn/CLAUDE.md`
"Adding new features -> New KB probes".

CI validates JSON well-formedness in
`valravn/mcp-server/src/burpsuite_mcp/knowledge/` in the
`valravn-burp-import` job (see CI section below).

## 12. Save-finding pipeline

**Symptom**: `assess_finding` passes (returns REPORT), but `save_finding`
fails with `chain_with_invalid` or `evidence_endpoint_mismatch`.

**Fix pipeline**:

1. `assess_finding` wins per-question Q1-Q7 validity gates.
2. `save_finding` structurally checks:
   - Each `chain_with[]` anchors a `confirmed` finding (not
     `likely_false_positive` / `stale`).
   - Each `evidence.logger_index` / `proxy_history_index` /
     `reproductions[].logger_index` resolves to a request whose host+path
     matches the finding `endpoint`. Indices pointing to unrelated traffic fail
     with `evidence_endpoint_mismatch`.
3. Re-run `resend_with_modification(index)` to capture the correct
   `logger_index`, or adjust the finding's `endpoint` to match what was
   captured.

**Tip**: `evidence_endpoint_mismatch` is the #1 cause of "Burp annotation
errors, writeup errors, report quote errors" - always write-then-read-back,
never cite request text directly.

---

## Free / free-tier API keys

The CTI layer (`valravn_investigate_*`) is enhanced by external API keys.
They all have a free tier sufficient for triage and shallow investigation.
Configure them in `.env` (gitignored, never commit).

### Recon & intel (CTI layer)

| Provider | Free tier | How to get | Munin env vars |
|---|---|---|---|
| Shodan | 1 query/month, 100 results/query, free account | <https://www.shodan.io/register> | `SHODAN_API_KEY` |
| Censys | 250 queries/month, free personal account | <https://search.censys.io/register> | `CENSYS_API_ID`, `CENSYS_API_SECRET` |
| VirusTotal | 4 queries/min, free account | <https://www.virustotal.com/gui/my-apikey> (register and request API key) | `VT_API_KEY` |
| urlscan.io | 1000 scans/day, free public account | <https://urlscan.io/user/profile> | `URLSCAN_API_KEY` |
| Netlas | 50 queries/month, free account | <https://netlas.io/register> | `NETLAS_API_KEY` |
| LeakIX | Free, registration optional but recommended | <https://leakix.net/register> | `LEAKIX_API_KEY` |
| ZoomEye | Free personal with email signup, monthly quota | <https://www.zoomeye.org/register> | `ZOOMEYE_API_KEY` |
| NVD (NIST NVD) | **Free, no key. Key recommended for 50 req/min rate-limit** | <https://nvd.nist.gov/developers/request-an-api-key> | `NVD_API_KEY` |
| Cloudflare Radar | Free tier, register Cloudflare account | <https://developers.cloudflare.com/radar/> then <https://dash.cloudflare.com/profile/api-tokens> | `CLOUDFLARE_RADAR_TOKEN` |
| crt.sh | **No key required, free** | <https://crt.sh> | - |
| HaveIBeenPwned | Free API for breach-domain lookup, anonymous always free | <https://haveibeenpwned.com/API/Key> | - (used by `valravn_investigate_organization`, domain breach endpoint requires no key) |
| Wayback Machine / Common Crawl | **No key required, free** | <https://web.archive.org/> / <https://commoncrawl.org/> | - |
| RIPEstat | **No key required, free** | <https://stat.ripe.net/> | - |

### DAST & Burp adjuncts

| Tool / Service | Free? | How to get | Notes |
|---|---|---|---|
| Burp Suite Community | **Free forever** | <https://portswigger.net/burp/communitydownload> | No scanner/collaborator. Enough for most of the `burp_*` wrapper |
| Burp Suite Professional | 30-day trial (trial license disables scanner) | <https://portswigger.net/burp/pro/trial> | Trial only. For day-to-day, Community + Munin's typed wrappers |
| Java 21+ (Temurin) | Free, OSS | <https://adoptium.net/temurin/releases/?version=21> | Required for the extension |
| uv | Free, OSS | <https://astral.sh/uv/> | Required for the Burp MCP server |
| CloakBrowser | OSS + license (some premium features) | <https://github.com/CloakHQ/CloakBrowser> | Stealth Chromium. Without a license it works; loses premium fingerprint features |
| subfinder | Free, OSS | `go install -v github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest` (requires Go) | DNS recon. Standalone binaries may fail via the Burp MCP server if Go is not in PATH |
| nuclei | Free, OSS | `go install -v github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest` | Vulnerability scanner |
| katana | Free, OSS | `go install -v github.com/projectdiscovery/katana/cmd/katana@latest` | Crawler |
| httpx (PD) | Free, OSS | `go install -v github.com/projectdiscovery/httpx/cmd/httpx@latest` | Prober |
| Chaos API (PD) | Free, requires signup | <https://cloud.projectdiscovery.io/> | Subdomain DB; export `PDCP_API_KEY` |
| interact.sh | Free, OSS | <https://interact.sh> or self-host | OOB callback |
| webhook.site | Free, no signup | <https://webhook.site> | OOB callback, temporary URL |
| requestcatcher.com | Free, no signup | <https://requestcatcher.com> | OOB callback with custom subdomain |
| HackTricks | **No key required, free** | <https://book.hacktricks.xyz/> | KB used by the Burp MCP server |
| PayloadsAllTheThings | **No key required, free** | <https://github.com/swisskyrepo/PayloadsAllTheThings> | KB payloads reference |
| SecLists | **No key required, free** | <https://github.com/danielmiessler/SecLists> | Wordlists; runtime-detected by `check_recon_tools`, cached in `.valravn-intel/_seclists_path.json` |

### Recommendations to start

1. **Burp Community install, Java 21, uv** - table stakes.
2. **Shodan + Censys + VT + urlscan.io** - cover 80% of `valravn_investigate_ioc`
   and `valravn_search_assets` with these 4 free.
3. **NVD API key** (free, drastically improves CVE lookup rate-limit).
4. **ProjectDiscovery tools + interact.sh** - for DAST scans; pre-installed by
   `./setup.sh`.
5. **CloakBrowser without license** - fine to start; buy a license if you hit
   fingerprint-aware WAFs.

### Setting reminder - shell level

```sh
# .env or shell
export SHODAN_API_KEY=...
export CENSYS_API_ID=...
export CENSYS_API_SECRET=...
export VT_API_KEY=...
export URLSCAN_API_KEY=...
export NETLAS_API_KEY=...
export LEAKIX_API_KEY=...
export ZOOMEYE_API_KEY=...
export NVD_API_KEY=...
export CLOUDFLARE_RADAR_TOKEN=...
# DAST adjuncts
export BURP_API_HOST=127.0.0.1
export BURP_API_PORT=8111
export BURP_API_TIMEOUT=30
export CLOAKBROWSER_LICENSE_KEY=...  # optional
export PDCP_API_KEY=...  # optional
```

## Build the extension

```sh
cd valravn
./build.sh           # builds JAR; prints absolute path
# Or manually: cd burp-extension && mvn package
# Output: valravn/burp-extension/target/valravn-burp-ext-1.0.0.jar
# Load into Burp: Extensions -> Add -> Java
```

If `mvn` fails with a wrong JAVA_HOME:

```sh
export JAVA_HOME=$(dirname $(dirname $(readlink -f $(which javac))))
# or point directly to a Java 21 JDK
```

## Setup automation (do not run locally)

```sh
# Linux/macOS
./setup.sh
# Windows PowerShell
./setup.ps1
# Windows double click
./setup.bat
# Post-install diagnostic
./doctor.sh   # color-coded OK/WARN/FAIL, non-zero exit only if critical missing
```

**Host reminder**: the local host does NOT install Burp/Java/CloakBrowser/nuclei
- the CI runner does. Locally you only code and commit. The `valravn-smoke.yml`
workflow validates everything on the runner.

## CI diagnostic (authoritative)

The `.github/workflows/valravn-smoke.yml` workflow runs two jobs relevant to
the mesh:

- **`valravn` (existing)** - `compileall munin/valravn`, protocol smoke,
  catalog smoke (no external probes), `tests/test_valravn_integration.py`,
  full Munin tests. Pro optional: `probe_external_apis=true` workflow_dispatch
  runs live probes against external providers if the keys are in secrets.
- **`valravn-burp-import` (new, added by this PR)** - `compileall` of the new
  `valravn/mcp-server/src/burpsuite_mcp`, syntactic + AST check of the KB JSON,
  smoke tests of `munin/mcp/tools/burp_tool.py` with the extension expected
  unreachable (resilience check).

Questions or doubts about workflow output: the `munin-cicd` skill reads
workflow run logs and triggers `workflow_dispatch` from the terminal.

## Secrets never committed

Reminder: API keys go to `.env` (gitignored) and for `probe_external_apis=true`
workflow_dispatch they are loaded via **repo Secrets** (not the committed
.env). For full rules see `~/.claude/CLAUDE.md` global secrets discipline.

## Quick references

- README: [`valravn/README.md`](../../valravn/README.md)
- AGENTS: [`valravn/AGENTS.md`](../../valravn/AGENTS.md)
- CLAUDE: [`valravn/CLAUDE.md`](../../valravn/CLAUDE.md)
- Munin wrapper: [`munin/mcp/tools/burp_tool.py`](../../munin/mcp/tools/burp_tool.py)
- Capabilities catalog: [`munin/mcp/capabilities.py`](../../munin/mcp/capabilities.py)
- Soul: [`soul/valravn.md`](../../soul/valravn.md)
- Workflow: [`.github/workflows/valravn-smoke.yml`](../../.github/workflows/valravn-smoke.yml)
- Valravn CTI gateway: [`munin/valravn/`](../../munin/valravn/)
