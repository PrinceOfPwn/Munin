import { createUIMessageStream, createUIMessageStreamResponse } from "ai";
import type { UIMessageChunk } from "ai";
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 14400;

const BACKEND = (process.env.MUNIN_PRODUCTION_API_URL || "http://127.0.0.1:8787").replace(/\/+$/, "");

// ---------------------------------------------------------------------------
// Backend envelope types (production run-event vocabulary)
// ---------------------------------------------------------------------------

export type BackendEnvelopeKind =
  | "reasoning"
  | "tool_intent"
  | "tool_started"
  | "tool_result"
  | "tool_completed"
  | "tool_failed"
  | "subagent_started"
  | "subagent_state"
  | "human_request"
  | "human_resolved"
  | "artifact"
  | "run_state"
  | "heartbeat"
  | "note"
  | "guidance";

export interface BackendEnvelope {
  kind: BackendEnvelopeKind;
  run_id?: string;
  sequence?: number;
  text?: string;
  tool_name?: string;
  tool_call_id?: string;
  input?: Record<string, unknown>;
  output?: string;
  error?: string;
  subagent_id?: string;
  name?: string;
  state?: string;
  request_id?: string;
  args?: Record<string, unknown>;
  resolution?: "approved" | "rejected";
  artifact_id?: string;
  mime_type?: string;
  uri?: string;
  ts?: number;
  elapsed_seconds?: number;
}

// ---------------------------------------------------------------------------
// Envelope → AI SDK v5 UIMessageChunk translation (pure, exported for tests)
// ---------------------------------------------------------------------------

export interface TranslatorState {
  textId: string;
  textStarted: boolean;
  finished: boolean;
}

export function createTranslator(runId: string): {
  state: TranslatorState;
  translate: (envelope: BackendEnvelope) => UIMessageChunk[];
} {
  const state: TranslatorState = {
    textId: `text-${runId}`,
    textStarted: false,
    finished: false,
  };

  function textDeltas(text: string): UIMessageChunk[] {
    const chunks: UIMessageChunk[] = [];
    if (!state.textStarted) {
      state.textStarted = true;
      chunks.push({ type: "text-start", id: state.textId });
    }
    chunks.push({ type: "text-delta", id: state.textId, delta: text });
    return chunks;
  }

  function closeText(): UIMessageChunk[] {
    if (!state.textStarted) return [];
    state.textStarted = false;
    return [{ type: "text-end", id: state.textId }];
  }

  function translate(envelope: BackendEnvelope): UIMessageChunk[] {
    switch (envelope.kind) {
      case "reasoning":
        // Assistant streamed text (never hidden chain-of-thought: the backend
        // only forwards provider-explicit, policy-cleared content).
        return envelope.text ? textDeltas(envelope.text) : [];

      case "tool_intent": {
        if (!envelope.tool_call_id || !envelope.tool_name) return [];
        return [
          { type: "tool-input-start", toolCallId: envelope.tool_call_id, toolName: envelope.tool_name, dynamic: true },
          { type: "tool-input-available", toolCallId: envelope.tool_call_id, toolName: envelope.tool_name, input: envelope.input ?? {}, dynamic: true },
        ];
      }

      case "tool_started":
        if (!envelope.tool_call_id) return [];
        return [{ type: "tool-input-start", toolCallId: envelope.tool_call_id, toolName: envelope.tool_name ?? "unknown", dynamic: true }];

      case "tool_result":
      case "tool_completed":
        if (!envelope.tool_call_id) return [];
        return [{ type: "tool-output-available", toolCallId: envelope.tool_call_id, output: envelope.output ?? "", dynamic: true }];

      case "tool_failed":
        if (!envelope.tool_call_id) return [];
        return [{ type: "tool-output-error", toolCallId: envelope.tool_call_id, errorText: envelope.error ?? "unknown error", dynamic: true }];

      case "subagent_started":
      case "subagent_state":
        if (!envelope.subagent_id) return [];
        return [{
          type: "data-subagent",
          id: `subagent-${envelope.subagent_id}`,
          data: {
            subagentId: envelope.subagent_id,
            name: envelope.name ?? envelope.subagent_id,
            state: envelope.state ?? (envelope.kind === "subagent_started" ? "started" : "unknown"),
          },
        }];

      case "human_request":
        if (!envelope.request_id) return [];
        return [{
          type: "data-hitl-request",
          id: `hitl-${envelope.request_id}`,
          data: { requestId: envelope.request_id, toolName: envelope.tool_name ?? "unknown", args: envelope.args ?? {}, resolved: false },
        }];

      case "human_resolved":
        if (!envelope.request_id) return [];
        return [{
          type: "data-hitl-request",
          id: `hitl-${envelope.request_id}`,
          data: { requestId: envelope.request_id, resolved: true, resolution: envelope.resolution },
        }];

      case "artifact":
        if (!envelope.artifact_id) return [];
        return [{
          type: "data-artifact",
          id: `artifact-${envelope.artifact_id}`,
          data: { artifactId: envelope.artifact_id, mimeType: envelope.mime_type ?? "application/octet-stream", uri: envelope.uri ?? "" },
        }];

      case "run_state": {
        const runState = envelope.state ?? "unknown";
        if (runState === "completed") {
          if (state.finished) return [];
          state.finished = true;
          return [...closeText(), { type: "finish" }];
        }
        if (["failed", "cancelled", "interrupted"].includes(runState)) {
          if (state.finished) return [];
          state.finished = true;
          return [...closeText(), { type: "error", errorText: envelope.error ?? `run ${runState}` }];
        }
        return [{ type: "data-run-state", id: "run-state", data: { state: runState } }];
      }

      case "heartbeat":
        return [{
          type: "data-heartbeat",
          id: "heartbeat",
          data: { ts: envelope.ts ?? Date.now(), elapsedSeconds: envelope.elapsed_seconds },
          transient: true,
        }];

      case "note":
        return envelope.text ? [{ type: "data-note", data: { text: envelope.text } }] : [];

      case "guidance":
        return envelope.text ? [{ type: "data-guidance", data: { text: envelope.text } }] : [];

      default:
        return [];
    }
  }

  return { state, translate };
}

