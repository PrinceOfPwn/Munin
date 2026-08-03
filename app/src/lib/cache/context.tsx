// tags: [utility-library, indexeddb, browser-cache, persistence, client-component, use-browser-cache, use-memo, use-effect, use-callback, use-context, use-state, t-e-r-m-i-n-a-l--m-a-r-k-e-r--s-t-a-t-e-s, atomic-replace, browser-cache-provider, browser-cache-context]
"use client";

// -----------------------------------------------------------------------------
// context.tsx — BrowserCacheProvider + useBrowserCache().
//
// The single entry point for the browser-side cache. Components never touch
// IndexedDB directly; they read the in-memory mirror (`cachedConversations`)
// or call the async helpers below. Every write is fire-and-forget and
// failures are swallowed — the cache is an optional accelerator, Turso stays
// authoritative.
//
// Actor scoping: the store is wiped when the authenticated actor id differs
// from the one the cache was built under (shared-workstation hygiene). The
// actor is resolved via the production session endpoint on mount; the app's
// own AuthGate runs the same call in parallel.
// -----------------------------------------------------------------------------

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { UIMessage } from "ai";

import type { Conversation } from "@/lib/production-api";
import { productionApi } from "@/lib/production-api";

import {
  CACHE_SCHEMA_VERSION,
  KV_ACTOR_KEY,
  KV_SCHEMA_KEY,
  STORES,
  clearAllStores,
  deleteConversation,
  deleteKv,
  getAllConversations,
  getKv,
  getMessagesByConversation,
  putConversation,
  putConversations,
  putKv,
  replaceMessagesByConversation,
  type CacheMessage,
  type RunMarker,
} from "./db";

const TERMINAL_MARKER_STATES = new Set([
  "completed",
  "failed",
  "interrupted",
  "cancelled",
]);

interface BrowserCacheApi {
  /** Authenticated actor id the cache is scoped to (null before session resolves). */
  actorId: string | null;
  /** True once the schema guard + actor check + list load have completed. */
  ready: boolean;
  /** In-memory mirror of the cached conversation list (newest first). */
  cachedConversations: Conversation[];
  /** Replace the cached conversation list (server truth) and mirror it in memory. */
  writeConversations: (items: Conversation[]) => void;
  /** Upsert a single conversation (optimistic mutation path) + write-through. */
  upsertConversation: (item: Conversation) => void;
  /** Remove a single conversation from cache + memory (archive / rollback). */
  removeConversation: (id: string) => void;
  /** Read the last-known timeline for a conversation (async, from IndexedDB). */
  getMessages: (conversationId: string) => Promise<CacheMessage[]>;
  /** Write the last-known timeline for a conversation (debounced by caller). */
  setMessages: (conversationId: string, messages: UIMessage[]) => void;
  /** Read the interrupted-run marker for a conversation, if any. */
  getRunMarker: (conversationId: string) => Promise<RunMarker | null>;
  /** Set or clear the interrupted-run marker for a conversation. */
  setRunMarker: (conversationId: string, marker: RunMarker | null) => void;
}

const BrowserCacheContext = createContext<BrowserCacheApi | null>(null);

function sortByActivityDesc(items: Conversation[]): Conversation[] {
  return [...items].sort((a, b) => b.last_activity_at_ms - a.last_activity_at_ms);
}

