# Prompt PR-3 — Shell 3 Zonas + Conversación (AI Elements Adaptados)

> Issue: #18 · Fase 2 · Ola 2 · **Requiere PR-1 mergeado en `main`**
> Ejecutar en paralelo con `pr-4` y `pr-5`.
> Contexto compartido obligatorio: `docs/prompts/issue-18/00-master.md` — léelo primero.

---

## 1. Instrucciones para la IA ejecutora

Tu objetivo es construir el **shell principal** de la consola operacional Munin con tres zonas responsivas, reemplazando el layout actual por uno **AI-native** que soporta flujo de conversación vertical con auto-scroll inteligente.

Debes ADAPTAR primitivas de AI Elements a **React 18.3 + Tailwind 3.4** (el repo NO está en React 19 / Tailwind 4). **NUNCA** ejecutes `npx ai-elements@latest add`. Crea los componentes a mano en `app/src/components/ai-elements/`.

---

## 2. Rutas Permitidas

- `app/src/components/ai-elements/` (NUEVO directorio completo):
  - `Conversation.tsx`, `Message.tsx`, `PromptInput.tsx`, `Reasoning.tsx`, `Suggestion.tsx`
- `app/src/components/FlightDeckStable.tsx` (EDITAR — orquestación del shell)
- `app/src/components/AgentConsole.tsx` (EDITAR — consumir nuevo Conversation)
- `app/src/app/page.tsx` (EDITAR — mínimo cambio de layout si necesario)
- `app/src/app/globals.css` (EDITAR — solo scrollbar y a11y focus-ring)
- `app/src/hooks/useStickToBottom.ts` (NUEVO — stick-to-bottom con unstick en scroll up)
- `app/src/lib/aiChat.ts` (EDITAR — surface `status` y `stop` a la UI)
- `app/tailwind.config.ts` (EDITAR — añadir keyframes `fade-slide`/`feather`/`blink`/`spine-flow` si no están)
- `changes.md` (AÑADIR entrada)

### Rutas Prohibidas
- `munin/**`
- `app/src/types/**` (PR-1 es dueño)
- `app/src/renderers/**` (PR-1 es dueño)
- `app/src/lib/chat/translator.ts` (frozen hasta PR-4)
- `app/src/extensions/**`

---

## 3. Spec: Shell 3 Zonas

Layout responsivo usando CSS Grid: en desktop (≥1024px) tres columnas `[sidebar 260px | conversation 1fr | workspace 480px]`; en tablet (768-1023px) dos columnas con workspace colapsable; en móvil (<768px) single column con tabs inferiores.

```tsx
// app/src/components/FlightDeckStable.tsx (esqueleto)
export function FlightDeckStable() {
  return (
    <div className="h-screen w-screen bg-bg text-body font-sans overflow-hidden">
      <Header /> {/* 48px alto; bg-surface, border-b border-border */}
      <main className="grid grid-cols-1 lg:grid-cols-[260px_1fr] xl:grid-cols-[260px_1fr_480px] gap-0 h-[calc(100vh-48px)]">
        <Sidebar />            {/* ConversationSidebar existente, bg-surface border-r border-border */}
        <ConversationPane />   {/* AgentConsole refactor; bg-bg */}
        <WorkspacePane />      {/* PR-5 lo rellena. PR-3 crea placeholder con tabs vacíos */}
      </main>
    </div>
  );
}
```

### 3.1 `Conversation.tsx` (AI Elements adaptado)

Props contract (Context7 `/vercel/ai-elements`):

```tsx
interface ConversationProps {
  messages: UIMessage[];
  isStreaming: boolean;
  onScrollTop?: () => void;   // paginación futura
  autoScroll?: boolean;       // default true
}
```

Implementación:
- Contenedor: `flex flex-col min-h-0 min-w-0` (cero scroll-horizontal garantía).
- Cada message envuelto por `<Message role={msg.role} parts={msg.parts} />`.
- `ConversationScrollButton`: aparece cuando `!isStickToBottom`; botón circular 32px abajo-derecha con icono `ArrowDown` de lucide-react, bg-raised border border-borderStrong hover:bg-active.
- `ConversationEmptyState`: cuando `messages.length === 0` → muestra raven-mark.png (`/raven-mark.png`) centrada + tagline en `text-muted font-mono text-xs`: `// munin — war-raven ready`.
- Auto-scroll: usa `app/src/hooks/useStickToBottom.ts` (basado en IntersectionObserver sobre sentinel `<div>` al final + `scroll` listener para detectar unstick).

