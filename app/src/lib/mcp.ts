/**
 * Munin MCP Client — fault-tolerant JSON-RPC 2.0 transport.
 *
 * Layers (innermost → outermost):
 *   1. fetch with AbortController timeout
 *   2. Error classification (retryable vs fatal)
 *   3. Retry with exponential backoff + jitter (idempotent calls only)
 *   4. Circuit breaker (stops hammering a dead server)
 *   5. In-flight deduplication (one concurrent request per idempotent key)
 */

import { uuid } from "./utils";
import { log } from "./logger";
import type { McpTool, McpToolResult } from "@/types/mcp";

const L = log.mcp;

// ─────────────────────────────────────────────────────────────────────────────
// Config
// ─────────────────────────────────────────────────────────────────────────────

export interface McpConfig {
  baseUrl: string;
  token: string;
}

/** Tune these to taste. */
const DEFAULTS = {
  /** Per-request network timeout (ms). Tool calls that take longer get aborted. */
  requestTimeout: 30_000,
  /** Max retries for idempotent calls (listTools, ping, job status). */
  maxRetries: 4,
  /** Base backoff (ms). Actual delay = base * 2^attempt + jitter. */
  backoffBase: 5_000,
  /** Max backoff cap (ms). */
  backoffMax: 60_000,
  /** Circuit opens after this many consecutive failures. */
  cbFailureThreshold: 5,
  /** Circuit stays open for this long before allowing a probe (ms). */
  cbResetTimeout: 20_000,
};

// ─────────────────────────────────────────────────────────────────────────────
// Error classification
// ─────────────────────────────────────────────────────────────────────────────

type ErrorKind =
  | "network"     // fetch threw — server unreachable, CORS, etc.
  | "timeout"     // AbortError — request took too long
  | "auth"        // 401 / 403 — wrong token; user action required, don't retry
  | "not-found"   // 404 — wrong endpoint
  | "rate-limit"  // 429 — back off longer
  | "server"      // 5xx — transient server error, retryable
  | "rpc"         // JSON-RPC error object — application-level, don't retry
  | "parse"       // response wasn't valid JSON
  | "unknown";

interface ClassifiedError extends Error {
  kind: ErrorKind;
  status?: number;
  retryable: boolean;
  hint: string;
  rpcCode?: number | string;
  rpcData?: any;
}

function classified(
  message: string,
  kind: ErrorKind,
  opts: Partial<Pick<ClassifiedError, "status" | "rpcCode" | "rpcData">> = {}
): ClassifiedError {
  const retryable = ["network", "timeout", "rate-limit", "server"].includes(kind);
  const hints: Record<ErrorKind, string> = {
    network:    "Munin server is unreachable. Is it running? Check URL in Settings.",
    timeout:    "Request timed out. Server may be overloaded or the tool takes too long.",
    auth:       "Bearer token invalid or missing. Open Settings and update it.",
    "not-found":"MCP endpoint not found. Verify the server URL in Settings (expected /mcp/).",
    "rate-limit":"Server is rate-limiting. Requests will slow down automatically.",
    server:     "Server error in Munin. Check Munin's process logs.",
    rpc:        "Munin rejected the request. See rpcCode / rpcData for details.",
    parse:      "Server returned non-JSON. Possible HTML error page or proxy issue.",
    unknown:    "Unexpected error. Check DevTools Network tab.",
  };
  const err = new Error(message) as ClassifiedError;
  err.kind = kind;
  err.retryable = retryable;
  err.hint = hints[kind];
  err.status = opts.status;
  err.rpcCode = opts.rpcCode;
  err.rpcData = opts.rpcData;
  return err;
}

// ─────────────────────────────────────────────────────────────────────────────
// Circuit Breaker
// ─────────────────────────────────────────────────────────────────────────────

type CbState = "CLOSED" | "OPEN" | "HALF_OPEN";

class CircuitBreaker {
  private state: CbState = "CLOSED";
  private failures = 0;
  private openedAt = 0;
  private readonly threshold: number;
  private readonly resetTimeout: number;

