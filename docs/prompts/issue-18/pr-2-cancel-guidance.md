# Prompt PR-2 (Issue PR A) — Operator-control correctness: Detach label, durable Cancel, Guidance lifecycle, E2E

> Issue: #18 · PR breakdown del issue: **PR A — operator-control correctness**
> **Requiere solo `main`.** Paralelizable con PR B (typed UI protocol) y parte de PR E (artifact endpoints).
> **No visual rewrite in this PR.** Solo corrige los defectos de atribución y de verdad operacional.
> Referencia autoritativa: Issue #18 comentarios C1 §Stop/Guidance bugs, C5 §7-9 (cancellation + guidance design), C5 §16 (test matrix).

---

## 1. Objetivo

Cerrar **tres** defectos release-blocking del issue:

1. **Stop no es cancel real** → renombrar visible a "Detach"/"Stop viewing" + añadir Cancel durable server-side.
2. **Guidance aparece en UI sin proof de delivery** → lifecycle completo con 6 estados + E2E test demostrando `HumanMessage(name="operator")` en el próximo model input.

PR A es **puramente backend + mínimas etiquetas frontend**. No redibuja la UI.

## 2. Rutas permitidas

- `munin/production/store.py` (EDIT — `request_run_cancellation`, `mark_run_cancelling`, `complete_run_cancellation`, `is_run_cancellation_requested`, guidance lifecycle methods, forward migrations)
- `munin/production/chat.py` (EDIT — `POST /api/runs/{run_id}/cancel` endpoint; cancel-safe `complete_run`; terminal queued-guidance handling; replay new lifecycle events)
- `munin/production/asgi.py` (EDIT — registrar nuevo endpoint bajo reglas CSRF)
- `munin/production/recovery.py` o equivalente chat-recovery worker (EDIT — skip/finalize cancelled runs, no relaunch)
- `munin/core/middleware/operator_guidance.py` (EDIT — typed `OperatorControlPort`, mark applied step, emit `guidance.applied` event, observable failures)
- `munin/core/middleware/progress_emit.py` (EDIT — pre-tool cancellation check)
- `munin/core/runtime_adapter.py` (EDIT — cooperative cancellation event/check between graph events, before model step, before tool call, during jobs)
- `munin/core/supervisor.py` o equivalente (EDIT — `asyncio.Event` per active run, fencing en `complete_run`)
- `munin/mcp/jobs.py` (EDIT — `JobManager.cancel_for_run(run_id)`)
- `munin/server.py` (EDIT — compose `OperatorControlPort` en lugar de monkey-patching)
- `app/src/lib/aiChat.ts` (EDIT — solo relabel `stop` → exportar como `detachViewer`; añadir helper `cancelRun(runId)`)
- `app/src/components/__tests__/OperatorControls.test.tsx` (NUEVO — Detach nunca muestra "Cancelled"; Cancel espera backend state)
- `tests/test_production_cancel.py` (NUEVO)
- `tests/test_operator_guidance_lifecycle.py` (NUEVO)
- `tests/test_guidance_e2e_model_input.py` (NUEVO — el test blocking del issue)
- `tests/test_cancel_recovery_safety.py` (NUEVO)
- `changes.md` (AÑADIR)

### Rutas prohibidas (visual)
- Cualquier `app/src/components/**` SALVO minimal label edit en el botón Stop/Cancel.
- `app/src/lib/munin-ui/**` (PR B es dueño).
- `app/src/lib/chat/translator.ts` (PR B es dueño de los nuevos kinds).

## 3. Cancelación durable — contrato EXACTO (C5 §8)

### 3.1 Store methods nuevos en `ProductionStore` + `MuninStore` (facades hot/durable):

