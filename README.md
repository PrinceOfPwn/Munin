<p align="center">
  <img src="app/public/raven-mark.png" alt="Munin raven mark" width="168" />
</p>

<h1 align="center">Munin</h1>

<p align="center">
  <strong>A durable, operator-governed runtime for autonomous security operations.</strong>
</p>

<p align="center">
  Threat intelligence, authorised red-team work, evidence capture, human approval and long-running agent execution in one control plane.
</p>

<p align="center">
  <a href="https://www.python.org/"><img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white"></a>
  <a href="https://fastapi.tiangolo.com/"><img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-0.110%2B-009688?logo=fastapi&logoColor=white"></a>
  <a href="https://langchain-ai.github.io/langgraph/"><img alt="LangGraph" src="https://img.shields.io/badge/LangGraph-1.x-1C3C3C"></a>
  <a href="https://modelcontextprotocol.io/"><img alt="MCP" src="https://img.shields.io/badge/MCP-1.x-6F42C1"></a>
  <a href="https://nextjs.org/"><img alt="Next.js 15" src="https://img.shields.io/badge/Next.js-15-black?logo=next.js"></a>
  <a href="https://www.typescriptlang.org/"><img alt="TypeScript" src="https://img.shields.io/badge/TypeScript-5.x-3178C6?logo=typescript&logoColor=white"></a>
  <a href="https://www.sqlite.org/"><img alt="SQLite" src="https://img.shields.io/badge/SQLite-Durable_State-003B57?logo=sqlite&logoColor=white"></a>
  <a href="LICENSE"><img alt="PolyForm Noncommercial License" src="https://img.shields.io/badge/License-PolyForm_Noncommercial-orange"></a>
</p>

<p align="center">
  <a href="#quick-start"><strong>Quick start</strong></a> ·
  <a href="#architecture"><strong>Architecture</strong></a> ·
  <a href="#operation-modes"><strong>Operation modes</strong></a> ·
  <a href="#documentation"><strong>Documentation</strong></a>
</p>

> **Authorised use only.** Munin is designed for legitimate security research, threat intelligence and controlled red-team operations. You are responsible for obtaining permission, defining scope, protecting credentials, reviewing impact and complying with applicable law. A healthy server or successful tool call does not establish authorisation.

## Why Munin exists

Most agent systems are built around a temporary chat window. Security operations are not.

They last hours or days. They cross tools, models and interfaces. They require evidence, approvals, recovery, auditability and a clear record of what the agent actually did.

**Munin turns that work into a durable operation instead of a disposable conversation.**

| Disposable agent loop | Munin operation |
| --- | --- |
| Context disappears when the session ends | Stable conversations, checkpoints and replayable events |
| Tool calls are reconstructed from logs | Tool intent, output, artifacts and failures are first-class events |
| Approval is an informal prompt | Sensitive actions pause at durable graph interrupts |
| Capabilities are copied into a static prompt | The live registry composes tools and specialists at runtime |
| Reconnects risk duplicate execution | Renewable leases and persisted run state protect continuity |
| Long tasks become opaque | Operators can follow progress across web, MCP and Discord |

## What Munin gives you

### Durable autonomous operations

Runs preserve executable state through LangGraph checkpoints, while a separate event timeline records messages, evidence, tools, artifacts, approvals and operator decisions. A browser reconnects to the same operation instead of silently starting another one.

### Human control at the execution boundary

Approval is part of the runtime, not a suggestion in the prompt. Sensitive actions pause with the exact proposed capability and arguments. Approval resumes that action; rejection or expiry cannot mutate into an unrelated call.

### One runtime, multiple control surfaces

Use the web console for the full operational timeline, expose live capabilities to MCP clients, or interact remotely through Discord. Every surface reaches the same server-side policy, identity, state and approval layer.

### Live, extensible capability registry

Munin discovers native tools, reviewed skills, generated capabilities and bounded specialist profiles at runtime. Generated tools use the `gen__*` namespace and must pass validation, registration and the same policy checks as native capabilities.

### Evidence-first observability

Assistant messages, provider-emitted reasoning, tool lifecycle, streamed output, delegations, artifacts and human requests remain separate events. Munin does not invent hidden reasoning from internal state or collapse the operation into a vague status string.

## Architecture

```mermaid
flowchart LR
    Operator[Operator] --> Web[Web console]
    Operator --> Discord[Discord]
    Client[MCP client] --> MCP[/mcp/]

    Web --> API[/api/]
    Discord --> Server[Munin server]
    API --> Server
    MCP --> Server

    Server --> Policy[Identity, policy and approvals]
    Server --> Runtime[Deep Agents + LangGraph]
    Runtime --> Registry[Live capability registry]
    Runtime --> Checkpoints[Persistent checkpoints]
    Server --> Timeline[Run and event store]
    Timeline --> Replay[Replayable stream]

    Registry --> Hugin[Hugin research skill]
    Registry --> Valravn[Valravn reconnaissance mesh]
```

Munin separates four concerns that are often blurred together:

| Layer | Responsibility |
| --- | --- |
| **Knowledge** | Research context, relationships, technique references and hypotheses |
| **Authority** | Scope, identity, approvals and policy enforcement |
| **Execution** | Tools, specialist delegation, generated capabilities and agent state |
| **Evidence** | Events, artifacts, outputs, decisions and recovery history |

