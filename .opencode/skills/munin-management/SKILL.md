---
name: munin-management
description: "Repository file tagging and search management convention. Teaches how files are tagged for AI searchability and how to perform grep queries using these tags."
---

# Munin File Tagging and Search Management

To facilitate context loading and searchability for AI agents and developers, this repository enforces a file tagging convention. Every source file must contain a metadata tag header.

## 1. Tagging Convention

Every core source file in `munin/` must start with a tag header on the very first line of the file.

### Format by File Type

- **Python (`.py`)**:
  ```python
  # tags: [mcp, tools, ldap, ActiveDirectory]
  ```
- **JavaScript / TypeScript (`.js`, `.ts`, `.tsx`)**:
  ```typescript
  // tags: [nextjs, component, ui, logs]
  ```
- **Markdown (`.md`, `.mdx`)**:
  Add a `tags` array or comma-separated list in the YAML frontmatter:
  ```markdown
  ---
  name: some-skill
  tags: [recon, active, nmap]
  ---
  ```

## 2. Standard Tag Vocabulary

When tagging files, select tags from the following standard categories to ensure high-fidelity search matching:

| Category | Recommended Tags | Description |
|---|---|---|
| **Core Architecture** | `core`, `supervisor`, `runtime`, `orchestrator`, `soul`, `timers`, `parallel`, `langgraph` | Supervisor loop, LangGraph runtimes, state orchestration, process lifecycles. |
| **MCP Integration** | `mcp`, `mcp-server`, `mcp-tool`, `capabilities`, `registry`, `tool-forge` | FastMCP servers, custom tool registration, capability maps, runtime tool generation. |
| **Database & Persistence** | `database`, `sqlite`, `turso`, `persistence`, `store`, `checkpointer` | SQLite transactional storage, Turso remote databases, state history, checkpoints. |
| **Reconnaissance & OSINT** | `valravn`, `recon`, `intel`, `osint`, `dns`, `scanning`, `shodan`, `censys`, `leakix` | Passive reconnaissance networks, OSINT collection, external APIs. |
| **Active Reconnaissance** | `active-recon`, `nmap`, `httpx`, `nuclei`, `sqlmap`, `hydra`, `smbmap` | Active network scanning, web crawling, service enumeration, vuln assessment. |
| **Active Directory & LDAP** | `ldap`, `activedirectory`, `kerberos`, `kerberoasting`, `asrep-roasting` | LDAP schema-tolerant queries, domain topology dumping, ticket roasting attacks. |
| **Offensive Tradecraft** | `payload`, `shellcode`, `injection`, `evasion`, `c2`, `persistence-tactic`, `privesc` | Malware mechanics, ROP obfuscation, process injection, userland EDR evasion. |
| **Frontend & UI Console** | `web-ui`, `nextjs`, `react`, `component`, `theme`, `radix-ui`, `tailwind` | Next.js console, React components, state, tailwind tokens, Lucide icons. |
| **CI/CD & Deployment** | `cicd`, `actions`, `scripts`, `smoke-test`, `tunnel` | GitHub Actions workflows, proxy configurations, local tunnel managers. |
| **Memory & Coordination** | `memory`, `episodic-memory`, `shared-intel`, `presence`, `coordination` | Agent short-term memory, shared indicators, coordination messages. |
| **Autonomy & Workers** | `subagent`, `workflow`, `worker-fanout`, `beast-mode`, `hitl-approval` | Subagent creation, multi-step workflows, parallel fan-out, human-in-the-loop gates. |

## 3. Searching by Tags

AIs and developers can leverage these tags to quickly locate relevant modules without reading filenames or full codebases:

```bash
# Find all LDAP-related files
git grep -l "tags:.*ldap"

# Find all MCP tools
git grep -l "tags:.*mcp-tool"

# Find all database and state persistence files
git grep -l "tags:.*persistence"
```

## 4. Maintenance Rule

Whenever you create a new file or modify an existing one, ensure the tag header on line 1 is present, accurate, and updated to reflect any new capabilities or integrations.
