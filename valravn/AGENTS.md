# Configuración del agent team

La mesh Valravn despacha agents especializados en paralelo para penetration
work. El orchestrator (conversación principal) spawnea agents para workflows
independientes, mergea resultados y toma decisiones estratégicas.

## Archivos de definición

Cada rol mapea a un archivo `.claude/agents/<role>.md`. El `Agent` tool de
Claude Code los carga por nombre. Cambiar un rol implica actualizar este
overview (rol delta) y el archivo `.md` (operación detail).

Los orchestrator roles fueron separados: `grow-agent` es el orchestrator per
sesión (ver `docs/specs/2026-05-22-grow-agent-design.md`). Cuando se invoca,
`grow-agent` despacha los 9 roles de worker de abajo.

## Command layer (encima de grow-agent)

Dos engagement leads sobre `grow-agent`. Las leads own strategy — research,
planes escritos, multi-domain dispatch, cross-target synthesis, delivery —
nunca corren per-domain loops. Ambos usan el SOP compartido
`.claude/skills/command-engagement.md`; cada agent file solo lleva el role
delta. Despachados on-demand.

```
{pentest|redteam}-commander        engagement lead — research, plan, synthesis, report
  └─ grow-agent(domain)   × N      per-domain executor (límite: 2–3 in-flight)
       └─ 10 workers
```

### pentest-commander
**Propósito:** engagement lead coverage-driven. Cobertura WSTG/OWASP completa,
cada finding verificado + reportado.
**Cuándo despachar:** multi-endpoint / multi-domain pentest donde breadth +
findings report es el deliverable.
**Dispatch:** `Agent(subagent_type="pentest-commander", prompt="domains=[...], objective=..., session_name=...")`.
**Éxito:** coverage matrix completa (o negativa documentada), todos los findings
confirmados + writeup, reporte en `reports/`.

### redteam-commander
**Propósito:** engagement lead goal-driven. Kill chain más corta al objetivo
declarado con budget de stealth/noise acotado.
**Cuándo despachar:** red-team objective-driven (alcanzar data/access/flag) en
vez de coverage scan.
**Dispatch:** `Agent(subagent_type="redteam-commander", prompt="objective=..., domains=[...], noise_budget=low|moderate|high")`.
**Éxito:** objetivo alcanzado + kill chain documentada, o cadena de avance más
lejos + blockers.

**Anti-recursión (HARD):** commander nunca despacha commander. Commander despacha
`grow-agent` (que nunca despacha `grow-agent`) + specialists. Una sola command
layer.

## Roles de agent

### recon-agent
**Propósito:** mapear attack surface del target en paralelo con otros análisis.
**Cuándo despachar:** inicio de cualquier engagement nuevo, o cuando el segmento
de endpoints esté stale.
**Tools:** `discover_attack_surface`, `discover_common_files`, `full_recon`,
`detect_tech_stack`, `get_unique_endpoints`, `discover_hidden_parameters`
**Devuelve:** lista de endpoints con risk score, tech stack, sensitive files
hallados, hidden parameters.

### js-analyst
**Propósito:** deep JavaScript analysis — secrets, DOM sinks, API endpoints.
**Cuándo despachar:** post-recon identificación de JS files, o en paralelo con
recon.
**Tools:** `fetch_page_resources`, `extract_js_secrets`, `analyze_dom`,
`extract_api_endpoints`, `fetch_resource`
**Devuelve:** secrets hallados (con severity), DOM XSS sink→source flows,
hidden API endpoints.

### vuln-scanner
**Propósito:** test de vulnerability classes específicas sobre endpoints dados.
**Cuándo despachar:** post-recon, un agent por clase, targets no superpuestos.
**Tools:** `auto_probe`, `bulk_test`, `probe_endpoint`, `fuzz_parameter`,
`test_lfi`, `test_file_upload`, `test_cors`, `test_graphql`,
`test_cloud_metadata`, `test_open_redirect`, `test_jwt`, `get_payloads`
**Devuelve:** findings scoreados, params probadas, _outliers_ pendientes de
investigar.
**Importante:** cada vuln-scanner agent recibe un grupo target o clase
diferente — evitar requests duplicados.

### finding-verifier
**Propósito:** re-verificar findings confirmados, investigar _outliers_.
**Cuándo despachar:** sesiones de resume con findings stale, o post-scan cuando
surgen _outliers_.
**Tools:** `session_request`, `compare_auth_states`, `auto_collaborator_test`,
`get_collaborator_interactions`, `compare_responses`, `save_target_intel`
**Devuelve:** status de findings actualizado (confirmed/stale/likely_false_positive)
con evidence.