  constructor(threshold: number, resetTimeout: number) {
    this.threshold = threshold;
    this.resetTimeout = resetTimeout;
  }

  get status(): CbState { return this.state; }

  /** Returns true if a request should be allowed through. */
  canRequest(): boolean {
    if (this.state === "CLOSED") return true;
    if (this.state === "OPEN") {
      if (Date.now() - this.openedAt >= this.resetTimeout) {
        this.state = "HALF_OPEN";
        L.info("Circuit breaker → HALF_OPEN (probe allowed)");
        return true;
      }
      return false;
    }
    // HALF_OPEN: allow exactly one probe
    return true;
  }

  onSuccess() {
    if (this.state !== "CLOSED") {
      L.info(`Circuit breaker → CLOSED (recovered after ${this.failures} failures)`);
    }
    this.failures = 0;
    this.state = "CLOSED";
  }

  onFailure(kind: ErrorKind) {
    // Auth / not-found errors are not transient — don't count them toward the circuit
    if (kind === "auth" || kind === "not-found" || kind === "rpc") return;

    this.failures++;
    if (this.state === "HALF_OPEN" || this.failures >= this.threshold) {
      this.state = "OPEN";
      this.openedAt = Date.now();
      L.warn(`Circuit breaker → OPEN after ${this.failures} failures`, {
        resetIn: `${this.resetTimeout / 1000}s`,
      });
    }
  }

