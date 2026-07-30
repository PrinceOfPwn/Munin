# Munin

> *What was once seen is never forgotten.*

Munin es una plataforma MCP para investigación y automatización de seguridad en
entornos **autorizados**. Combina un agente ReAct, herramientas de reconocimiento,
LDAP, inteligencia pasiva, memoria persistente, subagentes y una interfaz web.
Su rasgo diferencial es que puede crear herramientas Python y configuraciones de
subagentes ("grafos") durante una sesión, verificarlas y conservar su estado para
la siguiente.

No es un sistema autónomo para atacar objetivos. El operador conserva el control
de los secretos, el alcance, los comandos de alto impacto y las propuestas de
cambio de identidad. Úsalo únicamente contra laboratorios y activos para los que
tengas autorización explícita.

## Qué hay disponible

| Área | Qué aporta |
| --- | --- |
| MCP | Servidor FastMCP por HTTP (`/mcp`) con autenticación Bearer. |
| Agente | `munin_chat` ejecuta un loop ReAct, registra decisiones y puede delegar. |
| LDAP | Consultas parametrizadas para AD/OpenLDAP, más un mock `dc=meli,dc=com`. |
| Recon | La sesión de GitHub instala y comprueba nmap, nuclei, ffuf, feroxbuster y otras utilidades. |
| Inteligencia | Hugin, CVE/NVD/EPSS/CISA/OSV y Tavily opcional. |
| Memoria | Hechos semánticos, episodios, mensajes, presencia, cola de trabajo e inteligencia compartida. |
| Evolución | `tool_forge` crea `gen__*`; `graph_forge` crea especialistas ReAct con una lista permitida de tools. |
| GUI | Chat, explorador de tools, memoria, Soul y trazas incrementales de subagentes. |

Consulta el inventario de herramientas en [docs/tools_reference.md](docs/tools_reference.md).

## Inicio rápido local

Requisitos: Python/Poetry, Node.js 18+ para la GUI y Docker si se usará el LDAP
de demostración.

```bash
poetry install
cp .env.example .env
# Completar como mínimo LLM_BASE_URL, LLM_API_KEY, LLM_MODEL y MUNIN_MCP_AUTH_TOKEN.

poetry run munin ldap-mock up
poetry run munin mcp --transport streamable-http --host 127.0.0.1 --port 8890
```

En otra terminal:

```bash
cd app
npm ci
npm run dev
```

Abre `http://localhost:3000`, entra en **Settings** y configura:

- MCP Base URL: `http://localhost:8890`
- Bearer Token: el valor de `MUNIN_MCP_AUTH_TOKEN`

El navegador guarda el token solo en su `localStorage`; Turso y las claves de
proveedores LLM nunca se entregan a la GUI.

Para un recorrido guiado y ejemplos de cada panel, lee la
[guía de usuario y operador](docs/guia-operativa-es.md).

## Sesión online: GitHub Actions + Turso

La opción recomendada para una demostración persistente es ejecutar **Munin Live
Session** desde Actions. El workflow crea un runner Kali temporal, levanta un LDAP
mock aislado, verifica la toolchain y publica una GUI temporal.

1. Configura estos secretos de Actions: `LLM_BASE_URL`, `LLM_API_KEY`,
   `LLM_MODEL`, `MUNIN_MCP_AUTH_TOKEN`, `MUNIN_DB_URL` y
   `MUNIN_DB_AUTH_TOKEN`.
2. Abre **Actions → Munin Live Session → Run workflow**.
3. Para una sesión habitual elige `open_web_gui=true`, `persist_state=true` y
   `preflight_policy=off` únicamente porque el target es el LDAP mock del job.
4. Abre **Web GUI** desde el Job Summary e introduce solo el Bearer token.
5. Ejecuta `munin_diagnostics` en modo `deep` antes de una demo, o `paranoid`
   para comprobar la cadena forge → graph → wake → resultado.

Turso es la fuente de verdad online. Con `MUNIN_DB_URL=libsql://...`, Munin usa
una conexión directa con commits autocommit: no depende de una réplica local del
runner ni de que un artifact de GitHub esté disponible. Los artifacts siguen
siendo una copia auxiliar para resultados/archivos de una sesión, y una cuota de
artifacts agotada no invalida el estado que ya se guardó en Turso.

