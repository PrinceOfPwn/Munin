# Prompt PR-1 — Contrato UX: Schemas Zod versionados, Renderer Registry y Fixture Gallery

> Issue: #18 · Fase 1 (parte frontend) · Ola 1 · **Requiere solo `main`**
> Ejecutar en paralelo con `pr-2` y `pr-6`.
> Contexto compartido obligatorio: `docs/prompts/issue-18/00-master.md` — léelo primero.

---

## 1. Instrucciones para la IA ejecutora

Eres un desarrollador junior/medio que debe seguir estas instrucciones **al pie de la letra**. NO tomes decisiones arquitectónicas distintas a las descritas aquí. NO inventes tipos nuevos fuera de los especificados. NO edites archivos prohibidos.

### Tu objetivo único en este PR
Crear la capa de **contrato de datos frontend y seguridad de renderizado** para Munin:
1. `app/src/types/munin-ui.ts` — Schemas Zod versionados para todas las partes de UI.
2. `app/src/renderers/registry.ts` — Renderer registry con allow-list estricta y fallback seguro.
3. `app/src/fixtures/FixtureGallery.tsx` — Galería visual de partes de UI para prueba en 5 viewports.
4. `app/src/types/__tests__/munin-ui.test.ts` y `app/src/renderers/__tests__/registry.test.ts` — Tests unitarios con Vitest.
5. `docs/issue-18-ui-contract.md` — Documentación del contrato.

---

## 2. Rutas que SOLO este PR modifica (Rutas Permitidas)

PUEDES crear o editar ÚNICAMENTE estas rutas:
- `app/src/types/munin-ui.ts` (NUEVO)
- `app/src/types/__tests__/munin-ui.test.ts` (NUEVO)
- `app/src/renderers/registry.ts` (NUEVO — ojo: la carpeta es `renderers`, NO `extensions`)
- `app/src/renderers/__tests__/registry.test.ts` (NUEVO)
- `app/src/fixtures/FixtureGallery.tsx` (NUEVO)
- `app/src/fixtures/mockParts.ts` (NUEVO)
- `docs/issue-18-ui-contract.md` (NUEVO)
- `changes.md` (AÑADIR entrada)

### Rutas Prohibidas (NO TOCAR BAJO NINGUNA CIRCUNSTANCIA)
- `munin/**`
- `tests/**`
- `app/src/components/**`
- `app/src/lib/**`
- `app/src/app/api/**`
- `app/src/types/mcp.ts` (déjalo intacto; los nuevos tipos viven en `munin-ui.ts`)

---

## 3. Especificación detallada paso a paso

### Paso 3.1: Crear `app/src/types/munin-ui.ts`

Crea el archivo `app/src/types/munin-ui.ts` con el siguiente contenido exacto de Zod schemas:

