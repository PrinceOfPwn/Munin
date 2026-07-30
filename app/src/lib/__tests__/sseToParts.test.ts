import { describe, it, expect } from "vitest";
import { sseEnvelopeToPart } from "@/app/api/chat/[[...path]]/route";
import type { BackendEnvelope } from "@/app/api/chat/[[...path]]/route";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeEnvelope(
  overrides: Partial<BackendEnvelope> & { kind: BackendEnvelope["kind"] }
): BackendEnvelope {
  return { run_id: "run-123", ...overrides };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("sseEnvelopeToPart", () => {
  // --- reasoning ---
  it("maps 'reasoning' to a reasoning part", () => {
    const part = sseEnvelopeToPart(makeEnvelope({ kind: "reasoning", text: "I think..." }));
    expect(part).toMatchObject({ type: "reasoning", text: "I think..." });
    expect((part as { id: string }).id).toBeDefined();
  });

  it("returns null for 'reasoning' with no text", () => {
    const part = sseEnvelopeToPart(makeEnvelope({ kind: "reasoning" }));
    expect(part).toBeNull();
  });

  // --- tool_intent ---
  it("maps 'tool_intent' to a partial-call tool-invocation", () => {
    const part = sseEnvelopeToPart(
      makeEnvelope({
        kind: "tool_intent",
        tool_name: "web_search",
        tool_call_id: "tc-1",
        input: { query: "foo" },
      })
    );
    expect(part).toMatchObject({
      type: "tool-invocation",
      toolCallId: "tc-1",
      toolName: "web_search",
      state: "partial-call",
      args: { query: "foo" },
    });
  });

  it("returns null for 'tool_intent' with missing tool_call_id", () => {
    const part = sseEnvelopeToPart(
      makeEnvelope({ kind: "tool_intent", tool_name: "web_search" })
    );
    expect(part).toBeNull();
  });

  // --- tool_started ---
  it("maps 'tool_started' to a call-state tool-invocation", () => {
    const part = sseEnvelopeToPart(
      makeEnvelope({ kind: "tool_started", tool_call_id: "tc-1", tool_name: "web_search" })
    );
    expect(part).toMatchObject({
      type: "tool-invocation",
      toolCallId: "tc-1",
      state: "call",
    });
  });

  it("returns null for 'tool_started' with missing tool_call_id", () => {
    const part = sseEnvelopeToPart(makeEnvelope({ kind: "tool_started" }));
    expect(part).toBeNull();
  });

  // --- tool_result ---
  it("maps 'tool_result' to a result-state tool-invocation", () => {
    const part = sseEnvelopeToPart(
      makeEnvelope({ kind: "tool_result", tool_call_id: "tc-1", output: "search results" })
    );
    expect(part).toMatchObject({
      type: "tool-invocation",
      toolCallId: "tc-1",
      state: "result",
      result: "search results",
    });
  });

  // --- tool_completed (alias) ---
  it("maps 'tool_completed' same as 'tool_result'", () => {
    const part = sseEnvelopeToPart(
      makeEnvelope({ kind: "tool_completed", tool_call_id: "tc-2", output: "done" })
    );
    expect(part).toMatchObject({ type: "tool-invocation", state: "result" });
  });

  // --- tool_failed ---
  it("maps 'tool_failed' to a result-state tool-invocation with error text", () => {
    const part = sseEnvelopeToPart(
      makeEnvelope({ kind: "tool_failed", tool_call_id: "tc-1", error: "timeout" })
    );
    expect(part).toMatchObject({
      type: "tool-invocation",
      toolCallId: "tc-1",
      state: "result",
    });
    expect((part as { result: string }).result).toMatch(/timeout/);
  });

  // --- subagent_started ---
  it("maps 'subagent_started' to a subagent-presence custom part", () => {
    const part = sseEnvelopeToPart(
      makeEnvelope({ kind: "subagent_started", subagent_id: "sa-1", name: "Recon" })
    );
    expect(part).toMatchObject({
      type: "custom",
      id: "subagent-presence",
      subagentId: "sa-1",
      name: "Recon",
    });
  });

  it("returns null for 'subagent_started' with missing subagent_id", () => {
    const part = sseEnvelopeToPart(makeEnvelope({ kind: "subagent_started", name: "Recon" }));
    expect(part).toBeNull();
  });

  // --- subagent_state ---
  it("maps 'subagent_state' to a subagent-presence custom part with state", () => {
    const part = sseEnvelopeToPart(
      makeEnvelope({ kind: "subagent_state", subagent_id: "sa-1", state: "running" })
    );
    expect(part).toMatchObject({
      type: "custom",
      id: "subagent-presence",
      state: "running",
    });
  });

  // --- human_request ---
  it("maps 'human_request' to a hitl-request custom part", () => {
    const part = sseEnvelopeToPart(
      makeEnvelope({
        kind: "human_request",
        request_id: "req-1",
        tool_name: "exec_shell",
        args: { cmd: "ls" },
      })
    );
    expect(part).toMatchObject({
      type: "custom",
      id: "hitl-request",
      requestId: "req-1",
      toolName: "exec_shell",
      args: { cmd: "ls" },
    });
  });

  it("returns null for 'human_request' with missing request_id", () => {
    const part = sseEnvelopeToPart(makeEnvelope({ kind: "human_request" }));
    expect(part).toBeNull();
  });

  // --- human_resolved ---
  it("maps 'human_resolved' to a hitl-request part with resolution", () => {
    const part = sseEnvelopeToPart(
      makeEnvelope({ kind: "human_resolved", request_id: "req-1", resolution: "approved" })
    );
    expect(part).toMatchObject({
      type: "custom",
      id: "hitl-request",
      requestId: "req-1",
      resolution: "approved",
    });
  });

  // --- artifact ---
  it("maps 'artifact' to an artifact custom part", () => {
    const part = sseEnvelopeToPart(
      makeEnvelope({
        kind: "artifact",
        artifact_id: "art-1",
        mime_type: "application/pdf",
        uri: "https://example.com/report.pdf",
      })
    );
    expect(part).toMatchObject({
      type: "custom",
      id: "artifact",
      artifactId: "art-1",
      mimeType: "application/pdf",
      uri: "https://example.com/report.pdf",
    });
  });

  // --- run_state (terminal) ---
  it("returns terminal sentinel for 'completed' run_state", () => {
    const result = sseEnvelopeToPart(
      makeEnvelope({ kind: "run_state", state: "completed" })
    );
    expect(result).toEqual({ __terminal: true });
  });

  it("returns terminal sentinel for 'failed' run_state", () => {
    const result = sseEnvelopeToPart(
      makeEnvelope({ kind: "run_state", state: "failed" })
    );
    expect(result).toEqual({ __terminal: true });
  });

  it("returns terminal sentinel for 'interrupted' run_state", () => {
    const result = sseEnvelopeToPart(
      makeEnvelope({ kind: "run_state", state: "interrupted" })
    );
    expect(result).toEqual({ __terminal: true });
  });

  it("returns terminal sentinel for 'cancelled' run_state", () => {
    const result = sseEnvelopeToPart(
      makeEnvelope({ kind: "run_state", state: "cancelled" })
    );
    expect(result).toEqual({ __terminal: true });
  });

  it("returns a non-terminal custom part for a non-terminal run_state", () => {
    const result = sseEnvelopeToPart(
      makeEnvelope({ kind: "run_state", state: "running" })
    );
    expect(result).toMatchObject({ type: "custom", id: "run-state" });
    expect(result).not.toHaveProperty("__terminal");
  });

  // --- heartbeat ---
  it("maps 'heartbeat' to a heartbeat custom part", () => {
    const part = sseEnvelopeToPart(makeEnvelope({ kind: "heartbeat", ts: 1_700_000_000 }));
    expect(part).toMatchObject({ type: "custom", id: "heartbeat", ts: 1_700_000_000 });
  });

  it("falls back to current time when ts is missing", () => {
    const before = Math.floor(Date.now() / 1000);
    const part = sseEnvelopeToPart(makeEnvelope({ kind: "heartbeat" })) as {
      ts: number;
    } | null;
    const after = Math.ceil(Date.now() / 1000);
    expect(part).not.toBeNull();
    // ts should be close to now (within the test execution window)
    expect(part!.ts).toBeGreaterThanOrEqual(before - 1);
    expect(part!.ts).toBeLessThanOrEqual(after + 1);
  });

  // --- note ---
  it("maps 'note' to a note custom part", () => {
    const part = sseEnvelopeToPart(makeEnvelope({ kind: "note", text: "Watch this domain" }));
    expect(part).toMatchObject({ type: "custom", id: "note", text: "Watch this domain" });
  });

  it("returns null for 'note' with no text", () => {
    const part = sseEnvelopeToPart(makeEnvelope({ kind: "note" }));
    expect(part).toBeNull();
  });

  // --- guidance ---
  it("maps 'guidance' to a guidance custom part", () => {
    const part = sseEnvelopeToPart(makeEnvelope({ kind: "guidance", text: "Focus on OPSEC" }));
    expect(part).toMatchObject({ type: "custom", id: "guidance", text: "Focus on OPSEC" });
  });

  it("returns null for 'guidance' with no text", () => {
    const part = sseEnvelopeToPart(makeEnvelope({ kind: "guidance" }));
    expect(part).toBeNull();
  });

  // --- unknown kind ---
  it("returns null for an unknown envelope kind", () => {
    const part = sseEnvelopeToPart({ kind: "unknown_future_kind" as never, run_id: "r" });
    expect(part).toBeNull();
  });
});
