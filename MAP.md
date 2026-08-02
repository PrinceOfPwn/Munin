# MAP.md — Munin: Arquitectura y capacidades

Referencia técnica completa. Para instrucciones de uso ver `README.md`.

---

## Flujo de una petición

```
Usuario (Frontend / MCP client / curl / Discord)
    │
    ▼
munin serve — ASGI unificado :8787
    │  HTTP API /api/* (auth + policy)
    │  MCP streamable-http /mcp/  (Bearer token, JSON-RPC 2.0)
    │  Discord adapter (opcional)
    │
    ▼
Tool handler (en munin/mcp/tools/ y munin/valravn/)
    │
    ├── audit.py → events.jsonl (redacción de secrets)
    ├── opsec.py → preflight check (OSINT/active level gate)
    │
    ├── LDAP tools → ldap3 → OpenLDAP / AD
    ├── Forge tools → ToolForgeSubagent → sandbox → gen__*.py → registry.register()
    ├── Memory tools → SharedStateStore → SQLite / Turso (libsql)
    ├── Recon tools → nmap/nuclei/ffuf/... (subproceso)
    ├── Intel tools → NVD/EPSS/CISA/Hugin/Tavily (HTTP)
    └── Valravn tools → valravn_* (recon mesh: IOCs, CVE, Shodan/Censys, Wayback, ...)
```

---

## Capas del sistema

### 1. Interfaces de usuario

| Interfaz | Cómo conecta | Cuándo usar |
|----------|-------------|-------------|
| `munin serve` | API + MCP en un proceso :8787 | Runtime único (dev y producción) |
| Frontend React | HTTP API → localhost:8787 | Uso normal local / GUI |
| Claude Code / opencode | MCP streamable-http → `/mcp/` | Tools nativas en sesión |
| GitHub Actions | live-session.yml → MCP HTTP → URL pública (tunnel) | Sesión temporal operativa |
| curl / cualquier cliente MCP | JSON-RPC 2.0 directo | Integración, scripting |
| Discord | Adapter opcional (`MUNIN_DISCORD_BOT_TOKEN`) | Continuidad remota |

### 2. MCP Server

- **Framework:** `mcp.server.fastmcp.FastMCP` montado en `munin serve` bajo `/mcp/`
- **Transports:** `streamable-http` (canónico en producción) / `stdio` local
- **Auth:** Bearer token en header `Authorization`
- **Catálogo:** tools nativas (recon, LDAP, Forge, Memory, Intel, Valravn, diagnostics) + N generadas (rehydrate del SQLite/Turso)
- **Audit:** cada tool call → `data/events.jsonl` con secrets redactados
- **Modos de operación:** Standard / YOLO / GOAL / BEAST (contrato por turno; ver README)

### 3. Tools (catálogo nativo)

Los conteos aproximados por grupo; el catálogo real se descubre en runtime vía
MCP `tools/list` (ver `docs/tools_reference.md`). Los grupos principales:

#### LDAP — 8 tools (ice blue)

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

#### Intel / CVE — tools (muted)

`cve_lookup`, `cve_search`, `cve_enrich`, `exploit_search`, `package_vuln_lookup`, `tavily_search`, `hugin_search`, `hugin_refresh`, `publish_shared_intel`, `query_shared_intel`, `shared_state_overview`

#### Valravn — ~12 tools (recon mesh)

Mesh nativo de recon inteligencia externa (`munin/valravn/`), expuesto como
`valravn_*`: enriquecimiento IOC/malware/ransomware/CVE-KEV-EPSS, búsqueda de
assets en Shodan/Censys/ZoomEye/Netlas/LeakIX, pivotes web históricos
(Wayback/Common Crawl/urlscan), routing y RPKI (RIPEstat), contexto Cloudflare
Radar, dark-web (Ahmia vía Tor2Web), captura de evidencia CloakBrowser y
traducción. Ver `docs/VALRAVN.md` para el detalle de cada tool.

#### Admin / Diagnostics — tools

`health_check`, `job_status`, `job_cancel`, `wiki_git_syncer`, `munin_diagnostics`, `munin_read_source`, `munin_self_diagnose`, capabilities/skills helpers.

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

