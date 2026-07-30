import { useChat } from "@ai-sdk/react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface UseMuninChatOptions {
  runId: string;
  chatId: string;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * `useMuninChat` wraps Vercel AI SDK's `useChat` with Munin-specific defaults.
 *
 * - Routes through the BFF SSE endpoint at `/api/chat/runs/:runId/events`
 * - Preserves the `chatId` for client-side deduplication
 * - Keeps the message history scoped to a single run
 */
export function useMuninChat({ runId, chatId }: UseMuninChatOptions) {
  const chat = useChat({
    api: `/api/chat/runs/${runId}/events`,
    id: chatId,
    // Send messages as POST to the same base path (BFF handles routing by method)
    sendExtraMessageFields: true,
    // Maintain partial streaming updates
    experimental_throttle: 50,
    onError: (err) => {
      console.error(`[useMuninChat] run=${runId} error:`, err);
    },
  });

  return chat;
}

// ---------------------------------------------------------------------------
// Standalone helpers
// ---------------------------------------------------------------------------

/**
 * Send guidance text to a run directly via the BFF without going through
 * the useChat hook (e.g., from a separate operator input field).
 */
export async function sendGuidance(
  runId: string,
  text: string
): Promise<void> {
  const res = await fetch(`/api/chat/runs/${runId}/message`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind: "guidance", text }),
  });

  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(
      `sendGuidance failed (${res.status}): ${detail}`
    );
  }
}

/**
 * Approve a human-in-the-loop request.
 */
export async function approveHitlRequest(
  runId: string,
  requestId: string
): Promise<void> {
  const res = await fetch(`/api/chat/runs/${runId}/message`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      kind: "human_resolution",
      request_id: requestId,
      resolution: "approved",
    }),
  });

  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`approveHitlRequest failed (${res.status}): ${detail}`);
  }
}

/**
 * Reject a human-in-the-loop request with an optional reason.
 */
export async function rejectHitlRequest(
  runId: string,
  requestId: string,
  reason?: string
): Promise<void> {
  const res = await fetch(`/api/chat/runs/${runId}/message`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      kind: "human_resolution",
      request_id: requestId,
      resolution: "rejected",
      reason: reason ?? "",
    }),
  });

  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`rejectHitlRequest failed (${res.status}): ${detail}`);
  }
}
