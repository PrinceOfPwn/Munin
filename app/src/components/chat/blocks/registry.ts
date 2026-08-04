// tags: [block-registry, renderer-registry, artifact-renderer, sandboxed-html, ioc-table, mermaid, PR-5B, PR-5D, zod, client-component]
"use client";
// -----------------------------------------------------------------------------
// PR-5B / PR-5D — artifact block registry.
//
// The ``munin-ui/v1`` renderer registry (``@/lib/rendererRegistry``) maps AI
// SDK *part types* (``tool-invocation``, ``artifact``, …) to components. This
// module is the sibling layer for *artifact content media types*: it maps a
// stored artifact's ``media_type`` (``text/markdown``,
// ``artifact/sandboxed-html``, ``application/x-munin-ioc-table``, …) to the
// renderer that displays its body. Payloads are validated with the same
// discipline as the v1 schemas — Zod ``safeParse`` with a literal
// discriminator, unknown-but-tolerated fields typed ``z.unknown()``/optional,
// and a ``logError`` + annotated fallback card instead of a crash or a
// silent catch.
//
// The payload shape mirrors the PLAN-6 artifact read-model: ``media_type`` +
// ``content`` plus the rich metadata fields (``renderer``, ``version``,
// ``provenance``, ``preview_url``, ``download_url``).
//
// ``ArtifactPart`` (the v1 ``artifact`` renderer) consults this registry for
// media types it does not render natively (images stay native — inline
// preview + download chip). ``<BlockRendererFor mediaType data extraProps>``
// is the consumer entry point: lookup → schema validation → adapter → error
// boundary → component.
// -----------------------------------------------------------------------------
import type { ComponentType, ReactNode } from "react";
import { Component, createElement } from "react";
import { z } from "zod";
import type { ZodTypeAny } from "zod";

import { Markdown } from "@/components/Markdown";
import { logError } from "@/lib/logError";
import { cn } from "@/lib/utils";
import { CommandOutputPart } from "./parts/CommandOutputPart";
import { IocTablePart } from "./parts/IocTablePart";
import { MermaidPart } from "./parts/MermaidPart";
import { SandboxedPreview } from "./parts/SandboxedPreview";

// ---------------------------------------------------------------------------
// Payload schemas (PR-5B) — mirror the PLAN-6 rich artifact metadata fields.
// ---------------------------------------------------------------------------

/** PLAN-6 rich metadata shared by every artifact content payload. */
export const artifactRichMetadataFields = {
  renderer: z.string().optional(),
  version: z.number().int().nonnegative().optional(),
  provenance: z.string().optional(),
  preview_url: z.string().optional(),
  download_url: z.string().optional(),
} as const;

/** Generic validated artifact body: any media type + content + rich metadata. */
export const artifactContentSchema = z.object({
  media_type: z.string(),
  content: z.string(),
  ...artifactRichMetadataFields,
});

/** The sandboxed-html payload contract — literal discriminator, body string. */
export const sandboxedHtmlArtifactSchema = z.object({
  media_type: z.literal("artifact/sandboxed-html"),
  content: z.string(),
  ...artifactRichMetadataFields,
});

/** IOC table payload — raw indicator content, optional pre-parsed columns. */
export const iocTableSchema = z.object({
  media_type: z.literal("application/x-munin-ioc-table"),
  content: z.string(),
  columns: z.array(z.string()).optional(),
  rows: z.array(z.record(z.string(), z.unknown())).optional(),
  ...artifactRichMetadataFields,
});

export type SandboxedHtmlArtifactPayload = z.infer<typeof sandboxedHtmlArtifactSchema>;
export type IocTableArtifactPayload = z.infer<typeof iocTableSchema>;

// ---------------------------------------------------------------------------
// Media type vocabulary — the full 5D set (plus aliases).
// ---------------------------------------------------------------------------

export const ARTIFACT_MEDIA_TYPES = {
  MARKDOWN: "text/markdown",
  PLAIN_TEXT: "text/plain",
  CODE_PYTHON: "text/x-python",
  DIFF: "text/x-diff",
  JSON: "application/json",
  CSV: "text/csv",
  TABLE: "application/x-munin-table",
  MERMAID: "text/vnd.mermaid",
  MERMAID_ALT: "application/x-mermaid",
  SANDBOXED_HTML: "artifact/sandboxed-html",
  IOC_TABLE: "application/x-munin-ioc-table",
} as const;

export type ArtifactMediaType = (typeof ARTIFACT_MEDIA_TYPES)[keyof typeof ARTIFACT_MEDIA_TYPES];

