import type { UIMessageChunk } from "ai";

// ---------------------------------------------------------------------------
// Backend envelope types (production run-event vocabulary)
// ---------------------------------------------------------------------------

export type BackendEnvelopeKind =
  | "assistant_text"
  | "reasoning"
  | "activity"
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
      case "assistant_text":
      case "reasoning":
        // `reasoning` is retained solely to render historical runs created
        // before this protocol split. New backend output uses
        // `assistant_text`; no private chain-of-thought is sent to the UI.
        return envelope.text ? textDeltas(envelope.text) : [];

      case "activity":
        return envelope.text ? [{
          type: "data-activity",
          id: `activity-${envelope.sequence ?? `${envelope.stage ?? "event"}-${envelope.text.slice(0, 48)}`}`,
          data: { stage: envelope.stage ?? "working", text: envelope.text },
        }] : [];

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
          return [...closeText(), runStatePart, { type: "finish" }];
        }
        if (["failed", "cancelled", "interrupted"].includes(runState)) {
          if (state.finished) return [];
          state.finished = true;
          return [
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
