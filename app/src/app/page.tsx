"use client";

import { useEffect } from "react";
import TopNav from "@/components/TopNav";
import LeftSidebar from "@/components/LeftSidebar";
import RightSidebar from "@/components/RightSidebar";
import Chat from "@/components/Chat";
import ToolExplorer from "@/components/ToolExplorer";
import MemoryPanel from "@/components/MemoryPanel";
import SoulPanel from "@/components/SoulPanel";
import AgentsPanel from "@/components/AgentsPanel";
import SettingsModal from "@/components/SettingsModal";
import ErrorBoundary from "@/components/ErrorBoundary";
import { useMuninStore } from "@/store/muninStore";
import { installGlobalErrorHandlers, log } from "@/lib/logger";

const L = log.init;

export default function Page() {
  const view = useMuninStore((s) => s.view);
  const settingsOpen = useMuninStore((s) => s.settingsOpen);
  const init = useMuninStore((s) => s.init);

  useEffect(() => {
    installGlobalErrorHandlers();
    L.info("Munin UI mounting", {
      env: process.env.NODE_ENV,
      userAgent: navigator.userAgent,
      viewport: `${window.innerWidth}x${window.innerHeight}`,
    });
    init();
    L.info("store.init() called — connecting to MCP server");

    return () => {
      L.info("Munin UI unmounting");
    };
  }, [init]);

  return (
    <div className="h-screen flex flex-col bg-bg text-body overflow-hidden">
      <TopNav />
      <div className="flex-1 flex min-h-0">
        {/* Left sidebar — hidden on mobile */}
        <aside className="hidden md:flex w-60 shrink-0 border-r border-border bg-surface/40 feather-bg">
          <ErrorBoundary name="LeftSidebar">
            <LeftSidebar />
          </ErrorBoundary>
        </aside>

        {/* Center main */}
        <main className="flex-1 min-w-0 flex flex-col bg-bg">
          <ErrorBoundary name={`view:${view}`}>
            {view === "chat"    && <Chat />}
            {view === "tools"   && <ToolExplorer />}
            {view === "memory"  && <MemoryPanel />}
            {view === "soul"    && <SoulPanel />}
            {view === "agents"  && <AgentsPanel />}
          </ErrorBoundary>
        </main>

        {/* Right context sidebar — hidden below lg */}
        <aside className="hidden lg:flex w-72 shrink-0 border-l border-border bg-surface/40">
          <ErrorBoundary name="RightSidebar">
            <RightSidebar />
          </ErrorBoundary>
        </aside>
      </div>

      {/* Mobile bottom tab bar */}
      <nav className="md:hidden flex border-t border-border bg-surface">
        {(["chat", "tools", "memory", "soul", "agents"] as const).map((v) => (
          <button
            key={v}
            onClick={() => useMuninStore.getState().setView(v)}
            className={`flex-1 py-2.5 text-[10px] uppercase tracking-wider font-mono ${
              view === v ? "text-accent border-t-2 border-accent" : "text-muted"
            }`}
          >
            {v}
          </button>
        ))}
      </nav>

      {settingsOpen && <SettingsModal />}
    </div>
  );
}
