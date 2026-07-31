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
  savedAt: number;
  state: DehydratedState;
};

function canUseIndexedDb(): boolean {
  return typeof window !== "undefined" && "indexedDB" in window;
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

function buildSnapshot(client: QueryClient): StoredSnapshot {
  return {
    id: SNAPSHOT_ID,
    savedAt: Date.now(),
    state: dehydrate(client, {
      shouldDehydrateQuery: (query) => {
        const root = String(query.queryKey[0] || "");
        return PERSISTABLE_ROOTS.has(root) && query.state.status === "success";
      },
    }),
  };
}

export async function hydrateMuninQueryCache(client: QueryClient): Promise<boolean> {
  try {
    const snapshot = await readSnapshot();
    if (!snapshot) return false;
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

export function subscribeMuninQueryCache(client: QueryClient): () => void {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let writing = false;
  let dirty = false;

  const flush = async () => {
    if (writing) {
      dirty = true;
      return;
    }
    writing = true;
    try {
      await writeSnapshot(buildSnapshot(client));
    } catch {
      // IndexedDB is an optional accelerator; ignore quota/private-mode failures.
    } finally {
      writing = false;
      if (dirty) {
        dirty = false;
        timer = setTimeout(() => void flush(), 750);
      }
    }
  };

  const unsubscribe = client.getQueryCache().subscribe(() => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => void flush(), 750);
  });

  return () => {
    unsubscribe();
    if (timer) clearTimeout(timer);
    void flush();
  };
}

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

export async function muninQueryCacheInfo(): Promise<{ savedAt: number; queryCount: number } | null> {
  try {
    const snapshot = await readSnapshot();
    if (!snapshot) return null;
    return { savedAt: snapshot.savedAt, queryCount: snapshot.state.queries.length };
  } catch {
    return null;
  }
}
