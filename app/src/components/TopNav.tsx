"use client";

import { useEffect } from "react";
import { Settings } from "lucide-react";
import Raven from "./Raven";
import { useMuninStore } from "@/store/muninStore";
import type { ViewKey } from "@/types/mcp";
import { cn } from "@/lib/utils";

const NAV: { key: ViewKey; label: string }[] = [
  { key: "chat", label: "Chat" },
  { key: "tools", label: "Tools" },
  { key: "memory", label: "Memory" },
  { key: "soul", label: "Soul" },
  { key: "agents", label: "Agents" },
];

export default function TopNav() {
  const view = useMuninStore((s) => s.view);
  const setView = useMuninStore((s) => s.setView);
  const openSettings = useMuninStore((s) => s.openSettings);

  // "/" focuses chat input from anywhere
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const inField =
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable);
      if (e.key === "/" && !inField) {
        e.preventDefault();
        setView("chat");
        setTimeout(() => {
          const el = document.getElementById("munin-chat-input");
          el?.focus();
        }, 0);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [setView]);

  return (
    <header className="h-14 shrink-0 border-b border-border bg-surface/60 flex items-center px-4 gap-4">
      {/* Brand */}
      <div className="flex items-center gap-2">
        <Raven size={28} className="text-body" />
        <div className="leading-none">
          <div className="font-mono text-accent font-bold tracking-widest text-sm">
            MUNIN
          </div>
          <div className="text-[10px] text-muted italic">
            What was once seen is never forgotten.
          </div>
        </div>
      </div>

      {/* Nav */}
      <nav className="hidden md:flex items-center gap-1 ml-4">
        {NAV.map((n) => (
          <button
            key={n.key}
            onClick={() => setView(n.key)}
            className={cn(
              "px-3 py-1.5 text-sm font-mono uppercase tracking-wider rounded transition-colors",
              view === n.key
                ? "text-accent bg-accent/10 border border-accent/30"
                : "text-muted hover:text-body border border-transparent"
            )}
          >
            {n.label}
          </button>
        ))}
      </nav>

      <div className="ml-auto flex items-center gap-2">
        <button
          onClick={openSettings}
          className="p-2 rounded text-muted hover:text-accent hover:bg-accent/10 transition-colors"
          aria-label="Settings"
          title="Settings"
        >
          <Settings size={18} />
        </button>
      </div>
    </header>
  );
}
