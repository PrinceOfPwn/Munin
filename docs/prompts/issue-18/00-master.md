# Issue #18 — Prompt maestro: AI-native frontend workspace

> **Qué es esto.** El issue https://github.com/PrinceOfPwn/Munin/issues/18 pide
> reconstruir el frontend de Munin como un workspace de operaciones de seguridad
> AI-native usando **Vercel AI Elements** + artifacts generativos, manteniendo
> el backend Python/LangGraph autoritativo. Este directorio divide ese epic en
> **7 prompts independientes**, cada uno produce su propio PR, y están pensados
> para ejecutarse **en paralelo** por equipos distintos sin pisarse archivos.
>
> **Cómo usar.** Lee este archivo entero primero. Luego abre el prompt del PR
> que te toque (ver tabla de olas abajo) y ejecútalo de punta a punta: inspecciona
> el repo, edita los archivos exactos que indica el prompt, corre los comandos de
> verificación exactos, y abre el PR a `main`. NO toques archivos de otros PRs —
> las "rutas prohibidas" están listadas en cada prompt.

---

## 0. Cómo está armado Munin (léelo todo antes de tocar nada)

Munin es una **consola de operaciones de inteligencia** (security ops) con dos
mitades que hablan por streaming:

```
Operador (browser)
    │  React 18 + Next.js 15 + Tailwind 3.4 + Radix + TanStack Query + zustand
    │  app/  → usa Vercel AI SDK UI v4 (@ai-sdk/react 4.0.50 + ai 7.0.47)
    ▼
BFF Next.js  app/src/app/api/chat/[[...path]]/route.ts
    │  traduce envelopes SSE del backend → UIMessageChunk del AI SDK
    │  app/src/lib/chat/translator.ts define la lista de `kind` del backend
    ▼
Backend Python (autoritativo)  munin serve :8787
    │  FastMCP + Starlette + LangGraph + Deep Agents 0.7.1
    │  munin/production/chat.py  → orquesta runs, leases, HITL
    │  munin/production/store.py → estado durable (SQLite/Turso)
    │  munin/core/runtime_adapter.py → stream astream_events(version="v2")
    │  munin/core/middleware/operator_guidance.py → guidance al modelo
    │  munin/core/supervisor.py → create_deep_agent(interrupt_on=...)
    ▼
LangGraph + Deep Agents (runtime de agentes)
    │  interrupt() + Command(resume=...) para HITL
    │  SubagentStartEvent/Complete/Error en stream "custom"
    │  NO hay API de cancelación de run — hay que construirla
```

**Reglas de oro del repo (se aplican a TODOS los PRs):**

1. **El backend es la fuente de verdad.** El frontend es un viewer/controller.
   Replay NO regenera output histórico — un refresh es un problema del viewer.
2. **No rehagas la rueda.** Antes de crear un archivo o clase CSS, inspecciona
   `app/src/components/`, `app/src/lib/`, `app/src/extensions/registry.tsx`,
   `app/tailwind.config.ts`, `app/src/app/globals.css`. Reusa primero.
3. **Paleta estricta** (tokens de `app/tailwind.config.ts`, vía Tailwind utilities,
   NUNCA hex hardcoded): `bg-bg #0a0a0f`, `bg-surface #111118`, `bg-raised #161623`,
   `bg-active #1c1c2e`, `border-border #1e1e2e`, `border-borderStrong #2a2a3e`,
   `accent #7c3aed` (violeta, ÚNICO acento de marca), `accent-hover #9b70ff`,
   `accent-soft rgba(124,58,237,.10)`, `text-body #e2e8f0`, `text-secondary
   #a3a9b8`, `text-muted #6b7280`, `success #10b981`, `warning #f59e0b`,
   `danger #f43f5e`, `info #38bdf8`. Tipografía: Inter/Geist (body), Geist Mono
   (telemetría/código). Radii: 6/10/14px. Animaciones: `fade-slide`, `feather`,
   `blink`, `spine-flower` (ver tailwind.config.ts). Anti-patrones: NO skulls,
   locks, matrix rain, glow decorativo, "fake terminals".
