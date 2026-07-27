# Munin — Web Frontend

A living intelligence terminal for **Munin**, a multi-agent offensive security AI
system with a persistent soul, episodic memory, and dynamic tool forging.

> *What was once seen is never forgotten.*

## Quickstart

```bash
npm install
npm run dev
```

Then open [http://localhost:3000](http://localhost:3000).

On first launch, click the gear icon (top-right) and configure:

- **MCP Base URL** — default `http://localhost:8890`
- **Bearer Token** — your `MUNIN_MCP_AUTH_TOKEN`

Credentials are stored only in `localStorage`; they are never sent anywhere
except directly to the Munin MCP server.

The `Munin Live Session` GitHub Actions workflow can also build and publish this
GUI as a temporary website. In that mode, the frontend defaults to its own
origin and Next.js proxies `/mcp/` to the authenticated Munin server inside the
same runner. Only the bearer token must be entered in Settings; Turso credentials
remain server-side.

## Requirements

- Node.js 18+ (Node 20 recommended)
- Munin's FastMCP server running at the configured base URL with
  `streamable-http` transport exposing `/mcp`.

## Architecture

The UI speaks JSON-RPC 2.0 directly to the MCP server:

```
POST <base>/mcp/
Authorization: Bearer <token>
Content-Type: application/json

{ "jsonrpc": "2.0", "id": "<uuid>", "method": "tools/list", "params": {} }
{ "jsonrpc": "2.0", "id": "<uuid>", "method": "tools/call",
  "params": { "name": "<tool>", "arguments": { ... } } }
```

No backend changes are required.

## Features

- **Chat** — multi-line input (Ctrl+Enter to send), inline tool-call cards with
  category-colored borders, thinking indicator, jump-to-bottom FAB.
  Slash commands: `/tool_name key=value key2=value2` invoke tools directly.
- **Tool Explorer** — catalog grouped by category; run tools via an
  auto-generated form drawer with syntax-highlighted JSON output.
- **Memory** — Semantic (key/value facts), Episodic (event timeline),
  Forged Graphs (generated ReAct configs).
- **Soul** — read Munin's identity files, propose edits (textarea diff editor
  submitted via `soul_propose_edit`).
- **Agents** — presence table, wake queue, agent message feed.
- **Live sidebar** — MCP status, presence, forged tool count, wake queue,
  last episodic event. Polls every 15s.

## Tech

- Next.js 14 (App Router) + React 18
- Tailwind CSS v3 (dark theme only)
- Zustand for state
- Lucide React for icons
- react-markdown + rehype-highlight for prose
- Native fetch for MCP transport

## Keyboard

- `/` — focus chat input from anywhere
- `Esc` — close any open drawer/modal
- `Ctrl+Enter` — send chat message

## Notes

- Dark mode only. No light toggle.
- Tool call card border color is driven by tool category:
  LDAP=ice blue, Forge=violet, Memory=amber, Recon=rose, Agents=emerald,
  Intel/CVE=muted.
- `ok: false` responses render as rose-bordered error cards with
  `error.code` and `error.message`.
