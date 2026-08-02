# Prompt PR-2 — Defectos release-blocking: cancelación real + guidance lifecycle

> Issue: #18 · Fase 2 · Ola 1 · **Requiere solo `main`**
> Ejecutar en paralelo con `pr-1` y `pr-6`. No toques rutas ajenas (ver abajo).
> Contexto completo: `docs/prompts/issue-18/00-master.md` — léelo primero.

## Alcance de este PR

Arreglar los DOS defectos release-blocking del epic con tests end-to-end. Es trabajo
**de backend Python** (con el mínimo frontend necesario para mostrar el estado real,
SIN redesign). Es el PR de corrección de verdad: todo lo demás se apoya en que esto sea cierto.

## Rutas que SOLO este PR toca (fronteras disjuntas con PR-1 y PR-6)

- `munin/production/**` — endpoints, store, run lifecycle.
- `munin/core/**` — runtime adapter, middleware, supervisor.
- `tests/**` — tests backend.
- `app/src/lib/production-api.ts` — SOLO si hace falta exponer las nuevas llamadas al frontend.
- `app/src/components/**` — SOLO los botones Detach/Cancel del flujo actual y el estado
  de guidance; nada de redesign.

**Prohibido**: tocar `app/src/types/**`, `app/src/extensions/**`, `app/src/fixtures/**`,
`app/src/lib/chat/**`, `app/src/app/api/**`, `docs/` (salvo `changes.md`).
Si PR-1 define schemas que quieras consumir, espera a que esté en main o define tu
propia constante local temporal y reconcilia en el merge.

## Contexto técnico verificado (no re-verifiques, úsalo)

- **Deep Agents 0.7.1** (`.venv`, pyproject `>=0.7.1,<0.8`) — **NO tiene API de cancelación
  de run**. La cancelación real hay que construirla:
  - `store.request_run_cancellation(actor_id, run_id)` YA EXISTE (`munin/production/store.py:1771`):
    marca el run `cancelled` + `cancel_requested_at_ms`, limpia lease (fencing),
    emite evento durable `run.cancelled`, audita. **Pero NO está expuesta por HTTP.**
  - El executor de runs está en `munin/production/chat.py` (lease heartbeat en `:99`,
    abort en `:1026` cuando el cliente desconecta, `TERMINAL_STATES` en `:58`).
  - El stream usa `astream_events(version="v2")` en `munin/core/runtime_adapter.py:454`;
    los `__interrupt__` se manejan en `:206`; `graph_task.cancel()` en `:527-528` (cancela
    el task asyncio del graph).
- **Guidance**: `munin/core/middleware/operator_guidance.py` — `OperatorGuidanceMiddleware`
  drena la cola de guidance SOLO antes de cada llamada al modelo (`wrap_model_call`).
  Hoy "success" = audit persistido (`munin/production/chat.py:1532`, `kind="operator_guidance"`),
  pero NO hay evidencia de que el siguiente request al modelo contuviera el guidance.
  Hooks de middleware Deep Agents: `wrap_model_call` (antes del LLM), `before_tool_call`,
  `after_tool_call`. `ACTIVE_RUN_ID` se comparte vía módulo (`tool_gateway.py:139`,
  `runtime_adapter.py:358`).
- Eventos del backend ya emiten envelopes SSE con `kind`; `translator.ts` los traduce.
  El frontend actual tiene botón "Stop" que llama `stop()` de AI SDK (solo aborta el reader).

## Contenido

### A. Cancelación real (defecto 1)

1. **Endpoint HTTP durable**: `POST /api/chat/{conversation_id}/runs/{run_id}/cancel`
   (o el path que encaje con el router existente en `munin/production/chat.py`):
   - Autenticado (mismo mecanismo que el resto de `/api/chat/*`).
   - Llama a `store.request_run_cancellation(...)` — es **idempotente**: cancelar dos veces
     devuelve el mismo estado sin error; cancelar un run ya terminal responde el estado actual.
   - Respuesta: estado del run tras la cancelación.
2. **El runtime respeta la cancelación**: cuando `request_run_cancellation` marca el run,
   el executor en curso debe:
   - Dejar de encolar nuevos pasos (fencing — ya lo hace el lease; verifica que el check
     de `cancel_requested_at_ms`/estado se respete en el loop de iteración),
   - Emitir `run_state: cancelling` (mientras drena) y `run_state: cancelled` (confirmado)
     en el stream de eventos,
   - Detener el `graph_task` (patrón de `runtime_adapter.py:527-528`),
   - **Nunca** auto-resumir un run cancelado desde recovery (ya es así para `waiting_for_human`;
     asegúralo para `cancelled` — verifica `chat.py` recovery).
