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
  | "subagent_started"
  | "subagent_state"
  | "human_request"
  | "human_resolved"
  | "artifact"
  | "run_state"
  | "heartbeat"
  | "note"
  | "guidance"
  | "plan"
  | "todo"
  | "replan"
  | "hypothesis"
  | "goal"
  | "timer_tick";

// Fase 3 (autonomous modes): a durable plan item as rendered by the backend.
export interface PlanItemEnvelope {
  id: string;
  title: string;
  status: "pending" | "in_progress" | "blocked" | "done" | "discarded";
  priority?: "low" | "normal" | "high" | "critical";
  dependencies?: string[];
  hypothesis?: string;
  evidence?: string;
  owner?: "agent" | "operator";
  change_reason?: string;
  updated_at_ms?: number;
}

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
  provider?: string;
  step?: number;
  // Fase 3 (autonomous modes)
  goal?: {
    id?: string;
    objective?: string;
    state?: string;
    success_criteria?: string[];
    deadline_ms?: number | null;
    scope?: Record<string, unknown>;
    budget?: Record<string, unknown>;
  } | null;
  items?: PlanItemEnvelope[];
  updated_at_ms?: number;
  op?: string;
  item?: PlanItemEnvelope;
  reason?: string;
  reset_ids?: string[];
  statement?: string;
  status?: string;
  evidence?: string;
  timer_id?: string;
  timer_kind?: string;
  goal_id?: string;
  tick_count?: number;
  due_at_ms?: number;
  last_tick_at_ms?: number;
}

// ---------------------------------------------------------------------------
// Envelope → AI SDK v5 UIMessageChunk translation (pure, exported for tests)
// ---------------------------------------------------------------------------

export interface TranslatorState {
  textId: string;
  textStarted: boolean;
  reasoningId: string | null;
  finished: boolean;
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

  function reasoningDeltas(text: string, step: number): UIMessageChunk[] {
    const id = `reasoning-${runId}-${step}`;
    const chunks: UIMessageChunk[] = [];
    if (state.reasoningId !== id) {
      chunks.push(...closeReasoning());
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
        return envelope.text ? [...closeReasoning(), {
          type: "data-activity",
          id: `activity-${envelope.sequence ?? `${envelope.stage ?? "event"}-${envelope.text.slice(0, 48)}`}`,
          data: { stage: envelope.stage ?? "working", text: envelope.text },
        }] : [];

      case "tool_intent": {
        if (!envelope.tool_call_id || !envelope.tool_name) return [];
        return [
          ...closeReasoning(),
          { type: "tool-input-start", toolCallId: envelope.tool_call_id, toolName: envelope.tool_name, dynamic: true },
          { type: "tool-input-available", toolCallId: envelope.tool_call_id, toolName: envelope.tool_name, input: envelope.input ?? {}, dynamic: true },
        ];
      }

      case "tool_started":
        if (!envelope.tool_call_id) return [];
        return [...closeReasoning(), { type: "tool-input-start", toolCallId: envelope.tool_call_id, toolName: envelope.tool_name ?? "unknown", dynamic: true }];

      case "tool_result":
      case "tool_completed":
        if (!envelope.tool_call_id) return [];
        return [...closeReasoning(), { type: "tool-output-available", toolCallId: envelope.tool_call_id, output: envelope.output ?? "", dynamic: true }];

      case "tool_failed":
        if (!envelope.tool_call_id) return [];
        return [...closeReasoning(), { type: "tool-output-error", toolCallId: envelope.tool_call_id, errorText: envelope.error ?? "unknown error", dynamic: true }];

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
          return [...closeReasoning(), ...closeText(), runStatePart, { type: "finish" }];
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

      // Fase 3 (autonomous modes): durable plan / goal / timer visibility.
      case "plan":
        return [{
          type: "data-plan",
          id: `plan-${runId}-${envelope.sequence ?? "snapshot"}`,
          data: {
            goal: envelope.goal ?? null,
            items: envelope.items ?? [],
            updatedAtMs: envelope.updated_at_ms ?? 0,
          },
        }];

      case "todo":
        if (!envelope.item) return [];
        return [{
          type: "data-todo",
          id: `todo-${envelope.item.id}-${envelope.op ?? "update"}`,
          data: { op: envelope.op ?? "update", item: envelope.item, reason: envelope.reason ?? "" },
        }];

      case "replan":
        return [{
          type: "data-todo",
          id: `replan-${envelope.sequence ?? Date.now()}`,
          data: { op: "replan", reason: envelope.reason ?? "", resetIds: envelope.reset_ids ?? [] },
        }];

      case "hypothesis":
        if (!envelope.statement) return [];
        return [{
          type: "data-hypothesis",
          id: `hypothesis-${envelope.statement.slice(0, 48)}-${envelope.sequence ?? Date.now()}`,
          data: {
            statement: envelope.statement,
            status: envelope.status ?? "proposed",
            evidence: envelope.evidence ?? "",
          },
        }];

      case "goal":
        return [{
          type: "data-goal",
          id: `goal-${envelope.goal?.id ?? "current"}`,
          data: { goal: envelope.goal ?? null, state: envelope.goal?.state ?? envelope.state ?? "unknown" },
        }];

      case "timer_tick":
        return [{
          type: "data-timer-tick",
          id: `timer-${envelope.timer_id ?? "unknown"}-${envelope.tick_count ?? 0}`,
          data: {
            timerId: envelope.timer_id ?? "",
            timerKind: envelope.timer_kind ?? "",
            goalId: envelope.goal_id ?? "",
            tickCount: envelope.tick_count ?? 0,
            dueAtMs: envelope.due_at_ms ?? 0,
            lastTickAtMs: envelope.last_tick_at_ms ?? 0,
          },
        }];

      default:
        return [];
    }
  }

  return { state, translate };
}
