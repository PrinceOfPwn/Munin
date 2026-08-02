# Prompt PR-2 — Defectos Backend: Cancelación Durable + Guidance Lifecycle E2E

> Issue: #18 · Fase 1 (parte backend) · Ola 1 · **Requiere solo `main`**
> Ejecutar en paralelo con `pr-1` y `pr-6`.
> Contexto compartido obligatorio: `docs/prompts/issue-18/00-master.md` — léelo primero.

---

## 1. Instrucciones para la IA ejecutora

Debes cerrar dos brechas backend que el frontend de Munin necesita para ser una consola operacional creíble:

1. **Cancelación de run durable**: exponer `store.request_run_cancellation` vía HTTP y conectarla al runtime LangGraph.
2. **Lifecycle de operator guidance**: persistir transiciones de estado del guidance enviado por el operador para que el frontend pueda reconciliar `queued → delivered → applied → expired`.

Sigue la especificación al pie de la letra. **No inventes** funciones: lee el código existente referenciado antes de editar.

---

## 2. Rutas Permitidas

- `munin/production/chat.py` (EDITAR — añadir endpoint)
- `munin/production/store.py` (EDITAR — añadir lifecycle guidance)
- `munin/core/middleware/operator_guidance.py` (EDITAR — emitir eventos)
- `munin/core/runtime_adapter.py` (EDITAR — respetar fencing en resume)
- `tests/test_production_cancel.py` (NUEVO)
- `tests/test_operator_guidance_lifecycle.py` (NUEVO)
- `changes.md` (AÑADIR entrada)

### Rutas Prohibidas
- `app/**`
- `munin/mcp/**`
- `munin/valravn/**`
- `soul/**`

---

## 3. Spec: Cancelación Durable

### 3.1 Endpoint HTTP en `munin/production/chat.py`

Añade el endpoint **exactamente** con esta firma (rerautea a la zona `# ===== Run management =====` o crea una nueva):

```python
async def cancel_run_endpoint(request: Request) -> JSONResponse:
    """
    POST /api/chat/{conversation_id}/runs/{run_id}/cancel

    Cancela un run durable. Establece state='cancelled' + borra lease.
    El runtime_adapter consulta store.is_run_fenced(run_id) para abortar el stream.
    """
    conv_id = request.path_params["conversation_id"]
    run_id = request.path_params["run_id"]

    actor = await _require_auth(request)
    if actor is None:
        return _unauthorized()

    # Authorization: solo operator del conversation_id o admin
    if not await _can_modify_run(actor, conv_id):
        return _forbidden()

    fencing_token = await store.request_run_cancellation(
        actor_id=actor.id,
        run_id=run_id,
        reason="operator_request",
    )

    if fencing_token is None:
        # run ya terminal o no existe
        return JSONResponse(
            {"error": "run_not_cancellable", "run_id": run_id},
            status_code=409,
        )

    return JSONResponse(
        {
            "ok": True,
            "run_id": run_id,
            "state": "cancelled",
            "fencing_token": fencing_token,
        },
        status_code=202,
    )
```

Registra la ruta en el router de la app (`routes.append(Route("/api/chat/{conversation_id}/runs/{run_id}/cancel", cancel_run_endpoint, methods=["POST"]))`).

### 3.2 Fencing en `runtime_adapter.py`

En el loop de `astream_events(version="v2")` (línea ~454), añade un **early exit** antes de producir cada chunk:

```python
async def astream(self, ...):
    run_id = self.run_id
    async for ev in self.graph.astream_events(...):
        # === CHECK FENCING ===
        if await self.store.is_run_fenced(run_id):
            # emite un último chunk run_state=cancelled y break
            yield self._build_state_event(run_id, "cancelled", reason="operator_cancel")
            await self.store.mark_run_cancelled(run_id)
            break
        # ... resto del handler existente
```

Añade en `store.py` (si no existen ya) los helpers:

```python
async def is_run_fenced(self, run_id: str) -> bool: ...
async def mark_run_cancelled(self, run_id: str) -> None: ...
```

