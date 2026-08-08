// tags: [api-route, bff-proxy, server-side, ai-sdk, vercel-ai, use-chat, b-a-c-k-e-n-d, g-e-t, d-o-t-t-e-d--k-i-n-d--m-a-p, p-o-s-t, cancel-proxy, schema-validation, munin-ui-v1, guidance-lifecycle, PR-2C, PR-2F]
import { createUIMessageStream, createUIMessageStreamResponse } from "ai";
import type { UIMessageChunk } from "ai";
import { NextRequest, NextResponse } from "next/server";

import { createTranslator, type BackendEnvelope } from "@/lib/chat/translator";
import { logError } from "@/lib/logError";
import { schemaForV1PartType } from "@/types/muninUiSchemas";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 14400;

const BACKEND = (process.env.MUNIN_PRODUCTION_API_URL || "http://127.0.0.1:8787").replace(/\/+$/, "");

// ---------------------------------------------------------------------------
// Envelope shape shared with the Python backend
// ---------------------------------------------------------------------------
// The Python `/api/chat` SSE stream emits Munin envelopes directly (flat
// object per `data:` line). We still normalize the dotted `run_events` shape
// so we can also consume `/api/chat/{id}/guidance` responses or a future
// replay stream on this branch without introducing a second translator.

const DOTTED_KIND_MAP: Record<string, BackendEnvelope["kind"]> = {
  "agent.text": "assistant_text",
  "agent.activity": "activity",
  "agent.reasoning": "reasoning",
  "reasoning.provider_reasoning": "provider_reasoning",
  "reasoning.operational_summary": "activity",
  "reasoning.operator_guidance": "guidance",
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
      resolvedKind = event.kind.endsWith("provider_reasoning")
        ? "provider_reasoning"
        : "reasoning";
    } else if (event.kind.startsWith("human_request.")) {
      resolvedKind = "human_request";
    } else if (event.kind.startsWith("run.")) {
      resolvedKind = "run_state";
    } else {
      resolvedKind = event.kind as BackendEnvelope["kind"];
    }
  }

  const normalized: BackendEnvelope = { kind: resolvedKind };

  const topLevelFields = ["run_id", "sequence", "text", "content", "stage", "tool_name", "tool_call_id", "input",
    "output", "error", "subagent_id", "name", "state", "request_id", "args", "resolution",
    "nonce", "choices", "artifact_id", "mime_type", "uri", "ts", "elapsed_seconds", "provider", "step",
    "job_id", "stream", "elapsed_ms", "final", "last_output_ms", "transient"];

  // Cast via `unknown` because BackendEnvelope is a typed interface without an
  // index signature; assigning dynamic string-keyed fields onto it requires an
  // explicit narrowing that `as Record<string, unknown>` alone refuses under
  // strict mode.
  const normalizedRecord = normalized as unknown as Record<string, unknown>;
  for (const field of topLevelFields) {
    if (field in event && event[field] !== undefined) {
      normalizedRecord[field] = event[field];
    }
  }

  if (event.payload && typeof event.payload === "object") {
    Object.assign(normalizedRecord, event.payload);
  }

  // Durable production events use `human_request_id` and a choice string;
  // the UIMessage contract deliberately uses one stable id and an explicit
  // approved/rejected resolution. Normalizing here means live and replayed
  // HITL cards follow exactly the same protocol.
  if (event.kind === "human_request.resolved") {
    normalized.kind = "human_resolved";
    if (!normalized.request_id && typeof normalizedRecord.human_request_id === "string") {
      normalized.request_id = normalizedRecord.human_request_id;
    }
    if (!normalized.resolution && typeof normalizedRecord.choice === "string") {
      const choice = normalizedRecord.choice.trim().toLowerCase();
      normalized.resolution = choice.startsWith("reject") || choice.startsWith("deny") || choice.startsWith("cancel")
        ? "rejected"
        : "approved";
    }
  }

  return normalized;
}

// ---------------------------------------------------------------------------
// PR-2F — munin-ui/v1 schema validation at the BFF boundary.
// ---------------------------------------------------------------------------
// The Python backend emits a durable ``BackendEnvelope`` per SSE frame; the
// route normalizes the dotted ``run_events`` shape before handing it to the
// translator. Around that boundary we run the matching ``munin-ui/v1`` Zod
// schema so a malformed or unknown payload degrades to a logged validation
// error instead of crashing the live console tree.
//
// The mapping below is intentionally narrow: it covers the eight renderer
// keys defined in ``muninUiSchemas.ts`` and ignores every other envelope
// kind (text streaming, heartbeat, run-state, finish, etc.) that is not
// part of the munin-ui/v1 data-part contract. A failed ``safeParse`` is
// logged via ``logError`` (never swallowed) and the envelope is still
// forwarded so the user keeps seeing the live stream — the renderer
// registry falls back to an annotated ErrorBoundary card for unknown
// payloads (PR-2G).

