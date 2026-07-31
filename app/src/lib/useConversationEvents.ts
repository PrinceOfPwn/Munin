"use client";

/**
 * SSE subscription for a conversation's collaboration channel.
 *
 * The stream accelerates cache invalidation, but it must never suppress the
 * polling repair path merely because an EventSource object was constructed.
 * We only report `live` after an actual open/event/heartbeat signal and mark
 * server warnings as `stale` so React Query can repair the read model.
 */
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { ConversationNote, PresenceEntry } from "./production-api";

export type ConversationStreamStatus = "connecting" | "live" | "stale" | "closed";

interface Options {
  conversationId: string | null;
}

export function useConversationEvents({ conversationId }: Options): {
  status: ConversationStreamStatus;
} {
  const qc = useQueryClient();
  const [status, setStatus] = useState<ConversationStreamStatus>("connecting");
  const staleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!conversationId) {
      setStatus("closed");
      return;
    }

    setStatus("connecting");
    const url = `/api/production/conversations/${encodeURIComponent(conversationId)}/events`;
    const es = new EventSource(url, { withCredentials: true });

    const armStaleTimer = () => {
      if (staleTimer.current) clearTimeout(staleTimer.current);
      staleTimer.current = setTimeout(() => setStatus("stale"), 45_000);
    };

    const markLive = () => {
      setStatus("live");
      armStaleTimer();
    };

    // A connection that never opens must leave `connecting` and become stale.
    armStaleTimer();

    es.addEventListener("note-appended", (msg) => {
      markLive();
      try {
        const note = JSON.parse((msg as MessageEvent).data) as ConversationNote;
        qc.setQueryData<ConversationNote[] | undefined>(
          ["conversation-notes", conversationId],
          (prev) =>
            prev && !prev.some((existing) => existing.id === note.id)
              ? [...prev, note]
              : prev,
        );
      } catch {
        /* ignore malformed */
      }
    });

    es.addEventListener("presence-changed", (msg) => {
      markLive();
      try {
        const raw = JSON.parse((msg as MessageEvent).data) as
          | { presence: PresenceEntry[] }
          | PresenceEntry[];
        const presence = Array.isArray(raw) ? raw : raw.presence;
        qc.setQueryData(["conversation-presence", conversationId], presence);
      } catch {
        /* ignore malformed */
      }
    });

    es.addEventListener("run-transition", (msg) => {
      markLive();
      try {
        const payload = JSON.parse((msg as MessageEvent).data) as {
          run_id?: string;
          state?: string;
        };
        if (payload.run_id) {
          qc.invalidateQueries({ queryKey: ["run", payload.run_id, "detail"] });
        }
      } catch {
        /* ignore malformed */
      }
      qc.invalidateQueries({ queryKey: ["conversation", conversationId] });
      qc.invalidateQueries({ queryKey: ["conversations"] });
    });

    es.addEventListener("guidance-delivered", (msg) => {
      markLive();
      try {
        const payload = JSON.parse((msg as MessageEvent).data) as {
          run_id?: string;
          guidance_id?: string;
          delivered_at_step?: number;
        };
        if (payload.run_id) {
          qc.invalidateQueries({ queryKey: ["run", payload.run_id, "detail"] });
          qc.invalidateQueries({ queryKey: ["run", payload.run_id, "guidance"] });
        }
      } catch {
        /* ignore malformed */
      }
    });

    es.addEventListener("heartbeat", () => markLive());
    es.addEventListener("warning", () => {
      if (staleTimer.current) clearTimeout(staleTimer.current);
      setStatus("stale");
      qc.invalidateQueries({ queryKey: ["conversation", conversationId] });
    });
    es.addEventListener("close", () => {
      setStatus("closed");
      es.close();
    });

    es.onopen = () => markLive();
    es.onerror = () => {
      if (staleTimer.current) clearTimeout(staleTimer.current);
      setStatus("stale");
    };

    return () => {
      es.close();
      if (staleTimer.current) clearTimeout(staleTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  return { status };
}
