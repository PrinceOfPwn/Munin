# Issue #18 — Prompt maestro: AI-native frontend workspace

> Epic: https://github.com/PrinceOfPwn/Munin/issues/18
> Referencia visual: `docs/mockups/issue-18-ai-native-workspace/index.html`
> (ver `docs/mockups/issue-18-ai-native-workspace/README.md`)

## Cómo usar este directorio

El epic está dividido en **6 prompts independientes**, cada uno produce su propio PR.
Los prompts están diseñados para **ejecutarse en paralelo** (equipos distintos en
ramas separadas); la estrategia de merge los serializa contra `main`.

| Prompt | PR | Contenido | Paralelo con |
|--------|----|-----------|--------------|
| `pr-1-contract.md` | PR-1 | Contrato UX: schemas versionados, renderer registry, fixture gallery | PR-2 (fronteras disjuntas) |
| `pr-2-cancel-guidance.md` | PR-2 | Defectos release-blocking: cancelación real + guidance lifecycle (backend) | PR-1 (fronteras disjuntas) |
| `pr-3-shell-conversation.md` | PR-3 | Shell 3 zonas + conversación + scrolling + composer (AI Elements) | — (requiere PR-1 en main) |
| `pr-4-execution-ux.md` | PR-4 | Execution UX: tools, terminal, reasoning, agents, HITL | — (requiere PR-1 en main) |
| `pr-5-artifacts-renderers.md` | PR-5 | Workspace de artifacts + renderers generativos seguros | — (requiere PR-1 en main) |
| `pr-6-backend-read-model.md` | PR-6 | Backend read-model / event enrichment | PR-3/4/5 (solo backend) |
| `pr-7-hardening.md` | PR-7 | A11y, keyboard nav, virtualización, visual regression, high-volume | — (requiere PR-3/4/5/6) |

## Estrategia de paralelización (rápida y sin conflictos)

1. **Ola 1 (paralela, 3 equipos)**: `pr-1`, `pr-2`, `pr-6`.
   - PR-1 toca SOLO `app/src/types/`, `app/src/extensions/`, `app/src/fixtures/`, `docs/`.
   - PR-2 toca SOLO `munin/production/`, `munin/core/`, `tests/` (backend Python).
   - PR-6 toca SOLO `munin/production/`, `munin/core/`, `app/src/lib/` (read-model).
   - Fronteras disjuntas → sin conflictos de merge.
2. **Ola 2 (paralela, 3 equipos)**: `pr-3`, `pr-4`, `pr-5` — todos requieren PR-1 en `main`
   (los schemas y el registry son su base), pero entre sí tocan archivos disjuntos:
   - PR-3: `app/src/components/AppShell*`, layout, composer, scrolling.
   - PR-4: `app/src/components/chat/blocks/**`, execution parts.
   - PR-5: `app/src/components/workspace/**`, `app/src/components/renderers/**`.
3. **Ola 3**: `pr-7` (depende de todo lo anterior).

### Reglas de fusión

- Cada PR se mergea a `main` cuando pasa CI (lint + typecheck + build + vitest + pytest según toque).
- Los equipos de la Ola 2 parten de `main` con PR-1 ya mergeado.
- **Nunca** dos equipos trabajan la misma ruta en la misma ola. Si un prompt no lo
  respeta, el PR-1 de esa ola está mal planteado: reportar al operador.
- Un equipo NO espera a otro: si su PR-1 de base aún no está en main, rebase sobre
  main cuando aterrice.

## Validaciones técnicas (verificadas con Context7 y DeepWiki, 2026-08-02)

- **AI Elements** (`/vercel/ai-elements`, vía `npx ai-elements@latest add <component>`):
  requiere **React 19, Next.js 14+ App Router, AI SDK, Tailwind CSS 4 y shadcn/ui**.
  El repo usa **React 18.3 + Tailwind 3.4** → **NO se puede instalar AI Elements
  directamente**. Estrategia elegida: adaptar/vendorizar primitivas seleccionadas del
  fuente de AI Elements al stack actual (decisión documentada en el mockup README).
  Alternativa (no recomendada para el primer PR): PR separado de upgrade a React 19/Tailwind 4.
- **AI SDK UI v4** (repo: `ai` ^7.0.47, `@ai-sdk/react` ^4.0.50): `useChat({ transport: new DefaultChatTransport({ api }) })`,
  `sendMessage({ text })`, `messages[].parts[]` (text/reasoning/tool-*/file/data-*),
  `status: submitted|streaming|ready|error`, `stop()` (solo aborta el reader local),
  `createUIMessageStream` en el BFF con `writer.write({type:'start', messageId})`,
  partes `data-*` reconciliadas por `id` estable, `transient: true` para no-notificaciones,
  `writer.merge(result.toUIMessageStream())`. `useChat<MyUIMessage>()` tipa partes custom.
