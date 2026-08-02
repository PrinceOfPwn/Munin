# Issue #18 — Prompt maestro: AI-native frontend workspace

> **Epic de referencia**: https://github.com/PrinceOfPwn/Munin/issues/18
> El issue tiene 7 comentarios que contienen especificaciones MUY MÁS detalladas que el cuerpo inicial. **TODO agente ejecutor DEBE leer el issue completo (body + comments 1-7) antes de empezar.**
> Mockup visual: rama `docs/issue-18-ai-native-workspace-reference` → `docs/mockups/issue-18-ai-native-workspace/index.html` (PR #19).

## 0. Disclaimer y disciplina obligatoria

Este directorio contiene la especificación para construir el AI-native frontend workspace de Munin en **7 PRs** (6 PRs de implementación + 1 de hardening). Los prompts son **una reorganización autoritaria** del contenido de los comentarios del issue para consumo de agentes de IA. Si hay cualquier conflicto entre este documento y el issue #18, **el issue es la verdad**. Lee el issue.

Los comentarios relevantes del issue son:

- **C1**: `Current-code audit and implementation anchors` (rutas reales, bugs confirmados, definition-of-done PR 1).
- **C2**: Imagen mockup.
- **C3**: `HTML/CSS reference implementation and complete interaction contract` (zonas, endpoints exactos, controles, a11y).
- **C4**: `Vercel AI SDK UI + AI Elements adoption plan` (responsabilidad split, `dataPartSchemas`, `messageMetadataSchema`, mapping de componentes).
- **C5**: `Backend inventory, exact reuse plan, missing contracts, and implementation sequence` (14 secciones, PR breakdown A-F bloqueante + test matrix).
- **C6**: `Backend availability matrix and final view scope` (? ?? ?? zonas + vistas).
- **C7**: `Final design architecture: views, rationale, backend grounding, and conditional renderers`.

## 1. Las cuatro zonas del producto

Munin debe ser **una sola aplicación persistente**, no varios dashboards. Layout exigido por el issue:

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Rail │ Operations sidebar │ Conversation + execution │ Context Workspace  │
│ 48px │   240-280px        │        flexible        │  collapsible 480px │
└──────────────────────────────────────────────────────────────────────────┘
```

- **Primary rail** (48px): iconos. Botones: Operations, Run activity, Artifacts, Agents, Settings, Notifications, Help. NO filtra chat — Cambia de "área de producto".
- **Operations sidebar** (240-280px): lista duradera de conversaciones con estado de acción requerida.
- **Conversation + execution** (flexible): objetivo → actividad → tools/agrupados → HITL → artifacts → final answer (jerarquía visual FORTECIDA).
- **Context Workspace** (480px collapsible): tabs contextual* NO permanente — cuando está cerrado, la conversation reclamiza todo el ancho.
- Responsive: <1280px workspace colapsa a drawer/route *NO columna aplastada*.

## 2. Mapeo del stack (`app/package.json` actual)

```json
"@ai-sdk/react": "^4.0.50", "ai": "^7.0.47", "next": "^15.5.21",
"react": "^18.3.1", "react-dom": "^18.3.1", "tailwindcss": "^3.4.6",
"zod": "^3.25.0"
```

- **AI Elements exige React 19 + Tailwind 4** → `npx ai-elements@latest` queda PROHIBIDO en producción.
- **Estrategia B del issue**: copiar/adaptar primitivas seleccionadas a `app/src/components/ai-elements/` y reescribir a React 18 + Tailwind 3.4 usando tokens de `app/tailwind.config.ts`.
- **NO mover runtime a `streamText`** — el backend Python/LangGraph es autoritario.

## 3. Backend: lo que existe, lo que hay que exponer, lo que es nuevo

Esta tabla resume lo CRÍTICO del comentario C5+C6. Toda IA ejecutora debe referirse a ella.

| Categoría | Estado | Endpoints/acciones |
|---|---|---|
| Auth | ✅ existe | `/api/auth/{bootstrap,login,logout,session,recovery/*}` |
| Conversations | ✅ existe | `GET/POST/PATCH/DELETE /api/conversations/{id}` + `/export`, search `q=`, `status=`, `archived=`, `limit=`, `cursor=` (frontend hoy solo usa `q` — EXTENDER) |
| Chat stream + resume | ✅ existe | `POST /api/chat`, `GET /api/chat/{conv_id}/stream` (`resume:true` BFF) |
| Artifacts GET by id | ✅ existe | `GET /api/artifacts/{id}`, `?inline=1`, `?download=1` |
| HITL resolve | ✅ existe | `POST /api/human-requests/{req_id}/resolve` (server-issued nonce) |
| Guidance enqueue | ✅ existe | `POST /api/chat/{run_id}/guidance` — pero retorna 200 sin proof de delivery |
| `JobManager.cancel(job_id)` | ✅ existe | `munin/mcp/jobs.py` — falta `cancel_for_run(run_id)` |
| Run list con filtros | 🔴 NUEVO | `GET /api/runs?state=running,waiting_for_human&limit=50&cursor=...` |
| Run detail read model | 🔴 NUEVO | `GET /api/runs/{run_id}/detail` (run/tools/reasoning/activities/subagents/human_requests/guidance/artifacts/evidence/summary) |
| Run cancel durable | 🔴 NUEVO | `POST /api/runs/{run_id}/cancel` body `{reason, terminate_jobs}` |
| Command transcript | 🔴 NUEVO | `GET /api/runs/{run_id}/commands/{job_id}/transcript?format=text\|json` |
| Artifacts list/read | 🔴 NUEVO | `GET /api/conversations/{id}/artifacts`, `GET /api/runs/{run_id}/artifacts`, `GET /api/artifacts?...` |
| Artifact upload | 🔴 NUEVO | `POST /api/artifacts/uploads` |
| Artifact notes | 🔴 NUEVO | `POST/PATCH/DELETE /api/artifacts/{id}/notes{/{note_id}}` |
| Sandboxed HTML preview | 🔴 NUEVO | `GET /api/artifacts/{id}/sandbox-preview` (CSP estricto, NO inline) |
| Run evidence | 🔴 NUEVO | `GET /api/runs/{run_id}/evidence`, `GET /api/conversations/{id}/evidence` |

## 4. Cancelación durable — contrato EXACTO

Endpoints, store methods, secuencias de estados y tests están especificados en **C5 sección 8**. Resumen NO reemplaza lectura del issue:

```python
# munin/production/store.py — métodos públicos NUEVOS en ProductionStore + MuninStore
request_run_cancellation(*, actor_id, run_id, reason) -> dict[str, Any]
mark_run_cancelling(*, run_id, lease_token: str | None) -> bool
complete_run_cancellation(*, run_id, outcome, details) -> bool
is_run_cancellation_requested(*, run_id) -> bool
```

- `request_run_cancellation`: idempotente, autoriza actor, setea `cancel_requested_at_ms`, append `run.cancel_requested`, audit event, retorna current state.
- Endpoint `POST /api/runs/{run_id}/cancel` body `{"reason":"operator_requested","terminate_jobs":true}` → 202 en proceso, 200 terminal/idempotente.
- Executor integration: `asyncio.Event` per-active-run + durable store flag (cross-restart authority) + checkpoints en (a) antes del graph, (b) entre eventos, (c) antes de otro model step, (d) antes de otra tool call, (e) durante jobs async, (f) durante recovery.
- **Opción 1 del issue asumida**: añadir `cancelling` como run_state real con forward migration (NO substate en running).
- `complete_run` debe RECHAZAR `completed` post-cancel-fenced via lease token/epoch/state version.
- Recovery SKIP/finaliza cancelled runs — nunca relanzar como ordinary work.

## 5. Guidance lifecycle — contrato EXACTO (C5 secciones 9)

Tabla `run_guidance_queue` ya existe. Añadir columnas: `state`, `failure_reason`, `applied_at_ms`, `expired_at_ms`, `superseded_by_id`, `idempotency_key`.

Estados **exactos** del issue (NO cambiar nombre):
```
queued -> consumed_by_runtime -> applied_to_model
                              \-> run_finished_undelivered / expired / failed
```

Eventos durables en `run_events`:
```
guidance.queued
guidance.consumed
guidance.applied
guidance.undelivered
guidance.failed
```

API response de `POST /api/chat/{run_id}/guidance` debe retornar:
```json
{"ok":true,"data":{"id":"guid_...","run_id":"run_...","state":"queued","created_at_ms":0}}
```

**Requisito CRÍTICO**: extraer `OperatorControlPort` typed protocol en `munin/server.py`:
```python
class OperatorControlPort(Protocol):
    def consume_pending_guidance(...) -> list[dict[str, Any]]: ...
    def mark_guidance_applied(...) -> None: ...
    def is_run_cancellation_requested(...) -> bool: ...
```

**E2E test	REQUIRE**: el test debe demostrar `HumanMessage(name="operator")` aparece en el input del siguiente model step. Un unit test de `_inject` solo NO basta.

## 6. Frontend — estructura de archivos EXIGIDA por el issue (C5 §13, C4 §6)

```
app/src/components/
  AppShell.tsx                          # EDIT: replace 2-col with rail|operations|conversation|workspace
  ConversationSidebar.tsx               # EDIT: richer row, action-required badge, archive section
  AgentConsole.tsx                      # EDIT: split monolith → compose typed components
  ai-elements/                          # NUEVO: AI Elements adaptados (React 18/Tailwind 3)
    conversation.tsx, message.tsx, prompt-input.tsx, reasoning.tsx, tool.tsx,
    sources.tsx, queue.tsx
  munin-ai/                             # NUEVO: Munin-specific execution/operator components
    MuninConversation.tsx, MuninTurn.tsx, ExecutionGroup.tsx,
    ToolExecutionGroup.tsx, CommandTerminal.tsx,
    SubagentGroup.tsx, HumanRequestCard.tsx,
    GuidanceLifecycle.tsx, ArtifactReference.tsx, EvidenceReference.tsx,
    FinalAnswer.tsx

app/src/lib/
  aiChat.ts                             # EDIT: detach/reconnect helper, guidance returns state, cancel helper
  chat/translator.ts                    # EDIT: add guidance lifecycle + cancel states + evidence/source/workers
  production-api.ts                     # NUEVO: typed methods para run detail/cancel/artifact list/evidence/notes
  munin-ui/                             # NUEVO: schemas + registry + agrupación
    schemas.ts, group-parts.ts, renderer-registry.tsx, renderers.tsx
```

**IMPORTANTE**: nuestro PR-1 propone `app/src/renderers/registry.ts` y `app/src/types/munin-ui.ts` — RENOMBRAR a `app/src/lib/munin-ui/` para alinearse con el issue.

## 7. AI SDK UI — `useChat` integration exacta (C4 §4)

```ts
const muninDataPartSchemas = {
  activity: activitySchema,
  runState: runStateSchema,
  commandOutput: commandOutputSchema,
  toolHeartbeat: toolHeartbeatSchema,
  subagent: subagentSchema,
  humanRequest: humanRequestSchema,
  artifact: artifactSchema,
  guidance: guidanceSchema,
  evidence: evidenceSchema,
  workerGroup: workerGroupSchema,
  workflowNode: workflowNodeSchema,
};

useChat({
  id: conversationId,
  transport: new DefaultChatTransport({ api: "/api/chat" }),
  resume: true,
  dataPartSchemas: muninDataPartSchemas,         // ← nuevo, validación en browser boundary
  messageMetadataSchema: metadataSchema,         // conv/run/seq/persisted/created_at/schema_version
  onData: (part) => { /* side-effects only: workspace select, HITL toast */ },
  onFinish: (msg) => { /* renderer snapshot cache, no run-completion inference */ },
  onError: (err) => { /* classify: viewer disconnect vs backend reject vs protocol vs terminal */ },
});
```

- NO mantener store paralelo de eventos en `onData` — `messages[].parts` + backend durables son autoritativos.
- `stop()` = **Detach** (renombrar en UI). NO usarlo para cancel durable.
- `Cancel` = nuevo helper → `POST /api/runs/{run_id}/cancel`.
- Composer dual: botón primario cambia entre **Send** / **Guide active run** según `run_state`. Nunca convertir silent un Send a guidance.

## 8. Jerarquía visual FORTECIDA (C7 §Product shell / C4 §6)

A un turn del asistente debe agruparse:

1. plan o current status (conciso, sin chain-of-thought)
2. live operational activity (lista agrupada por stable ID, NO un card por activity event)
3. tools/commands/subagentes agrupados por `{tool_call_id, job_id, subagent_id}`
4. HITL (action + scope + redacted args + risk + evidence + choices + nonce) NO solo prompt
5. artifacts/evidence producidos
6. **final answer — la sección más prominente visualmente**

Reglas:
- Tool cards NO heredan `max-w-[80%]` del chat bubble — ocupan full readable execution width.
- Long URLs/JSON/code → containment (min-w-0, w-full, max-w-full) en ancestors.
- Wrap toggle on/off. Exacto terminal mode → horizontal scroll INTERNO, no page-level.
- Fullscreen terminal con sticky header, stream tabs, search, copy, download, focus trap.

## 9. Renderer registry — contrato EXACTO (C4 §8, C5 §10)

```ts
const rendererRegistry = {
  "ioc-table@1": IocTableRenderer,
  "asset-exposure@1": AssetExposureRenderer,
  "cve-assessment@1": CveAssessmentRenderer,
  "investigation-timeline@1": InvestigationTimelineRenderer,
  "relationship-graph@1": RelationshipGraphRenderer,
  "markdown-report@1": MarkdownReportRenderer,
  "json-tree@1": JsonTreeRenderer,
  "csv-table@1": CsvTableRenderer,
  "code-file@1": CodeFileRenderer,
  "code-diff@1": CodeDiffRenderer,
  "mermaid@1": MermaidRenderer,
  "image@1": ImageRenderer,
  "command-transcript@1": CommandTranscriptRenderer,
  "sandboxed-html@1": SandboxedHtmlRenderer,
} as const;
```

Backend envía:
```json
{"artifactId":"art_...","renderer":"ioc-table@1","schemaVersion":1,
 "title":"...","data":{"columns":[],"rows":[]},"provenance":{}}
