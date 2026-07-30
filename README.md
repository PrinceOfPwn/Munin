# Munin

> *"What was once seen is never forgotten."*

Munin es un agente ReAct ofensivo con **alma persistente** (soul), memoria
episódica, forjado dinámico de tools/subagentes y una arquitectura que le
permite **evolucionar sesión a sesión** en GitHub Actions.

**Highlights de esta build:**

- 65+ tools MCP nativas (LDAP, recon, intel, memoria, subagentes)
- **`tool_forge` / `graph_forge`** — Munin escribe sus propias tools y
  subagentes en runtime; se persisten en el repo y se rehidratan al arrancar
- **Runner Kali** — nmap, nuclei, feroxbuster, ffuf, sqlmap, hydra, smbmap,
  netexec, katana, searchsploit preinstalados
- **Persistencia entre sesiones** — SQLite roundtripping vía artifact (free)
  o Turso/libsql (opt-in, ~gratis)
- **Auto-commit + PR** — forged tools se commitean, soul edits abren PRs
- **Live subagent trace** — el frontend muestra iteración en vivo del
  subagent con progress messages
- **`munin_diagnostics`** — probe end-to-end que verifica forge → wake →
  RESULT (modo `paranoid`) antes de shippear

---

## Índice

1. [Inicio rápido — local](#1-inicio-rápido--local)
2. [Inicio rápido — GitHub Actions](#2-inicio-rápido--github-actions)
3. [Mock LDAP](#3-mock-ldap)
4. [Conectar desde Claude Code](#4-conectar-desde-claude-code)
5. [Conectar el frontend](#5-conectar-el-frontend)
6. [Persistencia entre sesiones](#6-persistencia-entre-sesiones)
7. [Diagnóstico end-to-end](#7-diagnóstico-end-to-end)
8. [Secrets de GitHub](#8-secrets-de-github)
9. [Estructura del proyecto](#9-estructura-del-proyecto)
10. [Variables de entorno](#10-variables-de-entorno)
11. [Comandos CLI](#11-comandos-cli)

Para el detalle arquitectónico de persistencia, forge y multi-agente ver
[`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## 1. Inicio rápido — local

```bash
poetry install
cp .env.example .env
# Editar .env: LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, MUNIN_MCP_AUTH_TOKEN

./scripts/ldap_mock.sh up              # mock LDAP (requiere Docker)

poetry run munin run                   # REPL interactivo
# o
poetry run munin mcp --transport streamable-http   # servidor MCP en :8890
```

### Proveedores LLM compatibles

| Proveedor | `LLM_BASE_URL` | Modelo recomendado |
|-----------|-------------|-------------------|
| **Groq** (gratis) | `https://api.groq.com/openai/v1` | `llama-3.3-70b-versatile` |
| NVIDIA NIM | `https://integrate.api.nvidia.com/v1` | `meta/llama-3.3-70b-instruct` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| vLLM local | `http://localhost:8000/v1` | el que tengas |
| Ollama local | `http://localhost:11434/v1` | `llama3.1:70b` |

---

## 2. Inicio rápido — GitHub Actions

El workflow `live-session.yml` corre Munin dentro de un contenedor Kali con la
toolchain ofensiva completa, restaura el state de la última sesión, y al final
sube el nuevo state + commitea las tools forjadas.

1. Fork / clonar el repo, push a un remoto propio.
2. Agregar los [4 secrets](#8-secrets-de-github).
3. `Actions → Munin Live Session → Run workflow`. Configurable:
   - **duration_minutes** (30 por defecto, max 55)
   - **preflight_policy** (`off` en runner es lo normal)
   - **munin_max_iterations** (dejar en blanco = sin cap)
   - **persist_state** (dejar en `true` para roundtrip del SQLite)
   - **open_web_gui** (`true` por defecto: levanta y publica el frontend Next.js)
   - **open_public_tunnel** (opcional: publica MCP además de la GUI)
4. Al finalizar el step "Print connection info" en el Job Summary aparece:
   - **Web GUI**, lista para abrir en el navegador
   - URL MCP pública si se solicitó; si no, la GUI usa su proxy same-origin
   - referencia al secret `MUNIN_MCP_AUTH_TOKEN` (su valor no se imprime)
5. Abrí **Web GUI** y pegá ese token en **Settings**. Para Claude Code, usá la
   URL MCP pública.

Al terminar la sesión el workflow:
- Sube `data/shared_state.sqlite` como artifact `munin-state`.
- Pushea las tools forjadas al branch `munin/session-<run_id>`.
- Crea PRs `soul: <file>` etiquetados `soul-proposal` si Munin propuso ediciones.

**En la siguiente corrida:** al bajarse el artifact, Munin recuerda todo del run
anterior (memoria episódica, semantic, forge registry).

---

## 3. Mock LDAP

Dominio `dc=meli,dc=com` con escenarios ofensivos pre-sembrados. **Sin pistas
filtradas en descriptions** — las señales (SPNs, DONT_REQ_PREAUTH) viven en
atributos estructurados (`title`, `employeeType`) para que el LLM tenga que
inferir la vulnerabilidad, no leer prosa.

| Usuario | Tipo | Detalle |
|---------|------|---------|
| `jdoe`, `administrator` | Domain Admins | Miembros de `Domain Admins` |
| `asmith`, `rgarcia`, `mlopez` | Usuarios normales | IT, Dev, HR |
| `htarget` | AS-REP Roastable | `employeeType: DONT_REQ_PREAUTH` |
| `svc_backup` | Kerberoastable | `title: MSSQLSvc/DBSERVER01.meli.com:1433` |
| `svc_mssql` | Kerberoastable | `title: MSSQLSvc/SQL01.meli.com:1433` |
| `svc_http` | Kerberoastable | `title: HTTP/intranet.meli.com` |
| `svc_jenkins` | Kerberoastable | `title: HTTP/jenkins.meli.com:8080` |

Las tools LDAP (`find_kerberoastable_users`, `find_asrep_roastable_users`,
`find_domain_admins`) detectan automáticamente si el servidor es AD o OpenLDAP
y ajustan filtros/atributos.

```bash
poetry run munin ldap-mock up        # levantar y sembrar
poetry run munin ldap-mock down      # bajar y borrar contenedor
poetry run munin ldap-mock status    # estado + conteo
poetry run munin ldap-mock logs      # logs del contenedor
```

---

## 4. Conectar desde Claude Code

Agregar a `~/.claude.json`:

```json
{
  "mcpServers": {
    "munin": {
      "type": "http",
      "url": "http://localhost:8890/mcp/",
      "headers": {
        "Authorization": "Bearer dev123"
      }
    }
  }
}
```

Para el runner de GitHub Actions, reemplazar la URL con la del Job Summary.
Reiniciar Claude Code — las tools aparecen como tools nativas incluyendo
`munin_diagnostics`, `subagent_trace`, `tool_forge`, `graph_forge`, etc.

---

## 5. Conectar el frontend

```bash
cd munin-app/app
npm install
npm run dev   # → http://localhost:3000
```

En **Settings**: MCP URL + Token del `.env`. El frontend expone:

- **Chat** con Munin (via `munin_chat`)
- **Tool Explorer** con formulario validado por tool
- **Memory** (episódica, semantic, forged graphs)
- **Soul** editor con propose-edit → PR
- **Agents** — presence table + wake queue + **panel de trace en vivo**
  del subagente seleccionado (progress messages + eventos del ReAct loop)

---

## 6. Persistencia entre sesiones

Munin puede evolucionar de una sesión a la próxima. Ver detalle completo en
[`ARCHITECTURE.md`](ARCHITECTURE.md).

**Tres capas de estado:**

| Layer | Vive en | Persiste con |
|---|---|---|
| **Soul** — identidad | `soul/*.md` en el repo | PRs `soul-proposal` que el humano mergea |
| **Forged tools/graphs** — código y specs generados | `munin/generated/*.py` + `munin/generated/graphs/*.json` | Commit automático al branch de sesión + catálogo en Turso |
| **Memoria** — episódicos, semantic, wake queue | `data/shared_state.sqlite` | Free: artifact `munin-state`. Paid: Turso/libsql |

**Modo free (default):** el workflow sube `shared_state.sqlite` como artifact
al final de cada corrida, y baja el más reciente al arrancar la próxima.

**Modo Turso (recomendado):** setear los repo secrets
`MUNIN_DB_URL=libsql://xxx.turso.io` y `MUNIN_DB_AUTH_TOKEN=<token>`.
Munin usa una réplica embebida por runner sincronizada contra Turso, conserva
memoria, resultados, mensajes, grafos y cachés entre sesiones, y habilita
sesiones concurrentes.

---

## 7. Diagnóstico end-to-end

Antes de una demo, o cuando algo no cuadra, correr:

```
/munin_diagnostics mode=paranoid
```

Verifica en cascada:

1. Backend SQLite/libsql (row counts por tabla)
2. LLM configurado (URL + key + model)
3. LDAP (bind real en modo `deep` o `paranoid`)
4. Binarios de recon en PATH (nmap, nuclei, feroxbuster, ffuf, sqlmap, hydra…)
5. Hugin cache freshness + refresh
6. Tavily config
7. Forge registry (cada script existe, cada callable importa limpio)
8. Graphs (cada tool en cada whitelist existe realmente)
9. Wake queue + agent presence
10. Auth middleware
11. git-persist config (auto-commit / auto-PR / branch)
12. **[paranoid]** end-to-end real: forja `gen__echo_text`, forja grafo
    especialista, `munin_wake`, espera RESULT hasta 45s, cleanup completo.

Devuelve `{ok, hard_failures, advisories, checks: [{name, ok, latency_ms, detail}]}`.
Con `ok=true` en modo `paranoid`, el sistema está ready to ship.

Modos:
- `mode=quick` (~500ms, sin llamadas externas)
- `mode=deep` (~2-5s, además LDAP bind + Hugin refresh)
- `mode=paranoid` (~30-60s, requiere LLM configurado)

---

## 8. Secrets de GitHub

`Settings → Secrets and variables → Actions → New repository secret`

| Secret | Ejemplo / Ejemplo | Requerido |
|--------|---------|---|
| `LLM_BASE_URL` | `https://api.groq.com/openai/v1` | sí |
| `LLM_API_KEY` | `gsk_...` | sí |
| `LLM_MODEL` | `llama-3.3-70b-versatile` | sí |
| `MUNIN_MCP_AUTH_TOKEN` | `openssl rand -hex 32` | sí |
| `MUNIN_DB_URL` | `libsql://xxx.turso.io` | recomendado (Turso) |
| `MUNIN_DB_AUTH_TOKEN` | token read/write de la base | recomendado (Turso) |

El workflow tiene ya declarado `permissions: contents: write, pull-requests: write`
así que el `GITHUB_TOKEN` auto-generado puede hacer commit + push + abrir PRs.

---

## 9. Estructura del proyecto

```
munin/
├── munin/                          # Código Python
│   ├── cli.py                      # CLI entry point
│   ├── mcp/                        # MCP server + tools
│   │   ├── main.py                 # FastMCP, tools nativas, auth middleware
│   │   ├── config.py               # Settings desde env
│   │   ├── persistence.py          # ▸ NUEVO: SQLite / libsql / Turso abstraction
│   │   ├── git_persist.py          # ▸ NUEVO: async commit worker for forged artifacts
│   │   ├── shared_state.py         # SQLite WAL, 9 tablas, try_claim_spawn_slot
│   │   ├── registry.py             # Hot-load gen__ tools, cache por mtime
│   │   ├── audit.py                # Audit log + redacción de secretos
│   │   ├── opsec.py                # Preflight policy + install hints
│   │   ├── intel.py                # CVE/NVD/EPSS/CISA/OSV
│   │   └── tools/                  # tools MCP registradas
│   │       ├── diagnostics_tool.py # ▸ NUEVO: munin_diagnostics
│   │       ├── ldap_tools.py       # LDAP compatible AD + OpenLDAP
│   │       ├── forge_tool.py       # tool_forge (con commit a git)
│   │       ├── graph_forge_tool.py # graph_forge, list/describe/drop
│   │       ├── munin_tools.py      # memoria, soul, wake, subagent_trace
│   │       ├── tavily_tool.py      # Tavily con errores estructurados
│   │       └── hugin_tool.py       # Hugin con fallback multi-URL
│   ├── core/                       # LLM, soul, memory, orchestrator, agent
│   │   ├── munin_agent.py          # ReAct loop, guard de repetición
│   │   ├── orchestrator.py         # Wake + spawn subprocess
│   │   ├── memory.py
│   │   ├── soul.py
│   │   └── llm_client.py
│   └── subagents/                  # ldap_agent, tool_forge, graph_forge, runner
│       ├── runner.py               # subprocess entrypoint, RESULT overflow → artifact
│       ├── base.py                 # ReActSubagentBase, build_tool_catalog con gen__
│       ├── sandbox.py              # AST guard, banned attrs, validate_source_file
│       ├── ldap_agent.py
│       ├── tool_forge.py
│       └── graph_forge.py
├── soul/                           # Identidad de Munin (Markdown, editable via PR)
├── scripts/
│   ├── ldap_mock.sh                # Toggle mock LDAP
│   ├── ldap_mock.ldif              # Datos del mock (SPNs en title, UAC en employeeType)
│   └── open_tunnel.sh              # Tunnel público (localhost.run / cloudflared)
├── tests/                          # ~40 tests offline
├── docs/                           # architecture, security-notes, tools_reference
├── .github/workflows/
│   └── live-session.yml            # Kali runner + state roundtrip + auto-commit
├── ARCHITECTURE.md                 # ▸ NUEVO: persistencia + multi-agente
├── .env.example                    # ▸ ampliado con MUNIN_DB_URL, MUNIN_AUTO_COMMIT, MUNIN_AUTO_PR
├── pyproject.toml
├── MAP.md
└── README.md
```

Frontend en `munin-app/app/` (Next.js 14 + Tailwind + Zustand). Componente
clave nuevo: `SubagentTrace.tsx` (live iteration view del subagent).

---

## 10. Variables de entorno

Ver `.env.example` para el listado completo con comentarios.

### Núcleo (siempre necesarias)

| Variable | Default | Descripción |
|----------|---------|-------------|
| `LLM_BASE_URL` | — | Endpoint OpenAI-compatible |
| `LLM_API_KEY` | — | API key |
| `LLM_MODEL` | — | Modelo |
| `MUNIN_MCP_AUTH_TOKEN` | — | Bearer token para HTTP transport |

### Persistencia (opcionales)

| Variable | Default | Descripción |
|----------|---------|-------------|
| `MUNIN_DB_URL` | `""` | Vacío = SQLite local. `libsql://...` = Turso |
| `MUNIN_DB_AUTH_TOKEN` | `""` | Token separado para la base Turso |
| `MUNIN_AUTO_COMMIT` | `0` | `1` para commitear forged tools al repo |
| `MUNIN_AUTO_PR` | `0` | `1` para abrir PRs en `soul_propose_edit` |
| `MUNIN_GIT_BRANCH` | *(unset)* | Branch destino para auto-commits |

### ReAct + logging

| Variable | Default | Descripción |
|----------|---------|-------------|
| `MUNIN_MAX_ITERATIONS` | *(unset)* | Blank = sin cap (HARD_CEILING 10000) |
| `MUNIN_LOG_LEVEL` | `INFO` | `DEBUG` para trace de tool calls |

### LDAP, OPSEC, Munin paths, Hugin/Tavily — ver `.env.example`.

---

## 11. Comandos CLI

```bash
poetry run munin run                            # REPL
poetry run munin mcp --transport streamable-http  # MCP server
poetry run munin config                         # settings redactados
poetry run munin snapshot-soul                  # freeze soul/
poetry run munin reset                          # wipe + restore
poetry run munin ldap-mock up/down/status/logs  # mock LDAP
poetry run munin subagent ldap                  # subagente directo
.venv/bin/python -m pytest tests/ -q             # tests
```