(`request_run_cancellation` ya existe en `store.py:1771`; verifica su comportamiento leyendo el código antes de tocar nada.)

---

## 4. Spec: Guidance Lifecycle (6 estados)

### 4.1 Tabla/estado en `store.py`

El guidance se persiste hoy como `audit_events` con `kind="operator_guidance"` (`chat.py:1532`). En lugar de reutilizar `audit_events`, añade una tabla dedicada:

```sql
CREATE TABLE IF NOT EXISTS operator_guidance (
    guidance_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    run_id TEXT,
    target_agent_id TEXT,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',  -- queued|delivered_to_runtime|applied_to_model_step|expired|superseded|run_finished_undelivered
    queued_at_ms INTEGER NOT NULL,
    delivered_at_ms INTEGER,
    applied_at_step INTEGER,
    expired_at_ms INTEGER,
    superseded_by TEXT,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_guidance_conv_status ON operator_guidance(conversation_id, status);
```

Añade métodos en `store.py`:

```python
async def enqueue_guidance(self, *, conversation_id, run_id, body, target_agent_id=None) -> str: ...
async def mark_guidance_delivered(self, guidance_id: str) -> None: ...
async def mark_guidance_applied(self, guidance_id: str, step: int) -> None: ...
async def mark_guidance_expired(self, guidance_id: str) -> None: ...
async def supersede_guidance(self, guidance_id: str, by_guidance_id: str) -> None: ...
async def finalize_undelivered_guidance(self, run_id: str) -> None: ...  # run_finished_undelivered
async def list_guidance(self, conversation_id: str, run_id: str | None = None) -> list[dict]: ...
```

### 4.2 `OperatorGuidanceMiddleware` emite eventos

En `munin/core/middleware/operator_guidance.py`, dentro de `wrap_model_call` (que se ejecuta antes del LLM):
1. Lee el guidance pendiente vía `store.list_guidance(conv_id, run_id)` con `status='queued'`.
2. Si existe:
   - Llama `store.mark_guidance_delivered(guidance_id)` ANTES de inyectar en el prompt.
   - Emite un **evento SSE** `operator_guidance` con `status='delivered_to_runtime'` al canal de la conversación (usa el mismo emisor que ya usa el runtime_adapter para `run_state`).
3. Tras el paso del modelo ejecutado con éxito: `store.mark_guidance_applied(guidance_id, step=N)` y emite evento SSE con `status='applied_to_model_step'`.
4. Si el paso falla (excepción): deja el guidance en `delivered_to_runtime` (no `applied`).
5. Si expira el lease del run (`get_run_state != 'running'` y estado previo era `running`): `store.finalize_undelivered_guidance(run_id)` emite `status='run_finished_undelivered'`.

---

## 5. Tests Requeridos

### `tests/test_production_cancel.py`

- `test_cancel_running_run_returns_202`: crea run, arranca, llama endpoint, asserts `state='cancelled'` y `fencing_token` no vacío.
- `test_cancel_completed_run_returns_409`: run ya terminal → 409.
- `test_cancel_unauthorized_returns_401`: sin bearer.
- `test_cancel_forbidden_actor_returns_403`: actor sin permiso sobre `conversation_id`.

### `tests/test_operator_guidance_lifecycle.py`

- `test_guidance_lifecycle_happy_path`: `queued → delivered_to_runtime → applied_to_model_step`.
- `test_guidance_expired_when_run_dies_undelivered`: run muere tras `queued`, llama `finalize_undelivered_guidance` → `run_finished_undelivered`.
- `test_guidance_superseded`: segundo guidance con mismo target_supersedes al primero → `superseded`.
- `test_guidance_list_filters_by_run`: dos guidances en runs distintos → `list_guidance(run_id=A)` solo devuelve A.

---

## 6. Verificación

```bash
python -m compileall -q munin tests scripts
python -m pytest -q tests/test_production_cancel.py tests/test_operator_guidance_lifecycle.py
```

## 7. Commit / PR

- Branch: `feat/issue-18-2-backend-defects`
- Commit: `feat(issue-18-2): durable run cancellation + operator guidance lifecycle`
- Abre PR contra `main`.
