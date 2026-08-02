# Reporte Final: Auditoría Completa de Bugs y Defectos en Frontend (`app/src/`)

Este informe recopila el diagnóstico exhaustivo realizado por los subagentes especializados sobre la capa de componentes UI, gestión de estado, persistencia local IndexedDB, resiliencia de comunicación y SSE proxy (`app/src/lib/`, `app/src/components/`, `app/src/app/api/`).

---

## 1. Bugs Críticos de Estado y Almacenamiento Local

| ID | Archivo / Componente | Severidad | Descripción Técnica del Defecto |
| :---: | :--- | :---: | :--- |
| **BUG-1** | `queries.ts` (Línea 71-96) | **CRÍTICO** | **Destrucción del Caché al Buscar**: Al filtrar en la barra lateral, `writeConversations` **sobrescribe la base de datos IndexedDB únicamente con los resultados de la búsqueda**. Al limpiar el buscador, el caché en el navegador queda corrupto y se pierden las demás conversaciones locales. |
| **BUG-2** | `cache/context.tsx` & `db.ts` | **CRÍTICO** | **Mensajes Fantasma ("Ghost Messages")**: `setMessages` guarda los mensajes devueltos pero no borra los mensajes previos de esa conversación en IndexedDB. Si el servidor elimina/recorta mensajes, los antiguos reaparecen al reabrir el chat. |
| **BUG-3** | `cache/context.tsx` (Línea 95) | **CRÍTICO** | **Fuga de Datos entre Usuarios en Logout**: `BrowserCacheProvider` evalúa la sesión sólo al montar. Al hacer logout e iniciar sesión con otro usuario sin presionar F5, el nuevo usuario ve e interactúa sobre los chats locales del usuario anterior. |

---

## 2. Vulnerabilidades de Crash, Error Boundaries y Componentes UI

| ID | Archivo / Componente | Severidad | Descripción Técnica del Defecto |
| :---: | :--- | :---: | :--- |
| **BUG-4** | `app/src/components/` | **CRÍTICO** | **Ausencia Total de Error Boundaries**: No existe un solo `ErrorBoundary` en todo el frontend. Cualquier payload nulo o excepción de renderizado desmonta todo el árbol de React provocando una **pantalla blanca de la muerte (White Screen of Death)**. |
| **BUG-5** | `AgentConsole.tsx` (Línea 574) | **ALTA** | **Claves `key` Inestables por Índice**: `message.parts.map((part, idx) => <PartRenderer key={`${message.id}-part-${idx}`} />)` usa el índice `idx` como clave React. Durante el streaming SSE, las partes mutan de posición forzando desmontajes erráticos y pérdida de estado local (como desplegables JSON). |
| **BUG-6** | `ArtifactPart.tsx`, `HitlRequestPart.tsx`, `PlanPart.tsx` | **ALTA** | **Desestructuración Peligrosa sobre Payloads Nulos**: <br>- `ArtifactPart`: `uri.split("/")` y `mimeType.split("/")` lanzan `TypeError` si `uri` o `mimeType` vienen como `undefined`.<br>- `HitlRequestPart`: `Object.keys(args)` lanza `TypeError` si `args` es `null`.<br>- `PlanPart`: `item.status.replace()` lanza `TypeError` si `status` es `undefined`. |
| **BUG-7** | `AgentConsole.tsx` (Línea 1140) | **ALTA** | **Cero Virtualización (DOM Overload)**: Renderiza el 100% de los mensajes y partes del historial sin paginación ni scroll infinito. En chats maduros, se mantienen miles de nodos DOM/SVG bloqueando el hilo principal del navegador. |
| **BUG-8** | `Markdown.tsx` (Línea 25-45) | **ALTA** | **Re-parsing AST en Cada Token**: Pasa plugins de Markdown como arreglos literales inline en JSX (`remarkPlugins={[remarkGfm]}`). `ReactMarkdown` los trata como nuevas referencias en cada render, forzando la re-inicialización y parseo AST de todo el texto en cada token emitido. |

