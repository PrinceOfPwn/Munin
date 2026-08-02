# Prompt PR-1 (Issue PR B) — Typed UI Protocol: Zod schemas, data parts, reconciliation, replay compatibility

> Issue: #18 · PR breakdown del issue: **PR B — typed UI protocol**
> **Requiere solo `main`.** Paralelizable con PR A (operator-control) y parte de PR E (artifact/workspace).
> Referencia autoritativa: Issue #18 comentarios C3 §6 (Message rendering architecture), C4 §4 (`useChat` integration) y §10 (Type/version discipline), C5 §16 (test matrix).

---

## 1. Objetivo

Definir el contrato frontend tipado de Munin: Zod schemas para todos los Munin `data-*` parts, `messageMetadataSchema`, agrupación por stable ID, reconciliación in-place, tests de compatibilidad con eventos históricos. **No toca UX visual.**

## 2. Rutas permitidas

- `app/src/lib/munin-ui/` (NUEVO directorio):
  - `schemas.ts`
  - `group-parts.ts`
  - `part-registry.tsx`
  - `renderers.tsx` (solo el dispatcher, los component impls viven en `app/src/components/munin-ai/`)
  - `__tests__/schemas.test.ts`
  - `__tests__/group-parts.test.ts`
  - `__tests__/replay-compat.test.ts`
- `app/src/lib/aiChat.ts` (EDIT — añadir `dataPartSchemas` y `messageMetadataSchema` a `useChat`)
- `app/src/lib/chat/translator.ts` (EDIT — añadir guidance lifecycle events, cancellation states, evidence/source/workers/workflow parts; stable IDs; schema_version)
- `app/src/lib/chat/__tests__/translator.test.ts` (EDIT)
- `app/src/components/__tests__/FixtureGallery.test.tsx` (NUEVO)
- `app/src/fixtures/mockParts.ts` (NUEVO)
- `app/src/components/FixtureGallery.tsx` (NUEVO — para visual regression, NO mergeada a rutas productivas)
- `docs/issue-18-ui-contract.md` (NUEVO)
- `changes.md` (AÑADIR)

### Rutas prohibidas
- `munin/**`
- `app/src/components/ai-elements/**` (PR C)
- `app/src/components/munin-ai/**` (PR C y PR D)
- `app/src/renderers/**` (NO existe en este布局; usar `lib/munin-ui/`)

## 3. Schemas Zod (`app/src/lib/munin-ui/schemas.ts`)

Define **todos** los Munin parts. **Mantén nombres EXACTOS** del issue (C4 §4):

