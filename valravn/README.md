# Valravn — Burp DAST surface

[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Version](https://img.shields.io/badge/version-1.0.0-blue)](#versionado)
[![Java](https://img.shields.io/badge/java-21%2B-blue)](https://adoptium.net/temurin/releases/?version=21)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-stdio-blue)](https://modelcontextprotocol.io/)
[![Platforms](https://img.shields.io/badge/platforms-linux%20%7C%20macos%20%7C%20windows%20%7C%20wsl-blue)]#plataformas)

> **v1.0** — Distribuciones: `valravn-mcp` (servidor MCP) y `valravn-burp-ext-<version>.jar`
> (extensión Burp). El paquete Python aún se importa como `burpsuite_mcp` por
> compatibilidad histórica; plan de renombradopreserve en v1.1.

Valravn es la surface de **DAST agéntico fundida con Burp Suite** dentro de la mesh
de inteligencia homónima. No es "otra herramienta encima de Burp" — es la capa que
convierte un LLM concretado con MCP en un pentester nativo de Burp: cada request
pasa por Burp, cada finding vuelve indexado al Logger, cada decisión reconoce la
política server-side de Munin.

## Por qué existe

La mesh Valravn ya cubre inteligencia pasiva (`munin/valravn/` — gateway CTI, AS-lookups,
Wayback, oscurecidos). Esta delivery cierra la otra mitad — trabajo activo bajoBurp
con un cerebro LLM — para que las dos capas compartan evidencia, scope y audit
log sin fricción. Lo que aquí se prueba, Valravn lo recuerda; lo que Valravn
recuerda, aquí se valida antes de declarar finding.

## Autorización

Es un **tool ofensivo**. Usalo únicamente sobre sistemas con autorización escrita:
bug bounty en scope, pentest contratado, red team con ROE firmado, laboratorio
propio, CTF. La política del runtime de Munin y los gates de HITL quedan
**encima** de cualquier instrucción del prompt — el prompt no otorga authority.

## Arquitectura

```
LLM client  <- stdio MCP ->  Python MCP server  <- HTTP ->  Java Burp extension  <- Montoya API ->  Burp Suite
                                            127.0.0.1:8111
```

- La extensión Java expone REST en `127.0.0.1:8111` y enruta HTTP por el proxy
  listener de Burp (`127.0.0.1:8080`), así todo probe aparece en Proxy history.
- El servidor MCP Python es un thin client stdio. Parla con el LLM y con el
  extension, sin knowledge de Burp internals.
- Intel de objetivo persiste en `.valravn-intel/<domain>/` (gitignored). Es el
  mismo directorio que usa la mesh CTI — single source of truth por dominio.

## Características

- **MCP tool surface** — ~370 herramientas cubre recon, scan, exploit, browser,
  auth, research, reporting. Ver [superficie](#superficie-de-tools).
- **HTTP vía Burp** — `curl_request`, `send_raw_request`, `concurrent_requests`,
  Repeater, Intruder estilo — todo indexado en Logger.
- **Scan adaptativo con KB** — 138+ matchers JSON en `knowledge/` mapando OWASP
  Top 10 (Web / API / LLM / Mobile), WSTG, PayloadsAllTheThings, HackTricks. Ver
  [cobertura](#cobertura).
- **Vulnerabilidad classes nativas** — `test_csrf`, `test_ssrf`, `test_ssti`
  (multi-stage polyglot → engine differential → capability probe → blind),
  `test_xxe`, `test_websocket` (CSWSH), `test_prototype_pollution`.
- **Auth tooling zero-deps** — `forge_jwt` (8 modos), `crack_jwt_secret`,
  `test_login_bypass`, `test_mfa_bypass`, `test_session_lifecycle`,
  `analyze_reset_tokens` (entropía + secuencia).
- **Wrappers terceros via proxy** — sqlmap, dalfox, commix, nuclei, ffuf,
  katana, subfinder, amass, wafw00f, arjun, gau, waybackurls, wpscan, nikto.
- **SAST + secrets (v1.0)** — `audit_crawled_artifacts` (opengrep DOM),
  `run_opengrep_source`, `run_gitleaks`, `run_trufflehog` (verificación viva =
  segundo HIGH), `dump_exposed_git` + `discover_common_files` (rebuild `.git/`).
  Noir OpenAPI ingest via `import_scope --format noir_json`.
- **Probes activos LLM/MCP (v1.0)** — `ai_prompt_injection`, `rag_injection`,
  `mcp_server_attacks`, `mcp_tool_poisoning`, `vector_db_injection`, `echoleak`
  (CVE-2025-32711). Guardrails declarativos via `inspect_for_prompt_injection`.
- **CI ready (v1.0)** — SARIF 2.1.0 + JUnit XML, tags de compliance (OWASP /
  PCI-DSS / HIPAA / SOC2 / GDPR / CWE), flag `intensity=safe|normal|aggressive`,
  cost cap por engagement (`set_engagement_cost_cap`), auto-PoC
  `generate_repro_script` curl desde `logger_index`.
- **Save-finding pipeline** — 3 fases: verify (replay ≥3×) → `assess_finding`
  (7-Q gate) → `save_finding`. Q3 es la puerta real de impacto.
- **Falsos positivos defendidos** — reflexión viva (no reflexión "safe-encoded"),
  baseline access-control doble (public ≠ IDOR), OOB forzado para blind
  (Collaborator o callback del operador), timing ≥3× replay.
- **CloakBrowser** — Chromium headless con fingerprints parchados al nivel
  binario (no JS shim). Trafica via proxy de Burp. Ver
  [CloakBrowser](https://github.com/CloakHQ/CloakBrowser).
- **Memoria de objetivo persistente** — staleness detection, patrón cross-target
  reusable. Vitá en la mesh: lo que aprendiste en un engagement anterior no se
  redescubre.
- **Operator override surface** — severity floor, scope filter, NEVER-SUBMIT
  list, confidence floor, program policy.

## Requisitos

- Burp Suite Professional **o** Community Edition
- Java 21+
- Python 3.11+ con [uv](https://docs.astral.sh/uv/)
- MCP client (Claude Code, Claude Desktop, etc.)

Opcional:

- Go para ProjectDiscovery tools (`subfinder`, `nuclei`, `katana`)
- Burp Pro para scanner activo y Collaborator (con fallback elegante en Community)

### Compatibilidad Burp Edition

**Professional** — completo. Es el entorno default.

**Community** — soporta con setup manual. La mayoría funciona, porque extension y
MCP server usan Montoya API para HTTP / proxy / scope, no el scanner pipeline.
Degradación elegante:

| Pro feature | Herramienta afectada | Alternativa Community |
|---|---|---|
| Scanner activo | `scan_url`, `crawl_target`, `get_scan_status`, `cancel_scan`, `get_scanner_findings`, `get_new_findings`, `get_issues_dashboard` | `auto_probe` (KB-driven), `fuzz_parameter`, `fuzz_with_feedback`, `test_*` nativos |
| Collaborator | `generate_collaborator_payload`, `auto_collaborator_test`, `get_collaborator_interactions`, `collaborator_pool_status` | Callback del operador — interact.sh / webhook.site / requestcatcher.com. Rule 9a prohibe dominios inventados |
| Intruder full | `send_to_intruder_configured` | `concurrent_requests` (concurrency Python-side via Burp proxy) |

`check_pro_features()` al inicio de sesión reporta qué capacidades Pro están —
runtime detection, nunca hang silencioso.

## Instalación

### Rápida — `uvx` (sin clone del repo)

El MCP server se ejecuta directo desde el source tree. La extensión Burp JAR
sigue requiriendo checkout — ver Manual.

```sh
uvx --from "git+https://github.com/PrinceOfPwn/Munin.git#subdirectory=valravn/mcp-server" \
    valravn-mcp
```

O en `.mcp.json` (gitignored por Convención):

```json
{
  "mcpServers": {
    "burpsuite": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/PrinceOfPwn/Munin.git#subdirectory=valravn/mcp-server",
        "valravn-mcp"
      ]
    }
  }
}
```

### Automatizada (extension + server)

```sh
./setup.sh        # Linux / macOS
./setup.ps1       # Windows PowerShell
./setup.bat       # Windows doble click
```

El script instala lo faltante (Java 21+, Maven, Python 3.11+, uv, Go), construye
la extensión, instala el MCP server (incluye CloakBrowser warm-up), opcionalmente
instala ProjectDiscovery tools, escribe `.mcp.json`.

Correr `./doctor.sh` para validación post-instalación.

### Manual

```sh
# 1. Build extensión Burp
cd burp-extension
mvn package
# Cargar target/valravn-burp-ext-1.0.0.jar en Burp: Extensions -> Add -> Java

# 2. Instalar MCP server
cd ../mcp-server
uv venv
uv sync

# 3. Configurar tu MCP client (ver abajo)
```

### `pipx`

```sh
pipx install "git+https://github.com/PrinceOfPwn/Munin.git#subdirectory=valravn/mcp-server"
valravn-mcp
```

## Configuración

`.mcp.json` en el root del proyecto. Gitignored; cada operador lo mantiene.

```json
{
  "mcpServers": {
    "burpsuite": {
      "command": "/absolute/path/to/valravn/mcp-server/.venv/bin/python",
      "args": ["-m", "burpsuite_mcp"]
    }
  }
}
```

**Conexión default — single host cero config.** La extensión escucha en
`127.0.0.1:8111` por defecto y el MCP server lo matchea. Cuando Burp y el MCP
client comparten host (o WSL mirrored) no hace falta tocar nada. Windows: usar
`C:\\...\\.venv\\Scripts\\python.exe`.

### WSL (Burp en host Windows)

`./setup.sh` detecta WSL y su networking mode, escribe `.mcp.json` acorde:

- **Mirrored (recomendado, seguro).** Agregá a `%UserProfile%\.wslconfig`:
  ```ini
  [wsl2]
  networkingMode=mirrored
  ```
  `wsl --shutdown` y re-abrir. Burp en `127.0.0.1:8111` queda alcanzable desde
  WSL — sin env overrides, sin bind changes, sin exponer fuera del host. Requiere
  Windows 11 22H2+.
- **NAT (default).** Alcanzá Burp via el host IP (WSL default route gateway):
  1. `.mcp.json` con `env.BURP_API_HOST` = ese IP
  2. En la config tab de Valravn usar `Host = 0.0.0.0`
  3. JVM flag `-Dvalravn.allow_non_loopback_bind=true` al lanzar Burp — la
     extensión rechaza bind non-loopback sin este flag

  NAT expone API sin auth en el switch virtual de WSL — solo host confiable.
  Preferí mirrored.

### Variables de entorno

| Variable | Default | Uso |
|---|---|---|
| `BURP_API_HOST` | `127.0.0.1` | Extension API host |
| `BURP_API_PORT` | `8111` | Extension API port |
| `BURP_API_TIMEOUT` | `30` | HTTP timeout (s) |
| `BURP_PROXY_HOST` | `127.0.0.1` | Proxy listener host |
| `BURP_PROXY_PORT` | `8080` | Proxy listener port |

La extensión Java también acepta JVM system properties `valravn.proxy.host`
y `valravn.proxy.port` (mayor prioridad).

## Uso

Con `.mcp.json` cargado por el MCP client, las tools son visibles al agent.
Sesión típica:

1. `configure_scope` — include/exclude, auto-filter tracker domains
2. `browser_crawl` o `discover_attack_surface` — mapear
3. `auto_probe` — probes KB-driven sobre parámetros
4. `assess_finding` → `save_finding` por cada sospechoso
5. `generate_report` — export

Las skills en `.claude/skills/`-le entregan metodología al agent. El operador
pilotea con override flags o editando `.valravn-intel/programs/<slug>.json`.

## Superficie de tools

Server MCP expone estas familias. Detalles en [CLAUDE.md](CLAUDE.md).

| Grupo | Ejemplos |
|---|---|
| Scope & config | `configure_scope`, `check_scope`, `get_scope` |
| Lectura | `get_proxy_history`, `get_proxy_count`, `get_sitemap`, `get_scanner_findings`, `get_websocket_history` |
| Análisis | `smart_analyze`, `find_injection_points`, `extract_js_secrets`, `analyze_dom` |
| Send (via Burp) | `curl_request`, `send_raw_request`, `concurrent_requests`, `send_to_repeater` |
| Browser | `browser_crawl`, `browser_navigate`, `browser_click`, `browser_execute_js` |
| Session | `create_session`, `session_request`, `extract_token`, `run_flow` |
| Scan adaptativo | `discover_attack_surface`, `auto_probe`, `quick_scan`, `full_recon` |
| Ataque dirigido | `test_auth_matrix`, `test_race_condition`, `fuzz_parameter`, `test_parameter_pollution` |
| Vulnerabilidad classes | `test_csrf`, `test_ssrf`, `test_ssti`, `test_xxe`, `test_websocket` (CSWSH), `test_prototype_pollution` |
| Auth attacks | `forge_jwt`, `crack_jwt_secret`, `test_login_bypass`, `test_mfa_bypass`, `test_session_lifecycle`, `analyze_reset_tokens`, `compare_auth_states` |
| Edge | `test_cors`, `test_jwt`, `test_graphql`, `test_cloud_metadata`, `test_open_redirect` |
| Avanzado | `test_host_header`, `test_request_smuggling`, `test_mass_assignment`, `test_business_logic` |
| Extract | `extract_regex`, `extract_json_path`, `extract_css_selector`, `extract_headers` |
| Repeater & macros | `send_to_repeater_tracked`, `repeater_resend`, `create_macro`, `run_macro` |
| Recon (third-party) | `run_subfinder`, `run_nuclei`, `run_katana`, `run_sqlmap`, `run_dalfox`, `run_ffuf`, `query_crtsh`, `analyze_dns`, `fetch_wayback_urls` |
| Subdomain takeover | `test_subdomain_takeover` — 129 fingerprints (W8 nuclei merge) + DNS-only signal mode (W9) |
| Collaborator | `generate_collaborator_payload`, `auto_collaborator_test`, `get_collaborator_interactions` |
| Intel | `save_target_intel`, `load_target_intel`, `lookup_cross_target_patterns`, `set_program_policy` |
| Hunt advisor | `get_hunt_plan`, `get_next_action`, `assess_finding`, `pick_tool` |
| Security research | `research_attack_vector` — deep-dive prompts + HackerOne hacktivity + writeup URLs (Rule 27) |
| Report | `save_finding`, `generate_report`, `format_finding_for_platform`, `export_report` |

## MCP Prompts

Workflows reusables expuestos por el server:

| Prompt | Args | Uso |
|---|---|---|
| `hunt-target` | `target` | Loop de caza estándar: scope → recon → probe → verify → save |
| `verify-finding` | `vuln_type`, `endpoint`, `evidence` | 7-Q gate pre-save |
| `triage-program` | `program` | Configurar program policy, scope, overrides |
| `chain-findings` | `domain` | Proponer chains A→B→C upgrading severity |
| `save-finding-checklist` | `vuln_type`, `endpoint` | Checklist pre-save forzando replay → assess → save |

## MCP Resources

Contexto read-only montable sin gastar tool budget:

| URI | Devuelve |
|---|---|
| `burp://rules/hunting` | 28 hunting rules permanentes (HARD/DEFAULT/ADVISORY) |
| `burp://rules/engineering` | 4 engineering rules |
| `burp://skills/{name}` | Skill markdown por stem |
| `burp://knowledge/index` | Listado de categorías KB con counts |
| `burp://knowledge/{category}` | JSON crudo de una categoría (probes + matchers + craft) |
| `burp://intel/{domain}/{kind}` | Intel de objetivo: `profile`, `endpoints`, `coverage`, `findings`, `fingerprint`, `patterns`, `notes` |
| `burp://findings/{domain}` | Aliased de `burp://intel/{domain}/findings` |

## Cobertura

KB en `mcp-server/src/burpsuite_mcp/knowledge/`. Cada JSON declara contexts,
server-side matchers y opcional craft guidance. Agregar `.json` extiende coverage;
`auto_probe` lo levanta runtime. Índice por categoría en
[`mcp-server/src/burpsuite_mcp/knowledge/_INDEX.md`](mcp-server/src/burpsuite_mcp/knowledge/_INDEX.md).

| Framework | Estado |
|---|---|
| OWASP Web Top 10 (2021) | 10/10 |
| OWASP API Security Top 10 (2023) | 10/10 |
| OWASP LLM Top 10 (2025) | 9/10 (LLM09 misinformation out-of-scope) |
| OWASP Mobile Top 10 (2024) | Aplicar surface cubierta (deep-link, WebView, mobile API, payments). M5 por `mobile-dynamic-agent` Frida pinning bypass; M7 binary protections out-of-scope |
| OWASP WSTG | Cobertura completa — information gathering, config, identity, authn, authz, session, input val, error handling, crypto, business logic, client-side, API |
| PayloadsAllTheThings | Cada injection/abuse class mapeada — Zip Slip, parameter injection, GraphQL engine-specific |
| HackTricks Web | Path traversal, SSRF, SSTI, deserialization, prototype pollution, request smuggling, cache poisoning, CSPP, OAuth, SAML, WebDAV, file upload |
| HackTricks Cloud | Anonymous external surface — object storage misconfig (S3/GCS/Azure Blob/R2/B2/Spaces/OCI/MinIO), function URL (Lambda/Cloud Run/Cloud Functions/Azure/OpenFaaS), API gateway (AWS/GCP/Azure APIM/Kong/KrakenD/Tyk), Kubernetes (kubelet/kube-apiserver/etcd/dashboard/ArgoCD/Tekton/Rancher/Portainer/registries). Privesc basada en creds (Paci-class) out-of-scope por policy operador |

Perimeter appliances CVE packs cubiertos: Citrix NetScaler, F5 BIG-IP, Ivanti
Connect Secure, PAN-OS GlobalProtect, MOVEit, SonicWall SSLVPN, CrushFTP,
Exchange, Confluence, TeamCity, GeoServer, Log4Shell.

### Scope y no-goals

Valravn es un **DAST orchestrator sobre Burp** — web / API / cloud / LLM. La lane
es a propósito: tools, memory model y finding pipeline están optimizados para
eso. Out of scope por diseño:

- **Intranet / Active Directory** — no BloodHound, NetExec, impacket, Kerberos
  abuse, SMB. `probe_kerberos_spnego_auth` solo detecta (envía `WWW-Authenticate:
  Negotiate`); full GSSAPI y AD lateral no cubierto. Usar toolbox AD dedicado.
- **Thick client / native desktop binary** — Electron IPC/ASAR como KB
  referencial (`desktop_electron`) + skill; native Windows/macOS app sin
  binary-instrumentation automation.
- **Non-HTTP / binary fuzzing** — fuzz es HTTP/parameter-targeted; no
  boofuzz/AFL grammar o network protocol fuzzing.
- **Mail infra security** — DNS analysis solo anota SPF/DMARC *existencia*; sin
  DKIM selector enum, SMTP/STARTTLS/open-relay, BEC/spoofing.
- **Container runtime / eBPF detection** — image scanning (Trivy/Grype/Hadolint)
  cubierto; runtime (Falco-class) no.
- **Autonomous exploitation destructiva/RCE** — RCE es detection gate; Metasploit
  integration es operator-supervised. Valravn usa benign markers para impact,
  nunca data destruction (Rule 5–8).

Para esos casos,_tool dedicado al lado_ de Valravn — no esperar que Valravn los
absorba.

## Save-Finding pipeline

Tres fases forzadas por gate:

1. **Replay.** `resend_with_modification(index)` confirma la anomalía y registra
   el `logger_index`.
2. **Assess.** `assess_finding(...)` corre el 7-Q gate (scope, reproducibilidad,
   impacto, dedup, evidence, NEVER-SUBMIT, triager) → `REPORT` /
   `NEEDS MORE EVIDENCE` / `DO NOT REPORT` + confidence sugerida.
3. **Save.** Si el gate pasa, `save_finding(...)` persiste. La extensión Java
   rechaza findings sin evidence parseable, sin `chain_with[]` NEVER-SUBMIT, sin
   `reproductions[]` con timing/blind.

Override operator: `overrides=["q5_evidence:<reason>", ...]` (audit log),
`human_verified=True`, o `set_program_policy`. Ver `.claude/skills/user-override.md`.

## Skills

`.claude/skills/` contiene behavioral skills:

- `hunt.md` — workflow de caza sistemático
- `verify-finding.md` — evidence thresholds por clase + 7-Q gate
- `resume.md` — continuar sesión previa, re-verificar findings
- `chain-findings.md` — low findings a chain con impact reportable
- `report-templates.md` — format por plataforma
- `autopilot.md` — autonomous hunt loop con circuit breaker
- `dispatch-agents.md` — parallel agent orchestration
- `burp-workflow.md`, `investigate.md`, `craft-payload.md`,
  `static-dynamic-analysis.md`
- `user-override.md` — operator override surface cuando defaults bloquean
- `operational-discipline.md` — disciplina cross-role (pentester / BBH / red
  team / researcher)
- `security-research.md` — deep-dive via `research_attack_vector` + WebFetch

`.claude/rules/` permanentes:

- `engineering.md` — 4 rules (think first, simplicity, surgical, goal-driven)
- `hunting.md` — 32 rules en tiers HARD (1–10) / DEFAULT (11–21) / ADVISORY
  (22–32)

## Agents

`.claude/agents/` define sub-agents cargados autoal inicio por Claude Code.
Orquestador + specialists:

- `grow-agent` — orquestador por sesión, un dominio activo por vez
- `recon-agent` — attack surface mapping
- `js-analyst` — JS secrets y DOM source→sink flows
- `vuln-scanner` — proba por clase, una instance por clase
- `finding-verifier` — re-verificación con evidence thresholds
- `payload-crafter` — WAF/filter bypass
- `auth-tester` — authz matrix, IDOR/BFLA, JWT
- `browser-agent` — SPA y JS-heavy targets
- `auth-payment-agent` — OAuth, FIDO2/passkey, Apple/Google/Samsung Pay, IAP, 3DS
- `fuzz-agent` — tech-aware wordlist generation y ffuf
- `mobile-dynamic-agent` — Frida y adb (pinning bypass, runtime hook, deep-link sinks)

Roles y modos paralelos en [AGENTS.md](AGENTS.md).

## Plataformas

- Linux
- macOS
- Windows (en `.mcp.json` usar `.venv\Scripts\python.exe`)
- WSL (Burp en host Windows — mirrored preferido; NAT fallback)

La extensión Java y el server Python usan libs platform-agnostic.

## Diagnóstico

Cuando algo fallout — ver [`valravn-diagnostic` skill](../.opencode/skills/valravn-diagnostic/SKILL.md)
en el root del repo Munin. Cubre failure modes frecuentes, API keys gratuitas/
free tier y fixes.

## Versionado

`v1.0.0` — `valravn-mcp` (server + `burpsuite_mcp` paquete Python), `valravn-burp-ext`
(extensión Java). El cambio de nombre del paquete Python a `valravn_mcp` está
planificado para v1.1 — el import path `burpsuite_mcp.*` sigue siendo el binding
contract este release.

La version sigue SemVer. Patch: bug fixes. Minor: backwards-compatible tools.
Major: breaking schema o tool signature changes.

## Contribuir

Issues y pull requests bienvenidos. Por favor:

- Abrí issue antes de PRs no triviales.
- Antes de commitear corré la suite Python completa
  (`cd mcp-server && uv run python -m unittest discover -s tests -v`) y
  `cd burp-extension && mvn package`. CI es el environment autoritativo.
- Match existing style (Java: camelCase methods, snake_case JSON keys; Python:
  PEP 8, async tools).
- No agregar dependencias Java externas — la extensión usa solo Montoya API y JDK.

**Trailer conventions**: `--trailer "Reported-by:<name>"` para bugs/features por
nombre; `--trailer "Github-Issue:#<number>"` para issues. Nunca `co-authored-by`
ni referencias a AI tools en commits/PRs.

## License

[Apache License 2.0](LICENSE) — incluye `NOTICE` con attribution upstream
requerido por Apache-2.0 §4(d).

Proyecto integra Burp Suite (producto de PortSwigger Ltd). Burp Suite es marca
registrada de PortSwigger Ltd. No afiliado ni endorsado por PortSwigger.

Usa el Model Context Protocol (MCP), desarrollado por Anthropic.
