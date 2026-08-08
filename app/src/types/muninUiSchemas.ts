// tags: [munin-ui-schemas, zod, ui-message-parts, versioned-schemas, munin-ui-v1, schema-validation, PR-2F, tool-invocation, command-output, operational-trace, hitl-request, artifact, reasoning, plan, guidance-lifecycle]
// -----------------------------------------------------------------------------
// munin-ui/v1 — versioned Zod schemas for the Munin BFF data-part contract.
//
// The Python backend emits SSE envelopes that the Next.js BFF translates to AI
// SDK v5 ``UIMessageChunk`` frames, which the client stores as data parts. The
// shapes consumed by the per-type renderers (see ``rendererRegistry.tsx`` from
// PR-2G) are validated here at the BFF boundary so an unknown payload degrades
// to a versioned error attribute instead of crashing the live console tree.
//
// Discriminator: every schema uses ``type`` as the literal discriminator, so
// the union ``MuninUiV1Part`` is built with ``z.discriminatedUnion``. Where a
// backend payload's shape is not yet fully specified (or has shipped across
// multiple versions), the field is typed ``z.unknown()`` rather than guessing
// — strict schemas reject real payloads; ``z.unknown()`` accepts them while
// still pinning the discriminator and structural envelope.
//
// The renderer key (``type``) for each part matches the strings used in the
// 2.F/2.G renderer registry so a future bulk-renderer can do a single
// ``schema.safeParse`` against the part before dispatching to the trusted
// component. See ``app/src/lib/rendererRegistry.tsx`` for the dispatch map.
// -----------------------------------------------------------------------------
import { z } from "zod";
import type { ZodTypeAny } from "zod";

// ---------------------------------------------------------------------------
// Discriminator constants — shared with the renderer registry (PR-2G).
// ---------------------------------------------------------------------------

/**
 * The union of known ``munin-ui/v1`` data part discriminator keys.
 * Adding a new renderer means adding a string here + a new schema below.
 */
export const MUNIN_UI_V1_PART_TYPES = [
  "tool-invocation",
  "command-output",
  "operational-trace",
  "hitl-request",
  "artifact",
  "reasoning",
  "plan",
  "guidance-lifecycle",
] as const;

export type MuninUiV1PartType = (typeof MUNIN_UI_V1_PART_TYPES)[number];

/**
 * Versioned schema-version attribute attached to every emitted part so a
 * future munin-ui/v2 can coexist with v1 payloads during a migration.
 */
export const MUNIN_UI_V1_VERSION = "munin-ui/v1" as const;

// ---------------------------------------------------------------------------
// Individual part schemas.
//
// These mirror the ``UIMessageChunk`` type names emitted by the BFF
// translator (``app/src/lib/chat/translator.ts``). Where the BFF emits the
// native AI SDK chunk shapes directly (text/reasoning/dynamic-tool/step-start),
// the corresponding v1 schema covers the data the renderer component actually
// consumes — not the transient delta chunk.
//
// Tool invocations are rendered from the AI SDK ``dynamic-tool`` UIMessage
// part (which the AI SDK normalizes from the tool-input/tool-output chunks).
// We model the renderer input as the on-screen "tool invocation" data shape.
// ---------------------------------------------------------------------------

export const toolInvocationSchema = z.object({
  type: z.literal("tool-invocation"),
  toolCallId: z.string(),
  toolName: z.string(),
  // Three terminal states cover the rendered tool card; the streaming
  // ``input-streaming`` state is the same component with ``partial-call``.
  state: z.union([
    z.literal("partial-call"),
    z.literal("call"),
    z.literal("result"),
  ]),
  // ``input`` arrives on call; result/errorText on terminal. Both stay
  // ``z.unknown()`` because tools have arbitrary schema-shaped arguments.
  input: z.unknown().optional(),
  result: z.unknown().optional(),
  errorText: z.string().optional(),
});

export const commandOutputSchema = z.object({
  type: z.literal("command-output"),
  jobId: z.string().optional(),
  toolCallId: z.string().optional(),
  toolName: z.string(),
  stream: z.union([z.literal("stdout"), z.literal("stderr"), z.literal("meta")]).optional(),
  text: z.string(),
  sequence: z.number().int().nonnegative().optional(),
  elapsedMs: z.number().int().nonnegative().optional(),
  final: z.boolean().optional(),
});

export const operationalTraceSchema = z.object({
  type: z.literal("operational-trace"),
  stage: z.string(),
  text: z.string(),
});

export const hitlRequestSchema = z.object({
  type: z.literal("hitl-request"),
  requestId: z.string(),
  toolName: z.string().optional(),
  // Arguments vary by action; keep loose so a new HITL action does not
  // require a v2 schema bump.
  args: z.record(z.string(), z.unknown()).optional(),
  nonce: z.string().optional(),
  choices: z.array(z.string()).optional(),
  resolved: z.boolean().optional(),
  resolution: z.union([z.literal("approved"), z.literal("rejected")]).optional(),
});

export const artifactSchema = z.object({
  type: z.literal("artifact"),
  artifactId: z.string(),
  mimeType: z.string().optional(),
  uri: z.string().optional(),
});

