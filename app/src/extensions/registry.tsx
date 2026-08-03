// tags: [generative-ui, renderer-registry, client-component, munin-ui-v1, typed-renderer-registry, PR-2G]
"use client";

import dynamic from "next/dynamic";
import type { ComponentType } from "react";

import { ArtifactPart } from "@/components/chat/blocks/parts/ArtifactPart";
import { CommandOutputPart } from "@/components/chat/blocks/parts/CommandOutputPart";
import { GuidancePart } from "@/components/chat/blocks/parts/GuidancePart";
import { HitlRequestPart } from "@/components/chat/blocks/parts/HitlRequestPart";
import { OperationalTracePart } from "@/components/chat/blocks/parts/OperationalTracePart";
import { PlanSnapshotPart } from "@/components/chat/blocks/parts/PlanPart";
import { ReasoningPart } from "@/components/chat/blocks/parts/ReasoningPart";
import { ToolInvocationPart } from "@/components/chat/blocks/parts/ToolInvocationPart";
import { logError } from "@/lib/logError";
import {
  registerAdaptedRenderer,
  registerDataRenderer,
  RendererFor,
  __resetRendererRegistryForTests,
} from "@/lib/rendererRegistry";
import {
  artifactSchema,
  commandOutputSchema,
  guidanceLifecycleSchema,
  planSchema,
  reasoningSchema,
  schemaForV1PartType,
  toolInvocationSchema,
  operationalTraceSchema,
  hitlRequestSchema,
} from "@/types/muninUiSchemas";

export type ExtensionSlot = "command_center" | "conversation_inspector" | "run_timeline" | "settings";
export type ExtensionPermission = "read:conversation" | "read:run" | "read:artifact" | "propose:diff";
export type ExtensionManifest = { id: string; version: string; slots: ExtensionSlot[]; permissions: ExtensionPermission[]; featureFlag: string; entrypoint: string };
export type Widget = { manifest: ExtensionManifest; Component: ComponentType };

const permittedSlots: ExtensionSlot[] = ["command_center", "conversation_inspector", "run_timeline", "settings"];

export function registerWidget(manifest: ExtensionManifest): Widget {
  if (!manifest.id || manifest.entrypoint.startsWith("http") || manifest.slots.some((slot) => !permittedSlots.includes(slot))) throw new Error("Invalid isolated Munin extension manifest");
  return { manifest, Component: dynamic(() => import(manifest.entrypoint), { ssr: false, loading: () => null }) };
}

export function enabledWidgets(registry: Widget[], slot: ExtensionSlot, flags: Record<string, boolean>) {
  return registry.filter((widget) => widget.manifest.slots.includes(slot) && flags[widget.manifest.featureFlag]);
}

// ---------------------------------------------------------------------------
// PR-2G — typed renderer registry hook-up.
//
// The original module only wired the Munin extension widget system (the
// ``registerWidget`` / ``enabledWidgets`` export above). PR-2G introduces a
// parallel typed renderer registry that maps each ``munin-ui/v1`` schema key
// (PR-2F) to a trusted React component behind an inline ``ErrorBoundary``
// (``app/src/lib/rendererRegistry.tsx``). We register each trusted renderer
// here once at module load so :func:`RendererFor` can look them up by key
// without ever doing implicit shape-based dispatch.
//
// ``data-command-output`` → ``command-output``, ``data-artifact`` →
// ``artifact``, etc. The renderer components consume the AI-SDK data-part
// ``.data`` payload directly, so the registry's ``registerDataRenderer``
// helper merges ``dataPart.data`` into the component props (see
// ``RendererFor`` in ``rendererRegistry.tsx``).
//
// Notes:
// * The ``guidance-lifecycle`` registry entry is intentionally a no-op
//   placeholder using :component:`GuidancePart` because the v1 lifecycle card
//   reuses the existing guidance styling. A dedicated lifecycle renderer can
//   later replace the entry via :func:`registerDataRenderer` without touching
//   this module's import surface.
// * ``tool-invocation`` reuses :component:`ToolInvocationPart` which already
//   speaks the ``state`` "partial-call" / "call" / "result" enum.
//
// The ``registerOnce`` guard below keeps the registry idempotent across
// hot-reload cycles.
// ---------------------------------------------------------------------------

let REGISTERED = false;
function registerMuninUiV1Renderers(): void {
  if (REGISTERED) return;
  try {
    registerAdaptedRenderer(
      "tool-invocation",
      ToolInvocationPart,
      // ``ToolInvocationPart`` keeps the legacy prop names ``args`` / ``error``
      // so the prior visual contract does not move. The v1 schema exposes
      // ``input`` / ``errorText`` — map them here at the trust boundary.
      (data) => ({
        toolCallId: data.toolCallId ?? "",
        toolName: data.toolName ?? "unknown",
        state: data.state ?? "call",
        args: data.input as Record<string, unknown> | undefined,
        result: data.result,
        error: data.errorText as string | undefined,
      }),
      toolInvocationSchema,
    );
    registerDataRenderer("command-output", CommandOutputPart, commandOutputSchema);
    registerDataRenderer("operational-trace", OperationalTracePart, operationalTraceSchema);
    registerDataRenderer("hitl-request", HitlRequestPart, hitlRequestSchema);
    registerDataRenderer("artifact", ArtifactPart, artifactSchema);
    registerDataRenderer("reasoning", ReasoningPart, reasoningSchema);
    registerDataRenderer("plan", PlanSnapshotPart, planSchema);
    registerDataRenderer("guidance-lifecycle", GuidancePart, guidanceLifecycleSchema);
    REGISTERED = true;
  } catch (error) {
    // A registration failure (e.g. an unexpected duplicate) must not break the
    // module import — the typed registry's fallback card handles missing
    // renderers at render time. The error contract requires that we still
    // surface it via ``logError`` so it is never swallowed silently.
    logError({
      context: "renderer_error",
      error,
      meta: { phase: "registerMuninUiV1Renderers" },
      ts: new Date().toISOString(),
    });
  }
}

registerMuninUiV1Renderers();

export {
  registerDataRenderer,
  RendererFor,
  __resetRendererRegistryForTests,
  schemaForV1PartType,
  RendererFor as default,
};
