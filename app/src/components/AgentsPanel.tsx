"use client";

import { useEffect, useState, createContext, useContext } from "react";
import {
  Users,
  Bell,
  MessageSquare,
  RefreshCw,
  Loader2,
  Play,
  Activity,
} from "lucide-react";
import EmptyState from "./EmptyState";
import StatusDot from "./StatusDot";
import SubagentTrace from "./SubagentTrace";
import { useMuninStore } from "@/store/muninStore";
import { getMcpClient, extractToolResultContent } from "@/lib/mcp";
import { relativeTime, localTime } from "@/lib/format";
import type { AgentPresence, WakeItem } from "@/types/mcp";

// Trace target context — a click on any presence / wake row sets the currently
// observed subagent. The trace pane at the right listens.
const TraceCtx = createContext<{ target: string; setTarget: (n: string) => void }>({
  target: "",
  setTarget: () => {},
});

export default function AgentsPanel() {
  const [traceTarget, setTraceTarget] = useState<string>("");

  return (
    <TraceCtx.Provider value={{ target: traceTarget, setTarget: setTraceTarget }}>
      <div className="flex-1 flex min-h-0">
        {/* Left column — presence / wake / messages */}
        <div className="flex-1 flex flex-col min-h-0 overflow-y-auto">
          <div className="border-b border-border px-4 py-3">
            <h2 className="font-mono text-accent uppercase tracking-widest text-sm flex items-center gap-2">
              <Users size={14} /> Agents
            </h2>
            <p className="text-[10px] text-muted mt-1 font-mono">
              Click any agent to watch its ReAct loop in real time.
            </p>
          </div>
          <div className="p-4 space-y-6">
            <PresenceSection />
            <WakeSection />
            <MessagesSection />
          </div>
        </div>

        {/* Right column — live trace of the selected subagent */}
        {traceTarget && (
          <div className="w-[420px] shrink-0 border-l border-border bg-surface/30 p-3 min-h-0 flex flex-col">
            <div className="text-[10px] uppercase tracking-widest text-muted font-mono mb-2 flex items-center gap-1.5">
              <Activity size={12} /> Live trace
            </div>
            <div className="flex-1 min-h-0">
              <SubagentTrace
                key={traceTarget}
                subagent={traceTarget}
                autoStart
                onClose={() => setTraceTarget("")}
              />
            </div>
          </div>
        )}
      </div>
    </TraceCtx.Provider>
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
      const arr = Array.isArray(json?.data?.matches) ? json.data.matches : [];
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
              const name = r.agent || (r as any).agent_name || r.name || "";
              return (
                <ClickableRow key={i} agent={name}>
                  <Td>
                    <span className="font-mono text-body">{name || "—"}</span>
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
                      {(r as any).last_seen_at
                        ? relativeTime((r as any).last_seen_at)
                        : r.last_seen
                        ? relativeTime(r.last_seen)
                        : "—"}
                    </span>
                  </Td>
                </ClickableRow>
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
      const arr = Array.isArray(json?.data?.items) ? json.data.items : [];
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
        subagent: wakeTarget.trim(),
        task_json: JSON.stringify({ prompt: wakeTask.trim() || "Continue your assigned mission." }),
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
                    {typeof r.task === "string" ? r.task : JSON.stringify(r.task || {})}
                  </span>
                </Td>
                <Td>
                  <span className="text-xs text-amber">
                    {r.claimed_at ? "CLAIMED" : "QUEUED"}
                  </span>
                </Td>
                <Td>
                  <span className="text-xs text-muted">
                    {r.claimer_pid || "—"}
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
  const [agent, setAgent] = useState("munin");

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const client = getMcpClient({ baseUrl: mcpUrl, token: mcpToken });
      const args: Record<string, any> = {
        recipient_agent: agent.trim() || "munin",
      };
      const r = await client.callTool("fetch_agent_messages", args);
      const { json } = extractToolResultContent(r);
      const arr = Array.isArray(json?.data?.matches) ? json.data.matches : [];
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
          placeholder="Recipient inbox (defaults to munin)…"
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
                {m.created_at && <span>{localTime(m.created_at)}</span>}
                {m.sender_agent && (
                  <>
                    <span className="text-amber">·</span>
                    <span className="text-ice">{m.sender_agent}</span>
                  </>
                )}
                {m.recipient_agent && (
                  <>
                    <span className="text-amber">→</span>
                    <span className="text-ice">{m.recipient_agent}</span>
                  </>
                )}
                <span className="uppercase">{m.message_type}</span>
              </div>
              <div className="text-sm text-body mt-0.5">
                {m.body || m.subject || JSON.stringify(m)}
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

/**
 * A table row that becomes the current trace target when clicked. If the row
 * has no agent name (unwakeable placeholder), rendering falls back to a plain
 * <tr> so the row is still visible but non-interactive.
 */
function ClickableRow({ agent, children }: { agent: string; children: React.ReactNode }) {
  const { target, setTarget } = useContext(TraceCtx);
  if (!agent) return <tr>{children}</tr>;
  const isActive = target === agent;
  return (
    <tr
      onClick={() => setTarget(agent)}
      className={
        "cursor-pointer transition-colors " +
        (isActive ? "bg-accent/10" : "hover:bg-surface/40")
      }
      title={`Trace ${agent} in real time`}
    >
      {children}
    </tr>
  );
}
