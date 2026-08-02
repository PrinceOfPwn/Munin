# Prompt PR-5 — Artifact workspace + renderers generativos seguros

> Issue: #18 · Fase 5 · Ola 2 · **Requiere PR-1 en `main`** (schemas + registry).
> Ejecutar en paralelo con `pr-3` y `pr-4` (fronteras disjuntas, ver abajo).
> Contexto completo: `docs/prompts/issue-18/00-master.md` — léelo primero.

## Alcance de este PR

Construir el workspace contextual (tabs Artifact/Evidence/Run/Agents) y los renderers
generativos: componentes trusted que renderizan datos estructurados del modelo de forma
interactiva, **sin jamás inyectar JSX/JS arbitrario** y sandboxeando el HTML generado.

## Rutas que SOLO este PR toca (fronteras disjuntas con PR-3 y PR-4)

- `app/src/components/workspace/**` — paneles Artifact/Evidence/Run/Agents, pinnable,
  resizable, fullscreen, historia de versiones.
- `app/src/components/renderers/**` — los componentes trusted del registry (uno por key).
- `app/src/components/ai-elements/artifact/**`, `app/src/components/ai-elements/sandbox/**`,
  `app/src/components/ai-elements/jsx-preview/**`, `app/src/components/ai-elements/web-preview/**`
  — primitivas AI Elements adaptadas que PR-3 no copió (SOLO estas).
- `app/src/components/sandbox/**` — iframe hardening para HTML generado.
- `app/src/lib/artifacts/**` — helpers de metadata/versioning/provenance (solo frontend).
- `docs/**` — decisiones.

**Prohibido**: tocar `app/src/components/AppShell.tsx` / `shell/**` / `layout/**` (PR-3),
`app/src/components/chat/blocks/**` (PR-4), `munin/**`, `tests/**`,
`app/src/extensions/registry.tsx` (PR-1 lo definió — aquí solo registras renderers en él),
`app/src/types/**` (PR-1).

## Contexto técnico verificado (no re-verifiques, úsalo)

- AI Elements (componentes verificados): `Artifact`/`ArtifactHeader`/`ArtifactContent`,
  `Sandbox`/`SandboxHeader`/`SandboxContent`/`SandboxTabs*` (con `CodeBlock` para code+output),
  `JSXPreview`/`JSXPreviewContent`/`JSXPreviewError` (acepta `components` map —_allow-list—,
  NO ejecuta JS generado, parsea JSX string con `react-jsx-parser`), `WebPreview`/
  `WebPreviewBody`/`WebPreviewNavigation`/`WebPreviewUrl` (renderiza una URL en iframe).
- Renderer registry de PR-1: contrato `data part (Zod) -> renderer key allow-listed ->
  componente React confiable`. Las `RendererKey` definidas: `'ioc-table' | 'cve-assessment' |
  'timeline' | 'evidence' | 'json' | 'csv-table' | 'markdown' | 'code' | 'diff' | 'mermaid' |
  'graph' | 'finding-card' | 'screenshot' | 'terminal' | 'download' | 'sandboxed-html'`.
- Backend emite `artifact` (envelope con metadata) y `evidence` (de procesos de recon);
  PR-6 enriquece el read-model. Aquí renderiza lo que hoy llega y deja el contrato para PR-6.
- Manifest del issue (rich artifact metadata): filename, size, language, renderer, version,
  provenance, preview/download URLs, conversation/run artifact list.
- Workflow objetivo (mockup): el workspace como objeto de primera clase con acciones:
  open, pin, fullscreen, copy, download, version history, comparison, evidence promotion,
  provenance.

## Contenido

### 1. Workspace contextual (4 tabs)

1. **Artifact**: lista de artifacts de la conversación/run (del read-model), preview
   seleccionable en panel principal, acciones (pin/open fullscreen/copy/download/version
   history/compare). Metadata rica visible (filename, size, language, renderer, version,
   provenance, urls).
2. **Evidence**: evidencia recolectada (screenshots, outputs, ficheros) con promotion a
   artifact y provenance (fuente, tiempo, tool que la generó).
3. **Run**: detalle read-only del run — lifecycle, tools, activities, commands, agents,
   approvals, guidance, artifacts, summaries (PR-6 alimenta; aquí consumes lo disponible
   con fallback graceful).
4. **Agents**: presencia de subagentes (RUNNING/IDLE), mensajes inter-agente, tareas del
   pool — reusa/helpers existentes de `app/src/lib/` (inspecciona primero); resume, no flood.
5. **Comportamiento del panel**: colapsable, pinnable (se mantiene al cambiar de
   conversación), resizable, fullscreen (overlay). El centro de la conversación reclama
   ancho al colapsar (contrato con PR-3 — verifica que no rompa el shell). Persistencia
   local del estado del panel por conversación (zustand ya en el repo).
