---
name: arsenal
description: Valravn Arsenal workflow for selecting FuzzingLabs security MCP servers on demand
---

# Valravn Arsenal

Valravn Arsenal integrates the 38-server `FuzzingLabs/mcp-security-hub` fleet behind four compact Munin tools. The upstream repository remains separately attributed and pinned; Munin provides stable Valravn aliases rather than copying hundreds of remote tool schemas into every prompt.

## Select before loading tools

1. `valravn_arsenal_status()` — confirm the pinned upstream and local installation state.
2. `valravn_arsenal_list(category='<category>')` — narrow the fleet.
3. `valravn_arsenal_tools(server='<alias>', query='<task>', include_schema=false)` — inspect only one MCP server.
4. Request `include_schema=true` only when the exact remote tool is chosen.
5. `valravn_arsenal_call(..., authorized=true)` only for an explicitly authorised operation.

## Valravn categories

- `recon`: Nmap, Shodan, ProjectDiscovery, WhatWeb, Masscan, ZoomEye, NetworksDB, ExternalAttacker.
- `web`: Nuclei, sqlmap, Nikto, ffuf, WaybackURLs, official Burp wrapper.
- `binary`: radare2, Binwalk, YARA, capa, Ghidra, IDA.
- `cloud`: Trivy, Prowler, RoadRecon.
- `code`: Semgrep.
- `secrets`: Gitleaks.
- `intel`: VirusTotal, OTX.
- `osint`: Maigret, dnstwist.
- `ad`: BloodHound.
- `password`: Hashcat.
- `fuzz`: Boofuzz, Dharma.
- `blockchain`: DAML viewer, Medusa, Solazy.
- `exploit`: SearchSploit.
- `meta`: MCP Scan.

## Bounty-oriented default subset

For web bounty work, start with `recon/projectdiscovery`, `recon/whatweb`, `web/waybackurls`, `web/nuclei`, `web/ffuf`, `secrets/gitleaks`, `code/semgrep`, `recon/externalattacker`, `osint/dnstwist`, and `exploit/searchsploit`.

Do not run every scanner because it exists. Treat Arsenal servers as sensors/executors selected by a hypothesis.

## Evidence contract

Record:

- Valravn alias;
- upstream service name;
- remote tool name;
- arguments after secret redaction;
- result/evidence;
- whether the call was passive or active;
- target scope used to authorise the operation.

Transport errors are capability failures, not target findings.