```ts
import { z } from "zod";

// Base: stable ID para reconciliación + version para forward-compat
const basePart = z.object({
  id: z.string().min(1),
  schemaVersion: z.literal(1).default(1),
  timestamp_ms: z.number().optional(),
  run_id: z.string().optional(),
});

// === AI SDK core parts (no inventamos, mapeamos) ===
export const textPart = basePart.extend({
  type: z.literal("text"), text: z.string(),
});
export const reasoningPart = basePart.extend({
  type: z.literal("reasoning"), text: z.string(),
  isStreaming: z.boolean().optional(),
});

// === Munin data-* parts (dataPartSchemas map exacto del issue C4 §4) ===
export const activitySchema = basePart.extend({
  type: z.literal("data-activity"),
  stage: z.string(),
  summary: z.string(),
  data: z.record(z.unknown()).optional(),
});

export const runStateSchema = basePart.extend({
  type: z.literal("data-run-state"),
  state: z.enum([
    "queued", "running", "waiting_for_human",
    "cancel_requested", "cancelling", "cancelled",
    "completed", "failed", "interrupted"
  ]),
  error: z.string().optional(),
});

export const commandOutputSchema = basePart.extend({
  type: z.literal("data-command-output"),
  job_id: z.string(),
  tool_call_id: z.string(),
  stream: z.enum(["stdout", "stderr", "meta"]),
  sequence: z.number(),
  chunk: z.string(),
  elapsed_ms: z.number().optional(),
  final: z.boolean().default(false),
});

export const toolHeartbeatSchema = basePart.extend({
  type: z.literal("data-tool-heartbeat"),
  tool_call_id: z.string(),
  elapsed_ms: z.number(),
});

export const subagentSchema = basePart.extend({
  type: z.literal("data-subagent"),
  subagent_id: z.string(),
  subagent_type: z.string(),
  state: z.enum(["start", "running", "complete", "error"]),
  label: z.string().optional(),
  description: z.string().optional(),
  parent_id: z.string().optional(),
  objective: z.string().optional(),
  summary: z.string().optional(),
  duration_ms: z.number().optional(),
});

export const humanRequestSchema = basePart.extend({
  type: z.literal("data-hitl-request"),
  request_id: z.string(),
  nonce: z.string(),
  action: z.string(),               // exact proposed action
  scope: z.string().optional(),
  target: z.string().optional(),
  redacted_args: z.record(z.unknown()).optional(),
  risk: z.enum(["low", "medium", "high", "critical"]).optional(),
  evidence_ref_ids: z.array(z.string()).optional(),
  choices: z.array(z.string()),
  prompt: z.string().optional(),
  expires_at_ms: z.number().optional(),
});

export const artifactSchema = basePart.extend({
  type: z.literal("data-artifact"),
  artifact_id: z.string(),
  title: z.string().optional(),
  filename: z.string(),
  mime_type: z.string().optional(),
  language: z.string().optional(),
  size_bytes: z.number().optional(),
  renderer: z.string().regex(/^[a-z-]+@v?\d+$/, "renderer must be like ioc-table@1"),
  schemaVersion: z.number().default(1),
  provenance: z.object({
    conversation_id: z.string().optional(),
    run_id: z.string().optional(),
    message_id: z.string().optional(),
    tool_call_id: z.string().optional(),
    agent_id: z.string().optional(),
    subagent_id: z.string().optional(),
    parent_artifact_id: z.string().optional(),
    content_hash: z.string().optional(),
    redaction_policy_version: z.string().optional(),
  }).optional(),
  content_uri: z.string().optional(),  // por defecto backend NO embebe content; fetch by id
});

export const guidanceSchema = basePart.extend({
  type: z.literal("data-guidance"),
  guidance_id: z.string(),
  body: z.string(),
  target_agent_id: z.string().optional(),
  state: z.enum([
    "queued",
    "consumed_by_runtime",
    "applied_to_model",
    "run_finished_undelivered",
    "expired",
    "failed",
    "superseded"     // adición a los 6 del issue: cuando un nuevo guidance pisa este
  ]),
  applied_at_step: z.number().optional(),
  failure_reason: z.string().optional(),
  idempotency_key: z.string().optional(),
  created_at_ms: z.number().optional(),
});

export const evidenceSchema = basePart.extend({
  type: z.literal("data-evidence"),
  evidence_id: z.string(),
  kind: z.string(),
  title: z.string(),
  summary: z.string().optional(),
  source_url: z.string().url().optional(),
  artifact_id: z.string().optional(),
  tool_call_id: z.string().optional(),
  confidence: z.number().min(0).max(1).optional(),
  redaction_policy_version: z.string().optional(),
});

export const workerGroupSchema = basePart.extend({
  type: z.literal("data-worker-group"),
  worker_group_id: z.string(),
  label: z.string().optional(),
  total: z.number(),
  running: z.number(),
  completed: z.number(),
  failed: z.number(),
});

export const workflowNodeSchema = basePart.extend({
  type: z.literal("data-workflow-node"),
  node_id: z.string(),
  node_type: z.string(),
  state: z.string(),
  label: z.string().optional(),
  parent_id: z.string().optional(),
});

// === Sources part (C4 §7 Sources) ===
export const sourceSchema = basePart.extend({
  type: z.literal("data-source"),
  source_id: z.string(),
  title: z.string(),
  url: z.string().url().optional(),
  provider: z.string().optional(),
  retrieved_at: z.number(),
  confidence: z.number().min(0).max(1).optional(),
  evidence_id: z.string().optional(),
});

// === Plan & Progress events (C7 §3) - reconciled by stable item ID ===
export const planSchema = basePart.extend({
  type: z.literal("data-plan"),
  plan_id: z.string(),
  goal: z.string(),
  success_criteria: z.array(z.string()).optional(),
  scope: z.string().optional(),
  budget: z.string().optional(),
  items: z.array(z.object({
    id: z.string(),
    label: z.string(),
    status: z.enum(["pending", "active", "blocked", "done", "discarded"]),
    owner: z.enum(["agent", "operator"]).optional(),
    depends_on: z.array(z.string()).optional(),
    priority: z.number().optional(),
  })),
});

export const todoSchema = basePart.extend({
  type: z.literal("data-todo"),
  todo_id: z.string(),
  label: z.string(),
  status: z.enum(["pending", "active", "blocked", "done", "discarded"]),
  parent_plan_id: z.string().optional(),
});

export const replanSchema = basePart.extend({
  type: z.literal("data-replan"),
  replan_id: z.string(),
  parent_plan_id: z.string(),
  reason: z.string(),
  new_items: z.array(z.object({ id: z.string(), label: z.string() })),
});

export const hypothesisSchema = basePart.extend({
  type: z.literal("data-hypothesis"),
  hypothesis_id: z.string(),
  statement: z.string(),
  supporting_evidence_ids: z.array(z.string()).optional(),
  refuting_evidence_ids: z.array(z.string()).optional(),
  status: z.enum(["open", "confirmed", "refuted", "inconclusive"]).optional(),
});

export const goalSchema = basePart.extend({
  type: z.literal("data-goal"),
  goal_id: z.string(),
  statement: z.string(),
  status: z.enum(["active", "achieved", "abandoned"]).optional(),
});

export const timerTickSchema = basePart.extend({
  type: z.literal("data-timer-tick"),
  timer_id: z.string(),
  tick_at_ms: z.number(),
  deadline_ms: z.number().optional(),
});

// === Notes (artifact-scoped operator notes) ===
export const noteSchema = basePart.extend({
  type: z.literal("data-note"),
  note_id: z.string(),
  target_artifact_id: z.string().optional(),
  body: z.string(),
  author: z.string().optional(),
});

// === messageMetadata ===
export const messageMetadataSchema = z.object({
  schemaVersion: z.literal(1).default(1),
  conversation_id: z.string(),
  run_id: z.string().optional(),
  message_sequence: z.number().optional(),
  persisted: z.boolean().default(true),
  created_at_ms: z.number().optional(),
});

// Discriminated union export
export const muninDataPartSchemas = {
  activity: activitySchema,
  runState: runStateSchema,
  commandOutput: commandOutputSchema,
  toolHeartbeat: toolHeartbeatSchema,
  subagent: subagentSchema,
  humanRequest: humanRequestSchema,
  artifact: artifactSchema,
  guidance: guidanceSchema,
  evidence: evidenceSchema,
  workerGroup: workerGroupSchema,
  workflowNode: workflowNodeSchema,
  source: sourceSchema,
  plan: planSchema,
  todo: todoSchema,
  replan: replanSchema,
  hypothesis: hypothesisSchema,
  goal: goalSchema,
  timerTick: timerTickSchema,
  note: noteSchema,
} as const;

export type MuninDataPart = typeof muninDataPartSchemas[keyof typeof muninDataPartSchemas];
```