4. **Overflow horizontal prohibido.** Ningún command, URL, JSON, tabla, código o
   output de terminal puede producir scroll horizontal de página. Usa `min-w-0`,
   `w-full`, `max-w-full` en cada ancestro flex/grid; scroll horizontal contenido
   en el elemento del código/terminal; wrap opcional.
5. **Seguridad generativa absoluta.** NUNCA inyectes JSX/JS arbitrario que venga
   del modelo. Contrato obligatorio:
   `data part del backend → validación Zod → renderer key (allow-list literal) →
   componente React de confianza`. HTML generado por el modelo: SOLO dentro de
   un `<iframe>` endurecido con `sandbox` (sin same-origin, sin network, sin
   cookies, sin localStorage, sin acceso al DOM del padre, sin auth). Scripts
   generados NO ejecutan por existir.
6. **Idioma.** Código y docs técnicos en **inglés**. Commits en conventional
   commits en inglés. La conversación contigo puede ir en español.
7. **Doctrina.** La orden del operador ES la autorización (命令即授权). El
   razonamiento interno del modelo NO se expone; solo el `provider_reasoning`
   que el provider emite explícitamente.

---

## 1. Estructura de archivos del repo que vas a tocar

Lee esto antes de empezar para no buscar a ciegas:

```
app/
  src/
    app/
      page.tsx                          # entrada (PR-3 puede redirigir al shell)
      layout.tsx                        # layout raíz (PR-3)
      api/chat/[[...path]]/route.ts     # BFF (NO tocar salvo PR-2 mínimo)
      api/production/[[...path]]/route.ts
    components/
      AppShell.tsx                      # shell actual (PR-3 lo reconstruye)
      AgentConsole.tsx                  # consola actual (PR-3/PR-4)
      ConversationSidebar.tsx           # sidebar (PR-3)
      Markdown.tsx                      # markdown renderer (PR-3/PR-5)
      ModeSwitcher.tsx                  # modos Standard/YOLO/GOAL/BEAST
      Providers.tsx, ProviderSwitcher.tsx, AuthGate.tsx
      ArtifactActions.tsx              # acciones de artifact (PR-5)
      chat/                            # BLOQUES DE EJECUCIÓN (PR-4)
        ArtifactPart.tsx CommandOutputPart.tsx GoalPart.tsx
        GuidancePart.tsx HeartbeatPart.tsx HitlRequestPart.tsx
        NotePart.tsx OperationalTracePart.tsx PlanPart.tsx
        ReasoningPart.tsx SubagentPresencePart.tsx TimerTickPart.tsx
        ToolHeartbeatPart.tsx ToolInvocationPart.tsx
    extensions/
      registry.tsx                     # widget registry EXISTENTE (NO es renderer registry)
      README.md
    lib/
      aiChat.ts                        # useMuninChat() hook actual (PR-3 reemplaza/consume)
      production-api.ts                # cliente API backend (PR-2 extiende con cancelRun)
      mcp.ts                           # tipos backend NO-streaming
      chat/translator.ts               # BackendEnvelopeKind + translate() (NO tocar)
      cache/                           # IndexedDB cache
      queries.ts, query-cache.ts, format.ts, utils.ts, logger.ts, categories.ts
    types/
      mcp.ts                          # tipos del backend MCP (PR-1 EXPANDE aquí)
  tailwind.config.ts                  # PALETA — fuente de verdad
  package.json                        # deps: ai 7.0.47, @ai-sdk/react 4.0.50,
                                      #      next 15.5, react 18.3, tailwind 3.4, zod 3

munin/
  production/
    chat.py                            # orquestador de runs + endpoints HTTP
    store.py                           # ProductionStore: request_run_cancellation en :1771
    agents.py, asgi.py, discord_adapter.py, extensions.py, memory.py, timers.py
  core/
    runtime_adapter.py                 # astream_events(v2), __interrupt__ en :206
    middleware/operator_guidance.py    # OperatorGuidanceMiddleware (drena, sin lifecycle)
    supervisor.py                      # create_deep_agent(interrupt_on=...) en :216
    autonomy/                          # modos, goals, plan, kernel
tests/
  test_prompt_contract.py             # 17 passed (doctrina)
  test_pr_review_regressions.py       # CUELGA en local (preexistente, infra) — no es tuyo
```