### payload-crafter
**Propósito:** construir bypass payloads cuando estándares son bloqueados por
WAF/filter.
**Cuándo despachar:** vuln-scanner reporta que todos los payloads fueron
bloqueados en un param que parece injectable.
**Tools:** `fuzz_parameter`, `get_payloads`, `decode_encode`, `session_request`,
`probe_endpoint`, `save_target_notes`
**Devuelve:** bypass payload utilizable con filter map, o "filter too strong" con
evidence.

### auth-tester
**Propósito:** test de authorization y access control cross-endpoint.
**Cuándo despachar:** cuando múltiples session/auth states están disponibles
(admin + user + anon).
**Tools:** `test_auth_matrix`, `compare_auth_states`, `test_race_condition`,
`test_parameter_pollution`, `test_jwt`, `session_request`
**Devuelve:** IDOR findings, auth bypass results, race conditions.

### browser-agent
**Propósito:** SPA/JS-heavy crawling e JavaScript interaction.
**Cuándo despachar:** target con client-side rendering pesado (Angular/React/Vue),
o cuando server-side crawl pierde contenido auto-loaded.
**Tools:** `browser_navigate`, `browser_crawl`, `browser_interact_all`,
`browser_click`, `browser_fill`, `browser_execute_js`, `browser_get_page_info`
**Devuelve:** endpoints descubiertos via JS rendering, dynamic routes, XHR/API
calls en proxy history.
**Constraint:** un solo browser agent a la vez — browser instance única.

### mobile-dynamic-agent
**Propósito:** operar Frida (iOS + Android) y adb (Android solo) en el host del
operador — bypass SSL pinning / root-JB detection, hook runtime crypto + storage,
abuse Android exported components y deep links, dump iOS keychain — para
enrutar backend traffic a Burp. Solo dynamic; sin static decompile.
**Cuándo despachar:** el operador ya tiene APK/IPA en device + Frida server +
adb authorized + Burp CA en device. Triggered by `playbook-mobile-dynamic.md`.
Despachar antes de `playbook-mobile-backend.md` — este agent desbloquea traffic.
**Tools:** `Bash` para `frida -U -l <script>`, `adb shell ...`,
`objection -g <pkg>`; `get_proxy_history`, `extract_api_endpoints`,
`search_history`, `build_target_header_profile`, `save_target_intel`,
`annotate_request`
**Devuelve:** pinning bypass status, backend endpoints + headers + tokens
capturados, hooked HMAC/crypto keys, exported components, deep link param sinks,
iOS keychain items, IAP receipt structure. Handoff a `playbook-mobile-backend.md`
§3.
**Constraint:** un solo mobile-dynamic agent a la vez — un device, un app, una
Frida session. Nunca despachar sobre device ajeno. No reportar pinning/root
bypass como findings separados — son medios, no bugs.

### auth-payment-agent
**Propósito:** deep-test el attack surface de mayor valor — OAuth 2.0 / OIDC,
WebAuthn / FIDO2 / passkey, Google Pay, Apple Pay, Samsung Pay, IAP server-side
validation, 3DS 2.x bypass, SCA exemption abuse, wallet linking, recovery flow
downgrade. Bugs de $5k–$50k.
**Cuándo despachar:** Router Q7 match o pedido operatorio explícito de
"OAuth / SSO / payment / FIDO / wallet / recovery testing". Frecuentemente
co-dispatched con `mobile-dynamic-agent` cuando el payment/auth flow es
mobile-app native.
**Tools:** `session_request`, `run_flow`,
`auto_probe(categories=["oauth","oauth_device_flow","webauthn_passkey","payment_flow"])`,
`test_jwt`, `auto_collaborator_test`, `compare_auth_states`, `concurrent_requests`
(recovery-code bruteforce probe), `resend_with_modification`, `search_history`,
`extract_regex`, `assess_finding`, `save_finding`
**Devuelve:** bypasses confirmados con repro, replay-chain evidence, findings
severity-graded con PoC steps, `chain_with[]` anchors sugeridos para higher
severity report.
**Constraint:** siempre por `playbook-payment-and-auth.md` workflow — mapear el
multi-step flow antes de mutar single-step. No fuzz 1000 payloads a
`redirect_uri` cuando `auto_probe` cubre bypasses disponibles.

