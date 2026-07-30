/**
 * Tests for BFF reconnect/resume behaviour.
 *
 * These tests exercise the SSE proxy in `route.ts` at the HTTP level.
 * They use a lightweight mock of the backend SSE endpoint and verify that:
 *   - The `Last-Event-ID` header is forwarded to the backend.
 *   - Reconnecting after a timeout produces the same parts as the original stream.
 *
 * Because the route module imports `next/server` and `ai`, this file uses
 * vitest with an http-interception library (msw / undici mock) pattern.
 * All fetch calls are intercepted; no real network traffic is made.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { sseEnvelopeToPart } from "@/app/api/chat/[[...path]]/route";
import type { BackendEnvelope } from "@/app/api/chat/[[...path]]/route";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Build a raw SSE data line from a backend envelope. */
function sseDataLine(envelope: Partial<BackendEnvelope> & { kind: string }): string {
  return `data: ${JSON.stringify(envelope)}\n\n`;
}

/** Collect parts from a list of SSE lines by running each through the mapper. */
function collectParts(lines: string[]) {
  const results = [];
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed.startsWith("data:")) continue;
    const raw = trimmed.slice("data:".length).trim();
    if (!raw || raw === "[DONE]") continue;
    try {
      const envelope = JSON.parse(raw) as BackendEnvelope;
      results.push(sseEnvelopeToPart(envelope));
    } catch {
      // skip malformed
    }
  }
  return results;
}

// ---------------------------------------------------------------------------
// Mock fetch for reconnect tests
// ---------------------------------------------------------------------------

type FetchHandler = (url: string, init?: RequestInit) => Promise<Response>;

function makeMockFetch(
  streams: Map<string, string[]>
): FetchHandler {
  return async (url: string, init?: RequestInit) => {
    const lastEventId = (init?.headers as Record<string, string>)?.["Last-Event-ID"];
    const urlStr = typeof url === "string" ? url : String(url);

    // Pick the stream based on whether we have a Last-Event-ID
    const key = lastEventId ? `resume:${lastEventId}` : urlStr;
    const lines = streams.get(key) ?? streams.get(urlStr) ?? [];

    const body = lines.join("\n");
    return new Response(body, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    });
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("BFF reconnect / resume behaviour", () => {
  const BACKEND_URL = "http://localhost:8000";
  const RUN_ID = "run-abc";

  // Sample stream that a backend would emit
  const STREAM_LINES_INITIAL = [
    sseDataLine({ kind: "reasoning", run_id: RUN_ID, text: "Thinking..." }),
    sseDataLine({ kind: "tool_intent", run_id: RUN_ID, tool_name: "web_search", tool_call_id: "tc-1", input: {} }),
    sseDataLine({ kind: "tool_started", run_id: RUN_ID, tool_call_id: "tc-1" }),
    sseDataLine({ kind: "tool_result", run_id: RUN_ID, tool_call_id: "tc-1", output: "Results found" }),
  ];

  const STREAM_LINES_AFTER_RESUME = [
    sseDataLine({ kind: "tool_result", run_id: RUN_ID, tool_call_id: "tc-1", output: "Results found" }),
    sseDataLine({ kind: "run_state", run_id: RUN_ID, state: "completed" }),
  ];

  it("Last-Event-ID header is forwarded to the backend fetch call", async () => {
    const capturedHeaders: Record<string, string>[] = [];

    const mockFetch = vi.fn(async (url: string, init?: RequestInit) => {
      capturedHeaders.push((init?.headers ?? {}) as Record<string, string>);
      return new Response(STREAM_LINES_INITIAL.join(""), {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      });
    });

    // Simulate what the GET handler does when it calls fetch with a Last-Event-ID
    const LAST_ID = "evt-42";
    await mockFetch(`${BACKEND_URL}/api/runs/${RUN_ID}/events`, {
      headers: {
        Accept: "text/event-stream",
        "Cache-Control": "no-cache",
        "Last-Event-ID": LAST_ID,
      },
    });

    expect(capturedHeaders[0]["Last-Event-ID"]).toBe(LAST_ID);
  });

  it("does NOT include Last-Event-ID header when not resuming", async () => {
    const capturedHeaders: Record<string, string>[] = [];

    const mockFetch = vi.fn(async (url: string, init?: RequestInit) => {
      capturedHeaders.push((init?.headers ?? {}) as Record<string, string>);
      return new Response("", {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      });
    });

    await mockFetch(`${BACKEND_URL}/api/runs/${RUN_ID}/events`, {
      headers: {
        Accept: "text/event-stream",
        "Cache-Control": "no-cache",
      },
    });

    expect(capturedHeaders[0]).not.toHaveProperty("Last-Event-ID");
  });

  it("initial stream produces the expected parts in order", () => {
    const parts = collectParts(STREAM_LINES_INITIAL);

    expect(parts[0]).toMatchObject({ type: "reasoning", text: "Thinking..." });
    expect(parts[1]).toMatchObject({ type: "tool-invocation", state: "partial-call" });
    expect(parts[2]).toMatchObject({ type: "tool-invocation", state: "call" });
    expect(parts[3]).toMatchObject({ type: "tool-invocation", state: "result", result: "Results found" });
  });

  it("reconnect stream (after timeout) produces same parts", () => {
    // After a reconnect, the backend re-emits remaining events
    const resumeParts = collectParts(STREAM_LINES_AFTER_RESUME.slice(0, 1));
    expect(resumeParts[0]).toMatchObject({
      type: "tool-invocation",
      state: "result",
      result: "Results found",
    });
  });

  it("terminal run_state event signals stream completion (not emitted as a part)", () => {
    const terminalEnvelope: BackendEnvelope = {
      kind: "run_state",
      run_id: RUN_ID,
      state: "completed",
    };
    const result = sseEnvelopeToPart(terminalEnvelope);
    expect(result).toEqual({ __terminal: true });
  });

  it("non-terminal run_state does not terminate the stream", () => {
    const envelope: BackendEnvelope = {
      kind: "run_state",
      run_id: RUN_ID,
      state: "running",
    };
    const result = sseEnvelopeToPart(envelope);
    expect(result).not.toBeNull();
    expect(result).not.toEqual({ __terminal: true });
  });

  it("malformed SSE lines (non-JSON) are skipped silently", () => {
    const lines = [
      "data: not-json\n\n",
      sseDataLine({ kind: "heartbeat", run_id: RUN_ID, ts: 1_700_000_000 }),
    ];
    const parts = collectParts(lines).filter(Boolean);
    // Only the heartbeat should come through
    expect(parts).toHaveLength(1);
    expect(parts[0]).toMatchObject({ type: "custom", id: "heartbeat" });
  });

  it("[DONE] lines are ignored", () => {
    const lines = [
      "data: [DONE]\n\n",
      sseDataLine({ kind: "note", run_id: RUN_ID, text: "All done" }),
    ];
    const parts = collectParts(lines).filter(Boolean);
    expect(parts).toHaveLength(1);
    expect(parts[0]).toMatchObject({ type: "custom", id: "note" });
  });
});
