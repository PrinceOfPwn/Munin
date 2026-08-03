---
name: valravn-diagnostic
description: "Diagnosticar y reparar failures de la mesh Valravn (CTI pasivo + Burp DAST activo). Cómo conseguir API keys gratuitas / free tier para los providers de inteligencia y cómo fixear Burp extension unreachable, puerto 8111 ocupado, Java 21 faltante, uv no instalado, Collaborator en Community, Intruder rate-limiting, CloakBrowser y hooks OOB. Úsala cuando valravn_* devuelva falling sources, cuando burp_* devuelva extension_unreachable, antes de una sesión de DAST o para auditar configuración de providers."
tags: [valravn, diagnostic, troubleshooting, api-keys, free-tier, burp-suite, cloAKbrowser, projectdiscovery, osint, dast, resilience, fix, runbook]
---

# Valravn Diagnostic

Runbook para diagnosticar y reparar la mesh Valravn (recon pasivo + DAST activo).
Ambos surface viven en Munin: CTI en `munin/valravn/` (Python, pasivo), DAST en
`valravn/` (Burp extension + MCP server) con puente HTTP via `munin/mcp/tools/burp_tool.py`.

Cuando algo falle, este doc te dice por qué y cómo fixearlo. **Test en CI
únicamente** — no instalamos Burp/Java/CloakBrowser localmente.

## Tabla fallback rápido

