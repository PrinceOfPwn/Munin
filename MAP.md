# MAP.md — Munin: Arquitectura y capacidades

Referencia técnica completa. Para instrucciones de uso ver `README.md`.

---

## Flujo de una petición

```
Usuario (CLI / Frontend / Claude Code / curl)
    │
    ▼
MCP Server — FastMCP streamable-http :8890
    │  Authorization: Bearer <token>
    │  JSON-RPC 2.0: tools/call { name, arguments }
    │
    ▼
Tool handler (en munin/mcp/tools/)
    │
    ├── audit.py → events.jsonl (redacción de secrets)
    ├── opsec.py → preflight check (OSINT/active level gate)
    │
    ├── LDAP tools → ldap3 → OpenLDAP / AD
    ├── Forge tools → ToolForgeSubagent → sandbox → gen__*.py → registry.register()
    ├── Memory tools → SharedStateStore → SQLite
    ├── Recon tools → nmap/nuclei/ffuf/... (subproceso)
    └── Intel tools → NVD/EPSS/CISA/Hugin/Tavily (HTTP)
```

---

## Capas del sistema

### 1. Interfaces de usuario

| Interfaz | Cómo conecta | Cuándo usar |
|----------|-------------|-------------|
| `munin run` CLI | REPL directo, sin red | Desarrollo, debug |
| Frontend React | MCP HTTP → localhost:8890 | Uso normal local |
| Claude Code | `~/.claude.json` mcpServers | Tools nativas en sesión |
| GitHub Actions | MCP HTTP → URL pública (tunnel) | Demo remota, sesión temporal |
| curl / cualquier cliente MCP | JSON-RPC 2.0 directo | Integración, scripting |

### 2. MCP Server

- **Framework:** `mcp.server.fastmcp.FastMCP`
- **Transports:** `stdio` / `sse` / `streamable-http`
- **Auth:** Bearer token en header `Authorization`
- **Tools registradas al iniciar:** 65 fijas + N generadas (rehydrate del SQLite)
- **Audit:** cada tool call → `data/events.jsonl` con secrets redactados

### 3. Tools (65 fijas)

#### LDAP — 8 tools (ice blue)

Todas los parámetros de usuario pasan por `ldap3.utils.conv.escape_filter_chars` (CWE-90).
Credenciales sólo desde `.env`, nunca desde parámetros.

| Tool | Qué hace |
|------|----------|
| `ldap_who_am_i` | Bind + whoami extended op |
| `get_current_user_info` | Atributos del usuario actual |
| `get_user_groups` | Grupos de un usuario (por uid/cn/sAMAccountName) |
| `ldap_search` | Búsqueda paramétrica segura con filter_template + params_json |
| `find_kerberoastable_users` | Users con `servicePrincipalName=*` |
| `find_asrep_roastable_users` | Users con `userAccountControl:DONT_REQ_PREAUTH` |
| `find_domain_admins` | Miembros de un grupo privilegiado |
| `dump_domain_structure` | OUs y containers del dominio |

#### Forge — 9 tools (violet)

| Tool | Qué hace |
|------|----------|
| `tool_forge` | Genera una tool Python, la prueba en sandbox, la hot-loada como `gen__<slug>` |
| `graph_forge` | Genera un config de subagente ReAct (JSON → `generated_graphs` table) |
| `list_generated_tools` | Catálogo de tools forjadas (Munin consulta esto antes de forjar) |
| `describe_generated_tool` | Spec de una tool, opcionalmente con source |
| `run_generated_tool` | Invoca una tool generada por nombre |
| `deactivate_generated_tool` | Soft-delete (active=0) |
| `list_generated_graphs` | Catálogo de grafos generados |
| `describe_generated_graph` | Spec de un grafo |
| `drop_generated_graph` | Elimina un grafo generado |

#### Conversacional — 1 tool

| Tool | Qué hace |
|------|----------|
| `munin_chat` | Interfaz conversacional completa. Recibe texto libre, ejecuta el loop ReAct internamente (LLM → tool calls → LLM → ...) y devuelve la respuesta final + log de cada tool call con args, resultado y elapsed_ms. El frontend renderiza los tool calls como cards inline. Requiere LLM configurado. |

