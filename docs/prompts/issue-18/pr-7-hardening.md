# Prompt PR-7 — Hardening: a11y, keyboard, virtualización, visual regression, high-volume streaming

> Issue: #18 · Fase 7 · Ola 3 · **Requiere PR-3, PR-4, PR-5, PR-6 en `main`**.
> Contexto completo: `docs/prompts/issue-18/00-master.md` — léelo primero.

## Alcance de este PR

Endurecer el producto: accesibilidad, navegación por teclado, virtualización de listas
largas, visual regression en desktop/tablet/mobile, y tests de high-volume streaming. Es
el PR de cierre del epic; cuerda todo lo anterior y eleva la calidad a producción.

## Rutas que toca

- Ajustes puntuales en `app/src/components/**` para a11y/teclado/virtualización.
- `app/src/__tests__/**` o `app/tests/**` — tests de visual regression y streaming.
- `app/playwright.config.*` o equivalente si introduces E2E (verifica la stack actual).
- `app/vitest.config.ts` — si añades config de tests.
- `docs/**` — a11y statement, plan de regression, resultados.

**Prohibido**: romper funcionalidad de PR-3/4/5/6 (ajustes defensivos, no reescrituras).

## Contexto técnico verificado (no re-verifiques, úsalo)

- Skill `munin-frontend` exige: HTML semántico, forms etiquetados, focus-visible global
  (ring 2px accent en globals.css), `prefers-reduced-motion` ya honrado, keyboard-navigable.
- Anti-patrones: skull/locks/matrix-rain; no glow decorativo; terminal real solo con output real.
- Viewports del issue: 1366×768, 1440×900, 1920×1080, tablet (768), mobile (360).
- Fixtures de overflow: PR-4 ya cubre command output; aquí añade regression para URL/JSON/
  tablas/código largos. Skill recomienda verificar librerías con Context7 antes de añadir E2E.
- AI SDK UI: el stream puede ser high-volume (muchos UIMessageChunk); `messages[].parts[]`
  puede crecer — virtualización para no degradar renders de conversaciones largas.

## Contenido

### 1. Accesibilidad

1. Auditar `app/src/components/**` con lint a11y (eslint-plugin-jsx-a11y verificar si está en
   `app/.eslintrc.json`; si no, añádelo) y arreglar: roles, etiquetas, foco visible,
   contraste (paleta Munin ya probado — confirma AAA/AA con herramientas), estado no solo por color.
2. Reasoning/artifacts/terminal: anuncios a screen-reader (aria-live cuando corresponde),
   no esconder error states visualmente solo.
3. A11y statement documentado en `docs/issue-18-a11y.md`.

### 2. Navegación por teclado

1. Atajos globales y por zona (composer, conversación, workspace, sidebar); documentarlos.
2. Focus traps en modales/drawers/fullscreen (Radix ya está — verifica el uso existente).
3. Skip links, orden de tab lógico, no atascos.

### 3. Virtualización

1. Listas que pueden ser largas: mensajes de conversación, run events, tool outputs, artifacts.
2. Evaluar `@tanstack/react-virtual` (verifica versión con Context7) o técnica existente ;
   implementar sin romper replay/streaming/detached state.
3. Métrica: conversación de 5000 mensajes y 20000 parts renderiza < 16ms por frame promedio.

### 4. Visual regression

1. Storybook/Playwright/Chromatic — verifica la stack actual; si no hay, añade Playwright con
   screenshots en los 5 viewports para: conversación vacía, conversación con tool/terminal/
   HITL/artifact, workspace abierto/cerrado, overflow fixtures, estados detach/cancel.
2. CI: job o step que corre visual regression (no rompe contracts existentes — AGENTS.md).

### 5. High-volume streaming

1. Test E2E (Playwright o equivalente) que cubre: streaming de chunks rápidos, replay tras
   reconnect, detach + reconnect, cancel confirmado en backend, guidance reaching model input,
   HITL durable, artifact render + export. (Reusa tests de PR-2 donde aplique.)
2. Medir: bundles estables, no crece memoria, sin jank, sin overflow de página.

### 6. Docs

- `docs/issue-18-hardening.md`: a11y statement + resultados, atajos, plan de regression,
  métricas de high-volume, decisiones de virtualización.
- `changes.md`.

## Criterios de aceptación

- [ ] eslint a11y pasa; focus/roles/labels correctos; contraste AA/AA A.
- [ ] Atajos de teclado documentados y funcionales; focus traps en modals/drawers.
- [ ] Listas largas virtualizadas (conversación 5k+ messages sin degradar).
- [ ] Visual regression en 5 viewports; CI lo corre.
- [ ] E2E cubre streaming, replay, detach, cancel, guidance, HITL, artifacts, export.
- [ ] Sin overflow de página; sin jank medible; memoria estable.
- [ ] `npm run lint`, `npm run typecheck`, `npm run build`, `npm test` pasan; cualquier CI
  nuevo verde.
- [ ] `python -m compileall -q munin tests scripts` + pytest (si tocas backend — en principio no).

## Non-goals

- NO redesign nuevo (se construye sobre PR-3+).
- NO nuevas features de producto (solo hardening sobre lo existente).
- NO instalar AI Elements ahora.

## Verificación final antes del PR

```bash
cd app && npm run lint && npm run typecheck && npm run build && npm test
# + el job de visual regression / E2E que hayas añadido
```

Branch: `feat/issue-18-7-hardening`. PR a `main`. Reporta: resultados de la auditoría a11y,
atajos, métricas de virtualización (antes/después), capturas del visual regression en los 5
viewports, y el resultado del E2E high-volume.
