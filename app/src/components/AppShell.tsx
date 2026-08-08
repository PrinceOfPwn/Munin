// tags: [ui-component, console-surface, client-component, use-conversation-events, use-send-turn, use-effect, use-run-events, use-collab, use-state, app-shell, shell-layout, workspace-panel, PR-3A, mobile-drawer, PR-3B]
"use client";

import Image from "next/image";
import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import {
  Archive,
  Bot,
  FileCheck2,
  ListChecks,
  Menu,
  PanelRightClose,
  PanelRightOpen,
} from "lucide-react";

import AgentConsole from "@/components/AgentConsole";
import AuthGate from "@/components/AuthGate";
import ConversationSidebar from "@/components/ConversationSidebar";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// PR-3A — workspace panel tabs (artifacts / evidence / runs / agents)
//
// The right-hand zone renders placeholder empty states. Real panels for
// artifacts, evidence, runs and agents do not exist yet in the codebase, so
// this card ships the LAYOUT (tab bar + empty states) and the per-tab mounts
// can be wired to live queries in a later PR.
// ---------------------------------------------------------------------------

const WORKSPACE_TABS = [
  { key: "artifacts", label: "Artifacts", icon: Archive },
  { key: "evidence", label: "Evidence", icon: FileCheck2 },
  { key: "runs", label: "Runs", icon: ListChecks },
  { key: "agents", label: "Agents", icon: Bot },
] as const;

type WorkspaceTabKey = (typeof WORKSPACE_TABS)[number]["key"];

const WORKSPACE_EMPTY: Record<WorkspaceTabKey, { title: string; hint: string }> = {
  artifacts: {
    title: "No artifacts yet",
    hint: "Reports, bundles and files produced by a run appear here.",
  },
  evidence: {
    title: "No evidence yet",
    hint: "Pinned screenshots and collected facts appear here.",
  },
  runs: {
    title: "No runs yet",
    hint: "Durable runs started from this console appear here.",
  },
  agents: {
    title: "No agents yet",
    hint: "Specialist subagents working the conversation appear here.",
  },
};

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
 *   - PR-3A: a responsive 3-zone grid (sidebar + center + right workspace
 *     panel). The third track width is driven by the CSS variable
 *     `--workspace-w` (0px collapsed / 380px expanded via React state), pure
 *     CSS Grid — no absolute positioning, no external dependencies.
 *   - PR-3B: a mobile-only header (`lg:hidden`) with a hamburger button that
 *     opens `ConversationSidebar` in a Radix Dialog side-sheet (`dialog.tsx`
 *     reused — no `vaul`). The drawer closes on conversation select or
 *     backdrop click.
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
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const [workspaceTab, setWorkspaceTab] = useState<WorkspaceTabKey>("artifacts");
  const [drawerOpen, setDrawerOpen] = useState(false);

  // Rehydrate from localStorage once on mount (kept in sync by the sidebar).
  useEffect(() => {
    if (typeof window === "undefined") return;
    const stored = window.localStorage.getItem("munin.activeConversationId");
    if (stored) setActiveConversationId(stored);
  }, []);

  // PR-3A diagnostic: page-level horizontal overflow must never appear when
  // the workspace track expands or contracts. Warns per the card contract
  // ({context: "overflow_check", scrollWidth, innerWidth, viewport}) — the
  // grid is pure CSS so this is a tripwire, not a fix.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const checkOverflow = () => {
      const { scrollWidth } = document.documentElement;
      const { innerWidth } = window;
      if (scrollWidth > innerWidth) {
        console.warn({
          context: "overflow_check",
          scrollWidth,
          innerWidth,
          viewport: innerWidth,
        });
      }
    };
    checkOverflow();
    window.addEventListener("resize", checkOverflow);
    return () => window.removeEventListener("resize", checkOverflow);
  }, [workspaceOpen]);

  function handleSelectConversation(id: string) {
    setActiveConversationId(id);
    setDrawerOpen(false);
  }

  return (
    <AuthGate>
      {(actor, logout) => (
        <main
          className="flex h-screen flex-col overflow-hidden bg-bg text-body"
          style={
            { "--workspace-w": workspaceOpen ? "380px" : "0px" } as CSSProperties
          }
        >
          {/* PR-3B mobile header — hamburger opens the drawer sidebar. */}
          <MobileHeader onMenuClick={() => setDrawerOpen(true)} />

          {/* PR-3A 3-zone grid — the third track is the workspace width var. */}
          <div className="grid min-h-0 min-w-0 flex-1 grid-cols-1 grid-rows-1 overflow-hidden transition-[grid-template-columns] duration-300 ease-out lg:grid-cols-[240px_minmax(0,1fr)_var(--workspace-w,0px)]">
            <ConversationSidebar
              actor={actor}
              activeConversationId={activeConversationId}
              onSelect={setActiveConversationId}
              onLogout={logout}
            />

            <section className="flex min-h-0 min-w-0 flex-col overflow-hidden">
              <CenterToolbar
                workspaceOpen={workspaceOpen}
                onToggleWorkspace={() => setWorkspaceOpen((open) => !open)}
              />
              <div className="min-h-0 min-w-0 flex-1 overflow-hidden">
                <AgentConsole conversationId={activeConversationId} />
              </div>
            </section>

            <WorkspacePanel
              open={workspaceOpen}
              activeTab={workspaceTab}
              onTabChange={setWorkspaceTab}
              onToggle={() => setWorkspaceOpen((open) => !open)}
            />
          </div>

          {/* PR-3B mobile drawer — ConversationSidebar inside a Radix
              Dialog side-sheet (< 1024px only, opened from MobileHeader). */}
          <Dialog open={drawerOpen} onOpenChange={setDrawerOpen}>
            <DialogContent className="fixed inset-y-0 left-0 top-0 h-full w-72 max-w-[85vw] translate-x-0 translate-y-0 gap-0 rounded-none border-r border-border bg-surface p-0 shadow-2xl data-[state=open]:animate-in data-[state=open]:slide-in-from-left data-[state=closed]:animate-out data-[state=closed]:slide-out-to-left">
              <DialogTitle className="sr-only">Conversations</DialogTitle>
              <ConversationSidebar
                embedded
                actor={actor}
                activeConversationId={activeConversationId}
                onSelect={handleSelectConversation}
                onLogout={() => {
                  setDrawerOpen(false);
                  return logout();
                }}
              />
            </DialogContent>
          </Dialog>
        </main>
      )}
    </AuthGate>
  );
}