#### Memory / Soul — 7 tools (amber)

| Tool | Qué hace |
|------|----------|
| `memory_remember` | Guarda fact semántico (key → JSON value) |
| `memory_recall` | Recupera fact por key |
| `memory_list` | Lista facts con prefijo opcional |
| `episodic_query` | Últimos N eventos de la memoria episódica |
| `soul_list` | Lista archivos en `soul/` |
| `soul_read` | Lee un archivo de soul (path-traversal guarded) |
| `soul_propose_edit` | Propone edición → `data/soul_pending/` (requiere aprobación humana) |

#### Agentes — 12 tools (emerald)

| Tool | Qué hace |
|------|----------|
| `munin_wake` | Encola wake request para un subagente |
| `munin_wake_claim` | Subagente reclama su próxima tarea (EXCLUSIVE lock) |
| `munin_wake_list` | Lista wake queue (pending / claimed) |
| `list_agent_presence` | Estado RUNNING/IDLE de subagentes |
| `upsert_agent_presence` | Actualiza estado propio |
| `post_agent_message` | Publica mensaje a otro agente |
| `fetch_agent_messages` | Lee mensajes no leídos |
| `ack_agent_message` | Marca mensaje como leído |
| `claim_shared_task` | Reclama tarea del pool compartido |
| `complete_shared_task` | Marca tarea como completada |
| `heartbeat_shared_task` | Heartbeat para tareas long-running |
| `list_shared_tasks` | Lista tareas del pool |

#### Recon / Scan — 14 tools (rose)

Heredadas de OFFX-MCP con fixes de PR#1. Requieren herramientas instaladas en el sistema.

`nmap_scan`, `nmap_advanced_scan`, `nuclei_scan`, `feroxbuster_scan`, `ffuf_scan`, `httpx_probe`, `katana_crawl`, `smbmap_scan`, `netexec_scan`, `hydra_attack`, `sqlmap_scan`, `web_evidence_screenshotter`, `execute_command`, `vpn_status`

#### Intel / CVE — 11 tools (muted)

`cve_lookup`, `cve_search`, `cve_enrich`, `exploit_search`, `package_vuln_lookup`, `tavily_search`, `hugin_search`, `hugin_refresh`, `publish_shared_intel`, `query_shared_intel`, `shared_state_overview`

#### Admin — 4 tools

`health_check`, `job_status`, `job_cancel`, `wiki_git_syncer`

---

### 4. Tools generadas — gen__*

Cuando `tool_forge` crea una tool:

1. LLM genera JSON `{description, function_name, allowed_imports, tags, python}`
2. AST guard valida el código (no subprocess/ctypes/exec/eval)
3. Sandbox in-process ejecuta la función (timeout thread)
4. Si pasa: se escribe `munin/generated/<slug>.py`
5. `registry.register()` → `importlib.util.spec_from_file_location` → `mcp.tool()(handler)`
6. La tool aparece como `gen__<slug>` en el MCP server
7. Se persiste en `procedural` table del SQLite

Al reiniciar el servidor: `registry.rehydrate()` recarga todas las tools `active=1`.

---

### 5. Memoria — SQLite compartido

Archivo único: `data/shared_state.sqlite` (WAL mode, busy_timeout=5s)

| Tabla | Origen | Contenido |
|-------|--------|-----------|
| `shared_intel` | OFFX-MCP | Hallazgos de recon (CVEs, puertos, servicios) |
| `tasks` | OFFX-MCP | Tareas del pool multi-agente |
| `agent_messages` | OFFX-MCP | Mensajes inter-agente |
| `agent_presence` | OFFX-MCP | Estado RUNNING/IDLE de agentes |
| `episodic` | Munin | Log de tool calls y decisiones del orquestador |
| `semantic` | Munin | Facts clave-valor persistentes |
| `procedural` | Munin | Tools generadas (script_path, signature, tags, active) |
| `generated_graphs` | Munin | Configuraciones de subagentes forjados |
| `agent_wake_queue` | Munin | Cola de wake requests (claimed atómicamente con EXCLUSIVE) |

---