```python
async def request_run_cancellation(
    self, *, actor_id: str, run_id: str, reason: str,
) -> dict[str, Any]:
    """Idempotente. Autoriza actor. Set cancel_requested_at_ms. Append run.cancel_requested. Audit event. Return current cancellation state."""

async def mark_run_cancelling(
    self, *, run_id: str, lease_token: str | None,
) -> bool: ...

async def complete_run_cancellation(
    self, *, run_id: str, outcome: str, details: dict[str, Any],
) -> bool: ...

async def is_run_cancellation_requested(self, *, run_id: str) -> bool: ...
```

`request_run_cancellation` specification:
- autoriza actor access al run (mismo helper que conversations use);
- idempotente: si ya cancel_requested/cancelling/cancelled, retorna estado actual sin escribir;
- rechaza/retorna terminal state (completed/failed) sin reescribir history;
- setea `agent_runs.cancel_requested_at_ms = now_ms`;
- append `run.cancel_requested` a `run_events`;
- audit_event `kind="run_cancel"`;
- retorna `{"state": "cancel_requested"|"cancelling"|"cancelled"|"completed"|"failed", "cancel_requested_at_ms": ..., "lease_owner": ...}`.

### 3.2 Forward migration `cancelling` state (opción 1 del issue):

El issue asume forward migration añadiendo `cancelling` al CHECK constraint `RUN_STATES`. Usa el patrón `forward/idempotent migration` del repo (DDL checksum locked → NO edit histórica in-place). Hot SQLite + durable Turso. Idempotente (falso positivo detectado por checksum).

### 3.3 HTTP endpoint en `chat.py`:

```python
async def cancel_run_endpoint(request: Request) -> JSONResponse:
    """
    POST /api/runs/{run_id}/cancel
    body: {"reason": "operator_requested", "terminate_jobs": true}
    202 in-progress, 200 terminal/idempotent.
    """
    run_id = request.path_params["run_id"]
    actor = await _require_auth(request)
    if actor is None: return _unauthorized()
    if not await _can_modify_run(actor, run_id): return _forbidden()

    body = await request.json() if await request.body() else {}
    reason = body.get("reason", "operator_requested")
    terminate_jobs = bool(body.get("terminate_jobs", True))

    result = await store.request_run_cancellation(actor_id=actor.id, run_id=run_id, reason=reason)

    if result["state"] in ("cancelling", "cancelled"):
        return JSONResponse({"ok": True, "run_id": run_id, "state": result["state"]}, status_code=200)

    # Adicional: notificar asyncio.Event del run si está en proceso (chat.py mantiene registry)
    await notify_active_run_cancellation(run_id, terminate_jobs=terminate_jobs)

    return JSONResponse({"ok": True, "run_id": run_id, "state": "cancel_requested", "cancel_requested_at_ms": result["cancel_requested_at_ms"]}, status_code=202)
```

Registra en `asgi.py`:
```python
routes.append(Route("/api/runs/{run_id}/cancel", cancel_run_endpoint, methods=["POST"]))
```

### 3.4 Executor integration — Cancel checks en SEIS sitios (C5 §8):

1. **Antes de invocar el graph** (`chat.py:_launch_chat_run`).
2. **Entre graph events** (`runtime_adapter.astream_events` loop):
   ```python
   async for ev in self.graph.astream_events(version="v2"):
       if await self.control_port.is_run_cancellation_requested(run_id=self.run_id):
           # Marca cancelling, asyncio.Event set, break del loop
           await self.store.mark_run_cancelling(run_id=self.run_id, lease_token=self.lease_token)
           yield self._build_state_event("cancelling", reason="operator_cancel")
           break
       # ... existing handler
   ```
3. **Antes de otro model step** (`supervisor_runner` boundary): si cancel_requested → marcar cancelling, abortar.
4. **Antes de tool call** (`progress_emit` middleware `before_tool_call`): si cancel_requested → marcar cancelling, return `ToolCancellation`.
5. **Durante async command jobs** (`jobs.py`): el `JobManager.cancel_for_run(run_id)` (ver 3.5) cancela futuras y terminate PIDs.
6. **Recovery worker** (chat_recovery): si `is_run_cancellation_requested` → SKIP reclaim, finalize como cancelled.

