"use client";

/**
 * Zero-dependency floating-window registry.
 *
 * We deliberately avoid `zustand` here because:
 *   * The state shape is trivial (a Map keyed by window id).
 *   * The rest of the app already uses TanStack Query for server state and
 *     `useState`/`useReducer` for local state — introducing a third mental
 *     model for six lines of state would be gratuitous.
 *
 * The implementation is a module-level subscribable store with
 * `useSyncExternalStore` on the read side, which keeps components in sync
 * across React 18's concurrent renders without any extra machinery.
 */
import { useSyncExternalStore } from "react";

export type FloatingWindowKind = "forge";

export interface FloatingWindowEntry {
  id: string;
  kind: FloatingWindowKind;
  runId: string;
  subagentId: string;
  subagentProfileId: string;
  subagentRole: string;
  /** Wall-clock ms when the window was opened; used as a tiebreaker in the UI. */
  openedAt: number;
}

const STORAGE_KEY = "munin.floatingWindows.open";

interface Store {
  windows: Map<string, FloatingWindowEntry>;
}

const store: Store = { windows: new Map() };
const listeners = new Set<() => void>();

function persist(): void {
  if (typeof window === "undefined") return;
  try {
    const payload = Array.from(store.windows.values());
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
  } catch {
    /* quota / storage disabled — no-op */
  }
}

function hydrate(): void {
  if (typeof window === "undefined") return;
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (!raw) return;
  try {
    const parsed = JSON.parse(raw) as FloatingWindowEntry[];
    if (!Array.isArray(parsed)) return;
    for (const entry of parsed) {
      if (
        entry &&
        typeof entry.id === "string" &&
        typeof entry.runId === "string" &&
        typeof entry.subagentId === "string"
      ) {
        store.windows.set(entry.id, entry);
      }
    }
  } catch {
    /* corrupted payload — drop it */
  }
}

let hydrated = false;
function ensureHydrated(): void {
  if (hydrated) return;
  hydrated = true;
  hydrate();
}

function emit(): void {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  ensureHydrated();
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot(): FloatingWindowEntry[] {
  ensureHydrated();
  return Array.from(store.windows.values()).sort((a, b) => a.openedAt - b.openedAt);
}

let cachedSnapshot: FloatingWindowEntry[] = [];
let cachedRevision = -1;
let revision = 0;

function stableSnapshot(): FloatingWindowEntry[] {
  if (revision !== cachedRevision) {
    cachedSnapshot = getSnapshot();
    cachedRevision = revision;
  }
  return cachedSnapshot;
}

function bump(): void {
  revision += 1;
  persist();
  emit();
}

export function openFloatingWindow(entry: Omit<FloatingWindowEntry, "openedAt">): void {
  ensureHydrated();
  if (store.windows.has(entry.id)) return;
  store.windows.set(entry.id, { ...entry, openedAt: Date.now() });
  bump();
}

export function closeFloatingWindow(id: string): void {
  ensureHydrated();
  if (!store.windows.has(id)) return;
  store.windows.delete(id);
  bump();
}

export function isFloatingWindowOpen(id: string): boolean {
  ensureHydrated();
  return store.windows.has(id);
}

export function useFloatingWindows(): FloatingWindowEntry[] {
  return useSyncExternalStore(subscribe, stableSnapshot, stableSnapshot);
}
