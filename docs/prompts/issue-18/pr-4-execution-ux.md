# Prompt PR-4 (Issue PR D) — Execution UX: grouped tools, terminal fullscreen/wrap/transcript, subagents, HITL, reasoning split

> Issue: #18 · PR breakdown del issue: **PR D — execution UX**
> **Requiere PR B (typed UI protocol) + PR C (shell) merged en `main`.** Paralelizable con PR E (workspace + renderers).
> Referencia autoritativa: Issue #18 comentarios C3 §4-5 (Conversation + Command surface), C4 §7 Tool/Queue mapping, C5 §13 AgentConsole plan, C7 §4 Execution + §5 Terminal + §11 HITL.

---

## 1. Objetivo

Reconstruir la experiencia de ejecución del agente: tools agrupados por `tool_call_id`, terminal unificado con wrap/fullscreen/copy/download-transcript, subagents/workers en vistas detalladas, reasoning/activity separation SOLO para explícito provider reasoning, HITL cards con redacted args + risk + evidence (NO solo prompt+options).

Agrega un endpoint backend: `GET /api/runs/{run_id}/commands/{job_id}/transcript`.

## 2. Rutas permitidas

### Backend
- `munin/production/chat.py` (EDIT — `GET /api/runs/{run_id}/commands/{job_id}/transcript` endpoint, auth + authz)
- `munin/production/store.py` (EDIT — método `get_command_transcript(run_id, job_id, format)` que reconstruya desde `run_events`)
- `tests/test_command_transcript.py` (NUEVO)

### Frontend
- `app/src/components/munin-ai/ToolExecutionGroup.tsx` (NUEVO)
- `app/src/components/munin-ai/CommandTerminal.tsx` (NUEVO)
- `app/src/components/munin-ai/SubagentGroup.tsx` (NUEVO)
- `app/src/components/munin-ai/HumanRequestCard.tsx` (NUEVO)
- `app/src/components/munin-ai/ExecutionGroup.tsx` (NUEVO — wrapper que orquesta los anteriores por turn)
- `app/src/components/munin-ai/GuidanceLifecycle.tsx` (NUEVO — banner de estados del guidance; PR B ya define el schema)
- `app/src/components/ai-elements/tool.tsx` (EDIT — adaptar ToolHeader/ToolInput/ToolOutput; PR C creó stub)
- `app/src/components/ai-elements/queue.tsx` (EDIT — adaptar Queue/Task para subagent fan-out y plan.items)
- `app/src/lib/production-api.ts` (EDIT — `getCommandTranscript(runId, jobId, format)`)
- `app/src/components/__tests__/ToolExecutionGroup.test.tsx` (NUEVO)
- `app/src/components/__tests__/CommandTerminal.test.tsx` (NUEVO)
- `app/src/components/__tests__/HumanRequestCard.test.tsx` (NUEVO)
- `app/src/components/__tests__/SubagentGroup.test.tsx` (NUEVO)
- `changes.md` (AÑADIR)

### Rutas prohibidas
- `munin/production/store.py:` — cualquier cambio fuera de `get_command_transcript`
- `app/src/components/ai-elements/conversation.tsx|message.tsx|prompt-input.tsx|reasoning.tsx|sources.tsx` (PR C es dueño; PR D solo consume)
- `app/src/lib/munin-ui/**` (PR B es dueño)
- `app/src/components/munin-ai/MuninTurn.tsx|FinalAnswer.tsx|ArtifactReference.tsx|EvidenceReference.tsx|MuninConversation.tsx` (PR C es dueño; PR D se pluga vía `ExecutionGroup` slot)

## 3. Endpoint backend: command transcript (C3 §5 Download transcript, C5 §13)

### `GET /api/runs/{run_id}/commands/{job_id}/transcript?format=text|json`

