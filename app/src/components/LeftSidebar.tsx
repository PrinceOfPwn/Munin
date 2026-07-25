"use client";

import Raven from "./Raven";
import StatusDot from "./StatusDot";
import { useMuninStore } from "@/store/muninStore";
import type { ViewKey } from "@/types/mcp";
import { cn } from "@/lib/utils";

const NAV: { key: ViewKey; label: string; desc: string }[] = [
  { key: "chat", label: "Chat", desc: "Speak with Munin" },
  { key: "tools", label: "Tools", desc: "MCP tool explorer" },
  { key: "memory", label: "Memory", desc: "Semantic + episodic" },
  { key: "soul", label: "Soul", desc: "Identity files" },
  { key: "agents", label: "Agents", desc: "Presence + wake" },
];

export default function LeftSidebar() {
  const view = useMuninStore((s) => s.view);
  const setView = useMuninStore((s) => s.setView);
  const connected = useMuninStore((s) => s.live.mcpConnected);
  const toolCount = useMuninStore((s) => s.live.toolCount);
  const lastError = useMuninStore((s) => s.live.lastError);

  return (
    <div className="w-full h-full flex flex-col">
      <div className="p-4 border-b border-border flex flex-col items-center">
        <Raven size={64} className="text-body" eyeColor="#7c3aed" />
        <div className="mt-2 font-mono text-accent text-xs tracking-widest">
          MUNIN
        </div>
        <div className="text-[10px] text-muted italic text-center mt-0.5">
          What was once seen
          <br />
          is never forgotten.
        </div>
      </div>

      <nav className="flex-1 py-2">
        {NAV.map((n) => (
          <button
            key={n.key}
            onClick={() => setView(n.key)}
            className={cn(
              "w-full text-left px-4 py-2 transition-colors border-l-2",
              view === n.key
                ? "border-accent bg-accent/5 text-body"
                : "border-transparent text-muted hover:text-body hover:bg-surface"
            )}
          >
            <div className="font-mono text-sm uppercase tracking-wider">
              {n.label}
            </div>
            <div className="text-[10px] text-muted">{n.desc}</div>
          </button>
        ))}
      </nav>

      <div className="p-3 border-t border-border">
        <div className="flex items-center gap-2 text-xs">
          <StatusDot status={connected ? "ok" : "error"} pulse={connected} />
          <span className={connected ? "text-success" : "text-rose"}>
            {connected ? "MCP connected" : "MCP offline"}
          </span>
        </div>
        <div className="text-[11px] text-muted mt-1 font-mono">
          {connected ? `${toolCount} tools` : lastError ? "no server" : "—"}
        </div>
      </div>
    </div>
  );
}
