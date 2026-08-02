import type { UIMessageChunk } from "ai";

// ---------------------------------------------------------------------------
// Backend envelope types (production run-event vocabulary)
// ---------------------------------------------------------------------------

export type BackendEnvelopeKind =
  | "assistant_text"
  | "provider_reasoning"
  | "reasoning"
  | "activity"
  | "tool_intent"
  | "tool_started"
  | "tool_result"
  | "tool_completed"
  | "tool_failed"
  | "tool_output"
  | "tool_heartbeat"
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
  content?: string;
  stage?: string;
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
  nonce?: string;
  choices?: string[];
  artifact_id?: string;
  mime_type?: string;
  uri?: string;
  ts?: number;
  elapsed_seconds?: number;
  provider?: string;
  step?: number;
  job_id?: string;
  stream?: "stdout" | "stderr" | "meta";
  elapsed_ms?: number;
  final?: boolean;
  last_output_ms?: number;
  transient?: boolean;
}

// ---------------------------------------------------------------------------
// Envelope → AI SDK v5 UIMessageChunk translation (pure, exported for tests)
// ---------------------------------------------------------------------------

export interface TranslatorState {
  textId: string;
  textStarted: boolean;
  reasoningId: string | null;
  finished: boolean;
  textSegments: string[];
}

