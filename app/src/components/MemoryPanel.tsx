"use client";

import { useEffect, useState } from "react";
import {
  Brain,
  History,
  Network,
  Plus,
  Trash2,
  Search,
  RefreshCw,
  Loader2,
  Star,
} from "lucide-react";
import EmptyState from "./EmptyState";
import JsonViewer from "./JsonViewer";
import { useMuninStore } from "@/store/muninStore";
import { getMcpClient, extractToolResultContent, unwrapToolData } from "@/lib/mcp";
import { relativeTime, localTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  SemanticFact,
  EpisodicEvent,
  ForgedGraph,
} from "@/types/mcp";

type SubTab = "semantic" | "episodic" | "forged";

export default function MemoryPanel() {
  const [tab, setTab] = useState<SubTab>("semantic");
  return (
    <div className="flex-1 flex flex-col min-h-0">
      <div className="border-b border-border px-4 py-3">
        <h2 className="font-mono text-accent uppercase tracking-widest text-sm flex items-center gap-2">
          <Brain size={14} /> Memory
        </h2>
      </div>
      <div className="border-b border-border px-4 py-2 flex gap-1">
        {[
          { k: "semantic", label: "Semantic", icon: <Search size={12} /> },
          { k: "episodic", label: "Episodic", icon: <History size={12} /> },
          { k: "forged", label: "Forged Graphs", icon: <Network size={12} /> },
        ].map((t) => (
          <button
            key={t.k}
            onClick={() => setTab(t.k as SubTab)}
            className={cn(
              "flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-mono uppercase tracking-wider rounded transition-colors",
              tab === t.k
                ? "bg-accent/20 border border-accent/50 text-accent"
                : "text-muted hover:text-body border border-transparent"
            )}
          >
            {t.icon} {t.label}
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-y-auto">
        {tab === "semantic" && <SemanticView />}
        {tab === "episodic" && <EpisodicView />}
        {tab === "forged" && <ForgedView />}
      </div>
    </div>
  );
}

function SemanticView() {
  const mcpUrl = useMuninStore((s) => s.mcpUrl);
  const mcpToken = useMuninStore((s) => s.mcpToken);
  const [facts, setFacts] = useState<SemanticFact[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const [saving, setSaving] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const client = getMcpClient({ baseUrl: mcpUrl, token: mcpToken });
      const r = await client.callTool("memory_list", {});
      const { json } = extractToolResultContent(r);
      const data = unwrapToolData(json);
      const arr = Array.isArray(data)
        ? data
        : data && Array.isArray(data.facts)
        ? data.facts
        : data && Array.isArray(data.items)
        ? data.items
        : data && Array.isArray(data.memories)
        ? data.memories
        : [];
      setFacts(arr);
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

  const remember = async () => {
    if (!key.trim()) return;
    setSaving(true);
    try {
      const client = getMcpClient({ baseUrl: mcpUrl, token: mcpToken });
      await client.callTool("memory_remember", {
        key: key.trim(),
        value_json: JSON.stringify(value),
      });
      setKey("");
      setValue("");
      await load();
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setSaving(false);
    }
  };

  const recall = async (k: string) => {
    try {
      const client = getMcpClient({ baseUrl: mcpUrl, token: mcpToken });
      const r = await client.callTool("memory_recall", { key: k });
      const { json, text } = extractToolResultContent(r);
      const data = unwrapToolData(json);
      alert(`Recall "${k}":\n\n${JSON.stringify(data?.value ?? data ?? text, null, 2) || "(no value)"}`);
    } catch (e: any) {
      alert(`Error: ${e?.message || String(e)}`);
    }
  };

  const remove = async (k: string) => {
    if (!confirm(`Forget "${k}"?`)) return;
    try {
      const client = getMcpClient({ baseUrl: mcpUrl, token: mcpToken });
      // Some servers expose memory_forget; fall back to memory_remember with empty value
      try {
        await client.callTool("memory_forget", { key: k });
      } catch {
        await client.callTool("memory_remember", { key: k, value_json: JSON.stringify("") });
      }
      await load();
    } catch (e: any) {
      alert(`Error: ${e?.message || String(e)}`);
    }
  };

  return (
    <div className="p-4 space-y-4">
      {/* Remember form */}
      <div className="bg-surface border border-border rounded-md p-3 space-y-2">
        <div className="text-[10px] uppercase tracking-widest text-accent font-mono flex items-center gap-1.5">
          <Plus size={12} /> Remember
        </div>
        <div className="grid grid-cols-1 md:grid-cols-[1fr_2fr] gap-2">
          <input
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder="key"
            className="bg-bg border border-border rounded px-2 py-1.5 text-sm font-mono text-body focus:outline-none focus:border-accent/60"
          />
          <input
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="value"
            className="bg-bg border border-border rounded px-2 py-1.5 text-sm font-mono text-body focus:outline-none focus:border-accent/60"
          />
        </div>
        <button
          onClick={remember}
          disabled={saving || !key.trim()}
          className="px-3 py-1.5 text-xs font-mono uppercase tracking-wider rounded bg-accent/20 border border-accent/50 text-accent hover:bg-accent/30 disabled:opacity-50"
        >
          {saving ? "Remembering…" : "Remember"}
        </button>
      </div>

      <div className="flex items-center justify-between">
        <div className="text-[11px] text-muted font-mono">
          {facts.length} facts
        </div>
        <button
          onClick={load}
          className="text-muted hover:text-accent"
          aria-label="Refresh"
        >
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
        </button>
      </div>

      {error && (
        <div className="text-xs text-rose font-mono border border-rose/40 bg-rose/5 px-3 py-2 rounded">
          {error}
        </div>
      )}

      {loading ? (
        <div className="text-muted text-sm font-mono">Loading memories…</div>
      ) : facts.length === 0 ? (
        <EmptyState message="No memories yet." hint="Use the form above to teach Munin." />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-widest text-muted font-mono border-b border-border">
                <th className="py-2 pr-3">Key</th>
                <th className="py-2 pr-3">Value</th>
                <th className="py-2 pr-3">Updated</th>
                <th className="py-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {facts.map((f, i) => (
                <tr key={i} className="border-b border-border/60">
                  <td className="py-2 pr-3 font-mono text-accent break-all">
                    {f.key}
                  </td>
                  <td className="py-2 pr-3 font-mono text-success break-all">
                    {String(f.value ?? "")}
                  </td>
                  <td className="py-2 pr-3 text-muted text-xs">
                    {f.updated_at ? relativeTime(f.updated_at) : "—"}
                  </td>
                  <td className="py-2">
                    <div className="flex items-center gap-1">
                      <button
                        onClick={() => recall(f.key)}
                        className="text-muted hover:text-ice p-1"
                        title="Recall"
                      >
                        <Search size={12} />
                      </button>
                      <button
                        onClick={() => remove(f.key)}
                        className="text-muted hover:text-rose p-1"
                        title="Forget"
                      >
                        <Trash2 size={12} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function EpisodicView() {
  const mcpUrl = useMuninStore((s) => s.mcpUrl);
  const mcpToken = useMuninStore((s) => s.mcpToken);
  const [events, setEvents] = useState<EpisodicEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [agentFilter, setAgentFilter] = useState("");

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const client = getMcpClient({ baseUrl: mcpUrl, token: mcpToken });
      const r = await client.callTool("episodic_query", { limit: 50 });
      const { json } = extractToolResultContent(r);
      const data = unwrapToolData(json);
      const arr = Array.isArray(data)
        ? data
        : data && Array.isArray(data.events)
        ? data.events
        : data && Array.isArray(data.items)
        ? data.items
        : [];
      setEvents(arr);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    const id = window.setInterval(load, 30000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mcpUrl, mcpToken]);

  const filtered = agentFilter.trim()
    ? events.filter((e) =>
        String(e.agent || "").toLowerCase().includes(agentFilter.toLowerCase())
      )
    : events;

  return (
    <div className="p-4 space-y-3">
      <div className="flex items-center gap-2">
        <input
          value={agentFilter}
          onChange={(e) => setAgentFilter(e.target.value)}
          placeholder="Filter by agent…"
          className="flex-1 bg-bg border border-border rounded px-2 py-1.5 text-sm font-mono text-body focus:outline-none focus:border-accent/60"
        />
        <button
          onClick={load}
          className="text-muted hover:text-accent p-1.5"
          aria-label="Refresh"
        >
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
        </button>
      </div>

      {error && (
        <div className="text-xs text-rose font-mono border border-rose/40 bg-rose/5 px-3 py-2 rounded">
          {error}
        </div>
      )}

      {loading ? (
        <div className="text-muted text-sm font-mono">Loading events…</div>
      ) : filtered.length === 0 ? (
        <EmptyState message="No episodic events." hint="Events appear as Munin acts." />
      ) : (
        <ul className="space-y-2">
          {filtered.map((e, i) => (
            <li
              key={i}
              className="border-l-2 border-accent/40 bg-surface/60 px-3 py-2 rounded-r"
            >
              <div className="flex items-center gap-2 text-[11px] text-muted font-mono">
                <span>{e.timestamp ? localTime(e.timestamp) : "—"}</span>
                <span className="text-amber">·</span>
                <span className="text-ice">{e.agent || "—"}</span>
              </div>
              <div className="text-sm text-body mt-0.5">
                {e.action || "—"}
              </div>
              {e.summary && (
                <div className="text-xs text-muted mt-0.5">{e.summary}</div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ForgedView() {
  const mcpUrl = useMuninStore((s) => s.mcpUrl);
  const mcpToken = useMuninStore((s) => s.mcpToken);
  const [graphs, setGraphs] = useState<ForgedGraph[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [descriptions, setDescriptions] = useState<Record<string, any>>({});

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const client = getMcpClient({ baseUrl: mcpUrl, token: mcpToken });
      const r = await client.callTool("list_generated_graphs", {});
      const { json } = extractToolResultContent(r);
      const data = unwrapToolData(json);
      const arr = Array.isArray(data)
        ? data
        : data && Array.isArray(data.graphs)
        ? data.graphs
        : data && Array.isArray(data.items)
        ? data.items
        : [];
      setGraphs(arr);
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

  const describe = async (name: string) => {
    try {
      const client = getMcpClient({ baseUrl: mcpUrl, token: mcpToken });
      const r = await client.callTool("describe_generated_graph", { name });
      const { json, text } = extractToolResultContent(r);
      const data = unwrapToolData(json);
      setDescriptions((d) => ({ ...d, [name]: data !== undefined ? data : text }));
      setExpanded(name === expanded ? null : name);
    } catch (e: any) {
      setDescriptions((d) => ({
        ...d,
        [name]: { error: e?.message || String(e) },
      }));
      setExpanded(name);
    }
  };

  const drop = async (name: string) => {
    if (!confirm(`Drop graph "${name}"?`)) return;
    try {
      const client = getMcpClient({ baseUrl: mcpUrl, token: mcpToken });
      await client.callTool("drop_generated_graph", { name });
      await load();
    } catch (e: any) {
      alert(`Error: ${e?.message || String(e)}`);
    }
  };

  return (
    <div className="p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="text-[11px] text-muted font-mono">
          {graphs.length} graphs
        </div>
        <button
          onClick={load}
          className="text-muted hover:text-accent p-1.5"
          aria-label="Refresh"
        >
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
        </button>
      </div>

      {error && (
        <div className="text-xs text-rose font-mono border border-rose/40 bg-rose/5 px-3 py-2 rounded">
          {error}
        </div>
      )}

      {loading ? (
        <div className="text-muted text-sm font-mono">Loading graphs…</div>
      ) : graphs.length === 0 ? (
        <EmptyState
          message="No forged graphs."
          hint="Use graph_forge to create ReAct configs."
        />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {graphs.map((g, i) => {
            const name = g.name;
            const isExpanded = expanded === name;
            return (
              <div
                key={i}
                className="bg-surface border border-border rounded-md p-3"
              >
                <div className="flex items-center gap-2">
                  <Star size={12} className="text-accent" />
                  <div className="font-mono text-sm text-body truncate">
                    {name}
                  </div>
                </div>
                {g.purpose && (
                  <div className="text-xs text-muted mt-1">{g.purpose}</div>
                )}
                {g.tool_whitelist && (
                  <div className="text-[11px] text-muted mt-1 font-mono">
                    whitelist:{" "}
                    {Array.isArray(g.tool_whitelist)
                      ? g.tool_whitelist.join(", ")
                      : String(g.tool_whitelist)}
                  </div>
                )}
                <div className="flex gap-1 mt-2">
                  <button
                    onClick={() => describe(name)}
                    className="text-[11px] font-mono uppercase tracking-wider px-2 py-1 rounded border border-border text-muted hover:text-accent hover:border-accent/50"
                  >
                    Describe
                  </button>
                  <button
                    onClick={() => drop(name)}
                    className="text-[11px] font-mono uppercase tracking-wider px-2 py-1 rounded border border-border text-muted hover:text-rose hover:border-rose/50"
                  >
                    Drop
                  </button>
                </div>
                {isExpanded && descriptions[name] !== undefined && (
                  <div className="mt-2 border-t border-border pt-2">
                    <JsonViewer data={descriptions[name]} expanded maxExpandDepth={4} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
