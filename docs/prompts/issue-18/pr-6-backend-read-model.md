# Prompt PR-6 — Backend Read-Model: Run Details + Event Enrichment + Artifacts API

> Issue: #18 · Fase 1 (parte backend) · Ola 1 · **Requiere solo `main`**
> Ejecutar en paralelo con `pr-1` y `pr-2`.
> Contexto compartido obligatorio: `docs/prompts/issue-18/00-master.md` — léelo primero.

---

## 1. Instrucciones para la IA ejecutora

Tu objetivo es exponer endpoints backend que el frontend necesita para lazily fetch el detalle de runs, artifacts y subagentes. El stream SSE ya emite los eventos en vivo; estos endpoints son para **cargar el histórico** al hacer refresh o reanudar una conversación.

Lee `munin/production/store.py` antes de escribir nada. Los métodos de store que necesitas ya existen en su mayoría — tu trabajo es encajarlos en endpoints Starlette con auth.

---

## 2. Rutas Permitidas

- `munin/production/chat.py` (EDITAR — añadir 3 endpoints GET)
- `munin/production/store.py` (EDITAR — solo si hay helpers faltantes; minimal touch)
- `tests/test_production_readmodel.py` (NUEVO)
- `changes.md` (AÑADIR entrada)

### Rutas Prohibidas
- `app/**`
- `munin/core/**`
- `munin/mcp/**`
- `munin/valravn/**`

---

## 3. Spec: Endpoints

### 3.1 `GET /api/chat/{conversation_id}/runs/{run_id}`

Devuelve el detalle de un run durable (state, leases, guidance count, token usage si está disponible, eventos terminales):

```json
{
  "run_id": "string",
  "conversation_id": "string",
  "state": "queued|running|waiting_for_human|cancelling|cancelled|completed|failed|interrupted",
  "created_at_ms": 1700000000000,
  "updated_at_ms": 1700000000000,
  "actor_id": "string",
  "lease_owner": "string|null",
  "lease_expires_at_ms": 1700000000000,
  "guidance_count": 3,
  "tool_call_count": 14,
  "token_usage": {
    "prompt_tokens": 12345,
    "completion_tokens": 6789,
    "total_tokens": 19134
  } | null,
  "terminal_error": "string|null"
}
```

Implementación:
1. `_require_auth(request)` → 401 si no authenticated.
2. Verifica que `actor` tiene acceso a `conversation_id` (reusa helper `_can_view_conversation` o equivalente en chat.py).
3. `store.get_run(run_id)` → si None → 404.
4. Enriquece con `count(guidance)`, `count(tool_calls)` para esa run_id.
5. Devuelve JSONResponse 200 con el payload.

### 3.2 `GET /api/chat/{conversation_id}/artifacts`

Lista todos los artifacts de la conversation_id (con paginación simple):

```json
{
  "items": [
    {
      "artifact_id": "string",
      "filename": "string",
      "media_type": "string|null",
      "language": "string|null",
      "renderer_key": "markdown|ioc-table|cve-assessment|...",
      "size_bytes": 12345,
      "created_at_ms": 1700000000000,
      "produced_by_tool": "tool_name|null"
    }
  ],
  "next_cursor": "string|null"
}
```

Implementación:
1. Authn + authz (mismo actor conversation access).
2. Query param `?cursor=N&limit=50` (default 50, max 100).
3. Lee de `conversation_artifacts` table (ver store.py para método existente).
4. Computa `renderer_key` desde artifact metadata o desde `media_type` heurística (si JSON → `json`, si text/csv → `csv-table`, si text/html → `sandboxed-html`, default `markdown`).
5. `produced_by_tool` optional desde la referencia cruzada con `tool_calls` table.

### 3.3 `GET /api/chat/{conversation_id}/artifacts/{artifact_id}/raw`

Devuelve el contenido raw del artifact con Content-Type apropiado:
- `application/json` para JSON
- `text/plain` para texto/markdown
- `text/html` para HTML artifacts (que se renderizarán dentro del sandbox iframe)
- `image/png` / `image/jpeg` para screenshots
- Default: `application/octet-stream`

Implementación:
1. Authn + authz.
2. `store.get_artifact(conversation_id, artifact_id)` → 404 si None.
3. Set headers `Content-Type` y `Content-Disposition: inline; filename=\"{filename}\"`.
4. Stream response (StreamingResponse) si el artifact es grande (>100KB).

---

## 4. SSE Event Enrichment

El stream `GET /api/chat/{conversation_id}/stream` ya emite envelopes en formato `BackendEnvelopeKind` (27 tipos, ver `app/src/lib/chat/translator.ts`). PR-6 NO debe añadir nuevos kinds. Solo asegurar que:

1. Cada evento SSE incluye el campo `event_id` (auto-increment) persistido en `run_events` table → permite `Last-Event-ID` header para resume desde el últimoevento visto en una reconexión.
2. Cada evento incluye `envelope_version: 1` (forward compatibility).
3. Cuando un evento `run_state` cambia a `waiting_for_human`, el siguiente chunk debe incluir también el objeto `human_request` completo (no solo el ID).

Lee `chat.py` para encontrar el emisor SSE (probablemente un async generator `event_stream_generator`). Inserta la lógica de `event_id` y `Last-Event-ID` parsing en el handler del stream endpoint.

```python
async def conversation_stream_endpoint(request: Request):
    # ... auth + authz ...
    last_event_id = request.headers.get("Last-Event-ID")
    cursor = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0

    async def gen():
        async for ev in store.stream_events(conversation_id, after_id=cursor):
            payload = ev.to_envelope()
            yield f"id: {ev.event_id}\nevent: {payload['kind']}\ndata: {json.dumps(payload)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
```

---

## 5. Tests `tests/test_production_readmodel.py`

- `test_run_details_endpoint_happy_path`: crea run, completa, GET endpoint → 200 con JSON schema completo.
- `test_run_details_404_unknown_run`: GET run inexistente → 404.
- `test_run_details_401_no_auth`: sin bearer → 401.
- `test_run_details_403_wrong_actor`: actor sin acceso a la conversation → 403.
- `test_artifacts_list_pagination`: 120 artifacts en DB, GET con `?limit=50` → 50 items + `next_cursor`. GET con cursor → 50 más, luego 20 finales.
- `test_artifact_raw_content_type_json`: artifact media_type=application/json → response Content-Type: application/json, body es el JSON.
- `test_artifact_raw_content_type_html`: artifact media_type=text/html → Content-Type: text/html, body es el HTML (prueba sin sanitización aquí — el frontend lo sanitiza en el iframe sandbox).
- `test_stream_event_id_persisted`: stream → primer evento tiene `id: 1`, segundo `id: 2`.
- `test_stream_resume_with_last_event_id`: stream → primer consumer lee hasta event_id=5; segundo consumer con `Last-Event-ID: 5` solo recibe events > 5.

---

## 6. Verificación

```bash
python -m compileall -q munin tests scripts
python -m pytest -q tests/test_production_readmodel.py
```

## 7. Commit / PR

- Branch: `feat/issue-18-6-backend-readmodel`
- Commit: `feat(issue-18-6): run details + artifacts API + SSE event_id resume`
- PR contra `main`. Paralelizable con PR-1 y PR-2.