export function createTranslator(runId: string): {
  state: TranslatorState;
  translate: (envelope: BackendEnvelope) => UIMessageChunk[];
} {
  const state: TranslatorState = {
    textId: `text-${runId}`,
    textStarted: false,
    reasoningId: null,
    finished: false,
    textSegments: [],
  };

  let currentTextSegment = "";
  let textSegmentNumber = 0;

  function textDeltas(text: string): UIMessageChunk[] {
    const chunks: UIMessageChunk[] = [];
    if (!state.textStarted) {
      state.textStarted = true;
      chunks.push({ type: "text-start", id: state.textId });
    }
    chunks.push({ type: "text-delta", id: state.textId, delta: text });
    currentTextSegment += text;
    return chunks;
  }

  function closeText(): UIMessageChunk[] {
    if (!state.textStarted) return [];
    const id = state.textId;
    state.textStarted = false;
    if (currentTextSegment) {
      state.textSegments.push(currentTextSegment);
      currentTextSegment = "";
    }
    textSegmentNumber += 1;
    state.textId = `text-${runId}-${textSegmentNumber}`;
    return [{ type: "text-end", id }];
  }

  function reasoningDeltas(text: string, step: number): UIMessageChunk[] {
    const id = `reasoning-${runId}-${step}`;
    const chunks: UIMessageChunk[] = [];
    if (state.reasoningId !== id) {
      chunks.push(...closeText(), ...closeReasoning());
      state.reasoningId = id;
      chunks.push({ type: "reasoning-start", id });
    }
    chunks.push({ type: "reasoning-delta", id, delta: text });
    return chunks;
  }

  function closeReasoning(): UIMessageChunk[] {
    if (!state.reasoningId) return [];
    const id = state.reasoningId;
    state.reasoningId = null;
    return [{ type: "reasoning-end", id }];
  }

  function translate(envelope: BackendEnvelope): UIMessageChunk[] {
    function closeForNonText(): UIMessageChunk[] {
      return closeText();
    }

    function finalTextDelta(content: string): string {
      if (!content) return "";
      const emitted = [...state.textSegments, currentTextSegment].join("");
      if (content === currentTextSegment || emitted.endsWith(content)) return "";
      if (currentTextSegment && content.startsWith(currentTextSegment)) {
        return content.slice(currentTextSegment.length);
      }
      if (emitted && content.startsWith(emitted)) return content.slice(emitted.length);
      // Provider adapters can return a final message that omits earlier
      // tool-planning prose.  Avoid duplicating the longest overlap while
      // still preserving a genuinely missing tail.
      // Bound the duplicate-tail search so a long final answer cannot
      // turn this request-path reconciliation into quadratic work.
      const max = Math.min(emitted.length, content.length, 4096);
      for (let size = max; size > 0; size -= 1) {
        if (emitted.endsWith(content.slice(0, size))) return content.slice(size);
      }
      return content;
    }

    switch (envelope.kind) {
      case "assistant_text":
      case "reasoning":
        // `reasoning` is retained solely to render historical runs created
        // before this protocol split. New backend output uses
        // `assistant_text`; no private chain-of-thought is sent to the UI.
        return envelope.text ? [...closeReasoning(), ...textDeltas(envelope.text)] : [];

      case "provider_reasoning":
        // Only provider-emitted thinking arrives here. It is an independent
        // UIMessage part, never concatenated into the assistant answer.
        return envelope.text
          ? reasoningDeltas(envelope.text, Math.max(0, envelope.step ?? 0))
          : [];

      case "activity":
        return envelope.text ? [...closeForNonText(), ...closeReasoning(), {
          type: "data-activity",
          id: `activity-${envelope.sequence ?? `${envelope.stage ?? "event"}-${envelope.text.slice(0, 48)}`}`,
          data: { stage: envelope.stage ?? "working", text: envelope.text },
        }] : [];

      case "tool_intent": {
        if (!envelope.tool_call_id || !envelope.tool_name) return [];
        return [
          ...closeForNonText(), ...closeReasoning(),
          { type: "tool-input-start", toolCallId: envelope.tool_call_id, toolName: envelope.tool_name, dynamic: true },
          { type: "tool-input-available", toolCallId: envelope.tool_call_id, toolName: envelope.tool_name, input: envelope.input ?? {}, dynamic: true },
        ];
      }

      case "tool_started":
        if (!envelope.tool_call_id) return [];
        return [...closeForNonText(), ...closeReasoning(), { type: "tool-input-start", toolCallId: envelope.tool_call_id, toolName: envelope.tool_name ?? "unknown", dynamic: true }];

      case "tool_result":
      case "tool_completed":
        if (!envelope.tool_call_id) return [];
        // LangGraph's lifecycle event does not carry the tool payload. The
        // progress middleware emits the authoritative result separately;
        // avoid replacing it with an empty output part.
        if (envelope.kind === "tool_completed" && envelope.output == null) return [];
        return [...closeForNonText(), ...closeReasoning(), { type: "tool-output-available", toolCallId: envelope.tool_call_id, output: envelope.output ?? "", dynamic: true }];

      case "tool_failed":
        if (!envelope.tool_call_id) return [];
        return [...closeForNonText(), ...closeReasoning(), { type: "tool-output-error", toolCallId: envelope.tool_call_id, errorText: envelope.error ?? "unknown error", dynamic: true }];

      case "tool_output":
        if (!envelope.text) return [];
        return [...closeForNonText(), {
          type: "data-command-output",
          id: `command-output-${envelope.job_id ?? envelope.tool_call_id ?? envelope.tool_name ?? "tool"}-${envelope.sequence ?? 0}`,
          data: {
            jobId: envelope.job_id ?? "",
            toolCallId: envelope.tool_call_id ?? "",
            toolName: envelope.tool_name ?? "unknown",
            stream: envelope.stream ?? "stdout",
            text: envelope.text,
            sequence: envelope.sequence ?? 0,
            elapsedMs: envelope.elapsed_ms ?? 0,
            final: Boolean(envelope.final),
          },
        }];

      case "tool_heartbeat":
        return [{
          type: "data-tool-heartbeat",
          id: `tool-heartbeat-${envelope.job_id ?? envelope.tool_call_id ?? envelope.tool_name ?? "tool"}`,
          data: {
            jobId: envelope.job_id ?? "",
            toolCallId: envelope.tool_call_id ?? "",
            toolName: envelope.tool_name ?? "unknown",
            elapsedMs: envelope.elapsed_ms ?? 0,
            lastOutputMs: envelope.last_output_ms ?? 0,
            text: envelope.text ?? "command still running",
          },
        }];

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
          data: {
            requestId: envelope.request_id,
            toolName: envelope.tool_name ?? "unknown",
            args: envelope.args ?? {},
            nonce: envelope.nonce ?? "",
            choices: envelope.choices ?? [],
            resolved: false,
          },
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
        const runStatePart: UIMessageChunk = {
          type: "data-run-state",
          id: "run-state",
          // Keep the durable run identifier with every state transition. It
          // lets the UI send operator guidance to a recovered stream rather
          // than relying on a process-local identifier.
          data: { state: runState, runId: envelope.run_id ?? runId },
        };
        if (runState === "completed") {
          if (state.finished) return [];
          state.finished = true;
          const missingTail = finalTextDelta(envelope.content ?? "");
          return [
            ...closeReasoning(),
            ...(missingTail ? textDeltas(missingTail) : []),
            ...closeText(),
            runStatePart,
            { type: "finish" },
          ];
        }
        if (["failed", "cancelled", "interrupted"].includes(runState)) {
          if (state.finished) return [];
          state.finished = true;
          return [
            ...closeReasoning(),
            ...closeText(),
            runStatePart,
            { type: "error", errorText: envelope.error ?? `run ${runState}` },
          ];
        }
        return [runStatePart];
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
