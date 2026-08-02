# Prompt PR-3 (Issue PR C) — AI Elements foundation + four-zone responsive shell + composer

> Issue: #18 · PR breakdown del issue: **PR C — AI Elements foundation and shell**
> **Requiere PR B (typed UI protocol) merged en `main`.** Paralelizable con PR D (execution UX) y PR E (workspace + renderers).
> Referencia autoritativa: Issue #18 comentarios C3 §1-3 (Global IA + Operations sidebar + Operation header), C4 §3-7 (AI SDK integration + AI Elements mapping), C5 §13 (frontend file plan), C7 §Product shell.

---

## 1. Objetivo

Reemplazar el actual `AppShell` two-column (`240px sidebar | AgentConsole`) por el **shell de cuatro zonas** exigido por el issue, adaptar primitivas AI Elements a React 18 + Tailwind 3.4 (NO `npx ai-elements@latest`), refactorizar el monolito `AgentConsole` para componer componentes tipados, y construir el composer dual Send/Guide + Detach/Cancel.

**No incluye** renderers de tools, terminal, HITL, artifacts (esos viven en PR D y PR E).

## 2. Rutas permitidas

- `app/src/components/AppShell.tsx` (EDIT — replace two-column con rail|operations|conversation|workspace)
- `app/src/components/PrimaryRail.tsx` (NUEVO — 48px icon rail)
- `app/src/components/ConversationSidebar.tsx` (EDIT — richer rows, action-required badge, archive section, search debounced)
- `app/src/components/AgentConsole.tsx` (EDIT — split monolith → compose `munin-ai/*` components; delega renderers a `lib/munin-ui/`)
- `app/src/components/ai-elements/` (NUEVO directorio):
  - `conversation.tsx`, `message.tsx`, `prompt-input.tsx`, `reasoning.tsx`, `tool.tsx`, `sources.tsx`, `queue.tsx`
- `app/src/components/munin-ai/` (NUEVO directorio):
  - `MuninConversation.tsx`, `MuninTurn.tsx`, `ExecutionGroup.tsx`, `FinalAnswer.tsx`, `ArtifactReference.tsx`, `EvidenceReference.tsx`, `GuidanceLifecycle.tsx`
  - (NOTE: `ToolExecutionGroup`, `CommandTerminal`, `SubagentGroup`, `HumanRequestCard` los crea PR D pero en este mismo dir; PR C crea solo los anteriores + placeholders si los necesita)
- `app/src/components/Composer.tsx` (NUEVO — Send/Guide + Detach/Cancel dual)
- `app/src/components/OperationHeader.tsx` (NUEVO — editable title, status chip, more menu)
- `app/src/components/KeyboardHelpDialog.tsx` (NUEVO — atajo help, también usado por PR F)
- `app/src/hooks/useKeyboardShortcuts.ts` (NUEVO — partial: `Cmd+K/B/J/`, `@`, `/`, `Escape`)
- `app/src/hooks/useStickToBottom.ts` (NUEVO — IntersectionObserver + unstick on scroll up)
- `app/src/hooks/useAnnouncer.ts` (NUEVO — `aria-live` polite announcer para SSE events; PR F refina)
- `app/src/lib/production-api.ts` (NUEVO — typed methods para conversation list con filtros `status`/`archived`/`cursor`, run list, run detail, run cancel; tanstack-query wrappers)
- `app/src/lib/aiChat.ts` (EDIT — surface `detachViewer`, `cancelRun`, dual Send/Guide logic)
- `app/src/app/globals.css` (EDIT — focus-visible rings, sr-only, scrollbars, `prefers-reduced-motion`)
- `app/tailwind.config.ts` (EDIT — keyframes `fade-slide`/`feather`/`blink`/`spine-flow`)
- `app/src/app/artifacts/[artifactId]/page.tsx` (NUEVO — `/artifacts/{id}` route para "Open in new view"; PR E lo rellena, PR C crea stub)
- `changes.md` (AÑADIR)

