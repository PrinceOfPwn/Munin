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
- MiMo V2.5

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
  valravn/              external reconnaissance and CTI mesh
soul/                   optional, human-governed persona packages
scripts/                operational and maintenance scripts
docs/                   canonical references and multilingual handbooks
tests/                  backend and integration tests
.github/workflows/       CI and operational runner workflows
```

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
```

CI is the authoritative integration environment. Local tests remain useful, but
the verified v1.1.0 path is the Discord adapter on GitHub Actions with MiMo V2.5.

Start at [docs/README.md](docs/README.md), [ARCHITECTURE.md](ARCHITECTURE.md)
and [MAP.md](MAP.md).