```python
async def command_transcript_endpoint(request: Request) -> Response:
    run_id = request.path_params["run_id"]
    job_id = request.path_params["job_id"]
    fmt = request.query_params.get("format", "text")
    if fmt not in ("text", "json"): return _bad_request()

    actor = await _require_auth(request)
    if actor is None: return _unauthorized()
    if not await _can_view_run(actor, run_id): return _forbidden()

    transcript = await store.get_command_transcript(run_id=run_id, job_id=job_id, format=fmt)

    if fmt == "json":
        return JSONResponse(transcript)
    else:
        # text: incluye header con metadata + redaction policy version
        text = f"# Run {run_id} Job {job_id}\n" \
               f"# Tool: {transcript['tool_name']}\n" \
               f"# Command: {transcript['command']}\n" \
               f"# Elapsed: {transcript['elapsed_ms']} ms\n" \
               f"# Redaction: {transcript['redaction_policy_version']}\n\n"
        for chunk in transcript["chunks"]:
            text += chunk["stream"] + ": " + chunk["data"] + "\n"
        return PlainTextResponse(text, headers={"Content-Disposition": f"attachment; filename=run-{run_id}-job-{job_id}.log"})

# Register:
routes.append(Route("/api/runs/{run_id}/commands/{job_id}/transcript", command_transcript_endpoint, methods=["GET"]))
```

### `store.get_command_transcript`

Lee `run_events` ordering por `event_id` donde `payload.tool_call_id` coincide y `job_id == job_id`. Reconstruye stream chunks (stdout/stderr/meta) ordenados por `sequence`. Incluye `redaction_policy_version` (default `"v1"`).

## 4. `ToolExecutionGroup.tsx` — agrupado por `tool_call_id` (C3 §4 Tool card, C7 §4)

```tsx
import { ToolAggregate } from "@/lib/munin-ui/group-parts";
import { ChevronDown, AlertTriangle, Loader2, Check, Zap } from "lucide-react";

const TOOL_ICONS: Record<string, LucideIcon> = {
  // patterns
  "gen__": Wand2,
  "nmap_": Radar, "nuclei_": Radar, "ffuf_": Radar, "katana_": Radar,
  "feroxbuster_": Radar, "httpx_": Radar, "katana_": Radar,
  "ldap_": Users,
  "cve_": BookOpen, "exploit_": BookOpen, "tavily_": BookOpen, "hugin_": BookOpen,
  "valravn_": Feather, "munin_": Bird,
};
const fallbackIcon = Wrench;

export function ToolCard({ agg }: { agg: ToolAggregate }) {
  const [expanded, setExpanded] = useState(agg.state === "intent" || agg.state === "running");
  const ToolIcon = selectToolIcon(agg.tool_name);
  const statePill = STATE_PILLS[agg.state];

  return (
    <div className="w-full min-w-0 rounded-lg border border-border bg-raised my-2">
      <button
        onClick={() => setExpanded(e => !e)}
        aria-expanded={expanded}
        aria-label={`tool ${agg.tool_name} ${agg.state}`}
        aria-busy={agg.state === "running" || agg.state === "intent"}
        className="w-full flex items-center gap-2 p-3 hover:bg-active transition-colors text-left"
      >
        <ChevronDown className={cn("w-4 h-4 text-muted transition-transform", expanded && "rotate-90")} />
        <ToolIcon className="w-4 h-4 text-accent" />
        <span className="font-mono text-sm text-body">{agg.tool_name}</span>
        {agg.elapsed_ms && <span className="text-xs text-muted font-mono">{agg.elapsed_ms}ms</span>}
        <span className="ml-auto">{statePill}</span>
      </button>
      {expanded && (
        <div className="border-t border-border p-3 flex flex-col gap-3 max-h-[480px] overflow-y-auto">
          {agg.input && (
            <section>
              <h4 className="text-xs uppercase text-muted font-mono mb-1">Input</h4>
              <pre className="text-xs font-mono whitespace-pre-wrap break-all min-w-0 max-w-full overflow-x-auto">{JSON.stringify(agg.input, null, 2)}</pre>
              <button onClick={() => navigator.clipboard.writeText(JSON.stringify(agg.input, null, 2))} className="text-xs text-accent hover:underline mt-1">Copy input</button>
            </section>
          )}
          {agg.output_chunks.length > 0 && (
            <CommandTerminalLive chunks={agg.output_chunks} toolCallId={agg.tool_call_id} />
          )}
          {agg.final_result && (
            <section>
              <h4 className="text-xs uppercase text-muted font-mono mb-1">Output</h4>
              <pre className="text-xs font-mono whitespace-pre-wrap break-all min-w-0 max-w-full overflow-x-auto">{agg.final_result}</pre>
              <button onClick={() => navigator.clipboard.writeText(agg.final_result!)}>Copy output</button>
            </section>
          )}
          {agg.final_error && (
            <section>
              <h4 className="text-xs uppercase text-danger font-mono mb-1 flex items-center gap-1"><AlertTriangle className="w-3 h-3" /> Error</h4>
              <pre className="text-xs font-mono text-danger whitespace-pre-wrap break-all min-w-0 overflow-x-auto">{agg.final_error}</pre>
            </section>
          )}
          {agg.artifact_ids.length > 0 && (
            <section className="text-xs text-secondary">
              Artifacts: {agg.artifact_ids.map(id => <span key={id} className="text-accent hover:underline cursor-pointer">{id}</span>)}
            </section>
          )}
        </div>
      )}
    </div>
  );
}
```

