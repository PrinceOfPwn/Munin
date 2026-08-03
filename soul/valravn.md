---
name: valravn
tags: [valravn, recon, intel, dast, burp-suite, osint, scanning, cti, mesh-valravn, capability-surface]
---

# Valravn intelligence & DAST mesh

Valravn es la **mesh de inteligencia y DAST** de Munin. Dos capas que
comparten scope, audit log y target intel — una passive, la otra active, ambas
subordinadas a la policy server-side y a los gates de HITL.

## Las dos capas

### Capa CTI — passive reconnaissance

Cubre los tools `valravn_*` (Python gateway en `munin/valravn/`).
Pasivos: no envían tráfico al target, sólo consultan fuentes OSINT.

- `valravn_status` — availability check (con `probe=true` live-check).
- `valravn_investigate_ioc` — IP, domain, URL, hash, email o CVE indicator.
- `valravn_investigate_organization` — ransomware, breach, infra expuesta,
  historical web evidence.
- `valravn_search_assets` — asset search. Operator owns index breadth.
- `valravn_investigate_cve` — KEV, EPSS, productos afectados, exploit refs,
  exposure context. Encontrar exploit refs es **inteligencia** — usarlos o no
  depende de la campaña, no es una capability ya adquirida.
- `valravn_investigate_network` — ASN, prefix, BGP, RPKI, outages, routing
  anomalies.
- `valravn_search_historical_web` — recuperar archivos URL, JavaScript,
  endpoints borrados.
- `valravn_investigate_url` — investigaBeforeSubmit; estrictamente passive.
- `valravn_submit_url` — URL submit activo — solo cuando el operator
  explícitamente habilita submission.
- `valravn_validate_asset` — cross-check cuando faltan corroboraciones.
- `valravn_search_darkweb` — index onion. `*.onion.pet` es gateway read-only
  — **no** es Tor anónimo.
- `valravn_capture_web_evidence` — screenshot passive + extracción acotada.
- `valravn_translate` — preservar source original y language metadata.

### Capa DAST — active Burp testing

Cubre los tools del **MCP server Burp** (`valravn/mcp-server/`, paquete
`burpsuite_mcp`) y el **wrapper HTTP Munin→Burp** (`munin/mcp/tools/burp_tool.py`).
Activos: envían tráfico al target via Burp proxy, indexan en Logger, requieren
autorización explícita del operator y pasan por el scope gate de la extensión.

En Munin se acceden de dos formas:

1. **Vía `burp_invoke(endpoint, method, json_body)`** — dispatcher genérico
   unrouted. El operator o el agente arman la llamada JSON y la dirección
   `/api/<group>/<action>` del extension; el wrapper la enruta vía HTTP al
   `127.0.0.1:8111` y devuelve un envelope estructurado. **Errres nunca tiran
   el runtime** —Burp unreachable, timeout o HTTP 5xx vuelven un dict
   `ok=False` con `code`/`error`/`hint` y el run Munin continúa.
2. **Vía los wrappers typed:**
   - `burp_status(probe=False)` — load state + reachable endpoints.
   - `burp_health_check()` — bool barato.
   - `burp_check_scope(url)` — wrapper a `POST /api/scope/check`.
   - `burp_get_proxy_count(host="")` — sub-ms read del Proxy history size.

Las tools del MCP server Burp (`scan_url`, `auto_probe`, `test_csrf`, `test_ssrf`,
`test_ssti`, `test_xxe`, `test_websocket` (`CSWSH`), `test_prototype_pollution`,
`forge_jwt`, `crack_jwt_secret`, `test_login_bypass`, `test_mfa_bypass`,
`test_session_lifecycle`, `analyze_reset_tokens`, `test_auth_matrix`,
`compare_auth_states`, `audit_crawled_artifacts`, `run_opengrep_source`,
`run_gitleaks`, `dump_exposed_git`, `ai_prompt_injection`, `mcp_server_attacks`,
`mcp_tool_poisoning`, `vector_db_injection`, `query_crtsh`, `analyze_dns`,
`run_nuclei`, `run_sqlmap`, `run_katana`, `generate_report`, `save_finding`,
`assess_finding`, `generate_collaborator_payload`, `auto_collaborator_test`,
…) están listadas en `valravn/skill.json` capabilities y expuestas vía
`burp_invoke`. **No** se wrappean uno por uno en Munin — el extension REST API
es su contrato y `burp_invoke` las dispara con la misma resilience envelope.

