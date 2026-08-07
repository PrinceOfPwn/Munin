# Valravn Talons and Arsenal — third-party notices

Valravn uses stable internal aliases for operator ergonomics, but those aliases do not replace or obscure upstream authorship.

## FuzzingLabs/mcp-security-hub

- Upstream: `FuzzingLabs/mcp-security-hub`
- Pinned revision: `b6800740da9965e9dd3fde2ec3cf4c775c358f72`
- License: MIT
- Copyright notice: Copyright (c) 2025 Fuzzing Labs
- Integration model: external checkout under gitignored `valravn/upstreams/mcp-security-hub`; Munin stores only the mapping/adapter code.

The upstream MIT copyright and permission notice must remain with copies or substantial portions of that software.

## 3ntr0pyX/burp-mcp-ultimate

- Upstream: `3ntr0pyX/burp-mcp-ultimate`
- Pinned revision used by CI/bootstrap: `1c2ffc541e15d7fcd45d750485e23b979e875295`
- License: MIT
- Copyright notice: Copyright (c) 2026 Ashton Vaughan
- Integration model: external Burp extension/provider; no source from the provider is vendored into Munin by this change.

The upstream MIT copyright and permission notice must remain with copies or substantial portions of that software.

## vvvvvvvvvvel/burp-awesome-mcp

- Upstream: `vvvvvvvvvvel/burp-awesome-mcp`
- Pinned revision used by bootstrap: `4d6b8c1aaccaf56e383430790fa67c463f83d72f` (`v1.1.1`)
- License: MIT at the time this integration was authored.
- Integration model: external Burp provider. Valravn adopts the provider's agent-friendly interaction pattern (stable IDs, list/get separation, pagination and output projection) without copying its implementation into Munin.

## PortSwigger/mcp-server

- Upstream: `PortSwigger/mcp-server`
- License: GPL-3.0
- Integration model: external, separately installed MCP/stdio proxy only. GPL source is **not vendored, copied or relicensed** into Munin/Valravn by this change.

## Naming

Names such as `valravn-ultimate`, `valravn-awesome`, `valravn-official` and `Valravn Arsenal` are local capability aliases. Runtime status and tool results retain the corresponding upstream repository/service identity so evidence and diagnostics preserve provenance.