3. **Detach vs Cancel en el frontend (mínimo, sin redesign)**:
   - **Detach / Stop viewing**: cierra el stream local (AI SDK `stop()` o equivalente de
     transporte), mantiene el run vivo, muestra estado "detached" con opción de reconectar
     (replay). No llama a ningún endpoint.
   - **Cancel run**: llama al endpoint nuevo; la UI **solo muestra "cancelling"/"cancelled"
     cuando el backend lo confirma en el stream** (nunca optimista). Si la llamada falla,
     muestra el error y mantiene el estado real.
   - Separa visualmente ambos controles (puede ser en el flujo actual, sin redesign).
4. **Tests**:
   - Backend (pytest): el endpoint cancela un run en curso; idempotencia (2ª llamada ok);
   - cancelar run terminal devuelve estado sin error; recovery NO revive runs cancelados;
   - el evento `run_state: cancelled` llega al stream.
   - Frontend (vitest): el botón Cancel llama al endpoint y NO pinta "cancelled" hasta que
     el evento de backend lo confirma; Detach NO llama al endpoint y mantiene el run vivo.

### B. Guidance lifecycle (defecto 2)

1. **Estados durables**:
   ```
   queued -> delivered_to_runtime -> applied_to_model_step
                                 \-> expired / superseded / run_finished_undelivered
   ```
   - `queued`: cuando el operador envía guidance (ya se persiste hoy).
   - `delivered_to_runtime`: cuando `OperatorGuidanceMiddleware` drena la cola en
     `wrap_model_call` (es decir, fue entregado al runtime, no necesariamente al modelo).
   - `applied_to_model_step`: cuando el siguiente model request REALMENTE incluye el
     guidance (verifica que el `HumanMessage(name="operator")` está en los mensajes que
     van al modelo — hook `wrap_model_call` recibe los mensajes antes del LLM: confirma
     la presencia y recién ahí marca applied).
   - Terminales negativos: `expired` (TTL), `superseded` (guidance nuevo reemplaza),
     `run_finished_undelivered` (el run terminó sin que el guidance se aplicara).
2. **Persistencia**: extiende el modelo del store (o la tabla que corresponda) para guardar
   el estado del lifecycle con `updated_at_ms` y la evidencia (por ejemplo, en qué step se
   aplicó). Emite **eventos de lifecycle con IDs estables** en el stream SSE para que la UI
   los reconcilie en el mismo lugar (reusa el `kind` existente `operator_guidance` con
   payload enriquecido, o un `kind` nuevo si el contrato lo exige — documenta la decisión).
3. **UI mínima**: el elemento de guidance actual muestra su estado real (queued /
   delivered / applied / undelivered) leyendo los eventos del stream; nunca "entregado"
   sin la evidencia de `applied_to_model_step`.
4. **Test E2E obligatorio** (el issue lo exige; un unit test de `_inject` NO basta):
   - Camino real (endpoint/BFF-compatible): operador envía guidance → el siguiente request
     al modelo (mock del LLM) contiene `HumanMessage(name="operator")` con el texto →
     el evento marca `applied_to_model_step`.
   - Test del ciclo completo: queued → delivered → applied; y al menos un caso
     `run_finished_undelivered` (guidance enviado, run termina, nunca se aplicó).

### C. Docs

- `changes.md`: entrada de la release con ambos fixes.
- Si el contrato de eventos cambia (`kind` nuevo), documenta en `docs/` (solo si aplica a este PR).

## Criterios de aceptación

- [ ] `POST .../runs/{id}/cancel` autenticado, durable, idempotente, con audit.
- [ ] El runtime deja de encolar pasos y emite `cancelling`→`cancelled` (confirmado).
- [ ] Recovery nunca revive runs cancelados.
- [ ] Detach y Cancel son acciones separadas; la UI nunca afirma cancelación sin confirmación backend.
- [ ] Guidance: lifecycle completo persistido + eventos estables + UI muestra estado real.
- [ ] Test E2E demuestra `HumanMessage(name="operator")` en el siguiente model input.
- [ ] `python -m compileall -q munin tests scripts` y pytest pasan (todo el suite backend).
- [ ] `npm run lint`, `npm run typecheck`, `npm run build`, `npm test` en `app/` (si tocaste frontend).
- [ ] No se tocaron rutas de otros PRs.

## Non-goals

- NO redesign visual (PR-3+).
- NO tocar schemas/renderer registry/fixtures (PR-1).
- NO read-model nuevo (PR-6).
- NO instalar AI Elements.

## Verificación final antes del PR

```bash
python -m compileall -q munin tests scripts
python -m pytest -q
cd app && npm run lint && npm run typecheck && npm run build && npm test
```

Branch: `feat/issue-18-2-cancel-guidance`. PR a `main`. Reporta: endpoints nuevos,
cómo el runtime respeta la cancelación (fencing + detención de iteración), el modelo de
estados de guidance, el test E2E (comando exacto + qué prueba), y qué tocaste en frontend.
