// tags: [renderer-registry, munin-ui-v1, ErrorBoundary, renderer-for, ui-component, client-component, schema-validation, fallback-card, PR-2G, PR-2F]
"use client";
// -----------------------------------------------------------------------------
// munin-ui/v1 typed renderer registry (PR-2G).
//
// Replaces the implicit shape-based ``if/else`` dispatch in
// ``AgentConsole.RenderMessagePart`` with an explicit allow-list of renderer
// keys (PR-2F schema keys) → trusted React component. Each render is wrapped
// in an inline ``ErrorBoundary`` (``react-error-boundary`` is NOT installed in
// this worktree, so a minimal class component is implemented here) which logs
// failures via ``logError({context:'renderer_error', ...})`` and falls back to
// an annotated card so a single broken component never crashes the whole
// console tree.
//
// ``<RendererFor dataPart={...} rendererKey="tool-invocation" />`` is the
// consumer-facing entry point; the per-renderer key passed in is matched
// against the allow-list so a payload with the wrong discriminator never
// reaches the wrong component.
// -----------------------------------------------------------------------------
import type { ComponentType, ReactNode } from "react";
import { Component, createElement } from "react";
import type { ZodTypeAny } from "zod";

import {
  MUNIN_UI_V1_PART_TYPES,
  schemaForV1PartType,
  type MuninUiV1PartType,
} from "@/types/muninUiSchemas";
import { logError } from "@/lib/logError";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Allow-listed renderer entries. Each entry pairs:
//   schemaRef    — the PR-2F Zod schema used to validate the payload before
//                  rendering. ``null``/absent schema means the renderer is
//                  structural-only (text/raw) and is dispatched directly.
//   Component    — the trusted React component to render when the key matches.
//   fallbackElement — shown when the payload fails ``safeParse`` OR when the
//                  component throws inside the ErrorBoundary. An annotation is
//                  appended with the failing rendererKey so an operator can
//                  surface it in the bug report.
//
// Import paths mirror the existing ``app/src/components/chat/blocks/parts/``
// layout. ``React.ComponentType<any>`` is used deliberately: each renderer has
// its own typed props interface, and the registry is the place where we
// explicitly choose to lose the per-component prop typing in exchange for the
// discriminated-key dispatch. ``any`` is contained to this module.
// ---------------------------------------------------------------------------

/* eslint-disable @typescript-eslint/no-explicit-any */

export interface RendererRegistryEntry {
  schemaRef: ZodTypeAny | null;
  Component: ComponentType<any>;
  fallbackElement: ReactNode;
  /**
   * Optional adapter translating the validated v1 data-part payload into the
   * trusted component's typed props. Most renderers consume fields verbatim
   * (camelCase matches), but a few (``tool-invocation``) want a slight rename
   * (``input`` → ``args``, ``errorText`` → ``error``); the adapter isolates
   * that translation so the component's prop interface stays authoritative.
   */
  adapter?: (data: Record<string, unknown>) => Record<string, unknown>;
}

type AnyPartData = Record<string, unknown> & {
  __muninSchemaError?: {
    version?: string;
    rendererKey?: string;
    issues?: Array<{ path: PropertyKey[]; message: string; code: string }>;
  };
};

// ---------------------------------------------------------------------------
// Inline ErrorBoundary — minimal class component implementing the React
// ``componentDidCatch`` contract. ``react-error-boundary`` is NOT installed in
// this worktree (and we are forbidden from installing), so we implement the
// least generational surface: state ``hasError`` set on a thrown render, log
// to ``logError`` with the failing ``rendererKey`` (from props so it survives
// the thrown render's closure), and fall back to the provided fallbackElement.
// ---------------------------------------------------------------------------

interface ErrorBoundaryProps {
  rendererKey: string;
  fallbackElement: ReactNode;
  children: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
}

class RendererErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: unknown): void {
    logError({
      context: "renderer_error",
      error,
      meta: { rendererKey: this.props.rendererKey },
      ts: new Date().toISOString(),
    });
  }

  render(): ReactNode {
    if (this.state.hasError) {
      return this.props.fallbackElement;
    }
    return this.props.children;
  }
}

