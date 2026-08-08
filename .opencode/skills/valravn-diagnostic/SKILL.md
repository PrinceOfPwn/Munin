---
name: valravn-diagnostic
description: Diagnose Valravn Talons, Burp MCP Ultimate, unattended Burp startup, Arsenal providers, and passive Valravn intelligence sources.
tags: [valravn, diagnostic, troubleshooting, burp-suite, mcp, ultimate, arsenal, osint, dast, runbook]
---

# Valravn diagnostic

Valravn now has three independent surfaces:

1. passive intelligence in `munin/valravn/`;
2. Burp execution through `valravn_talons_*` -> `burp-mcp-ultimate`;
3. external security tooling through `valravn_arsenal_*` -> FuzzingLabs Security Hub.

The retired Java REST extension and `burp_tool.py` wrapper are not part of the runtime. Port `8111` is no longer a Valravn dependency.

## Fast triage

| Symptom | Check first |
|---|---|
| `valravn_talons_status` shows Ultimate unreachable | Burp process, Ultimate extension load, MCP `9444` |
| Ultimate reachable but tool missing | `valravn_talons_tools(..., refresh=true)` and current schema |
| Active Talons call denied | explicit engagement authorization / `authorized=true` |
| Proxy traffic hangs | set Ultimate intercept mode to `observe`; check Burp Proxy `8080` |
| Juice Shop live CI fails before Burp | provider build or Burp download/checksum |
| Juice Shop live CI fails after Burp starts | inspect uploaded Burp log and MCP catalog |
| Arsenal server missing | `valravn_arsenal_status` / pinned Security Hub mapping |
| passive intelligence source missing | provider-specific API key / network availability |

## 1. Check Talons

Call:

```text
valravn_talons_status(refresh=true)
```

Expected primary provider:

```text
valravn-ultimate
```

Default endpoint:

```text
http://127.0.0.1:9444/mcp
```

Useful environment variables:

```sh
VALRAVN_TALON_ULTIMATE_URL=http://127.0.0.1:9444/mcp
BURP_MCP_TOKEN=<optional local bearer token>
```

A provider transport error is not evidence about the target.

## 2. Check the live provider catalog

Never debug a guessed tool name for long. Ask the provider:

```text
valravn_talons_tools(
  provider="valravn-ultimate",
  query="proxy",
  include_schema=false,
  refresh=true
)
```

Then request the schema only for the selected operation.

The pinned CI provider is expected to expose more than 100 tools and include core operations such as `burp_version`, `http_send_raw`, and `intercept_set_mode`.

## 3. Burp is not running

For unattended hosts and CI, do not manually add the extension. Build the pinned Ultimate shadow JAR and run:

```sh
BURP_ULTIMATE_JAR=/absolute/path/to/burp-mcp-ultimate-0.2.0.jar \
  valravn/scripts/start-burp-headless.sh
```

The launcher downloads the pinned Burp Desktop JAR, verifies its checksum and JAR shape, writes the user config that preloads Ultimate, starts Burp, and waits for a real MCP catalog.

When no display is available the launcher expects `xvfb-run`; CI installs Xvfb explicitly.

## 4. Burp starts but Ultimate does not

Inspect the runtime log:

```text
$BURP_HOME/burp.log
```

Check:

- Java 21+ is available;
- `BURP_ULTIMATE_JAR` exists and is the built shadow JAR;
- the generated Burp user config points to the absolute JAR path;
- no other process owns `127.0.0.1:9444`;
- `BURP_MCP_TOKEN` is identical in Burp and Munin when enabled.

Do **not** troubleshoot `127.0.0.1:8111`; that endpoint was retired.

## 5. Proxy traffic blocks

Burp's Proxy listener is separate from the MCP endpoint:

```text
MCP:   127.0.0.1:9444
Proxy: 127.0.0.1:8080 (normal default)
```

For unattended tests discover/call `intercept_set_mode` and use the provider-supported observation mode. The pinned integration uses:

```json
{"mode": "observe"}
```

This keeps Proxy traffic flowing while still recording state.

## 6. Read evidence back from Burp

Prefer resources for passive state:

```text
valravn_talons_read("burp://proxy/history", provider="valravn-ultimate")
valravn_talons_read("burp://sitemap", provider="valravn-ultimate")
valravn_talons_read("burp://target_summary", provider="valravn-ultimate")
```

If a live integration claims traffic passed through Burp, require evidence in Burp state rather than accepting only the target HTTP response.

## 7. Community vs Professional

The provider can run in Community or Professional, but Burp-native capabilities still depend on edition/features. Scanner and Collaborator are examples of features that may be unavailable in Community.

Handle absence explicitly. Do not silently substitute a different test and call it equivalent.

## 8. Arsenal diagnostics

Start with:

```text
valravn_arsenal_status()
valravn_arsenal_list(available_only=true)
```

Then inspect only the relevant server:

```text
valravn_arsenal_tools(server="web/nuclei", query="template")
```

The mapping manifest is `valravn/arsenal/security_hub.json`; CI validates all 38 mapped servers against the pinned upstream Security Hub revision.

## 9. Passive intelligence providers

Passive Valravn reconnaissance can use public/free-tier providers when configured. Common optional environment variables include:

- `SHODAN_API_KEY`
- `VT_API_KEY`
- `URLSCAN_API_KEY`
- `NETLAS_API_KEY`
- `LEAKIX_API_KEY`
- `ZOOMEYE_API_KEY`
- `NVD_API_KEY`
- `CENSYS_API_ID` / `CENSYS_API_SECRET`
- `CLOUDFLARE_RADAR_TOKEN`

Missing optional keys should degrade the corresponding source, not break the entire Valravn run. Never commit keys into the repository.

## 10. Knowledge / skill issues

The knowledge corpus remains under:

```text
valravn/mcp-server/src/burpsuite_mcp/knowledge/
```

The active playbooks remain under:

```text
valravn/.claude/skills/
```

If a retained skill references a legacy custom MCP tool, migrate the **intent** to a current Ultimate or Arsenal operation. See `valravn/MIGRATION.md` and the `burp-workflow` skill. Do not restore the old REST bridge for compatibility.

## 11. CI is authoritative

Two workflows matter:

- `Valravn Smoke`: validates Munin runtime plus preserved skills/agents/knowledge assets and asserts the retired Burp bridge stays absent.
- `Valravn Mesh E2E`: validates Talons/Arsenal protocol behavior plus a real Burp MCP Ultimate -> OWASP Juice Shop integration.

The live Burp job must build the pinned Ultimate provider, preload it without manual interaction, negotiate MCP, call the real provider, traverse the Burp Proxy listener, and read the marked request back from `burp://proxy/history`.