### fuzz-agent
**Propósito:** descubrir directorios y archivos hidden con smart, tech-aware
wordlist. SecLists slice surgical en vez de spray, alimentado por recon intel.
**Cuándo despachar:** post `detect_tech_stack` fingerprint del target, una vez
que los standard recon endpoints están mapeados. Triage tier `shallow`, main run
`medium`, solo `deep` si shallow+medium vacíos.
**Tools:** `detect_tech_stack`, `generate_smart_wordlist`, `run_ffuf` (via Burp
proxy; `match_codes=[200,204,301,307,401,403,500]`, `filter_size=<baseline>`),
`annotate_request` (color `YELLOW`, comment `hidden-path`), `send_to_organizer`,
`save_target_intel`
**Devuelve:** nuevos endpoints escritos a `.valravn-intel/<domain>/endpoints.json`,
cada hit YELLOW-annotated en proxy entries, follow-up organizer entries.
**Constraint:** nunca dos `fuzz-agent` simultáneos sobre mismo host — WAF trigger.
Max 1 concurrent `fuzz-agent` por host por sesión completa. Ver
`.claude/skills/fuzz-hidden-paths.md`.

## Dispatch rules

1. **Nunca despachar agents concurrentes contra mismo endpoint** — WAF trigger,
   rate limit, corrupted results.
2. **Todos los agents usan la misma session** para auth consistency (session es
   thread-safe en la Java extension).
3. **Orchestrator no duplica trabajo** — si despachó agent a SQLi scan, no escanea
   SQLi él mismo.
4. **Merge antes de próxima decisión estratégica** — esperar a todos los agents
   paralelos antes de decidir el próximo paso.
5. **Merge antes de guardar intel** — el orchestrator llama `save_target_intel`
   con resultados merged, no agentes individuales.
6. **Browser agent no paralelo** — solo una headless browser instance.
7. **Poblar proxy history antes de extract tools** — `browser_crawl` primero.

## Patrones paralelos

### Mode 1: Recon fan-out
Inicio de engagement despacha simultáneamente:
- recon-agent: crawl + mapeo endpoints
- js-analyst: scan JS files por secrets y DOM XSS
Ambos background. Orchestrator mergea resultados en attack priority list.

### Mode 2: Vulnerabilidad parallel
Post-recon, particionar targets por vulnerability class:
- vuln-scanner (SQLi): endpoints con params id/num/page
- vuln-scanner (XSS): endpoints con params search/comment/name
- vuln-scanner (LFI): endpoints con params file/path/include
- auth-tester: todos los auth endpoints (IDOR matrix)
Cada agent con targets no superpuestos.

### Mode 3: Verify batch
Sesión resume despacha verificación de múltiples findings simultáneos:
- finding-verifier #1: re-verificar CRITICAL findings
- finding-verifier #2: re-verificar HIGH findings
- finding-verifier #3: re-verificar MEDIUM findings

### Mode 4: Investigate + continue scan
Cuando se encuentra _outlier_:
- payload-crafter: investiga el _outlier_ (foreground, resultado necesario)
- vuln-scanner: continúa testeando próxima clase (background)

### Mode 5: Mobile engagement pipeline
Secuencial, no paralelo (cada stage depende del anterior):
1. **mobile-dynamic-agent (foreground):** bypass pinning + root/JB detection,
   hook runtime, captura endpoints. Corre `playbook-mobile-dynamic.md`. Cuando
   backend traffic fluye, stop.
2. **recon-agent + js-analyst (parallel):** enriquecer endpoints capturados,
   mapear JS bundles, encontrar hidden mobile-specific routes.
3. **vuln-scanner + auth-tester + auth-payment-agent (parallel):** against
   endpoints descubiertos con non-overlapping vuln classes. `auth-payment-agent`
   cubre OAuth/FIDO/IAP/Pay.
4. **finding-verifier:** confirmar y chain.

### Mode 6: Auth + payment scan
Para targets con SSO + payment integration (e-commerce, fintech, SaaS):
- **auth-payment-agent (foreground):** drive `playbook-payment-and-auth.md`,
  capturar OAuth + payment flows, correr KB scans.
- **auth-tester (background):** independent test IDOR/BFLA entre auth states
  descubiertas en auth flow.
- **finding-verifier (después de ambos):** chain auth findings con payment
  findings (e.g. OAuth redirect → ATO → payment-token theft → cross-account debit).

## Anti-patterns

- **No despachar agent para triviales** — single `quick_scan` call no necesita agent.
- **No despachar >4 agents concurrentes** — MCP server procesa requests secuencial,
  demasiados agentes encolan.
- **No dejar agents tomar decisiones estratégicas** — agents ejecutan, el
  orchestrator decide.
- **No skippear el merge step** — siempre colectar y analizar todos los agent
  results antes de avanzar.
- **No despachar agents para workflows secuenciales** — login flows, CSRF token
  extraction chains, `run_flow` steps deben ser secuenciales.
