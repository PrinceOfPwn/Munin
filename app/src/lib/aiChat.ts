import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import { useMemo } from "react";

import { currentCsrfToken, productionApi } from "@/lib/production-api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface UseMuninChatOptions {
  /** Backend conversation (operation) id — doubles as the useChat id. */
  conversationId: string;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * `useMuninChat` — Vercel AI SDK v5 `useChat` wired to the Munin BFF.
 *
 * The transport posts to `/api/chat` with the active conversation id and the
 * operator's CSRF token; the BFF commits a turn in the authoritative
 * production API and streams the run back as a v5 UI message stream
 * (text deltas, dynamic tool parts, data-* operational parts).
 */
export function useMuninChat({ conversationId }: UseMuninChatOptions) {
  const transport = useMemo(
    () =>
      new DefaultChatTransport({
        api: "/api/chat",
        body: { conversation_id: conversationId },
        headers: () => {
          const token = currentCsrfToken();
          return token ? { "X-CSRF-Token": token } : {};
        },
      }),
    [conversationId],
  );

  return useChat({
    id: conversationId,
    transport,
    onError: (err) => {
      console.error(`[useMuninChat] conversation=${conversationId}:`, err);
    },
  });
}

// ---------------------------------------------------------------------------
// Standalone helpers (guidance + HITL resolutions go straight to production)
// ---------------------------------------------------------------------------

/** Deliver operator guidance to a running execution (auditable server-side). */
export async function sendGuidance(runId: string, text: string): Promise<void> {
  await productionApi.guideRun(runId, text);
}

/** Approve a human-in-the-loop request. */
export async function approveHitlRequest(requestId: string): Promise<void> {
  await productionApi.resolveHumanRequest(requestId, "approved");
}

/** Reject a human-in-the-loop request with an optional reason. */
export async function rejectHitlRequest(requestId: string, reason?: string): Promise<void> {
  await productionApi.resolveHumanRequest(requestId, "rejected", "", reason ?? "");
}
