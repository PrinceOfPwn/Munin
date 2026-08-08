// tags: [tests, block-registry, PR-5B, PR-5D, zod, renderer-routing, vitest]
// -----------------------------------------------------------------------------
// PR-5B/5D — block registry routing + payload validation tests.
//
// ``npm test`` (vitest, node environment) is the CI-enforced gate for
// frontend logic, so routing is covered here rather than in a Playwright
// spec that CI cannot run yet (the e2e suite is CI-ready but unplugged —
// see changes.md).
//
// Covers: every registered media type resolves to a renderer; unknown media
// types take the logError + fallback path (never a silent catch); payload
// schemas accept the PLAN-6 rich metadata and reject malformed bodies; the
// v1-consistent schema discipline (literal discriminator) holds.
// -----------------------------------------------------------------------------
import { afterEach, describe, expect, it, vi } from "vitest";
import type * as React from "react";

import * as logErrorModule from "@/lib/logError";
import { SandboxedPreview } from "@/components/chat/blocks/parts/SandboxedPreview";
import { IocTablePart } from "@/components/chat/blocks/parts/IocTablePart";
import { MermaidPart } from "@/components/chat/blocks/parts/MermaidPart";
import {
  ARTIFACT_MEDIA_TYPES,
  BlockRendererFor,
  __resetBlockRegistryForTests,
  artifactContentSchema,
  blockSchemaForMediaType,
  iocTableSchema,
  lookupBlockRenderer,
  normalizeMediaType,
  sandboxedHtmlArtifactSchema,
} from "@/components/chat/blocks/registry";

const FULL_SET = [
  ARTIFACT_MEDIA_TYPES.MARKDOWN,
  ARTIFACT_MEDIA_TYPES.PLAIN_TEXT,
  ARTIFACT_MEDIA_TYPES.CODE_PYTHON,
  ARTIFACT_MEDIA_TYPES.DIFF,
  ARTIFACT_MEDIA_TYPES.JSON,
  ARTIFACT_MEDIA_TYPES.CSV,
  ARTIFACT_MEDIA_TYPES.TABLE,
  ARTIFACT_MEDIA_TYPES.MERMAID,
  ARTIFACT_MEDIA_TYPES.MERMAID_ALT,
  ARTIFACT_MEDIA_TYPES.SANDBOXED_HTML,
  ARTIFACT_MEDIA_TYPES.IOC_TABLE,
];

describe("block registry routing", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it.each(FULL_SET)("registers a renderer for media type %s", (mediaType) => {
    expect(lookupBlockRenderer(mediaType)).not.toBeNull();
    expect(blockSchemaForMediaType(mediaType)).not.toBeNull();
  });

  it("returns null for an unknown media type (lookup is safe)", () => {
    expect(lookupBlockRenderer("application/x-unknown")).toBeNull();
    expect(lookupBlockRenderer(null)).toBeNull();
    expect(lookupBlockRenderer("")).toBeNull();
  });

  it("routes sandboxed-html to SandboxedPreview", () => {
    expect(lookupBlockRenderer("artifact/sandboxed-html")?.Component).toBe(SandboxedPreview);
  });

  it("routes IOC tables to IocTablePart", () => {
    expect(lookupBlockRenderer("application/x-munin-ioc-table")?.Component).toBe(IocTablePart);
  });

  it("routes both mermaid aliases to MermaidPart", () => {
    expect(lookupBlockRenderer("text/vnd.mermaid")?.Component).toBe(MermaidPart);
    expect(lookupBlockRenderer("application/x-mermaid")?.Component).toBe(MermaidPart);
  });

  it("normalizes media type parameters before lookup", () => {
    expect(normalizeMediaType("Text/Markdown; charset=utf-8")).toBe("text/markdown");
    expect(lookupBlockRenderer("text/markdown; charset=utf-8")?.Component).toBe(
      lookupBlockRenderer("text/markdown")?.Component,
    );
  });

  it("routes an unknown type through logError + the annotated fallback", () => {
    const spy = vi.spyOn(logErrorModule, "logError").mockImplementation(() => {});
    const element = BlockRendererFor({
      mediaType: "application/x-unknown",
      data: { media_type: "application/x-unknown", content: "x" },
    }) as React.ReactElement<{ mediaType?: string; reason?: string }>;

    expect(element.props.mediaType).toBe("application/x-unknown");
    expect(element.props.reason).toBe("unknown_media_type");
    expect(spy).toHaveBeenCalledTimes(1);
    const call = spy.mock.calls[0][0];
    expect(call.context).toBe("block_renderer_error");
    expect(call.meta).toMatchObject({ mediaType: "application/x-unknown", phase: "lookup" });
  });
});

