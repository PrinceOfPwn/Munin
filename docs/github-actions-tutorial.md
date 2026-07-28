# GitHub Actions Live Session Tutorial

> A step-by-step tutorial on configuring, launching, and connecting to **Munin Live Session** on GitHub Actions with persistent Turso database state and public Web GUI access.

---

## 📖 Overview

**Munin Live Session** is a automated workflow (`.github/workflows/live-session.yml`) that provisions an ephemeral **Kali Linux runner** in GitHub Actions.

When triggered, the workflow:
1. Installs the full offensive & recon toolchain (`nmap`, `nuclei`, `feroxbuster`, `ffuf`, `sqlmap`, `hydra`, `smbmap`, `netexec`, `katana`, `httpx`).
2. Connects to your **Turso Cloud Database** for durable state (memory, episodic timeline, forged tools, subagents).
3. Spins up an isolated **OpenLDAP mock server** (`akatsuki.com`).
4. Builds the production **Next.js Web UI** and launches the **FastMCP HTTP server**.
5. Exposes a public temporary tunnel through one of three providers: authenticated
   **ngrok**, **cloudflared Quick Tunnel**, or **localhost.run**. `auto` prefers
   ngrok when the repository secret is configured, then falls back safely.
6. Validates the authenticated MCP server against the online Turso backend, the
   seeded LDAP directory, and the isolated Apache training fixture before the
   session is left open.

---

## 🔑 Step 1: Fork and Configure Required GitHub Secrets

Before launching the workflow for the first time, you need your own copy of the repository to run Actions and store your secrets safely:

1. **Fork this repository** to your personal GitHub account (click the "Fork" button at the top right of the original repo).
2. Navigate to your newly forked repository on GitHub.
3. Go to **Settings** → **Secrets and variables** → **Actions**.
4. Click **New repository secret** and add the following required variables:

| Secret Name | Description & Example Value |
| --- | --- |
| `LLM_BASE_URL` | OpenAI-compatible API base URL (e.g., `https://integrate.api.nvidia.com/v1`, `https://api.openai.com/v1`, `https://api.groq.com/openai/v1`) |
| `LLM_API_KEY` | Your LLM provider API key (`nvapi-...`, `sk-...`, `gsk_...`) |
| `LLM_MODEL` | Target LLM model name (e.g., `meta/llama-3.3-70b-instruct`, `gpt-4o-mini`, `qwen/qwen2.5-coder-32b-instruct`) |
| `MUNIN_MCP_AUTH_TOKEN` | Secret Bearer authentication token for the Web GUI and MCP server (e.g., `munin2024`) |
| `MUNIN_DB_URL` | Your Turso Database URL (`libsql://munin-xxx.aws-us-east-2.turso.io`) |
| `MUNIN_DB_AUTH_TOKEN` | Your Turso Database Auth Token |
| `NGROK_AUTH_TOKEN` | *(Optional)* Your ngrok Auth Token; enables the ngrok tunnel provider |
| `TAVILY_API_KEY` | *(Optional)* Tavily API key for web search capabilities |

---

## 🚀 Step 2: How to Launch the Action

1. Go to your repository on GitHub and click the **Actions** tab.
2. On the left sidebar under *Workflows*, select **Munin Live Session**.
3. Click the **Run workflow** dropdown button on the right side.
4. Fill out the launch parameters:

   - **`duration_minutes`**: Set session duration in minutes (default `30`, max `55`).
   - **`open_web_gui`**: Set to `true` (enables building Next.js GUI and opening the public tunnel).
   - **`persist_state`**: Set to `true` (enables Turso state synchronization).
   - **`preflight_policy`**: Set to `off` (bypasses OPSEC preflight checks specifically for the workflow's isolated mock LDAP lab).
   - **`tunnel_provider`**: Use `auto` for ngrok → cloudflared → localhost.run,
     or select one provider explicitly. Explicit selection never silently changes
     provider, which makes troubleshooting predictable.

5. Click the green **Run workflow** button.

---

## 🌐 Step 3: Connecting to the Live Web GUI

1. Once the workflow starts, click on the active run (e.g., *Munin Live Session #XX*).
2. Wait ~45-60 seconds for the setup steps (*Install system tools*, *Build Next.js Frontend*, *Start Munin MCP*, *Open public tunnel*) to complete.
3. Scroll down to the **Job Summary** section at the bottom of the page.
4. Locate the **Web GUI URL** link (for example an ngrok, `trycloudflare.com`,
   or localhost.run URL).
5. Open the link in your web browser.

### Authentication Setup in Settings
Once the Web GUI loads in your browser:
1. Click the **Settings (gear icon ⚙️)** in the top-right corner.
2. **MCP Base URL**: Leave set to `window.location.origin` (or `https://<ngrok-id>.ngrok-free.dev`).
3. **Bearer Token**: Paste your `MUNIN_MCP_AUTH_TOKEN` secret (e.g., `munin2024`).
4. Click **Test Connection** → **Save**.
5. You will see the status change to **"Connected. 70+ tools available."**

---

## 🧪 Step 4: System Verification & Operation

Once connected in the Web GUI, verify system health:

### 1. Run Health Diagnostics
Go to the **Tools** tab in the Web GUI, search for `munin_diagnostics`, and run with:
```json
{
  "mode": "deep"
}
```
This tests database connectivity (Turso), LLM endpoint readiness, LDAP bind authentication (`dn:cn=admin,dc=akatsuki,dc=com`), Hugin cache, and tool registries. The Actions workflow additionally performs an authenticated MCP E2E check against LDAP and the isolated Apache fixture.

### 2. Run Self-Diagnostics
Alternatively, run `munin_self_diagnose` to inspect installed binaries, environment configurations, and output the Master AI Refactoring prompt.

### 3. Interact via Chat
Switch to the **Chat** tab and send commands:
- *"Who am I in LDAP and what is the domain structure?"*
- *"Run a port scan against localhost and summarize open services."*
- *"Forge a new tool to summarize LDAP group memberships."*

---

## 🛑 Step 5: Session Termination & State Persistence

- **Automatic Cleanup**: When `duration_minutes` expires, GitHub Actions automatically stops the runner and closes the tunnel.
- **State Preservation**: Because Turso Cloud DB is connected, all facts saved to memory, episodic event timelines, forged tools (`gen__*`), and subagent graphs remain permanently saved in your Turso database and will automatically rehydrate when you launch your next session.