### Rutas prohibidas
- `munin/**`
- `app/src/lib/munin-ui/**` (PR B es dueño)
- `app/src/lib/chat/translator.ts` (PR B es dueño)
- `app/src/components/munin-ai/ToolExecutionGroup.tsx|CommandTerminal.tsx|SubagentGroup.tsx|HumanRequestCard.tsx` (PR D es dueño)
- `app/src/components/munin-ai/*Renderer.tsx` (PR E es dueño)

## 3. Spec: Shell cuatro zonas

Layout responsive (`AppShell.tsx`):

```tsx
export function AppShell() {
  const [railOpen, setRailOpen] = useState(true);
  const [opsOpen, setOpsOpen] = useState(true);
  const [wsOpen, setWsOpen] = useState(false);   // default CLOSED; abre cuando hay artifact

  return (
    <div className="h-screen w-screen bg-bg text-body font-sans overflow-hidden flex">
      <PrimaryRail onToggleOps={() => setOpsOpen(o => !o)} onToggleWs={() => setWsOpen(w => !w)} />
      {opsOpen && <ConversationSidebar className="w-[280px] shrink-0" />}
      <main className="flex-1 min-w-0 flex flex-col">
        <OperationHeader />
        <AgentConsole className="flex-1 min-h-0" workspaceSlot={
          <WorkspacePane open={wsOpen} onClose={() => setWsOpen(false)} />
        } />
        <Composer />
      </main>
    </div>
  );
}
```

### Responsive (`< 1024px`):
- Workspace → drawer RIGHT (`fixed inset-y-0 right-0 w-full max-w-md bg-surface border-l border-border z-floating`).
- Operations sidebar → drawer LEFT.
- `PrimaryRail` se compacta a 40 icons en < 768.

### `< 768px` (mobile):
- Single column. Sidebar → drawer inferior tipo bottom-sheet. Primary rail → bottom tab bar.

**Cero scroll horizontal de página** en todos los breakpoints.

## 4. `PrimaryRail.tsx` — 48px icon rail (C3 §1)

Botones verticales (todos con `aria-label` y tooltip Radix):
- **Operations** (icon `ListTree` lucide) → toggle de operations sidebar.
- **Run activity** (icon `Activity`) → abre `/runs` view que lista runs activos/waiting/failed cross-operation (PR E + `production-api.ts` consume `GET /api/runs?state=...`).
- **Artifacts** (icon `FileText`) → cross-operation artifact browser (`GET /api/artifacts?conversation_id=&run_id=&media_type=&renderer=&limit=&cursor=` — endpoint NUEVO de PR E).
- **Agents** (icon `Bird`) → vista subagents/workers (PR D + PR E alimentan).
- **Settings** (icon `Settings`) → user menu, provider profile, appearance.
- **Notifications** (icon `Bell` con badge) → solo cuenta actionable: HITL, failed runs, undelivered guidance, cancellation failures, completed long runs, artifact-ready. **NO** cuente heartbeats ni cada tool event.
- **Help** (icon `HelpCircle`) → `Cmd+/` abre `KeyboardHelpDialog`.

Botón activo: `bg-accent-soft text-accent border-l-2 border-accent`. Inactivo: `text-muted hover:text-body hover:bg-active`.

## 5. `ConversationSidebar.tsx` — richer rows (C3 §2, C5 §13)

Estiende, NO reemplaza. Hoy ya usa React Query. Añade:

- **Search** con debounce (300ms). States loading/empty/error. NO limpiar operación activa si está fuera de resultados.
- **New operation** primary button → `POST /api/conversations` con default title → select + focus composer. Dropdown futuro con templates (Investigation, IOC enrichment, Vuln assessment, Blank) — solo rellena title/tags/scope, NO bypass auth.
- **Refresh** explicit refetch. NO aggressive polling. Refetch on focus + post-mutation.
- **Operation row**:
  - title (click-to-rename via `PATCH /api/conversations/{id}`)
  - last activity time (mono `text-muted text-xs`)
  - durable run state chip (Running/Waiting for operator/Cancelling/Completed/Failed/Cancelled)
  - waiting-for-human badge (si action-required)
  - artifact count si > 0
  - archived indicator
- **Sections**: Recent / Running / Waiting for operator / Archived. Collapse each.
- **View all recent/archived** link → paginación cursor.
- **User menu** abajo (avatar, logout, provider profile).