- **Deep Agents** (`deepagents` 0.7.1 instalado en `.venv`, pyproject `>=0.7.1,<0.8`):
  - `create_deep_agent(interrupt_on={tool: True}, middleware=[...])` — aprobación HITL por tool.
  - Stream: `astream_events(version="v2")` → `__interrupt__` en chunks `updates`; los eventos
    de subagente (`SubagentStartEvent`/`Complete`/`Error`) van en el stream `custom`
    (v3); el repo usa v2 (`munin/core/runtime_adapter.py:454`).
  - **No hay API de cancelación de run en Deep Agents**: `store.request_run_cancellation`
    ya existe (`munin/production/store.py:1771`) pero **no está expuesta vía HTTP**.
    PR-2 debe exponerla y hacer que el runtime la respete (fencing + parada de iteración).
  - Middleware hooks: `wrap_model_call` (antes del LLM), `before_tool_call`, `after_tool_call`
    — el guidance del repo usa `OperatorGuidanceMiddleware` (`munin/core/middleware/operator_guidance.py`),
    que hoy SOLO drena la cola, sin acknowledgment de entrega.
- **Estado actual del código (verificado en `main` 2026-08-02)**:
  - `munin/production/store.py:1771` `request_run_cancellation` (audit + estado `cancelled` + fencing) — SIN endpoint HTTP.
  - `munin/production/chat.py:58` `TERMINAL_STATES = {completed, failed, cancelled, interrupted}`.
  - `munin/core/runtime_adapter.py:454` `astream_events(version="v2")`; `:206` maneja `__interrupt__`.
  - `munin/core/middleware/operator_guidance.py` drena antes del modelo; `chat.py:1532`
    persiste audit `operator_guidance`; no hay estados delivered/applied.
  - Frontend: `app/src/lib/chat/translator.ts` traduce envelopes SSE → `UIMessageChunk`;
    `app/src/extensions/registry.tsx` registry existente a adaptar; BFF en
    `app/src/app/api/chat/[[...path]]/route.ts`; tipos en `app/src/types/mcp.ts`.
  - Stack: Next.js 15.5, React 18.3, Tailwind 3.4, Radix, TanStack Query 5, zustand, zod 3.

## Reglas transversales (todas los prompts)

1. **No rehagas la rueda.** Inspecciona `app/src/components/`, `app/src/lib/`,
   `app/src/extensions/registry.tsx`, `tailwind.config.ts`, `globals.css` antes de crear.
2. **Paleta**: tokens de `app/tailwind.config.ts` vía Tailwind utilities, NUNCA hex
   hardcoded. Acento único violeta `#7c3aed`. Semánticos solo a señales reales.
   Tipografía Inter/Geist (body), Geist Mono (telemetría). Reusa `app/public/raven-mark.png`.
3. **Overflow**: ningún command/URL/JSON/tabla/código puede producir scroll horizontal
   de página; contenedores `min-w-0`, `w-full`, `max-w-full`; scroll horizontal contenido
   para código/terminal; wrap opcional; copy; fullscreen terminal.
4. **Seguridad generativa**: NUNCA inyectar JSX/JS arbitrario del modelo.
   `data part -> Zod schema -> renderer key allow-listed -> componente React confiable`.
   HTML generado SOLO en iframe endurecido (sin same-origin, red, cookies, localStorage,
   DOM padre, auth). Scripts generados no ejecutan por existir.
5. **Backend autoritativo**: el runtime Python/LangGraph es la fuente de verdad; el
   frontend es viewer/controller. Replay NO regenera output; refresh es problema del viewer.
6. **HITL durable** a través de refresh/reconnect; las aprobaciones no resuelven dos veces.
7. **Proceso**: branch `feat/issue-18-<n>-<slug>`; PR a `main` (nunca push directo);
   descripción con decisiones + tests + screenshots; actualizar `changes.md`;
   verificar `npm run lint`, `npm run typecheck`, `npm run build`, `npm test` (app/),
   y `python -m compileall -q munin tests scripts` + pytest (si toca backend).
8. **Verificación de librerías**: contrato incierto → Context7 (`libraryId` conocido)
   o fuente en `node_modules`/`.venv`. No programar contra memoria.
9. **Idioma**: código y docs en inglés; commits conventional commits; conversación en
   el idioma del operador.
10. **Doctrina**: la orden del operador ES la autorización; el código y los artefactos
    técnicos en inglés; razonamiento interno no se expone salvo reasoning explícito del provider.