### 3.5 `JobManager.cancel_for_run` en `munin/mcp/jobs.py`:

```python
def cancel_for_run(self, run_id: str) -> list[dict[str, Any]]:
    """Cancel queued/running jobs owned by run_id. Process-safe bajo manager lock."""
    with self._lock:
        cancelled = []
        for job_id, job in list(self.records.items()):
            if job.run_id != run_id: continue
            if job.status in ("queued", "running"):
                self.cancel(job_id)  # terminate process handle
                cancelled.append({"job_id": job_id, "previous_status": job.status})
        return cancelled
```

NO reaches `JOBS.records` desde HTTP handlers. `cancel_for_run` es wrapper autorizado.

### 3.6 `complete_run` fencing (C5 §8 Finalization race):

`store.complete_run` ahora:
- Si `is_run_cancellation_requested(run_id)` o `state in (cancelling, cancelled)` → RECHAZAR `completed` outcome, forzar `cancelled`.
- Use lease token / fencing epoch / state version existente.
- Cancelled executor nunca puede finalizar como completed.

### 3.7 Recovery skip cancelled (C5 §8 Recovery):

```python
async def chat_recovery_worker():
    while True:
        candidates = await store.list_recovery_candidates()
        for run in candidates:
            if await store.is_run_cancellation_requested(run_id=run.id):
                await store.complete_run_cancellation(run_id=run.id, outcome="cancelled_during_recovery", details={...})
                continue
            # ... reclaim normal path
```

## 4. Guidance lifecycle — contrato EXACTO (C5 §9)

### 4.1 Schema additions en `run_guidance_queue`:

```
ALTER TABLE run_guidance_queue ADD COLUMN state TEXT NOT NULL DEFAULT 'queued';
ALTER TABLE run_guidance_queue ADD COLUMN failure_reason TEXT;
ALTER TABLE run_guidance_queue ADD COLUMN applied_at_ms INTEGER;
ALTER TABLE run_guidance_queue ADD COLUMN expired_at_ms INTEGER;
ALTER TABLE run_guidance_queue ADD COLUMN superseded_by_id TEXT;
ALTER TABLE run_guidance_queue ADD COLUMN idempotency_key TEXT UNIQUE;
```

Usa forward-migration idempotente (DDL checksum locked).

Estados **exactos** (NO renombrar):
```
queued -> consumed_by_runtime -> applied_to_model
                              \-> run_finished_undelivered / expired / failed
```

(`superseded` es un estado lógico derivado de `superseded_by_id != null` → no es un семaforo de la columna state, pero se utilizará también como `state` para derivación frontend simplificada. PR B incluirá ambos: para detalle usar `superseded_by_id`; para state rápido usar `"superseded"`).

### 4.2 Store methods (`store.py`):

```python
async def enqueue_guidance(*, actor_id, run_id, body, target_agent_id=None, idempotency_key=None) -> dict:
    """Usa idempotency_key UNIQUE para evitar duplicates. Retorna {id, run_id, state:'queued', created_at_ms}."""

async def consume_pending_guidance(*, run_id) -> list[dict]:
    """Lee rows con state='queued', marca state='consumed_by_runtime', consumed_at=now, retorna list."""

async def mark_guidance_applied(*, guidance_id: str, model_step: int) -> None:
    """state='applied_to_model', applied_at_ms=now."""

async def mark_guidance_undelivered_for_run(*, run_id: str) -> list[str]:
    """Run terminal sin más boundary: todas las 'queued'/'consumed_by_runtime' -> 'run_finished_undelivered'. Retorna ids afectados."""

async def mark_guidance_failed(*, guidance_id: str, reason: str) -> None: ...
async def supersede_guidance(*, guidance_id: str, by_guidance_id: str) -> None: ...
```

### 4.3 Events en `run_events` (durables ordered):