describe("sandboxed-html payload schema (PR-5B)", () => {
  it("accepts the minimal payload (media_type + content)", () => {
    const parsed = sandboxedHtmlArtifactSchema.safeParse({
      media_type: "artifact/sandboxed-html",
      content: "<p>hello</p>",
    });
    expect(parsed.success).toBe(true);
  });

  it("accepts the PLAN-6 rich metadata fields", () => {
    const parsed = sandboxedHtmlArtifactSchema.safeParse({
      media_type: "artifact/sandboxed-html",
      content: "<p>hello</p>",
      renderer: "sandboxed-preview",
      version: 2,
      provenance: "evidence:valravn",
      preview_url: "/api/artifacts/x?inline=true",
      download_url: "/api/artifacts/x?download=true",
    });
    expect(parsed.success).toBe(true);
    if (parsed.success) {
      expect(parsed.data.version).toBe(2);
      expect(parsed.data.provenance).toBe("evidence:valravn");
    }
  });

  it("rejects a non-string content body", () => {
    const parsed = sandboxedHtmlArtifactSchema.safeParse({
      media_type: "artifact/sandboxed-html",
      content: 42,
    });
    expect(parsed.success).toBe(false);
  });

  it("rejects a different media type discriminator", () => {
    const parsed = sandboxedHtmlArtifactSchema.safeParse({
      media_type: "text/markdown",
      content: "x",
    });
    expect(parsed.success).toBe(false);
  });

  it("routes a schema-invalid payload through logError + fallback", () => {
    const spy = vi.spyOn(logErrorModule, "logError").mockImplementation(() => {});
    const element = BlockRendererFor({
      mediaType: "artifact/sandboxed-html",
      data: { media_type: "artifact/sandboxed-html", content: 42 },
    }) as React.ReactElement<{ mediaType?: string; reason?: string }>;

    expect(element.props.mediaType).toBe("artifact/sandboxed-html");
    expect(element.props.reason).toBe("schema_error");
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy.mock.calls[0][0].meta).toMatchObject({ phase: "schema_pre_render" });
  });

  it("dispatches a valid payload straight to SandboxedPreview", () => {
    const element = BlockRendererFor({
      mediaType: "artifact/sandboxed-html",
      data: { media_type: "artifact/sandboxed-html", content: "<p>ok</p>" },
    }) as React.ReactElement<{ children?: React.ReactElement<{ type?: unknown }> }>;
    // The consumer element is the error boundary; the renderer is its child.
    expect(element.props.children?.type).toBe(SandboxedPreview);
  });
});

describe("artifact content schemas", () => {
  it("artifactContentSchema accepts any media type + content", () => {
    const parsed = artifactContentSchema.safeParse({ media_type: "text/csv", content: "a,b" });
    expect(parsed.success).toBe(true);
  });

  it("artifactContentSchema rejects a missing body", () => {
    const parsed = artifactContentSchema.safeParse({ media_type: "text/csv" });
    expect(parsed.success).toBe(false);
  });

  it("iocTableSchema accepts content with optional structured rows", () => {
    const parsed = iocTableSchema.safeParse({
      media_type: "application/x-munin-ioc-table",
      content: "1.2.3.4\nevil.example",
      rows: [{ value: "1.2.3.4", kind: "ipv4" }],
    });
    expect(parsed.success).toBe(true);
  });

  it("iocTableSchema rejects a non-literal media type", () => {
    const parsed = iocTableSchema.safeParse({ media_type: "text/csv", content: "a,b" });
    expect(parsed.success).toBe(false);
  });

  it("re-registering after reset is deterministic (test isolation)", () => {
    __resetBlockRegistryForTests();
    expect(lookupBlockRenderer("text/markdown")).toBeNull();
  });
});
