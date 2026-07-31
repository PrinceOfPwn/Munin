// -----------------------------------------------------------------------------
// db.ts â€” minimal hand-rolled IndexedDB wrapper for the Munin browser cache.
//
// Schema (v1): three object stores behind one database:
//
//   * conversations â€” keyPath "id"           (Conversation rows)
//   * messages      â€” keyPath "id", index    (CacheMessage rows)
//                    "by-conversation" on conversation_id
//   * kv            â€” keyPath "key"          (schema guard, actor, run markers)
//
// The versioned database plus the kv schema guard make future migrations
// trivial: bump CACHE_DB_VERSION for structural changes (onupgradeneeded),
// bump CACHE_SCHEMA_VERSION to wipe and reseed logically-incompatible data.
//
// No new dependencies: the repo already hand-rolls IndexedDB this exact way
// in lib/query-cache.ts, and package-lock.json contains no idb/dexie.
// -----------------------------------------------------------------------------

import type { UIMessage } from "ai";

import type { Conversation } from "@/lib/production-api";

export const CACHE_DB_NAME = "munin-browser-cache";
export const CACHE_DB_VERSION = 1;
export const CACHE_SCHEMA_VERSION = "v1";

export const STORES = {
  conversations: "conversations",
  messages: "messages",
  kv: "kv",
} as const;

export const KV_SCHEMA_KEY = "schema";
export const KV_ACTOR_KEY = "actor";

export type CacheConversation = Conversation;

export type CacheMessage = {
  id: string;
  conversation_id: string;
  role: "system" | "user" | "assistant";
  parts: UIMessage["parts"];
  created_at: number;
  order: number;
};

export type RunMarker = {
  runId: string;
  state: string;
  startedAt: number;
};

export type KvRecord = {
  key: string;
  value: unknown;
};

let openPromise: Promise<IDBDatabase> | null = null;

function canUseIndexedDb(): boolean {
  return typeof window !== "undefined" && "indexedDB" in window;
}

function openDatabase(): Promise<IDBDatabase> {
  if (!canUseIndexedDb()) {
    return Promise.reject(new Error("IndexedDB unavailable"));
  }
  if (!openPromise) {
    openPromise = new Promise((resolve, reject) => {
      const request = window.indexedDB.open(CACHE_DB_NAME, CACHE_DB_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(STORES.conversations)) {
          db.createObjectStore(STORES.conversations, { keyPath: "id" });
        }
        if (!db.objectStoreNames.contains(STORES.messages)) {
          const store = db.createObjectStore(STORES.messages, { keyPath: "id" });
          store.createIndex("by-conversation", "conversation_id", { unique: false });
        }
        if (!db.objectStoreNames.contains(STORES.kv)) {
          db.createObjectStore(STORES.kv, { keyPath: "key" });
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => {
        openPromise = null;
        reject(request.error ?? new Error("Unable to open Munin browser cache"));
      };
    });
  }
  return openPromise;
}

function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () =>
      reject(request.error ?? new Error("IndexedDB request failed"));
  });
}

function transactionDone(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () =>
      reject(transaction.error ?? new Error("IndexedDB transaction failed"));
    transaction.onabort = () =>
      reject(transaction.error ?? new Error("IndexedDB transaction aborted"));
  });
}

async function readStore<T>(
  store: string,
  run: (objectStore: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  const db = await openDatabase();
  const transaction = db.transaction(store, "readonly");
  return requestResult(run(transaction.objectStore(store)));
}

async function writeStore(
  store: string,
  run: (objectStore: IDBObjectStore) => void,
): Promise<void> {
  const db = await openDatabase();
  const transaction = db.transaction(store, "readwrite");
  run(transaction.objectStore(store));
  return transactionDone(transaction);
}

// â”€â”€ conversations â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export async function getAllConversations(): Promise<CacheConversation[]> {
  const rows = await readStore(STORES.conversations, (store) => store.getAll());
  return (rows as CacheConversation[]) ?? [];
}

export async function putConversation(row: CacheConversation): Promise<void> {
  await writeStore(STORES.conversations, (store) => {
    store.put(row);
  });
}

export async function putConversations(rows: CacheConversation[]): Promise<void> {
  if (rows.length === 0) return;
  await writeStore(STORES.conversations, (store) => {
    for (const row of rows) store.put(row);
  });
}

export async function deleteConversation(id: string): Promise<void> {
  await writeStore(STORES.conversations, (store) => {
    store.delete(id);
  });
}

// â”€â”€ messages â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export async function getMessagesByConversation(
  conversationId: string,
): Promise<CacheMessage[]> {
  const rows = await readStore(STORES.messages, (store) =>
    store.index("by-conversation").getAll(conversationId),
  );
  const messages = (rows as CacheMessage[]) ?? [];
  return messages.sort((a, b) => a.order - b.order);
}

export async function putMessages(rows: CacheMessage[]): Promise<void> {
  if (rows.length === 0) return;
  await writeStore(STORES.messages, (store) => {
    for (const row of rows) store.put(row);
  });
}

// â”€â”€ kv (schema guard / actor / run markers) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export async function getKv(key: string): Promise<unknown> {
  const row = await readStore(STORES.kv, (store) => store.get(key));
  return (row as KvRecord | undefined)?.value ?? null;
}

export async function putKv(key: string, value: unknown): Promise<void> {
  await writeStore(STORES.kv, (store) => {
    store.put({ key, value });
  });
}

export async function deleteKv(key: string): Promise<void> {
  await writeStore(STORES.kv, (store) => {
    store.delete(key);
  });
}

export async function clearAllStores(): Promise<void> {
  const db = await openDatabase();
  const transaction = db.transaction(
    [STORES.conversations, STORES.messages, STORES.kv],
    "readwrite",
  );
  for (const store of [STORES.conversations, STORES.messages, STORES.kv]) {
    transaction.objectStore(store).clear();
  }
  await transactionDone(transaction);
}
