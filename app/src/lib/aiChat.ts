import { useChat } from "@ai-sdk/react";
import type { UIMessage } from "ai";
import { DefaultChatTransport } from "ai";
import { useMemo } from "react";

import { useBrowserCache } from "@/lib/cache";
import { currentCsrfToken } from "@/lib/production-api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface UseMuninChatOptions {
  /** Backend conversation (operation) id — doubles as the useChat id. */
  conversationId: string;
}

type RunStatePart = { data?: { state?: unknown; runId?: unknown } };

function latestRunState(messages: UIMessage[]): { runId: string; state: string } | null {
  let latest: { runId: string; state: string } | null = null;
  for (const message of messages) {
    if (message.role !== "assistant") continue;
    for (const part of message.parts) {
      if (part.type !== "data-run-state") continue;
      const data = (part as unknown as RunStatePart).data;
      const state = typeof data?.state === "string" ? data.state : "";
      const runId = typeof data?.runId === "string" ? data.runId : "";
      if (state && runId) latest = { state, runId };
    }
  }
  return latest;
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
 *
 * The server owns replay. `useChat({ resume: true })` reconnects straight to
 * its durable stream, avoiding a cache-first race that could overwrite new
 * replay events. The browser cache remains a best-effort timeline snapshot
 * and stores the latest server-issued run state for diagnostics.
 */
export function useMuninChat({ conversationId }: UseMuninChatOptions) {
  const cache = useBrowserCache();

  const transport = useMemo(
    () =>
      new DefaultChatTransport({
        api: "/api/chat",
        body: { conversation_id: conversationId },
        headers: () => {
          const token = currentCsrfToken();
          const headers: Record<string, string> = {};
          if (token) headers["X-CSRF-Token"] = token;
          return headers;
        },
      }),
    [conversationId],
  );

  const chat = useChat({
    id: conversationId,
    transport,
    // Reconnect through GET /api/chat/{conversationId}/stream. The backend
    // replays its durable event log, so a browser disconnect only detaches the
    // viewer; it does not cancel the operation.
    resume: true,
    onError: (err) => {
      console.error(`[useMuninChat] conversation=${conversationId}:`, err);
    },
    onFinish: ({ messages }) => {
      // Cache the rendered timeline, but derive lifecycle from the durable
      // server event rather than treating an SSE close (including a HITL
      // pause or a browser disconnect) as a completed operation.
      cache.setMessages(conversationId, messages as UIMessage[]);
      const run = latestRunState(messages as UIMessage[]);
      if (run) {
        cache.setRunMarker(conversationId, {
          runId: run.runId,
          state: run.state,
          startedAt: Date.now(),
        });
      }
    },
  });

  return chat;
}

// ---------------------------------------------------------------------------
// HITL resolves — a server-authorized resource mutation. The browser never
// decides a tool call locally; it submits the server-issued nonce and the
// authenticated store verifies membership, expiry and one-time use.
// ---------------------------------------------------------------------------

async function resolveHitlRequest(
  requestId: string,
  choice: string,
  nonce: string,
  guidance?: string,
): Promise<void> {
  const token = currentCsrfToken();
  const response = await fetch(`/api/production/human-requests/${encodeURIComponent(requestId)}/resolve`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { "X-CSRF-Token": token } : {}),
    },
    body: JSON.stringify({ choice, nonce, guidance }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body?.error?.message || body?.error || `HITL resolution failed (${response.status})`);
  }
}

/** Approve a human-in-the-loop request through the production authority. */
export async function approveHitlRequest(
  requestId: string,
  choice: string,
  nonce: string,
): Promise<void> {
  await resolveHitlRequest(requestId, choice, nonce);
}

/** Reject a human-in-the-loop request through the production authority. */
export async function rejectHitlRequest(
  requestId: string,
  choice: string,
  nonce: string,
  reason?: string,
): Promise<void> {
  await resolveHitlRequest(requestId, choice, nonce, reason);
}