**Backend filters** ya existen (`q=`, `status=`, `archived=`, `limit=`, `cursor=`). PR C los usa vía `production-api.ts` typed methods. **NO** un wrapper nuevo que solo use `q`.

## 6. AI Elements adaptados a `app/src/components/ai-elements/` (C4 §7)

**CRÍTICO**: NO ejecutar `npx ai-elements@latest`. Copia primitivas y reescribe CSS de Tailwind 4/React 19 a Tailwind 3.4/React 18 usando tokens `app/tailwind.config.ts`.

### 6.1 `conversation.tsx`

Adaptar de AI Elements `Conversation`, `ConversationContent`, `ConversationScrollButton`, `ConversationEmptyState`, `ConversationDownload` (opcional, con security review).

```tsx
export function Conversation({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("flex flex-col min-h-0 min-w-0", className)}>{children}</div>;
}
export function ConversationContent({ children, className }) {
  // scroller con ref para useStickToBottom
  return <div ref={ref} className={cn("flex-1 overflow-y-auto", className)}>{children}</div>;
}
export function ConversationScrollButton({ visible, onClick }) {
  return visible ? (
    <button onClick={onClick} aria-label="Jump to latest"
      className="absolute bottom-4 right-4 w-9 h-9 rounded-full bg-raised border border-borderStrong hover:bg-active flex items-center justify-center">
      <ArrowDown className="w-4 h-4" />
    </button>
  ) : null;
}
export function ConversationEmptyState({ onSuggestion }) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center gap-4 p-8 text-center">
      <img src="/raven-mark.png" alt="" className="w-16 h-16 opacity-70" />
      <div className="font-mono text-xs text-muted">// munin — war-raven ready</div>
      <Suggestion onSelect={onSuggestion} suggestions={["Resume from last checkpoint","Show recent artifacts","Run health check","List shared intel"]} />
    </div>
  );
}
```

Munin customizaciones (C4 §7 Conversation):
- scroll solo cuando `isStickToBottom` true (operator near tail).
- jump-to-latest button explicit.
- detached/reconnected viewer state visual (`text-info` ring en header si detached).
- virtualization deferred to PR F.

### 6.2 `message.tsx`

Adaptar `Message`, `MessageContent`, `MessageResponse`, `MessageActions`, `MessageAction`, `MessageToolbar`.

Munin customizaciones (C4 §7 Message):
- assistant execution objects (`ExecutionGroup`) ocupan **full readable execution width**, NO heredan `max-w-[80%]` del chat bubble.
- user messages compact (`max-w-fit`).
- final answer has stronger visual hierarchy (more padding, accent marker, `MessageResponse` component).
- links via allow-list (reusa util existing).
- code/table overflow contained (`min-w-0 w-full max-w-full overflow-x-auto`).
- copy uses rendered/redacted content, NO hidden unredacted backend payloads.

**Markdown renderer**: AI Elements Message usa streaming-md con depes propias. **Verificar compat** con el path ReactMarkdown existente en `app/src/components/Markdown.tsx` antes de reemplazar. Si rompe, mantener ReactMarkdown para texto y exponer hook futuro.

### 6.3 `prompt-input.tsx`

Adaptar `PromptInput`, `PromptInputHeader`, `PromptInputBody`, `PromptInputTextarea`, `PromptInputFooter`, `PromptInputSubmit`, `PromptInputTools`.

Munin customizaciones (C4 §7 PromptInput + C3 §6 Composer):
- **Primary action dual** según `run_state`:
  - `running`/`waiting_for_human`/`cancelling` → label "Guide active run", behavior send guidance.
  - `ready`/`completed`/`cancelled`/`failed`/sin run → label "Send", behavior normal `sendMessage`.
  - NUNCA convertir silently Send a guidance. Si hay 409 del BFF fallback, mostrar toast pidiendo clarificar action.
