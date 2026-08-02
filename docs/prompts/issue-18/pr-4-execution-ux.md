# Prompt PR-4 — Execution UX: tools, terminal, reasoning, agents, HITL

> Issue: #18 · Fase 4 · Ola 2 · **Requiere PR-1 en `main`** (schemas + registry).
> Ejecutar en paralelo con `pr-3` y `pr-5` (fronteras disjuntas, ver abajo).
> Contexto completo: `docs/prompts/issue-18/00-master.md` — léelo primero.

## Alcance de este PR

Reconstruir la UX de ejecución: cómo se muestran las tool calls, el output de comandos
(terminal), el reasoning/actividad, los subagentes/workflows y las peticiones HITL.
Agrupar por identidad estable, contener el output, y hacer que la telemetría no opaque
la respuesta final. **Sin redesign del shell (PR-3) ni del workspace de artifacts (PR-5).**

## Rutas que SOLO este PR toca (fronteras disjuntas con PR-3 y PR-5)

- `app/src/components/chat/blocks/**` — bloques de ejecución existentes (ToolInvocationPart,
  CommandOutputPart, ReasoningPart, HitlRequestPart, SubagentPresencePart, OperationalTracePart,
  ToolHeartbeatPart, GuidancePart, NotePart, HeartbeatPart, ArtifactPart).
- `app/src/components/execution/**` (nuevos si hace falta): terminal, tool lifecycle, agents.
- `app/src/components/ai-elements/tool/**`, `app/src/components/ai-elements/terminal/**`
  (primitivas AI Elements adaptadas que PR-3 no copió — SOLO estas).
- `app/src/lib/**` — SOLO helpers de agrupación/estado de ejecución si es imprescindible.
- `docs/**` — decisiones.

**Prohibido**: tocar `app/src/components/AppShell.tsx` / `shell/**` / `layout/**` (PR-3),
`app/src/components/workspace/**` / `renderers/**` (PR-5), `munin/**`, `tests/**`,
`app/src/extensions/**`, `app/src/types/**` (base de PR-1).

## Contexto técnico verificado (no re-verifiques, úsalo)

- AI SDK UI v4: partes `tool-*` con `state` (por ejemplo `ToolUIPart` con `input`/`output`/
  `state`/`errorText`); AI Elements las renderiza con `Tool`/`ToolHeader`/`ToolContent`/
  `ToolInput`/`ToolOutput`. En este repo las partes de ejecución llegan como envelopes SSE
  tipados y el translator ya las convierte — los `kind` actuales: `tool_intent`, `tool_started`,
  `tool_result`, `tool_failed`, `tool_output`, `tool_heartbeat`, `command_output`,
  `operational_trace`, `subagent_lifecycle`, `human_interrupt`, `provider_reasoning`, `note`.
