# changes.md — living changelog + hand-off log

Raven Mind (GLM-5.2) audita cada cambio no trivial. Formato: una entrada por cambio con
responsable, fecha, alcance, justificación y efectos en docs.

## 2026-07-30 — issue #9 final state (post-step8)

**Responsable:** Raven Mind (GLM-5.2).
**Rama:** `raven-mind/migration-issue9/final-state` (vs `origin/main`).
**Tipo:** snapshot de estado final de la migración issue #9 (Deep Agents + LangGraph +
Vercel AI SDK).

### Origen del snapshot
Aplicado desde `samples-vt.zip` provisto por el operador. El zip contiene el estado
post-step8: ya con la legacy glue removida (steps 8.1 / 8.4 / 8.5 / 8.6 ejecutados).

### Lo que aterriza en este cambio
- **+79 archivos nuevos** — toda la infraestructura de la migración:
  - `munin/core/supervisor.py`, `runtime_adapter.py`, `tool_gateway.py`
  - `munin/core/autonomy/` (agent_registry, subagent_factory, tool_factory,
    workflow_factory, workflow_registry, spec, workflow_spec)
  - `munin/core/coordination/` (swarm, handoff_tools)
  - `munin/core/middleware/` (operator_guidance, progress_emit, repetition_guard)
  - `munin/core/parallel/send_workers.py`
  - `langgraph.json` + `scripts/langgraph_*` (LangGraph server deployment)
  - Frontend AI SDK transport: `app/src/app/api/chat/[[...path]]/route.ts`,
    `app/src/lib/aiChat.ts`, `app/src/lib/partsRenderers.ts`, 9 message-part renderers
    bajo `app/src/components/chat/blocks/parts/`, 3 suites de tests frontend
    (`partsRenderers`, `reconnect`, `sseToParts`), `app/postcss.config.mjs`,
    `app/vitest.config.ts`
  - 30+ tests de characterization nuevos bajo `tests/characterization/`
- **+52 archivos modificados** — adaptaciones backend/frontend a la nueva
  arquitectura (mcp, production, subagents, docs, soul, scripts, workflows CI/live-session,
  pyproject.toml, package.json, tailwind.config.ts, tsconfig.json, etc.).
- **-63 archivos borrados** — legacy glue del step 8:
  - `munin/core/munin_agent.py`, `orchestrator.py`, `conversations.py`, `prompting.py`,
    `execution_progress.py` (step 8.1 — `respond()` y callers)
  - `munin/subagents/process_control.py`, `tool_forge.py`, `graph_forge.py`,
    `ldap_agent.py` (step 8.2 — runner subprocess entry / forge legacies)
  - `munin/mcp/persistence.py`, `git_persist.py`, `graph_persist.py`,
    `capabilities.py` y tools asociadas (step 8.4 / 8.5 — adaptadores de store v3.1)
  - `munin/forge/*`, `munin/integrations/*`, `munin/rag/*` (orquestación legacy)
  - Frontend legacy: `app/src/components/Chat.tsx`, `app/src/lib/mcp.ts`,
    `app/src/types/mcp.ts`, `app/src/app/mcp/[[...path]]/route.ts`,
    `app/postcss.config.js` (renombrado a `.mjs`)
  - 14 tests legacy de los módulos borrados
  - Docs legacy: `ARCHITECTURE.md`, `CHANGELOG.md`, `Dockerfile`, `docs/*` legacy,
    `labs/*`, `scripts/ci_live_smoke.py`, `scripts/ldap_seed/*`,
    `scripts/reset_turso_state.py`, `scripts/turso_smoke.py`,
    `.github/workflows/reset-turso-state.yml`

### Volumen del cambio
194 archivos: +8458 / -19923 líneas (neto ≈ -11k, consistente con la eliminación de
la legacy glue).

### Documentación afectada
- `changes.md` — creado en este cambio (no existía; CLAUDE.md lo pide como living
  changelog).
- `README.md`, `MAP.md`, `docs/architecture.md`, `docs/llm-providers.md`,
  `docs/security-notes.md`, `docs/tools_reference.md` — actualizados desde el zip
  (reflejan el estado final post-step8).
- `ARCHITECTURE.md`, `CHANGELOG.md` y 7 docs legacy — borrados (contenido absorbido
  por `docs/architecture.md` y `changes.md` en el nuevo modelo).

### Notas de advertencia / deuda abierta
1. **Salto directo al estado final vs roadmap incremental.** El roadmap
   `IMPLEMENTATION_ROADMAP.md` define PRs PR-02..PR-16 con parity gates entre cada uno.
   Este cambio aplica el snapshot final en un solo PR, saltándose la validación de
   paridad incremental. El roadmap §385 y step 8 §336 prohíben mezclar introducción
   de reemplazo con eliminación de glue; el operador autorizó explícitamente el gran
   PR (decisión registrada en la conversación).
2. **No hay verificación de `next build` ni pytest en el host.** Per CLAUDE.md, los
   tests/backend corren en GitHub Actions, no en este Windows host. La validación
   recae en CI/CD del PR. Si `next build` o pytest fallan por imports rotos en los
   componentes frontend que conservamos (32 archivos: `LeftSidebar.tsx`, etc.) que
   antes importaban de `lib/mcp.ts` (borrado) o `Chat.tsx` (borrado), los fallbacks
   o adaptaciones deberán emparcharse en este PR o fixups posteriores.
3. **La rama `raven-mind/migration-issue9/pr-02-ai-sdk-transport` previa queda
   obsoleta.** Su contenido está absorbedo en este estado final; el operador puede
   cerrar esa rama/PR conflicto.
4. **Issues #10 (PR-01 parity) ya mergedado.** Los tests de characterization del step
   1 están en `tests/characterization/` (14 archivos, 7 MOD + 7+ NEW) cubiertos en
   este snapshot.

### Hand-off log
- [Raven Mind → operador] Snapshot aplicado; rama lista para push + PR.
- Pendiente operador: revisar diff, push, abrir PR, autorizar CI, decidir qué hacer
  con la rama `pr-02-ai-sdk-transport` anterior.