`STATE_PILLS`:
```tsx
const STATE_PILLS = {
  intent:     <Pill className="text-muted bg-active"><span className="w-1.5 h-1.5 rounded-full bg-muted animate-feather" /> queued</Pill>,
  running:    <Pill className="text-info bg-info/10"><Loader2 className="w-3 h-3 animate-spin" /> running</Pill>,
  completed:  <Pill className="text-success bg-success/10"><Check className="w-3 h-3" /> completed</Pill>,
  failed:     <Pill className="text-danger bg-danger/10"><AlertTriangle className="w-3 h-3" /> failed</Pill>,
} as const;
```

## 5. `CommandTerminal.tsx` — terminal unificado (C3 §5, C7 §5)

Implementa:
- **stdout/stderr/metadata tabs** (default stdout).
- **Wrap toggle** on/off. Off → `whitespace-pre` + `overflow-x-auto` INTERNO (no page). On → `whitespace-pre-wrap break-all`.
- **Copy command** (redacted command preview).
- **Copy output** (del stream activo).
- **Fullscreen** modal con sticky header, stream tabs, **search dentro del output**, wrap toggle, copy, download, focus trap, Esc to close, accessible title.
- **Download transcript** via `GET /api/runs/{run_id}/commands/{job_id}/transcript?format=text|json`.
- **Sticky header** mientras output scrollea (`sticky top-0 z-10 bg-surface`).
- **Line numbers** opt-in.
- **Preserved ordering** por `sequence`.
- **Sanitized ANSI rendering** only (`ansi-to-react` ya instalado).
- NO raw terminal HTML injection.

```tsx
export function CommandTerminal({ agg }: { agg: CommandAggregate }) {
  const [tab, setTab] = useState<"stdout"|"stderr"|"meta">("stdout");
  const [wrap, setWrap] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

  const chunks = agg.streams[tab];
  const transcriptUrl = `/api/runs/${agg.run_id}/commands/${agg.job_id}/transcript`;

  return (
    <>
      <div className="rounded-md border border-border bg-bg overflow-hidden">
        {/* Sticky header */}
        <div className="sticky top-0 z-10 bg-surface border-b border-border px-3 py-2 flex items-center gap-2">
          <span className="font-mono text-xs text-muted">job {agg.job_id}</span>
          {(["stdout","stderr","meta"] as const).map(t => (
            <button key={t} onClick={() => setTab(t)} className={cn("text-xs px-2 py-0.5 rounded", tab === t ? "bg-active text-accent" : "text-muted hover:text-body")}>{t}</button>
          ))}
          <button onClick={() => setWrap(w => !w)} title="Wrap lines" className={cn("text-xs px-2 py-0.5 rounded", wrap ? "bg-active text-accent" : "text-muted hover:text-body")}>Wrap</button>
          <button onClick={() => navigator.clipboard.writeText(chunks.join(""))} className="text-xs px-2 py-0.5 rounded text-muted hover:text-body">Copy</button>
          <a href={transcriptUrl} download className="text-xs px-2 py-0.5 rounded text-muted hover:text-body">Download</a>
          <button onClick={() => setFullscreen(true)} title="Fullscreen" className="text-xs px-2 py-0.5 rounded text-muted hover:text-body"><Maximize2 className="w-3 h-3" /></button>
          {agg.final && <span className="ml-auto text-xs text-success font-mono">final</span>}
        </div>

        {/* Output */}
        <div className={cn("max-h-72 overflow-y-auto overflow-x-auto", wrap ? "whitespace-pre-wrap break-all" : "whitespace-pre")}>
          {chunks.map((chunk, i) => <AnsiToReact key={i} useClasses>{chunk}</AnsiToReact>)}
        </div>
      </div>

      {fullscreen && <FullscreenTerminal agg={agg} tab={tab} wrap={wrap} searchRef={searchRef} onClose={() => setFullscreen(false)} transcriptUrl={transcriptUrl} />}
    </>
  );
}
```

