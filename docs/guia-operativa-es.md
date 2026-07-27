# Guía de usuario y operación de Munin

Esta guía describe el comportamiento actual del repositorio: qué puede hacer
Munin, cómo conectar la GUI, cómo mantener el estado en Turso y cómo observar
un flujo con un humano en el loop. Está orientada a laboratorios, evaluaciones y
activos con autorización explícita.

## Modelo mental

Munin no es un único chatbot. Es un servidor MCP con cinco planos:

1. **Interacción:** la GUI o un cliente MCP llama `munin_chat` o una tool.
2. **Razonamiento:** el agente ReAct combina Soul, memoria y catálogo de tools.
3. **Ejecución:** las tools nativas realizan consultas, inteligencia o trabajos
   autorizados; el runner de Actions agrega binarios de recon.
4. **Delegación:** `munin_wake` manda trabajo a un subproceso nativo o a un
   grafo forjado.
5. **Evidencia y continuidad:** cada paso significativo se guarda como episodio,
   y Turso conserva el estado que no debe morir con el runner.

El operador decide el alcance y puede inspeccionar el avance. El sistema no
debe interpretarse como permiso para actuar contra terceros.

## Primer arranque local

1. Copia `.env.example` a `.env`. Define LLM, token MCP y, si corresponde,
   LDAP/Turso. No pongas esos valores en documentación, commits o capturas.
2. Instala Python:

   ```bash
   poetry install
   ```

3. Para el laboratorio incluido, levanta el mock LDAP:

   ```bash
   poetry run munin ldap-mock up
   poetry run munin ldap-mock status
   ```

4. Inicia MCP. Por defecto escucha en loopback, una elección deliberada:

   ```bash
   poetry run munin mcp --transport streamable-http --host 127.0.0.1 --port 8890
   ```

5. Inicia la GUI en otra terminal:

   ```bash
   cd app
   npm ci
   npm run dev
   ```

6. En `http://localhost:3000`, abre el engranaje e ingresa la URL y el token.
   Pulsa **Test connection**. Si falla, revisa primero que la URL incluya el
   host/puerto correctos, que el backend siga vivo y que el token coincida.

## Cómo usar la GUI

### Chat

Chat llama `munin_chat`. Para una tarea breve puede responder síncronamente;
para trabajos más largos acepta `mode="async"`, que devuelve un `job_id`. En
la GUI se muestran los tool calls y las respuestas estructuradas. Evita recargar
la página como mecanismo de cancelación: eso no cancela el trabajo del servidor.

Ejemplos de solicitudes seguras para el mock:

- “Comprueba mi identidad LDAP y resume la estructura del dominio.”
- “Guarda como memoria que este objetivo es el laboratorio LDAP de la sesión.”
- “Crea un especialista de inventario LDAP de solo lectura y muéstrame su
  whitelist antes de despertarlo.”

Para usar una tool de forma directa, el chat acepta `/nombre_tool clave=valor`.
También está el panel **Tools**, más conveniente para parámetros JSON complejos.

### Tools

El explorador obtiene el esquema de cada tool desde MCP. Las respuestas siguen
el sobre común `{ok, tool, mode, summary, data, error?}`. La información de
interés normalmente está dentro de `data`; `ok:false` describe un error del
dominio sin que necesariamente el servidor haya caído.

La disponibilidad de binarios depende del entorno. El workflow Live Session
instala y verifica nmap, nuclei, feroxbuster, ffuf, sqlmap, hydra, smbmap,
netexec, katana, httpx y herramientas auxiliares. Una instalación local sin
ellos devuelve un error de dependencia estructurado en vez de inventar éxito.

### Memory

**Semantic** contiene hechos identificados por clave. Para guardar desde MCP:

```json
{"key":"scope","value_json":"{\"environment\":\"ldap mock\",\"authorized\":true}"}
```

`value_json` es texto JSON válido, incluso para un valor simple (por ejemplo
`"\"laboratorio\""`). Para consultar usa `memory_recall` y para revisar el
conjunto `memory_list`.

**Episodic** es una línea de tiempo de decisiones, tool calls y eventos de los
agentes. No es un almacén de secretos ni sustituye un registro de auditoría
externo.

**Forged graphs** lista especialistas creados por `graph_forge`. Desde ahí se
puede inspeccionar la descripción; el estado real se actualiza con polling MCP.

### Soul