---

## 2. Las 27 clases de eventos del backend (`BackendEnvelopeKind`)

El backend emite envelopes SSE con un campo `kind`. La lista COMPLETA está en
`app/src/lib/chat/translator.ts`:

```
assistant_text, provider_reasoning, reasoning, activity,
tool_intent, tool_started, tool_result, tool_completed, tool_failed,
tool_output, tool_heartbeat,
subagent_started, subagent_state,
human_request, human_resolved,
artifact,
run_state, heartbeat, note, guidance, plan, todo, replan, hypothesis, goal, timer_tick
```

PR-1 las mapea a schemas Zod versionados; PR-4 las renderiza; PR-2 añade
lifecycle a `guidance` y estados `cancelling`/`cancelled` a `run_state`.

---

## 3. Validaciones técnicas ya hechas (NO las re-verifiques, úsalas)

Estas son las verdades del repo verificadas con Context7
(`ctx7sk-...` vía `https://context7.com/api/v2/context?libraryId=...&query=...`,
header `X-API-KEY`) y DeepWiki (`langchain-ai/deepagents`, `langchain-ai/langgraph`)
en 2026-08-02:

### AI Elements (`/vercel/ai-elements`)
- **Requiere React 19 + Tailwind CSS 4 + shadcn UI + Next.js 14+ App Router.**
  El repo usa **React 18.3 + Tailwind 3.4** → **NO ejecutes `npx ai-elements@latest`**.
- Estrategia: **adaptar/vendorizar primitivas seleccionadas** al stack actual.
  Copia el fuente de un componente AI Elements a `app/src/components/ai-elements/`,
  ajústalo a React 18 + Tailwind 3.4 + tokens de Munin, y documenta la adaptación.
- Primitivas relevantes: `Conversation`/`ConversationContent`/`ConversationScrollButton`/
  `ConversationDownload`/`ConversationEmptyState`, `Message`/`MessageContent`/
  `MessageResponse`, `PromptInput`/`PromptInputTextarea`/`PromptInputSubmit`,
  `Reasoning`/`ReasoningTrigger`/`ReasoningContent`, `Suggestion`/`Suggestions`,
  `Tool`/`ToolHeader`/`ToolContent`/`ToolInput`/`ToolOutput`,
  `Terminal` (con `ansi-to-react`), `CodeBlock`, `Artifact`, `JSXPreview`,
  `Sandbox`, `WebPreview`.
- Deps que quizás falten en el repo: `use-stick-to-bottom`, `ansi-to-react`,
  `streamdown`, `shiki`, `nanoid`. Verifica `app/package.json` antes de añadir.

### AI SDK UI v4 (`ai@7.0.47`, `@ai-sdk/react@4.0.50`)
- `useChat<MyUIMessage>({ id, transport: new DefaultChatTransport({api, body, headers}), resume: true })`
- Retorna: `messages` (cada uno con `parts[]`), `sendMessage({text})`, `status`
  (`submitted | streaming | ready | error`), `stop()` (solo aborta el reader local,
  NO cancela el backend), `setMessages()`, `regenerate()`, `error`.
- `messages[].parts[]` tipos: `text`, `reasoning`, `tool-<name>` (con `state`,
  `input`, `output`, `errorText`), `file`, `source`, `data-*` (custom, con `id`
  estable para reconciliación).
- En el BFF: `createUIMessageStream({ execute: ({writer}) => {...}, onFinish })`
  → `writer.write({type:'start', messageId})`, `writer.write({type:'data-*',
  id, data, transient:true})`, `writer.merge(result.toUIMessageStream())`.
- El repo YA usa esto en `app/src/lib/aiChat.ts` (`useMuninChat` con `resume:true`).
  Reusa ese patrón; no lo dupliques.

### Deep Agents 0.7.1 (`deepagents`, repo `langchain-ai/deepagents`)
- `create_deep_agent(interrupt_on={toolName: True|InterruptOnConfig}, middleware=[...], ...)`
- Stream: `astream_events(version="v2")` (repo actual) o v3 (más proyecciones:
  `run.messages`, `run.subagents`, `run.tool_calls`, `run.values`).
