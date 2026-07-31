import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * shadcn-style class merger: run clsx (handles conditional/array/object
 * inputs) then run tailwind-merge to dedupe conflicting Tailwind classes so
 * `cn("p-2", "p-4")` yields "p-4" instead of both.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** Format a millisecond timestamp as a compact locale-aware date/time. */
export function formatWhen(value: number): string {
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

/** Format a duration in seconds as ``H:MM:SS`` (drops the hour prefix under 1h). */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const pad = (n: number) => n.toString().padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
}

/** Render a byte size as KB/MB/GB with one decimal. */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const idx = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / Math.pow(1024, idx);
  return `${value.toFixed(idx === 0 ? 0 : 1)} ${units[idx]}`;
}

/** Convenience: shorten a run id / uuid for display without losing uniqueness. */
export function shortId(id: string, tail = 8): string {
  if (!id) return "";
  return id.length > tail ? id.slice(-tail) : id;
}

export function messageFromError(cause: unknown): string {
  return cause instanceof Error ? cause.message : "Request failed";
}

export const TERMINAL_RUN_STATES = new Set(["completed", "failed", "interrupted", "cancelled"]);

export function isTerminalRun(state: string | undefined): boolean {
  return !!state && TERMINAL_RUN_STATES.has(state);
}