Soul son archivos Markdown versionables en `soul/`: identidad, principios,
objetivos y skills. `soul_list` lista los archivos y `soul_read` recibe siempre
`path`, por ejemplo `identity.md` — no un objeto JSON copiado del resultado de
otra tool.

La GUI envía cambios mediante `soul_propose_edit(path, new_content, rationale)`.
No reemplaza automáticamente la identidad activa: deja una propuesta y, cuando
`MUNIN_AUTO_PR=1` y `gh` está disponible, intenta abrir un PR para revisión
humana. Revisa y mergea esa propuesta de manera explícita.

### Agents y humano en el loop

El panel **Agents** muestra presencia, cola de wake y mensajes. Al seleccionar
un agente, **Live trace** consulta `subagent_trace` cada pocos segundos con
cursors incrementales. La traza incluye episodios y mensajes `PROGRESS`, por
lo que permite observar qué está haciendo sin perder eventos intermedios.

El HITL actual es de **observación, revisión y nueva instrucción**: el humano
puede revisar la whitelist/resultado, enviar una nueva tarea o detener la sesión
desde su entorno de ejecución. No es un mecanismo de aprobación bloqueante por
cada tool call; no asumas que una pestaña abierta detiene automáticamente a un
subagente ya despertado.

## Sesión online con GitHub Actions y Turso

### Secrets mínimos

En `Settings → Secrets and variables → Actions`, configura:

| Secret | Uso |
| --- | --- |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | Proveedor OpenAI-compatible. |
| `MUNIN_MCP_AUTH_TOKEN` | Token Bearer de la GUI y clientes MCP. |
| `MUNIN_DB_URL` | URL `libsql://` de Turso. |
| `MUNIN_DB_AUTH_TOKEN` | Token con acceso de lectura/escritura a esa DB. |

`TAVILY_API_KEY`, NVD y similares son opcionales: su ausencia se informa como
advisory de diagnóstico, no como un fallo total.

### Lanzamiento

En **Actions → Munin Live Session → Run workflow** elige:

- `open_web_gui=true` para recibir un sitio temporal.
- `persist_state=true` para permitir la copia auxiliar de archivos/resultados.
- `open_public_tunnel=false` salvo que realmente necesites exponer MCP.
- `preflight_policy=off` solo para el LDAP mock aislado del workflow; usa una
  política restrictiva en cualquier otro contexto.
- `duration_minutes` entre 1 y 55. El runner se detiene al finalizar.

El Job Summary publica la URL temporal de la GUI y nunca imprime el token. En
esa GUI deja la URL MCP por defecto/same-origin y pega únicamente el token MCP.
No pegues tokens de Turso en el navegador.

### Qué se comprueba antes de quedar disponible

La acción instala dependencias, hace un smoke de Turso si está configurado,
espera y siembra el LDAP mock, arranca MCP, comprueba `tools/list` y ejecuta un
smoke autorizado contra el servicio LDAP del job. Con GUI activa, construye el
frontend de producción y comprueba que responda localmente antes de abrir el
túnel.

Los artifacts son auxiliares. GitHub puede rechazar su subida por cuota, pero
en modo Turso la memoria, grafos y fuentes de tools generadas ya quedaron en la
base online. Revisa el paso de Turso y `munin_diagnostics` para confirmar el
estado, en vez de concluir que se perdió solo porque falló un upload de artifact.

## Crear una tool y un especialista

Este es el ciclo recomendado, de menor a mayor privilegio:

1. Enumera la capacidad ya existente con `list_generated_tools` y las tools
   nativas. No forjes un duplicado sin necesidad.
2. Pide a `tool_forge` una función pequeña, determinista y con una sola
   responsabilidad. Ejemplo: resumir de manera local resultados LDAP que la
   sesión ya obtuvo. La herramienta pasa por validación AST y una ejecución de
   prueba antes de registrarse como `gen__<slug>`.
3. Inspecciona con `describe_generated_tool`, y ejecútala solo en datos/targets
   aprobados.
4. Crea un especialista con `graph_forge`. Dale una finalidad concreta y una
   whitelist mínima; una whitelist no es decoración, es el límite de su surface
   MCP.
5. Revisa el grafo con `describe_generated_graph`.
6. Despiértalo con `munin_wake`, proporcionando `task_json` como objeto JSON.
7. Sigue `subagent_trace`, `fetch_agent_messages` y la cola. El resultado vuelve
   como mensaje para Munin.

Ejemplo de grafo de análisis LDAP de solo lectura:

```json
{
  "name": "ldap_inventory",
  "purpose": "Inventariar el LDAP autorizado y resumir hallazgos verificables.",
  "system_prompt_hints_csv": "solo lectura,evidencia por atributo,no modificar LDAP",
  "tool_whitelist_csv": "ldap_who_am_i,ldap_search,dump_domain_structure,get_user_groups,publish_shared_intel",
  "reset_policy": "persistent"
}
```

Después, una wake segura para el mock puede ser:

```json
{
  "subagent": "ldap_inventory",
  "task_json": "{\"objective\":\"Inventariar OUs, grupos y cuentas del LDAP mock; publicar solo hechos comprobados.\"}",
  "priority": 0
}
```

Los nombres nativos aceptados incluyen `ldap_agent`, `tool_forge` y
`graph_forge`; el resto debe existir antes como grafo. Si `munin_wake` dice
`unknown_subagent`, primero crea o corrige el grafo, no reintentes a ciegas.

## Diagnóstico y verificación

Ejecuta `munin_diagnostics` después de arrancar y lee la lista `checks`:

- **db** debe informar backend y conteos.
- **llm** indica si están presentes URL, clave y modelo, sin revelar secretos.
- **ldap** valida bind real en `deep`/`paranoid`.
- **graphs** valida que sus whitelists se resuelvan.
- **forge_registry** verifica que scripts/callables carguen.
- **persistence** muestra si auto-commit/PR y Git están habilitados.

Un timeout externo no equivale necesariamente a datos perdidos. Antes de repetir
una operación costosa, usa la traza, la cola y el registro de episodios para ver
si ya finalizó o si existe una respuesta parcial.

## Troubleshooting

### `Hrana ... stream not found`

Es un error de sesión del transporte libSQL/Hrana, no una señal de que el PR
deba mergearse para que la DB persista. Con la implementación actual, las
conexiones remotas se abren directamente con autocommit para evitar depender de
un stream/réplica efímero. Reinicia el servidor MCP y vuelve a ejecutar
`munin_diagnostics(mode="quick")`; si persiste, confirma que la URL/token de
Turso pertenecen a la misma base y revisa conectividad del runner.

### `durable source missing` en una `gen__*`

La tool fue creada por una versión anterior que registraba solo su ruta local.
Si el artifact/ruta del runner ya no existe, el código no puede reconstruirse.
Regénérala una vez con `tool_forge`; las tools creadas luego guardan su fuente en
el registro remoto y se rehidratan al próximo arranque.

### La GUI muestra cero facts, grafos o tools aunque el log dice que existen

Comprueba primero **MCP Status** y ejecuta la misma tool en Tools. Las vistas
usan el campo `data` de la respuesta MCP; una conexión contra otra URL/runner o
un token inválido suele explicar discrepancias. Recarga después de validar la
conexión. Si el backend responde `ok:true` pero el panel sigue vacío, conserva
la respuesta JSON y abre un issue con ese resultado.

### `tools/call timed out after 30000ms`

El timeout puede pertenecer al cliente, no al agente. Prefiere `munin_chat` en
`mode="async"` para una conversación larga y consulta `job_status`/la traza.
La llamada LLM también usa límites configurables (`LLM_TIMEOUT_FLOOR` y
`LLM_TIMEOUT_CEILING`). No incrementes límites a ciegas: primero mira episodios,
mensajes y logs para distinguir un trabajo vivo de una dependencia bloqueada.

### `soul_read` dice que falta `path`

Pasa el nombre de archivo como `{"path":"identity.md"}`. El resultado de
`soul_list` debe desempaquetarse desde `data.files`; no copies el objeto completo
como si fuera el argumento de `soul_read`.

### Diagnostics marca `graphs` o `forge_registry` como fallo duro

Ejecuta `list_generated_graphs` o `list_generated_tools`, inspecciona el item
con `describe_*` y corrige/deactiva solo el elemento afectado. No ejecutes
`munin reset` como primer paso: borra estado operativo que puede servir para
entender el fallo.

## Límites conocidos

- El sandbox de Python generado es de proceso y defensas por AST; no es un
  aislamiento equivalente a VM/contenedor.
- El HITL es observabilidad y control operativo, no aprobación sincrónica por
  cada llamada.
- Un túnel temporal vuelve accesible la GUI/MCP mientras la sesión vive; no
  expongas un puerto sin token ni amplíes el alcance de un runner de demo.
- La persistencia no hace recuperable código que nunca se guardó (por ejemplo,
  tools heredadas cuyo único archivo murió con un runner previo).