- **Agrupación por ID estable** (issue: "stable aggregation by command/job/tool ID instead
  of many tiny output cards"): los envelopes llevan `tool_call_id`/`job_id`/`run_id` —
  el bloque de ejecución debe consolidar start→update→end del mismo ID en UNA tarjeta que
  evoluciona, no N tarjetas.
- **Command output**: `command_output` puede ser enorme (nmap, nuclei, sqlmap...). Requisitos
  del issue: sin overflow de página; scroll horizontal contenido; wrap opcional; copy;
  fullscreen terminal; download del transcript. AI Elements tiene `Terminal` (usa
  `ansi-to-react` para ANSI) y `CodeBlock` — adáptalos a Munin.
- **Reasoning**: el provider emite `provider_reasoning` (explicito, observable — NUNCA
  inventar hidden chain-of-thought). AI Elements `Reasoning` colapsable — consolidar partes
  del mismo mensaje.
- **Subagentes/workflows**: los eventos de subagente del backend (`subagent_lifecycle`)
  vienen de Deep Agents (`SubagentStartEvent`/`Complete`/`Error` del stream `custom` v3,
  hoy v2 en `runtime_adapter.py:454` — el backend los traduce; aquí renderizarlos como
  resumen de actividad, no flood del timeline). El issue exige "subagents summarized".
- **HITL**: `human_interrupt` renderiza la petición durable (aprobación/rechazo) — el
  flujo de aprobación ya existe; aquí su UX: visible, colapsable, sin pérdida tras
  refresh (replay), decisión única por petición. `GuidancePart` debe mostrar el estado
  real del lifecycle (queued/delivered/applied/undelivered) — si PR-2 no está en main,
  renderiza el estado que hoy provee el backend y deja el contrato listo.
- Skill `munin-frontend`: dirección de arte, anti-patrones (nada de "fake terminals" ni
  glow decorativo — terminal real solo donde hay output real), mono Geist Mono para
  telemetría, semánticos solo a señales reales.

## Contenido

### 1. Tool lifecycle unificado

1. Un componente que consume el estado por `tool_call_id` (desde el stream o desde el
   store del shell): fases `intent → started → running (heartbeats) → result/failed`.
2. Una tarjeta por tool call que evoluciona: header (tool name + estado + elapsed_ms),
   input colapsable, output colapsable con contenedor seguro.
3. Adapta `Tool`/`ToolHeader`/`ToolContent`/`ToolInput`/`ToolOutput` de AI Elements
   (estilo Munin, tokens del tailwind.config) o refina lo existente si ya cumple.
4. `tool_heartbeat` actualiza la misma tarjeta (elapsed, última señal) — sin tarjetas nuevas.

### 2. Terminal / command output

1. Contenedor de output con: scroll horizontal contenido (o wrap configurable), copy,
   fullscreen (overlay), download del transcript como archivo, y agrupación por
   `job_id`/`command_id` (un comando = un transcript, no N bloques).
2. Adapta `Terminal` de AI Elements (ANSI via `ansi-to-react` — verifica si ya está en
   el repo o añádelo con justificación) o conserva el existente si cumple.
3. **Nunca** overflow horizontal de página: `min-w-0` en ancestros, max-width, overflow-x
   contenido en el contenedor del terminal.

### 3. Reasoning y actividad

1. Reasoning del provider: colapsable (AI Elements `Reasoning` adaptado), consolidado por
   mensaje, con indicador de streaming.
2. `operational_trace`/`note`: actividad operativa compacta, subordinada visualmente a la
   respuesta final (menor jerarquía, colores muted/ice).
3. `provider_reasoning` NUNCA se presenta como cadena de pensamiento oculta: es reasoning
   emitido explícitamente por el provider, etiquetado como tal.

### 4. Subagentes / workflows

1. Eventos `subagent_lifecycle` renderizados como actividad resumida: tarjeta compacta
   (subagente, fase start/complete/error, duración) en lugar de flood de mensajes.
2. Si el backend emite eventos de worker/handoff/fan-out (PR-6 los enriquece), deja el
   render listo para consumirlos (contrato de datos documentado, sin romper por ausencia).

### 5. HITL y guidance

1. `human_interrupt`: petición durable — aprobar/rechazar/responder, colapsable, estado
   resuelto visible, decidir una sola vez (idempotente), sobrevive refresh (replay).
2. `GuidancePart`: estado real del guidance lifecycle (si PR-2 en main: los 4 estados;
   si no: estado actual del backend con contrato listo).

### 6. Docs

- `docs/issue-18-execution-ux.md`: modelos de estado por tool call, agrupación por ID,
  decisiones de terminal (scroll/copy/fullscreen/download), rendering de subagentes/HITL.
- `changes.md`.

## Criterios de aceptación

- [ ] Tool calls agrupadas y reconciliadas por ID estable (sin tarjetas duplicadas).
- [ ] Command output contenido (scroll/wrap/copy/fullscreen/download) sin overflow de página.
- [ ] Reasoning explícito del provider, colapsable, etiquetado correctamente.
- [ ] Subagentes resumidos, no flood.
- [ ] HITL durable, decisión única, sobrevive refresh.
- [ ] Guidance muestra su estado real (listo para PR-2).
- [ ] Jerarquía: respuesta final domina sobre telemetría.
- [ ] `npm run lint`, `npm run typecheck`, `npm run build`, `npm test` pasan.
- [ ] No se tocaron rutas de otros PRs.

## Non-goals

- NO shell/layout (PR-3).
- NO workspace/renderers de artifacts (PR-5).
- NO backend (PR-2/PR-6).
- NO upgrade React/Tailwind.

## Verificación final antes del PR

```bash
cd app && npm run lint && npm run typecheck && npm run build && npm test
```

Branch: `feat/issue-18-4-execution-ux`. PR a `main`. Reporta: el modelo de agrupación por
ID (esquema), decisiones del terminal, cómo renderizas subagentes/HITL/guidance, y capturas
con output real largo (p.ej. un nmap/nuclei) en desktop y mobile.