### 3.2 `Message.tsx`

- Avatar 32px circular a la izquierda (raven-mark.png para assistant, placeholder gris para user).
- Burbuja: `bg-surface` para user, `bg-raised` para assistant. Border border-border rounded-lg p-3.
- Itera `msg.parts` y para cada parte usa el **Renderer Registry** de PR-1: `registry.validateAndResolve(part)`. **No renderices partes con switch/case aquí** — eso es trabajo del registry.

### 3.3 `PromptInput.tsx` (AI Elements adaptado)

Firma exacta (Context7):
- `PromptInputHeader` (opcional, slot para tabs/mode)
- `PromptInputBody` → `PromptInputTextarea` (autogrow, max-h-40, resize-none)
- `PromptInputTools` (slot izquierda: ModeSwitcher, Anexos, ModelPicker)
- `PromptInputFooter` → `PromptInputSubmit` (botón circular con icon Send; cuando `status==='streaming'` muestra icon Stop y llama `stop()`)

Validación: input vacío deshabilita submit. Multi-linea soporta `Shift+Enter` para newline, `Enter` para submit (salvo si `multiline` mode activo).

### 3.4 `Reasoning.tsx`

Componente `<details>` collapsible por defecto. Cuando `isStreaming=true` muestra dot animado (keyframe `feather`). Icono `Sparkles` de lucide-react color `accent`. Texto en `font-mono text-xs text-muted overflow-x-auto`.

### 3.5 `Suggestion.tsx`

Chips de prompt sugeridos en `ConversationEmptyState`:
- `bg-active border border-borderStrong rounded-full px-3 py-1 text-xs hover:border-accent hover:text-accent`
- 4 ejemplos hardcodeados: `"Resume from last checkpoint"`, `"Show recent artifacts"`, `"Run health check"`, `"List shared intel"` → `onSelect(text)` propaga al input.

---

## 4. `app/src/lib/aiChat.ts` — Surfacear `status` y `stop`

El hook actual `useMuninChat` ya usa `useChat` de `@ai-sdk/react` con `DefaultChatTransport` y `resume: true`. PR-3 NO debe romper esto. Solo expón a la UI:

```tsx
export function useMuninChat(convId: string) {
  const chat = useChat({ transport: ..., resume: true });
  // PR-3: surface explícito
  return {
    messages: chat.messages,
    input: chat.input,
    setInput: chat.setInput,
    status: chat.status,           // 'submitted'|'streaming'|'ready'|'error'
    stop: chat.stop,                // abort local reader
    sendMessage: chat.sendMessage,
    error: chat.error,
  };
}
```

---

## 5. Tailwind Keyframes a añadir (si no existen)

En `app/tailwind.config.ts` → `theme.extend.keyframes`:

```js
"fade-slide": {
  "0%": { opacity: "0", transform: "translateY(8px)" },
  "100%": { opacity: "1", transform: "translateY(0)" },
},
"feather": {
  "0%, 100%": { opacity: "0.4" },
  "50%": { opacity: "1" },
},
"blink": {
  "0%, 50%": { opacity: "1" },
  "50.01%, 100%": { opacity: "0" },
},
"spine-flow": {
  "0%": { backgroundPosition: "0% 0%" },
  "100%": { backgroundPosition: "0% 100%" },
},
```

Con `animation`:
- `"fade-slide": "fade-slide 0.25s ease-out"`
- `"feather": "feather 1.4s ease-in-out infinite"`
- `"blink": "blink 1s step-end infinite"`
- `"spine-flow": "spine-flow 4s linear infinite"`

Y media query `@media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation: none !important; transition: none !important; } }` en `globals.css`.

---

## 6. Verificación

```bash
cd app
npm run lint
npm run typecheck
npm run build
```

## 7. Commit / PR

- Branch: `feat/issue-18-3-shell-conversation`
- Commit: `feat(issue-18-3): shell 3 zonas + AI Elements conversational primitives adaptadas a React 18`
- PR contra `main`.