## 4. `app/src/lib/munin-ui/group-parts.ts`

Reconciliación por stable ID (NO append cada mutation):

```ts
import { UIMessage } from "@ai-sdk/react";

export interface ToolAggregate {
  tool_call_id: string;
  tool_name?: string;
  input?: unknown;
  state: "intent" | "running" | "completed" | "failed";
  output_chunks: { stream: "stdout" | "stderr" | "meta"; sequence: number; chunk: string }[];
  final_result?: string;
  final_error?: string;
  elapsed_ms?: number;
  started_at_ms?: number;
  completed_at_ms?: number;
  artifact_ids: string[];
}

export interface CommandAggregate {
  job_id: string;            // PRIMARY KEY de groupol
  tool_call_id: string;
  command?: string;
  streams: { stdout: string[]; stderr: string[]; meta: string[] };
  elapsed_ms?: number;
  final: boolean;
}

export interface SubagentAggregate {
  subagent_id: string;
  subagent_type: string;
  state: "start" | "running" | "complete" | "error";
  parent_id?: string;
  objective?: string;
  summary?: string;
  duration_ms?: number;
}

export interface GuidanceAggregate {
  guidance_id: string;
  body: string;
  state: "queued"|"consumed_by_runtime"|"applied_to_model"|"run_finished_undelivered"|"expired"|"failed"|"superseded";
  applied_at_step?: number;
  last_update_ms?: number;
}

export interface PlanAggregate {
  plan_id: string;
  goal: string;
  items: Map<string, { id: string; label: string; status: string; owner?: string }>;
  replans: { replan_id: string; reason: string }[];
}

export interface GroupedTurn {
  plans: Map<string, PlanAggregate>;
  todos: Map<string, any>;
  hypotheses: Map<string, any>;
  goals: Map<string, any>;
  tools: Map<string, ToolAggregate>;
  commands: Map<string, CommandAggregate>;
  subagents: Map<string, SubagentAggregate>;
  guidance: Map<string, GuidanceAggregate>;
  artifacts: Map<string, any>;
  evidence: Map<string, any>;
  sources: Map<string, any>;
  notes: Map<string, any>;
  hitl: Map<string, any>;
  activities: { stage: string; summary: string }[];
  assistantText: string[];
  providerReasoning: string[];
  finalAnswer?: string;
}

export function groupPartsByStableId(parts: UIMessage["parts"]): GroupedTurn {
  // Iterar parts en orden, dispatchear por `type`:
  // - tool_intent|tool_started|tool_heartbeat|tool_output|tool_result|tool_failed -> tools/tool_call_id
  // - data-command-output -> commands/job_id (chunk push by stream/sequence)
  // - data-subagent -> subagents/subagent_id (estado upsert)
  // - data-guidance -> guidance/guidance_id (estado upsert)
  // - data-artifact -> artifacts/artifact_id
  // - data-evidence -> evidence/evidence_id
  // - data-source -> sources/source_id
  // - data-plan -> plans/plan_id (items merge por id)
  // - data-todo/data-replan -> plans.items / plans.replans
  // - data-hypothesis -> hypotheses/hypothesis_id
  // - data-hitl-request -> hitl/request_id
  // - text -> assistantText
  // - reasoning -> providerReasoning
  // NO renderiza aquí; solo produce el GroupedTurn.
  throw new Error("TODO implement");
}
```