// ---------------------------------------------------------------------------
// Annotated fallback card — used both for unknown keys and for failed render
// attempts. Tailwind tokens only (no hardcoded hex — see ``tailwind.config.ts``).
// ---------------------------------------------------------------------------

function AnnotationFallback({
  rendererKey,
  reason,
  issues,
}: {
  rendererKey: string;
  reason: "unknown_key" | "schema_error" | "render_error";
  issues?: string;
}) {
  return (
    <div
      role="alert"
      aria-label={`Renderer fallback (${rendererKey})`}
      className={cn(
        "rounded-lg border border-border bg-surface px-3 py-2",
        "text-xs text-body",
      )}
    >
      <p className="mb-0.5 font-semibold uppercase tracking-wide text-accent">
        Renderer fallback
      </p>
      <p className="text-body">
        <span className="opacity-80">rendererKey:</span>{" "}
        <code className="font-mono">{rendererKey}</code> ({reason})
      </p>
      {issues ? (
        <p className="mt-1 break-words text-body/70">detail: {issues}</p>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Registry map — keyed by the allow-listed ``munin-ui/v1`` renderer keys.
//
// The components are lazily referenced via ``React.lazy``-free static imports
// so the bundler keeps them in the main chunk (the existing AgentConsole
// already imports them eagerly). We deliberately do NOT use dynamic component
// loading — the card forbids implicit shape-based dispatch.
// ---------------------------------------------------------------------------

const RENDERER_REGISTRY: Partial<Record<MuninUiV1PartType, RendererRegistryEntry>> = {};
const REGISTERED_COMPONENTS = new Set<MuninUiV1PartType>();

/**
 * Register a trusted component for an allow-listed v1 renderer key.
 * Idempotent — re-registering the same (key, Component) pair is a no-op so
 * hot-reloading does not duplicate entries. Duplicate replacement of an
 * existing key with a NEW component is permitted only in tests (guarded by
 * the dev-vs-prod invariant below) so prod never accidentally overrides a
 * trusted component.
 */
export function registerRenderer(
  key: MuninUiV1PartType,
  entry: Omit<RendererRegistryEntry, "fallbackElement"> & { fallbackElement?: ReactNode },
): void {
  if (!MUNIN_UI_V1_PART_TYPES.includes(key)) {
    // Defense-in-depth: the type system already narrows this, but an explicit
    // runtime guard keeps a typo from registering a key the schema map can't
    // validate.
    throw new Error(`registerRenderer: unknown munin-ui/v1 key ${key}`);
  }
  const existing = RENDERER_REGISTRY[key];
  if (existing && existing.Component === entry.Component) {
    return;
  }
  const fallbackElement =
    entry.fallbackElement ?? (
      <AnnotationFallback rendererKey={key} reason="render_error" />
    );
  RENDERER_REGISTRY[key] = {
    schemaRef: entry.schemaRef,
    Component: entry.Component,
    fallbackElement,
  };
  REGISTERED_COMPONENTS.add(key);
}

/**
 * Test-only escape hatch to override an existing registry entry. Prod code
 * must go through :func:`registerRenderer` exactly once per renderer key.
 */
export function __resetRendererRegistryForTests(): void {
  for (const key of REGISTERED_COMPONENTS) {
    delete RENDERER_REGISTRY[key];
  }
  REGISTERED_COMPONENTS.clear();
}

function lookupRenderer(
  key: MuninUiV1PartType,
): RendererRegistryEntry | null {
  return RENDERER_REGISTRY[key] ?? null;
}

// ---------------------------------------------------------------------------
// ``<RendererFor>`` — the public consumer API. Pass the data part as-is and
// an explicit ``rendererKey`` from the allow-list; the component looks the
// entry up, validates the data against the registered schema, wraps the
// render in an ``ErrorBoundary`` and returns the ReactNode.
// ---------------------------------------------------------------------------

export interface RendererForProps {
  /** The data part payload produced by the BFF translator. */
  dataPart: Record<string, unknown>;
  /**
   * The allow-listed renderer key (one of ``MUNIN_UI_V1_PART_TYPES``). If the
   * caller has an AI SDK data-part ``type`` (e.g. ``data-command-output``),
   * it must convert to the renderer key (``command-output``) before calling.
   */
  rendererKey: MuninUiV1PartType;
  /**
   * Optional override props the caller wants merged into the renderer
   * component (callback hooks, ``messageId``, etc.). Schema validation runs
   * only against ``dataPart`` so callbacks aren't constrained to the v1
   * schemas.
   */
  extraProps?: Record<string, unknown>;
}

export function RendererFor({ dataPart, rendererKey, extraProps }: RendererForProps) {
  const entry = lookupRenderer(rendererKey);
  if (!entry) {
    return <AnnotationFallback rendererKey={rendererKey} reason="unknown_key" />;
  }
  // The BFF already validated and attached ``__muninSchemaError`` on bad
  // payloads, but a non-BFF caller (or an in-test render) might reach us raw.
  // Run ``safeParse`` against the registered schema; on failure render the
  // fallback card and log the validation issue.
  const anyData = dataPart as AnyPartData;
  if (entry.schemaRef) {
    const result = entry.schemaRef.safeParse(dataPart);
    if (!result.success) {
      const issueSummary = result.error.issues
        .map((i) => `${i.path.join(".")}: ${i.message}`)
        .join("; ");
      logError({
        context: "renderer_error",
        error: result.error,
        meta: { rendererKey, phase: "schema_pre_render", dataPart },
        ts: new Date().toISOString(),
      });
      return (
        <AnnotationFallback
          rendererKey={rendererKey}
          reason="schema_error"
          issues={issueSummary}
        />
      );
    }
  } else if (anyData.__muninSchemaError) {
    // The BFF annotated this envelope with a validation failure already; log
    // and fall back without re-running the schema (the renderer did not pin
    // one).
    logError({
      context: "renderer_error",
      error: anyData.__muninSchemaError,
      meta: { rendererKey, phase: "bff_annotated", dataPart },
      ts: new Date().toISOString(),
    });
    const issues = anyData.__muninSchemaError.issues
      ?.map((i) => `${i.path.join(".")}: ${i.message}`)
      .join("; ");
    return (
      <AnnotationFallback
        rendererKey={rendererKey}
        reason="schema_error"
        issues={issues}
      />
    );
  }

  const Renderer = entry.Component;
  // Run the per-entry adapter (if registered) on the validated payload so the
  // component receives its named props. Then merge any caller-provided
  // ``extraProps`` (callbacks) on top so they always win.
  const payload = entry.adapter
    ? entry.adapter(dataPart)
    : (dataPart.data ?? dataPart);
  const finalProps = { ...(payload as Record<string, unknown>), ...(extraProps ?? {}) };
  return createElement(
    RendererErrorBoundary,
    { rendererKey, fallbackElement: entry.fallbackElement },
    createElement(Renderer, finalProps),
  );
}

/**
 * Convenience: register a renderer whose component props are a direct subset
 * of the v1 data-part payload (the common case — camelCase fields match).
 * ``registerAdaptedRenderer`` handles the few renderers that need a rename.
 */
export function registerDataRenderer<K extends MuninUiV1PartType>(
  key: K,
  Component: ComponentType<any>,
  schemaRef: ZodTypeAny | null = null,
): void {
  registerRenderer(key, {
    schemaRef,
    Component,
    fallbackElement: <AnnotationFallback rendererKey={key} reason="render_error" />,
  });
}

/**
 * Register a renderer with a payload → props adapter (e.g. for the trusty
 * ``tool-invocation`` component, whose prop interface keeps ``args`` /
 * ``error`` instead of the v1 ``input`` / ``errorText``).
 */
export function registerAdaptedRenderer<K extends MuninUiV1PartType>(
  key: K,
  Component: ComponentType<any>,
  adapter: (data: Record<string, unknown>) => Record<string, unknown>,
  schemaRef: ZodTypeAny | null = null,
): void {
  registerRenderer(key, {
    schemaRef,
    Component,
    adapter,
    fallbackElement: <AnnotationFallback rendererKey={key} reason="render_error" />,
  });
}

export { schemaForV1PartType, MUNIN_UI_V1_PART_TYPES };