## 6. `SubagentGroup.tsx` — workers fan-out (C7 §10 Agents)

NO un card por subagent state event. Usa `SubagentAggregate` (PR B).

```tsx
export function SubagentGroup({ subagents }: { subagents: Map<string, SubagentAggregate> }) {
  const arr = Array.from(subagents.values());
  if (!arr.length) return null;
  return (
    <section aria-label="subagents" className="my-3">
      <h3 className="text-xs uppercase text-muted font-mono mb-2 flex items-center gap-1"><Bird className="w-3 h-3 text-accent" /> Workers ({arr.length})</h3>
      <div className="flex flex-col gap-2">
        {arr.map(s => <SubagentRow key={s.subagent_id} sub={s} />)}
      </div>
    </section>
  );
}

function SubagentRow({ sub }: { sub: SubagentAggregate }) {
  const stateColor = { start: "text-info", running: "text-info", complete: "text-success", error: "text-danger" }[sub.state];
  return (
    <div className="rounded-md border border-border bg-raised p-2 hover:bg-active cursor-pointer" onClick={() => {/* PR C/E: open agents tab filtered */}}>
      <div className="flex items-center gap-2">
        <Bird className={cn("w-3 h-3", stateColor)} />
        <span className="font-mono text-xs text-body">{sub.subagent_type}</span>
        <span className={cn("text-xs", stateColor)}>{sub.state}</span>
        {sub.duration_ms && <span className="text-xs text-muted font-mono">{sub.duration_ms}ms</span>}
      </div>
      {sub.objective && <p className="text-xs text-secondary mt-1">{sub.objective}</p>}
      {sub.summary && <p className="text-xs text-muted mt-1">{sub.summary}</p>}
      {sub.parent_id && <p className="text-xs text-muted mt-1">parent: {sub.parent_id}</p>}
    </div>
  );
}
```

## 7. `HumanRequestCard.tsx` — HITL durable (C7 §11, C3 §9)

HITL card NO solo prompt+options. Includes:
- exact proposed action;
- target and scope;
- redacted arguments;
- risk level (low|medium|high|critical) — pill color;
- evidence reference chips;
- requesting agent/tool;
- nonce-backed durable state;
- Approve, Reject, optional operator guidance;
- resolved state after refresh.