## 5. `app/src/lib/munin-ui/part-registry.tsx`

```tsx
import { ComponentType } from "react";
import { muninDataPartSchemas } from "./schemas";

export type RendererKey = `${string}@1`;   // ej: "ioc-table@1"

export interface RendererEntry {
  key: RendererKey;
  description: string;
  component: ComponentType<{ data: unknown; provenance?: unknown }>;
  dataSchema: import("zod").ZodType;       // validación específica de data
}

class RendererRegistry {
  private m = new Map<RendererKey, RendererEntry>();
  private fallback: RendererEntry;

  constructor() {
    this.fallback = {
      key: "fallback@1",
      description: "Safe fallback for unknown renderer versions",
      component: ({ data }) => (
        <pre className="p-3 my-2 rounded border border-borderStrong bg-raised text-xs font-mono overflow-x-auto">
          {JSON.stringify(data, null, 2)}
        </pre>
      ),
      dataSchema: import("zod").z.unknown() as any,
    };
  }

  register(entry: RendererEntry) { this.m.set(entry.key, entry); }
  get(key: RendererKey): RendererEntry { return this.m.get(key) ?? this.fallback; }
  list(): RendererKey[] { return Array.from(this.m.keys()); }
}

export const registry = new RendererRegistry();
```

## 6. `app/src/lib/aiChat.ts` — Use `dataPartSchemas` + `messageMetadataSchema`

```ts
import { useChat, DefaultChatTransport } from "@ai-sdk/react";
import { muninDataPartSchemas, messageMetadataSchema } from "@/lib/munin-ui/schemas";

export function useMuninChat(conversationId: string) {
  const chat = useChat({
    id: conversationId,
    transport: new DefaultChatTransport({ api: "/api/chat" }),
    resume: true,
    dataPartSchemas: muninDataPartSchemas,        // ← NUEVO
    messageMetadataSchema,                          // ← NUEVO
    onData: (part) => {
      if (part.type === "data-hitl-request") {
        // toast: "Operator decision required"
      }
      // NO manter store paralelo
    },
    onFinish: (msg) => { /* renderer snapshot cache only */ },
    onError: (err) => {
      // classify: viewer disconnect | backend reject | protocol | terminal
    },
  });

  return {
    messages: chat.messages,
    input: chat.input, setInput: chat.setInput,
    status: chat.status,            // 'submitted'|'streaming'|'ready'|'error'
    stop: chat.stop,                 // === DETACH (PR A relabels the button)
    sendMessage: chat.sendMessage,
    error: chat.error,
  };
}
```

