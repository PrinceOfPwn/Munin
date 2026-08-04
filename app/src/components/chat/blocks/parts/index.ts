// tags: [parts-barrel, chat-stream-parts, index, PR-5A, ui-component]
// -----------------------------------------------------------------------------
// PR-5A — barrel export for every chat part renderer.
//
// The registry modules (``../registry.ts`` block registry, and the legacy
// ``@/lib/rendererRegistry`` registrations in ``@/extensions/registry.tsx``)
// import from this single surface so a new part needs exactly two edits: its
// component file and this barrel. Components keep the PR-4A memo contract;
// each export preserves its own prop interface.
// -----------------------------------------------------------------------------

export { ArtifactPart, type ArtifactPartProps } from "./ArtifactPart";
export { CommandOutputPart, type CommandOutputPartProps } from "./CommandOutputPart";
export { GoalPart, type GoalPartProps } from "./GoalPart";
export { GuidancePart, type GuidancePartProps } from "./GuidancePart";
export { HeartbeatPart, type HeartbeatPartProps } from "./HeartbeatPart";
export { HitlRequestPart, type HitlRequestPartProps } from "./HitlRequestPart";
export { IocTablePart, type IocTablePartProps } from "./IocTablePart";
export { MermaidPart, type MermaidPartProps } from "./MermaidPart";
export { NotePart, type NotePartProps } from "./NotePart";
export { OperationalTracePart, type OperationalTracePartProps } from "./OperationalTracePart";
export {
  HypothesisPart,
  PlanSnapshotPart,
  TodoMutationPart,
  type HypothesisPartProps,
  type PlanSnapshotPartProps,
  type TodoMutationPartProps,
} from "./PlanPart";
export { ReasoningPart, type ReasoningPartProps } from "./ReasoningPart";
export {
  SandboxedPreview,
  type SandboxedPreviewProps,
  SANDBOX_ATTRIBUTES,
  SANDBOX_CSP,
  buildSandboxedDocument,
} from "./SandboxedPreview";
export {
  SubagentPresencePart,
  type SubagentPresencePartProps,
} from "./SubagentPresencePart";
export { TimerTickPart, type TimerTickPartProps } from "./TimerTickPart";
export { ToolHeartbeatPart, type ToolHeartbeatPartProps } from "./ToolHeartbeatPart";
export {
  ToolInvocationPart,
  type ToolInvocationPartProps,
  type ToolInvocationState,
} from "./ToolInvocationPart";
