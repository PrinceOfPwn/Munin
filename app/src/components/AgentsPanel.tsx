"use client";

import { useEffect, useState } from "react";
import {
  Users,
  Bell,
  MessageSquare,
  RefreshCw,
  Loader2,
  Play,
} from "lucide-react";
import EmptyState from "./EmptyState";
import StatusDot from "./StatusDot";
import { useMuninStore } from "@/store/muninStore";
import { getMcpClient, extractToolResultContent } from "@/lib/mcp";
import { relativeTime, localTime } from "@/lib/format";
import type { AgentPresence, WakeItem } from "@/types/mcp";

export default function AgentsPanel() {
  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-y-auto">
      <div className="border-b border-border px-4 py-3">
        <h2 className="font-mono text-accent uppercase tracking-widest text-sm flex items-center gap-2">
          <Users size={14} /> Agents
        </h2>
      </div>
      <div className="p-4 space-y-6">
        <PresenceSection />
        <WakeSection />
        <MessagesSection />
      </div>
    </div>
  );
}

function PresenceSection() {
  const mcpUrl = useMuninStore((s) => s.mcpUrl);
  const mcpToken = useMuninStore((s) => s.mcpToken);
  const [rows, setRows] = useState<AgentPresence[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const client = getMcpClient({ baseUrl: mcpUrl, token: mcpToken });
      const r = await client.callTool("list_agent_presence", {});
      const { json } = extractToolResultContent(r);
      const arr = Array.isArray(json)
        ? json
        : json && Array.isArray(json.presence)
        ? json.presence
        : json && Array.isArray(json.agents)
        ? json.agents
        : [];
      setRows(arr);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mcpUrl, mcpToken]);

  return (
    <section>
      <SectionHeader
        icon={<Users size={12} />}
        title="Presence"
        onRefresh={load}
        loading={loading}
      />
      {error && <ErrorBox message={error} />}
      {loading && rows.length === 0 ? (
        <Loading />
      ) : rows.length === 0 ? (
        <EmptyState message="No agents reporting." />
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>Agent</Th>
              <Th>Status</Th>
              <Th>Last seen</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const status = String(r.status || "UNKNOWN").toUpperCase();
              const dot =
                /RUNNING|ACTIVE/i.test(status)
                  ? "ok"
                  : /IDLE/i.test(status)
                  ? "idle"
                  : "unknown";
              return (
                <tr key={i}>
                  <Td>
                    <span className="font-mono text-body">
                      {r.agent || r.name || "—"}
                    </span>
                  </Td>
                  <Td>
                    <span className="flex items-center gap-1.5 text-xs">
                      <StatusDot status={dot} size={6} />
                      <span
                        className={
                          /RUNNING|ACTIVE/i.test(status)
                            ? "text-success"
                            : /IDLE/i.test(status)
                            ? "text-amber"
                            : "text-muted"
                        }
                      >
                        {status}
                      </span>
                    </span>
                  </Td>
                  <Td>
                    <span className="text-muted text-xs">
                      {r.last_seen ? relativeTime(r.last_seen) : "—"}
                    </span>
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      )}
    </section>
  );
}

