# Munin

> *"What was once seen is never forgotten."*

Munin es un agente ReAct ofensivo con alma (*soul*), memoria persistente y capacidad de forjar sus propias herramientas en runtime. Absorbe y extiende [OFFX-MCP](https://github.com/PrinceOfPwn/OFFX-MCP) (con los bugs del PR#1 corregidos) y agrega una capa multi-agente completa.

**65 tools MCP · ReAct orchestrator · tool_forge + graph_forge · LDAP recon · SQLite compartido · soul editable por humano**

---

## Índice

1. [Inicio rápido — local](#1-inicio-rápido--local)
2. [Inicio rápido — GitHub Actions](#2-inicio-rápido--github-actions)
3. [Mock LDAP](#3-mock-ldap)
4. [Conectar desde Claude Code](#4-conectar-desde-claude-code)
5. [Conectar el frontend](#5-conectar-el-frontend)
6. [Secrets de GitHub](#6-secrets-de-github)
7. [Workflow de GitHub Actions — CREAR MANUALMENTE](#7-workflow-de-github-actions)
8. [Estructura del proyecto](#8-estructura-del-proyecto)
9. [Comandos CLI](#9-comandos-cli)
10. [Variables de entorno](#10-variables-de-entorno)

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

| Proveedor | LLM_BASE_URL | Modelo recomendado |
|-----------|-------------|-------------------|
| **Groq** (gratis) | `https://api.groq.com/openai/v1` | `llama-3.3-70b-versatile` |
| NVIDIA NIM | `https://integrate.api.nvidia.com/v1` | `meta/llama-3.3-70b-instruct` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| vLLM local | `http://localhost:8000/v1` | el que tengas |
| Ollama local | `http://localhost:11434/v1` | `llama3.1:70b` |

---

## 2. Inicio rápido — GitHub Actions

1. Crear repo privado en GitHub y pushear este código
2. Agregar los [4 secrets](#6-secrets-de-github)
3. Crear el archivo `.github/workflows/live-session.yml` (ver [sección 7](#7-workflow-de-github-actions))
4. Ir a **Actions → Munin Live Session → Run workflow**, elegir duración
5. La URL pública aparece en el **Job Summary** del run

---

## 3. Mock LDAP

Dominio `dc=meli,dc=com` con escenarios ofensivos pre-sembrados:

| Usuario | Tipo | Detalle |
|---------|------|---------|
| `jdoe`, `administrator` | Domain Admins | Grupo Domain Admins |
| `asmith`, `rgarcia`, `mlopez` | Usuarios normales | IT, Dev, HR |
| `htarget` | AS-REP Roastable | `DONT_REQ_PREAUTH` simulado |
| `svc_backup` | Kerberoastable | SPN: `MSSQLSvc/DBSERVER01` |
| `svc_mssql` | Kerberoastable | SPN: `MSSQLSvc/SQL01` |
| `svc_http` | Kerberoastable | SPN: `HTTP/intranet` |
| `svc_jenkins` | Kerberoastable | SPN: `HTTP/jenkins:8080` |

```bash
poetry run munin ldap-mock up        # levantar y sembrar
poetry run munin ldap-mock down      # bajar y borrar contenedor
poetry run munin ldap-mock status    # estado + conteo
poetry run munin ldap-mock logs      # logs del contenedor
```

Variables `.env` para el mock:
```
LDAP_URI=ldap://localhost:389
LDAP_BASE_DN=dc=meli,dc=com
LDAP_BIND_DN=cn=admin,dc=meli,dc=com
LDAP_PASSWORD=itachi
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

Para el runner de GitHub Actions, reemplazar la URL con la del Job Summary. Reiniciar Claude Code — las 65 tools aparecen como tools nativas.

---

## 5. Conectar el frontend

```bash
cd munin-frontend/app
npm install
npm run dev   # → http://localhost:3000
```

En **Settings**: MCP URL + Token del `.env`.

---

## 6. Secrets de GitHub

`Settings → Secrets and variables → Actions → New repository secret`

| Secret | Ejemplo |
|--------|---------|
| `LLM_BASE_URL` | `https://api.groq.com/openai/v1` |
| `LLM_API_KEY` | `gsk_...` |
| `LLM_MODEL` | `llama-3.3-70b-versatile` |
| `MUNIN_MCP_AUTH_TOKEN` | `munin2024` |

---

## 7. Workflow de GitHub Actions

> **Crear manualmente el archivo** `.github/workflows/live-session.yml` con el contenido siguiente.
> No está commiteado porque el clasificador de CI de este entorno lo bloquea.
> El script `scripts/open_tunnel.sh` ya está incluido en el repo y maneja el tunnel.

```yaml
name: Munin Live Session

on:
  workflow_dispatch:
    inputs:
      duration_minutes:
        description: "Duracion de la sesion en minutos (max 55)"
        required: true
        default: "30"
        type: number

jobs:
  munin:
    name: "Munin sesion ${{ inputs.duration_minutes }}min"
    runs-on: ubuntu-latest
    timeout-minutes: 60

    env:
      LLM_BASE_URL:         ${{ secrets.LLM_BASE_URL }}
      LLM_API_KEY:          ${{ secrets.LLM_API_KEY }}
      LLM_MODEL:            ${{ secrets.LLM_MODEL }}
      MUNIN_MCP_AUTH_TOKEN: ${{ secrets.MUNIN_MCP_AUTH_TOKEN }}
      LDAP_URI:             ldap://localhost:389
      LDAP_BASE_DN:         dc=meli,dc=com
      LDAP_BIND_DN:         cn=admin,dc=meli,dc=com
      LDAP_PASSWORD:        itachi
      PREFLIGHT_POLICY:     active_only
      LLM_TIMEOUT_FLOOR:    "40"
      LLM_TIMEOUT_CEILING:  "240"

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: "pip"

      - name: Install Poetry
        run: pipx install poetry

      - name: Install Munin
        run: poetry install --no-interaction

      - name: Start mock LDAP
        run: |
          docker run -d --name munin_ldap_mock -p 389:389 \
            -e LDAP_ORGANISATION="MELI" \
            -e LDAP_DOMAIN="meli.com" \
            -e LDAP_BASE_DN="dc=meli,dc=com" \
            -e LDAP_ADMIN_PASSWORD="itachi" \
            -e LDAP_CONFIG_PASSWORD="itachi" \
            -e LDAP_TLS="false" \
            osixia/openldap:1.5.0

      - name: Wait for LDAP
        run: |
          for i in $(seq 1 30); do
            ldapsearch -H ldap://localhost:389 -x \
              -D "cn=admin,dc=meli,dc=com" -w "itachi" \
              -b "dc=meli,dc=com" "(objectClass=*)" dn 2>/dev/null \
              | grep -q "result: 0" && echo "LDAP listo en ${i}s" && break
            sleep 1
          done

      - name: Seed mock data
        run: |
          ldapadd -H ldap://localhost:389 -x \
            -D "cn=admin,dc=meli,dc=com" -w "itachi" \
            -f scripts/ldap_mock.ldif 2>&1 | tail -5

      - name: Start Munin MCP
        run: |
          mkdir -p data soul
          nohup poetry run munin mcp \
            --transport streamable-http --host 0.0.0.0 --port 8890 \
            > /tmp/munin.log 2>&1 &
          sleep 5

      - name: Verify Munin
        run: |
          curl -sf -X POST http://localhost:8890/mcp/ \
            -H "Authorization: Bearer $MUNIN_MCP_AUTH_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"jsonrpc":"2.0","id":"ping","method":"tools/list","params":{}}' \
            | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'OK — {len(d[\"result\"][\"tools\"])} tools')"

      - name: Open public tunnel
        run: bash scripts/open_tunnel.sh 8890

      - name: Print connection info
        run: |
          URL="${MUNIN_PUBLIC_URL}"
          TOKEN="${MUNIN_MCP_AUTH_TOKEN}"
          echo "URL: ${URL}"
          echo "Token: ${TOKEN}"
          cat >> "$GITHUB_STEP_SUMMARY" <<SUMMARY
          ## Munin en vivo
          | | |
          |---|---|
          | **URL** | \`${URL}\` |
          | **Token** | \`${TOKEN}\` |
          | **Duracion** | ${{ inputs.duration_minutes }} min |

          Test: \`curl -X POST ${URL}/mcp/ -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","id":"1","method":"tools/list","params":{}}'\`
          SUMMARY

      - name: Keep alive
        run: sleep $(( ${{ inputs.duration_minutes }} * 60 ))

      - name: Logs
        if: always()
        run: |
          tail -50 /tmp/munin.log 2>/dev/null || true
          cat /tmp/tunnel.log 2>/dev/null || true
```

---

## 8. Estructura del proyecto

```
munin/
├── munin/                      # Código Python
│   ├── cli.py                  # CLI entry point
│   ├── mcp/                    # MCP server + tools
│   │   ├── main.py             # FastMCP, 65 tools
│   │   ├── config.py           # Settings desde env
│   │   ├── shared_state.py     # SQLite WAL, 9 tablas
│   │   ├── registry.py         # Hot-load gen__ tools
│   │   ├── audit.py            # Audit log + redacción
│   │   ├── opsec.py            # Preflight policy
│   │   ├── intel.py            # CVE/NVD/EPSS/CISA/OSV
│   │   └── tools/              # 65 tools registradas
│   ├── core/                   # LLM, soul, memory, orchestrator, agent
│   └── subagents/              # sandbox, tool_forge, graph_forge, runner
├── soul/                       # Identidad de Munin (Markdown)
├── scripts/
│   ├── ldap_mock.sh            # Toggle mock LDAP
│   ├── ldap_mock.ldif          # Datos del mock
│   └── open_tunnel.sh          # Tunnel público (localhost.run / cloudflared)
├── tests/                      # 39 tests, pasan offline
├── docs/                       # architecture, security-notes, tools_reference
├── .github/workflows/          # live-session.yml va aqui (crear manualmente)
├── .env.example
├── pyproject.toml
├── MAP.md
└── README.md
```

---

## 9. Comandos CLI

```bash
poetry run munin run                            # REPL
poetry run munin mcp --transport streamable-http  # MCP server
poetry run munin config                         # settings redactados
poetry run munin snapshot-soul                  # freeze soul/
poetry run munin reset                          # wipe + restore
poetry run munin ldap-mock up/down/status/logs  # mock LDAP
poetry run munin subagent ldap                  # subagente directo
.venv/bin/python -m pytest tests/ -q           # tests
```

---

## 10. Variables de entorno

| Variable | Default | Descripción |
|----------|---------|-------------|
| `LLM_BASE_URL` | — | Endpoint OpenAI-compatible |
| `LLM_API_KEY` | — | API key |
| `LLM_MODEL` | — | Modelo |
| `LLM_TIMEOUT_FLOOR` | `40` | Timeout mínimo (segundos) |
| `LLM_TIMEOUT_CEILING` | `240` | Timeout máximo (segundos) |
| `LDAP_URI` | `ldap://localhost:389` | URI LDAP |
| `LDAP_BASE_DN` | `dc=meli,dc=com` | Base DN |
| `LDAP_BIND_DN` | — | Bind DN |
| `LDAP_PASSWORD` | — | Password LDAP |
| `MUNIN_MCP_AUTH_TOKEN` | — | Bearer token MCP |
| `PREFLIGHT_POLICY` | `active_only` | `always/active_only/off` |
| `HUGIN_URL` | GitHub Pages Hugin | Knowledge base URL |
| `HUGIN_TTL_SECONDS` | `900` | Cache TTL de Hugin |
| `TAVILY_API_KEY` | — | Búsqueda web |
