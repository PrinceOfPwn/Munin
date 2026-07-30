import type { ComponentType } from "react";

import { ReasoningPart } from "@/components/chat/blocks/parts/ReasoningPart";
import { ToolInvocationPart } from "@/components/chat/blocks/parts/ToolInvocationPart";
import { SubagentPresencePart } from "@/components/chat/blocks/parts/SubagentPresencePart";
import { HitlRequestPart } from "@/components/chat/blocks/parts/HitlRequestPart";
import { ArtifactPart } from "@/components/chat/blocks/parts/ArtifactPart";
import { HeartbeatPart } from "@/components/chat/blocks/parts/HeartbeatPart";
import { NotePart } from "@/components/chat/blocks/parts/NotePart";
import { GuidancePart } from "@/components/chat/blocks/parts/GuidancePart";
import { ParallelToolPart } from "@/components/chat/blocks/parts/ParallelToolPart";

/**
 * Maps a part `type` (for standard AI SDK parts) or a custom part's `id`
 * field (for `type: "custom"` parts) to its React renderer component.
 *
 * Standard AI SDK part types:
 *   - `"reasoning"` — comes from backend `kind: "reasoning"`
 *   - `"tool-invocation"` — comes from backend `kind: "tool_intent"` / `tool_started` / `tool_result` / `tool_failed`
 *
 * Custom part ids (all have `type: "custom"` in the stream):
 *   - `"subagent-presence"` — subagent_started / subagent_state
 *   - `"hitl-request"` — human_request / human_resolved
 *   - `"artifact"` — artifact
 *   - `"heartbeat"` — heartbeat
 *   - `"note"` — note
 *   - `"guidance"` — guidance
 *   - `"parallel-tool"` — grouped parallel tool invocations (synthetic, assembled client-side)
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const partRenderers: Record<string, ComponentType<any>> = {
  // Standard AI SDK parts
  reasoning: ReasoningPart,
  "tool-invocation": ToolInvocationPart,

  // Custom parts (keyed by the `id` field of the custom part)
  "subagent-presence": SubagentPresencePart,
  "hitl-request": HitlRequestPart,
  artifact: ArtifactPart,
  heartbeat: HeartbeatPart,
  note: NotePart,
  guidance: GuidancePart,

  // Synthetic client-side grouping
  "parallel-tool": ParallelToolPart,
};

/**
 * Look up the renderer for a given AI SDK part.
 * For `type: "custom"` parts, dispatches on `part.id`.
 * For all others, dispatches on `part.type`.
 *
 * Returns undefined when no renderer is registered.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function getPartRenderer(part: any): ComponentType<any> | undefined {
  if (!part || typeof part !== "object") return undefined;
  const key =
    part.type === "custom" ? (part.id as string) : (part.type as string);
  return partRenderers[key];
}
