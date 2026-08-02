# Prompt PR-4 — Execution UX: Tool Cards, Reasoning, Subagentes, Terminal ANSI, HITL durable

> Issue: #18 · Fase 2 · Ola 2 · **Requiere PR-1 mergeado en `main`**
> Ejecutar en paralelo con `pr-3` y `pr-5`.
> Contexto compartido obligatorio: `docs/prompts/issue-18/00-master.md` — léelo primero.

---

## 1. Instrucciones para la IA ejecutora

Tu objetivo es construir los **renderers de ejecución**: cómo se visualiza tool-call lifecycle, razonamiento del modelo, subagentes, output de terminal con ANSI, e interrupciones Human-in-the-Loop.

La regla de oro: **consolidación por ID estable**. Un tool call aparece en el backend como `tool_intent → tool_started → tool_heartbeat* → tool_output* → tool_result`. En la UI esto se reconstruye en **una sola tarjeta** que evoluciona, no en 5 cards duplicados.

El contracto de eventos que recibes del backend está documentado en `app/src/lib/chat/translator.ts` (27 `BackendEnvelopeKind`). Léelo antes de empezar.

---

## 2. Rutas Permitidas

- `app/src/renderers/components/` (NUEVO — todos los renderers de ejecución):
  - `ToolCard.tsx`, `ToolIntent.tsx`, `ToolResult.tsx`, `ToolOutput.tsx`
  - `ReasoningBlock.tsx`
  - `SubagentBadge.tsx`, `SubagentCard.tsx`
  - `TerminalOutput.tsx`
  - `HumanInterruptCard.tsx`
  - `RunStatePill.tsx`
- `app/src/renderers/index.ts` (NUEVO — registration de los renderers anteriores en el registry de PR-1)
- `app/src/lib/chat/translator.ts` (EDITAR — solo añadir tipos Guidance/HumanInterrupt nuevos que PR-2 introduce)
- `app/src/lib/chat/useExecutionStream.ts` (NUEVO — hook de reconciliación por tool_call_id)
- `app/src/components/chat/` (EDITAR solo si la integración con FlightDeck lo exige; no refactor masivo)
- `changes.md` (AÑADIR entrada)

### Rutas Prohibidas
- `munin/**`
- `app/src/types/**`
- `app/src/renderers/registry.ts` (PR-1 es dueño del core; PR-4 solo registra entries)

---

## 3. Spec: ToolCard — Máquina de Estados de Consolidación

### 3.1 Hook `useExecutionStream`

Lee el stream SSE del backend (vía `aiChat.ts`) y produce un `Map<tool_call_id, ToolAggregate>`:

```tsx
interface ToolAggregate {
  tool_call_id: string;
  tool_name: string;
  input?: unknown;
  state: "intent" | "running" | "completed" | "failed";
  output_chunks: string[];          // tool_output + tool_heartbeat se concatenan aquí
  final_result?: string;
  final_error?: string;
  elapsed_ms?: number;
  started_at_ms?: number;
  completed_at_ms?: number;
}
```

Lógica:
- `tool_intent` → crea entrada con `state="intent"`.
- `tool_started` → actualiza `state="running"`, `started_at_ms`.
- `tool_output` → push a `output_chunks`.
- `tool_heartbeat` → NO se muestra como output, solo actualiza un reloj visual `elapsed_ms`.
- `tool_result` → `state="completed"`, `final_result`, `elapsed_ms`.
- `tool_failed` → `state="failed"`, `final_error`.

### 3.2 `ToolCard.tsx`

Estructura (estructura unificada AI Elements `Tool`): Header (state pill + tool_name con icono lucide apropiado) + Content colapsable (Input en `pre` monospace + Output terminal en uno de dos modos: stream scrollback si output_chunks.length > 0, render final si tool_result).

Estados visuales:
- `intent` → pill `text-muted` con dot animado
- `running` → pill `info` (`#38bdf8`) con spinner
- `completed` → pill `success` con check
- `failed` → pill `danger` con icono `AlertTriangle`

Selección de icono por `tool_name`:
- `gen__*` → `Wand2` (accent)
- `nmap_*` / `nuclei_*` / `ffuf_*` / `katana_*` / `feroxbuster_*` / `httpx_*` → `Radar` (rose)
- `ldap_*` → `Users` (info)
- `cve_*` / `exploit_*` / `tavily_*` / `hugin_*` → `BookOpen` (text-secondary)
- `valravn_*` → `Feather`
- `munin_*` → `Bird`
- default → `Wrench`

**Stick-to-bottom en streaming de output**: cuando output_chunks crece, el content scrollea al fondo solo si `isStickToBottom === true` (hook de PR-3).

---

## 4. `ReasoningBlock.tsx`

Componente para partes `provider_reasoning`. Reusa `Reasoning.tsx` de PR-3 (`<details>` collapsible por defecto). Cuando `is_streaming=true` muestra dot animado en `accent`. Texto en `font-mono text-xs text-muted overflow-x-auto`.