Cada transición debe append:
```
guidance.queued        {guidance_id, run_id, body, idempotency_key}
guidance.consumed      {guidance_id, run_id}
guidance.applied       {guidance_id, run_id, model_step}
guidance.undelivered   {guidance_id, run_id, reason:"run_terminal"}
guidance.failed        {guidance_id, run_id, failure_reason}
```

### 4.4 HTTP response enrich de `POST /api/chat/{run_id}/guidance`:

Cambia response a (issue C5 §9):
```json
{
  "ok": true,
  "data": {
    "id": "guid_...",
    "run_id": "run_...",
    "state": "queued",
    "created_at_ms": 0
  }
}
```
Frontend debe mostrar **Queued**, no Delivered.

### 4.5 `OperatorControlPort` — typed dependency (C5 §9):

Reemplaza monkey-patching de middleware con protocol:

```python
# munin/core/operator_control.py (NUEVO)
from typing import Protocol

class OperatorControlPort(Protocol):
    async def consume_pending_guidance(self, *, run_id: str) -> list[dict]: ...
    async def mark_guidance_applied(self, *, guidance_id: str, model_step: int) -> None: ...
    async def is_run_cancellation_requested(self, *, run_id: str) -> bool: ...
    async def mark_guidance_failed(self, *, guidance_id: str, reason: str) -> None: ...
    async def mark_guidance_undelivered_for_run(self, *, run_id: str) -> list[str]: ...
```

`OperatorGuidanceMiddleware` ahora recibe `control: OperatorControlPort` por composición en lugar de tocar `server.py` methods.

### 4.6 `OperatorGuidanceMiddleware.wrap_model_call`:

```python
async def wrap_model_call(self, request, call_next):
    if await self.control.is_run_cancellation_requested(run_id=request.run_id):
        # NO model call; produce terminal event
        raise RunCancelledByOperator()

    pending = await self.control.consume_pending_guidance(run_id=request.run_id)
    if not pending:
        return await call_next(request)

    # Append HumanMessage(name="operator") con cada pending body
    request.messages.extend([
        HumanMessage(name="operator", content=g["body"]) for g in pending
    ])

    result = await call_next(request)

    # Mark applied con model_step actual
    step = result.step_index if hasattr(result, "step_index") else 0
    for g in pending:
        await self.control.mark_guidance_applied(guidance_id=g["id"], model_step=step)
        # emit guidance.applied event via event bus
        await self.event_bus.emit("guidance.applied", {"guidance_id": g["id"], "run_id": request.run_id, "model_step": step})

    return result

# except: si consume OK pero model call lanza → log warning con guidance_ids, NO silent debug
```

### 4.7 Run termination:

Antes de terminal finalization (en `complete_run` o `_finalize`):
```python
async def finalize_run(run_id):
    undelivered = await store.mark_guidance_undelivered_for_run(run_id=run_id)
    for gid in undelivered:
        await event_bus.emit("guidance.undelivered", {"guidance_id": gid, "run_id": run_id})
```

## 5. Frontend minimal adjustments (`app/src/lib/aiChat.ts`)

