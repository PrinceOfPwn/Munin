import { createDataStreamResponse } from "ai";
import { NextRequest, NextResponse } from "next/server";

const MUNIN_BACKEND_URL =
  process.env.MUNIN_BACKEND_URL ?? "http://localhost:8000";

// ---------------------------------------------------------------------------
// Types
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
  run_id: string;
  // reasoning
  text?: string;
  // tool_intent
  tool_name?: string;
  tool_call_id?: string;
  input?: Record<string, unknown>;
  // tool_result
  output?: string;
  // tool_failed
  error?: string;
  // subagent_started / subagent_state
  subagent_id?: string;
  name?: string;
  state?: string;
  // human_request
  request_id?: string;
  args?: Record<string, unknown>;
  // human_resolved
  resolution?: "approved" | "rejected";
  // artifact
  artifact_id?: string;
  mime_type?: string;
  uri?: string;
  // heartbeat
  ts?: number;
}

// AI SDK data-stream part shape (serialised as data: json lines by createDataStreamResponse)
export type AiSdkPart =
  | { type: "reasoning"; id: string; text: string }
  | {
      type: "tool-invocation";
      toolCallId: string;
      toolName: string;
      args?: Record<string, unknown>;
      state: "partial-call" | "call" | "result";
      result?: unknown;
    }
  | { type: "custom"; id: string; [key: string]: unknown };

// ---------------------------------------------------------------------------
// Pure mapping function (exported for tests)
// ---------------------------------------------------------------------------

/**
 * Maps a single backend SSE envelope to an AI SDK data-stream part.
 * Returns null for envelopes that should be silently ignored,
 * or the special sentinel `{ __terminal: true }` for stream-ending events.
 */