### 5. Memoria — SQLite / Turso (libsql)

Backend por defecto: `data/shared_state.sqlite` (WAL mode, busy_timeout=5s).
Con `MUNIN_DB_URL` + token → Turso remoto (estado persistente multi-sesión).

| Tabla | Origen | Contenido |
|-------|--------|-----------|
| `shared_intel` | OFFX-MCP | Hallazgos de recon (CVEs, puertos, servicios) |
| `active_tasks` | OFFX-MCP | Tareas del pool multi-agente |
| `agent_messages` | OFFX-MCP | Mensajes inter-agente |
| `agent_presence` | OFFX-MCP | Estado RUNNING/IDLE de agentes |
| `episodic` | Munin | Log de tool calls y decisiones del orquestador |
| `semantic` | Munin | Facts clave-valor persistentes |
| `procedural` | Munin | Tools generadas (script_path, signature, tags, active) |
| `generated_graphs` | Munin | Configuraciones de subagentes forjados |
| `agent_wake_queue` | Munin | Cola de wake requests (claimed atómicamente con EXCLUSIVE) |
| `runtime_cache` | Munin | Caché con TTL por namespace |
| `conversations` / `conversation_messages` / `conversation_artifacts` | Munin | Conversaciones persistentes del operador y sus artifacts |
| `provider_profiles` | Munin | Credenciales de provider cifradas (BYOK) |

Además, la Production Suite (`munin/production/store.py`) añade las tablas
operacionales: `users`, `auth_sessions`, `auth_rate_limits`, `messages`,
`agent_runs`, `run_events`, `tool_calls`, `human_requests`, `audit_events`,
`goals`, `todo_events`, `timers`, `operation_snapshots`/`branches`,
`run_guidance_queue`, `conversation_broadcasts`, `_sync_*`, más los registros
de autonomía `workflow_registry` y `agent_registry`. El script
`scripts/reset_turso_state.py` limpia **todas** estas tablas dinámicamente
(descubre tablas en runtime, preserva `schema_migrations`) — ver el workflow
`reset-turso-state.yml`.

---

### 6. Soul — identidad humana-editable

```
soul/
├── identity.md    # Quién es Munin (战争之鸦 / war-raven), carácter operacional
├── principles.md  # Doctrina: dogma, 命令即授权 (la orden ES la autorización), 孙子兵法, campaign loop, OPSEC
├── goals.md       # Misión actual y definición de excelencia (guerra al ritmo del dogma)
├── skills.md      # Inventario de tools nativas + catálogo autogenerado
└── valravn.md     # Protocolo de la mesh de recon Valravn
```

- `soul_read` / `soul_list` → Munin puede leer su propia identidad
- `soul_propose_edit` → propone un cambio → va a `data/soul_pending/` → humano lo revisa y aprueba
- Munin **no puede reescribirse a sí mismo en runtime** (propuestas pasan por review humano)
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
| Path Traversal (CWE-22) | `_safe_soul_path()` rechaza `../` que escape `soul/` |
| SSRF | `_validate_base_url()` en LLM client; `_validate_url()` en Hugin |
| Secret leak en logs | `audit.py` redacta Bearer, api_key, sk-, nvapi-, tvly-, ghp_ |
| Código malicioso en tool_forge | AST guard + builtins restringidos + cwd jailed |
| Token leak entre proveedores | Sesiones HTTP aisladas por proveedor en intel.py |
| Escritura fuera del workspace | Contrato de prompts: reportes/evidencia solo en `reports/` y `evidence/`; runner crea dirs escribibles |

---


Scripts ya incluidos que el workflow referencia:
- `scripts/open_tunnel.sh` — abre tunnel público (localhost.run → cloudflared fallback)
- `scripts/ldap_mock.ldif` — datos del mock LDAP
- `scripts/reset_turso_state.py` — limpieza total del estado Turso (workflow `reset-turso-state.yml`)
- `scripts/restore_munin_artifact.py` — restaura estado durable desde artifact de sesión anterior