## 7. `app/src/lib/chat/translator.ts` — extender el vocabulario

Map backend envelope kinds → Munin data parts. Hoy 27 kinds. **Añadir**:

- `guidance.queued` → `data-guidance` con `state="queued"`
- `guidance.consumed` → `data-guidance` con `state="consumed_by_runtime"`
- `guidance.applied` → `data-guidance` con `state="applied_to_model"` + `applied_at_step`
- `guidance.undelivered` → `data-guidance` con `state="run_finished_undelivered"`
- `guidance.failed` → `data-guidance` con `state="failed"` + `failure_reason`
- `run.cancel_requested` → `data-run-state` con `state="cancel_requested"`
- `run.cancelling` → `data-run-state` con `state="cancelling"`
- `run.cancelled` → `data-run-state` con `state="cancelled"`
- `run.cancellation_failed` → `data-run-state` con `state="failed"` y `error`
- `worker.started|running|complete|error|handoff` → `data-worker-group` o `data-workflow-node`
- `data-evidence` (nuevo) → `evidence_id` stable
- `data-source` (nuevo) → `source_id` stable
- Artifacts enriched con `title`, `renderer`, `schemaVersion`, `size_bytes`, `provenance` (en lugar de `{artifactId, mimeType, uri}`)

**CRÍTICO**: cada evento debe portar **stable `id`** para que `group-parts` pueda upsertar en lugar de append.

Mantén la función **pura** (sin side-effects) y los tests exhaustivos.

## 8. Tests requeridos

### `app/src/fixtures/mockParts.ts`
Al menos 40 fixtures cubriendo cada schema, casos edge:
- guidance en cada uno de los 7 estados
- run_state en cada uno de los 9 estados
- artifact con renderer `ioc-table@1` y provenance completa
- command_output con 50 chunks (stdout, stderr, meta en orden)
- 10 chunks para el mismo job_id (test aggregation)
- evidence con source_url, artifact_id
- subagent start→running→complete events con mismo subagent_id
- HITL con redacted_args, risk, evidence_ref_ids
- old-format artifact event (pre-schema) só {artifactId, mimeType, uri} → replay-compat test
- plan con 5 items, replan con 2 nuevos, todo mutations

### `app/src/lib/munin-ui/__tests__/schemas.test.ts`
- Cada schema acepta su good fixture
- Cada schema rechaza malformed (sin `id`, sin `schemaVersion`, `renderer` sin `@1`, run_state unknown, guidance state unknown, etc.)
- `muninDataPartSchemas` discriminators único por `type`

### `app/src/lib/munin-ui/__tests__/group-parts.test.ts`
- 10 command chunks mismo job_id → 1 CommandAggregate con streams.stdout ordenados por sequence
- 4 events mismo guidance_id (queued→consumed→applied) → 1 GuidanceAggregate con `state="applied_to_model"`
- plan + replan → 1 PlanAggregate con items merge por id y replans append
- 100 eventos clustered en 5 tools → 5 ToolAggregates exactos, NO 100 cards
- 3 evidence events mismo evidence_id → 1 entrada upserted

### `app/src/lib/munin-ui/__tests__/replay-compat.test.ts`
- Evento histórico pre-schemas `{artifactId, mimeType, uri}` (sin schemaVersion) → fallback seguro a `artifact@1` con warning en `data.unknown_version`
- Evento `{kind: "tool_output"}` (sin `id`) → fallback: sintetizar id `legacy_{tool_call_id}_{seq}` para mantener replay funcionando
- Evento con `schemaVersion: 999` (futuro) → fallback a diagnostic card, NO crash

### `app/src/components/__tests__/FixtureGallery.test.tsx`
- Renderiza 40 fixtures uno por uno sin crash
- Snapshots visual regression (Playwright o vitest+happy-dom)

## 9. Verificación

```bash
cd app
npm run lint
npm run typecheck
npm run build
npm test -- src/lib/munin-ui
```

## 10. Commit / PR

- Branch: `feat/issue-18b-typed-ui-protocol`
- Commit: `feat(issue-18b): Zod data-part schemas, messageMetadata, group-by-stable-id, replay compatibility`
- PR contra `main`. **Paralelizable con PR A (operator-control) y parte de PR E**.