const ENVELOPE_KIND_TO_V1_RENDERER: Record<BackendEnvelope["kind"], string | null> = {
  assistant_text: null,            // text streaming — not a data part.
  provider_reasoning: "reasoning",
  reasoning: "reasoning",
  activity: "operational-trace",
  tool_intent: "tool-invocation",
  tool_started: null,              // streaming input — same component below.
  tool_result: "tool-invocation",
  tool_completed: "tool-invocation",
  tool_failed: "tool-invocation",
  tool_output: "command-output",
  tool_heartbeat: null,            // metadata only — no renderer data part.
  subagent_started: null,          // subagents are not in v1 yet.
  subagent_state: null,
  human_request: "hitl-request",
  human_resolved: "hitl-request",
  artifact: "artifact",
  run_state: null,                  // metadata — handled inline.
  heartbeat: null,
  note: null,                       // note has no v1 renderer — skip.
  guidance: null,                   // legacy reasoning.operator_guidance text.
  guidance_lifecycle: "guidance-lifecycle",
  plan: "plan",
  todo: null,                        // todo mutation not in v1 (separate card).
  replan: null,
  hypothesis: null,
  goal: null,
  timer_tick: null,
};

/**
 * Map a normalized backend envelope to the munin-ui/v1 data-part shape the
 * renderer registry consumes. Returns ``null`` when the envelope maps to no
 * v1 renderer (those flow through with no validation against the schemas).
 */
function envelopeToV1Part(envelope: BackendEnvelope): Record<string, unknown> | null {
  const rendererKey = ENVELOPE_KIND_TO_V1_RENDERER[envelope.kind];
  if (!rendererKey) return null;
  // The renderer component shapes for the v1 schemas use the camelCase
  // fields the translator emits (toolCallId, toolName, requestId, ...).
  // The envelope already carries these as top-level fields after
  // ``normalizeRunEvent`` merged ``payload`` in. The real validation below
  // reuses these fields verbatim and adds the canonical ``type`` so the
  // Zod discriminator fires.
  const rec = envelope as unknown as Record<string, unknown>;
  const part: Record<string, unknown> = { type: rendererKey };
  switch (rendererKey) {
    case "tool-invocation": {
      part.toolCallId = rec.tool_call_id ?? "";
      part.toolName = rec.tool_name ?? "unknown";
      const stateStr = typeof rec.state === "string" ? rec.state : "";
      if (stateStr === "input-streaming") part.state = "partial-call";
      else if (stateStr === "input-available") part.state = "call";
      else if (stateStr === "output-available") part.state = "result";
      else if (stateStr === "output-error") part.state = "result";
      else part.state = "call";
      part.input = rec.input;
      part.result = rec.output;
      part.errorText = rec.error;
      break;
    }
    case "command-output": {
      part.toolName = rec.tool_name ?? "command";
      part.toolCallId = rec.tool_call_id;
      part.jobId = rec.job_id;
      part.stream = rec.stream;
      part.text = rec.text ?? "";
      part.sequence = rec.sequence;
      part.elapsedMs = rec.elapsed_ms;
      part.final = rec.final;
      break;
    }
    case "operational-trace": {
      part.stage = rec.stage ?? "working";
      part.text = rec.text ?? "";
      break;
    }
    case "hitl-request": {
      part.requestId = rec.request_id ?? "";
      part.toolName = rec.tool_name;
      part.args = rec.args;
      part.nonce = rec.nonce;
      part.choices = rec.choices;
      part.resolved = rec.resolution != null;
      if (rec.resolution != null) part.resolution = rec.resolution;
      break;
    }
    case "artifact": {
      part.artifactId = rec.artifact_id ?? "";
      part.mimeType = rec.mime_type;
      part.uri = rec.uri;
      break;
    }
    case "reasoning": {
      part.text = rec.text;
      part.delta = rec.text && typeof rec.text === "string" ? undefined : rec.text;
      part.step = rec.step;
      part.provider = rec.provider;
      break;
    }
    case "plan": {
      part.goal = rec.goal;
      part.items = rec.items;
      part.updatedAtMs = rec.updated_at_ms;
      break;
    }
    case "guidance-lifecycle": {
      part.state = rec.state;
      part.guidanceId = rec.guidance_id;
      part.appliedMessageId = rec.applied_message_id;
      part.supersededById = rec.superseded_by_id;
      part.deliveredAtStep = rec.delivered_at_step;
      part.actorId = rec.actor_id;
      part.runId = rec.run_id;
      break;
    }
    default:
      return null;
  }
  return part;
}

