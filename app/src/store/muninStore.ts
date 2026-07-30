/**
 * muninStore — Zustand store for Munin UI state (PR-16 final).
 *
 * Chat message state removed — messages are managed by useMuninChat
 * (Vercel AI SDK useChat) per conversation. Only navigation/config
 * state lives here.
 */
import { create } from "zustand";
import { persist } from "zustand/middleware";

export type ViewName = "dashboard" | "conversation" | "settings" | "tools";

export interface LiveStatus {
  runId: string | null;
  state: "idle" | "running" | "waiting_for_human" | "completed" | "failed";
  startedAt: number | null;
}

export interface ConversationSummary {
  id: string;
  title: string;
  updatedAt: number;
  activeRunId: string | null;
}

export interface Tool {
  name: string;
  description: string;
  active: boolean;
}

interface MuninState {
  mcpUrl: string;
  mcpToken: string;
  setMcpUrl: (url: string) => void;
  setMcpToken: (token: string) => void;

  settingsOpen: boolean;
  setSettingsOpen: (open: boolean) => void;
  view: ViewName;
  setView: (view: ViewName) => void;

  tools: Tool[];
  setTools: (tools: Tool[]) => void;

  conversations: ConversationSummary[];
  activeConversationId: string | null;
  setConversations: (conversations: ConversationSummary[]) => void;
  setActiveConversationId: (id: string | null) => void;
  upsertConversation: (summary: ConversationSummary) => void;

  live: LiveStatus;
  setLive: (status: Partial<LiveStatus>) => void;
}

export const useMuninStore = create<MuninState>()(
  persist(
    (set) => ({
      mcpUrl: process.env.NEXT_PUBLIC_MUNIN_MCP_URL ?? "http://localhost:8000",
      mcpToken: "",
      setMcpUrl: (url) => set({ mcpUrl: url }),
      setMcpToken: (token) => set({ mcpToken: token }),

      settingsOpen: false,
      setSettingsOpen: (open) => set({ settingsOpen: open }),
      view: "dashboard",
      setView: (view) => set({ view }),

      tools: [],
      setTools: (tools) => set({ tools }),

      conversations: [],
      activeConversationId: null,
      setConversations: (conversations) => set({ conversations }),
      setActiveConversationId: (id) => set({ activeConversationId: id }),
      upsertConversation: (summary) =>
        set((state) => {
          const idx = state.conversations.findIndex((c) => c.id === summary.id);
          if (idx >= 0) {
            const next = [...state.conversations];
            next[idx] = summary;
            return { conversations: next };
          }
          return { conversations: [summary, ...state.conversations] };
        }),

      live: { runId: null, state: "idle", startedAt: null },
      setLive: (status) =>
        set((state) => ({ live: { ...state.live, ...status } })),
    }),
    {
      name: "munin-store",
      partialize: (state) => ({
        mcpUrl: state.mcpUrl,
        activeConversationId: state.activeConversationId,
        view: state.view,
      }),
    }
  )
);
