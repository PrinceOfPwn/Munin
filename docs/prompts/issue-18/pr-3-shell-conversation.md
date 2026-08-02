# Prompt PR-3 — Shell 3 zonas + conversación + scrolling + composer (AI Elements adaptado)

> Issue: #18 · Fase 3 · Ola 2 · **Requiere PR-1 en `main`** (schemas + registry).
> Ejecutar en paralelo con `pr-4` y `pr-5` (fronteras disjuntas, ver abajo).
> Contexto completo: `docs/prompts/issue-18/00-master.md` — léelo primero.

## Alcance de este PR

Reconstruir el shell y la experiencia conversacional con las primitivas de AI Elements
**adaptadas al stack actual (React 18 + Tailwind 3.4)**. El workspace contextual de
artifacts NO es de este PR (PR-5). La ejecución de tools/terminal/agents NO es de este
PR (PR-4). Aquí: layout, navegación, conversación, scrolling, mensajes, composer, detach/reconnect.

## Rutas que SOLO este PR toca (fronteras disjuntas con PR-4 y PR-5)

- `app/src/components/AppShell.tsx` (o su reemplazo `app/src/components/shell/**`).
- `app/src/components/layout/**`, `app/src/components/nav/**` (nuevos).
- `app/src/components/chat/ConversationView/**` — contenedor de conversación, scrolling,
  composer (reutilizando donde se pueda lo existente; lo que se mueva, moverlo aquí).
- `app/src/components/ai-elements/**` — primitivas adaptadas/vendorizadas de AI Elements.
- `app/src/app/page.tsx`, `app/src/app/layout.tsx` (solo lo necesario para el shell).
- `docs/**` — decisiones de adaptación.

**Prohibido**: tocar `app/src/components/chat/blocks/**` (PR-4), `app/src/components/workspace/**`
(PR-5), `app/src/components/renderers/**` (PR-5), `munin/**`, `tests/**`,
`app/src/extensions/**`, `app/src/types/**` (si PR-1 no está en main: espera o rebase).

## Contexto técnico verificado (no re-verifiques, úsalo)

- **AI Elements exige React 19 + Tailwind 4 + shadcn CLI — el repo está en React 18.3 +
  Tailwind 3.4.** Estrategia decidida: **adaptar/vendorizar primitivas seleccionadas** del
  fuente de AI Elements al stack actual. NO ejecutar `npx ai-elements@latest`.
- Referencia de componentes (docs AI Elements, verificadas):
  - `Conversation`/`ConversationContent`/`ConversationScrollButton`/`ConversationDownload`/
    `ConversationEmptyState` — scrolling y contenedor.
  - `Message`/`MessageContent`/`MessageResponse` — mensajes; `from={role}`.
  - `PromptInput`/`PromptInputTextarea`/`PromptInputSubmit` — composer; `onSubmit` recibe
    `PromptInputMessage {text}`; `status: "streaming"|"ready"` para el submit.
  - `Reasoning`/`ReasoningTrigger`/`ReasoningContent` — reasoning colapsable; consolida
    partes `reasoning` del mismo mensaje.
  - `Suggestion`/`Suggestions` — sugerencias.
  - `Tool`/`ToolHeader`/`ToolContent`/`ToolInput`/`ToolOutput` — tool cards (PR-4 los
    usará; aquí puedes dejar el contrato de datos listo sin renderizar el detalle).
  - Dependencias típicas de AI Elements que tendrás que portar si adaptas: `use-stick-to-bottom`,
    `ansi-to-react`, `streamdown`/`shiki` (markdown streaming/highlighting), `lucide-react`,
    `nanoid` — verifica contra `app/package.json` qué ya existe y qué hay que añadir.
- AI SDK UI v4 (`ai@7.0.47`, `@ai-sdk/react@4.0.50`):
  - `useChat<MyUIMessage>({ transport: new DefaultChatTransport({ api: '/api/chat/...' }) })`
  - `sendMessage({ text })`, `status: submitted|streaming|ready|error`, `stop()` (solo detach),
  - `messages[].parts[]` tipadas; PR-1 dio los schemas custom.
- El BFF y translator ya traducen envelopes SSE → `UIMessageChunk` (NO tocar en este PR;
  si el shell necesita algo nuevo de ahí, anótalo para PR-6 o negocia el mínimo).
- Layout objetivo (mockup `docs/mockups/issue-18-ai-native-workspace/index.html`): 3 zonas
  responsive redimensionables: sidebar de operaciones, conversación/ejecución, workspace
  (workspace se activa en PR-5; aquí deja el slot colapsable).
- Skill `munin-frontend` (`.opencode/skills/munin-frontend/SKILL.md`): dirección de arte,
  paleta, reglas de tipografía, animaciones disponibles (`fade-slide`, `feather`, `blink`,
  `spine-flow`), viewports 360/768/1024/1440/1920, anti-patrones.