/**
 * Validate a normalized backend envelope against its munin-ui/v1 schema. On
 * failure logs via ``logError`` (per the frontend error contract — never a
 * silent catch) and attaches a versioned ``__muninSchemaError`` attribute so
 * the renderer registry can surface an annotated fallback (PR-2G) instead of
 * silently rendering a broken card. Never throws.
 */
function validateV1Envelope(envelope: BackendEnvelope): BackendEnvelope {
  const part = envelopeToV1Part(envelope);
  if (!part) return envelope; // not a v1-renderable envelope — pass through.
  const schema = schemaForV1PartType(part.type);
  if (!schema) return envelope;
  const result = schema.safeParse(part);
  if (result.success) return envelope;
  // Attach the versioned error attribute so the renderer layer falls back.
  // The envelope is a typed interface without an index signature; cast via
  // ``unknown`` and augment defensively (same idiom used in normalizeRunEvent).
  const annotated = envelope as unknown as Record<string, unknown>;
  annotated.__muninSchemaError = {
    version: "munin-ui/v1",
    rendererKey: part.type,
    issues: result.error.issues.map((issue) => ({
      path: issue.path,
      message: issue.message,
      code: issue.code,
    })),
  };
  logError({
    context: "schema_validation",
    error: result.error,
    meta: { dataPart: part, envelopeKind: envelope.kind, version: "munin-ui/v1" },
    ts: new Date().toISOString(),
  });
  return envelope;
}

// ---------------------------------------------------------------------------
// Auth header forwarding — the Python backend's CSRF guard requires `origin`
// in MUNIN_ALLOWED_ORIGINS and `sec-fetch-site` in {same-origin, same-site}.
// When the Next.js BFF makes a server-to-server fetch those headers are
// absent, so we inject them explicitly — mirroring what the legacy
// /api/production proxy did via MUNIN_PRODUCTION_PROXY_ORIGIN.
// ---------------------------------------------------------------------------

function forwardAuthHeaders(request: NextRequest): Headers {
  const headers = new Headers();
  for (const name of ["cookie", "x-csrf-token", "idempotency-key"] as const) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const proxyOrigin =
    process.env.MUNIN_PRODUCTION_PROXY_ORIGIN ||
    request.headers.get("origin") ||
    new URL(request.url).origin;
  headers.set("origin", proxyOrigin);
  headers.set("sec-fetch-site", "same-origin");
  return headers;
}

// ---------------------------------------------------------------------------
// SSE consumer — decodes `event: run-event` / `data: {envelope}` frames from
// the Python `/api/chat` handler and pipes each envelope through the shared
// translator. Terminates on `event: close`.
// ---------------------------------------------------------------------------

async function pumpChatStream(
  runId: string | null,
  upstream: Response,
  writer: { write: (chunk: UIMessageChunk) => void },
): Promise<void> {
  const effectiveRunId = runId ?? `chat-${Date.now()}`;
  const translator = createTranslator(effectiveRunId);
  writer.write({
    type: "data-run-state",
    id: "run-state",
    data: { state: "started", runId: effectiveRunId },
  });

  if (!upstream.body) {
    writer.write({ type: "error", errorText: `backend has no body (${upstream.status})` });
    return;
  }

  const reader = upstream.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const consumeFrame = (frame: string): boolean => {
    if (frame.includes("event: close")) return true;
    const dataLine = frame.split("\n").find((line) => line.startsWith("data:"));
    if (!dataLine) return false;
    const raw = dataLine.slice("data:".length).trim();
    if (!raw || raw === "[DONE]") return false;
    let envelope: BackendEnvelope | null;
    try {
      envelope = normalizeRunEvent(JSON.parse(raw));
    } catch {
      return false;
    }
    if (!envelope) return false;
    // PR-2F — BFF boundary schema validation. ``validateV1Envelope`` only
    // touches the eight munin-ui/v1 renderer parts; everything else flows
    // through unchanged. Failures annotate the envelope + log via logError
    // and the envelope keeps flowing so the live console tree never breaks.
    envelope = validateV1Envelope(envelope);
    for (const chunk of translator.translate(envelope)) {
      writer.write(chunk);
    }
    return translator.state.finished;
  };
  try {
    for (;;) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });

      // Starlette normally emits LF, but proxies are allowed to normalize
      // SSE framing to CRLF. On EOF there may be no trailing blank line; the
      // final flush below deliberately processes that frame too.
      const frames = buffer.split(/\r?\n\r?\n/);
      buffer = frames.pop() ?? "";
      for (const frame of frames) {
        if (consumeFrame(frame)) return;
      }
      if (done) {
        // A standards-compliant SSE server normally terminates frames with a
        // blank line, but a proxy or tunnel can close immediately after the
        // final data line. Do not lose the last assistant delta in that case.
        if (buffer && consumeFrame(buffer)) return;
        break;
      }
    }
  } finally {
    reader.releaseLock();
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function extractUserText(messages: unknown[]): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i] as {
      role?: string;
      parts?: Array<{ type?: string; text?: string }>;
    };
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