```typescript
import { z } from "zod";

// Base metadata común para todas las partes
export const basePartSchema = z.object({
  version: z.literal(1).default(1),
  id: z.string().min(1, "El ID es obligatorio para reconciliación"),
  timestamp_ms: z.number().optional(),
});

// 1. Text del asistente
export const assistantTextPartSchema = basePartSchema.extend({
  kind: z.literal("assistant_text"),
  text: z.string(),
});

// 2. Reasoning del provider
export const providerReasoningPartSchema = basePartSchema.extend({
  kind: z.literal("provider_reasoning"),
  text: z.string(),
  is_streaming: z.boolean().optional(),
});

// 3. Tool intent
export const toolIntentPartSchema = basePartSchema.extend({
  kind: z.literal("tool_intent"),
  tool_name: z.string(),
  tool_call_id: z.string(),
  input: z.record(z.unknown()).optional(),
});

// 4. Tool started
export const toolStartedPartSchema = basePartSchema.extend({
  kind: z.literal("tool_started"),
  tool_name: z.string(),
  tool_call_id: z.string(),
  input: z.record(z.unknown()).optional(),
});

// 5. Tool result
export const toolResultPartSchema = basePartSchema.extend({
  kind: z.literal("tool_result"),
  tool_call_id: z.string(),
  output: z.string(),
  elapsed_ms: z.number().optional(),
});

// 6. Tool failed
export const toolFailedPartSchema = basePartSchema.extend({
  kind: z.literal("tool_failed"),
  tool_call_id: z.string(),
  error: z.string(),
  code: z.string().optional(),
});

// 7. Tool output
export const toolOutputPartSchema = basePartSchema.extend({
  kind: z.literal("tool_output"),
  tool_call_id: z.string(),
  chunk: z.string(),
});

// 8. Tool heartbeat
export const toolHeartbeatPartSchema = basePartSchema.extend({
  kind: z.literal("tool_heartbeat"),
  tool_call_id: z.string(),
  elapsed_ms: z.number(),
});

// 9. Command output (terminal)
export const commandOutputPartSchema = basePartSchema.extend({
  kind: z.literal("command_output"),
  job_id: z.string(),
  command: z.string().optional(),
  output: z.string(),
  is_final: z.boolean().default(false),
});

// 10. Operational trace
export const operationalTracePartSchema = basePartSchema.extend({
  kind: z.literal("operational_trace"),
  stage: z.string(),
  summary: z.string(),
  data: z.record(z.unknown()).optional(),
});

// 11. Run state
export const runStatePartSchema = basePartSchema.extend({
  kind: z.literal("run_state"),
  run_id: z.string(),
  state: z.enum(["queued", "running", "waiting_for_human", "cancelling", "cancelled", "completed", "failed", "interrupted"]),
  error: z.string().optional(),
});

// 12. Human interrupt (HITL)
export const humanInterruptPartSchema = basePartSchema.extend({
  kind: z.literal("human_interrupt"),
  request_id: z.string(),
  nonce: z.string(),
  prompt: z.string(),
  options: z.array(z.string()),
  expires_at_ms: z.number().optional(),
});

// 13. Operator guidance (con lifecycle completo)
export const operatorGuidancePartSchema = basePartSchema.extend({
  kind: z.literal("operator_guidance"),
  guidance_id: z.string(),
  body: z.string(),
  target_agent_id: z.string().optional(),
  status: z.enum(["queued", "delivered_to_runtime", "applied_to_model_step", "expired", "superseded", "run_finished_undelivered"]),
  applied_at_step: z.number().optional(),
});

// 14. Artifact
export const artifactPartSchema = basePartSchema.extend({
  kind: z.literal("artifact"),
  artifact_id: z.string(),
  filename: z.string(),
  language: z.string().optional(),
  media_type: z.string().optional(),
  content: z.string(),
  renderer_key: z.enum([
    "ioc-table", "cve-assessment", "timeline", "evidence", "json",
    "csv-table", "markdown", "code", "diff", "mermaid", "graph",
    "finding-card", "screenshot", "terminal", "download", "sandboxed-html"
  ]).default("markdown"),
});

// 15. Subagent lifecycle
export const subagentLifecyclePartSchema = basePartSchema.extend({
  kind: z.literal("subagent_lifecycle"),
  subagent_id: z.string(),
  subagent_type: z.string(),
  phase: z.enum(["start", "running", "complete", "error"]),
  summary: z.string().optional(),
  duration_ms: z.number().optional(),
});

// 16. Note
export const notePartSchema = basePartSchema.extend({
  kind: z.literal("note"),
  text: z.string(),
  author: z.string().optional(),
});

// Discriminated Union de todas las partes
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

Crea el archivo `app/src/renderers/registry.ts` con el siguiente contenido exacto:

```typescript
import React, { ComponentType } from "react";
import { MuninPart, MuninPartKind, muninPartSchema } from "@/types/munin-ui";

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

function DefaultFallbackRenderer({ part, isStreaming }: RendererProps) {
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

### Paso 3.3: Crear `app/src/fixtures/mockParts.ts` y `FixtureGallery.tsx`

Crea `app/src/fixtures/mockParts.ts` con ejemplos de mock data válidos e inválidos.
Crea `app/src/fixtures/FixtureGallery.tsx` para renderizarlos.

### Paso 3.4: Crear Tests Vitest

Crea `app/src/types/__tests__/munin-ui.test.ts`:
- Test que cada una de las 16 partes válidas pase `muninPartSchema.parse()`.
- Test que un objeto sin `kind` falle.
- Test que un `kind` no soportado falle.
- Test que un `run_state` con estado inválido falle.

Crea `app/src/renderers/__tests__/registry.test.ts`:
- Test de validación y resolución con datos válidos.
- Test de fallback cuando la estructura es inválida.

---

## 4. Verificación Obligatoria Antes del Commit

Ejecuta exactamente estos comandos desde `app/`:

```bash
cd app
npm run lint
npm run typecheck
npm run build
npm test
```

Todos los comandos DEBEN responder sin errores (Exit code 0).

---

## 5. Instrucciones de Commit y PR

- Rama: `feat/issue-18-1-ui-contract`
- Commit message: `feat(issue-18-1): add Zod UI part schemas, renderer registry, and fixture gallery`
- Abre el PR contra `main`.