La configuración exacta, la duración, los límites del túnel y la recuperación
operativa están documentados en [docs/guia-operativa-es.md](docs/guia-operativa-es.md#sesión-online-con-github-actions-y-turso).

## Persistencia y rehidratación

Munin separa tres tipos de estado:

| Capa | Fuente de verdad | Cómo sobrevive |
| --- | --- | --- |
| Soul | Markdown en `soul/` | Las ediciones se proponen y el humano las acepta mediante PR. |
| Memoria/coord. | SQLite local o Turso | Turso persiste hechos, episodios, cola, mensajes, tools y grafos. |
| Herramientas/grafos forjados | Registro de Turso + archivos/manifests | El registro conserva metadata y el **código fuente de tools nuevas** para rehidratarlo en otro runner. |

Cuando una tool se forja, pasa por validación AST y sandbox, se registra como
`gen__<nombre>`, se expone inmediatamente por MCP y se persiste. En el siguiente
arranque, el registro reconstruye el archivo si falta y vuelve a cargarla. Las
tools creadas por versiones antiguas que solo guardaron una ruta de archivo no se
pueden reconstruir si ese runner/artifact ya desapareció: se señalan como legado
para regenerarlas una vez.

Los detalles de esquema, ciclo de vida y recuperación están en
[docs/arquitectura-persistencia-es.md](docs/arquitectura-persistencia-es.md).

## Operación segura y humana en el loop

- Revisa **Agents**: la traza muestra eventos y mensajes incrementales de cada
  subagente. Puedes inspeccionar lo que ocurre antes de decidir la próxima
  instrucción o cancelar la sesión desde el entorno que la ejecuta.
- Las ediciones de **Soul** no se aplican silenciosamente: se escriben como
  propuesta y pueden abrir un PR para revisión humana.
- `graph_forge` limita a cada especialista a una whitelist explícita de tools.
- Las acciones activas están sujetas a `PREFLIGHT_POLICY`; no uses `off` fuera
  del laboratorio del workflow.
- La ejecución de Python generado tiene defensas, pero no constituye una frontera
  de seguridad fuerte. Trata los specs y el LLM como insumos confiables.

Lee [docs/security-notes.md](docs/security-notes.md) antes de exponer un túnel o
habilitar auto-commit.

## Diagnóstico

`munin_diagnostics` devuelve un resumen estructurado de DB, LLM, LDAP, Hugin,
Tavily, registry, grafos, cola, presencia, auth y persistencia:

| Modo | Uso |
| --- | --- |
| `quick` | Revisión rápida de estado local/caché. |
| `deep` | Antes de una demo: además comprueba bind LDAP y refresca Hugin. |
| `paranoid` | Validación end-to-end: forja una tool temporal, un grafo, lo despierta y espera resultado; requiere LLM. |

Un advisory (por ejemplo, Tavily sin clave) no implica que el sistema entero esté
caído. Un `hard_failure` sí requiere corrección antes de depender de ese
subsistema. La guía incluye un procedimiento para errores de timeout, Turso,
tools heredadas y GUI en [Troubleshooting](docs/guia-operativa-es.md#troubleshooting).

## Estructura

```text
munin/                 servidor MCP, estado, registry y tools Python
munin/core/            agente ReAct, LLM y orquestador
munin/subagents/       runners, graph/tool forge y validación de código
app/                   GUI Next.js
soul/                  identidad, principios y objetivos versionables
docs/                  guías técnicas y operativas
.github/workflows/     CI y Munin Live Session
scripts/               LDAP mock, Turso smoke y túneles
tests/                 regresiones y contratos de persistencia
```

## Documentación

- [Guía de usuario y operador (español)](docs/guia-operativa-es.md)
- [Arquitectura y persistencia (español)](docs/arquitectura-persistencia-es.md)
- [Referencia de herramientas](docs/tools_reference.md)
- [Notas de seguridad](docs/security-notes.md)
- [Proveedores LLM](docs/llm-providers.md)
- [README de la GUI](app/README.md)

## Comandos útiles

```bash
poetry run munin run
poetry run munin mcp --transport streamable-http
poetry run munin ldap-mock up|down|status|logs
poetry run munin config
poetry run munin snapshot-soul
poetry run munin reset
poetry run pytest tests/ -q
```

`munin reset` elimina memoria operativa, cola y herramientas/grafos con política
`on_reset`; no lo uses como mecanismo de diagnóstico en una instancia que quieras
conservar sin revisar antes su impacto.