function WakeSection() {
  const mcpUrl = useMuninStore((s) => s.mcpUrl);
  const mcpToken = useMuninStore((s) => s.mcpToken);
  const [rows, setRows] = useState<WakeItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [wakeTarget, setWakeTarget] = useState("");
  const [wakeTask, setWakeTask] = useState("");
  const [wakePriority, setWakePriority] = useState("5");
  const [submitting, setSubmitting] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const client = getMcpClient({ baseUrl: mcpUrl, token: mcpToken });
      const r = await client.callTool("munin_wake_list", {});
      const { json } = extractToolResultContent(r);
      const arr = Array.isArray(json)
        ? json
        : json && Array.isArray(json.items)
        ? json.items
        : json && Array.isArray(json.queue)
        ? json.queue
        : [];
      setRows(arr);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mcpUrl, mcpToken]);

  const wake = async () => {
    if (!wakeTarget.trim()) return;
    setSubmitting(true);
    try {
      const client = getMcpClient({ baseUrl: mcpUrl, token: mcpToken });
      await client.callTool("munin_wake", {
        target_agent: wakeTarget.trim(),
        task: wakeTask || undefined,
        priority: wakePriority ? Number(wakePriority) : undefined,
      });
      setWakeTarget("");
      setWakeTask("");
      setWakePriority("5");
      await load();
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section>
      <SectionHeader
        icon={<Bell size={12} />}
        title="Wake queue"
        onRefresh={load}
        loading={loading}
      />

      {/* Wake form */}
      <div className="bg-surface border border-border rounded-md p-3 mb-3 space-y-2">
        <div className="text-[10px] uppercase tracking-widest text-success font-mono">
          Wake agent
        </div>
        <div className="grid grid-cols-1 md:grid-cols-[1fr_2fr_80px_auto] gap-2">
          <input
            value={wakeTarget}
            onChange={(e) => setWakeTarget(e.target.value)}
            placeholder="target_agent"
            className="bg-bg border border-border rounded px-2 py-1.5 text-sm font-mono text-body focus:outline-none focus:border-success/60"
          />
          <input
            value={wakeTask}
            onChange={(e) => setWakeTask(e.target.value)}
            placeholder="task description"
            className="bg-bg border border-border rounded px-2 py-1.5 text-sm font-mono text-body focus:outline-none focus:border-success/60"
          />
          <input
            value={wakePriority}
            onChange={(e) => setWakePriority(e.target.value)}
            placeholder="pri"
            type="number"
            className="bg-bg border border-border rounded px-2 py-1.5 text-sm font-mono text-body focus:outline-none focus:border-success/60"
          />
          <button
            onClick={wake}
            disabled={submitting || !wakeTarget.trim()}
            className="flex items-center justify-center gap-1.5 px-3 py-1.5 text-xs font-mono uppercase tracking-wider rounded bg-success/20 border border-success/50 text-success hover:bg-success/30 disabled:opacity-50"
          >
            {submitting ? (
              <Loader2 size={12} className="animate-spin" />
            ) : (
              <Play size={12} />
            )}
            Wake
          </button>
        </div>
      </div>

      {error && <ErrorBox message={error} />}
      {loading && rows.length === 0 ? (
        <Loading />
      ) : rows.length === 0 ? (
        <EmptyState message="No wake calls queued." />
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>ID</Th>
              <Th>Target</Th>
              <Th>Pri</Th>
              <Th>Task</Th>
              <Th>Status</Th>
              <Th>Claimed by</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <Td>
                  <span className="font-mono text-xs text-muted">
                    {r.id || "—"}
                  </span>
                </Td>
                <Td>
                  <span className="font-mono text-body">
                    {r.target_agent || "—"}
                  </span>
                </Td>
                <Td>
                  <span className="font-mono text-ice">
                    {r.priority ?? "—"}
                  </span>
                </Td>
                <Td>
                  <span className="text-xs text-muted">
                    {r.task || "—"}
                  </span>
                </Td>
                <Td>
                  <span className="text-xs text-amber">
                    {r.status || "—"}
                  </span>
                </Td>
                <Td>
                  <span className="text-xs text-muted">
                    {r.claimed_by || "—"}
                  </span>
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </section>
  );
}

function MessagesSection() {
  const mcpUrl = useMuninStore((s) => s.mcpUrl);
  const mcpToken = useMuninStore((s) => s.mcpToken);
  const [rows, setRows] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [agent, setAgent] = useState("");

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const client = getMcpClient({ baseUrl: mcpUrl, token: mcpToken });
      const args: Record<string, any> = {};
      if (agent.trim()) args.agent = agent.trim();
      const r = await client.callTool("fetch_agent_messages", args);
      const { json } = extractToolResultContent(r);
      const arr = Array.isArray(json)
        ? json
        : json && Array.isArray(json.messages)
        ? json.messages
        : json && Array.isArray(json.items)
        ? json.items
        : [];
      setRows(arr);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    const id = window.setInterval(load, 10000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mcpUrl, mcpToken, agent]);

  return (
    <section>
      <SectionHeader
        icon={<MessageSquare size={12} />}
        title="Agent messages"
        onRefresh={load}
        loading={loading}
      />
      <div className="mb-2">
        <input
          value={agent}
          onChange={(e) => setAgent(e.target.value)}
          placeholder="Filter by agent (leave empty for all)…"
          className="w-full bg-bg border border-border rounded px-2 py-1.5 text-sm font-mono text-body focus:outline-none focus:border-accent/60"
        />
      </div>
      {error && <ErrorBox message={error} />}
      {loading && rows.length === 0 ? (
        <Loading />
      ) : rows.length === 0 ? (
        <EmptyState message="No agent messages." />
      ) : (
        <ul className="space-y-2">
          {rows.map((m, i) => (
            <li
              key={i}
              className="border-l-2 border-success/50 bg-surface/60 px-3 py-2 rounded-r"
            >
              <div className="flex items-center gap-2 text-[11px] text-muted font-mono">
                {m.timestamp && <span>{localTime(m.timestamp)}</span>}
                {m.from && (
                  <>
                    <span className="text-amber">·</span>
                    <span className="text-ice">{m.from}</span>
                  </>
                )}
                {m.to && (
                  <>
                    <span className="text-amber">→</span>
                    <span className="text-ice">{m.to}</span>
                  </>
                )}
              </div>
              <div className="text-sm text-body mt-0.5">
                {m.content || m.message || m.text || JSON.stringify(m)}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// --- shared bits ---

function SectionHeader({
  icon,
  title,
  onRefresh,
  loading,
}: {
  icon: React.ReactNode;
  title: string;
  onRefresh: () => void;
  loading: boolean;
}) {
  return (
    <div className="flex items-center justify-between mb-2">
      <h3 className="flex items-center gap-1.5 text-[11px] uppercase tracking-widest text-muted font-mono">
        {icon} {title}
      </h3>
      <button
        onClick={onRefresh}
        className="text-muted hover:text-accent"
        aria-label="Refresh"
      >
        <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
      </button>
    </div>
  );
}

function Table({ children }: { children: React.ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">{children}</table>
    </div>
  );
}
function Th({ children }: { children: React.ReactNode }) {
  return (
    <th className="text-left text-[10px] uppercase tracking-widest text-muted font-mono py-2 pr-3 border-b border-border">
      {children}
    </th>
  );
}
function Td({ children }: { children: React.ReactNode }) {
  return <td className="py-2 pr-3 border-b border-border/60">{children}</td>;
}
function Loading() {
  return (
    <div className="text-muted text-sm font-mono flex items-center gap-1.5 py-4">
      <Loader2 size={14} className="animate-spin" /> Loading…
    </div>
  );
}
function ErrorBox({ message }: { message: string }) {
  return (
    <div className="text-xs text-rose font-mono border border-rose/40 bg-rose/5 px-3 py-2 rounded mb-2">
      {message}
    </div>
  );
}
```

---

## Notes on running

1. **Install & run:**
   ```bash
   npm install
   npm run dev
   ```
   Open `http://localhost:3000`.

2. **Configure the server:** click the gear icon → set MCP Base URL (`http://localhost:8890`) and Bearer Token → **Test connection** → **Save**. On success the left sidebar shows a green dot and tool count, the Tool Explorer populates, and the right Live panel begins polling every 15s.

3. **Chatting:** Type free text to converse (if Munin exposes a chat-style tool, it is called automatically; otherwise Munin replies with a thoughtful capability summary and instructs the user on slash-command syntax). Slash commands like `/ldap_who_am_i`, `/episodic_query limit=5`, or `/memory_remember key=foo value=bar` invoke tools directly with inline result cards.

4. **Resilience:** Every panel handles a missing/unreachable MCP server with a raven empty-state and the last error string. The Live sidebar's MCP status dot turns red and shows the error inline.

5. **Deviations from spec (deliberate, documented in README):**
   - Soul "Propose edit" uses a styled `<textarea>` instead of CodeMirror 6 to keep the dependency surface minimal and the build reliable. The diff is still submitted to `soul_propose_edit` exactly as specified.
   - `memory_forget` is attempted for fact deletion, falling back to `memory_remember` with an empty value if the tool is not present.
   - Agent-message polling runs every 10s, episodic timeline every 30s, and the right Live panel every 15s — all as specified.

The app requires zero backend changes — only Munin's MCP server at `localhost:8890` with a valid Bearer token.