```

Frontend: (1) validate envelope, (2) check allow-list, (3) validate `data` schema, (4) render trusted component, (5) fallback a diagnostic/raw card en unknown version.

**Prohibido**: `eval`, `Function()`, inyectar JSX/JS del model, parsear links de markdown como artifacts.

### Sandboxed HTML — contrato EXACTO (NO srcDoc)

```html
<iframe
  sandbox=""
  referrerpolicy="no-referrer"
  src="/api/artifacts/{id}/sandbox-preview"
/>
```

- `src` (NO `srcDoc`) — un endpoint backend dedicado.
- Sin `allow-same-origin`, `allow-forms`, `allow-popups`, `allow-scripts` por default.
- CSP en el response: deny scripts, network, forms, top navigation, popups, parent access, external fonts/images.
- NO sirve el route `?inline=true` para HTML — solo sandbox-preview.

## 10. Artifact metadata enrich (C5 §10)

Artifacts stream parts hoy portan `{artifact_id, mime_type, uri}`. Deben enriquecerse con:
```
title, renderer_key, schema_version, metadata_json, provenance_json,
parent_artifact_id, version, preview_policy, content_encoding
```
Usar forward-migration (DDL checksum locked — NO editar histórico in-place).

Provenance: `artifact_id, version, conversation, run, message, tool_call, agent/subagent, source_artifact/evidence_ids, created_at, content_hash, media_type, language, renderer_key, schema_version, redaction_policy_version`.

### Evidence derived model (C5 §11, C7 §8)

Tabla nueva cuando haga falta:
```
id, conversation_id, run_id, message_id, tool_call_id, subagent_id, kind,
title, summary, payload_json, source_url, artifact_id, content_hash,
confidence, redaction_policy_version, created_at_ms
```
Empezar con read-model derivada de artifacts + tool_calls + summaries. Solo añadir tabla si lifecycle (promote/annotate/verify/chain-of-custody) lo exige.

EndPoints: `GET /api/runs/{run_id}/evidence`, `GET /api/conversations/{id}/evidence`.

### Notes endpoints
`POST /api/artifacts/{id}/notes`, `PATCH /api/artifacts/{id}/notes/{note_id}`, `DELETE /api/artifacts/{id}/notes/{note_id}`.

## 11. Sources part (C4 §7 моментe "Sources")

```ts
type MuninSource = {
  id: string;
  title: string;
  url?: string;
  provider?: string;
  retrievedAt: number;
  confidence?: number;
  evidenceId?: string;
};
```
Backend debe emitir sources estructuradas cuando las tools los citen (NO solo embed en markdown).

## 12. Plan & Progress view (C7 §3)

Eventos ya traducidos hoy: `plan, todo, replan, hypothesis, goal, timer_tick`.
La UI debe agruparlos por **stable item ID** (NO append cada mutation como card nuevo). Reconcile in-place.

## 13. Atajos de teclado (C3 §6, C7 §Product shell)

- `Cmd/Ctrl + K`: focus composer prompt
- `Cmd/Ctrl + Enter`: submit primary action
- `Shift + Enter`: newline
- `@`: agent targeting menu
- `/`: command/action palette
- `Escape`: cerrar modal/fullscreen — **nunca** cancelar run silent
- `Cmd/Ctrl + B`: toggle sidebar (nosotros)
- `Cmd/Ctrl + J`: toggle workspace (nosotros)
- `Cmd/Ctrl + /`: keyboard help dialog (nosotros)

## 14. Responsive & a11y EXACTOS

Fixtures mínimas: 1366×768, 1440×900, 1920×1080, tablet (768×1024 / 1024×768), mobile (375×667 / 414×896).
- Bajo 1024px: workspace → drawer/route, NO col aplastada.
- Bajo 768px: operations → drawer inferior, single column.
- No page-level horizontal overflow en NINGÚN fixture.
- a11y: focus-visible rings, focus trap en dialogs, status NO solo color, aria-live announcer, `prefers-reduced-motion`.

## 15. E2E 10 escenarios OBLIGATORIOS (C5 §16, C3 §10 agrega tests)

1. Start long run, detach, refresh, reconnect, final answer consistent.
2. Start long command, live output, copy/wrap/fullscreen, no overflow.
3. Send guidance during command → queued → next model input contains `HumanMessage` → `guidance.applied` event.
4. Send guidance after final model boundary → `run_finished_undelivered`.
5. Cancel during model work → no later tool starts.
6. Cancel during command → process cancellation + durable `cancelled` state.
7. Approve/reject HITL → resume checkpoint → resolved card survives reload.
8. Create artifact → open workspace → refresh → same artifact listed & rendered.
9. Render malicious HTML fixture → sandbox cannot access parent/auth/network.
10. Replay historical events from pre-schemas → safe fallback, no crash.

Backend tests:
- cancel idempotente autorizado;
- cancelled executor cannot finalize completed;
- jobs activos reciben cancel;
- recovery no relaunch cancel-requested;
- guidance consumido exactamente una vez;
- `HumanMessage(name="operator")` en próximo model input;
- queued → undelivered en run terminal;
- replay reproduce states;
- artifact list/detail authorizado;
- sandbox-preview headers/CSP; redacción preservada.

Frontend tests unit:
- Zod accept valid / reject malformed;
- grouping determinista por ID;
- unknown renderer/version fallback;
- command chunks order preserved;
- guidance lifecycle reconciliation;
- run state reconciliation.

## 16. PR breakdown del issue (C5 §15 — autoritativo)

| Issue PR | Nuestro prompt | Notas |
|---|---|---|
| **PR A — operator-control correctness** | `pr-2-cancel-guidance.md` + parte de PR-3 | Detach label + Cancel durable + Guidance lifecycle + E2E. **No visual rewrite aquí.** |
| **PR B — typed UI protocol** | `pr-1-contract.md` | Zod, data parts, translator, grouping, compatibilidad replay viejo. Renombrar rutas a `lib/munin-ui/`. |
| **PR C — AI Elements foundation + shell** | `pr-3-shell-conversation.md` | 4 zonas, Conversation/Message/PromptInput adaptados, sidebar/header/composer. Refactor `AgentConsole`. |
| **PR D — execution UX** | `pr-4-execution-ux.md` | Grouped tools, terminal wrap/fullscreen/download, subagents/workers, reasoning/activity split, HITL cards. |
| **PR E — workspace + generative renderers** | `pr-5-artifacts-renderers.md` + parte PR-6 | Artifact/run/evidence/agents tabs, 14 renderers `@1`, sandboxed HTML via endpoint NO srcDoc. |
| **PR F — a11y/performance/polish** | `pr-7-hardening.md` | Virtualization, keyboard nav, visual regression, responsive fixtures, stress. |

**PR de backend de read-model (endpoints GET)**: NO es un PR separado en el issue — se distribuye entre PR A (cancel endpoint) y PR E (artifacts/run detail/evidence endpoints). Nuestro `pr-6-backend-read-model.md` está mal: debe MERGEarse con `pr-2` (cancel + cancel-safe finalization + terminal guidance + run-cancel endpoint) y `pr-5` (artifact list/detail/sandbox-preview/evidence endpoints) y `pr-4` (command transcript endpoint).

## 17. Reglas finales (sección 1.4 de nuestro master anterior sigue vigiendo)

- Paleta `app/tailwind.config.ts` es inviolable: `bg-bg #0a0a0f`, `accent #7c3aed` único acento violeta, semánticos `success/warning/danger/info`. NO hex hardcodeado.
- Cero scroll horizontal página (min-w-0 en todo ancestor).
- Backend Python authority. Replay no regenera histórico. `soul/` opcional.
- CI es autoritativo: `poetry run pytest` + `cd app && npm run build` y opcionalmente `npm test`.