```tsx
export function HumanRequestCard({ part }: { part: HumanRequestSchema }) {
  return (
    <AlertDialog open={true}>
      <AlertDialogContent className="bg-raised border border-accent/40 max-w-md">
        <AlertDialogHeader>
          <AlertDialogTitle className="text-body font-mono text-base flex items-center gap-2">
            <Shield className="w-4 h-4 text-accent" /> HITL — {part.action}
            {part.risk && <RiskPill risk={part.risk} />}
          </AlertDialogTitle>
          <AlertDialogDescription className="text-secondary text-sm whitespace-pre-wrap">
            {part.target && <div className="mb-1"><strong>Target:</strong> <code className="font-mono">{part.target}</code></div>}
            {part.scope && <div className="mb-1"><strong>Scope:</strong> {part.scope}</div>}
            {part.prompt && <div>{part.prompt}</div>}
            {part.redacted_args && (
              <details className="mt-2">
                <summary className="cursor-pointer text-xs text-muted hover:text-secondary">Args (redacted)</summary>
                <pre className="mt-2 text-xs font-mono whitespace-pre-wrap break-all overflow-x-auto">{JSON.stringify(part.redacted_args, null, 2)}</pre>
              </details>
            )}
            {part.evidence_ref_ids?.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {part.evidence_ref_ids.map(id => <span key={id} className="text-xs px-2 py-0.5 rounded bg-active font-mono text-secondary">{id}</span>)}
              </div>
            )}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="flex flex-col gap-2 mt-3">
          {part.choices.map(c => <Button key={c} variant="outline" onClick={() => resolve(c)} className="justify-start">{c}</Button>)}
          <Button variant="ghost" className="text-muted text-xs justify-start" onClick={() => resolve("__reject__")}>Reject (no resume)</Button>
        </div>
        {part.expires_at_ms && <CountdownTimer expiresAt={part.expires_at_ms} />}
      </AlertDialogContent>
    </AlertDialog>
  );
}
```

`resolve(choice)` → `POST /api/human-requests/{request_id}/resolve` con `{choice, nonce, optional_reason}`. Backend reanuda `Command(resume={decisions:[choice]})`.

Resolved state persisted: tras refresh, el card muestra " APPROVED: {choice}" inactivo, no reabre el dialog.

## 8. `GuidanceLifecycle.tsx` — banner duradero (C2 §9, C5 §19)

```tsx
const STATE_LABELS = {
  queued: { label: "Queued", color: "text-muted", detail: "Sent to backend, waiting for next model boundary" },
  consumed_by_runtime: { label: "Consumed", color: "text-info", detail: "Pulled from queue, will be in next model step" },
  applied_to_model: { label: "Applied", color: "text-success", detail: "Reached the next model input" },
  run_finished_undelivered: { label: "Undelivered", color: "text-warning", detail: "Run finished before next model boundary" },
  expired: { label: "Expired", color: "text-warning", detail: "Guidance expired before consumption" },
  failed: { label: "Failed", color: "text-danger", detail: "Failure during injection; see logs" },
  superseded: { label: "Superseded", color: "text-muted" },
} as const;

export function GuidanceLifecycle({ items }: { items: Map<string, GuidanceAggregate> }) {
  if (!items.size) return null;
  return (
    <div className="flex flex-col gap-2 my-2">
      {Array.from(items.values()).map(g => {
        const label = STATE_LABELS[g.state];
        return (
          <div key={g.guidance_id} className="rounded-md border border-border bg-active/50 px-3 py-2 flex items-center gap-3">
            <span className={cn("text-xs font-mono", label.color)}>{label.label}</span>
            <span className="text-sm text-secondary flex-1 min-w-0 truncate">{g.body}</span>
            {g.applied_at_step && <span className="text-xs text-muted font-mono">step {g.applied_at_step}</span>}
            <HelpTrigger content={label.detail} />
          </div>
        );
      })}
    </div>
  );
}
```

**CRÍTICO** (C3 §6 Guide, C5 §9):
- "Queued" se muestra tras HTTP 200 del enqueue endpoint — NO "Delivered".
- "Applied" SOLO cuando llega `guidance.applied` event del stream.
- "Undelivered" cuando `run_finished_undelivered`.

## 9. `ExecutionGroup.tsx` — wrapper (C7 §Product shell)

