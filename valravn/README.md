# Valravn — offensive knowledge and Burp execution mesh

Valravn is Munin's offensive web-testing knowledge/workflow layer. It keeps the imported skills, agents, rules, knowledge base, payload assets, and Security Hub mappings while delegating Burp execution to mature upstream MCP providers.

## Architecture

```text
Munin agent/runtime
      |
      +--> Valravn skills + knowledge + policy
      |
      +--> Valravn Talons (small MCP gateway)
      |        |
      |        +--> burp-mcp-ultimate (primary)
      |        |        |
      |        |        +--> Montoya API --> Burp Suite
      |        |
      |        +--> optional Awesome / official providers
      |
      +--> Valravn Arsenal --> FuzzingLabs mcp-security-hub
```

The custom Valravn Java REST extension and Munin's `burp_tool.py` wrapper were retired. There is no Valravn control API on `127.0.0.1:8111` anymore.

The default primary Burp MCP endpoint is:

```text
http://127.0.0.1:9444/mcp
```

Burp's ordinary Proxy listener remains separate (normally `127.0.0.1:8080`).

## What Valravn owns

Valravn continues to own the high-value parts that are independent of a transport implementation:

- 50+ workflow/playbook skills under `.claude/skills/`;
- specialized agents under `.claude/agents/`;
- hunting and engineering rules;
- the large JSON vulnerability knowledge base;
- payload/reference assets;
- target-intel conventions under `.valravn-intel/`;
- evidence and finding-validation discipline;
- provider routing/context-economy rules;
- Valravn Arsenal mappings to FuzzingLabs `mcp-security-hub`.

The old Python MCP source tree is retained as a migration/reference corpus for knowledge, payloads, and workflow semantics, but **Munin does not use it as the Burp execution backend**.

## What Ultimate owns

`burp-mcp-ultimate` is the primary live Burp provider. The pinned CI revision exposes broad Montoya coverage through Streamable HTTP MCP, including:

- HTTP send/session/batch/race operations;
- Proxy, Sitemap, Repeater, Intruder, Scanner, Collaborator, WebSockets;
- resources such as `burp://proxy/history` and `burp://sitemap`;
- intercept controls and event surfaces;
- JWT, OAuth/OIDC, GraphQL and JavaScript utilities;
- agent-native probes;
- Montoya reflection escape hatches;
- cross-extension reflection/bridging.

Valravn does not copy those implementations. Talons discovers them lazily and exposes only a compact Munin-facing gateway.

## Munin tools

The Burp execution surface in Munin is intentionally small:

- `valravn_talons_status`
- `valravn_talons_tools`
- `valravn_talons_read`
- `valravn_talons_call`

The Arsenal surface is likewise compact:

- `valravn_arsenal_status`
- `valravn_arsenal_list`
- `valravn_arsenal_tools`
- `valravn_arsenal_call`

Generic active dispatch requires explicit authorization.

## Operator workflow

Use discovery rather than hard-coded remote schemas:

```text
1. valravn_talons_status()
2. valravn_talons_tools(provider="valravn-ultimate", query="proxy")
3. valravn_talons_tools(..., include_schema=true) for the chosen tool only
4. valravn_talons_call(..., authorized=true)
```

For passive state, prefer resources:

```text
valravn_talons_read(
  uri="burp://proxy/history",
  provider="valravn-ultimate"
)
```

See `.claude/skills/burp-mesh.md` and `.claude/skills/burp-workflow.md` for the operational contract.

## Unattended Burp startup

`valravn/scripts/start-burp-headless.sh` starts the real Burp Desktop JAR without requiring the operator to manually add an extension.

The launcher:

1. requires a built `burp-mcp-ultimate` shadow JAR via `BURP_ULTIMATE_JAR`;
2. downloads the pinned Burp Desktop JAR from PortSwigger;
3. verifies its SHA-256 and rejects non-JAR/suspiciously small downloads;
4. generates a Burp user config that loads Ultimate automatically;
5. runs Burp under the existing display, or Xvfb when no display exists;
6. negotiates a real MCP session and requires the expected Ultimate catalog before returning success.

Important environment variables:

```sh
BURP_ULTIMATE_JAR=/absolute/path/to/burp-mcp-ultimate-0.2.0.jar
BURP_HOME=.valravn-burp
BURP_MCP_HOST=127.0.0.1
BURP_MCP_PORT=9444
BURP_MCP_TOKEN=optional-local-token
BURP_MAX_HEAP=2g
```

The CI workflow builds the exact pinned Ultimate revision before invoking the launcher.

## Live Juice Shop validation

`.github/workflows/valravn-mesh-e2e.yml` contains the authoritative integration gate.

The live job:

- starts OWASP Juice Shop `v20.1.1`;
- builds/tests pinned `burp-mcp-ultimate`;
- starts Burp unattended with Ultimate preloaded;
- verifies MCP initialize + tool discovery;
- invokes `http_send_raw` through Munin/Valravn Talons against Juice Shop;
- sends a marked request through the real Burp Proxy listener;
- reads `burp://proxy/history` through Ultimate and requires the marker.

That gate prevents a mock-only integration from being mistaken for a working Burp path.

## Knowledge and skill migration

Legacy skills may mention tool names implemented by the imported Python MCP server. Treat those names as **workflow intent**, not a reason to restore the old transport.

Migration order:

1. identify the intent of the legacy call;
2. discover an Ultimate primitive through Talons;
3. use Valravn Arsenal when the operation belongs to an external security tool rather than Burp;
4. preserve the skill's evidence/validation semantics;
5. if no typed Ultimate tool exists, inspect Montoya/reflection before writing new glue;
6. only add Munin-native code when neither provider path can express the requirement cleanly.

See `MIGRATION.md` for concrete mappings.

## Burp editions

Ultimate supports Burp Community and Professional, but Burp-native features still follow edition availability.

Community is enough for the CI Juice Shop path and many HTTP/Proxy/utility operations. Scanner and Collaborator capabilities require the corresponding Burp edition/features. Missing capability must be reported explicitly; do not silently change a test's semantics to make it pass.

## Security / authorization

Valravn is an offensive-security capability. Use active tools only against explicitly authorized scope: your own lab, CTF, in-scope bug bounty, contracted pentest, or red-team engagement under agreed rules.

An MCP provider being reachable is not authorization. Munin's policy and engagement scope remain above Talons and Burp.

## Attribution

The Valravn subtree retains the Apache-2.0 attribution required by its imported source. See `NOTICE` and `LICENSE`.

Third-party providers are not vendored merely because Valravn can route to them. CI pins upstream revisions explicitly and preserves upstream identity in diagnostics/evidence.