```ts
export function useMuninChat(conversationId: string) {
  const chat = useChat({ /* ...existing... */ });
  return {
    // ...existing exports
    detachViewer: chat.stop,              // RENAMED: aplica solo en UI label, NO cambia behavior
    cancelRun: async (runId: string, reason = "operator_requested") => {
      const r = await fetch(`/api/runs/${runId}/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": getCsrfToken() },
        body: JSON.stringify({ reason, terminate_jobs: true }),
      });
      if (!r.ok && r.status !== 202 && r.status !== 200) {
        throw new Error(`cancel failed: ${r.status}`);
      }
      return r.json();
    },
  };
}
```

El componente que usa `stop` ahora recibe `detachViewer`. Renombrar label visible a "Detach" o "Stop viewing" en el composer/header (PR C hace el visual; este PR solo ajusta el export).

NO se muestra "Cancelled" hasta recibir `data-run-state` con `state="cancelled"` del stream.

## 6. Tests obligatorios

### `tests/test_production_cancel.py`
- `test_cancel_running_run_returns_202_with_cancel_requested`
- `test_cancel_idempotent_returns_200_for_already_cancelling`
- `test_cancel_completed_run_returns_200_state_completed_no_rewrite`
- `test_cancel_unauthorized_returns_401`
- `test_cancel_forbidden_actor_returns_403`
- `test_cancel_request_appends_run_cancel_requested_event`
- `test_complete_run_fenced_after_cancel_request_rejects_completed`

### `tests/test_cancel_recovery_safety.py`
- `test_recovery_worker_skips_cancel_requested_does_not_reclaim`
- `test_recovery_worker_finalizes_cancelled_runs_with_outcome_cancelled_during_recovery`
- `test_recovery_worker_does_not_steal_active_lease_post_cancel_request`

### `tests/test_operator_guidance_lifecycle.py`
- `test_guidance_lifecycle_happy_path_queued_consumed_applied`
- `test_guidance_failed_when_model_call_raises_logs_warning_with_ids`
- `test_guidance_undelivered_when_run_terminal_without_next_boundary`
- `test_guidance_idempotency_key_prevents_duplicate_enqueue`
- `test_guidance_superseded_chain`
- `test_guidance_list_filters_by_run`

### `tests/test_guidance_e2e_model_input.py` — **blocking test del issue** (C1 §Definition of done):

```python
async def test_operator_guidance_human_message_reaches_next_model_input():
    """Issue #18 acceptance criteria:
        -utti: guidance queued -> consumed -> HumanMessage(name="operator")
                appears verbatim in next model invocation.
       NO un unit test de _inject solo.
    """
    # 1.Bootstrapea server real con starlette test client + sqlite in-memory
    # 2. Crea conversation + run activa
    # 3. Intercepta el LLM provider con un mock que captura el messages list invocado
    # 4. POST /api/chat/{run_id}/guidance body "STOP if you see exfil attempts"
    # 5. Wait hasta que el siguiente model call ocurra (el runtime drena middleware)
    # 6. Assert messages contiene HumanMessage(name="operator", content="STOP if you see exfil attempts")
    # 7. Assert stream emitió guidance.applied event
    # 8. Assert store row state='applied_to_model'
```

NO usar mocks de middleware en unit aislado. La prueba es E2E via HTTP + LLM provider mock.

### `app/src/components/__tests__/OperatorControls.test.tsx`
- `test_detach_button_label_does_not_show_cancelled`
- `test_cancel_button_calls_fetch_post_api_runs_cancel`
- `test_cancel_button_disabled_until_run_state_cancelled_from_stream`

## 7. Definition of Done (C1 §First functional PR)

1. ✅ guidance E2E test demuestra `HumanMessage(name="operator")` en próximo model input.
2. ✅ guidance delivery state reaches the replay stream (`guidance.applied` event).
3. ✅ current Stop button relabeled Detach (UI label; PR C refina visual).
4. ✅ real Cancel durable + replayable via `POST /api/runs/{run_id}/cancel`.
5. ✅ Cancel endpoint fences executor (no later tool/model step).
6. ✅ Recovery worker skip cancelled runs.

## 8. Verificación

```bash
python -m compileall -q munin tests scripts
cd app && npm run typecheck && npm run build
cd .. && python -m pytest -q tests/test_production_cancel.py tests/test_operator_guidance_lifecycle.py tests/test_guidance_e2e_model_input.py tests/test_cancel_recovery_safety.py
cd app && npm test -- OperatorControls
```

## 9. Commit / PR

- Branch: `feat/issue-18a-operator-control`
- Commit: `feat(issue-18a): detach label + durable run cancel + guidance lifecycle + E2E proof`
- PR contra `main`. Paralelizable con PR B y PR E backend.
- Cuerpo del PR: referencia a issue acceptance criteria checklist (sección "Release-blocking defects included in this epic" + "Definition of done for the first functional PR").
