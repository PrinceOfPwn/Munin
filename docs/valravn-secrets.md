# Valravn intelligence secrets

Valravn treats provider credentials as optional capability switches. Missing secrets must degrade only the affected provider; they must not prevent Munin from starting.

The names below are the environment variables consumed by the current Valravn native adapters. For local/container deployments, expose them directly as environment variables. For GitHub Actions, storing a repository secret does **not** automatically inject it into a job: the workflow must explicitly map the secret into `env`, or a Munin credential-vault layer must export it before Valravn initializes.

The Live Session bootstrap added in PR #72 is independent of these intelligence credentials: it brings Juice Shop, Burp MCP Ultimate and Talons online before Munin starts. Provider API credentials remain optional enrichment capabilities.

## Priority 1 — highest operational value

| Environment / secret name | Provider | Primary value |
|---|---|---|
| `SHODAN_API_KEY` | Shodan | Internet-exposed services, banners, ports, ASN/org pivots |
| `VT_API_KEY` | VirusTotal | IP/domain/hash reputation and malware context |
| `URLSCAN_API_KEY` | urlscan.io | Historical web scans, domains, IPs, screenshots/DOM metadata |
| `ABUSECH_AUTH_KEY` | abuse.ch | ThreatFox, URLhaus and MalwareBazaar enrichment |
| `ABUSEIPDB_API_KEY` | AbuseIPDB | IP reputation and abuse history |
| `NVD_API_KEY` | NVD | Higher-rate CVE/vulnerability lookups |

## Priority 2 — strong complementary coverage

| Environment / secret name | Provider | Primary value |
|---|---|---|
| `NETLAS_API_KEY` | Netlas | Internet asset and response search |
| `LEAKIX_API_KEY` | LeakIX | Exposed services, leaks, domains and subdomains |
| `ZOOMEYE_API_KEY` | ZoomEye | Additional Internet asset coverage |
| `OTX_API_KEY` | AlienVault OTX | IOC and pulse context; Valravn can also use limited public access |
| `CLOUDFLARE_RADAR_TOKEN` | Cloudflare Radar | Internet/routing/domain intelligence |
| `GOOGLE_TRANSLATE_API_KEY` | Google Translate | Translation for non-English web/intelligence sources |

## Optional / policy-controlled

| Secret | Notes |
|---|---|
| `FULLHUNT_API_KEY` | Used only when `VALRAVN_FULLHUNT_ENABLED=true`. |
| `GOOGLE_SAFE_BROWSING_API_KEY` | Used only when `VALRAVN_SAFE_BROWSING_ENABLED=true` and usage mode is non-commercial. |
| `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_URL_SCANNER_TOKEN` | URL scanning is opt-in through `VALRAVN_CLOUDFLARE_URL_SCAN_ENABLED=true`. |
| `CLOAKBROWSER_LICENSE_KEY` | Useful only when the browser capability is enabled. |

## Censys compatibility note

The current Valravn native adapter still targets the Censys Legacy Search API and therefore reads `CENSYS_API_ID` plus `CENSYS_API_SECRET`. Censys is transitioning users to its Platform API and Personal Access Tokens. Do not add a `CENSYS_PAT` secret until the native adapter is migrated to the Platform API; an unused secret would create a false impression that the provider is active.

## Credentials not required today

The current GreyNoise adapter uses the public Community endpoint and does not read a GreyNoise API key. RIPEstat, Wayback Machine and Common Crawl are also available without repository secrets.

SecurityTrails is not wired into the current native Valravn provider set. Adding a `SECURITYTRAILS_API_KEY` by itself therefore does nothing until an adapter/provider is implemented.

## Recommended repository variables

These are variables, not secrets, and default conservatively:

- `VALRAVN_USAGE_MODE=personal`
- `VALRAVN_URLSCAN_SUBMIT_ENABLED=false`
- `VALRAVN_CLOUDFLARE_URL_SCAN_ENABLED=false`
- `VALRAVN_FULLHUNT_ENABLED=false`
- `VALRAVN_SAFE_BROWSING_ENABLED=false`
- `VALRAVN_BROWSER_ENABLED=false`

Search/enrichment can therefore use configured providers while active submissions and heavier browser/scanner paths remain explicit opt-ins.