export const reasoningSchema = z.object({
  type: z.literal("reasoning"),
  id: z.string().optional(),
  // Either the full reasoning text (terminal) or a single delta (streaming).
  // The renderer switches on which is present, so both stay optional.
  text: z.string().optional(),
  delta: z.string().optional(),
  step: z.number().int().nonnegative().optional(),
  provider: z.string().optional(),
});

export const planSchema = z.object({
  type: z.literal("plan"),
  // A nullable ``goal`` so a planless turn still validates; the durable
  // ``goal`` shape is not finalised in v1, so keep the inner object loose.
  goal: z
    .object({
      id: z.string().optional(),
      objective: z.string().optional(),
      state: z.string().optional(),
      successCriteria: z.array(z.string()).optional(),
      deadlineMs: z.number().int().nullable().optional(),
      scope: z.record(z.string(), z.unknown()).optional(),
      budget: z.record(z.string(), z.unknown()).optional(),
    })
    .nullable()
    .optional(),
  items: z
    .array(
      z.object({
        id: z.string(),
        title: z.string(),
        status: z.string(),
        priority: z.string().optional(),
        dependencies: z.array(z.string()).optional(),
        hypothesis: z.string().optional(),
        evidence: z.string().optional(),
        owner: z.string().optional(),
        changeReason: z.string().optional(),
        updatedAtMs: z.number().int().nonnegative().optional(),
      }),
    )
    .optional(),
  updatedAtMs: z.number().int().nonnegative().optional(),
});

// PR-2D — durable ``guidance.<state>`` lifecycle event surfaced over SSE.
// The Python backend emits ``kind: "guidance_lifecycle"`` envelopes (see
// ``_envelope_from_event`` in ``munin/production/chat.py``); the BFF routes
// them through this schema before they reach the renderer registry.
export const guidanceLifecycleSchema = z.object({
  type: z.literal("guidance-lifecycle"),
  // Six lifecycle states — mirrors the SQLite CHECK constraint on
  // ``run_guidance_queue.state``: queued / delivered_to_runtime /
  // applied_to_model_step / expired / superseded / undelivered.
  state: z.union([
    z.literal("queued"),
    z.literal("delivered_to_runtime"),
    z.literal("applied_to_model_step"),
    z.literal("expired"),
    z.literal("superseded"),
    z.literal("undelivered"),
  ]),
  guidanceId: z.string(),
  appliedMessageId: z.string().optional(),
  supersededById: z.string().optional(),
  deliveredAtStep: z.number().int().nonnegative().nullable().optional(),
  actorId: z.string().optional(),
  runId: z.string().optional(),
});

// ---------------------------------------------------------------------------
// Discriminated union — the top-level ``MuninUiV1Part``.
// ---------------------------------------------------------------------------

export const muninUiV1Schemas = {
  "tool-invocation": toolInvocationSchema,
  "command-output": commandOutputSchema,
  "operational-trace": operationalTraceSchema,
  "hitl-request": hitlRequestSchema,
  artifact: artifactSchema,
  reasoning: reasoningSchema,
  plan: planSchema,
  "guidance-lifecycle": guidanceLifecycleSchema,
} as const;

export const muninUiV1PartSchema = z.discriminatedUnion("type", [
  toolInvocationSchema,
  commandOutputSchema,
  operationalTraceSchema,
  hitlRequestSchema,
  artifactSchema,
  reasoningSchema,
  planSchema,
  guidanceLifecycleSchema,
]);

export type MuninUiV1Part = z.infer<typeof muninUiV1PartSchema>;
export type ToolInvocationPart = z.infer<typeof toolInvocationSchema>;
export type CommandOutputPart = z.infer<typeof commandOutputSchema>;
export type OperationalTracePart = z.infer<typeof operationalTraceSchema>;
export type HitlRequestPart = z.infer<typeof hitlRequestSchema>;
export type ArtifactPart = z.infer<typeof artifactSchema>;
export type ReasoningPart = z.infer<typeof reasoningSchema>;
export type PlanPart = z.infer<typeof planSchema>;
export type GuidanceLifecyclePart = z.infer<typeof guidanceLifecycleSchema>;

// ---------------------------------------------------------------------------
// Helper — pick the matching v1 schema for a raw ``data`` part coming through
// the BFF translator. Returns ``null`` when the discriminator is unknown.
// ---------------------------------------------------------------------------

/**
 * Resolve the matching ``munin-ui/v1`` schema for a translator-emitted
 * ``UIMessageChunk`` (or the raw data-part object that the chunk settles into
 * on the client). The ``type`` discriminator is read from the chunk; data
 * chunk shapes that are not part of munin-ui/v1 (text-start, tool-input-*,
 * step-start, finish, etc.) return ``null`` so the caller treats them as
 * pass-through instead of a validation failure.
 */
export function schemaForV1PartType(
  type: unknown,
): ZodTypeAny | null {
  if (typeof type !== "string") return null;
  return muninUiV1Schemas[type as MuninUiV1PartType] ?? null;
}
