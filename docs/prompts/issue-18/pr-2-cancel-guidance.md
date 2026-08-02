# Prompt PR-2 — Defectos release-blocking: Cancelación durable y Guidance Lifecycle

> Issue: #18 · Fase 2 · Ola 1 · **Requiere solo `main`**
> Ejecutar en paralelo con `pr-1` y `pr-6`.
> Contexto compartido obligatorio: `docs/prompts/issue-18/00-master.md` — léelo primero.

---

## 1. Instrucciones para la IA ejecutora

Eres un desarrollador Python/Next.js senior que debe corregir **dos fallos de corrección críticos (release-blocking)** en Munin:
1. El botón "Stop" actual solo desconecta el SSE del navegador; el backend sigue corriendo de fondo. Debes exporner una **cancelación durable real** por HTTP y hacer que el executor Python se detenga verazmente.
2. El envío de guidance del operador hoy sólo guarda una fila de audit y no garantiza que el modelo haya recibido el texto en su siguiente paso. Debes implementar un **lifecycle completo de 6 estados** y escribir un **test E2E real** que compruebe que `HumanMessage(name="operator")` ingresó al modelo.

---

## 2. Rutas que SOLO este PR modifica (Rutas Permitidas)

PUEDES crear o editar ÚNICAMENTE estas rutas:
- `munin/production/chat.py` (Exponer endpoint `/cancel`, interceptar en el loop de ejecucion)
- `munin/production/store.py` (Métodos de soporte si hicieran falta; `request_run_cancellation` ya existe en `:1771`)
- `munin/core/middleware/operator_guidance.py` (Tracking de los estados de guidance)
- `munin/core/runtime_adapter.py` (Recepción de evento de cancelación y emisión de `run_state`)
- `app/src/lib/production-api.ts` (Añadir `cancelRun(conversationId, runId)`)
- `app/src/lib/aiChat.ts` (Añadir función `cancelActiveRun(conversationId, runId)`)
- `app/src/components/chat/HitlRequestPart.tsx` y `GuidancePart.tsx` (Botón Cancelar + Badge de Estado de Guidance)
- `tests/test_run_cancellation.py` (NUEVO)
- `tests/test_guidance_lifecycle.py` (NUEVO)
- `changes.md` (AÑADIR entrada)

### Rutas Prohibidas
- `app/src/types/munin-ui.ts`
- `app/src/renderers/**`
- `app/src/fixtures/**`
- `app/src/components/AppShell.tsx`

---

## 3. Especificación detallada paso a paso

### Paso 3.1: Endpoint HTTP de Cancelación Durable

En `munin/production/chat.py`, añade la ruta HTTP:
`POST /api/chat/{conversation_id}/runs/{run_id}/cancel`

```python
async def cancel_run_endpoint(request: Request) -> Response:
    """Cancela duraderamente una ejecución en curso."""
    conversation_id = request.path_params["conversation_id"]
    run_id = request.path_params["run_id"]
    actor = await require_authenticated_actor(request)
    
    # 1. Llamada idempotente a store
    result = store.request_run_cancellation(actor_id=actor["id"], run_id=run_id)
    
    # 2. Retornar JSON con estado cancelado
    return JSONResponse({
        "status": "success",
        "data": {
            "run_id": run_id,
            "state": result["state"],
            "cancel_requested_at_ms": result.get("cancel_requested_at_ms")
        }
    })
```

Registra la ruta en `register_chat_routes` en `chat.py`.

### Paso 3.2: Fencing e Interrupción en el Loop de Ejecución

En `munin/production/chat.py` (en el loop de ejecución de runs `_run_executor_loop` / `_stream_run_events`):

1. En cada iteración del loop, comprueba si `store.is_run_cancelled(run_id)` o si `run["state"] == "cancelled"`.
2. Si se solicitó cancelación:
   - Emite el evento SSE `{"kind": "run_state", "run_id": run_id, "state": "cancelling"}`.
   - Detén la tarea de LangGraph (`graph_task.cancel()`).
   - Emite el evento SSE `{"kind": "run_state", "run_id": run_id, "state": "cancelled"}`.
   - Libera los leases y cierra el generador.

