# Prompt PR-6 — Backend read-model / event enrichment

> Issue: #18 · Fase 6 · Ola 1 (con PR-1, PR-2) · **Requiere solo `main`** (modulo lo que PR-2 defina de cancelación).
> Ejecutar en paralelo con `pr-1` y `pr-2` (fronteras disjuntas, ver abajo).
> Contexto completo: `docs/prompts/issue-18/00-master.md` — léelo primero.

## Alcance de este PR

Enriquecer el read-model/event del backend para que el workspace (PR-5), la ejecución
(PR-4) y la conversación tengan datos estructurados, versionados y estables que
reconcilien lifecycle en el frontend. Es trabajo **100% backend Python** (con el contrato
de envelopes que el frontend ya consume), sin tocar UI.

## Rutas que SOLO este PR toca (fronteras disjuntas con PR-1 y PR-2)

- `munin/core/runtime_adapter.py` — emitir eventos enriquecidos (subagentes, workers,
  handoff, fan-out, artifacts, evidence).
- `munin/production/store.py` — read-model para artifacts/evidence/runs, versiones.
- `munin/production/asgi.py` / `munin/production/chat.py` — endpoints read-only del run
  (detalle de lifecycle, tools, activities, agents, approvals, guidance, artifacts, summaries).
- `munin/core/autonomy/**` — exponer datos de subagentes/workflows (presencia, handoff,
  fan-out) como envelopes estables.
- `app/src/lib/production-api.ts` — SOLO tipos/campos del read-model (sin UI); si PR-1
  define el schema de UI, reconcilia el envelope con ese schema.
- `tests/**` — tests backend del read-model/event enrichment.

**Prohibido**: tocar `app/src/types/**`, `app/src/extensions/**`, `app/src/fixtures/**`
(PR-1), `munin/core/middleware/operator_guidance.py` y `munin/core/tool_gateway.py` salvo
lo imprescindible si entra en conflicto con PR-2 (negocia con PR-2), `app/src/components/**`,
`app/src/app/api/**` (BFF — si necesita cambios, anótalo y coordina).

## Contexto técnico verificado (no re-verifiques, úsalo)

- Backend ya emite envelopes SSE tipados con `{ kind, ...payload }`; `runtime_adapter.py:454`
  usa `astream_events(version="v2")`; `__interrupt__` en `:206`; `graph_task.cancel()` en `:527`.
- `kind`s actuales (consumidos por `translator.ts`): `assistant_text`, `provider_reasoning`,
  `tool_intent`, `tool_started`, `tool_result`, `tool_failed`, `tool_output`, `tool_heartbeat`,
  `run_state`, `human_interrupt`, `operator_guidance`, `artifact`, `subagent_lifecycle`,
  `command_output`, `operational_trace`, `note`, `heartbeat`.
- Deep Agents 0.7.1: stream `custom` (v3) emite `SubagentStartEvent`/`SubagentCompleteEvent`/
  `SubagentErrorEvent` con `{id, type:"subagent", phase, eval_id, subagent_type, label,
  description, duration_ms}`; el repo usa v2 — puede que estos eventos hayan que
  proyectarlos/exponerlos en v2, o migrar a v3 (decisión documentada); los handoffs de
  `langgraph-swarm` generan eventos de transferencia que traducir a `subagent_lifecycle`.
- `munin/core/autonomy/` tiene `subagent_factory`, `workflow_factory`, `swarm`, `handoff_tools`,
  `agent_registry`, `workflow_registry` — los estados y mensajes inter-agente existen
  (`agent_presence`, `agent_messages`, `active_tasks` en el store); el enriquecimiento debe
  emitirlos como envelopes estables con `id` para que el frontend reconcilie.
- `store.py` ya persiste `messages`, `agent_runs`, `run_events`, `tool_calls`,
  `human_requests`, `audit_events`, `goals`, `todo_events`, `timers`, `operation_snapshots`/
  `branches`, `run_guidance_queue`, `conversation_broadcasts`, `workflow_registry`,
  `agent_registry` (ver `MAP.md`/`ARCHITECTURE.md`).
- Issue exige: "versioned, schema-validated Munin UI part types; stable IDs that reconcile
  lifecycle updates in place; rich artifact metadata (filename, size, language, renderer,
  version, provenance, preview/download URLs); conversation/run artifact list; intentional
  read-only run detail; worker/workflow/handoff/fan-out events; structured sources/evidence".