6. **Responsive**: ≥1024px panel lateral; <1024px drawer/modal; en mobile 1 tab a la vez.

### 2. Renderers trusted (registration en el registry de PR-1)

Registra un componente React en `app/src/extensions/registry.tsx` para cada `RendererKey`:
1. **Datos estructurados**: `ioc-table`, `cve-assessment`, `finding-card`, `csv-table`,
   `json` (con syntax highlight), `markdown` (rendermarkdown+rehype-highlight ya en el repo),
   `code`, `diff` (resaltar +/-), `mermaid` (renderiza diagramas Mermaid — verifica la lib
   adecuada o usa `@streamdown/mermaid` portado), `graph` (relación — fallback lista legible
   si es un hairball), `timeline` (investigación), `evidence` (viewer de fuente/screenshot).
2. **Archivos**: `download` (metadata + link), `screenshot` (imagen con zoom/pan).
3. **HTML generado**: `sandboxed-html` — renderiza SOLO en iframe hardening (ver abajo).
4. Cada renderer: valida el payload con el Zod schema del PR-1; si falla, fallback seguro
   (mensaje "Datos no válidos para {key}", nunca raw). Documenta el input esperado.

### 3. Sandbox para HTML generado (hardening)

1. `app/src/components/sandbox/HardenedIframe.tsx`:
   - `sandbox=""` (sin tokens: sin allow-scripts, allow-same-origin, etc.) salvo lo mínimo
     que el issue permita — por defecto **ninguno**; si una feature necesita allow-scripts,
     documéntalo y revisa con el operador.
   - `srcdoc` con el HTML generado (no `src` a red); CSP inline que bloquea network,
     cookies, localStorage, parent DOM access.
   - `referrerpolicy="no-referrer"`, `loading="lazy"`.
   - Comunicación postMessage restringida (allow-list de mensajes, validación de origen
     si se relaja el sandbox).
2. **Política documentada**: scripts generados NUNCA ejecutan por existir; si el artifact
   es interactivo, se explicita y sandboxea con tokens mínimos revisados.

### 4. JSXPreview adaptado (allow-list de componentes)

1. Adapta `JSXPreview` de AI Elements: acepta `components` map (allow-list explícito de
   componentes Munin permitidos — NUNCA el default que incluye todo React).
2. Renderiza JSX string generado por el modelo SOLO con componentes del allow-list; error
   graceful si usa uno no permitido.
3. Documenta: este renderer es opt-in y de riesgo; el default para HTML es `sandboxed-html`.

### 5. Version history y comparison

1. Si los artifacts tienen versiones (PR-6 las provee): historial navegable, diff entre
   versiones (texto/código/markdown), abrir versión antigua en readonly.
2. Si PR-6 no aterriza a la vez: deja el contrato y la UI con fallback "no versions".

### 6. Docs

- `docs/issue-18-artifacts-renderers.md`: tabla renderer key → componente → input schema
  → fallback; política de sandbox; JSXPreview allow-list; modelo de versiones/comparación.
- `changes.md`.

## Criterios de aceptación

- [ ] Workspace con 4 tabs (Artifact/Evidence/Run/Agents) colapsable/resizable/fullscreen/pinnable.
- [ ] Renderers trusted registrados para todas las `RendererKey` mínimo viables (markdown,
  code, json, table/CSV, image, mermaid, y al menos 1 específico Munin p.ej. ioc-table).
- [ ] HTML generado SOLO en iframe hardening (sandbox vacío, srcdoc, CSP, sin same-origin/cookies/auth).
- [ ] Scripts generados no ejecutan por existir; JSXPreview solo con allow-list explícita.
- [ ] Nunca se inyecta JSX/JS arbitrario: validación Zod + fallback seguro en cada renderer.
- [ ] Metadata rica visible (filename, size, language, renderer, version, provenance).
- [ ] Acciones: open, pin, fullscreen, copy, download, version history, comparison, evidence promotion.
- [ ] Sin overflow horizontal de página en los 5 viewports.
- [ ] `npm run lint`, `npm run typecheck`, `npm run build`, `npm test` pasan.
- [ ] No se tocaron rutas de otros PRs.

## Non-goals

- NO shell (PR-3).
- NO bloques de ejecución (PR-4).
- NO backend/read-model (PR-6).
- NO upgrade React/Tailwind.

## Verificación final antes del PR

```bash
cd app && npm run lint && npm run typecheck && npm run build && npm test
```

Branch: `feat/issue-18-5-artifacts-renderers`. PR a `main`. Reporta: tabla de renderers
(key → componente → input → fallback), política de sandbox exacta (tokens CSP/sandbox),
JSXPreview allow-list, capturas del workspace en desktop y mobile, y un caso de HTML
generado renderizado en el iframe (muestra que no accede a cookies/localStorage del padre).