interface OperatorGuidancePart {
  type?: string;
  data?: { body?: unknown; guidance?: unknown; runId?: unknown; targetAgentId?: unknown };
}

function extractOperatorGuidance(
  messages: unknown[],
): { body: string; runId?: string; targetAgentId?: string } | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i] as { role?: string; parts?: OperatorGuidancePart[] };
    if (message?.role !== "user" || !Array.isArray(message.parts)) continue;
    for (const part of message.parts) {
      if (part?.type !== "data-operator-guidance" || !part.data) continue;
      const body = String(part.data.body ?? part.data.guidance ?? "").trim();
      if (!body) continue;
      const runId = part.data.runId ? String(part.data.runId) : undefined;
      const targetAgentId = part.data.targetAgentId ? String(part.data.targetAgentId) : undefined;
      return { body, runId, targetAgentId };
    }
  }
  return null;
}

async function forwardOperatorGuidance(
  runId: string,
  body: string,
  targetAgentId: string | undefined,
  forwardHeaders: Headers,
): Promise<{ ok: boolean; status: number; message?: string }> {
  try {
    const res = await fetch(
      `${BACKEND}/api/chat/${encodeURIComponent(runId)}/guidance`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...Object.fromEntries(forwardHeaders),
        },
        body: JSON.stringify({ body, target_agent_id: targetAgentId }),
        cache: "no-store",
      },
    );
    if (!res.ok) {
      let message = `guidance failed (${res.status})`;
      try {
        const parsed = await res.json();
        message = parsed?.error?.message || message;
      } catch {}
      return { ok: false, status: res.status, message };
    }
    return { ok: true, status: res.status };
  } catch (err) {
    return { ok: false, status: 502, message: `backend unreachable: ${String(err)}` };
  }
}

// ---------------------------------------------------------------------------
// Route handler
// ---------------------------------------------------------------------------

/** POST /api/chat — AI SDK v5 DefaultChatTransport entrypoint.
 *
 * Fase 1a: this is now a single hop to the Python `/api/chat` endpoint,
 * which drives `supervisor_runner` directly. The legacy two-hop dance
 * (POST /turns → GET /events) has been retired; the legacy routes stay
 * live server-side until Fase 2.
 */
