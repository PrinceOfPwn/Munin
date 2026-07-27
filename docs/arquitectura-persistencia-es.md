# Arquitectura y persistencia de Munin

## Componentes

```text
GUI / cliente MCP
        │ JSON-RPC + Bearer
        ▼
FastMCP (`munin/mcp/main.py`)
        ├── tools nativas: LDAP, intel, memoria, coordinación, diagnostics
        ├── MuninAgent: loop ReAct + cliente LLM
        ├── Registry: carga dinámica de gen__*
        └── SharedStateStore
                 │
        ┌────────┴─────────┐
        ▼                  ▼
 SQLite local       Turso/libSQL remoto
 (desarrollo)       (sesiones online)
        │
        ▼
 Orchestrator → runner de subagente → episodios/mensajes/presencia
```

`munin/mcp/main.py` registra la superficie MCP. El agente y los subagentes no
hablan con la GUI directamente: intercambian estado por `SharedStateStore` y
exponen eventos mediante tools.

## Estado compartido

Las tablas relevantes son:

| Tabla | Finalidad |
| --- | --- |
| `semantic` | Hechos persistentes clave/valor. |
| `episodic` | Decisiones, tool calls y eventos ordenados. |
| `procedural` | Registro de tools generadas, incluyendo código fuente durable para nuevas filas. |
| `generated_graphs` | Nombre, prompt, whitelist, política de reset y estado del especialista. |
| `agent_wake_queue` | Trabajo pendiente/reclamado para runners. |
| `agent_presence` | Estado y latido de agentes. |
| `agent_messages` | Mensajes/resultados entre Munin y subagentes. |
| `shared_intel`, `active_tasks` | Intel y coordinación de tareas compartidas. |

La UI no lee la base directamente. Invoca tools MCP y usa el payload `data` de
la respuesta. Esto mantiene la autorización, la auditoría y la semántica de
errores en el servidor.

## Backends de persistencia

### Local

Sin `MUNIN_DB_URL`, el estado vive en
`$MUNIN_DATA_PATH/shared_state.sqlite`. Es apropiado para desarrollo. En
Actions puede copiarse como artifact, pero un artifact no es una base de datos
concurrente ni una garantía de disponibilidad.

### Turso/libSQL

Con `MUNIN_DB_URL=libsql://...` y `MUNIN_DB_AUTH_TOKEN`, `open_connection`
abre una conexión directa al servicio Turso con autocommit. Cada operación de
estado confirma en la base remota; no se requiere una réplica local que luego
deba sincronizarse. Esta elección evita que la vida de un stream de Hrana quede
acoplada al runner efímero.

Turso es por tanto la fuente de verdad para memoria, mensajes, cola, grafos y
registro procedural en la sesión online. El workflow conserva artifacts como
respaldo auxiliar de archivos y logs. La pérdida/cuota de ese artifact no borra
lo que Turso ya confirmó.

## Forjar y rehidratar una tool

```text
spec natural
  → ToolForgeSubagent
  → candidato Python
  → AST/import guard + sandbox de prueba
  → registry.register
       ├── munin/generated/<slug>.py
       └── procedural: metadata + source_code
  → tool MCP gen__<slug> disponible

siguiente arranque
  → procedural_list(include_source=true)
  → si falta el archivo, restaurarlo de source_code de forma atómica
  → importar y registrar gen__<slug>
```

El source code durable se aplica a las tools generadas después de la migración.
Una fila heredada que tiene solo `script_path` puede seguir apareciendo en el
catálogo, pero no es ejecutable si el archivo ya murió junto con el runner. El
registry devuelve un diagnóstico explícito para que se regenere, en vez de
declarar que una tool inexistente se ejecutó.

Cuando `MUNIN_AUTO_COMMIT=1`, el worker de `git_persist` además intenta
versionar el archivo en la rama de sesión. Ese commit sirve para revisión y
proveniencia; Turso no depende de que se complete para rehidratar una fuente
nueva.

## Grafos y subagentes

`graph_forge` no genera código Python arbitrario: genera una configuración de
especialista. Guarda un nombre, propósito, system prompt, whitelist de tools y
política de reset. `munin_wake` verifica que el nombre sea nativo o un grafo
existente, inserta trabajo en la cola y lanza un runner separado.

El runner reclama el ítem, actualiza presencia, ejecuta el loop ReAct con la
whitelist y publica `PROGRESS`/resultado. `subagent_trace` entrega eventos y
mensajes por cursor, lo que permite a la GUI consultar sin duplicar ni perder
la secuencia de un trabajo largo.

`reset_policy="on_reset"` elimina el grafo al hacer `munin reset`;
`"persistent"` permite conservarlo. Desactivar un grafo es un soft delete que
mantiene información suficiente para inspección.

## Soul y revisión humana

Soul se mantiene en Markdown bajo `soul/`, no dentro de Turso. Esta separación
es intencional: los cambios de identidad deben ser diffeables y revisables en
Git. `soul_propose_edit` produce una propuesta; con `MUNIN_AUTO_PR=1` y `gh`
puede abrir un PR sobre la rama base configurada. El contenido no debe ser
tratado como una mutación silenciosa aprobada.

## Observabilidad

- `episodic_query`: historial general.
- `subagent_trace`: feed incremental para un agente concreto.
- `list_agent_presence`: quién está vivo y su tarea.
- `munin_wake_list`: trabajo pendiente/reclamado.
- `fetch_agent_messages`: resultados y coordinación.
- `munin_diagnostics`: contract checks de infraestructura y persistencia.

La arquitectura distingue entre un error de transporte de una llamada y la
pérdida de datos. Ante timeout, consulta estos registros antes de reintentar;
un trabajo puede haber finalizado aunque el cliente haya dejado de esperar.