- `PromptInputTools` slot: ModeSwitcher existente, Attach (placeholder — PR E lo conecta a `POST /api/artifacts/uploads`), ProviderSwitcher (aplica al próximo turn con label claro).
- Adjacent controls: **Detach** (icon `Power`, tooltip "Detach: close local stream, run continues") + **Cancel** (icon `X`, confirmation dialog explicando: "cancellation is durable and replayable. Cancelled steps cannot be undone. Active jobs will be terminated.").
- Detach visible solo cuando `status === 'streaming'`.
- Cancel visible solo cuando `run_state in (running, waiting_for_human)`.

### 6.4 `reasoning.tsx`

Collapsible `<details>` solo para `provider_reasoning` parts. **NO** etiquetar `activity` (data-activity) como reasoning — activity es safe operational summary, debe ir al `ExecutionGroup` timeline no al Reasoning disclosure.

### 6.5 `tool.tsx` (placeholder — PR D lo pobla)

PR C crea el stubAI Elements `Tool`, `ToolHeader`, `ToolContent`, `ToolInput`, `ToolOutput` para que PR D los especialice con Munin lifecycle.

### 6.6 `sources.tsx` (C4 §7 Sources)

Render `data-source` parts como citation chip con URL validated.

```tsx
export function Sources({ sources }: { sources: SourceSchema[] }) {
  return (
    <div className="flex flex-wrap gap-2 mt-3">
      {sources.map(s => (
        <a key={s.id} href={s.url} target="_blank" rel="noopener noreferrer"
           className="inline-flex items-center gap-2 px-2 py-1 rounded-md bg-raised border border-border text-xs text-secondary hover:border-accent hover:text-accent">
          <ExternalLink className="w-3 h-3" /> {s.title}{s.provider ? ` · ${s.provider}` : ""}
        </a>
      ))}
    </div>
  );
}
```

Validar URLs con un allow-list (esquemas `http(s)://` OK; `javascript:`, `data:` rechazar).

### 6.7 `queue.tsx` (placeholder — PR D la usa para subagents/plan)

Adaptar AI Elements `Queue`/`Task` para high fan-out workers y plan items. NO es reemplazo de durable run state; solo renderer sobre typed data.

## 7. `app/src/components/munin-ai/MuninConversation.tsx`

```tsx
import { Conversation, ConversationContent, ConversationScrollButton, ConversationEmptyState } from "@/components/ai-elements/conversation";
import { groupPartsByStableId, GroupedTurn } from "@/lib/munin-ui/group-parts";
import { UIMessage } from "@ai-sdk/react";
import { useStickToBottom } from "@/hooks/useStickToBottom";

export function MuninConversation({ messages }: { messages: UIMessage[] }) {
  const { ref, isStickToBottom, jumpToBottom } = useStickToBottom();
  if (!messages.length) return <ConversationEmptyState onSuggestion={s => {/* TODO: setInput(s) via Composer */}} />;

  // group by role + turn boundary
  const turns = groupByAssistantTurn(messages);

  return (
    <Conversation>
      <ConversationContent ref={ref}>
        <div className="max-w-4xl mx-auto px-4 py-6 flex flex-col gap-6">
          {turns.map(t => <MuninTurn key={t.id} turn={t} />)}
        </div>
      </ConversationContent>
      <ConversationScrollButton visible={!isStickToBottom} onClick={jumpToBottom} />
    </Conversation>
  );
}
```

## 8. `MuninTurn.tsx` — Jerarquía visual FORTECIDA (C7 §Product shell)

Un turn del assistant debe agruparse:

```tsx
export function MuninTurn({ turn }: { turn: AssistantTurn }) {
  const grouped: GroupedTurn = useMemo(() => groupPartsByStableId(turn.parts), [turn]);

  return (
    <article aria-label={`assistant message at ${turn.timestamp}`} className="flex flex-col gap-3">
      {/* 1. Plan / current status (concise) - prime */}
      {grouped.plans.size > 0 && <PlanGroup plans={grouped.plans} />}

      {/* 2. Live operational activity - concise list */}
      {grouped.activities.length > 0 && <ExecutionGroup activities={grouped.activities} />}

      {/* 3. Tools + commands + subagents grouped by stable ID */}
      {grouped.tools.size > 0 || grouped.commands.size > 0 || grouped.subagents.size > 0 && (
        <ExecutionGroup tools={grouped.tools} commands={grouped.commands} subagents={grouped.subagents} />
      )}

      {/* 4. HITL approval cards */}
      {grouped.hitl.size > 0 && (
        <div className="flex flex-col gap-3">{Array.from(grouped.hitl.values()).map(h => <HumanRequestCard key={h.request_id} part={h} />)}</div>
      )}

      {/* 5. Artifacts + evidence references (compact, link to workspace) */}
      {grouped.artifacts.size > 0 && (
        <div className="flex flex-wrap gap-2">
          {Array.from(grouped.artifacts.values()).map(a => <ArtifactReference key={a.artifact_id} part={a} />)}
        </div>
      )}
      {grouped.evidence.size > 0 && (
        <div className="flex flex-wrap gap-2">
          {Array.from(grouped.evidence.values()).map(e => <EvidenceReference key={e.evidence_id} part={e} />)}
        </div>
      )}

      {/* 6. Sources cited */}
      {grouped.sources.size > 0 && <Sources sources={Array.from(grouped.sources.values())} />}

      {/* 7. Guidance lifecycle banner */}
      {grouped.guidance.size > 0 && <GuidanceLifecycle items={grouped.guidance} />}

      {/* 8. FINAL ANSWER — strongest visual hierarchy */}
      {turn.finalAnswer && (
        <FinalAnswer text={turn.finalAnswer} reasoning={grouped.providerReasoning} />
      )}

      {/* Provider reasoning disclosure (only explicit, NOT chain-of-thought fabrication) */}
      {grouped.providerReasoning.length > 0 && !turn.finalAnswer && (
        <Reasoning defaultOpen={false}>
          <ReasoningContent>{grouped.providerReasoning.join("\n\n")}</ReasoningContent>
        </Reasoning>
      )}
    </article>
  );
}
```

`FinalAnswer.tsx`:
```tsx
export function FinalAnswer({ text, reasoning }: { text: string; reasoning?: string[] }) {
  return (
    <div className="mt-4 rounded-lg border border-accent/40 bg-accent-soft/5 p-5">
      <div className="flex items-center gap-2 text-accent font-mono text-xs uppercase tracking-wide mb-3">
        <FeatherIcon className="w-4 h-4" /> Final Answer
      </div>
      <div className="prose prose-invert max-w-none text-body">
        <MarkdownRenderer content={text} />
      </div>
      {reasoning && reasoning.length > 0 && (
        <details className="mt-3 text-xs text-muted">
          <summary className="cursor-pointer hover:text-secondary">Provider reasoning</summary>
          <pre className="mt-2 whitespace-pre-wrap font-mono overflow-x-auto">{reasoning.join("\n\n")}</pre>
        </details>
      )}
    </div>
  );
}
```

## 9. `Composer.tsx` (C3 §6, C4 §7 PromptInput)

```tsx
export function Composer() {
  const { input, setInput, sendMessage, status, detachViewer, cancelRun, runState } = useMuninChat(convId);
  const isActiveRun = runState === "running" || runState === "waiting_for_human";
  const primaryLabel = isActiveRun ? "Guide active run" : "Send";
  const primaryAction = isActiveRun ? sendGuidance : () => sendMessage({ text: input });

  return (
    <div className="border-t border-border bg-surface px-4 py-3">
      <PromptInput>
        <PromptInputTools>
          <ModeSwitcher />        {/* existing */}
          {/* Attach placeholder — PR E conecta a /api/artifacts/uploads */}
        </PromptInputTools>
        <PromptInputBody>
          <PromptInputTextarea
            value={input}
            onChange={e => setInput(e.target.value)}
            placeholder={isActiveRun ? "Guide the active run…" : "Send a new message…"}
            minRows={1}
            maxRows={6}
          />
        </PromptInputBody>
        <PromptInputFooter>
          {status === "streaming" && (
            <Button variant="ghost" size="icon" onClick={detachViewer}
                    title="Detach: close local stream. Run continues.">
              <Power className="w-4 h-4" />
            </Button>
          )}
          {isActiveRun && (
            <Button variant="ghost" size="icon" onClick={onCancelClick}
                    title="Cancel run (durable)">
              <X className="w-4 h-4 text-danger" />
            </Button>
          )}
          <Button onClick={primaryAction} disabled={!input.trim() && !isActiveRun}>
            {primaryLabel}
          </Button>
        </PromptInputFooter>
      </PromptInput>
      <CancelConfirmDialog open={confirmOpen} onClose={() => setConfirmOpen(false)} onConfirm={() => cancelRun(runId)} />
    </div>
  );
}
```

