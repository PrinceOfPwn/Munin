/**
 * Tests for the v5 UIMessageStream translator (pure function over backend
 * envelopes - emitted by /api/runs/:runId/events - to AI SDK v5 chunk shapes).
 *
 * The translator lives in app/src/lib/chat/translator.ts and is exercised by
 * the BFF route at app/src/app/api/chat/[[...path]]/route.ts. Keeping these
 * tests decoupled from Next.js / fetch means they run under a plain node
 * vitest environment, no jsdom required.
 */
import { describe, it, expect } from "vitest";
import { createTranslator, type BackendEnvelope } from "@/lib/chat/translator";

function makeEnvelope(
  overrides: Partial<BackendEnvelope> & { kind: BackendEnvelope["kind"] },
): BackendEnvelope {
  return { run_id: "run-123", ...overrides };
}

describe("createTranslator - text part lifecycle", () => {
  it("emits text-start + text-delta for the first reasoning envelope", () => {
    const { translate } = createTranslator("run-x");
    const chunks = translate(makeEnvelope({ kind: "reasoning", text: "Analyzing..." }));
    expect(chunks).toEqual([
      { type: "text-start", id: "text-run-x" },
      { type: "text-delta", id: "text-run-x", delta: "Analyzing..." },
    ]);
  });

  it("reuses the same text id across consecutive reasoning deltas", () => {
    const { translate } = createTranslator("run-x");
    translate(makeEnvelope({ kind: "reasoning", text: "a" }));
    const next = translate(makeEnvelope({ kind: "reasoning", text: "b" }));
    // No new text-start on the second delta
    expect(next).toEqual([{ type: "text-delta", id: "text-run-x", delta: "b" }]);
  });

  it("closes the text part + emits finish on run_state completed", () => {
    const { translate, state } = createTranslator("run-x");
    translate(makeEnvelope({ kind: "reasoning", text: "hi" }));
    const chunks = translate(makeEnvelope({ kind: "run_state", state: "completed" }));
    expect(chunks).toContainEqual({ type: "text-end", id: "text-run-x" });
    expect(chunks).toContainEqual({ type: "finish" });
    expect(state.finished).toBe(true);
  });

  it("emits an error chunk (finito) for failed run_state, closing text first", () => {
    const { translate, state } = createTranslator("run-x");
    translate(makeEnvelope({ kind: "reasoning", text: "boom" }));
    const chunks = translate(makeEnvelope({ kind: "run_state", state: "failed", error: "OOM" }));
    expect(chunks[0]).toEqual({ type: "text-end", id: "text-run-x" });
    expect(chunks[1]).toEqual({ type: "error", errorText: "OOM" });
    expect(state.finished).toBe(true);
  });

  it("is idempotent: a second terminal run_state emits nothing", () => {
    const { translate } = createTranslator("run-x");
    translate(makeEnvelope({ kind: "run_state", state: "completed" }));
    expect(translate(makeEnvelope({ kind: "run_state", state: "completed" }))).toEqual([]);
  });
});

describe("createTranslator - tool parts", () => {
  it("tool_intent emits input-start + input-available (dynamic)", () => {
    const { translate } = createTranslator("run-x");
    const chunks = translate(
      makeEnvelope({
        kind: "tool_intent",
        tool_name: "web_search",
        tool_call_id: "tc-1",
        input: { q: "munin" },
      }),
    );
    expect(chunks).toEqual([
      { type: "tool-input-start", toolCallId: "tc-1", toolName: "web_search", dynamic: true },
      {
        type: "tool-input-available",
        toolCallId: "tc-1",
        toolName: "web_search",
        input: { q: "munin" },
        dynamic: true,
      },
    ]);
  });

  it("tool_started on its own only emits input-start", () => {
    const { translate } = createTranslator("run-x");
    const chunks = translate(
      makeEnvelope({ kind: "tool_started", tool_name: "ldap_search", tool_call_id: "tc-2" }),
    );
    expect(chunks).toEqual([
      {
        type: "tool-input-start",
        toolCallId: "tc-2",
        toolName: "ldap_search",
        dynamic: true,
      },
    ]);
  });

  it("tool_result emits output-available", () => {
    const { translate } = createTranslator("run-x");
    const chunks = translate(
      makeEnvelope({ kind: "tool_result", tool_call_id: "tc-2", output: "bound" }),
    );
    expect(chunks).toEqual([
      {
        type: "tool-output-available",
        toolCallId: "tc-2",
        output: "bound",
        dynamic: true,
      },
    ]);
  });

  it("tool_failed emits tool-output-error with the error text", () => {
    const { translate } = createTranslator("run-x");
    const chunks = translate(
      makeEnvelope({ kind: "tool_failed", tool_call_id: "tc-3", error: "timeout" }),
    );
    expect(chunks).toEqual([
      {
        type: "tool-output-error",
        toolCallId: "tc-3",
        errorText: "timeout",
        dynamic: true,
      },
    ]);
  });

  it("tool envelopes without tool_call_id are ignored", () => {
    const { translate } = createTranslator("run-x");
    expect(translate(makeEnvelope({ kind: "tool_result", output: "x" }))).toEqual([]);
  });
});

