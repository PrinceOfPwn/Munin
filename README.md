<p align="center">
  <img src="app/public/raven-mark.png" alt="Munin Raven Logo" width="160" />
</p>

# Munin — Autonomous AI Security & Threat Intelligence Agent

> *What was once seen is never forgotten.*

**Munin** is an operator-governed cybersecurity, threat-intelligence, and security-orchestration platform built on **MCP (Model Context Protocol)**. For authorized research and lab simulations, it combines multi-step **ReAct loops**, durable **Turso** state, episodic memory, dynamic Python tools, evidence-aware subagents, and reviewable self-extension proposals.

Munin enables operators to automate vulnerability analysis, Active Directory & OpenLDAP enumeration (`akatsuki.com`), passive intelligence enrichment (NVD, CVE, EPSS, CISA KEV, OSV, Hugin), and active reconnaissance (Nmap, Nuclei, Ffuf, Feroxbuster), while maintaining complete visibility via its modern Next.js control terminal.

---

## ⚡ Core Capabilities

| Architecture Layer | Description |
| --- | --- |
| **MCP Standard Protocol** | Asynchronous FastMCP HTTP server (`/mcp`) supporting Bearer authentication and session tokens. |
| **Autonomous ReAct Agent** | `munin_chat` runs multi-step reasoning cycles, self-diagnostics (`munin_self_diagnose`), and delegation. |
| **Durable Turso Persistence** | Real-time synchronization of semantic facts, episodic history, Soul identity, and forged tools to Turso cloud. |
| **Adaptive Directory Enumeration** | Parametric tools for Active Directory and OpenLDAP (`dc=akatsuki,dc=com`), evaluating OUs, users, and groups. |
| **Threat Intelligence Engines** | Native integration with Hugin Knowledge Base, CVE/NVD/EPSS/CISA KEV enrichment pipelines, and Tavily. |
| **Dynamic Tool & Graph Forging** | `tool_forge` generates Python tools (`gen__*`) on the fly; `graph_forge` builds specialized subagents. |
| **Evidence Mesh + Hugin RAG** | Graph-aware Hugin retrieval supplies scored evidence and plan candidates without granting extra scope. |
| **Governed Self-Extension** | `extension_forge` validates an inert diff proposal; only an explicitly approved `extension_open_pr` creates a reviewable PR. |
| **Discord Operator Bridge** | Optional allowlisted Discord control plane for 24/7 notifications and operator messages; active tools retain scope and OPSEC gates. |
| **Control Web Terminal** | Next.js dashboard featuring live chat, 70+ tool explorer, Soul/Memory inspection, and real-time subagent traces. |

> 🎯 **Recommended Exercise Planner**: For maximum efficiency during security exercise planning and tool execution, we invite operators to use the fine-tuned [OFFX-Qwen3.5-9B Track A (DoRA Planner)](https://www.kaggle.com/code/emilianoperalta/offx-qwen35-9b-track-a-dora-planner-w10-20260701) model. See [LLM Providers](docs/llm-providers.md) for deployment instructions.

---

## 🚀 Local Quickstart

### Prerequisites
- Python 3.11+ with Poetry
- Node.js 18+ for Web UI
- Docker (optional, for mock OpenLDAP server)

```bash
poetry install
cp .env.example .env
# Configure LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, and MUNIN_MCP_AUTH_TOKEN in .env
```

### 1. Launch Mock OpenLDAP & MCP Server
```bash
poetry run munin ldap-mock up
poetry run munin mcp --transport streamable-http --host 127.0.0.1 --port 8890
```

### 2. Launch Web UI Terminal
In another terminal window:
```bash
cd app
npm ci
npm run dev
```

Navigate to `http://localhost:3000`, open **Settings**, and configure:
- **MCP Base URL**: `http://localhost:8890`
- **Bearer Token**: The value of `MUNIN_MCP_AUTH_TOKEN` (e.g. `munin`)

Click **Test Connection** → **Save**.

---

## ☁️ Online Live Sessions (GitHub Actions + Turso)

Run **Munin Live Session** directly from GitHub Actions:
1. Configure Actions Secrets: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `MUNIN_MCP_AUTH_TOKEN`, `MUNIN_DB_URL`, `MUNIN_DB_AUTH_TOKEN`.
2. Go to **Actions → Munin Live Session → Run workflow**.
3. Select `open_web_gui=true`, `persist_state=true`, and `duration_minutes`.
4. Open the temporary Web UI provided in the Job Summary and enter your Bearer token.

For technical details, see the [Operator Guide](docs/operator-guide.md#online-sessions-github-actions--turso).

---

## 🏗️ Repository Architecture

```text
munin/                 MCP server, shared state, tool registry, and Python tools
munin/core/            ReAct agent loop, LLM client, and orchestrator
munin/subagents/       Subagent runners, tool/graph forge, and code validation
app/                   Next.js Web UI control terminal
soul/                  Versioned identity, principles, goals, and skills (Markdown)
docs/                  Technical and operational documentation
.github/workflows/     CI/CD and GitHub Actions Live Session workflows
scripts/               Mock LDAP setup, Turso smoke tests, and tunnels
tests/                 Unit, integration, and persistence regression tests
```

---

## 📚 Technical Documentation

- 🚀 [GitHub Actions Live Session Tutorial](docs/github-actions-tutorial.md)
- 📘 [Operator & User Guide](docs/operator-guide.md)
- 🏛️ [Architecture & Persistence Specification](docs/architecture-persistence.md)
- 🛠️ [Tools Reference Inventory](docs/tools_reference.md)
- 🔒 [Operational Security Notes](docs/security-notes.md)
- 🤖 [LLM Provider Configurations](docs/llm-providers.md)
- 🖥️ [Web UI README](app/README.md)
