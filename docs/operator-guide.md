# Munin User & Operator Guide

This guide details the operation, architecture, GUI setup, Turso persistent memory state, and Human-in-the-Loop workflows of **Munin**. It is intended for authorized labs, security research, and infrastructure assessment.

For the complete component map and Munin/Hugin operating model, see the
[Munin System Guide](munin-system-guide.md).

---

## 🧠 Mental Model

Munin is not just a chatbot. It is a Model Context Protocol (MCP) platform operating across five distinct planes:

1. **Interaction**: The Next.js Web UI or an external MCP client calls `munin_chat` or individual MCP tools.
2. **Reasoning**: The ReAct agent combines Soul guidelines, persistent memory, and the active tool catalog.
3. **Execution**: Native tools execute LDAP queries, threat intelligence lookups, and authorized system probes.
4. **Delegation**: `munin_wake` enqueues tasks for native subagents (`ldap_agent`, `tool_forge`, `graph_forge`) or forged specialist graphs.
5. **Evidence & Continuity**: Every milestone is logged as an episodic event, with Turso providing durable cloud database state.

The human operator maintains full control over scope, secrets, high-impact commands, and identity proposals.

---

## ⚡ Quick Local Start

1. Copy `.env.example` to `.env`. Configure your LLM provider (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`), `MUNIN_MCP_AUTH_TOKEN`, and LDAP settings.
2. Install Python dependencies:

   ```bash
   poetry install
   ```

3. Start the mock OpenLDAP environment:

   ```bash
   poetry run munin ldap-mock up
   poetry run munin ldap-mock status
   ```

4. Launch the MCP server (listens on loopback by default):

   ```bash
   poetry run munin mcp --transport streamable-http --host 127.0.0.1 --port 8890
   ```

5. In another terminal, launch the Web UI:

   ```bash
   cd app
   npm ci
   npm run dev
   ```

6. Navigate to `http://localhost:3000`, open **Settings**, and enter:
   - **MCP Base URL**: `http://localhost:8890`
   - **Bearer Token**: The value of `MUNIN_MCP_AUTH_TOKEN` (e.g. `munin2024`)

   Click **Test Connection** -> **Save**.

---

## 🖥️ Web UI Navigation

### Chat & ReAct Execution
`munin_chat` executes the ReAct loop. For short tasks it responds synchronously; for long operations it supports observable asynchronous execution (`mode="async"`). The UI renders inline cards for each tool invocation and progress state.

Examples of authorized queries against the mock environment:
- *"Check my LDAP identity and summarize the domain structure."*
- *"Store in memory that target `10.0.0.5` is the primary lab target."*
- *"Create a read-only LDAP inventory specialist and show its whitelist before waking it."*

To run a tool directly in chat, use `/tool_name key=value` syntax or switch to the **Tools** tab.

### Tools Explorer
The tool explorer fetches tool schemas directly via MCP JSON-RPC. Tool responses follow the standardized envelope `{ok, tool, mode, summary, data, error}`. Detailed payload data resides inside `data`.

### Memory Panel
- **Semantic Memory**: Key-value facts saved via `memory_remember`, queried with `memory_recall`, and listed with `memory_list`.
- **Episodic Memory**: Chronological event timeline of decisions and tool calls, queried via `episodic_query`.
- **Forged Graphs**: Specialists created by `graph_forge`.

### Soul Panel
Soul consists of versionable Markdown files in `soul/`: `identity.md`, `principles.md`, `goals.md`, and `skills.md`. Use `soul_list` and `soul_read` (passing `{"path": "identity.md"}`) to inspect.
Modifications are submitted via `soul_propose_edit`. When `MUNIN_AUTO_PR=1` and `gh` is available, proposals automatically open a Pull Request for human review.

### Agent Presence & Subagent Traces
The **Agents** panel shows presence, wake queues, and messages. Selecting an agent opens **Live Trace**, polling `subagent_trace` to display incremental events without losing intermediate context.

---

## ☁️ Online Sessions: GitHub Actions + Turso

### Required Secrets
In `Settings → Secrets and variables → Actions`:

| Secret | Description |
| --- | --- |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | OpenAI-compatible provider endpoints. |
| `MUNIN_MCP_AUTH_TOKEN` | Bearer token for MCP authentication. |
| `MUNIN_DB_URL` | Turso database URL (`libsql://...`). |
| `MUNIN_DB_AUTH_TOKEN` | Turso database auth token with read/write access. |

### Workflow Execution
In **Actions → Munin Live Session → Run workflow**:
- Set `open_web_gui=true` to spin up a public temporary Web UI.
- Set `persist_state=true` to enable Turso cloud state persistence.
- Set `duration_minutes` (1-55 minutes).

The Job Summary prints the temporary Web UI URL. In Settings, paste your `MUNIN_MCP_AUTH_TOKEN`.

---

## 🔨 Tool & Subagent Forging Workflow

1. Check existing capabilities via `list_generated_tools` and native tools.
2. Request `tool_forge` to generate focused, deterministic Python functions. Forged tools pass AST validation and sandbox execution before registering as `gen__<slug>`.
3. Inspect forged tools using `describe_generated_tool`.
4. Create specialist agent graphs with `graph_forge`, specifying a target `purpose` and a minimal `tool_whitelist_csv`.
5. Wake subagents via `munin_wake(subagent, task_json)`.
6. Monitor execution in **Agents → Live Trace**.

### Hugin-assisted investigations

Use `hugin_rag_search` for ranked evidence, `hugin_plan_for` to order candidates,
`hugin_neighbors` to expand relationships, and `hugin_node_detail` to inspect
provenance. Use `hugin_search` for direct matching and `hugin_refresh` only when
the cache is missing or stale. Hugin output is passive evidence to validate,
never authorization or proof of a target-specific finding.

---

## 🔍 Self-Diagnostics & Troubleshooting

Run `munin_diagnostics(mode="deep")` or `munin_self_diagnose()` to perform deep system validation across database connection, LLM configuration, LDAP authentication, tool registries, and system binary dependencies.

### Common Troubleshooting Scenarios
- **`durable source missing` on `gen__*`**: Legacy tool missing source code. Re-forge using `tool_forge`.
- **`tools/call timed out after 30000ms`**: Long operation exceeded synchronous HTTP timeout. Use `munin_chat` async mode or inspect `subagent_trace`.
- **`soul_read` path error**: Ensure `path` parameter is passed as a string (e.g., `{"path": "identity.md"}`).
