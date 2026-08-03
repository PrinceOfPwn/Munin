// tags: [ui-component, console-surface, client-component, use-conversation-events, use-send-turn, use-effect, use-run-events, use-collab, use-state, app-shell]
"use client";

import { useEffect, useState } from "react";

import AgentConsole from "@/components/AgentConsole";
import AuthGate from "@/components/AuthGate";
import ConversationSidebar from "@/components/ConversationSidebar";

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * `AppShell` — Fase 1b application shell.
 *
 * Composes:
 *   - `AuthGate`          (login / bootstrap / session teardown)
 *   - `ConversationSidebar`  (search + list + create + logout)
 *   - `AgentConsole`      (AI SDK v5 live stream over `/api/chat`)
 *
 * Mounted by `page.tsx` since Fase 1c — this is the primary shell. The old
 * `FlightDeckStable` file still exists on disk during the parity window but
 * has no importers; Fase 2 deletes it along with `useRunEvents`,
 * `useConversationEvents`, `useCollab`, and `useSendTurn`.
 */
export default function AppShell() {
  const [activeConversationId, setActiveConversationId] = useState<
    string | null
  >(null);

  // Rehydrate from localStorage once on mount (kept in sync by the sidebar).
  useEffect(() => {
    if (typeof window === "undefined") return;
    const stored = window.localStorage.getItem("munin.activeConversationId");
    if (stored) setActiveConversationId(stored);
  }, []);

  return (
    <AuthGate>
      {(actor, logout) => (
        <main className="grid h-screen grid-cols-1 grid-rows-1 overflow-hidden bg-bg text-body lg:grid-cols-[240px_minmax(0,1fr)]">
          <ConversationSidebar
            actor={actor}
            activeConversationId={activeConversationId}
            onSelect={setActiveConversationId}
            onLogout={logout}
          />
          <section className="min-h-0 min-w-0 overflow-hidden">
            <AgentConsole conversationId={activeConversationId} />
          </section>
        </main>
      )}
    </AuthGate>
  );
}