export async function POST(request: NextRequest) {
  const pathname = new URL(request.url).pathname;

  // PR-2C: durable run cancellation.  Forwarded to the Python
  // ``/api/chat/{run_id}/cancel`` endpoint; the response is a small JSON
  // object (202 cancelling / 200 terminal / 404 / 403), never an SSE stream,
  // so it bypasses the translator path.  Always forwards auth + CSRF so the
  // server-side participant/CSRF check stays authoritative.
  const cancelMatch = pathname.match(/\/api\/chat\/([^/]+)\/cancel$/);
  if (cancelMatch) {
    let cancelBody: Record<string, unknown> = {};
    // The client sends an empty body; tolerate non-JSON / parse failure.
    try {
      cancelBody = (await request.json()) as Record<string, unknown>;
    } catch {
      // Empty body is valid — Python handler reads no JSON.  Log the
      // malformed-but-acceptable case so a regression is never silent.
      console.error({
        context: "cancel.body",
        error: new Error("malformed cancel JSON body (ignored)"),
        meta: { runId: decodeURIComponent(cancelMatch[1]) },
        ts: new Date().toISOString(),
      });
    }
    try {
      const res = await fetch(
        `${BACKEND}/api/chat/${encodeURIComponent(decodeURIComponent(cancelMatch[1]))}/cancel`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...Object.fromEntries(forwardAuthHeaders(request)),
          },
          body: JSON.stringify(cancelBody),
          cache: "no-store",
        },
      );
      let parsed: Record<string, unknown> = {};
      const raw = await res.text();
      try {
        parsed = JSON.parse(raw) as Record<string, unknown>;
      } catch {
        // Non-JSON 5xx body — surface the raw text so the operator sees it.
        parsed = { error: { message: raw || `cancel failed (${res.status})` } };
      }
      if (!res.ok) {
        const message =
          (typeof parsed?.error === "object" && parsed?.error && "message" in (parsed.error as Record<string, unknown>)
            ? String((parsed.error as { message?: unknown }).message ?? "")
            : "") || `cancel failed (${res.status})`;
        return NextResponse.json({ error: message }, { status: res.status });
      }
      return NextResponse.json(parsed, { status: res.status });
    } catch (err) {
      console.error({
        context: "cancel.proxy",
        error: err,
        meta: { runId: decodeURIComponent(cancelMatch[1]) },
        ts: new Date().toISOString(),
      });
      return NextResponse.json(
        { error: `backend unreachable: ${String(err)}` },
        { status: 502 },
      );
    }
  }

  const guidanceMatch = pathname.match(/\/api\/chat\/([^/]+)\/guidance$/);
  if (guidanceMatch) {
    let guidanceBody: { body?: unknown; guidance?: unknown; target_agent_id?: unknown };
    try {
      guidanceBody = await request.json();
    } catch {
      return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
    }
    const bodyText = String(guidanceBody.body ?? guidanceBody.guidance ?? "").trim();
    if (!bodyText) {
      return NextResponse.json({ error: "body is required" }, { status: 400 });
    }
    const result = await forwardOperatorGuidance(
      decodeURIComponent(guidanceMatch[1]),
      bodyText,
      guidanceBody.target_agent_id ? String(guidanceBody.target_agent_id) : undefined,
      forwardAuthHeaders(request),
    );
    if (!result.ok) {
      return NextResponse.json({ error: result.message ?? "guidance failed" }, { status: result.status });
    }
    return NextResponse.json({ ok: true }, { status: 202 });
  }

  let body: {
    id?: string;
    conversation_id?: string;
    messages?: unknown[];
    messageId?: string;
  };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const conversationId = (body.conversation_id || body.id || "").trim();
  if (!conversationId) {
    return NextResponse.json({ error: "conversation_id is required" }, { status: 400 });
  }

  const forwardHeaders = forwardAuthHeaders(request);
  if (body.messageId && !forwardHeaders.has("idempotency-key")) {
    forwardHeaders.set("idempotency-key", body.messageId);
  }

  // If the client is sending operator guidance mid-run (AI SDK v5
  // data-operator-guidance UI part), forward it out-of-band and return an
  // ack — the guidance is delivered to the live run via the Python
  // OperatorGuidanceMiddleware queue, not through the chat stream.
  const guidance = extractOperatorGuidance(body.messages ?? []);
  if (guidance) {
    if (!guidance.runId) {
      return NextResponse.json(
        { error: "data-operator-guidance requires a runId in `data`" },
        { status: 400 },
      );
    }
    const result = await forwardOperatorGuidance(
      guidance.runId,
      guidance.body,
      guidance.targetAgentId,
      forwardHeaders,
    );
    if (!result.ok) {
      return NextResponse.json({ error: result.message ?? "guidance failed" }, { status: result.status });
    }
    return NextResponse.json({ ok: true }, { status: 202 });
  }

  const content = extractUserText(body.messages ?? []);
  if (!content) {
    return NextResponse.json({ error: "last user message has no text" }, { status: 400 });
  }

  // Single-hop: POST the Python chat endpoint. The response is either an SSE
  // stream (fresh run) or a JSON body (idempotent replay). Handle both.
  let upstream: Response;
  try {
    upstream = await fetch(`${BACKEND}/api/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...Object.fromEntries(forwardHeaders),
      },
      body: JSON.stringify({
        conversation_id: conversationId,
        content,
        message_id: body.messageId,
      }),
      cache: "no-store",
    });
  } catch (err) {
    return NextResponse.json({ error: `backend unreachable: ${String(err)}` }, { status: 502 });
  }

  if (!upstream.ok) {
    let message = `chat failed (${upstream.status})`;
    let parsed: Record<string, unknown> | null = null;
    try {
      parsed = await upstream.json();
      const error = parsed?.error;
      message = (typeof error === "object" && error && "message" in error
        ? String((error as { message?: unknown }).message ?? "")
        : String(error ?? "")) || message;
    } catch {}

    // A detached browser can submit a fresh turn after pressing Stop while
    // the durable executor is still running. Treat that message as guidance,
    // then attach the same AI SDK stream instead of surfacing a permanent 409.
    const activeRunId = typeof parsed?.active_run_id === "string" ? parsed.active_run_id : "";
    if (upstream.status === 409 && activeRunId) {
      const guidance = await forwardOperatorGuidance(
        activeRunId,
        content,
        undefined,
        forwardHeaders,
      );
      if (!guidance.ok) {
        return NextResponse.json({ error: guidance.message ?? message }, { status: guidance.status });
      }
      try {
        upstream = await fetch(`${BACKEND}/api/chat/${encodeURIComponent(conversationId)}/stream`, {
          method: "GET",
          headers: {
            Accept: "text/event-stream",
            ...Object.fromEntries(forwardHeaders),
          },
          cache: "no-store",
        });
      } catch (err) {
        return NextResponse.json({ error: `backend unreachable: ${String(err)}` }, { status: 502 });
      }
      if (!upstream.ok) {
        const replayError = await upstream.text().catch(() => "chat resume failed");
        return NextResponse.json({ error: replayError || "chat resume failed" }, { status: upstream.status });
      }
    } else {
      return NextResponse.json({ error: message }, { status: upstream.status });
    }
  }

  const contentType = upstream.headers.get("content-type") || "";
  if (!contentType.includes("text/event-stream")) {
    // Fallback: the Python backend returned JSON instead of SSE. Post
    // issue-#9 follow-up this should only happen when the store can no
    // longer produce a replay stream for the run (e.g. the run was
    // evicted from both hot and durable). Preserve the pre-follow-up
    // behaviour and surface the JSON as-is so the client can decide
    // whether to resume the previous run manually.
    const parsed = await upstream.json().catch(() => ({}));
    return NextResponse.json(parsed, { status: 200 });
  }

  const runId = upstream.headers.get("x-munin-run-id");
  // Observability breadcrumb: the backend sets ``x-munin-idempotent-replay``
  // when we're re-attaching to an existing run (browser refreshed mid-run).
  // The SSE frame shape is identical to a fresh run, so the translator on
  // the writer below needs no branching — this is just useful in logs.
  const isReplay = upstream.headers.get("x-munin-idempotent-replay") === "true";
  if (isReplay && runId) {
    console.info(`[api/chat] idempotent-replay SSE reconnect run_id=${runId}`);
  }

  const stream = createUIMessageStream({
    execute: async ({ writer }) => {
      try {
        await pumpChatStream(runId, upstream, writer);
      } catch (err) {
        if (!request.signal.aborted) {
          writer.write({ type: "error", errorText: String(err) });
        }
      }
    },
  });
  return createUIMessageStreamResponse({ stream });
}

/** GET /api/chat/{conversationId}/stream — AI SDK `useChat({ resume: true })`.
 * The backend resolves the actor's active run and replays its durable event
 * log. A viewer disconnect must not abort the server-side operation. */
export async function GET(request: NextRequest) {
  const forwardHeaders = forwardAuthHeaders(request);
  let upstream: Response;
  try {
    upstream = await fetch(`${BACKEND}${new URL(request.url).pathname}`, {
      method: "GET",
      headers: {
        Accept: "text/event-stream",
        ...Object.fromEntries(forwardHeaders),
      },
      cache: "no-store",
    });
  } catch (err) {
    return NextResponse.json({ error: `backend unreachable: ${String(err)}` }, { status: 502 });
  }

  if (upstream.status === 204) return new Response(null, { status: 204 });
  if (!upstream.ok) {
    const message = await upstream.text().catch(() => "chat resume failed");
    return NextResponse.json({ error: message || "chat resume failed" }, { status: upstream.status });
  }
  if (!(upstream.headers.get("content-type") || "").includes("text/event-stream")) {
    return NextResponse.json({ error: "backend returned a non-stream resume response" }, { status: 502 });
  }

  const stream = createUIMessageStream({
    execute: async ({ writer }) => {
      try {
        await pumpChatStream(upstream.headers.get("x-munin-run-id"), upstream, writer);
      } catch (err) {
        writer.write({ type: "error", errorText: String(err) });
      }
    },
  });
  return createUIMessageStreamResponse({ stream });
}
