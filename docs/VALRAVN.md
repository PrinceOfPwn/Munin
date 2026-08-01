# Valravn Architecture

## Design

Valravn is a single native Python layer inside Munin. The model sees workflow-level tools; provider selection, credentials, budgets, concurrency, normalization and provenance remain internal.

```text
Munin
  └── Valravn Gateway
      ├── Threat intelligence
      ├── Internet asset search
      ├── Vulnerability intelligence
      ├── Routing and BGP
      ├── Historical web
      ├── URL reputation and scanning
      ├── CloakBrowser evidence
      └── Dark-web gateway references
```

The Deep Agents Tool Gateway reads FastMCP's live registry, so every `valravn_*` tool is immediately available to Munin and generated subagents without another static catalog.

## Public tools

- `valravn_status`
- `valravn_investigate_ioc`
- `valravn_investigate_organization`
- `valravn_search_assets`
- `valravn_investigate_cve`
- `valravn_investigate_network`
- `valravn_search_historical_web`
- `valravn_investigate_url`
- `valravn_validate_asset`
- `valravn_search_darkweb`
- `valravn_capture_web_evidence`
- `valravn_translate`

## Provider budgets

Providers belong to one of three economic tiers:

- `no_key`: public sources with no credentials.
- `free_key`: free or free-tier credentials.
- `scarce`: low-credit or expensive providers.

Default quick budget:

```text
no_key=3, free_key=2, scarce=0
```

Default deep budget:

```text
no_key=8, free_key=6, scarce=1
```

A source not selected appears in `skipped_sources`. A selected source that fails remains in the evidence array with `ok=false`; one provider failure never discards the other evidence.

## Source groups

### Threat and malware intelligence

OTX, ThreatFox, URLhaus, MalwareBazaar, GreyNoise Community, AbuseIPDB, ThreatMiner, ransomware.live, HIBP domain breaches and VirusTotal.

### Internet assets and external exposure

Shodan, Censys, ZoomEye, Netlas and LeakIX. Provider-specific tools are intentionally hidden behind `valravn_search_assets`, `valravn_investigate_ioc` and `valravn_validate_asset`.

### Vulnerabilities

NVD, CISA KEV, FIRST EPSS, OTX and public GitHub exploit references. Public exploit repositories are references only; Valravn never auto-executes retrieved PoCs.

### Routing and strategic context

RIPEstat supplies network information, routing status, announced prefixes, routing history and RPKI context. Cloudflare Radar optionally adds BGP anomaly and outage evidence.

### Historical web

Wayback CDX and Common Crawl run concurrently. urlscan history joins in deep mode when configured. Results include provider records and a deduplicated `unique_urls` list.

### URL investigation

ThreatFox, URLhaus, OTX and urlscan history are queried before direct navigation. New urlscan or Cloudflare URL Scanner submissions are opt-in because submitting a URL discloses it to a third party.

## Browser evidence

CloakBrowser is imported lazily and is an optional runtime (`cloakbrowser==0.5.3`). The normal locked Munin environment does not require it. A manual workflow probe installs it only for browser validation.

Each capture creates an ephemeral context with:

- no persistent profile;
- downloads disabled;
- service workers disabled;
- private/internal destinations blocked;
- PNG screenshot;
- bounded text and link extraction;
- optional translation;
- JSON evidence and SHA-256 artifact metadata.

## Dark-web references

`valravn_search_darkweb` uses Ahmia to discover indexed `.onion` references. Results preserve both representations:

```json
{
  "onion_url": "http://service.onion/path",
  "gateway_url": "https://service.onion.pet/path"
}
```

The Tor2Web gateway is a third party that may observe or alter traffic. Valravn supports passive reading and evidence capture only: no credentials, forms, uploads, downloads or persistent cookies.

## Usage restrictions

Google Safe Browsing is intended for non-commercial use. Valravn defaults to `VALRAVN_USAGE_MODE=commercial` and suppresses that source unless usage mode is `personal` or `research` and the source is explicitly enabled.

FullHunt is treated as scarce and disabled unless both its key and `VALRAVN_FULLHUNT_ENABLED=true` are present.
