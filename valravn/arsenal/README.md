# Valravn Arsenal

Valravn Arsenal is Munin's compact capability gateway over external security MCP servers. It keeps the model-facing surface intentionally small while allowing operators to bootstrap and invoke specialist MCPs on demand.

## Surfaces

### Talons — Burp execution mesh

Valravn aliases the configured Burp providers while retaining their upstream identity in every status/tool result:

- `valravn-ultimate` -> `3ntr0pyX/burp-mcp-ultimate` (preferred, Streamable HTTP, default `127.0.0.1:9444/mcp`).
- `valravn-awesome` -> `vvvvvvvvvvel/burp-awesome-mcp` (context-efficient fallback, default `127.0.0.1:26001/mcp`).
- `valravn-official` -> `PortSwigger/mcp-server` through its separately installed stdio proxy.
- the existing `burp_*` tools remain the direct fallback to Valravn's bundled REST extension on port 8111.

Munin exposes only `status`, compact `tools`, `read` and generic `call`. Tool schemas are pulled lazily after selection.

### Arsenal — Security Hub fleet

`security_hub.json` maps all 38 servers from `FuzzingLabs/mcp-security-hub` into stable Valravn aliases such as:

- `recon/projectdiscovery`
- `web/nuclei`
- `web/ffuf`
- `code/semgrep`
- `secrets/gitleaks`
- `binary/radare2`
- `ad/bloodhound`

The upstream repository is not copied into this tree. `bootstrap.py` clones the pinned revision under `valravn/upstreams/` (gitignored), and the runtime either uses Docker Compose from that checkout or an explicit per-server command override.

## Bootstrap

```bash
# Security Hub, bounty-oriented image subset
python valravn/arsenal/bootstrap.py --arsenal bounty

# All 38 Security Hub images
python valravn/arsenal/bootstrap.py --arsenal all

# Clone Ultimate + Awesome; test/build Ultimate JAR
python valravn/arsenal/bootstrap.py --arsenal none --build-burp
```

## Runtime pattern

Do not inject all remote schemas into the agent context.

```text
status
  -> choose provider/category
  -> list compact tool metadata
  -> request one exact schema
  -> call one tool
  -> preserve evidence
```

Generic remote execution is classified as active and requires `authorized=true`; discovery/listing/resource reads remain passive.

## CI

`.github/workflows/valravn-mesh-e2e.yml` is the required integration gate. It validates:

1. all 38 Security Hub manifest paths against the pinned upstream commit;
2. Security Hub Docker Compose syntax;
3. Munin -> stdio MCP -> Valravn gateway -> Streamable HTTP Burp provider round trips;
4. Munin -> Valravn Arsenal -> the real FuzzingLabs Nuclei MCP over stdio;
5. explicit active-dispatch authorization gates;
6. upstream `burp-mcp-ultimate` tests and shadow-JAR build on JDK 21.

A real Burp GUI is intentionally not required in GitHub-hosted CI; Ultimate's own tests/build cover its extension code while Munin's transport E2E uses a protocol-faithful local MCP fixture.