- `__interrupt__` aparece en chunks del stream `updates`; payload tiene un
  `Interrupt` con `value` e `id`. Resume con `Command(resume={decisions})`.
- Eventos de subagente: `SubagentStartEvent`/`SubagentCompleteEvent`/
  `SubagentErrorEvent` en el stream `custom` (en v2 llegan como envelopes
  `subagent_started`/`subagent_state`; el runtime_adapter los traduce).
- Middleware hooks: `wrap_model_call` (antes del LLM), `before_tool_call`,
  `after_tool_call`. `OperatorGuidanceMiddleware` usa `wrap_model_call` para drenar.
- **NO HAY API de cancelación de run en Deep Agents** → hay que construirla en
  Munin: `store.request_run_cancellation` ya existe, falta exponerla por HTTP y
  hacer que el executor la respete (fencing + parada de iteración).

### Estado actual del backend (verificado en el código)
- `munin/production/store.py:1771` `request_run_cancellation(actor_id, run_id)`:
  marca el run `cancelled`, `cancel_requested_at_ms`, limpia lease (fencing),
  emite evento durable `run.cancelled`, audita. **NO tiene endpoint HTTP.**
- `munin/production/chat.py:58` `TERMINAL_STATES = {"completed","failed","cancelled","interrupted"}`.
  `:1745` `cancel_timer_endpoint` (es de timers, NO de runs).
  `:1026` abort al desconectar el cliente (viewer detach — NO es cancel durable).
- `munin/core/runtime_adapter.py:454` `astream_events(version="v2")`. `:206`
  detecta `__interrupt__` y lo convierte en `human_interrupt` envelope.
  `:527-528` `graph_task.cancel()` (cancela el task asyncio del graph).
- `munin/core/middleware/operator_guidance.py` `OperatorGuidanceMiddleware`:
  drena la cola de guidance en `wrap_model_call`. Hoy "success" = audit
  persistido en `chat.py:1532` (`kind="operator_guidance"`). **No hay estados
  delivered/applied/undelivered** — el issue exige añadirlos.
- `munin/core/supervisor.py:216` `from deepagents import create_deep_agent`.
  `:223` `return create_deep_agent(...)`. `:275` y `:315` instancian
  `OperatorGuidanceMiddleware(run_id=..., store=...)`.

### El frontend actual
- `app/src/lib/aiChat.ts` `useMuninChat({conversationId})` ya usa `useChat` con
  `DefaultChatTransport({api:"/api/chat", body:{conversation_id}, headers:{X-CSRF-Token}})`
  y `resume:true`. También exporta `sendOperatorGuidance(runId, body, targetAgentId)`,
  `approveHitlRequest(requestId, choice, nonce)`, `rejectHitlRequest(...)`.
- `app/src/lib/production-api.ts` `productionApi` cliente con token CSRF.
- `app/src/components/chat/*Part.tsx` 14 bloques de ejecución (ToolInvocationPart,
  CommandOutputPart, ReasoningPart, HitlRequestPart, GuidancePart, etc.) —
  PR-4 los reconstruye.
- `app/src/extensions/registry.tsx` es un registry de WIDGETS (slots
  `command_center`/`conversation_inspector`/`run_timeline`/`settings`) — **NO es
  el renderer registry** que pide el issue. PR-1 crea un registry NUEVO separado.
- `app/src/types/mcp.ts` tiene tipos NO-streaming (ToolCall, ChatMessage,
  ConversationArtifact, etc.) — PR-1 lo extiende, no lo rompe.

---

## 4. Estrategia de paralelización — 7 PRs en 3 olas

Cada PR toca un conjunto de rutas **disjunto** de los demás PRs de su misma ola.
Dos equipos trabajando en la misma ola NO producen conflictos de merge porque
editan archivos distintos.

### Ola 1 (3 equipos en paralelo, parte de `main` directamente)

