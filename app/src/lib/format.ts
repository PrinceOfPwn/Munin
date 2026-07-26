export function relativeTime(ts: number | string | Date | null | undefined): string {
  if (ts == null) return "—";
  const date =
    ts instanceof Date ? ts : new Date(typeof ts === "number" ? ts : String(ts));
  if (isNaN(date.getTime())) return "—";
  const diff = Date.now() - date.getTime();
  const sec = Math.floor(diff / 1000);
  if (sec < 0) return "just now";
  if (sec < 5) return "just now";
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `${day}d ago`;
  const mo = Math.floor(day / 30);
  if (mo < 12) return `${mo}mo ago`;
  return `${Math.floor(mo / 12)}y ago`;
}

export function localTime(ts: number | string | Date | null | undefined): string {
  if (ts == null) return "—";
  const date =
    ts instanceof Date ? ts : new Date(typeof ts === "number" ? ts : String(ts));
  if (isNaN(date.getTime())) return "—";
  return date.toLocaleString();
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(2)}s`;
  const m = Math.floor(s / 60);
  const rem = Math.floor(s % 60);
  return `${m}m${rem}s`;
}

export function safeJsonStringify(value: any, indent = 2): string {
  try {
    return JSON.stringify(value, null, indent);
  } catch {
    return String(value);
  }
}

export function truncate(s: string, n = 80): string {
  if (!s) return "";
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

export function parseMaybeJson(s: string | undefined): any {
  if (!s) return null;
  try {
    return JSON.parse(s);
  } catch {
    return s;
  }
}