| Síntoma | Causa probable | Sección |
|---|---|---|
| `burp_status` o `burp_invoke` returns `code=extension_unreachable` | Burp no corre o extension no loaded | [Burp unreachable](#1-burp-extension-unreachable) |
| Puerto `8111` ocupado | Otra Burp/proceso lo tiene | [Puerto 8111 ocupado](#2-puerto-8111-ocupado) |
| `code=client_exception` con `*Timeout*` | Burp muy lento o target colgado | [Timeout Burp](#3-timeout-burp) |
| `code=client_exception` con `*Connect*` (no `unreachable`) | DNS/proxy loopback | [Connect (no unreachable)](#4-connect-errores-no-unreachable) |
| Java se queja de versión | Sin Java 21+ | [Java 21 faltante](#5-java-21-faltante) |
| `uv: command not found` | Sin uv | [uv no instalado](#6-uv-no-instalado) |
| `scan_url` falla | Sin Burp Pro | [Scanner/Collaborator Community fallback](#7-scannercollaborator-community-fallback) |
| `concurrent_requests` lento | Intruder throttled Community | [Intruder rate-limiting](#8-intruder-rate-limiting-community) |
| `cloakbrowser not installed` | Sin CloakBrowser o license | [CloakBrowser](#9-cloakbrowser) |
| `valravn_search_darkweb` empty | Tor no encouraged; provider offline | [Darkweb provider](#10-darkweb-provider) |
| `valravn_investigate_ioc` returns few sources | Falta API keys | [API keys free tier](#api-keys-gratuitas--free-tier) |
| `*.json` KB no encuentra matches | Stale KB | [KB stale](#11-kb-stale) |
| `assess_finding` válido pero `save_finding` rechaza | Ancla `chain_with[]` inválido | [Save-finding pipeline](#12-save-finding-pipeline) |

---

## 1. Burp extension unreachable

**Síntoma**: `burp_status` o `burp_invoke` returns
`{"ok": False, "error": {"code": "extension_unreachable"}}`.

**Causas y fixes**:

1. **Burp Suite no está corriendo.**
   - Abrir Burp. Si es primera vez, completar el wizard de Community accept.
2. **La extensión Valravn no está cargada.**
   - En Burp: `Extensions` → `Add` → `Java` → seleccionar
     `valravn/burp-extension/target/valravn-burp-ext-1.0.0.jar`. Si el JAR no
     existe, ver [Build de la extensión](#build-de-la-extensión).
3. **Puerto default cambiado.**
   - La extension escucha `127.0.0.1:8111`. Si cambiaste el host/puerto en la
     tab de Valravn, propagar al proceso Munin con `BURP_API_HOST`/`BURP_API_PORT`.
   - Ver variable de entorno abajo.
4. **WSL con Burp en Windows host.**
   - Mirrored networking: `127.0.0.1` (default) funciona. Si Windows 11 22H2+:
     activar `[wsl2] networkingMode=mirrored` en `%UserProfile%\.wslconfig` y
     `wsl --shutdown`.
   - NAT: `BURP_API_HOST=<windows host IP>` (default route gateway) y la
     extension tab `Host = 0.0.0.0` con JVM flag `-Dvalravn.allow_non_loopback_bind=true`.

**Verificación**: `burp_health_check()` debe devolver `{"ok": True, "data": {"healthy": True}}`.
Alternativamente desde host Linux: `curl -sS http://127.0.0.1:8111/api/health`.

**Variable setting reminder**:

```sh
export BURP_API_HOST=127.0.0.1
export BURP_API_PORT=8111
export BURP_API_TIMEOUT=30  # opcional; default 30
export BURP_MAX_RESPONSE_SIZE=50000  # opcional; default 50k chars
```

## 2. Puerto 8111 ocupado

**Síntoma**: Burp loggea `java.net.BindException: Address already in use` al
cargar la extension, o `burp_status` devuelve `extension_unreachable` aunque
Burp esté corriendo.

**Diagnóstico**:

```sh
# Linux/macOS
lsof -i :8111
ss -tlnp | grep 8111
# Windows
netstat -ano | findstr :8111
```

**Fix**:

- Matar el proceso que lo tiene (otro Burp, JVM orphan, etc.).
- O cambiar puerto en la tab de Valravn y propagar `BURP_API_PORT`.

## 3. Timeout Burp

**Síntoma**: `code=client_exception` con `hint` mencionando "didn't respond
within 30s".

**Causas**:

- Target remoto colgado; la extension Java está esperando socket read.
- Sobrecarga del 24-thread pool del extension.
- Un probe de `test_*` ejecutando una serie multi-stage muy larga
  (p. ej. `test_ssti` polyglot → engine diff → payload engine-specific).

**Fix**:

```sh
export BURP_API_TIMEOUT=60  # proxima invocacion
export BURP_API_TIMEOUT=120 # si la cosa sigue colgada
```

Tu timeout harus ser > que el HTTP timeout del target. Si el target responde
en 30s y tu probe hace 3 roundtrips, `BURP_API_TIMEOUT=120` queda seguro.

## 4. Connect errores (no unreachable)

**Síntoma**: `code=client_exception` con `cls=ConnectError` pero reachability
health-check pasa.

**Causas y fixes**:

- **Proxy loopback.** Tu `.mcp.json` o shell tiene `HTTP_PROXY`/`HTTPS_PROXY`
  apuntando a algo que no es Burp (`127.0.0.1:8080`). Para los calls del
  wrapper al extension (8111), asegúrate de que los proxies ignoren `127.0.0.1`:
  ```sh
  export NO_PROXY=127.0.0.1,localhost
  ```
- **IPv6 vs IPv4.** `127.0.0.1` es IPv4. Si el extension solo listen en IPv6
  `::1`, usar `BURP_API_HOST=::1` (raro en la práctica).
- **Firewall local.** Windows Defender / ufw drop loopback. Permitir
  `127.0.0.1:8111` in/outbound.

## 5. Java 21 faltante

**Síntoma**: la extension no carga; Burp lanza
`Unsupported class file major version` u `UnsupportedClassVersionError`.

**Check**:

```sh
java -version
# debe mostrar 21.x o superior
```

**Install free**:
- Linux/macOS: [Adoptium Temurin 21](https://adoptium.net/temurin/releases/?version=21)
- Windows: [adoptium.net download](https://adoptium.net/temurin/releases/?version=21)
- Package managers: `apt install temurin-21-jdk` (con
  [Adoptium APT repo](https://adoptium.net/installation/linux.html)),
  `brew install --cask temurin@21`, `scoop install temurin21-jdk`,
  `choco install temurin21`.

Después de instalar, reiniciar Burp y recargar la extension.

## 6. uv no instalado

**Síntoma**: `uv: command not found` al correr el MCP server Burp o el
`./setup.sh`.

**Install**:

```sh
# Linux/macOS
curl -LsSf https://astral.sh/uv/install.sh | sh
# Windows PowerShell
irm https://astral.sh/uv/install.ps1 | iex
# O via pip
pip install uv
```

`uv` es gratis y open source (Astro). Sin login, sin API key.

**Verify**: `uv --version`.

## 7. Scanner/Collaborator Community fallback

**Síntoma**: tools `scan_url`, `crawl_target`, `get_scan_status`,
`cancel_scan`, `get_scanner_findings`, `get_new_findings`, `get_issues_dashboard`,
`generate_collaborator_payload`, `auto_collaborator_test`,
`get_collaborator_interactions`, `collaborator_pool_status` fallan con "Pro
required" o errores 412 del extension.

**Causa**: son Pro-only; Community no los tiene.

**Alternativas Community** (no requieren Pro license):

- **Scanner activo** → `auto_probe` (KB-driven, runs via HTTP API del extension
  sin usar el scanner pipeline de Burp), `fuzz_parameter`, `fuzz_with_feedback`,
  `test_*` nativos (`test_csrf`, `test_ssrf`, `test_ssti`, `test_xxe`,
  `test_websocket`, `test_prototype_pollution`, `test_login_bypass`,
  `test_mfa_bypass`).
- **Collaborator OOB** → operator-suministra callback URL:
  - [interact.sh](https://interact.sh) — ProjectDiscovery, free
  - [webhook.site](https://webhook.site) — free, sin signup
  - [requestcatcher.com](https://requestcatcher.com) — free, sin signup
  - Tu dominio propio con DNS hostname wildcard para prod Foolproof.
  Pasar la callback URL explicita en el payload. **Rule 9a prohibe inventar
  dominios** — usar uno de estos concretos o el tuyo propio.
- **Intruder full speed** → `concurrent_requests` (concurrency Python-side via
  Burp proxy, no rate-limited).

**Verificación Pro availability**: llamar `check_pro_features()` al inicio de
sesión — devuelve `{pro_features: [...]}`.

## 8. Intruder rate-limiting Community

**Síntoma**: `send_to_intruder_configured` lento o throttled (Community cap
~1 req/s after ~200 reqs/min).

**Fix**: usar `concurrent_requests(url, count, method, headers=...)` en lugar
de Intruder. Bypass Community throttling via Python-side concurrency en Burp
proxy (no Intruder internal loop).

```python
# Ejemplo via burp_invoke
burp_invoke(
    endpoint="/api/http/concurrent",
    method="POST",
    json_body={
        "method": "GET",
        "url": "https://target.example/path",
        "count": 50,
        "headers": {"X-Custom": "value"},
        "match_codes": [200, 302, 403]
    }
)
```

`match_codes` filter en server-side reduce noise.

## 9. CloakBrowser

**Síntoma**: `browser_crawl` / `browser_navigate` /
`browser_capture_web_evidence` fallan con `"error": "CloakBrowser not installed",
"hint": "Run: uv pip install cloakbrowser"`.

**Causa**: CloakBrowser es Chromium patched (stealth fingerprints al nivel
binario, no JS shim). Requiere install.

**Install**:

```sh
uv pip install cloakbrowser
# O con CDN download incluido:
uv pip install "cloakbrowser>=0.3.28"
```

CloakBrowser descarga un Chromium patched de ~200MB en el primer run (warm-up).
Se queda cacheado en `~/.cache/cloakbrowser/` (Linux/macOS) o
`%LOCALAPPDATA%\cloakbrowser\` (Windows).

**License**: CloakBrowser es OSS pero requiere una **license key** para
algunos fingerprints features premium. La license se setea con
`CLOAKBROWSER_LICENSE_KEY` env var. Sin license las features stealth funcionan
para los fingerprint básicos; las avanzadas degradan a Chromium vanilla. No es
un blocker para tooling cotidiano.

**Sin license**: los tools `browser_*` todavía funcionan — solo pierden algunas
features anti-fingerprinting premium. Para CTF / pentest con un target con WAF
fingerprint-aware, obtener license es worth it.

License calendario: [cloakhq.com](https://github.com/CloakHQ/CloakBrowser) para
info actual.

## 10. Darkweb provider

**Síntoma**: `valravn_search_darkweb` devuelve `{"count": 0, "onions": []}` y
`sources: {onion_pet: failed, ahmia: failed}`.

**Causa**: `*.onion.pet` (gateway read-only a `.onion` sin Tor) y
[ahmia.fi](https://ahmia.fi) son los providers default; caen a menudo.

**Fix**: es expected behavior — no rompe otras tools. Retry después. No
instalar Tor localmente; **Rule**: `*.onion.pet` NO es anonimato Tor, es
gateway read-only. Para deep darkweb research, correrán desde un Tails/Whonix
físico — **fuera de Munin host** (opsec rule global).

## 11. KB stale

**Síntoma**: `auto_probe` no encuentra matchers para una vuln class que sabes
que existe, o `get_payloads` no devuelve payloads para esa categoría.

**Causa**: KB JSON bajo `valravn/mcp-server/src/burpsuite_mcp/knowledge/` stale.

**Diagnosis rápido**:

```python
# En el MCP server Burp runtime (via burp_invoke / resources)
burp_invoke(endpoint="/api/kb/index", method="GET")
# Mira el count por category y la freshness
```

**Refresh**: el KB evolving vial upstream. Si encontrás un class nueva,
agregar JSON a `knowledge/` con la designación de `contexts`/`matchers`. Ver
`valravn/CLAUDE.md` "Agregar features nuevas → Nuevos KB probes".

CI valida JSON-well-formedness en `valravn/mcp-server/src/burpsuite_mcp/knowledge/`
en el job `valravn-burp-import` (ver sección CI abajo).

## 12. Save-finding pipeline

**Síntoma**: `assess_finding` passes (devuelve REPORT), pero `save_finding`
falla con `chain_with_invalid` o `evidence_endpoint_mismatch`.

**Fix pipeline**:

1. `assess_finding` gana gates de validity per-question Q1–Q7.
2. `save_finding` chequea estructuralmente:
   - Cada `chain_with[]` ancla a un finding `confirmed` (no
     `likely_false_positive` / `stale`).
   - Cada `evidence.logger_index` / `proxy_history_index` /
     `reproductions[].logger_index` resuelve a un request con host+path
     que matchee el finding `endpoint`. Indices que apuntan a unrelated traffic
     fallan con `evidence_endpoint_mismatch`.
3. Re-runnar `resend_with_modification(index)` para capturar el logger_index
   correcto, o ajustar `endpoint` del finding para que matchee lo que se capturó.

**Tip**: `evidence_endpoint_mismatch` es la causa #1 de "Burp annotation
errors, writeup errors, report quote errors" — siempre write-then-read-back,
nunca citar request text directo.

---

## API keys gratuitas / free tier

La capa CTI (`valravn_investigate_*`) está mejorada por API keys externas.
Todas tienen free tier suficiente para triage e investigation shal­low.
Configurarlas en `.env` (gitignored, nunca commitear).

### Recon & intel (capa CTI)

| Provider | Free tier | Cómo conseguir | Env vars Munin |
|---|---|---|---|
| Shodan | 1 query/mes, 100 results/query, account free | <https://www.shodan.io/register> | `SHODAN_API_KEY` |
| Censys | 250 queries/mes, free personal account | <https://search.censys.io/register> | `CENSYS_API_ID`, `CENSYS_API_SECRET` |
| VirusTotal | 4 queries/min, free account | <https://www.virustotal.com/gui/my-apikey> (registrar y pedir API key) | `VT_API_KEY` |
| urlscan.io | 1000 scans/day, free public account | <https://urlscan.io/user/profile> | `URLSCAN_API_KEY` |
| Netlas | 50 queries/mes, free account | <https://netlas.io/register> | `NETLAS_API_KEY` |
| LeakIX | Free, registration no mandatory pero recomienda | <https://leakix.net/register> | `LEAKIX_API_KEY` |
| ZoomEye | Free personal with email signup, monthly quota | <https://www.zoomeye.org/register> | `ZOOMEYE_API_KEY` |
| NVD (NIST NVD) | **Free, sin key. Key recomendada para rate-limit 50 req/min** | <https://nvd.nist.gov/developers/request-an-api-key> | `NVD_API_KEY` |
| Cloudflare Radar | Free tier, register Cloudflare account | <https://developers.cloudflare.com/radar/> then <https://dash.cloudflare.com/profile/api-tokens> | `CLOUDFLARE_RADAR_TOKEN` |
| crt.sh | **No requiere key, libre** | <https://crt.sh> | — |
| HaveIBeenPwned | Free API for breach-domain lookup, anon always free | <https://haveibeenpwned.com/API/Key> | — (usado por `valravn_investigate_organization`, no key required domain breach endpoint) |
| Wayback Machine / Common Crawl | **No requiere key, libre** | <https://web.archive.org/> / <https://commoncrawl.org/> | — |
| RIPEstat | **No requiere key, libre** | <https://stat.ripe.net/> | — |

### DAST & Burp adjuncts

| Tool / Service | Free? | Cómo conseguir | Notas |
|---|---|---|---|
| Burp Suite Community | **Free forever** | <https://portswigger.net/burp/communitydownload> | Sin scanner/collaborator. Suficiente para la mayoría del wrapper `burp_*` |
| Burp Suite Professional | Trial 30 días (la trial license disabled scanner) | <https://portswigger.net/burp/pro/trial> | Solo prueba. Para el día a día, Community + los wrappers typed de Munin |
| Java 21+ (Temurin) | Free, OSS | <https://adoptium.net/temurin/releases/?version=21> | Required para la extension |
| uv | Free, OSS | <https://astral.sh/uv/> | Required para el MCP server Burp |
| CloakBrowser | OSS + license (algunos features premium) | <https://github.com/CloakHQ/CloakBrowser> | Stealth Chromium. Sin license funciona 基本: pierde features fingeprint premium |
| subfinder | Free, OSS | `go install -v github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest` (requires Go) | DNS recon. Las binaries separadas quizá fallen via el MCP server Burp si Go no esta en PATH |
| nuclei | Free, OSS | `go install -v github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest` | Vulnerability scanner |
| katana | Free, OSS | `go install -v github.com/projectdiscovery/katana/cmd/katana@latest` | Crawler |
| httpx (PD) | Free, OSS | `go install -v github.com/projectdiscovery/httpx/cmd/httpx@latest` | Prober |
| Chaos API (PD) | Free, requiere signup | <https://cloud.projectdiscovery.io/> | Subdomains DB; export `PDCP_API_KEY` |
| interact.sh | Free, OSS | <https://interact.sh> o self-host | OOB callback |
| webhook.site | Free, no signup | <https://webhook.site> | OOB callback, temporal URL |
| requestcatcher.com | Free, no signup | <https://requestcatcher.com> | OOB callback con subdomain custom |
| HackTricks | **No requiere key, free** | <https://book.hacktricks.xyz/> | KB usado por el MCP server Burp |
| PayloadsAllTheThings | **No requiere key, free** | <https://github.com/swisskyrepo/PayloadsAllTheThings> | KB payloads reference |
| SecLists | **No requiere key, free** | <https://github.com/danielmiessler/SecLists> | Wordlists; detectado runtime por `check_recon_tools`, cached en `.valravn-intel/_seclists_path.json` |

### Recommendations para empezar

1. **Burp Community install**, Java 21, uv — table-stakes.
2. **Shodan + Censys + VT + urlscan.io** — cover el 80% de `valravn_investigate_ioc` y `valravn_search_assets` con estos 4 free.
3. **NVD API key** (gratis, mejora rate-limit del CVE lookups mucho).
4. **ProjectDiscovery tools + interact.sh** — para DAST scans; pre-instalado por `./setup.sh`.
5. **CloakBrowser sin license** — wen para arrancar; compra license si te topás con fingerprint-aware WAFs.

### Setting reminder — nivel shell

```sh
# .env o shell
export SHODAN_API_KEY=...
export CENSYS_API_ID=...
export CENSYS_API_SECRET=...
export VT_API_KEY=...
export URLSCAN_API_KEY=...
export NETLAS_API_KEY=...
export LEAKIX_API_KEY=...
export ZOOMEYE_API_KEY=...
export NVD_API_KEY=...
export CLOUDFLARE_RADAR_TOKEN=...
# DAST adjuncts
export BURP_API_HOST=127.0.0.1
export BURP_API_PORT=8111
export BURP_API_TIMEOUT=30
export CLOAKBROWSER_LICENSE_KEY=...  # optional
export PDCP_API_KEY=...  # optional
```

## Build de la extensión

```sh
cd valravn
./build.sh           # builds JAR; prints absolute path
# O manual: cd burp-extension && mvn package
# Output: valravn/burp-extension/target/valravn-burp-ext-1.0.0.jar
# Cargarlo en Burp: Extensions -> Add -> Java
```

Si `mvn` falla con JAVA_HOME incorrecto:

```sh
export JAVA_HOME=$(dirname $(dirname $(readlink -f $(which javac))))
# o apuntar directamente a un Java 21 JDK
```

## Setup automation (no ejecutar localmente💼)

```sh
# Linux/macOS
./setup.sh
# Windows PowerShell
./setup.ps1
# Windows double click
./setup.bat
# Diagnóstico post-install
./doctor.sh   # color-coded OK/WARN/FAIL, exit code non-zero solo si critical missing
```

**Recordatorio host**: el host local NO instala Burp/Java/CloakBrowser/nuclei —
el runner de CI lo hace. Local solo se codea y se commitea. El workflow
`valravn-smoke.yml` valida todo en el runner.

## CI diagnostic (autoritativo)

El workflow `.github/workflows/valravn-smoke.yml` corre dos jobs relevantes
a la mesh:

- **`valravn` (existente)** — `compileall munin/valravn`, protocol smoke,
  catalog smoke (sin external probes), `tests/test_valravn_integration.py`,
  Munin tests completos. Pro optional: `probe_external_apis=true` workflow_dispatch
  efectúa live probes contra los providers externos si las keys están en secrets.
- **`valravn-burp-import` (nuevo, agrega este PR)** — `compileall` del nuevo
  `valravn/mcp-server/src/burpsuite_mcp`, syntactic + AST check de los KB
  JSON, smoke tests del `munin/mcp/tools/burp_tool.py` con extension
  unreachable expectado (resilience check).

Correćiones o dudas del output del workflow: skill `munin-cicd` para leer
workflow run logs y trigger `workflow_dispatch` desde el terminal.

## Secretos no commiteados

Recordar: API keys van a `.env` (gitignored) y para `probe_external_apis=true`
workflow_dispatch se cargan via **repo Secrets** (no .env del commit). Para
siguientes rules ver `~/.claude/CLAUDE.md` global secrets discipline.

## Referencias rápidas

- README: [`valravn/README.md`](../../valravn/README.md)
- AGENTS: [`valravn/AGENTS.md`](../../valravn/AGENTS.md)
- CLAUDE: [`valravn/CLAUDE.md`](../../valravn/CLAUDE.md)
- Wrapper Munin: [`munin/mcp/tools/burp_tool.py`](../../munin/mcp/tools/burp_tool.py)
- Capabilities catalog: [`munin/mcp/capabilities.py`](../../munin/mcp/capabilities.py)
- Soul: [`soul/valravn.md`](../../soul/valravn.md)
- Workflow: [`.github/workflows/valravn-smoke.yml`](../../.github/workflows/valravn-smoke.yml)
- Valravn CTI gateway: [`munin/valravn/`](../../munin/valravn/)