## Contenido

### 1. Event enrichment: subagentes/workers/handoff/fan-out

1. Emitir envelopes estables con `id` y `phase` para subagentes: start/complete/error (desde
  Deep Agents `custom` si migras a v3, o desde hooks del swarm si te quedas en v2 — decide
  y documenta por qué).
2. Handoff/fan-out (`langgraph-swarm`): evento del paso de control entre subagentes con
  `from_agent`, `to_agent`, `task_id`, `payload resumido`.
3. Workers/workflows: lifecycle del workflow (start/progress/complete/error) con `workflow_id`
  estable y fan-out (`child_workflow_ids`).
4. IDs estables en TODOS los envelopes con lifecycle: el mismo `id` se actualiza, no se duplica.

### 2. Read-model de artifacts y evidence

1. Endpoints read-only que devuelven:
   - `GET /api/chat/{id}/artifacts` — lista con metadata rica (filename, size, language,
     renderer, version, provenance, preview_url, download_url).
   - `GET /api/chat/{id}/evidence` — evidencia estructurada (fuente, tipo, time, tool_id).
   - `GET /api/chat/{id}/runs/{run_id}` — detalle read-only: lifecycle, tools, activities,
     commands, agents, approvals, guidance, artifacts, summaries.
2. Versión del artifact: cuando un artifact se actualiza (regenerado, editado), crea una
   nueva versión preservando la anterior; el read-model lista versiones y permite
   seleccionar una. (Tabla nueva o columna `version` — documenta.)
3. Provenance: cada artifact/evidence lleva un árbol de procedencia (de qué tool/run/paso
   salió, y si se promovió desde evidence, el ID del evidence origen).

### 3. Contrato de partes que el frontend consume

1. Revisa que cada envelope enriquecido encaje con el schema `munin-ui/*` que PR-1 define
   (espera a PR-1 en main o concilia durante el merge). Si introduces un `kind` nuevo, lo
   defines en el store Y lo documentas para que PR-1 lo añada a su Zod schema.
2. **Idempotencia**: un miso `id` reenviado reemplaza/actualiza el anterior (replay friendly;
   el frontend ya hace replay — ver `ARCHITECTURE.md` invariants).

### 4. Tests

- pytest: el read-model devuelve artifacts/evidence/run con metadata rica; versiones
  preservan la anterior; provenance correcto; eventos de subagente/workflow emiten con `id`
  estable y se reconcilian en replay (re-stream produce el mismo `id`).
- Test del contrato: emits con `id` duplicado no duplican, actualizan.

### 5. Docs

- `docs/issue-18-read-model.md`: modelo de datos del read-model, los `kind`s enriquecidos,
  endpoints read-only, y la政策的 de versiones/provenance.
- `changes.md`.

## Criterios de aceptación

- [ ] Envelopes estables para subagentes/workflows/handoff/fan-out con `id` reconciliable.
- [ ] Read-model de artifacts con metadata rica + versiones + provenance.
- [ ] Endpoints read-only del run (lifecycle/tools/agents/approvals/guidance/artifacts/summaries).
- [ ] Replay-friendly (re-stream reutiliza `id`s).
- [ ] Envelopes encajan con el schema `munin-ui/*` (o reconcilia con PR-1).
- [ ] `python -m compileall -q munin tests scripts` y pytest pasan.
- [ ] `npm run lint`, `npm run typecheck`, `npm run build`, `npm test` en `app/` si tocaste frontend tipos.
- [ ] No se tocaron rutas de otros PRs.

## Non-goals

- NO tocar middleware de guidance/cancelación (PR-2).
- NO definir schemas Zod del frontend (PR-1).
- NO construir UI (PR-3/4/5).
- NO migrar el runtime Python a TS ni crear segunda fuente de verdad de runs.

## Verificación final antes del PR

```bash
python -m compileall -q munin tests scripts
python -m pytest -q
cd app && npm run lint && npm run typecheck && npm run build && npm test
```

Branch: `feat/issue-18-6-backend-read-model`. PR a `main`. Reporta: `kind`s enriquecidos
(tabla), endpoints read-only, decisión v2 vs v3 del stream de subagentes, modelo de
versiones/provenance, y el test de replay (comando + qué prueba).