---

## Resumen de cambios en los prompts que siguen

- **`pr-1` PR B**: reescribir con estructura de archivos `lib/munin-ui/`, 16 schemas Zod, **dataPartSchemas** en `useChat`, **messageMetadataSchema**, **compatibilidad replay viejo events**, **fixture gallery + visual regression fixtures**.
- **`pr-2` PR A**: renombrar Stop→Detach (UI), Cancel durable con `operator_control_port` typed, `JobManager.cancel_for_run`, recovery skip, fencing `complete_run`, E2E test con `HumanMessage(name="operator")`.
- **`pr-3` PR C**: shell 4 zonas (rail+operations+conversation+workspace), `AppShell.tsx` edit, `ConversationSidebar` richer rows, `AgentConsole` split, composer dual Send/Guide, atajos `@` y `/`.
- **`pr-4` PR D**: grouped tools por stable ID, terminal tabs stdout/stderr/meta, **wrap/fullscreen/copy/download transcript**, subagent/worker views, **reasoning/activity split explícito**, HITL card con redacted args + risk + evidence, `GET /api/runs/{run}/commands/{job_id}/transcript`.
- **`pr-5` PR E**: workspace tabs, 14 renderers con `@1` versioning, **sandboxed HTML via `/api/artifacts/{id}/sandbox-preview` endpoint** (no srcDoc), `GET /api/conversations/{id}/artifacts`, `GET /api/runs/{run_id}/artifacts`, `POST /api/artifacts/uploads`, notes endpoints, **evidence endpoints**, `GET /api/runs/{run_id}/detail`, **sources part**, pin artifact, `Open in new view` route `/artifacts/{id}`.
- **`pr-6` (eliminado como standalone)** → mergear en PR A + PR D + PR E. La rama ya no crea PR individual; su contenido se reparte.
- **`pr-7` PR F**: a11y AAA, virtualization `useVirtualizer + measureElement`, **visual regression fixtures 1366×768+1440×900+1920×1080+tablet+mobile**, 10 E2E escenarios Playwright, stress 5000 chunks, atajos `Cmd+K/B/J//+Escape`, keyboard help dialog.
