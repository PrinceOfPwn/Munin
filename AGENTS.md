# AGENTS.md — Munin

Guía operativa para agentes (opencode Raven-Mind, Claude Code, Codex) que trabajan
en el repositorio `PrinceOfPwn/Munin`.

## TL;DR — Munin se ejecuta en GitHub Actions, no en tu máquina

**CRÍTICO**: El runtime completo de Munin (backend MCP + frontend React + estado
persistente) vive dentro de **GitHub Actions**. No arranques el MCP server ni
pytest localmente salvo orden explícita del operador.

| Workflow `.github/workflows/` | Dispara | Qué hace |
|---|---|---|
| `ci.yml` | push a `main`/`agent/**`/`codex/**`, PRs, manual | Backend (compileall + pytest + Turso online) + Frontend (`next build`, `next lint`). 20/15 min timeout. |
| `live-session.yml` | manual/dispatch | Arranca el runtime unificado `munin serve` en `:8787`, restaura estado (artifact SQLite o Turso/libsql), abre tunnel para el frontend React. Crea dirs de home escribibles + `reports/`/`evidence/`. |
| `reset-turso-state.yml` | manual (confirmación `WIPE_MUNIN_TURSO`) | Reset total del estado Turso: limpia **todas** las tablas operacionales (descubiertas dinámicamente, incluye producción/autonomía), preserva `schema_migrations`. |
| `valravn-smoke.yml` | paths `munin/valravn/**` + `soul/valravn.md` + tests valravn, manual | Compile + protocol smoke + tests de la mesh Valravn; probes opcionales de APIs externas/browser. |

**Estado** (`data/shared_state.sqlite` WAL) sobrevive a la muerte del runner
entre las 3 capas: Soul (`soul/*.md`), Forged tools (`munin/generated/`),
Memoria/working state (artifact o Turso). Ver `ARCHITECTURE.md`.

## Cómo verificar ejecución — usa el GitHub MCP o `gh`

No adivines el estado de CI/CD. Úsa:

- **MCP `github`** (configurado en `.opencode/opencode.json`) para listar runs,
  check runs, issues, PRs, releases.
- **`gh`** local (`gh run list`, `gh pr checks <n>`, `gh issue list`, `gh pr view <n>`).
  El operador está autenticado en `github.com`.

Reportar estado con IDs reales: run_id, PR number, commit SHA, workflow name.

## Estructura del repo

```
app/                 Frontend Next.js 14 (React 18 + Tailwind 3.4 + Radix + TanStack Query + zustand)
  src/app/           globals.css, page.tsx
  src/components/    FlightDeckStable, Providers, ...
  src/lib/           queries.ts, query-cache.ts, useConversationEvents.ts
  tailwind.config.ts PALETA COMPLETA — todos los hex viven aquí
  public/raven-mark.png  asset de marca (reusar antes de crear otro)
munin/               Backend Python — FastMCP server (unificado en `munin serve` :8787)
  core/              llm_client, llm_stream, prompting (contratos chino-first), autonomy (modos)
  mcp/tools/         audit, opsec, LDAP, Forge, Memory, Recon, Intel
  mcp/persistence.py SQLite/Turso abstraction
  production/        Production Suite (store, chat, asgi, timers, agents)
  valravn/           mesh de recon externa (IOC/CVE/assets/wayback)
soul/                identidad, doctrina y principios (merge vía soul-proposal PRs)
data/shared_state.sqlite  estado WAL (artifact o Turso)
.github/workflows/   ci + live-session + reset-turso + valravn-smoke
docs/  specs/  reports/  evidence/  intel/  knowledge_sync/  templates/
```

## Reglas de arte y código

- **No rehagas la rueda.** Inspecciona antes de escribir nuevo CSS/componentes.
- Paleta y tokens en `app/tailwind.config.ts` (vía Tailwind utilities, nunca hex).
- Acento único violeta `#7c3aed`; semánticos solo para señales reales.
- Tipografía: Inter/Geist (body); Geist Mono (telemetría/código).
- Motivo raven: reusa `app/public/raven-mark.png`. No skulls/locks/matrix-rain.
- Ver `ARCHITECTURE.md`, `MAP.md`, y la skill `munin-frontend` para detalle.

## Lint / build / tests

Ejecutar en el runner via CI, no localmente salvo petición explícita:

- Frontend: `npm run lint`, `npm run build` (dentro de `app/`).
- Backend: `python -m compileall -q munin tests scripts`, `python -m pytest -q`.
- Ruff para estilo Python (viene en `pyproject.toml`).

## Sincronización del remoto

1. `git fetch origin --prune`.
2. `git pull` la rama base (`main` por defecto).
3. Ramas nuevas `feat/*` o `fix/*` a consolidar → rama `raven-mind/sync-*` +
   `git merge origin/<rama>` + PR. Nunca push directo a `main` sin PR.

## Documentación

Cualquier cambio no trivial se documenta en `changes.md` (formato histórico del
repo). Si aplica, actualiza también `README.md` y este `AGENTS.md`.