## Contenido

### 1. Primitivas AI Elements adaptadas (vendorizadas)

1. Crea `app/src/components/ai-elements/` con SOLO las primitivas que el shell necesita:
   conversation (scrolling), message, prompt-input, reasoning, suggestion. (Tool/artifact/
   terminal se adaptan en PR-4/PR-5 — no las copies aquí todavía.)
2. Cada primitiva adaptada:
   - Porta el comportamiento (props, estado, a11y) del componente AI Elements correspondiente
     (fuente: repo `vercel/ai-elements`, carpeta `packages/elements`),
   - **Estilizada con los tokens de Munin** (Tailwind utilities de `app/tailwind.config.ts`,
     NUNCA hex hardcoded),
   - Documenta la adaptación en un comentario de cabecera: componente original + versión + qué cambió.
3. `use-stick-to-bottom` (o equivalente de AI Elements) para el scroll inteligente: pegado al
   fondo durante streaming, despegable al hacer scroll arriba, botón de vuelta al fondo
   (`ConversationScrollButton`).
4. Markdown/código: usa lo que ya existe (react-markdown + rehype-highlight están en el repo);
   solo si AI Elements ofrece una mejora clara (streamdown) y se puede portar con riesgo bajo,
   adóptala. Si no, mantén lo existente — el issue exige "reemplazar primitivas custom donde
   AI Elements da mejor comportamiento", no "migrar todo a AI Elements".

### 2. Shell de 3 zonas

1. **Sidebar de operaciones** (izquierda): búsqueda, conversaciones recientes/archivadas,
   previews significativos, estado de runs. Componentes existentes de navegación (sidebar/
   conversaciones) se reusan o se mueven aquí — inspecciona `app/src/components/` primero.
2. **Conversación/ejecución** (centro): columna de mensajes con scrolling + composer sticky.
   - Los bloques de ejecución (tools/terminal/agents/HITL) se renderizan HOY con lo existente
     (PR-4 los reemplaza); el shell debe dejar el slot correcto.
3. **Workspace contextual** (derecha): **slot colapsable/resizable en este PR** — tabs
   Artifact/Evidence/Run/Agents llegan en PR-5. El centro debe reclamar el ancho al colapsar.
4. **Responsive**: ≥1024px 3 zonas (sidebar colapsable a íconos); <1024px sidebar y workspace
   como drawers; composer siempre accesible. Sin overflow horizontal de página en ningún viewport.
5. Composer: `PromptInput` adaptado con textarea auto-resize, submit con estado
   `streaming`/`ready`, atajos de teclado, y **separación visible Detach vs Cancel** (el
   comportamiento real es de PR-2; aquí renderiza los dos controles con sus estados — si
   PR-2 ya está en main, conéctalos de verdad).

### 3. Streaming y replay

1. Mantener durable replay: refresh/reconnect restaura el timeline (el BFF ya lo hace —
   verifica que el shell no rompa la reconexión).
2. Estados de la conversación: `submitted` (spinner), `streaming` (parts incrementales),
   `ready`, `error` (con retry). El detach/reconnect debe ser explícito en la UI (estado
   "detached" con botón de reconectar).

### 4. Docs

- `docs/issue-18-ai-elements-port.md`: qué primitivas se adaptaron, de qué versión,
  qué cambió, qué NO se adaptó y por qué (React 18/Tailwind 3.4), qué dependencias nuevas
  entraron y por qué.
- `changes.md`.

## Criterios de aceptación

- [ ] Shell de 3 zonas responsive; center reclaims width al colapsar el workspace.
- [ ] Scroll inteligente (stick-to-bottom, despegable, botón vuelta abajo).
- [ ] Mensajes renderizan partes tipadas (PR-1) con jerarquía: respuesta final > telemetría.
- [ ] Composer con estados y Detach/Cancel separados (lógica real si PR-2 está en main).
- [ ] No parece consola de debug: las respuestas finales dominan visualmente.
- [ ] Sin overflow horizontal de página con commands/URLs/JSON/tablas/código en los 5 viewports.
- [ ] Replay/reconnect funciona tras refresh.
- [ ] `npm run lint`, `npm run typecheck`, `npm run build`, `npm test` pasan.
- [ ] No se tocaron rutas de otros PRs.

## Non-goals

- NO renderers de artifacts (PR-5).
- NO execution blocks nuevos (PR-4).
- NO backend (PR-2/PR-6).
- NO upgrade React/Tailwind (adaptar es la decisión).

## Verificación final antes del PR

```bash
cd app && npm run lint && npm run typecheck && npm run build && npm test
```

Branch: `feat/issue-18-3-shell-conversation`. PR a `main`. Reporta: primitivas adaptadas
(lista con fuente y cambios), decisiones de layout (zonas, breakpoints, drawers), cómo
quedó el scroll, y capturas en 1366×768, 1440×900, 1920×1080, 768, 360.