`CancelConfirmDialog` explica (C3 §6):
> Active graph/model step may need cooperative cancellation. Cancellable command jobs will be terminated. Already-completed external side effects cannot be undone. Cancellation is durable and replayable.

## 10. `OperationHeader.tsx` (C3 §3)

- Editable title click-to-rename. Enter saves, Escape cancels, conflict toast + refetch, no silent overwrite.
- Run/operation status chip — informational. Suggested states: `Draft / Active / Running / Waiting for operator / Cancelling / Completed / Failed / Cancelled / Archived`. NO fake dropdown si no hay commands.
- Favorite icon (local pref first, durable later).
- Share (placeholder — futuro).
- More menu: Rename, Archive/unarchive, Export, Copy operation ID, Delete (con confirmation + optimistic version), Open run diagnostics.

## 11. `app/src/lib/production-api.ts` — typed API client (C5 §13)

```ts
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

export const productionApi = {
  async listConversations(params: { q?: string; status?: string; archived?: boolean; limit?: number; cursor?: string }) {
    const qs = new URLSearchParams(params as any).toString();
    const r = await fetch(`/api/conversations?${qs}`, { headers: { "X-CSRF-Token": getCsrf() } });
    if (!r.ok) throw new Error(`listConversations ${r.status}`);
    return r.json() as Promise<{ items: Conversation[]; next_cursor: string | null }>;
  },

  async listRuns(params: { state?: string; limit?: number; cursor?: string }) {
    const qs = new URLSearchParams(params as any).toString();
    const r = await fetch(`/api/runs?${qs}`, { headers: { "X-CSRF-Token": getCsrf() } });
    if (!r.ok) throw new Error(`listRuns ${r.status}`);
    return r.json() as Promise<{ items: RunSummary[]; next_cursor: string | null }>;
  },

  async runDetail(runId: string) {
    const r = await fetch(`/api/runs/${runId}/detail`, { headers: { "X-CSRF-Token": getCsrf() } });
    if (!r.ok) throw new Error(`runDetail ${r.status}`);
    return r.json();
  },

  async runCancel(runId: string, body: { reason: string; terminate_jobs: boolean }) {
    const r = await fetch(`/api/runs/${runId}/cancel`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": getCsrf() },
      body: JSON.stringify(body),
    });
    if (!r.ok && r.status !== 202 && r.status !== 200) throw new Error(`runCancel ${r.status}`);
    return r.json();
  },

  async listArtifacts(params: { conversation_id?: string; run_id?: string; media_type?: string; renderer?: string; limit?: number; cursor?: string }) {
    const qs = new URLSearchParams(params as any).toString();
    const r = await fetch(`/api/artifacts?${qs}`, { headers: { "X-CSRF-Token": getCsrf() } });
    if (!r.ok) throw new Error(`listArtifacts ${r.status}`);
    return r.json();
  },

  async listEvidence(params: { conversation_id?: string; run_id?: string }) {
    const qs = new URLSearchParams(params as any).toString();
    const r = await fetch(`/api/conversations/${params.conversation_id ?? ""}/evidence?${qs}`, { headers: { "X-CSRF-Token": getCsrf() } });
    if (!r.ok) throw new Error(`listEvidence ${r.status}`);
    return r.json();
  },
};

export function useListConversations(params) {
  return useQuery({
    queryKey: ["conversations", params],
    queryFn: () => productionApi.listConversations(params),
    staleTime: 30_000,
  });
}

export function useRunDetail(runId: string | null) {
  return useQuery({
    queryKey: ["run", runId],
    queryFn: () => productionApi.runDetail(runId!),
    enabled: !!runId,
    staleTime: 10_000,
  });
}

// useRunCancel, useRunList, useArtifacts, useEvidence… similar
```