### 6. Soul — identidad humana-editable

```
soul/
├── identity.md    # Quién es Munin, qué es soul/memory/manos/subagentes
├── principles.md  # Reglas: escapar LDAP, consultar catálogo antes de forjar, etc.
├── goals.md       # Misión actual (AKATSUKI challenge, tool forging, multi-agent)
└── skills.md      # Inventario de tools nativas + catálogo autogenerado
```

- `soul_read` / `soul_list` → Munin puede leer su propia identidad
- `soul_propose_edit` → propone un cambio → va a `data/soul_pending/` → humano lo revisa y aprueba
- Munin **no puede reescribirse a sí mismo en runtime**
- `munin snapshot-soul` → freeze en `data/soul.snapshot.json`
- `munin reset` → restaura soul desde snapshot

---

### 7. LLM Client — timeout adaptativo

- **EMA de latencias** exitosas × 2.5 → timeout dinámico
- Floor: 40s (nunca por debajo, NIM 70B+ lo necesita)
- Ceiling: 240s, sube 25% por cada timeout, se resetea en éxito
- `_validate_base_url()`: requiere `https://` salvo loopback; bloquea `169.254.x.x`
- `make_langchain()` → retorna `ChatOpenAI` para uso con LangGraph

---

### 8. Sandbox in-process

```
run_code(source) →
  1. ast.parse() → _validate_ast()
     - imports: solo allowlist (ldap3, requests, json, re, etc.)
     - atributos: bloquea __class__, __subclasses__, __mro__, exec, eval
  2. Thread con timeout (best-effort)
  3. exec() con __builtins__ restringidos y cwd jailed
```

Limitación documentada: threads de Python no se pueden interrumpir limpiamente. Código CPU-bound puede exceder el timeout real.

---

### 9. Orquestador multi-agente

```
munin_wake("ldap", task) →
  SharedStateStore.enqueue_wake() →   INSERT agent_wake_queue
  orchestrator._spawn_runner("ldap") → subprocess Popen detach
    python -m munin.subagents.runner ldap
      poll loop:
        claim_wake_item() (BEGIN IMMEDIATE)  →  task
        handle_task(task)
        post_agent_message("munin", result)
      exit after N segundos idle
```

Subagentes nativos: `ldap`, más cualquier grafo en `generated_graphs` (cargados por `runner.py`).

---

### 10. PR#1 fixes (OFFX-MCP)

| Bug | Archivo | Fix |
|-----|---------|-----|
| Token leak: `GITHUB_TOKEN` en sesión compartida → va a NVD/CIRCL/MITRE | `intel.py` | Sesión aislada por proveedor; `Authorization` sólo en `_github_search()` per-request |
| `additional_args` tokenizado como string único | `main.py`, `utils.py` | `split_extra_args()` usa `shlex.split()` |
| Tavily: `api_key` en body + sesión compartida con GitHub PAT | `tavily_tool.py` | Sesión propia; `Authorization: Bearer` en header per-request |

---

### 11. Seguridad

| Riesgo | Mitigación |
|--------|-----------|
| LDAP Injection (CWE-90) | `escape_filter_chars` en todos los params de usuario |
| Path Traversal (CWE-22) | `_safe_soul_path()` rechaza `../` que escape `soul/` |
| SSRF | `_validate_base_url()` en LLM client; `_validate_url()` en Hugin |
| Secret leak en logs | `audit.py` redacta Bearer, api_key, sk-, nvapi-, tvly-, ghp_ |
| Código malicioso en tool_forge | AST guard + builtins restringidos + cwd jailed |
| Token leak entre proveedores | Sesiones HTTP aisladas por proveedor en intel.py |

---

### 12. Archivos que crear manualmente

El siguiente archivo no está incluido en el repo (bloqueado por el clasificador de CI del entorno de desarrollo). Crearlo manualmente copiando el YAML de `README.md` sección 7:

```
.github/workflows/live-session.yml
```

Scripts ya incluidos que el workflow referencia:
- `scripts/open_tunnel.sh` — abre tunnel público (localhost.run → cloudflared fallback)
- `scripts/ldap_mock.ldif` — datos del mock LDAP
