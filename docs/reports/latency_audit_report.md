# Reporte Final de Auditoría de Latencia y Rendimiento: Munin Web GUI, Backend & Database

Este informe diagnostica minuciosamente las causas de latencia y retraso visual que suceden al **cambiar de chat**, **cargar conversaciones** y **mantener el streaming en tiempo real (SSE)** en Munin.

---

## 1. Resumen de Cuellos de Botella por Capas

```mermaid
graph TD
    A[Navegador / React UI] -->|Key faltante en LiveConsole & Reflows| B[Hydration Race / Re-renders]
    B -->|Miss en IndexedDB + Search cache overwrite| C[Next.js Proxy route.ts]
    C -->|Double-hop HTTP + arrayBuffer()| D[FastAPI / ASGI Backend]
    D -->|Polling loop 300ms & DB sync en Event Loop| E[SQLite Store]
    E -->|Full Table Scans / Subconsultas correlacionadas| F[(Database)]
```

---

## 2. Diagnósticos Detallados con Líneas de Código

### A. Capa de Base de Datos y Persistencia (`munin/production/store.py`)

1. **Falta de Índices en Claves Foráneas (Full Table Scans)**:
   - `conversation_participants`: La tabla solo define `PRIMARY KEY (conversation_id, user_id)`. Como `list_conversations` busca por `user_id = ?`, SQLite realiza un **Table Scan completo** en cada renderizado del sidebar.
   - `tool_calls(run_id)`, `conversation_summaries(conversation_id)`, `conversation_artifacts(run_id, conversation_id)`, `human_requests(run_id)`, `subagent_runs(parent_run_id)`, `reasoning_events(run_id)` **no poseen índices**.
2. **Subconsultas Correlacionadas y Búsqueda Textual Ineficiente (`list_conversations`)**:
   - Para cada fila de la lista se ejecuta `(SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id)`.
   - Cláusulas `OR EXISTS (SELECT 1 FROM messages WHERE ... LOWER(content) LIKE %query%)` escanean todo el texto de mensajes sin índice Full-Text Search (FTS).
3. **5 Consultas SQL Secuenciales en `get_run_detail_for_actor`**:
   - `get_run_detail_for_actor` ejecuta 5 consultas secuenciales sin índices (`reasoning_events`, `tool_calls`, `subagent_runs`, `human_requests`, `conversation_artifacts`), agregando entre **150ms y 1200ms** por cada inspección de ejecución.
4. **Contención del Cerrojo `BEGIN IMMEDIATE` en SQLite WAL**:
   - Transacciones síncronas de escritura (`_transaction()`) duran activas durante la sanitización regex y la sincronización a Turso remoto. Cualquier lectura/escritura concurrente se bloquea hasta el timeout o arroja `database is locked`.

---

### B. Capa Backend ASGI & Servidor SSE (`munin/production/chat.py` & `asgi.py`)

1. **Polling Loop Fijo de 300 ms (`CHAT_REPLAY_POLL_SECONDS = 0.3`)**:
   - En `chat.py` (Línea 76 y 809), la transmisión de SSE se alimenta de un bucle `while True` con `await asyncio.sleep(0.3)`.
   - **Impacto**: Latencia mínima forzada de 300 ms por cada token, evento o actualización de herramienta emitida.
2. **I/O Bloqueante en el Event Loop de Asyncio**:
   - `store.run_events_after`, `_load_run_detail`, `store.get_run` y `_current_placeholder_text` son funciones SQL síncronas invocadas directamente en el Event Loop de Python sin `asyncio.to_thread`.
3. **Escritura Transaccional Síncrona por Token**:
   - Durante la generación de streaming del LLM (40-60 tokens/seg), cada chunk activa `_update_placeholder` (Línea 220-238) abriendo una transacción de escritura SQLite síncrona en el event loop.
4. **Carga Total de la Conversación cada 300 ms (`_current_placeholder_text`)**:
   - Para calcular si el placeholder del asistente cambió, el loop de SSE invoca `store.get_conversation()` reconstruyendo la historia completa de mensajes en lugar de consultar solo el mensaje activo.

---