/** Canonicalize a MIME string: strip parameters, lowercase, trim. */
export function normalizeMediaType(mediaType: string | null | undefined): string {
  return (mediaType ?? "").split(";")[0]?.trim().toLowerCase() ?? "";
}

// ---------------------------------------------------------------------------
// Registry entry + lookup
// ---------------------------------------------------------------------------

export interface BlockRendererEntry {
  schemaRef: ZodTypeAny | null;
  Component: ComponentType<any>;
  fallbackElement: ReactNode;
  /** snake_case read-model payload → component prop names (camelCase). */
  adapter?: (data: Record<string, unknown>) => Record<string, unknown>;
}

const BLOCK_REGISTRY: Record<string, BlockRendererEntry> = {};
const REGISTERED_MEDIA_TYPES = new Set<string>();

export function registerBlockRenderer(
  mediaType: string,
  entry: Omit<BlockRendererEntry, "fallbackElement"> & { fallbackElement?: ReactNode },
): void {
  const key = normalizeMediaType(mediaType);
  const existing = BLOCK_REGISTRY[key];
  if (existing && existing.Component === entry.Component) return;
  BLOCK_REGISTRY[key] = {
    schemaRef: entry.schemaRef,
    Component: entry.Component,
    fallbackElement:
      entry.fallbackElement ??
      createElement(BlockFallbackCard, { mediaType: key, reason: "render_error" }),
    adapter: entry.adapter,
  };
  REGISTERED_MEDIA_TYPES.add(key);
}

export function lookupBlockRenderer(mediaType: string | null | undefined): BlockRendererEntry | null {
  return BLOCK_REGISTRY[normalizeMediaType(mediaType)] ?? null;
}

export function blockSchemaForMediaType(mediaType: string | null | undefined): ZodTypeAny | null {
  return BLOCK_REGISTRY[normalizeMediaType(mediaType)]?.schemaRef ?? null;
}

/** Test-only reset so suites can re-register deterministic state. */
export function __resetBlockRegistryForTests(): void {
  for (const key of REGISTERED_MEDIA_TYPES) {
    delete BLOCK_REGISTRY[key];
  }
  REGISTERED_MEDIA_TYPES.clear();
}

// ---------------------------------------------------------------------------
// Fallback + error boundary (mirrors the v1 registry contract)
// ---------------------------------------------------------------------------

function BlockFallbackCard({
  mediaType,
  reason,
  issues,
}: {
  mediaType: string;
  reason: "unknown_media_type" | "schema_error" | "render_error";
  issues?: string;
}) {
  // ``createElement`` rather than JSX: this module stays a plain ``.ts``
  // file (per the PR-5B card), so all element construction is imperative.
  return createElement(
    "div",
    { role: "alert", "aria-label": `Block renderer fallback (${mediaType})`, className: cn("rounded-lg border border-border bg-surface px-3 py-2 text-xs text-body") },
    createElement(
      "p",
      { className: "mb-0.5 font-semibold uppercase tracking-wide text-accent" },
      "Block renderer fallback",
    ),
    createElement(
      "p",
      { className: "text-body" },
      createElement("span", { className: "opacity-80" }, "media type:"),
      " ",
      createElement("code", { className: "font-mono" }, mediaType),
      ` (${reason})`,
    ),
    issues
      ? createElement(
          "p",
          { className: "mt-1 break-words text-body/70" },
          `detail: ${issues}`,
        )
      : null,
  );
}

interface BlockBoundaryProps {
  mediaType: string;
  fallbackElement: ReactNode;
  children?: ReactNode;
}

interface BlockBoundaryState {
  hasError: boolean;
}

class BlockRendererErrorBoundary extends Component<BlockBoundaryProps, BlockBoundaryState> {
  state: BlockBoundaryState = { hasError: false };

  static getDerivedStateFromError(): BlockBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: unknown): void {
    logError({
      context: "block_renderer_error",
      error,
      meta: { mediaType: this.props.mediaType, phase: "render" },
      ts: new Date().toISOString(),
    });
  }

  render(): ReactNode {
    if (this.state.hasError) return this.props.fallbackElement;
    return this.props.children;
  }
}

// ---------------------------------------------------------------------------
// Public consumer — ``<BlockRendererFor>``
// ---------------------------------------------------------------------------