// ---------------------------------------------------------------------------
// PR-3B — mobile header (hamburger, visible only < 1024px)
// ---------------------------------------------------------------------------

function MobileHeader({ onMenuClick }: { onMenuClick: () => void }) {
  return (
    <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border bg-surface px-3 lg:hidden">
      <Button
        variant="ghost"
        size="icon"
        aria-label="Open conversations"
        onClick={onMenuClick}
        className="-ml-1"
      >
        <Menu />
      </Button>
      <Image
        src="/raven-mark.png"
        width={20}
        height={20}
        alt=""
        className="rounded-sm"
      />
      <div className="flex flex-col leading-tight">
        <b className="text-xs font-semibold tracking-wider">MUNIN</b>
        <small className="text-[0.65rem] text-muted">Agent Console</small>
      </div>
    </header>
  );
}

// ---------------------------------------------------------------------------
// PR-3A — center-column toolbar with the workspace toggle (desktop only)
// ---------------------------------------------------------------------------

function CenterToolbar({
  workspaceOpen,
  onToggleWorkspace,
}: {
  workspaceOpen: boolean;
  onToggleWorkspace: () => void;
}) {
  return (
    <div className="hidden shrink-0 items-center justify-end border-b border-border bg-surface px-3 py-1 lg:flex">
      <Button
        variant="ghost"
        size="sm"
        onClick={onToggleWorkspace}
        aria-expanded={workspaceOpen}
        aria-controls="workspace-panel"
        title={workspaceOpen ? "Collapse workspace" : "Expand workspace"}
      >
        {workspaceOpen ? (
          <PanelRightClose className="h-3.5 w-3.5" />
        ) : (
          <PanelRightOpen className="h-3.5 w-3.5" />
        )}
        Workspace
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PR-3A — right workspace panel (artifacts / evidence / runs / agents)
// ---------------------------------------------------------------------------

function WorkspacePanel({
  open,
  activeTab,
  onTabChange,
  onToggle,
}: {
  open: boolean;
  activeTab: WorkspaceTabKey;
  onTabChange: (tab: WorkspaceTabKey) => void;
  onToggle: () => void;
}) {
  return (
    <aside
      id="workspace-panel"
      aria-label="Workspace panel"
      className={cn(
        "hidden min-h-0 min-w-0 flex-col overflow-hidden border-l border-border bg-surface lg:flex",
        !open && "invisible",
      )}
    >
      <div className="flex shrink-0 items-center gap-1 border-b border-border px-2 py-1.5">
        {WORKSPACE_TABS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            onClick={() => onTabChange(key)}
            aria-pressed={activeTab === key}
            className={cn(
              "flex items-center gap-1.5 rounded px-2 py-1 text-xs transition-colors",
              activeTab === key
                ? "bg-active text-body"
                : "text-muted hover:bg-raised hover:text-body",
            )}
          >
            <Icon className="h-3.5 w-3.5" />
            {label}
          </button>
        ))}
        <button
          type="button"
          onClick={onToggle}
          aria-label="Collapse workspace"
          title="Collapse workspace"
          className="ml-auto rounded p-1 text-muted transition-colors hover:bg-raised hover:text-body"
        >
          <PanelRightClose className="h-4 w-4" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        <WorkspaceEmptyState tab={activeTab} />
      </div>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// PR-3A — placeholder empty states per workspace tab
// ---------------------------------------------------------------------------

function WorkspaceEmptyState({ tab }: { tab: WorkspaceTabKey }) {
  const { title, hint } = WORKSPACE_EMPTY[tab];
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center">
      <p className="text-sm font-medium text-secondary">{title}</p>
      <p className="max-w-[240px] text-xs leading-relaxed text-muted">{hint}</p>
    </div>
  );
}