export function sseEnvelopeToPart(
  envelope: BackendEnvelope
): AiSdkPart | null | { __terminal: true } {
  switch (envelope.kind) {
    case "reasoning":
      if (!envelope.text) return null;
      return {
        type: "reasoning",
        id: `reasoning-${envelope.run_id}-${Date.now()}`,
        text: envelope.text,
      };

    case "tool_intent":
      if (!envelope.tool_call_id || !envelope.tool_name) return null;
      return {
        type: "tool-invocation",
        toolCallId: envelope.tool_call_id,
        toolName: envelope.tool_name,
        args: envelope.input ?? {},
        state: "partial-call",
      };

    case "tool_started":
      if (!envelope.tool_call_id) return null;
      return {
        type: "tool-invocation",
        toolCallId: envelope.tool_call_id,
        toolName: envelope.tool_name ?? "unknown",
        state: "call",
      };

    case "tool_result":
    case "tool_completed":
      if (!envelope.tool_call_id) return null;
      return {
        type: "tool-invocation",
        toolCallId: envelope.tool_call_id,
        toolName: envelope.tool_name ?? "unknown",
        state: "result",
        result: envelope.output,
      };

    case "tool_failed":
      if (!envelope.tool_call_id) return null;
      return {
        type: "tool-invocation",
        toolCallId: envelope.tool_call_id,
        toolName: envelope.tool_name ?? "unknown",
        state: "result",
        result: `ERROR: ${envelope.error ?? "unknown error"}`,
      };

    case "subagent_started":
    case "subagent_state":
      if (!envelope.subagent_id) return null;
      return {
        type: "custom",
        id: "subagent-presence",
        subagentId: envelope.subagent_id,
        name: envelope.name ?? envelope.subagent_id,
        state: envelope.state ?? (envelope.kind === "subagent_started" ? "started" : "unknown"),
      };

    case "human_request":
      if (!envelope.request_id) return null;
      return {
        type: "custom",
        id: "hitl-request",
        requestId: envelope.request_id,
        toolName: envelope.tool_name ?? "unknown",
        args: envelope.args ?? {},
      };

    case "human_resolved":
      if (!envelope.request_id) return null;
      return {
        type: "custom",
        id: "hitl-request",
        requestId: envelope.request_id,
        resolution: envelope.resolution,
      };

    case "artifact":
      if (!envelope.artifact_id) return null;
      return {
        type: "custom",
        id: "artifact",
        artifactId: envelope.artifact_id,
        mimeType: envelope.mime_type ?? "application/octet-stream",
        uri: envelope.uri ?? "",
      };

    case "run_state": {
      const terminalStates = ["completed", "failed", "interrupted", "cancelled"];
      if (terminalStates.includes(envelope.state ?? "")) {
        return { __terminal: true };
      }
      // non-terminal run_state: emit as custom part
      return {
        type: "custom",
        id: "run-state",
        state: envelope.state,
      };
    }

    case "heartbeat":
      return {
        type: "custom",
        id: "heartbeat",
        ts: envelope.ts ?? Date.now(),
      };

    case "note":
      if (!envelope.text) return null;
      return {
        type: "custom",
        id: "note",
        text: envelope.text,
      };

    case "guidance":
      if (!envelope.text) return null;
      return {
        type: "custom",
        id: "guidance",
        text: envelope.text,
      };

    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Route handlers
// ---------------------------------------------------------------------------

function extractRunId(params: string[]): string | null {
  // params from [[...path]] will be like ["runs", "<runId>", "events"]
  // or ["runs", "<runId>", "message"]
  if (params.length >= 2 && params[0] === "runs") {
    return params[1];
  }
  return null;
}

/** GET /api/chat/runs/:runId/events — SSE fan-out */
export async function GET(
  request: NextRequest,
  { params }: { params: { path?: string[] } }
) {
  const path = params.path ?? [];
  const runId = extractRunId(path);

  if (!runId) {
    return NextResponse.json(
      { error: "Missing runId in path. Expected /api/chat/runs/:runId/events" },
      { status: 400 }
    );
  }

  const lastEventId = request.headers.get("Last-Event-ID") ?? undefined;

  const backendUrl = new URL(
    `${MUNIN_BACKEND_URL}/api/runs/${runId}/events`
  );
  if (lastEventId) {
    backendUrl.searchParams.set("last_event_id", lastEventId);
  }

  return createDataStreamResponse({
    execute: async (dataStream) => {
      let backendRes: Response;
      try {
        backendRes = await fetch(backendUrl.toString(), {
          headers: {
            Accept: "text/event-stream",
            "Cache-Control": "no-cache",
            ...(lastEventId ? { "Last-Event-ID": lastEventId } : {}),
          },
        });
      } catch (err) {
        dataStream.writeData({
          type: "custom",
          id: "error",
          message: `Failed to connect to backend: ${String(err)}`,
        });
        return;
      }

      if (!backendRes.ok || !backendRes.body) {
        dataStream.writeData({
          type: "custom",
          id: "error",
          message: `Backend returned ${backendRes.status}`,
        });
        return;
      }

      const reader = backendRes.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";

          for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed.startsWith("data:")) continue;

            const raw = trimmed.slice("data:".length).trim();
            if (!raw || raw === "[DONE]") continue;

            let envelope: BackendEnvelope;
            try {
              envelope = JSON.parse(raw) as BackendEnvelope;
            } catch {
              continue; // malformed JSON, skip
            }

            const part = sseEnvelopeToPart(envelope);

            if (part === null) continue;

            if ("__terminal" in part) {
              // Signal the stream is done
              return;
            }

            // Write the part to the AI SDK data stream
            if (part.type === "reasoning") {
              dataStream.writeMessageAnnotation({
                type: "reasoning",
                id: part.id,
                text: part.text,
              });
            } else if (part.type === "tool-invocation") {
              dataStream.writeData(part);
            } else {
              // custom parts
              dataStream.writeData(part);
            }
          }
        }
      } finally {
        reader.releaseLock();
      }
    },
    onError: (err) => {
      console.error("[BFF] stream error:", err);
      return `Stream error: ${String(err)}`;
    },
  });
}

/** POST /api/chat/runs/:runId/message — forward message to backend */
export async function POST(
  request: NextRequest,
  { params }: { params: { path?: string[] } }
) {
  const path = params.path ?? [];
  const runId = extractRunId(path);

  if (!runId) {
    return NextResponse.json(
      { error: "Missing runId in path. Expected /api/chat/runs/:runId/message" },
      { status: 400 }
    );
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const backendUrl = `${MUNIN_BACKEND_URL}/api/runs/${runId}/message`;

  let backendRes: Response;
  try {
    backendRes = await fetch(backendUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (err) {
    return NextResponse.json(
      { error: `Failed to forward message to backend: ${String(err)}` },
      { status: 502 }
    );
  }

  const responseBody = await backendRes.text();
  return new NextResponse(responseBody, {
    status: backendRes.status,
    headers: { "Content-Type": "application/json" },
  });
}