  /** Diagnostic snapshot for the sidebar. */
  snapshot() {
    return {
      state: this.state,
      failures: this.failures,
      openedAt: this.openedAt,
    };
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Backoff helper
// ─────────────────────────────────────────────────────────────────────────────

function backoffMs(attempt: number, base: number, max: number): number {
  const exp = Math.min(base * Math.pow(2, attempt), max);
  const jitter = exp * 0.2 * (Math.random() * 2 - 1); // ±20%
  return Math.round(exp + jitter);
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

function parseRpcEnvelope(text: string, contentType: string, requestId: string): any {
  if (contentType.includes("text/event-stream")) {
    const events = text.split(/\r?\n\r?\n/);
    for (const event of events) {
      const payload = event
        .split(/\r?\n/)
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n");
      if (!payload) continue;
      const parsed = JSON.parse(payload);
      if (parsed?.id === requestId || parsed?.error) return parsed;
    }
    throw new Error("SSE response contained no JSON-RPC result");
  }
  return JSON.parse(text);
}

// ─────────────────────────────────────────────────────────────────────────────
// McpClient
// ─────────────────────────────────────────────────────────────────────────────

export class McpClient {
  private baseUrl: string;
  private token: string;
  private sessionId = "";
  private initializePromise: Promise<void> | null = null;
  private cb = new CircuitBreaker(DEFAULTS.cbFailureThreshold, DEFAULTS.cbResetTimeout);

  /**
   * In-flight map for idempotent calls.
   * Key: "method:stableParamsHash" → Promise.
   * Prevents duplicate concurrent requests (e.g. listTools called from 3 components at once).
   */
  private inflight = new Map<string, Promise<any>>();

  constructor(config: McpConfig) {
    this.baseUrl = (config.baseUrl || "").replace(/\/+$/, "");
    this.token = config.token || "";
    L.info("McpClient created", { baseUrl: this.baseUrl, hasToken: !!this.token });
  }

  setConfig(config: McpConfig) {
    const prev = this.baseUrl;
    const prevToken = this.token;
    this.baseUrl = (config.baseUrl || "").replace(/\/+$/, "");
    this.token = config.token || "";
    if (prev !== this.baseUrl || prevToken !== this.token) {
      L.info("McpClient reconfigured", { baseUrl: this.baseUrl, hasToken: !!this.token });
      // URL/token changes imply a different authenticated MCP session.
      this.cb = new CircuitBreaker(DEFAULTS.cbFailureThreshold, DEFAULTS.cbResetTimeout);
      this.inflight.clear();
      this.sessionId = "";
      this.initializePromise = null;
    }
  }

  /** Expose circuit breaker state for the sidebar status indicator. */
  get circuitState(): CbState { return this.cb.status; }

  private endpoint(): string {
    // Next.js normalizes `/mcp/` to `/mcp` in the hosted GUI. Avoid that
    // redirect: browsers may not retain the bearer header across the proxy's
    // redirect chain, turning a valid token into a misleading 403.
    return this.baseUrl.endsWith("/mcp") ? this.baseUrl : `${this.baseUrl}/mcp`;
  }

  private async ensureSession(timeoutMs: number): Promise<void> {
    if (this.sessionId) return;
    if (!this.initializePromise) {
      this.initializePromise = this.fetchOnce(
        "initialize",
        {
          protocolVersion: "2024-11-05",
          capabilities: {},
          clientInfo: { name: "munin-ui", version: "0.1.0" },
        },
        Math.min(timeoutMs, DEFAULTS.requestTimeout)
      ).then(() => undefined).catch((error) => {
        this.sessionId = "";
        throw error;
      }).finally(() => {
        this.initializePromise = null;
      });
    }
    await this.initializePromise;
  }

  // ───── Layer 1: raw fetch with timeout ────────────────────────────────────

  private async fetchOnce<T>(
    method: string,
    params: any,
    timeoutMs: number,
    allowSessionRecovery = true
  ): Promise<T> {
    if (method !== "initialize") {
      await this.ensureSession(timeoutMs);
    }
    const id = uuid();
    const url = this.endpoint();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const requestSessionId = this.sessionId;

    L.debug(`→ ${method}`, { id, timeoutMs, params });

    let res: Response;
    try {
      res = await fetch(url, {
        method: "POST",
        signal: controller.signal,
        headers: {
          "Content-Type": "application/json",
          "Accept": "application/json, text/event-stream",
          ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}),
          ...(this.sessionId ? {
            "mcp-session-id": this.sessionId,
            "MCP-Protocol-Version": "2024-11-05",
          } : {}),
        },
        body: JSON.stringify({ jsonrpc: "2.0", id, method, params }),
      });
    } catch (e: any) {
      clearTimeout(timer);
      if (e?.name === "AbortError") {
        throw classified(`${method} timed out after ${timeoutMs}ms`, "timeout");
      }
      throw classified(
        `Network error: ${e?.message || String(e)}`,
        "network"
      );
    } finally {
      clearTimeout(timer);
    }

    if (!res.ok) {
      const body = await res.text().catch(() => "");
      const status = res.status;
      if (
        status === 404 &&
        method !== "initialize" &&
        allowSessionRecovery &&
        requestSessionId &&
        this.sessionId === requestSessionId
      ) {
        L.info(`MCP session ${requestSessionId} expired; initializing a replacement`);
        this.sessionId = "";
        return this.fetchOnce<T>(method, params, timeoutMs, false);
      }
      const kind: ErrorKind =
        status === 401 || status === 403 ? "auth"
        : status === 404               ? "not-found"
        : status === 429               ? "rate-limit"
        : status >= 500                ? "server"
        : "unknown";
      throw classified(
        `HTTP ${status} ${res.statusText}`,
        kind,
        { status }
      );
    }

    if (method === "initialize") {
      const initializedSession = res.headers.get("mcp-session-id") || "";
      if (!initializedSession) {
        throw classified("MCP initialize response omitted mcp-session-id", "rpc");
      }
      this.sessionId = initializedSession;
    }

    let data: any;
    try {
      const text = await res.text();
      data = parseRpcEnvelope(text, res.headers.get("content-type") || "", id);
    } catch (e: any) {
      throw classified(
        `MCP response parse failed: ${e?.message}`,
        "parse"
      );
    }

    if (data?.error) {
      throw classified(
        `RPC ${data.error.code}: ${data.error.message}`,
        "rpc",
        { rpcCode: data.error.code, rpcData: data.error.data }
      );
    }

    L.debug(`← ${method} ok`);
    return data.result as T;
  }

  // ───── Layer 2: retry with backoff (idempotent calls only) ────────────────

  private async sendWithRetry<T>(
    method: string,
    params: any,
    opts: { retries: number; timeoutMs: number }
  ): Promise<T> {
    const { retries, timeoutMs } = opts;
    let lastErr: ClassifiedError | undefined;

    for (let attempt = 0; attempt <= retries; attempt++) {
      // Layer 3: circuit breaker check
      if (!this.cb.canRequest()) {
        const snap = this.cb.snapshot();
        L.warn(`Circuit OPEN — rejecting ${method} without network call`, snap);
        throw classified(
          `Circuit breaker is OPEN after ${snap.failures} failures. ` +
          `Will retry in ~${Math.ceil((DEFAULTS.cbResetTimeout - (Date.now() - snap.openedAt)) / 1000)}s.`,
          "network"
        );
      }

      if (attempt > 0) {
        const delay = backoffMs(attempt - 1, DEFAULTS.backoffBase, DEFAULTS.backoffMax);
        L.info(`Retry ${attempt}/${retries} for ${method} in ${delay}ms`);
        await sleep(delay);
      }

      try {
        const result = await this.fetchOnce<T>(method, params, timeoutMs);
        this.cb.onSuccess();
        return result;
      } catch (e: any) {
        const err = e as ClassifiedError;
        lastErr = err;
        this.cb.onFailure(err.kind ?? "unknown");

        L.warn(`${method} attempt ${attempt + 1} failed [${err.kind}]: ${err.message}`, {
          retryable: err.retryable,
          hint: err.hint,
          remaining: retries - attempt,
        });

        if (!err.retryable || attempt >= retries) break;
      }
    }

    // All attempts exhausted — log final structured error
    L.error(`${method} failed after ${retries + 1} attempt(s)`, lastErr, {
      hint: lastErr?.hint,
      kind: lastErr?.kind,
      circuitState: this.cb.status,
    });
    throw lastErr;
  }

  // ───── Layer 3: in-flight deduplication (idempotent calls only) ──────────

  private deduped<T>(key: string, factory: () => Promise<T>): Promise<T> {
    const existing = this.inflight.get(key);
    if (existing) {
      L.debug(`Deduped in-flight request: ${key}`);
      return existing as Promise<T>;
    }
    const p = factory().finally(() => this.inflight.delete(key));
    this.inflight.set(key, p);
    return p;
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Public API
  // ─────────────────────────────────────────────────────────────────────────

  /** Fetch the tool catalog. Retried, deduplicated. */
  async listTools(): Promise<McpTool[]> {
    L.debug("listTools()");
    const result = await this.deduped("listTools", () =>
      this.sendWithRetry<{ tools?: McpTool[] }>("tools/list", {}, {
        retries: DEFAULTS.maxRetries,
        timeoutMs: DEFAULTS.requestTimeout,
      })
    );
    const tools = result?.tools || [];
    L.info(`listTools → ${tools.length} tools`);
    return tools;
  }

  /**
   * Execute a tool. Mutating tools are never retried: a transient connection
   * loss must not duplicate a scan, LDAP write, or forge. Read-only status
   * probes are safe to retry with the standard 5s→60s backoff.
   */
  async callTool(name: string, args: Record<string, any>): Promise<McpToolResult> {
    L.info(`callTool(${name})`, { args });
    const done = L.time(`callTool(${name})`);

    // ReAct/forge operations are intentionally long-lived. Their server-side
    // LLM floor is 40s, so the old 30s UI timeout guaranteed a false failure
    // before even a slow first token could arrive. Direct calls retain a five
    // minute budget; the GUI uses munin_chat async mode and polls progress.
    const longRunningTools = /^(munin_chat|tool_forge|graph_forge|munin_wake)$|nmap|nuclei|feroxbuster|ffuf|hydra|sqlmap|katana|screenshotter/i;
    const timeoutMs = longRunningTools.test(name) ? 300_000 : DEFAULTS.requestTimeout;

    if (!this.cb.canRequest()) {
      done("circuit-open");
      throw classified("Circuit breaker OPEN — tool call rejected", "network");
    }

    const idempotentTools = new Set([
      "job_status",
      "list_agent_presence",
      "list_generated_tools",
      "list_generated_graphs",
      "munin_wake_list",
      "episodic_query",
      "memory_list",
    ]);
    const params = { name, arguments: args || {} };
    const mayRetry = idempotentTools.has(name);
    let result: McpToolResult;
    try {
      result = mayRetry
        ? await this.sendWithRetry<McpToolResult>("tools/call", params, {
            retries: DEFAULTS.maxRetries,
            timeoutMs,
          })
        : await this.fetchOnce<McpToolResult>("tools/call", params, timeoutMs);
      // sendWithRetry already updates the breaker for each attempted status
      // probe. Direct (possibly mutating) calls reach it only once here.
      if (!mayRetry) this.cb.onSuccess();
    } catch (e: any) {
      done(`error:${(e as ClassifiedError).kind ?? "unknown"}`);
      if (!mayRetry) this.cb.onFailure((e as ClassifiedError).kind ?? "unknown");
      L.error(`callTool(${name}) threw`, e, { hint: (e as ClassifiedError).hint });
      throw e;
    }

    const isError = result?.isError === true;
    if (isError) {
      done("tool-isError");
      L.warn(`callTool(${name}) returned isError=true`, {
        content: result?.content?.slice(0, 2),
      });
    } else {
      done("ok");
      L.debug(`callTool(${name}) success`, { contentItems: result?.content?.length });
    }
    return { ...result, _raw: result };
  }

  /** Lightweight connectivity check. Retried, deduplicated. */
  async ping(): Promise<boolean> {
    L.debug("ping()");
    return this.deduped("ping", async () => {
      // Try MCP ping first; fall back to tools/list if not implemented
      try {
        await this.sendWithRetry("ping", {}, {
          retries: 1,
          timeoutMs: 8_000,
        });
        L.info("ping → ok (native)");
        return true;
      } catch (e: any) {
        const err = e as ClassifiedError;
        if (err.kind === "auth" || err.kind === "not-found") throw e; // fatal
        L.debug("ping/native failed — trying tools/list probe");
        try {
          await this.sendWithRetry("tools/list", {}, {
            retries: 1,
            timeoutMs: 8_000,
          });
          L.info("ping → ok (via tools/list)");
          return true;
        } catch (e2) {
          L.warn("ping completely failed", { native: err.message });
          return false;
        }
      }
    });
  }

  /** Diagnostic info for debugging panels. */
  diagnostics() {
    return {
      baseUrl: this.baseUrl,
      hasToken: !!this.token,
      circuit: this.cb.snapshot(),
      inflightCount: this.inflight.size,
    };
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Singleton
// ─────────────────────────────────────────────────────────────────────────────

let _client: McpClient | null = null;

export function getMcpClient(config: McpConfig): McpClient {
  if (!_client) {
    _client = new McpClient(config);
  } else {
    _client.setConfig(config);
  }
  return _client;
}

// ─────────────────────────────────────────────────────────────────────────────
// Result parser
// ─────────────────────────────────────────────────────────────────────────────

/** Normalize MCP tool result content into usable text + parsed JSON. */
export function extractToolResultContent(result: McpToolResult): {
  text: string;
  json: any;
  isError: boolean;
} {
  const isError = result?.isError === true;
  const content = result?.content || [];
  let text = "";
  let json: any = undefined;

  for (const item of content) {
    if (item?.type === "text" && typeof item.text === "string") {
      text += item.text;
      const trimmed = item.text.trim();
      if (
        (trimmed.startsWith("{") && trimmed.endsWith("}")) ||
        (trimmed.startsWith("[") && trimmed.endsWith("]"))
      ) {
        try { json = JSON.parse(trimmed); } catch { /* leave as text */ }
      }
    } else if (item?.type === "json" || item?.json !== undefined) {
      json = item.json;
      text += JSON.stringify(item.json, null, 2);
    }
  }

  if (json === undefined && text) json = text;
  return { text, json, isError };
}