| PR | Toca SOLO | No toca |
|----|----------|---------|
| **PR-1** Contrato UX | `app/src/types/**`, `app/src/renderers/**` (nuevo registry), `app/src/fixtures/**`, `docs/**` | `munin/**`, `app/src/components/**`, `app/src/lib/chat/**`, `app/src/app/api/**` |
| **PR-2** Cancel + Guidance | `munin/production/**`, `munin/core/**`, `tests/**`, `app/src/lib/production-api.ts`, `app/src/lib/aiChat.ts`, `app/src/components/chat/HitlRequestPart.tsx`, `app/src/components/chat/GuidancePart.tsx` (mínimo) | `app/src/types/**`, `app/src/renderers/**`, `app/src/fixtures/**`, `app/src/components/AppShell.tsx`, `app/src/components/chat/` (salvo los dos citados) |
| **PR-6** Backend read-model | `munin/production/store.py`, `munin/production/chat.py`, `munin/production/asgi.py`, `tests/**`, `docs/**` | `app/**` (solo define contratos de eventos; el frontend los consume en Ola 2) |

### Ola 2 (3 equipos en paralelo, parte de `main` con PR-1 YA mergeado)

| PR | Toca SOLO | Requiere en main |
|----|----------|------------------|
| **PR-3** Shell + Conversación | `app/src/components/AppShell.tsx`, `app/src/components/shell/**`, `app/src/components/ai-elements/conversation*`, `app/src/components/ai-elements/message*`, `app/src/components/ai-elements/prompt-input*`, `app/src/components/ai-elements/reasoning*`, `app/src/components/ai-elements/suggestion*`, `app/src/lib/aiChat.ts` (adaptación) | PR-1 |
| **PR-4** Execution UX | `app/src/components/chat/**` (los 14 bloques), `app/src/components/ai-elements/tool/**`, `app/src/components/ai-elements/terminal/**`, `app/src/lib/categories.ts`, `app/src/lib/format.ts` | PR-1 |
| **PR-5** Artifacts + Renderers | `app/src/components/workspace/**`, `app/src/components/renderers/**`, `app/src/components/ai-elements/artifact*`, `app/src/components/ai-elements/code-block*`, `app/src/components/ai-elements/web-preview*`, `app/src/components/ai-elements/sandbox*`, `app/src/components/ArtifactActions.tsx`, `app/src/components/Markdown.tsx` | PR-1 |

### Ola 3 (1 equipo, requiere Ola 2 mergeada)

| PR | Toca | Requiere |
|----|------|----------|
| **PR-7** Hardening | a11y, keyboard nav, virtualización, visual regression, high-volume streaming — toca muchos archivos pero SOLO añade/mejora, no rediseña | PR-3, PR-4, PR-5, PR-6 |

### Reglas de fusión
- Cada PR se mergea a `main` cuando pasa CI: `npm run lint && npm run typecheck
  && npm run build && npm test` en `app/`, y `python -m compileall -q munin tests
  scripts && python -m pytest -q` en la raíz (solo backend).
- Equipo de Ola 2: parte de `main` con PR-1 ya mergeado. Si PR-1 no aterrizó,
  espera (no trabajes sobre la rama de PR-1 — eso rompe paralelismo).
- Equipo de Ola 3: parte de `main` con PR-3/4/5/6 mergeados.
- **Nunca** dos equipos pisan la misma ruta en la misma ola. Si un prompt te
  lleva a tocar una ruta prohibida, ALTO: el prompt está mal, reporta al operador.

---

## 5. Reglas de proceso (todos los PRs)

1. Crea la rama EXACTA que indica tu prompt (`feat/issue-18-<n>-<slug>`).
2. Antes de editar: lee el archivo que vas a tocar. NO rompas imports existentes.
3. Después de editar: corre los comandos de verificación del prompt en orden.
4. Si un comando falla, ARREGLA antes de abrir el PR.
5. Commit con conventional commits en inglés: `feat(issue-18-1): ...`,
   `fix(issue-18-2): ...`, `docs(issue-18-1): ...`.
6. `git push -u origin <rama>` y `gh pr create --base main --head <rama> --title "..." --body-file <body>`.
7. **Nunca** push directo a `main`.
8. Actualiza `changes.md` (formato histórico del repo) en cada PR.
9. Si una decisión resulta no trivial, documenta en `docs/issue-18-<slug>.md`.
10. NO commitees secrets, `.env`, ni tokens. La API key de Context7
    (`ctx7sk-...`) y el `GH_TOKEN` solo viven en el runner, NUNCA en código.
