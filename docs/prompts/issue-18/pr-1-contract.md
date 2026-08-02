# Prompt PR-1 — Contrato UX: schemas versionados, renderer registry, fixture gallery

> Issue: #18 · Fase 1 (parte frontend) · Ola 1 · **Requiere solo `main`**
> Ejecutar en paralelo con `pr-2` y `pr-6`. No toques rutas ajenas (ver abajo).
> Contexto completo: `docs/prompts/issue-18/00-master.md` — léelo primero.

## Alcance de este PR

Sentar el contrato de datos y seguridad del redesign, **sin redesign visual masivo**
(eso es PR-3+). Este PR es la base sobre la que construyen PR-3, PR-4 y PR-5.

## Rutas que SOLO este PR toca (fronteras disjuntas con PR-2 y PR-6)

- `app/src/types/**` — schemas y tipos de partes de UI.
- `app/src/extensions/**` — renderer registry.
- `app/src/fixtures/**` — fixture gallery (nuevo).
- `docs/**` — documentación de contrato (solo si aplica).

**Prohibido**: tocar `munin/**`, `tests/**`, `app/src/components/**`, `app/src/lib/chat/**`,
`app/src/app/api/**`, `app/src/lib/production-api.ts`. Si necesitas algo de ahí, define
la interfaz y déjalo para el PR que le toca.

## Contexto técnico verificado (no re-verifiques, úsalo)

- AI SDK UI v4 (`ai@7.0.47`, `@ai-sdk/react@4.0.50`): `useChat<MyUIMessage>()` acepta un
  tipo de mensaje custom; `messages[].parts[]` con `type: 'text' | 'reasoning' | 'tool-*' | 'file' | 'data-*'`.
- El backend ya emite envelopes SSE tipados; el BFF (`app/src/app/api/chat/[[...path]]/route.ts`)
  los traduce con `app/src/lib/chat/translator.ts` a `UIMessageChunk`.
- `app/src/extensions/registry.tsx` existe: revísalo y adáptalo, no lo reemplaces a ciegas.
- Zod 3 está en el repo. `app/src/types/mcp.ts` tiene tipos actuales del backend.

## Contenido

### 1. Partes de UI de Munin versionadas y validadas (Zod)

1. Define en `app/src/types/` un modelo de partes Munin (`munin-ui/*`):
   - Cada parte: `{ kind, version, id (estable), payload }`.
   - Tipos iniciales (los que el frontend ya consume hoy): `assistant_text`, `provider_reasoning`,
     `tool_intent`, `tool_started`, `tool_result`, `tool_failed`, `tool_output`, `tool_heartbeat`,
     `run_state`, `human_interrupt`, `operator_guidance`, `artifact`, `subagent_lifecycle`,
     `command_output`, `operational_trace`, `note`, `heartbeat`.
   - Cada tipo con su `z.object` + discriminador `kind`, `version` (número), `id` (string,
     opcional pero estable cuando hay lifecycle), `payload` tipado.
   - Exporta `MuninPart = z.infer<typeof muninPartSchema>` y un `muninPartSchema` (z.discriminatedUnion).
2. **IDs estables que reconcilian en el mismo lugar**: los lifecycles de tool/command/artifact
   se agrupan por `tool_call_id`/`job_id`/`artifact_id` — un mismo elemento se actualiza
   (start→update→end) en lugar de crear tarjetas nuevas. El schema debe exigir `id` en las
   partes con lifecycle.
3. Compatibilidad: mantén los tipos existentes en `mcp.ts` funcionando (no rompas imports
   de otros componentes); añade, no sustituyas abruptamente. Si algo se depreca, déjalo
   marcado `@deprecated` con el reemplazo.
4. **Tests de schema** (vitest en `app/src/types/__tests__/`): cada parte válida pasa;
   partes inválidas (kind desconocido, versión mala, payload roto, id faltante en lifecycle) fallan.
   Incluye un test de que el schema **rechaza** cualquier `kind` no permitido (allow-list).

### 2. Renderer registry tipado y allow-listed

1. Reusa/adapta `app/src/extensions/registry.tsx`:
   - Contrato: `data part (validado por Zod) -> renderer key confiable -> componente React confiable`.
   - `RendererKey = union de strings literales` (p.ej. `'ioc-table' | 'cve-assessment' | 'timeline' | 'evidence' | 'json' | 'csv-table' | 'markdown' | 'code' | 'diff' | 'mermaid' | 'graph' | 'finding-card' | 'screenshot' | 'terminal' | 'download' | 'sandboxed-html'`).
   - Registro: `Map<RendererKey, { component, schema, validate(input) }>` — el schema valida el
     payload ANTES de renderizar; si falla, renderiza un fallback seguro (nunca raw).
   - `resolveRenderer(kind, payload)` → componente o fallback.
2. **Seguridad**: en este PR el registry solo se registra (los renderers reales llegan en PR-5),
   pero el contrato debe impedir: (a) renderizar JSX arbitrario del modelo, (b) ejecutar JS
   generado, (c) HTML sin sanitizar. Documenta la política en el propio registry.
3. **Tests**: registro de un renderer de ejemplo (puede ser un placeholder `NotImplementedRenderer`
   en `app/src/fixtures/`), resolución correcta por key, fallback para key desconocida o payload inválido.

### 3. Fixture gallery

1. `app/src/fixtures/` — muestrario de partes con datos falsos **marcados como tales**
   (p.ej. banner "Fixture — no es producción").
2. Una página/componente de gallery accesible en dev (`/fixtures` o ruta de preview) que
   muestre cada parte definida con un caso válido y un caso inválido (para ver el fallback).
3. **Responsive**: la gallery debe comprobarse en 1366×768, 1440×900, 1920×1080, tablet (768)
   y mobile (360) — sin overflow horizontal de página en ninguno. Documenta cómo verificarlo.
4. Todos los datos fixture son inventados; nada llama APIs reales.

### 4. Docs del contrato

1. `docs/issue-18-ui-contract.md`: el modelo de partes (tabla kind→payload→renderer key),
   la política de seguridad del registry, y cómo añadir una parte nueva (paso a paso).
2. Actualiza `changes.md` (formato histórico del repo).

## Criterios de aceptación

- [ ] Partes de UI versionadas (`muni-ui/*`) validadas por Zod, con `z.discriminatedUnion`.
- [ ] IDs estables exigidos para partes con lifecycle; docs de reconciliación.
- [ ] `RendererKey` union literal + registry con schema-validation y fallback seguro.
- [ ] No se renderiza JSX/JS arbitrario; política documentada.
- [ ] Fixture gallery con caso válido + inválido por parte; marcada como fixture.
- [ ] Gallery sin overflow horizontal en los 5 viewports.
- [ ] Tests vitest nuevos pasan; `npm run lint`, `npm run typecheck`, `npm run build` en `app/`.
- [ ] No se tocaron rutas de otros PRs (ver "Rutas que SOLO este PR toca").

## Non-goals

- NO redesign visual (PR-3+).
- NO tocar backend Python (PR-2/PR-6).
- NO instalar AI Elements (decisión de ola 2; aquí solo el contrato).
- NO tocar translator/BFF/componentes existentes salvo que el contrato lo exija para tipos.

## Verificación final antes del PR

```bash
cd app && npm run lint && npm run typecheck && npm run build && npm test
```

Branch: `feat/issue-18-1-ui-contract`. PR a `main`. Reporta: archivos cambiados,
decisiones de schema (especialmente qué `kind`s del translator actual se mapean a partes),
tests y capturas de la gallery en los 5 viewports.