```tsx
export function ExecutionGroup({ turn }: { turn: GroupedTurn }) {
  const hasTools = turn.tools.size;
  const hasCommands = turn.commands.size;
  const hasSubagents = turn.subagents.size;
  if (!hasTools && !hasCommands && !hasSubagents && !turn.activities.length) return null;

  return (
    <section aria-label="execution" className="w-full min-w-0">
      {turn.activities.length > 0 && (
        <div className="my-2">
          <h4 className="text-xs uppercase text-muted font-mono mb-1">Activity</h4>
          {turn.activities.map((a, i) => (
            <div key={i} className="text-xs text-secondary py-0.5">{a.stage}: {a.summary}</div>
          ))}
        </div>
      )}
      {Array.from(turn.tools.values()).map(t => <ToolCard key={t.tool_call_id} agg={t} />)}
      {Array.from(turn.commands.values()).map(c => <CommandTerminal key={c.job_id} agg={c} />)}
      {hasSubagents > 0 && <SubagentGroup subagents={turn.subagents} />}
    </section>
  );
}
```

Tool cards ocupan **full readable execution width** (NO heredan chat bubble `max-w-[80%]`). `min-w-0 w-full max-w-full overflow-hidden` en ancestors (PR C ya protege container).

## 10. Reasoning / Activity separation (C4 §7 Reasoning)

- `provider_reasoning` parts → `Reasoning` collapsible disclosure (ya existe en PR C).
- `data-activity` parts → `ExecutionGroup.activity` list (concise string, NO chain-of-thought).
- `final-answer` text → `FinalAnswer` (PR C, primario).

**PROHIBIDO** mezclar activity con reasoning. `progress_emit.py` ya diferencia; `translator.ts` no debe unificar.

## 11. Tests

### Backend `tests/test_command_transcript.py`
- transcript regresa chunks ordenados por sequence
- format text incluye header con metadata + redaction policy version
- format json retorna array estructurado
- 403 si actor sin acceso al run
- 404 si job_id no encontrado

### Frontend `__tests__/ToolExecutionGroup.test.tsx`
- Renderiza ToolCard con 4 estados (intent/running/completed/failed)
- Icono seleccionado por patrón de tool_name (gen__/nmap_/ldap_/cve_/valravn_/munin_/default)
- Click expande/collapse, copy input/output via clipboard mock
- 5 chunks mismo job_id → 1 CommandTerminal con stdout strings ordered
- aria-busy=true cuando running, aria-label correcto
- 4 events mismo guidance_id (queued→consumed→applied) → 1 GuidanceLifecycle con state="Applied"

### Frontend `__tests__/CommandTerminal.test.tsx`
- stdout/stderr/meta tabs cambian contenido
- Wrap toggle cambia `whitespace-pre` a `whitespace-pre-wrap break-all`
- Fullscreen modal abre con focus trap, Esc cierra, restored focus
- Download transcript link apunta a `/api/runs/{run_id}/commands/{job_id}/transcript?format=text`
- Page no horizontal scroll cuando output tiene URLs/JSON muy largas
- 100 chunks mismo job_id → 1 componente (no 100 cards) — viene de group-parts (PR B) pero PR D valida UI

### Frontend `__tests__/HumanRequestCard.test.tsx`
- Renderiza action, target, scope, redacted_args, risk pill, evidence chips
- Approve click llama `POST /api/human-requests/{req_id}/resolve` con choice y nonce
- Reject click llama con `__reject__`
- Resolved state persiste tras refresh (mock stream replay)
- Countdown timer muestra segundos restantes cuando expires_at_ms presente
- ESC NO cancela el run (sólo cierra dialogs navegable)

### Frontend `__tests__/SubagentGroup.test.tsx`
- Renderiza N subagents como N rows compactos (no un card grande por evento)
- Click en row abre Agents tab filtrado (PR E)
- Parent_id visible
- Color de state correcto (start/running blue, complete green, error red)

## 12. Verificación

```bash
python -m compileall -q munin tests
python -m pytest -q tests/test_command_transcript.py
cd app
npm run lint
npm run typecheck
npm run build
npm test
```

## 13. Commit / PR

- Branch: `feat/issue-18d-execution-ux`
- Commit: `feat(issue-18d): grouped tools, terminal wrap/fullscreen/transcript, subagents, HITL cards, guidance lifecycle`
- PR contra `main`. Requiere PR B + PR C merged.
