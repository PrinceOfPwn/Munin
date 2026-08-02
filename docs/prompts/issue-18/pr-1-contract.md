# Prompt PR-1 — Contrato UX: Schemas Zod versionados, Renderer Registry y Fixture Gallery

> Issue: #18 · Fase 1 (parte frontend) · Ola 1 · **Requiere solo `main`**
> Ejecutar en paralelo con `pr-2` y `pr-6`.
> Contexto compartido obligatorio: `docs/prompts/issue-18/00-master.md` — léelo primero.

---

## 1. Instrucciones para la IA ejecutora

Debes construir la capa de **contrato de datos frontend y seguridad de renderizado** para Munin. Sigue la especificación de archivos, funciones y tipos al pie de la letra.

---

## 2. Rutas Permitidas (Rutas que SOLO este PR modifica)

- `app/src/types/munin-ui.ts` (NUEVO)
- `app/src/types/__tests__/munin-ui.test.ts` (NUEVO)
- `app/src/renderers/registry.ts` (NUEVO — Ojo: la carpeta es `renderers`, NO `extensions`)
- `app/src/renderers/__tests__/registry.test.ts` (NUEVO)
- `app/src/fixtures/mockParts.ts` (NUEVO)
- `app/src/fixtures/FixtureGallery.tsx` (NUEVO)
- `docs/issue-18-ui-contract.md` (NUEVO)
- `changes.md` (AÑADIR entrada)

### Rutas Prohibidas (NO TOCAR)
- `munin/**`
- `tests/**`
- `app/src/components/**`
- `app/src/lib/**`
- `app/src/app/api/**`
- `app/src/types/mcp.ts`

---

## 3. Especificación Código a Código

### Paso 3.1: Crear `app/src/types/munin-ui.ts`

Escribe exactamente el siguiente código Zod:

```typescript
import { z } from "zod";

export const basePartSchema = z.object({
  version: z.literal(1).default(1),
  id: z.string().min(1, "El ID es obligatorio para reconciliación de lifecycle"),
  timestamp_ms: z.number().optional(),
});

export const assistantTextPartSchema = basePartSchema.extend({
  kind: z.literal("assistant_text"),
  text: z.string(),
});

export const providerReasoningPartSchema = basePartSchema.extend({
  kind: z.literal("provider_reasoning"),
  text: z.string(),
  is_streaming: z.boolean().optional(),
});

export const toolIntentPartSchema = basePartSchema.extend({
  kind: z.literal("tool_intent"),
  tool_name: z.string(),
  tool_call_id: z.string(),
  input: z.record(z.unknown()).optional(),
});

export const toolStartedPartSchema = basePartSchema.extend({
  kind: z.literal("tool_started"),
  tool_name: z.string(),
  tool_call_id: z.string(),
  input: z.record(z.unknown()).optional(),
});

export const toolResultPartSchema = basePartSchema.extend({
  kind: z.literal("tool_result"),
  tool_call_id: z.string(),
  output: z.string(),
  elapsed_ms: z.number().optional(),
});

export const toolFailedPartSchema = basePartSchema.extend({
  kind: z.literal("tool_failed"),
  tool_call_id: z.string(),
  error: z.string(),
  code: z.string().optional(),
});

export const toolOutputPartSchema = basePartSchema.extend({
  kind: z.literal("tool_output"),
  tool_call_id: z.string(),
  chunk: z.string(),
});

export const toolHeartbeatPartSchema = basePartSchema.extend({
  kind: z.literal("tool_heartbeat"),
  tool_call_id: z.string(),
  elapsed_ms: z.number(),
});

export const commandOutputPartSchema = basePartSchema.extend({
  kind: z.literal("command_output"),
  job_id: z.string(),
  command: z.string().optional(),
  output: z.string(),
  is_final: z.boolean().default(false),
});

export const operationalTracePartSchema = basePartSchema.extend({
  kind: z.literal("operational_trace"),
  stage: z.string(),
  summary: z.string(),
  data: z.record(z.unknown()).optional(),
});

export const runStatePartSchema = basePartSchema.extend({
  kind: z.literal("run_state"),
  run_id: z.string(),
  state: z.enum([
    "queued",
    "running",
    "waiting_for_human",
    "cancelling",
    "cancelled",
    "completed",
    "failed",
    "interrupted"
  ]),
  error: z.string().optional(),
});

export const humanInterruptPartSchema = basePartSchema.extend({
  kind: z.literal("human_interrupt"),
  request_id: z.string(),
  nonce: z.string(),
  prompt: z.string(),
  options: z.array(z.string()),
  expires_at_ms: z.number().optional(),
});

export const operatorGuidancePartSchema = basePartSchema.extend({
  kind: z.literal("operator_guidance"),
  guidance_id: z.string(),
  body: z.string(),
  target_agent_id: z.string().optional(),
  status: z.enum([
    "queued",
    "delivered_to_runtime",
    "applied_to_model_step",
    "expired",
    "superseded",
    "run_finished_undelivered"
  ]),
  applied_at_step: z.number().optional(),
});

export const artifactPartSchema = basePartSchema.extend({
  kind: z.literal("artifact"),
  artifact_id: z.string(),
  filename: z.string(),
  language: z.string().optional(),
  media_type: z.string().optional(),
  content: z.string(),
  renderer_key: z.enum([
    "ioc-table",
    "cve-assessment",
    "timeline",
    "evidence",
    "json",
    "csv-table",
    "markdown",
    "code",
    "diff",
    "mermaid",
    "graph",
    "finding-card",
    "screenshot",
    "terminal",
    "download",
    "sandboxed-html"
  ]).default("markdown"),
});

export const subagentLifecyclePartSchema = basePartSchema.extend({
  kind: z.literal("subagent_lifecycle"),
  subagent_id: z.string(),
  subagent_type: z.string(),
  phase: z.enum(["start", "running", "complete", "error"]),
  summary: z.string().optional(),
  duration_ms: z.number().optional(),
});

export const notePartSchema = basePartSchema.extend({
  kind: z.literal("note"),
  text: z.string(),
  author: z.string().optional(),
});

export const muninPartSchema = z.discriminatedUnion("kind", [
  assistantTextPartSchema,
  providerReasoningPartSchema,
  toolIntentPartSchema,
  toolStartedPartSchema,
  toolResultPartSchema,
  toolFailedPartSchema,
  toolOutputPartSchema,
  toolHeartbeatPartSchema,
  commandOutputPartSchema,
  operationalTracePartSchema,
  runStatePartSchema,
  humanInterruptPartSchema,
  operatorGuidancePartSchema,
  artifactPartSchema,
  subagentLifecyclePartSchema,
  notePartSchema,
]);

export type MuninPart = z.infer<typeof muninPartSchema>;
export type MuninPartKind = MuninPart["kind"];
```