## Operation lifecycle

1. The operator creates or resumes a conversation and provides an objective, authorised scope and desired evidence.
2. Munin loads the stable thread, current evidence and live capability registry.
3. The runtime can answer, delegate, call a permitted tool, request approval or stop with an evidence-backed result.
4. Every meaningful transition is persisted and streamed to connected clients.
5. Completed, failed and cancelled runs remain auditable; interrupted runs may recover from checkpoints and leases.

## Operation modes

All modes use the same supervised runtime. They change the autonomy contract, not the hard security invariants.

| Mode | Best for | Approval behaviour |
| --- | --- | --- |
| **Standard** | Careful interactive operations | Per-action approvals |
| **YOLO** | Fast work inside a trusted, bounded environment | Skips routine approvals; admin and critical actions remain protected |
| **GOAL** | Persistent objectives that must survive refreshes or restarts | Durable goal, TODO state and scheduled re-evaluation |
| **BEAST** | Deep planning and specialist delegation | Expanded budgets with explicit scope and anti-runaway controls |

The `critical` approval floor, preflight validation, audit trail and token redaction remain enforced across every mode.

## Hugin and Valravn

Munin separates knowledge from authority.

[Hugin](https://github.com/PrinceOfPwn/Hugin) is its knowledge sibling: a passive graph of source-linked security research and relationships. Munin includes the reviewed `hugin-research` skill to retrieve a small, relevant and provenance-labelled subset for a bounded task.

[Valravn](munin/valravn/) is the external reconnaissance mesh. It exposes IOC and CVE enrichment, asset search through sources such as Shodan, Censys, ZoomEye, Netlas and LeakIX, historical-web pivots, routing and RPKI context, dark-web search and browser evidence capture through `valravn_*` MCP tools.

Neither research context nor tool availability grants permission to act. Scope and approval remain independent runtime controls.

## Quick start

### Requirements

- Python 3.11+
- Poetry
- Node.js and npm
- An OpenAI-compatible LLM endpoint

### 1. Configure the environment

```bash
cp .env.example .env
```

Set a strong `MUNIN_MASTER_KEY`, `MUNIN_MCP_AUTH_TOKEN`, an allowed local origin and your model provider configuration.

### 2. Start the unified server

```bash
poetry install
poetry run munin serve --host 127.0.0.1 --port 8787
```

### 3. Start the web console

```bash
cd app
npm ci
npm run dev
```

Open `http://localhost:3000`. MCP clients connect to `http://127.0.0.1:8787/mcp/` with the configured bearer token.

> Keep Munin bound to loopback unless you have configured a protected reverse proxy, explicit origin policy, authentication and persistent storage.

## Before an operational session

- Confirm `/health` and authenticated web access.
- Verify that the selected LLM provider completes a structured tool-call round trip.
- Inspect the live capability surface instead of relying on a copied list.
- Confirm written authorisation, target boundaries and required preflight.
- Use persistent hot and checkpoint storage when the run must survive restarts.
- Confirm who can approve, reject and cancel sensitive actions.

## Storage and recovery

SQLite is the fast transactional store for active conversations, runs and events. LangGraph checkpoints use persistent SQLite by default. A libSQL or Turso archive can mirror durable records for longer-lived continuity.

Production deployments must persist both the hot store and checkpoint path. A disposable filesystem cannot recover in-flight state after a restart.

## Skills and self-extension

A `SKILL.md` file provides instructions and context; it does not become executable authority by existing on disk.

To add a reviewed skill:

1. Create `munin/agent_skills/<skill-name>/SKILL.md` with matching Agent Skills YAML frontmatter.
2. Keep supporting material in the same directory and reference it from `SKILL.md`.
3. Commit and review the package.
4. Expose it only to specialist profiles whose `SubagentSpec` explicitly lists the skill.

Generated capabilities follow the same rule: narrow contract, validation, registration, policy enforcement and visible provenance.

## Validation

```bash
poetry run pytest
cd app && npm run build
```

## Documentation

| Guide | Purpose |
| --- | --- |
| [Architecture](ARCHITECTURE.md) | System boundaries and invariants |
| [Runtime architecture](docs/architecture.md) | Execution and event contracts |
| [Persistence](docs/architecture-persistence.md) | Recovery and storage roles |
| [System guide](docs/munin-system-guide.md) | Framing and following a run |
| [Operator guide](docs/operator-guide.md) | Deployment and operating practices |
| [Capability reference](docs/tools_reference.md) | Tools, skills and generated extensions |
| [Provider contract](docs/llm-providers.md) | Model endpoint expectations |
| [Security notes](docs/security-notes.md) | Boundaries and review checklist |
| [GitHub Actions guide](docs/github-actions-tutorial.md) | Temporary live sessions |
| [Valravn](docs/VALRAVN.md) | Reconnaissance mesh and providers |

## License

Munin is distributed under the [PolyForm Noncommercial License 1.0.0](LICENSE).

You may inspect, study, research, experiment with and modify the source for permitted noncommercial purposes. Commercial use—including paid products or services, consulting engagements, internal commercial operations or anticipated commercial applications—requires a separate commercial licence from the copyright holder.

Because commercial use is restricted, Munin is **source-available**, not open source under the Open Source Initiative definition.

---

<p align="center">
  <em>What was once seen is never forgotten.</em>
</p>
