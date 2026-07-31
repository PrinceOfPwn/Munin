"use client";

import {
  dehydrate,
  hydrate,
  type DehydratedState,
  type QueryClient,
} from "@tanstack/react-query";

const DB_NAME = "munin-flight-deck";
const DB_VERSION = 1;
const STORE_NAME = "query-snapshots";
const SNAPSHOT_ID = "operator-cache-v1";
const MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;
const ACTOR_MARKER = "munin.cacheActorId";
const PERSISTABLE_ROOTS = new Set([
  "conversations",
  "conversation",
  "run",
  "agents",
  "provider-profiles",
  "conversation-notes",
  "conversation-presence",
  "run-guidance",
]);

type StoredSnapshot = {
  id: string;
  actorId?: string;
  savedAt: number;
  state: DehydratedState;
};

function canUseIndexedDb(): boolean {
  return typeof window !== "undefined" && "indexedDB" in window;
}

function currentActorMarker(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(ACTOR_MARKER);
}

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = window.indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: "id" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("Unable to open Munin cache"));
  });
}

async function readSnapshot(): Promise<StoredSnapshot | null> {
  if (!canUseIndexedDb()) return null;
  const db = await openDatabase();
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, "readonly");
      const request = tx.objectStore(STORE_NAME).get(SNAPSHOT_ID);
      request.onsuccess = () => resolve((request.result as StoredSnapshot | undefined) || null);
      request.onerror = () => reject(request.error || new Error("Unable to read Munin cache"));
    });
  } finally {
    db.close();
  }
}

async function writeSnapshot(snapshot: StoredSnapshot): Promise<void> {
  if (!canUseIndexedDb()) return;
  const db = await openDatabase();
  try {
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, "readwrite");
      tx.objectStore(STORE_NAME).put(snapshot);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error || new Error("Unable to persist Munin cache"));
      tx.onabort = () => reject(tx.error || new Error("Munin cache transaction aborted"));
    });
  } finally {
    db.close();
  }
}

function sanitizedState(client: QueryClient): DehydratedState {
  const state = dehydrate(client, {
    shouldDehydrateQuery: (query) => {
      const root = String(query.queryKey[0] || "");
      return PERSISTABLE_ROOTS.has(root) && query.state.status === "success";
    },
  });

  return state;
}

function buildSnapshot(client: QueryClient, actorId: string): StoredSnapshot {
  return {
    id: SNAPSHOT_ID,
    actorId,
    savedAt: Date.now(),
    state: sanitizedState(client),
  };
}

/** Hydrate only a snapshot that belongs to the authenticated actor marker. */
export async function hydrateMuninQueryCache(
  client: QueryClient,
  actorId = currentActorMarker() || "",
): Promise<boolean> {
  try {
    if (!actorId) {
      await clearMuninQueryCache();
      return false;
    }
    const snapshot = await readSnapshot();
    if (!snapshot) return false;
    if (!snapshot.actorId || snapshot.actorId !== actorId) {
      await clearMuninQueryCache();
      return false;
    }
    if (Date.now() - snapshot.savedAt > MAX_AGE_MS) {
      await clearMuninQueryCache();
      return false;
    }
    hydrate(client, snapshot.state);
    return true;
  } catch {
    // Cache acceleration must never block the authoritative Turso path.
    return false;
  }
}

/** Persist successful read models while an actor marker is active. */
export function subscribeMuninQueryCache(
  client: QueryClient,
  fixedActorId?: string,
): () => void {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let writing = false;
  let dirty = false;
  let disposed = false;

  const activeActor = () => fixedActorId || currentActorMarker();

  const flush = async () => {
    const actorId = activeActor();
    if (disposed || !actorId || currentActorMarker() !== actorId) return;
    if (writing) {
      dirty = true;
      return;
    }
    writing = true;
    try {
      if (!disposed && currentActorMarker() === actorId) {
        await writeSnapshot(buildSnapshot(client, actorId));
      }
    } catch {
      // IndexedDB is an optional accelerator; ignore quota/private-mode failures.
    } finally {
      writing = false;
      const nextActor = activeActor();
      if (dirty && !disposed && nextActor && currentActorMarker() === nextActor) {
        dirty = false;
        timer = setTimeout(() => void flush(), 750);
      }
    }
  };

  const unsubscribe = client.getQueryCache().subscribe(() => {
    const actorId = activeActor();
    if (disposed || !actorId || currentActorMarker() !== actorId) return;
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => void flush(), 750);
  });

  return () => {
    disposed = true;
    dirty = false;
    unsubscribe();
    if (timer) clearTimeout(timer);
    timer = null;
  };
}

/** Delete the browser acceleration snapshot without touching Turso. */
export async function clearMuninQueryCache(): Promise<void> {
  if (!canUseIndexedDb()) return;
  const db = await openDatabase();
  try {
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, "readwrite");
      tx.objectStore(STORE_NAME).delete(SNAPSHOT_ID);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error || new Error("Unable to clear Munin cache"));
    });
  } finally {
    db.close();
  }
}

/** Return metadata only for the current actor's cache snapshot. */
export async function muninQueryCacheInfo(): Promise<{ savedAt: number; queryCount: number } | null> {
  try {
    const actorId = currentActorMarker();
    const snapshot = await readSnapshot();
    if (!snapshot || !actorId || snapshot.actorId !== actorId) return null;
    return { savedAt: snapshot.savedAt, queryCount: snapshot.state.queries.length };
  } catch {
    return null;
  }
}