---

## 5. `SubagentCard.tsx` y `SubagentBadge.tsx`

Consulta Context7 de `langchain-ai/deepagents`. Los subagentes emiten:
- `SubagentStartEvent` con `eval_id`, `subagent_type`, `label`, `description`
- `SubagentCompleteEvent` con `eval_id`, `summary`, `duration_ms`

Renderiza `SubagentBadge`: chip horizontal con icono `Bird` violeta hovering entre elementos: `<div className="inline-flex items-center gap-2 px-2 py-0.5 rounded-full bg-accent-soft text-accent text-xs border border-accent/30">`.

`SubagentCard` expande al click: header con subagent_type + duration_ms en mono, body con `summary` en markdown, y mini-timeline de su lifecycle.

---

## 6. `TerminalOutput.tsx`

Salida de `command_output` (tool calls `execute_command`, `web_evidence_screenshotter`, etc.).

- Usar `ansi-to-react` (dep ya instalada en Munin) para renderizar escapes ANSI coloreados.
- Contenedor: `bg-bg border border-border rounded font-mono text-xs p-2 max-h-72 overflow-y-auto overflow-x-auto` (cero scroll-horizontal de página garantizado).
- Auto-stick-to-bottom usando el mismo hook de PR-3.
- Árbol de salida finalizada: `border-l-2 border-success/60` a la izquierda del último chunk tras `is_final=true`.

---

## 7. `HumanInterruptCard.tsx`

Renderiza partes `human_interrupt` (originadas por `__interrupt__` en el stream LangGraph).

```tsx
function HumanInterruptCard({ part }: RendererProps<HumanInterruptPart>) {
  return (
    <AlertDialog open={true}>
      <AlertDialogContent className="bg-raised border border-accent/40">
        <AlertDialogHeader>
          <AlertDialogTitle className="text-body font-mono text-base">
            <Shield className="inline mr-2 text-accent" /> HITL — Operator decision required
          </AlertDialogTitle>
          <AlertDialogDescription className="text-secondary text-sm whitespace-pre-wrap">
            {part.prompt}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="flex flex-col gap-2 mt-2">
          {part.options.map(opt => (
            <Button key={opt} variant="outline" onClick={() => respond(opt)}>
              {opt}
            </Button>
          ))}
          <Button variant="ghost" className="text-muted text-xs" onClick={() => respond("__reject__")}>
            Reject (no resume)
          </Button>
        </div>
        {part.expires_at_ms && (
          <CountdownTimer expiresAt={part.expires_at_ms} />
        )}
      </AlertDialogContent>
    </AlertDialog>
  );
}
```

`respond(decision)` hace POST a `/api/chat/{conv}/runs/{run}/respond` con `{nonce, decision}` — el backend responde con `Command(resume={decisions: [decision]})` para reanudar el graph.

Usa `AlertDialog` de Radix (ya instalado).

---

## 8. `RunStatePill.tsx`

Estado del run global para el header de la pane de conversación:
- `queued` → `text-muted` con dot pulsante
- `running` → `success` con spinner
- `waiting_for_human` → `warning` con icono `Hand`
- `cancelling` → `warning` con icono `X`
- `cancelled` → `text-muted` con icono `X`
- `completed` → `success` con check
- `failed` → `danger` con `AlertTriangle`
- `interrupted` → `text-muted` con `Zap`

---

## 9. Registro en `renderers/index.ts`

```tsx
import { registry } from "./registry";
import { ToolCard } from "./components/ToolCard";
// ...imports

export function registerExecutionRenderers() {
  // PR-1 solo creó el registry y los schemas. PR-4 pobla los renderers:
  // NOTA: registry.register solo se usa cuando artifact.renderer_key coincide.
  // Para los demás kinds, usar switch en Message.tsx según PR-1 doc
  // o crear un RendererResolver central.
}
```

Implementa un `RendererResolver` central en `app/src/renderers/index.ts` que decide, dado un `MuninPart`:
- `assistant_text` → markdown renderer (PR-3 / existente)
- `provider_reasoning` → `ReasoningBlock`
- `tool_intent|tool_started|tool_heartbeat|tool_output` → `ToolCard` (via `useExecutionStream` aggregation)
- `tool_result|tool_failed` → `ToolCard`
- `command_output` → `TerminalOutput`
- `subagent_lifecycle` → `SubagentCard`
- `human_interrupt` → `HumanInterruptCard`
- `run_state` → `RunStatePill`
- `artifact` → registry lookup por `renderer_key`
- `operator_guidance` → ver PR-2 lifecycle, mostrar como banner top con status badge

---

## 10. Verificación

```bash
cd app
npm run lint
npm run typecheck
npm run build
```

## 11. Commit / PR

- Branch: `feat/issue-18-4-execution-ux`
- Commit: `feat(issue-18-4): tool cards, ANSI terminal, subagent & HITL renderers with ID-stable consolidation`
- PR contra `main`. Cuerpo del PR debe referenciar que **requiere PR-1 merged**.
