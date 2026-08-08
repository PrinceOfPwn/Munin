# Working on Munin

This guide is for coding agents and contributors working on `PrinceOfPwn/Munin`.
It describes the v1.1.0 repository and runtime; it is not an instruction to use
the CTF-oriented prompts in `soul/` as the default personality.

## Project contract

Munin is a durable, operator-governed runtime for autonomous security
operations. The server owns identity, policy, approvals, state and capability
composition. The Web GUI, MCP and Discord are control surfaces over that same
runtime.

The configuration tested and verified for **v1.1.0** is:

- Discord community adapter (stable operator surface)
- GitHub Actions execution environment
- DeepSeek V4-Flash Free (via OpenCode Zen)

The Web GUI remains the target long-term interface, but live-session testing
uncovered frontend bugs that have not yet passed the full repair loop; treat
GUI-only claims as unverified until they do. Other combinations may work, but
are not verified unless documented.

## Repository map

```text
app/                    Next.js 15 / React 18 / TypeScript Web GUI
munin/                  Python 3.11+ backend and runtime
  core/                 prompting, model integration and autonomy contracts
  production/           ASGI API, durable chat, runs, events and recovery
  mcp/                  MCP transport and native capabilities
    tools/burp_tool.py  resilient HTTP bridge to the Valravn Burp extension (DAST)
  valravn/              external reconnaissance and CTI mesh (passive)
valravn/                Valravn agentic Burp DAST surface (active)
  burp-extension/       Java 21+ Burp extension (REST API at 127.0.0.1:8111)
  mcp-server/           Python 3.11+ MCP server (package burpsuite_mcp, stdio)
  .claude/              agent/skill/rule prompts for the Burp DAST surface
  docs/                 Valravn design specs, plans and reviews
  knowledge/            151 JSON files driving the adaptive scan engine
soul/                   optional, human-governed persona packages
scripts/                operational and maintenance scripts
docs/                   canonical references and multilingual handbooks
tests/                  backend and integration tests
.opencode/skills/       opencode-native skills loaded by the editing agent
  valravn-diagnostic/   failure-mode runbook, free-tier API keys and fixes
.github/workflows/       CI and operational runner workflows
```

The Valravn mesh has two layers — `munin/valravn/` is the passive CTI gateway
(Python, no Burp / Java / CloakBrowser needed at runtime); `valravn/` is the
active Burp DAST surface (Burp Suite + Java 21 + uv). The Burp layer is
optional at runtime: `munin/mcp/tools/burp_tool.py` is a resilient HTTP bridge
that returns a structured `ok=False` envelope when the Burp extension is
unreachable, so Munin keeps running in CI and dev hosts without Burp.

## Engineering rules

1. Read live code before trusting a static capability list.
2. Keep authority server-side; UI, skills and prompts cannot bypass policy.
3. Preserve the separation between checkpoints, events and artifacts.
4. Treat `soul/` as optional characterization. The bundled profile is for CTFs
   and controlled labs, not the recommended production default.
5. Do not claim provider or deployment compatibility without a verified test.
6. Update documentation whenever behavior, storage, interfaces or workflows change.

## Validation

```bash
poetry run pytest
cd app && npm run build
# Valravn Burp surface smoke (no Burp install needed -- resilience probe only):
#   gh workflow run "Valravn Smoke" --ref <branch>
#   gh run watch
```

The `valravn-smoke` workflow runs two jobs: `valravn` covers the passive CTI
gateway (`munin/valravn/`) and `valravn-burp-import` covers the active Burp
surface (`valravn/` + `munin/mcp/tools/burp_tool.py`) without installing Burp,
Java or CloakBrowser — it validates Python AST, JSON knowledge files,
`skill.json` shape, the Apache-2.0 §4(d) attribution retention in `valravn/NOTICE`,
and that the `burp_tool` wrapper degrades gracefully when the Burp extension is
unreachable.

For Burp runtime failures and how to obtain free-tier API keys for the CTI
providers, see the [`valravn-diagnostic` skill](.opencode/skills/valravn-diagnostic/SKILL.md).

CI is the authoritative integration environment. Local tests remain useful, but
the verified v1.1.0 path is the Discord adapter on GitHub Actions with DeepSeek V4-Flash Free (via OpenCode Zen).

Start at [docs/README.md](docs/README.md), [ARCHITECTURE.md](ARCHITECTURE.md)
and [MAP.md](MAP.md).