export function BrowserCacheProvider({ children }: { children: ReactNode }) {
  const [actorId, setActorId] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [cachedConversations, setCachedConversations] = useState<
    Conversation[]
  >([]);

  // One-time init: schema guard → actor check → load the cached list.
  useEffect(() => {
    let disposed = false;

    void (async () => {
      try {
        const schema = (await getKv(KV_SCHEMA_KEY)) as string | null;
        if (schema !== CACHE_SCHEMA_VERSION) {
          await clearAllStores();
          await putKv(KV_SCHEMA_KEY, CACHE_SCHEMA_VERSION);
        }
      } catch {
        // IndexedDB unavailable — cache stays a no-op; server is authoritative.
      }

      if (disposed) return;

      let storedActor: string | null = null;
      try {
        storedActor = (await getKv(KV_ACTOR_KEY)) as string | null;
      } catch {
        storedActor = null;
      }

      try {
        const actor = await productionApi.session();
        if (disposed) return;
        setActorId(actor.id);
        if (storedActor !== actor.id) {
          await clearAllStores();
          await putKv(KV_SCHEMA_KEY, CACHE_SCHEMA_VERSION);
          await putKv(KV_ACTOR_KEY, actor.id);
          setCachedConversations([]);
        } else {
          const items = await getAllConversations();
          if (disposed) return;
          setCachedConversations(sortByActivityDesc(items));
        }
      } catch {
        // Not authenticated yet (login screen). Keep whatever is stored so a
        // returning operator sees their timeline immediately after login.
        if (storedActor) setActorId(storedActor);
        try {
          const items = await getAllConversations();
          if (disposed) return;
          setCachedConversations(sortByActivityDesc(items));
        } catch {
          // ignore — cache unavailable
        }
      }

      if (!disposed) setReady(true);
    })();

    return () => {
      disposed = true;
    };
  }, []);

  const writeConversations = useCallback((items: Conversation[]) => {
    const next = sortByActivityDesc(items);
    setCachedConversations(next);
    void putConversations(next).catch(() => {
      // Cache is optional — ignore quota/private-mode failures.
    });
  }, []);

  const upsertConversation = useCallback((item: Conversation) => {
    setCachedConversations((current) => {
      const exists = current.some((entry) => entry.id === item.id);
      return sortByActivityDesc(exists ? current.map((entry) => (entry.id === item.id ? item : entry)) : [item, ...current]);
    });
    void putConversation(item).catch(() => {
      // Cache is optional.
    });
  }, []);

  const removeConversation = useCallback((id: string) => {
    setCachedConversations((current) =>
      current.filter((entry) => entry.id !== id),
    );
    void deleteConversation(id).catch(() => {
      // Cache is optional.
    });
  }, []);

  const getMessages = useCallback(
    (conversationId: string) => getMessagesByConversation(conversationId),
    [],
  );

  const setMessages = useCallback(
    (conversationId: string, messages: UIMessage[]) => {
      const rows: CacheMessage[] = messages.map((message, index) => ({
        id: message.id,
        conversation_id: conversationId,
        role: message.role,
        // Heartbeats are transient liveness markers — keep them out of the
        // persistent timeline so rehydrated history stays compact.
        parts: message.parts.filter((part) => part.type !== "data-heartbeat"),
        created_at: Date.now(),
        order: index,
      }));
      // Atomic clear+put inside one IDB readwrite transaction — avoids the
      // empty-cache window the old clear→then(put) chain exposed when `put`
      // threw after `clear` had already committed.
      void replaceMessagesByConversation(conversationId, rows).catch((error) => {
        console.error({
          context: "cache.setMessages",
          error,
          meta: { conversationId, count: messages.length },
          ts: Date.now(),
        });
      });
    },
    [],
  );

  const getRunMarker = useCallback(
    (conversationId: string) =>
      getKv(`run:${conversationId}`).then((value) => (value as RunMarker | null) ?? null),
    [],
  );

  const setRunMarker = useCallback(
    (conversationId: string, marker: RunMarker | null) => {
      const key = `run:${conversationId}`;
      if (!marker || TERMINAL_MARKER_STATES.has(marker.state)) {
        void deleteKv(key).catch(() => {
          // Cache is optional.
        });
        return;
      }
      void putKv(key, marker).catch(() => {
        // Cache is optional.
      });
    },
    [],
  );

  const api = useMemo<BrowserCacheApi>(
    () => ({
      actorId,
      ready,
      cachedConversations,
      writeConversations,
      upsertConversation,
      removeConversation,
      getMessages,
      setMessages,
      getRunMarker,
      setRunMarker,
    }),
    [
      actorId,
      ready,
      cachedConversations,
      writeConversations,
      upsertConversation,
      removeConversation,
      getMessages,
      setMessages,
      getRunMarker,
      setRunMarker,
    ],
  );

  return (
    <BrowserCacheContext.Provider value={api}>
      {children}
    </BrowserCacheContext.Provider>
  );
}

export function useBrowserCache(): BrowserCacheApi {
  const api = useContext(BrowserCacheContext);
  if (!api) {
    throw new Error("useBrowserCache must be used inside <BrowserCacheProvider>");
  }
  return api;
}