## 12. `app/src/hooks/useKeyboardShortcuts.ts` (parcial — PR F refina)

Atajos implementados en PR C:
- `Cmd/Ctrl + K`: focus composer prompt
- `Cmd/Ctrl + B`: toggle operations sidebar
- `Cmd/Ctrl + J`: toggle workspace
- `Escape`: cerrar topmost modal/fullscreen
- `Cmd/Ctrl + /`: abrir `KeyboardHelpDialog`

PR F añade: `Cmd/Ctrl + Enter` (submit primary), `Shift + Enter` (newline), `@` (agent targeting), `/` (command palette).

Hook global `useEffect` keydown en `window`. Mod = `event.metaKey || event.ctrlKey`. Cuando target es `input`/`textarea`/`[contenteditable]`: solo `escape` y `Cmd+K`, `Cmd+B`, `Cmd+J` interceptados. `preventDefault` + `stopPropagation` cuando se intercepta.

## 13. Tailwind keyframes (en `app/tailwind.config.ts` si no existen)

```js
theme: { extend: { 
  keyframes: {
    "fade-slide": { "0%": { opacity: "0", transform: "translateY(8px)" }, "100%": { opacity: "1", transform: "translateY(0)" } },
    "feather": { "0%, 100%": { opacity: "0.4" }, "50%": { opacity: "1" } },
    "blink": { "0%, 50%": { opacity: "1" }, "50.01%, 100%": { opacity: "0" } },
    "spine-flow": { "0%": { backgroundPosition: "0% 0%" }, "100%": { backgroundPosition: "0% 100%" } },
  },
  animation: {
    "fade-slide": "fade-slide 0.25s ease-out",
    "feather": "fother 1.4s ease-in-out infinite",
    "blink": "blink 1s step-end infinite",
    "spine-flow": "spine-flow 4s linear infinite",
  },
}}
```

`globals.css` añadidos:
```css
*:focus-visible {
  outline: 2px solid var(--accent, #7c3aed);
  outline-offset: 2px;
  border-radius: 4px;
}
*:focus:not(:focus-visible) { outline: none; }
.sr-only {
  position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
  overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0;
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; }
}
```

## 14. Responsive fixtures (C3 §10)

Mínimas soportadas: **1366×768, 1440×900, 1920×1080, tablet (768×1024, 1024×768), mobile (375×667, 414×896)**.

PR C debe incluir un test Playwright (`app/playwright/responsive.spec.ts`) que navegue a la app vacía y verifique que **ningún fixture produce page-level horizontal scrollbar**:

```ts
for (const [w, h] of [[1366,768],[1440,900],[1920,1080],[768,1024],[1024,768],[375,667],[414,896]]) {
  await page.setViewportSize({ width: w, height: h });
  await page.goto("/");
  const hasHScroll = await page.evaluate(() => document.body.scrollWidth > document.body.clientWidth);
  expect(hasHScroll, `fixture ${w}x${h}`).toBe(false);
}
```

## 15. `app/src/app/artifacts/[artifactId]/page.tsx` — stub "Open in new view" (C3 §8)

```tsx
export default function ArtifactPage({ params }: { params: { artifactId: string } }) {
  return (
    <div className="h-screen bg-bg flex flex-col">
      <header className="h-12 border-b border-border flex items-center px-4 text-secondary text-sm font-mono">
        Artifact {params.artifactId}
      </header>
      <div className="flex-1 p-6 text-muted text-xs">
        {/* PR E rellena con fetch artifact by id + registry lookup */}
        Loading…
      </div>
    </div>
  );
}
```

## 16. Verificación

```bash
cd app
npm run lint
npm run typecheck
npm run build
npm test
npx playwright test responsive.spec.ts
```

## 17. Commit / PR

- Branch: `feat/issue-18c-shell-ai-elements`
- Commit: `feat(issue-18c): four-zone shell, AI Elements adapted primitives, dual composer, OperationHeader, production-api client`
- PR contra `main`. Requiere PR B merged.
