// -----------------------------------------------------------------------------
// cache — browser-side cache layer for the GUI conversation experience.
//
//   db.ts      hand-rolled IndexedDB wrapper + schema (v1)
//   context.tsx BrowserCacheProvider + useBrowserCache() — the only surface
//              components should touch
//
// Conversations render instantly from the in-memory mirror (seeded from
// IndexedDB at startup), messages rehydrate from IndexedDB per conversation,
// and a run marker survives page reloads so the console can surface a
// "resume streaming?" hint after a mid-run refresh.
// -----------------------------------------------------------------------------

export {
  BrowserCacheProvider,
  useBrowserCache,
} from "./context";
export type {
  CacheConversation,
  CacheMessage,
  KvRecord,
  RunMarker,
} from "./db";
export {
  CACHE_DB_NAME,
  CACHE_DB_VERSION,
  CACHE_SCHEMA_VERSION,
  KV_ACTOR_KEY,
  KV_SCHEMA_KEY,
  STORES,
} from "./db";
