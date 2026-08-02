# Issue #18 — Prompt maestro: AI-native frontend workspace

> **Epic de referencia**: https://github.com/PrinceOfPwn/Munin/issues/18  
> **Diseño de referencia**: `docs/mockups/issue-18-ai-native-workspace/index.html` (ver `docs/mockups/issue-18-ai-native-workspace/README.md`)

## 0. Propósito y Arquitectura de Prompts

Este directorio contiene la especificación para construir la interfaz de usuario **AI-native** de Munin dividida en **7 PRs secuenciales y paralelizables**.
Cada prompt está diseñado con un nivel de detalle tan exhaustivo (código exacto, tipos Zod, firmas de funciones, rutas permitidas y prohibidas) que cualquier agente de IA de menor capacidad (Codex, Claude 3.5 Sonnet, GLM-4, etc.) puede ejecutarlo sin cometer errores arquitectónicos.

```
Ola 1 (3 IA en paralelo — parten de main):
  ├── PR-1: Contrato UX (Zod Schemas + Renderer Registry + Fixture Gallery)
  ├── PR-2: Defectos Backend (Cancelación Durable + Guidance Lifecycle E2E)
  └── PR-6: Backend Read-Model (Run Details + Event Enrichment + Artifacts API)

Ola 2 (3 IA en paralelo — requieren PR-1 en main):
  ├── PR-3: Shell 3 Zonas + Conversación (AI Elements Adaptados)
  ├── PR-4: Execution UX (Tool Cards, ANSI Terminal, Reasoning, Subagentes, HITL)
  └── PR-5: Artifact Workspace + Renderers Generativos + Sandbox HTML Seguro

Ola 3 (1 IA — requiere Ola 2 en main):
  └── PR-7: Production Hardening (a11y, Atajos, Virtualización, Test de Estrés)
```

---

## 1. Mapeo Completo del Stack Tecnológico (Verificado con Context7 y DeepWiki)

### A. Frontend (Next.js 15.5 + React 18.3 + Tailwind 3.4)
- **Vercel AI SDK UI v4** (`ai@7.0.47` y `@ai-sdk/react@4.0.50`):
  - `useChat<MyUIMessage>({ transport: new DefaultChatTransport({ api: '/api/chat' }), resume: true })`
  - Manejo de estado: `status` puede ser `'submitted' | 'streaming' | 'ready' | 'error'`.
  - Partes de mensaje (`UIMessage.parts`):
    - `text`: `{ type: 'text', text: string }`
    - `reasoning`: `{ type: 'reasoning', text: string }`
    - `tool-*`: `{ type: 'tool-<name>', toolCallId: string, state: 'input-streaming'|'input-available'|'output-available'|'output-error'|'approval-requested'|'approval-responded'|'output-denied', input: any, output?: any, errorText?: string }`
    - `data-*`: `{ type: 'data-<kind>', id: string, data: any }`
  - Servidor BFF (`createUIMessageStream`):
    - `writer.write({ type: 'start', messageId: string })`
    - `writer.write({ type: 'data-run-state', id: string, data: { state: 'running' } })`
    - `writer.write({ type: 'data-notification', data: { message: string }, transient: true })` (no se guarda en historial)
    - `writer.merge(result.toUIMessageStream())`

- **AI Elements** (`/vercel/ai-elements`):
  - **ADVERTENCIA DE COMPATIBILIDAD**: AI Elements oficial exige React 19 y Tailwind CSS 4. El proyecto Munin usa React 18.3 y Tailwind 3.4.
  - **Regla de Integración**: **NUNCA** ejecutes `npx ai-elements@latest add`. Copia las primitivas seleccionadas a `app/src/components/ai-elements/` y adapta la sintaxis de React 19 / Tailwind 4 a React 18 / Tailwind 3.4 usando las utilidades de `app/tailwind.config.ts`.

### B. Backend (Python 3.11 + Starlette + Deep Agents 0.7.1 + LangGraph)
- **Deep Agents 0.7.1** (`langchain-ai/deepagents`):
  - `create_deep_agent(interrupt_on={...}, middleware=[...])`
  - Interrupción Human-in-the-Loop: `interrupt_on` pausa la ejecución ante tool calls seleccionadas. El graph se detiene y emite un chunk `__interrupt__` en el stream `updates`.
  - Reanudación de interrupción: enviar `Command(resume={decisions: [...]})`.
  - Eventos de subagente: `SubagentStartEvent`, `SubagentCompleteEvent`, `SubagentErrorEvent` transmitidos en el canal `custom`.
  - Middleware hooks: `wrap_model_call` (ejecutado antes del LLM), `before_tool_call`, `after_tool_call`.
- **Cancelación Real Backend**:
  - Deep Agents y LangGraph NO poseen un endpoint nativo para cancelar un run desde el cliente.
  - Munin posee `store.request_run_cancellation(actor_id, run_id)` en `munin/production/store.py:1771` que establece `state='cancelled'` y borra el lease de ejecución (`fencing`).
  - El PR-2 expone esto vía HTTP `POST /api/chat/{conversation_id}/runs/{run_id}/cancel`.

---

## 2. Reglas de Oro Transversales

1. **El Backend Python es la Autoridad**: El frontend es un visualizador/controlador. El historial de eventos y el estado de la conversación viven duraderamente en SQLite/Turso (`munin/production/store.py`). Un refresh de navegador recarga el historial mediante `resume: true`.
2. **Paleta de Colores Inviolable (`app/tailwind.config.ts`)**:
   - Fondo Base: `bg-bg` (`#0a0a0f`)
   - Paneles/Tarjetas: `bg-surface` (`#111118`)
   - Tarjetas Anidadas: `bg-raised` (`#161623`)
   - Estados Hover/Selección: `bg-active` (`#1c1c2e`)
   - Bordes: `border-border` (`#1e1e2e`), `border-borderStrong` (`#2a2a3e`)
   - **Único Acento de Marca**: `accent` (`#7c3aed`) — Violeta
   - Semánticos: `success` (`#10b981`), `warning` (`#f59e0b`), `danger` (`#f43f5e`), `info` (`#38bdf8`)
   - **NUNCA escribas valores hexadecimales hardcodeados en clases CSS o componentes React.**
3. **Cero Scroll Horizontal de Página**:
   - Todo bloque de código, comando de terminal, tabla o JSON debe estar contenido en contenedores con `min-w-0 w-full max-w-full overflow-x-auto`.
4. **Seguridad Generativa**:
   - Queda estrictamente prohibido ejecutar JSX o JavaScript dinámico inyectado desde cadenas de texto del modelo (`eval`, `exec`, `Function()`).
   - El renderizado de HTML generado por el modelo DEBE realizarse exclusivamente dentro de un `<iframe sandbox="allow-scripts" srcdoc={content} />` endurecido sin `allow-same-origin`.
5. **Verificación de Calidad**:
   - Todo PR debe pasar `npm run lint`, `npm run typecheck`, `npm run build`, y `npm test` en `app/`.
   - Si toca código backend, debe pasar `python -m compileall -q munin tests scripts` y `pytest`.
