# Prompt PR-6 — Read-Model de Backend y Enriquecimiento de Eventos

> Issue: #18 · Fase 6 · Ola 1 · **Requiere solo `main`**
> Ejecutar en paralelo con `pr-1` y `pr-2`.
> Contexto compartido obligatorio: `docs/prompts/issue-18/00-master.md` — léelo primero.

---

## 1. Instrucciones para la IA ejecutora

Eres un desarrollador Backend Python especializado en FastAPI/Starlette y LangGraph. Debes enriquecer el **Read-Model de ejecuciones y eventos** en Munin para dar soporte a la consulta de detalles de runs, metadatos de artefactos y eventos estructurados de subagentes y herramientas.

---

## 2. Rutas que SOLO este PR modifica (Rutas Permitidas)

PUEDES crear o editar ÚNICAMENTE estas rutas:
- `munin/production/store.py` (Nuevos métodos de consulta de runs y artefactos)
- `munin/production/chat.py` (Endpoints de lectura HTTP)
- `munin/production/asgi.py` (Registro de rutas si fuera necesario)
- `munin/core/runtime_adapter.py` (Enriquecimiento de envelopes SSE)
- `tests/test_backend_read_model.py` (NUEVO)
- `docs/issue-18-backend-read-model.md` (NUEVO)
- `changes.md` (AÑADIR entrada)

### Rutas Prohibidas
- `app/**` (Este es un PR 100% Backend)

---

## 3. Especificación detallada paso a paso

### Paso 3.1: Endpoint de Detalle de Run (`GET /api/chat/{conversation_id}/runs/{run_id}`)

Agrega en `munin/production/chat.py`:

```python
async def get_run_detail_endpoint(request: Request) -> Response:
    """Retorna la fotograma/snapshot completa de un run."""
    conversation_id = request.path_params["conversation_id"]
    run_id = request.path_params["run_id"]
    actor = await require_authenticated_actor(request)
    
    run_detail = store.get_run_detail(conversation_id=conversation_id, run_id=run_id)
    if not run_detail:
        return JSONResponse({"error": {"code": "not_found", "message": "Run no encontrado"}}, status_code=404)
        
    return JSONResponse({"status": "success", "data": run_detail})
```

El método `store.get_run_detail` debe retornar:
- Estado del run, timestamps, actor_id, fencing_epoch.
- Conteo y lista de tool calls ejecutadas.
- Lista de artefactos generados.
- Peticiones HITL y su resolución.
- Guidance aplicado.

### Paso 3.2: Endpoint de Lista de Artefactos (`GET /api/chat/{conversation_id}/artifacts`)

Agrega en `munin/production/chat.py`:
Endpoint para listar todos los artefactos persistidos en la tabla `conversation_artifacts` para una conversación dada, incluyendo: `id`, `filename`, `language`, `media_type`, `created_at` y URL de descarga.

### Paso 3.3: Enriquecimiento de Eventos SSE en `runtime_adapter.py`

En `munin/core/runtime_adapter.py`:
Asegúrate de que los eventos emitidos durante el stream incluyan metadatos ricos:
- Eventos de subagente (`subagent_lifecycle`): Incluir `subagent_id`, `subagent_type`, `phase` (`start` | `complete` | `error`), `duration_ms` y `eval_id` del tool call padre.
- Eventos de comando (`command_output`): Incluir `job_id`, `command`, `output`, `is_final`.

### Paso 3.4: Tests Unitarios Backend

Crea `tests/test_backend_read_model.py`:
- `test_get_run_detail_returns_aggregated_data`: Crea conversación + run + tool calls + artefactos -> invoca el endpoint -> comprueba respuesta estructurada.
- `test_list_conversation_artifacts`: Verifica la lista de artefactos.

---

## 4. Verificación Obligatoria

```bash
python -m compileall -q munin tests scripts
python -m pytest -q tests/test_backend_read_model.py
```

---

## 5. Instrucciones de Commit y PR

- Rama: `feat/issue-18-6-backend-read-model`
- Commit: `feat(issue-18-6): add run detail endpoint, artifact listing, and enriched SSE envelopes`
- Abre el PR contra `main`.