describe("createTranslator - data-* parts", () => {
  it("subagent_started maps to data-subagent with name + 'started' state", () => {
    const { translate } = createTranslator("run-x");
    const chunks = translate(
      makeEnvelope({ kind: "subagent_started", subagent_id: "sa-1", name: "Recon" }),
    );
    expect(chunks).toEqual([
      {
        type: "data-subagent",
        id: "subagent-sa-1",
        data: { subagentId: "sa-1", name: "Recon", state: "started" },
      },
    ]);
  });

  it("subagent_state carries the explicit state field", () => {
    const { translate } = createTranslator("run-x");
    const chunks = translate(
      makeEnvelope({ kind: "subagent_state", subagent_id: "sa-1", name: "Recon", state: "running" }),
    );
    expect(chunks[0]).toMatchObject({ type: "data-subagent", data: { state: "running" } });
  });

  it("human_request becomes a data-hitl-request with resolved:false", () => {
    const { translate } = createTranslator("run-x");
    const chunks = translate(
      makeEnvelope({
        kind: "human_request",
        request_id: "req-1",
        tool_name: "execute_command",
        args: { cmd: "ls" },
      }),
    );
    expect(chunks).toEqual([
      {
        type: "data-hitl-request",
        id: "hitl-req-1",
        data: {
          requestId: "req-1",
          toolName: "execute_command",
          args: { cmd: "ls" },
          resolved: false,
        },
      },
    ]);
  });

  it("human_resolved flips resolved:true and carries the resolution", () => {
    const { translate } = createTranslator("run-x");
    const chunks = translate(
      makeEnvelope({ kind: "human_resolved", request_id: "req-1", resolution: "approved" }),
    );
    expect(chunks).toEqual([
      {
        type: "data-hitl-request",
        id: "hitl-req-1",
        data: { requestId: "req-1", resolved: true, resolution: "approved" },
      },
    ]);
  });

  it("artifact maps to data-artifact with id/mime/uri", () => {
    const { translate } = createTranslator("run-x");
    const chunks = translate(
      makeEnvelope({
        kind: "artifact",
        artifact_id: "art-1",
        mime_type: "application/pdf",
        uri: "https://x/y.pdf",
      }),
    );
    expect(chunks).toEqual([
      {
        type: "data-artifact",
        id: "artifact-art-1",
        data: { artifactId: "art-1", mimeType: "application/pdf", uri: "https://x/y.pdf" },
      },
    ]);
  });

  it("heartbeat is transient", () => {
    const { translate } = createTranslator("run-x");
    const chunks = translate(makeEnvelope({ kind: "heartbeat", ts: 1, elapsed_seconds: 0.5 }));
    expect(chunks).toEqual([
      {
        type: "data-heartbeat",
        id: "heartbeat",
        data: { ts: 1, elapsedSeconds: 0.5 },
        transient: true,
      },
    ]);
  });

  it("note and guidance map to data-note / data-guidance", () => {
    const { translate } = createTranslator("run-x");
    expect(translate(makeEnvelope({ kind: "note", text: "hi" }))).toEqual([
      { type: "data-note", data: { text: "hi" } },
    ]);
    expect(translate(makeEnvelope({ kind: "guidance", text: "go left" }))).toEqual([
      { type: "data-guidance", data: { text: "go left" } },
    ]);
  });

  it("non-terminal run_state maps to data-run-state", () => {
    const { translate } = createTranslator("run-x");
    const chunks = translate(makeEnvelope({ kind: "run_state", state: "queued" }));
    expect(chunks).toEqual([
      { type: "data-run-state", id: "run-state", data: { state: "queued" } },
    ]);
  });

  it("envelopes with empty text payloads produce no chunks", () => {
    const { translate } = createTranslator("run-x");
    expect(translate(makeEnvelope({ kind: "reasoning", text: "" }))).toEqual([]);
    expect(translate(makeEnvelope({ kind: "note", text: "" }))).toEqual([]);
  });
});
