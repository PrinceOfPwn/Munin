"use client";

import { useMemo, useState } from "react";
import { Play, Star, Search, Wrench } from "lucide-react";
import Drawer from "./Drawer";
import EmptyState from "./EmptyState";
import ToolRunForm from "./ToolRunForm";
import { useMuninStore } from "@/store/muninStore";
import { categorize, CATEGORY_TABS } from "@/lib/categories";
import { cn } from "@/lib/utils";
import type { McpTool } from "@/types/mcp";

export default function ToolExplorer() {
  const tools = useMuninStore((s) => s.tools);
  const loading = useMuninStore((s) => s.toolsLoading);
  const [tab, setTab] = useState<string>("All");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<McpTool | null>(null);

  const filtered = useMemo(() => {
    return tools.filter((t) => {
      const cat = categorize(t.name);
      if (tab !== "All" && cat.label !== tab) return false;
      if (query.trim()) {
        const q = query.toLowerCase();
        return (
          t.name.toLowerCase().includes(q) ||
          (t.description || "").toLowerCase().includes(q)
        );
      }
      return true;
    });
  }, [tools, tab, query]);

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Header */}
      <div className="border-b border-border px-4 py-3 space-y-2">
        <div className="flex items-center justify-between">
          <h2 className="font-mono text-accent uppercase tracking-widest text-sm flex items-center gap-2">
            <Wrench size={14} /> Tool Explorer
          </h2>
          <div className="text-[11px] text-muted font-mono">
            {tools.length} tools
          </div>
        </div>

        {/* Search */}
        <div className="relative">
          <Search
            size={14}
            className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted"
          />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter tools by name or description…"
            className="w-full bg-bg border border-border rounded pl-8 pr-3 py-1.5 text-sm font-mono text-body focus:outline-none focus:border-accent/60"
          />
        </div>

        {/* Tabs */}
        <div className="flex flex-wrap gap-1">
          {CATEGORY_TABS.map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={cn(
                "px-2.5 py-1 text-[11px] font-mono uppercase tracking-wider rounded transition-colors",
                tab === t
                  ? "bg-accent/20 border border-accent/50 text-accent"
                  : "text-muted hover:text-body border border-transparent"
              )}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      {/* Grid */}
      <div className="flex-1 overflow-y-auto p-4">
        {loading ? (
          <div className="text-muted text-sm font-mono">Loading tools…</div>
        ) : filtered.length === 0 ? (
          <EmptyState
            message="No tools match."
            hint="Connect to the MCP server or adjust filters."
          />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
            {filtered.map((t) => {
              const cat = categorize(t.name);
              const isGen = t.name.startsWith("gen__");
              const paramCount = t.inputSchema?.properties
                ? Object.keys(t.inputSchema.properties).length
                : 0;
              return (
                <div
                  key={t.name}
                  className="bg-surface border rounded-md p-3 flex flex-col"
                  style={{ borderColor: cat.color + "44" }}
                >
                  <div className="flex items-center gap-2 mb-1">
                    <span
                      className="text-[10px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded-sm"
                      style={{ backgroundColor: cat.color + "22", color: cat.color }}
                    >
                      {cat.label}
                    </span>
                    {isGen && (
                      <span
                        className="text-amber flex items-center gap-0.5 text-[10px] font-mono"
                        title="Forged tool"
                      >
                        <Star size={10} /> forged
                      </span>
                    )}
                  </div>
                  <div className="font-mono text-sm text-body break-all">
                    {t.name}
                  </div>
                  <div className="text-xs text-muted mt-1 line-clamp-3 flex-1">
                    {t.description || "No description."}
                  </div>
                  <div className="text-[10px] text-muted mt-2 font-mono">
                    {paramCount} parameter{paramCount === 1 ? "" : "s"}
                  </div>
                  <button
                    onClick={() => setSelected(t)}
                    className="mt-2 w-full flex items-center justify-center gap-1.5 text-xs font-mono uppercase tracking-wider py-1.5 rounded border transition-colors"
                    style={{
                      borderColor: cat.color + "66",
                      color: cat.color,
                      backgroundColor: cat.color + "11",
                    }}
                  >
                    <Play size={12} /> Run
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <Drawer
        open={!!selected}
        onClose={() => setSelected(null)}
        title={selected ? `Run: ${selected.name}` : ""}
      >
        {selected && <ToolRunForm tool={selected} onDone={() => setSelected(null)} />}
      </Drawer>
    </div>
  );
}