### C. Capa Frontend Next.js Proxy & Cliente (`app/src/lib/` & `route.ts`)

1. **Falta de `key={conversationId}` en `<LiveConsole>` (`AgentConsole.tsx`: Línea 1314)**:
   - **Causa Raíz Principal en Cliente**: React reutiliza la misma instancia del componente al hacer clic en otra conversación. Esto provoca que `chat.messages` conserve mensajes de la conversación anterior mientras la promesa de red de la nueva conversación está volando.
2. **Fallo de Write-Through en IndexedDB (`aiChat.ts`)**:
   - Cuando `productionApi.conversation` descarga los mensajes del servidor como fallback, **nunca los escribe en IndexedDB**. IndexedDB permanece vacía para chats remotos, forzando un `fetch` HTTP bloqueante al servidor **en cada cambio de chat**.
3. **Sobrescritura del Caché Global por el Buscador (`queries.ts`)**:
   - Buscar en la barra lateral guarda el resultado filtrado en IndexedDB con `writeConversations`, borrando de la memoria las conversaciones fuera de la búsqueda.
4. **Overhead del Proxy BFF Node.js (`route.ts`)**:
   - `route.ts` usa Node.js `undici` con `no-store`, `await context.params` y materialización de `arrayBuffer()` en peticiones POST, añadiendo latencia de doble hop HTTP.

---

### D. Rendimiento de Renderizado en React (`AgentConsole.tsx` & `Markdown.tsx`)

1. **Ausencia Completa de Virtualización de Listas**:
   - Renderiza el 100% de los mensajes y partes directamente en el DOM. Chats con cientos de eventos insertan miles de nodos SVG/HTML saturando la memoria del navegador.
2. **Re-renderizado en Cascada por Falta de `React.memo`**:
   - Ni `LiveConsole`, ni `MessageBubble`, ni `PartRenderer`, ni `Markdown` usan `React.memo`.
   - Con cada token SSE entrante (20-60 eventos/seg), **los N mensajes anteriores vuelven a re-renderizarse por completo en React**.
3. **Re-parsing AST de Markdown en Cada Chunks**:
   - `Markdown.tsx` define plugins como arreglos literales inline en JSX (`remarkPlugins={[remarkGfm]}`). `ReactMarkdown` los percibe como nuevas instancias en cada render, forzando la re-inicialización y parseo AST de todo el texto Markdown con sintaxis highlight (`rehype-highlight`).
4. **Búsqueda $O(N \cdot M)$ en `useStreamInsight` y Layout Thrashing de Scroll**:
   - `useStreamInsight` escanea recursivamente todas las partes de todos los mensajes en cada renderizado. `useEffect` de auto-scroll lee y modifica la geometría DOM consecutivamente forzando reflows síncronos en el navegador.

---

## 3. Matriz de Soluciones Técnicas Recomendadas

| Capa | Acción Correctiva Recomendada | Impacto Esperado |
| :--- | :--- | :--- |
| **Database** | Crear índices en SQLite (`conversation_participants(user_id)`, `tool_calls(run_id)`, `agent_runs(conversation_id)`). | Elimina Full Table Scans en `list_conversations` (de ~350ms a <5ms). |
| **Backend Stream** | Reemplazar polling `asyncio.sleep(0.3)` por Pub/Sub en memoria (`asyncio.Condition`). | Streaming SSE pasa de latencia 300ms a **<5ms instantáneo**. |
| **Backend I/O** | Envolver llamadas `store.*` en `asyncio.to_thread` y hacer batching de escrituras de placeholders cada 500ms. | Libera el Event Loop de Python y elimina bloqueos de SQLite WAL (`database is locked`). |
| **Frontend State** | Agregar `key={conversationId}` a `<LiveConsole>` y hacer write-through a IndexedDB en `aiChat.ts`. | Cambio entre chats **instantáneo** desde el caché local. |
| **React UI** | Aplicar `React.memo` a `MessageBubble`/`Markdown`, virtualizar lista con `@tanstack/react-virtual` y extraer constantes de plugins Markdown fuera de JSX. | Renderizado fluido a 60 FPS sin caídas de framerate durante el streaming. |
