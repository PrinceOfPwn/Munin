import { createUIMessageStream, createUIMessageStreamResponse } from "ai";
import type { UIMessageChunk } from "ai";
import { NextRequest, NextResponse } from "next/server";

import { createTranslator, type BackendEnvelope } from "@/lib/chat/translator";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 14400;

const BACKEND = (process.env.MUNIN_PRODUCTION_API_URL || "http://127.0.0.1:8787").replace(/\/+$/, "");

// ---------------------------------------------------------------------------
// Backend SSE pump
// ---------------------------------------------------------------------------

const DOTTED_KIND_MAP: Record<string, BackendEnvelope["kind"]> = {
  "agent.reasoning": "reasoning",
  "tool.intent": "tool_intent",
  "tool.started": "tool_started",
  "tool.running": "tool_started",
  "tool.result": "tool_result",
  "tool.completed": "tool_completed",
  "tool.failed": "tool_failed",
  "subagent.started": "subagent_started",
  "subagent.state": "subagent_state",
  "subagent.queued": "subagent_started",
  "human.request": "human_request",
  "human.resolved": "human_resolved",
  "run.state": "run_state",
};

function normalizeRunEvent(raw: unknown): BackendEnvelope | null {
  if (typeof raw !== "object" || !raw) return null;
  const event = raw as { kind?: string; payload?: Record<string, unknown>; [key: string]: unknown };
  if (!event.kind || typeof event.kind !== "string") return null;

  let resolvedKind = DOTTED_KIND_MAP[event.kind];
  if (!resolvedKind) {
    if (event.kind.startsWith("reasoning.")) {
      resolvedKind = "reasoning";
    } else if (event.kind.startsWith("human_request.")) {
      resolvedKind = "human_request";
    } else if (event.kind.startsWith("run.")) {
      resolvedKind = "run_state";
    } else {
      resolvedKind = event.kind as BackendEnvelope["kind"];
    }
  }

  const normalized: BackendEnvelope = { kind: resolvedKind };

  const topLevelFields = ["run_id", "sequence", "text", "tool_name", "tool_call_id", "input",
    "output", "error", "subagent_id", "name", "state", "request_id", "args", "resolution",
    "nonce", "choices", "artifact_id", "mime_type", "uri", "ts", "elapsed_seconds"];

  for (const field of topLevelFields) {
    if (field in event && event[field] !== undefined) {
      (normalized as Record<string, unknown>)[field] = event[field];
    }
  }

  if (event.payload && typeof event.payload === "object") {
    Object.assign(normalized, event.payload);
  }

  return normalized;
}

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
        let envelope: BackendEnvelope | null;
        try {
          const parsed = JSON.parse(raw);
          envelope = normalizeRunEvent(parsed);
        } catch {
          continue;
        }
        if (!envelope) continue;
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