### Paso 3.2: Crear `app/src/renderers/registry.ts`

Escribe el archivo `app/src/renderers/registry.ts`:

```typescript
import React, { ComponentType } from "react";
import { MuninPart, muninPartSchema } from "@/types/munin-ui";

export type RendererKey =
  | "ioc-table"
  | "cve-assessment"
  | "timeline"
  | "evidence"
  | "json"
  | "csv-table"
  | "markdown"
  | "code"
  | "diff"
  | "mermaid"
  | "graph"
  | "finding-card"
  | "screenshot"
  | "terminal"
  | "download"
  | "sandboxed-html"
  | "fallback";

export interface RendererProps<T = unknown> {
  part: MuninPart;
  payload: T;
  isStreaming?: boolean;
}

export interface RendererEntry {
  key: RendererKey;
  description: string;
  component: ComponentType<RendererProps<any>>;
}

class RendererRegistry {
  private renderers = new Map<RendererKey, RendererEntry>();
  private fallbackRenderer: RendererEntry;

  constructor() {
    this.fallbackRenderer = {
      key: "fallback",
      description: "Fallback seguro para partes no soportadas o inválidas",
      component: DefaultFallbackRenderer,
    };
  }

  public register(entry: RendererEntry): void {
    this.renderers.set(entry.key, entry);
  }

  public get(key: RendererKey): RendererEntry {
    return this.renderers.get(key) || this.fallbackRenderer;
  }

  public validateAndResolve(rawInput: unknown): {
    valid: boolean;
    part: MuninPart | null;
    renderer: RendererEntry;
    error?: string;
  } {
    const parseResult = muninPartSchema.safeParse(rawInput);
    if (!parseResult.success) {
      return {
        valid: false,
        part: null,
        renderer: this.fallbackRenderer,
        error: parseResult.error.message,
      };
    }

    const part = parseResult.data;
    let rendererKey: RendererKey = "fallback";

    if (part.kind === "artifact") {
      rendererKey = part.renderer_key as RendererKey;
    } else if (part.kind === "command_output") {
      rendererKey = "terminal";
    } else if (part.kind === "assistant_text") {
      rendererKey = "markdown";
    }

    const renderer = this.get(rendererKey);
    return {
      valid: true,
      part,
      renderer,
    };
  }
}

function DefaultFallbackRenderer({ part }: RendererProps) {
  return React.createElement(
    "div",
    {
      className:
        "p-3 my-2 rounded border border-borderStrong bg-raised text-body text-xs font-mono overflow-x-auto",
    },
    React.createElement(
      "div",
      { className: "text-muted font-sans mb-1" },
      `[Fallback Renderer · Part: ${part?.kind || "unknown"}]`
    ),
    React.createElement("pre", null, JSON.stringify(part, null, 2))
  );
}

export const registry = new RendererRegistry();
```

---

## 4. Verificación y Tests

Ejecuta desde `app/`:

```bash
cd app
npm run lint
npm run typecheck
npm run build
npm test
```

---

## 5. Instrucciones de Commit y PR

- Branch: `feat/issue-18-1-ui-contract`
- Commit: `feat(issue-18-1): add Zod UI part schemas, renderer registry, and fixture gallery`
- Abre el PR contra `main`.
