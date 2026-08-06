# Munin system map

This is the current v1.0.0 map. Runtime discovery remains authoritative for
exact capabilities, schemas and enabled providers.

```mermaid
flowchart TB
  subgraph Clients
    GUI[Next.js 15 Web GUI]
    MCPClient[MCP client]
    Discord[Discord adapter]
  end
  subgraph Control[Munin control plane]
    API[FastAPI /api]
    MCP[MCP /mcp]
    Auth[Identity and authentication]
    Policy[Policy and approvals]
    Chat[Durable chat supervisor]
  end
  subgraph Runtime
    Graph[Deep Agents + LangGraph]
    Registry[Live capability registry]
    Specialists[Bounded specialists]
    Generated[Generated gen__ capabilities]
  end
  subgraph Data
    Hot[Hot SQLite store]
    Checkpoints[LangGraph checkpoints]
    Archive[Optional libSQL / Turso archive]
    Artifacts[Reports and evidence]
  end
  subgraph Knowledge
    Hugin[Hugin knowledge graph]
    Valravn[Valravn reconnaissance mesh]
  end

  GUI --> API --> Auth --> Policy --> Chat --> Graph
  MCPClient --> MCP --> Auth
  Discord --> Chat
  Graph <--> Registry
  Registry --> Specialists
  Registry --> Generated
  Graph --> Hot
  Graph --> Checkpoints
  Hot --> Archive
  Hot --> Artifacts
  Hugin --> Graph
  Valravn --> Registry
```

## Control surfaces

| Surface | Role |
| --- | --- |
| Discord | **Stable v1.0.0 operator surface** — presence, commands, threads and approvals |
| Web GUI | Target long-term interface; under repair after live-session frontend bugs |
| `/api/*` | Authenticated application and integration API |
| `/mcp/` | Live MCP discovery and invocation under server policy |

## Runtime invariants

- The server owns policy and authority.
- A disconnect does not erase or duplicate a run.
- Replay reads durable events instead of regenerating history.
- Human approval is bound to one exact action and argument set.
- Checkpoints preserve executable state; events preserve the audit story.
- Skills, Hugin and Soul provide context, not permission.
- The bundled CTF Soul is optional and not a default deployment profile.

## Verified v1.0.0 path

```mermaid
flowchart LR
  Operator --> Discord[Discord surface]
  Discord --> Actions[GitHub Actions runner]
  Actions --> Munin[Munin v1.0.0]
  Munin --> MiMo[MiMo V2.5]
```

See [docs/README.md](docs/README.md) for the complete multilingual documentation.