Burp Edition degrada elegante en Community: scanner/collaborator caen, se
sustituyen con `auto_probe` / callback del operator (`interact.sh`,
`webhook.site`, `requestcatcher.com`). Ver `valravn-diagnostic` skill para
troubleshooting y API keys free tier.

## Authorization

Capa DAST = ofensiva. Traer authorization escrita para cada target:
bug bounty en scope, contract pentest, red team ROE, internal lab, CTF.
La extension scope `valravn/burp-extension/.../ScopeHandler` es la última
palabra — el prompt no la overridea. Rules 1–4 (scope) y 5–9 (destructivo,
OOB, egress) quedan HARD independientemente del scope mode (`operator` o
`strict`).

## Investigación depth

Triage con `depth="quick"`; cuando hay restricciones de contexto, evidencia
insuficiente, conflicto o alto impacto, escalar a `depth="deep"` — consume más
free-tier providers, **máximo un scarce source** extra. No apilar múltiples
deep en una sola investigation.

## Evidencia discipline

Cada investigation preserva: **provider attribution**, **retrieval time**,
**original URL**, **first/last-seen**, **confidence**, **contradictions**,
**failed source records**.

- Distinguir **observation** (provider dice X) vs **inference** (Munin concluye
  Y). No usar un único score opaco.
- Failed sources también se registran — "provider X returned empty" es
  evidence negative informativa.
- Complementa con **Hugin** (knowledge layer — malware, low-level, evasion,
  persistence; candidate paths + node metadata) mientras Valravn cubre
  observation layer (assets, exposure, network, history).
- Critical findings guardan `node_id` / source URL / retrieval timestamp para
  que el operator pueda revisar.

## Engagement loop

Valravn se sienta en el campaign loop de `principles.md` §2 entre los
pasos 2–3:

- Recall (memory + shared intel) → **Valravn CTI** cubre observación externa →
  **Hugin** cubre specialized knowledge → hypothesis observable → minimal
  action validate → **Valravn DAST** ejecuta el probe activo via Burp → finding
  validated → `publish_shared_intel` o `memory_remember`.
- No hagas data hoarding en Valravn — sea observación CTI o probe DAST, qué
  hacer con la output es decisión de Munin.
- Findings que pasan validation de Munin AND cambian decisiones downstream van
  a `publish_shared_intel` (`principles.md` §8); enumeración común va a
  `memory_remember`.

## HITL gates

Tools activos Burp (`scan_url`, `test_*`, `forge_jwt`, `crack_jwt_secret`,
`run_sqlmap`, `dump_exposed_git`, `send_to_intruder_*`, `auto_collaborator_test`,
`browser_*` stealth, etc.) cruzan el graph interrupt del runtime Munin cuando
el policy del conversation lo requiere — generan `waiting_for_human` request.
Approve es bound a la **exact action y argument set**, no reusable para otra
tool/args/run. El wrapper `burp_invoke` es `active` en la audit trail por
defecto; los wrappers typed (status/health/scope_check/get_proxy_count) son
`passive` por lo que no parent HITL pero sí audit-logged.

## Failure modes (no-debug summary)

Burp DAST fallar == Munin runtime sigue. Errores expected:
- `code=extension_unreachable` — Burp no corre / extension no cargada / puerto
  mal. CI runners sin Burp reciben esto y degradan.
- `code=http_*` — extension responde pero con status HTTP. Java-side envelope
  preservado.
- `code=client_exception` — timeout, connect error, etc. `hint` accional.
- `code=bad_args` / `bad_method` — invocación incorrecta del wrapper.

Para diagnostic exhaustivo: skill `valravn-diagnostic`
(`.opencode/skills/valravn-diagnostic/SKILL.md`) con API keys free tier y
fixes.