### Paso 3.3: Lifecycle de Operator Guidance (6 Estados)

En `munin/core/middleware/operator_guidance.py` y `munin/production/store.py`:

Los 6 estados obligatorios son:
- `queued`: Guidance creado por el operador, esperando drena.
- `delivered_to_runtime`: `OperatorGuidanceMiddleware` drena el mensaje antes del paso del LLM.
- `applied_to_model_step`: El hook `wrap_model_call` confirma que `HumanMessage(name="operator")` está presente en la lista `messages` enviada al LLM.
- `expired`: Pasó el TTL sin ejecutarse.
- `superseded`: Un nuevo guidance reemplazó al anterior.
- `run_finished_undelivered`: El run terminó (completed/failed/cancelled) sin haber aplicado el guidance.

Actualiza `OperatorGuidanceMiddleware.wrap_model_call` para emitir el evento `applied_to_model_step` cuando verifique la presencia del mensaje.

### Paso 3.4: Tests Unitarios y E2E Obligatorios

Crea `tests/test_run_cancellation.py`:
1. `test_cancel_running_run_is_idempotent`: Crear run -> llamar cancel -> verificar estado `cancelled` -> llamar cancel de nuevo -> verificar que responde 200 sin error.
2. `test_cancelled_run_emits_events`: Verificar que los eventos SSE emiten `cancelling` y `cancelled`.
3. `test_recovery_does_not_revive_cancelled_run`: Simular restart y verificar que el recovery worker omite los runs cancelados.

Crea `tests/test_guidance_lifecycle.py`:
1. `test_guidance_applied_to_model_step_e2e`:
   - Crear run.
   - Enviar guidance vía API.
   - Ejecutar un paso con mock de LLM.
   - Verificar que los mensajes recibidos por el mock contienen `HumanMessage(content=..., name="operator")`.
   - Verificar que el evento de guidance cambió a `applied_to_model_step`.
2. `test_guidance_undelivered_on_run_finish`:
   - Enviar guidance.
   - Cancelar o terminar el run sin llamar al modelo.
   - Verificar que el estado cambia a `run_finished_undelivered`.

### Paso 3.5: Cliente Frontend (`production-api.ts` y `aiChat.ts`)

En `app/src/lib/production-api.ts`:
```typescript
export async function cancelRun(conversationId: string, runId: string): Promise<void> {
  const token = currentCsrfToken();
  const res = await fetch(`/api/chat/${encodeURIComponent(conversationId)}/runs/${encodeURIComponent(runId)}/cancel`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { "X-CSRF-Token": token } : {}),
    },
  });
  if (!res.ok) throw new Error(`Cancel failed (${res.status})`);
}
```

En `app/src/components/chat/HitlRequestPart.tsx`:
Añade dos botones claramente diferenciados:
- **"Stop Viewing (Detach)"**: Llama a `stop()` de AI SDK UI. Solo desengancha el stream local.
- **"Cancel Run (Backend)"**: Llama a `cancelRun(...)`. Muestra spinner hasta que el evento SSE `run_state: cancelled` confirme la detención real.

---

## 4. Verificación Obligatoria

```bash
# 1. Tests Python del Backend
python -m compileall -q munin tests scripts
python -m pytest -q tests/test_run_cancellation.py tests/test_guidance_lifecycle.py

# 2. Verificación Frontend (si aplicaste cambios en ts/tsx)
cd app
npm run lint
npm run typecheck
```

---

## 5. Instrucciones de Commit y PR

- Rama: `feat/issue-18-2-cancel-guidance`
- Commit: `feat(issue-18-2): add durable run cancellation endpoint and guidance lifecycle tracking`
- Abre el PR contra `main`.