export interface BlockRendererForProps {
  /** The artifact's raw media type (parameters stripped inside). */
  mediaType: string;
  /** The read-model payload: ``media_type``, ``content``, rich metadata. */
  data: Record<string, unknown>;
  /** Caller props merged on top of the adapter output (always win). */
  extraProps?: Record<string, unknown>;
}

/**
 * Dispatch an artifact body to its registered block renderer.
 *
 * Unknown media type or payload that fails the registered schema → ``logError``
 * + annotated fallback card (never a silent catch, never a crash).
 */
export function BlockRendererFor({ mediaType, data, extraProps }: BlockRendererForProps) {
  const key = normalizeMediaType(mediaType);
  const entry = lookupBlockRenderer(key);
  if (!entry) {
    logError({
      context: "block_renderer_error",
      error: new Error(`no renderer registered for media type ${key}`),
      meta: { mediaType: key, phase: "lookup" },
      ts: new Date().toISOString(),
    });
    return createElement(BlockFallbackCard, {
      mediaType: key || "unknown",
      reason: "unknown_media_type",
    });
  }
  if (entry.schemaRef) {
    const result = entry.schemaRef.safeParse(data);
    if (!result.success) {
      const issueSummary = result.error.issues
        .map((issue) => `${issue.path.join(".")}: ${issue.message}`)
        .join("; ");
      logError({
        context: "block_renderer_error",
        error: result.error,
        meta: { mediaType: key, phase: "schema_pre_render", data },
        ts: new Date().toISOString(),
      });
      return createElement(BlockFallbackCard, {
        mediaType: key,
        reason: "schema_error",
        issues: issueSummary,
      });
    }
  }
  const Renderer = entry.Component;
  const payload = entry.adapter ? entry.adapter(data) : data;
  const finalProps = { ...payload, ...(extraProps ?? {}) };
  return createElement(
    BlockRendererErrorBoundary,
    { mediaType: key, fallbackElement: entry.fallbackElement },
    createElement(Renderer, finalProps),
  );
}

// ---------------------------------------------------------------------------
// The 5D full set — registered once at module load (idempotent guard).
// ---------------------------------------------------------------------------

let REGISTERED = false;

function registerBuiltinBlockRenderers(): void {
  if (REGISTERED) return;
  REGISTERED = true;

  registerBlockRenderer(ARTIFACT_MEDIA_TYPES.MARKDOWN, {
    schemaRef: artifactContentSchema,
    Component: Markdown,
    adapter: (data) => ({
      text: data.content as string,
      className: "rounded-md border border-border bg-surface p-3",
    }),
  });

  // code / diff / json / csv / table — the generic monospace block is
  // CommandOutputPart, reused for every machine-readable body.
  for (const mediaType of [
    ARTIFACT_MEDIA_TYPES.PLAIN_TEXT,
    ARTIFACT_MEDIA_TYPES.CODE_PYTHON,
    ARTIFACT_MEDIA_TYPES.DIFF,
    ARTIFACT_MEDIA_TYPES.JSON,
    ARTIFACT_MEDIA_TYPES.CSV,
    ARTIFACT_MEDIA_TYPES.TABLE,
  ]) {
    registerBlockRenderer(mediaType, {
      schemaRef: artifactContentSchema,
      Component: CommandOutputPart,
      adapter: (data) => ({
        toolName: "artifact",
        stream: "stdout" as const,
        text: data.content as string,
      }),
    });
  }

  // Mermaid — both alias media types route to the lazy diagram renderer.
  for (const mediaType of [ARTIFACT_MEDIA_TYPES.MERMAID, ARTIFACT_MEDIA_TYPES.MERMAID_ALT]) {
    registerBlockRenderer(mediaType, {
      schemaRef: artifactContentSchema,
      Component: MermaidPart,
      adapter: (data) => ({ content: data.content as string }),
    });
  }

  // Sandboxed HTML (PR-5B) — hardened iframe preview.
  registerBlockRenderer(ARTIFACT_MEDIA_TYPES.SANDBOXED_HTML, {
    schemaRef: sandboxedHtmlArtifactSchema,
    Component: SandboxedPreview,
    adapter: (data) => ({
      content: data.content as string,
      downloadUrl: data.download_url as string | undefined,
      previewUrl: data.preview_url as string | undefined,
    }),
  });

  // IOC table (PR-5D) — indicators with client-side filtering.
  registerBlockRenderer(ARTIFACT_MEDIA_TYPES.IOC_TABLE, {
    schemaRef: iocTableSchema,
    Component: IocTablePart,
    adapter: (data) => ({
      content: data.content as string,
    }),
  });
}

registerBuiltinBlockRenderers();