---

## 3. Desbordamientos de Layout CSS & Estilos

| ID | Archivo / Componente | Severidad | Descripción del Defecto |
| :---: | :--- | :---: | :--- |
| **BUG-9** | `Markdown.tsx` (Líneas 83-89) | **MEDIA** | **Código Inline sin Break Word**: Los bloques `<code>` en línea carecen de `break-all` / `break-words`. Cadenas largas ininterrumpidas (hashes SHA-256, tokens JWT, claves API, Base64) rompen los márgenes horizontales del chat. |
| **BUG-10** | `ConversationSidebar.tsx` (Línea 87) | **MEDIA** | **Ancho Inestable de Sidebar**: El panel `<aside>` carece de anchura fija explícita (`w-64` / `w-72`). Se expande o contrae de forma impredecible según la longitud de los títulos de las operaciones. |

---

## 4. Resiliencia de Comunicación, Streams SSE y Autenticación

| ID | Archivo / Componente | Severidad | Descripción del Defecto |
| :---: | :--- | :---: | :--- |
| **BUG-11** | `app/api/chat/[[...path]]/route.ts` | **ALTA** | **Truncado de Streams SSE**: Al recibir trozos de datos (`chunks`) que cortan una línea `data: {...}` por la mitad, `JSON.parse` falla y cae en el `catch` ignorando y **descartando silenciosamente la trama en lugar de retener el buffer** hasta recibir la línea completa. |
| **BUG-12** | `production-api.ts` (Línea 54, 178) | **MEDIA** | **Carrera en Token CSRF Global**: `csrfToken` es una variable mutable a nivel de módulo sin mutex/cola. Si varias peticiones `POST` se lanzan simultáneamente en el inicio o tras la rotación, envían un token vacío u obsoleto provocando fallos HTTP 403. |
| **BUG-13** | `aiChat.ts` (Línea 90-115) | **MEDIA** | **Reconexión Ininterrumpida sin Backoff**: `useChat({ resume: true })` no configura una estrategia de *exponential backoff* en reconexiones de stream. Ante un error HTTP 500/503 persistente en el backend, el frontend spamea peticiones en un loop apretado. |
| **BUG-14** | `production-api.ts` (Línea 70-74) | **MEDIA** | **Crash `TypeError` en 200 OK Malformado**: `.catch(() => ({}))` convierte respuestas 200 OK con HTML o JSONs malformados en `{}`. Métodos posteriores como `productionApi.conversations()` intentan leer `payload.data.conversations`, arrojando un error insalvable: `TypeError: Cannot read properties of undefined`. |

---

## 5. Matriz de Correcciones Priorizadas para el Frontend

1. **Fase 1 (Seguridad y Persistencia Crítica)**:
   - Corregir `writeConversations` en `queries.ts` para no pisar la tabla completa de IndexedDB durante búsquedas filtradas.
   - Modificar `setMessages` en `cache/context.tsx` para borrar mensajes previos de esa conversación en IndexedDB antes de insertar el nuevo arreglo.
   - Reiniciar/limpiar el estado de `BrowserCacheProvider` al ejecutar `logout()`.
2. **Fase 2 (Estabilidad UI & Prevención de Crashes)**:
   - Crear un componente `<ErrorBoundary>` envolviendo las partes de mensajes en `AgentConsole.tsx`.
   - Reemplazar `idx` por IDs estables de partes (`part.id` o `part.kind + part.sequence`) como `key` en `MessagePartList`.
   - Agregar validaciones de presencia opcional (`uri?.split("/")`, `args && Object.keys(args)`) en `ArtifactPart`, `HitlRequestPart` y `PlanPart`.
3. **Fase 3 (Rendimiento & Resiliencia)**:
   - Retener el buffer de líneas cortadas SSE en `route.ts` hasta completar el marco `\n\n`.
   - Extraer constantes `REMARK_PLUGINS` fuera de JSX en `Markdown.tsx`.
