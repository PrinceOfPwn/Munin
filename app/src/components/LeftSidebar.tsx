"use client";

import Raven from "./Raven";
import StatusDot from "./StatusDot";
import { useMuninStore } from "@/store/muninStore";
import type { ViewKey } from "@/types/mcp";
import { cn } from "@/lib/utils";
import { MessageSquarePlus } from "lucide-react";

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
  const conversations = useMuninStore((s) => s.conversations);
  const activeConversationId = useMuninStore((s) => s.activeConversationId);
  const conversationsLoading = useMuninStore((s) => s.conversationsLoading);
  const newConversation = useMuninStore((s) => s.newConversation);
  const selectConversation = useMuninStore((s) => s.selectConversation);

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

      <nav className="py-2">
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

      <section className="border-t border-border flex-1 min-h-0 flex flex-col">
        <div className="flex items-center justify-between px-3 py-2">
          <span className="text-[10px] uppercase tracking-widest font-mono text-muted">
            Conversations
          </span>
          <button
            onClick={newConversation}
            className="inline-flex items-center gap-1 rounded border border-accent/40 px-1.5 py-1 text-[10px] font-mono text-accent hover:bg-accent/10"
            title="New conversation"
          >
            <MessageSquarePlus size={12} /> New
          </button>
        </div>
        <div className="overflow-y-auto px-2 pb-2 space-y-1">
          {conversationsLoading && conversations.length === 0 && (
            <div className="px-2 py-2 text-[11px] text-muted">Loading Turso…</div>
          )}
          {!conversationsLoading && conversations.length === 0 && (
            <div className="px-2 py-2 text-[11px] leading-relaxed text-muted">
              Start a thread. Its history will live in Turso.
            </div>
          )}
          {conversations.map((conversation) => (
            <button
              key={conversation.id}
              onClick={() => void selectConversation(conversation.id)}
              className={cn(
                "w-full rounded px-2 py-2 text-left transition-colors",
                activeConversationId === conversation.id
                  ? "bg-accent/10 text-body ring-1 ring-accent/30"
                  : "text-muted hover:bg-surface hover:text-body"
              )}
              title={conversation.title || conversation.id}
            >
              <div className="truncate text-xs font-mono">
                {conversation.title || "Untitled conversation"}
              </div>
              <div className="mt-0.5 text-[10px] text-muted">
                {conversation.message_count || 0} turns
              </div>
            </button>
          ))}
        </div>
      </section>

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