// ---------------------------------------------------------------------------
// Backend SSE pump
// ---------------------------------------------------------------------------

async function pumpRunEvents(
  runId: string,
  writer: { write: (chunk: UIMessageChunk) => void },
  forwardHeaders: Headers,
  signal: AbortSignal,
): Promise<void> {
  const translator = createTranslator(runId);
  writer.write({ type: "data-run-state", id: "run-state", data: { state: "started", runId } });

  const upstream = await fetch(`${BACKEND}/api/runs/${encodeURIComponent(runId)}/events`, {
    headers: { Accept: "text/event-stream", ...Object.fromEntries(forwardHeaders) },
    cache: "no-store",
    signal,
    // @ts-expect-error — Node fetch extension for streaming request bodies
    duplex: "half",
  });
  if (!upstream.ok || !upstream.body) {
    writer.write({ type: "error", errorText: `backend events failed (${upstream.status})` });
    return;
  }

  const reader = upstream.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE frames are separated by a blank line; an `event: close` frame ends
      // the pump, `run-event` frames carry the JSON envelope in `data:`.
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";
      for (const frame of frames) {
        if (frame.includes("event: close")) return;
        const dataLine = frame.split("\n").find((line) => line.startsWith("data:"));
        if (!dataLine) continue;
        const raw = dataLine.slice("data:".length).trim();
        if (!raw || raw === "[DONE]") continue;
        let envelope: BackendEnvelope;
        try {
          envelope = JSON.parse(raw) as BackendEnvelope;
        } catch {
          continue;
        }
        for (const chunk of translator.translate(envelope)) {
          writer.write(chunk);
        }
        if (translator.state.finished) return;
      }
    }
  } finally {
    reader.releaseLock();
  }
}

function forwardAuthHeaders(request: NextRequest): Headers {
  const headers = new Headers();
  for (const name of ["cookie", "x-csrf-token", "idempotency-key"] as const) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  return headers;
}

// ---------------------------------------------------------------------------
// Route handlers
// ---------------------------------------------------------------------------

function extractUserText(messages: unknown[]): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i] as { role?: string; parts?: Array<{ type?: string; text?: string }> };
    if (message?.role !== "user" || !Array.isArray(message.parts)) continue;
    const text = message.parts
      .filter((part) => part?.type === "text" && typeof part.text === "string")
      .map((part) => part.text)
      .join("\n")
      .trim();
    if (text) return text;
  }
  return "";
}

/** POST /api/chat — AI SDK v5 DefaultChatTransport entrypoint. */
export async function POST(request: NextRequest) {
  let body: { id?: string; conversation_id?: string; messages?: unknown[]; messageId?: string };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const conversationId = (body.conversation_id || body.id || "").trim();
  const content = extractUserText(body.messages ?? []);
  if (!conversationId) {
    return NextResponse.json({ error: "conversation_id is required" }, { status: 400 });
  }
  if (!content) {
    return NextResponse.json({ error: "last user message has no text" }, { status: 400 });
  }

  const forwardHeaders = forwardAuthHeaders(request);
  if (body.messageId && !forwardHeaders.has("idempotency-key")) {
    forwardHeaders.set("idempotency-key", body.messageId);
  }

  // 1. Commit the turn in the authoritative backend (creates + dispatches the run).
  let runId: string;
  try {
    const turnRes = await fetch(
      `${BACKEND}/api/conversations/${encodeURIComponent(conversationId)}/turns`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", ...Object.fromEntries(forwardHeaders) },
        body: JSON.stringify({ content }),
        cache: "no-store",
      },
    );
    const payload = await turnRes.json().catch(() => ({}));
    if (!turnRes.ok || payload?.ok === false) {
      const message = payload?.error?.message || `turn failed (${turnRes.status})`;
      return NextResponse.json({ error: message }, { status: turnRes.ok ? 502 : turnRes.status });
    }
    runId = payload?.data?.run?.id;
    if (!runId) throw new Error("missing run id in turn response");
  } catch (err) {
    return NextResponse.json({ error: `backend unreachable: ${String(err)}` }, { status: 502 });
  }

  // 2. Stream the run as an AI SDK v5 UI message stream.
  const stream = createUIMessageStream({
    execute: async ({ writer }) => {
      try {
        await pumpRunEvents(runId, writer, forwardHeaders, request.signal);
      } catch (err) {
        if (!request.signal.aborted) {
          writer.write({ type: "error", errorText: String(err) });
        }
      }
    },
  });
  return createUIMessageStreamResponse({ stream });
}

/** GET /api/chat/runs/:runId/events — resume/replay an active run as a UI stream. */
export async function GET(
  request: NextRequest,
  { params }: { params: { path?: string[] } },
) {
  const path = params.path ?? [];
  const runId = path.length >= 2 && path[0] === "runs" ? path[1] : null;
  if (!runId) {
    return NextResponse.json({ error: "expected /api/chat/runs/:runId/events" }, { status: 400 });
  }
  const forwardHeaders = forwardAuthHeaders(request);
  const stream = createUIMessageStream({
    execute: async ({ writer }) => {
      try {
        await pumpRunEvents(runId, writer, forwardHeaders, request.signal);
      } catch (err) {
        if (!request.signal.aborted) {
          writer.write({ type: "error", errorText: String(err) });
        }
      }
    },
  });
  return createUIMessageStreamResponse({ stream });
}
