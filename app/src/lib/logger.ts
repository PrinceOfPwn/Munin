/**
 * Munin — Defensive Logger
 *
 * Namespaced, leveled console logger with grouped output and timing helpers.
 * All output goes to the browser DevTools console — open it to diagnose issues.
 *
 * Usage:
 *   import { log } from "@/lib/logger";
 *   const L = log.ns("mcp");           // namespace
 *   L.info("connecting", { url });     // [munin:mcp] connecting {url: ...}
 *   const done = L.time("listTools");  // starts a timer
 *   done("ok");                        // logs elapsed ms
 */

type Level = "debug" | "info" | "warn" | "error";

const COLORS: Record<Level, string> = {
  debug: "#6b7280",
  info:  "#38bdf8",
  warn:  "#f59e0b",
  error: "#f43f5e",
};

const NS_COLORS: Record<string, string> = {
  mcp:    "#7c3aed",
  store:  "#10b981",
  chat:   "#38bdf8",
  tools:  "#f59e0b",
  memory: "#a78bfa",
  soul:   "#818cf8",
  agents: "#34d399",
  poll:   "#6b7280",
  init:   "#c084fc",
};

function nsColor(ns: string): string {
  return NS_COLORS[ns] ?? "#94a3b8";
}

function fmt(level: Level, ns: string, msg: string): string[] {
  const time = new Date().toISOString().slice(11, 23); // HH:MM:SS.mmm
  const badge = `%c[munin:${ns}]%c`;
  const lvl   = `%c${level.toUpperCase()}%c`;
  return [
    `${badge} ${lvl} ${time} — ${msg}`,
    `color:${nsColor(ns)};font-weight:bold`,
    "color:inherit",
    `color:${COLORS[level]};font-weight:bold`,
    "color:inherit",
  ];
}

class Logger {
  private namespace: string;

  constructor(ns: string) {
    this.namespace = ns;
  }

  debug(msg: string, ...data: any[]) {
    if (process.env.NODE_ENV !== "development") return;
    console.debug(...fmt("debug", this.namespace, msg), ...data);
  }

  info(msg: string, ...data: any[]) {
    console.info(...fmt("info", this.namespace, msg), ...data);
  }

  warn(msg: string, ...data: any[]) {
    console.warn(...fmt("warn", this.namespace, msg), ...data);
  }

  error(msg: string, err?: unknown, ...data: any[]) {
    const args = [...fmt("error", this.namespace, msg)];
    if (err instanceof Error) {
      args.push("\n  message:", err.message);
      if (err.stack) args.push("\n  stack:", err.stack);
      const ext = err as any;
      if (ext.code  !== undefined) args.push("\n  code:", ext.code);
      if (ext.data  !== undefined) args.push("\n  data:", ext.data);
    } else if (err !== undefined) {
      args.push(err);
    }
    args.push(...data);
    console.error(...args);
  }

  /** Start a timer; call returned fn with outcome label to log elapsed ms. */
  time(label: string): (outcome?: string) => number {
    const t0 = performance.now();
    this.debug(`→ ${label}`);
    return (outcome = "done") => {
      const ms = Math.round(performance.now() - t0);
      this.debug(`← ${label} [${outcome}] ${ms}ms`);
      return ms;
    };
  }

  /** Wrap an async call with request/response logging. */
  async traced<T>(
    label: string,
    fn: () => Promise<T>,
    opts: { logInput?: any; redact?: string[] } = {}
  ): Promise<T> {
    const done = this.time(label);
    let input = opts.logInput;
    if (input && opts.redact) {
      input = { ...input };
      for (const k of opts.redact) if (k in input) input[k] = "[REDACTED]";
    }
    if (input !== undefined) this.debug(`${label} input`, input);
    try {
      const result = await fn();
      done("ok");
      return result;
    } catch (err) {
      done("error");
      this.error(`${label} failed`, err);
      throw err;
    }
  }

  /** Open a console group for a batch of related ops. Closes on returned fn call. */
  group(label: string): () => void {
    console.groupCollapsed(
      `%c[munin:${this.namespace}]%c ${label}`,
      `color:${nsColor(this.namespace)};font-weight:bold`,
      "color:inherit"
    );
    return () => console.groupEnd();
  }
}

function ns(namespace: string): Logger {
  return new Logger(namespace);
}

/** Pre-built namespace loggers — import what you need. */
export const log = {
  ns,
  mcp:    new Logger("mcp"),
  store:  new Logger("store"),
  chat:   new Logger("chat"),
  tools:  new Logger("tools"),
  memory: new Logger("memory"),
  soul:   new Logger("soul"),
  agents: new Logger("agents"),
  poll:   new Logger("poll"),
  init:   new Logger("init"),
};

/**
 * Global unhandled error/rejection catcher.
 * Call once from layout or root component.
 */
export function installGlobalErrorHandlers() {
  if (typeof window === "undefined") return;

  const L = new Logger("global");

  window.addEventListener("error", (e) => {
    L.error(
      `Unhandled error at ${e.filename}:${e.lineno}:${e.colno}`,
      e.error,
      { message: e.message }
    );
  });

  window.addEventListener("unhandledrejection", (e) => {
    L.error("Unhandled promise rejection", e.reason);
  });

  L.info("Global error handlers installed.");
}
