// tags: [tests, sandboxed-preview, iframe, csp, PR-5A, security-contract, vitest]
// -----------------------------------------------------------------------------
// PR-5A — SandboxedPreview security-contract tests.
//
// The component's iframe sandbox token set and CSP meta are THE security
// contract: ``sandbox="allow-scripts"`` exactly (never ``allow-same-origin``)
// plus a CSP that blocks every external load. These constants are exported
// from the component and asserted here verbatim so a future edit that
// weakens the contract fails the suite immediately (the Playwright e2e spec
// asserts the same strings in a live browser).
//
// Rendering is exercised with ``react-dom/server.renderToString`` (the
// component is a pure memo + useMemo render — no DOM hooks needed), so the
// invalid-payload → ``logError`` + safe-fallback path is verified without a
// browser.
// -----------------------------------------------------------------------------
import { afterEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { renderToString } from "react-dom/server";

import * as logErrorModule from "@/lib/logError";
import {
  SANDBOX_ATTRIBUTES,
  SANDBOX_CSP,
  buildSandboxedDocument,
} from "@/lib/sandboxContract";
import { SandboxedPreview } from "@/components/chat/blocks/parts/SandboxedPreview";

const INLINE_OK_HTML = '<script>document.body.dataset.ok = "1";</script><p>report</p>';

describe("sandbox attribute contract (5A)", () => {
  it("is exactly allow-scripts", () => {
    expect(SANDBOX_ATTRIBUTES).toBe("allow-scripts");
  });

  it("never grants same-origin", () => {
    expect(SANDBOX_ATTRIBUTES).not.toContain("allow-same-origin");
  });
});

describe("CSP contract (5A)", () => {
  it("blocks every default source", () => {
    expect(SANDBOX_CSP).toContain("default-src 'none'");
  });

  it("allows inline scripts but nothing external", () => {
    expect(SANDBOX_CSP).toContain("script-src 'unsafe-inline'");
    expect(SANDBOX_CSP).toContain("style-src 'unsafe-inline'");
  });

  it("restricts images to data URIs", () => {
    expect(SANDBOX_CSP).toContain("img-src data:");
  });

  it("does not permit connect-src, frame-src or external hosts", () => {
    expect(SANDBOX_CSP).not.toContain("connect-src");
    expect(SANDBOX_CSP).not.toContain("http:");
  });
});

describe("buildSandboxedDocument (5A)", () => {
  it("injects the CSP meta into the head", () => {
    const doc = buildSandboxedDocument(INLINE_OK_HTML);
    expect(doc).toContain("<head>");
    expect(doc).toContain('http-equiv="Content-Security-Policy"');
    expect(doc).toContain(`content="${SANDBOX_CSP}"`);
    // The CSP meta must precede any payload content.
    const headEnd = doc.indexOf("</head>");
    const bodyStart = doc.indexOf("<body>");
    expect(headEnd).toBeGreaterThan(-1);
    expect(bodyStart).toBeGreaterThan(headEnd);
  });

  it("places the payload inside the body", () => {
    const doc = buildSandboxedDocument(INLINE_OK_HTML);
    expect(doc).toContain(`<body>${INLINE_OK_HTML}</body>`);
    expect(doc).toContain("<!doctype html>");
  });

  it("srcdoc is memo-stable: identical content yields an identical string", () => {
    expect(buildSandboxedDocument("a")).toBe(buildSandboxedDocument("a"));
    expect(buildSandboxedDocument("a")).not.toBe(buildSandboxedDocument("b"));
  });
});

describe("SandboxedPreview render contract (5A)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the hardened iframe for a valid payload", () => {
    const html = renderToString(
      createElement(SandboxedPreview, { content: INLINE_OK_HTML, filename: "report.html" }),
    );
    expect(html).toContain('sandbox="allow-scripts"');
    expect(html).not.toContain("allow-same-origin");
    expect(html).toContain('srcDoc="');
    // Attribute quotes inside srcdoc are HTML-escaped by the SSR serializer.
    expect(html).toContain("http-equiv=&quot;Content-Security-Policy&quot;");
    expect(html).toContain("sandboxed preview");
  });

  it("logs via logError and renders the safe fallback for an invalid payload", () => {
    const spy = vi.spyOn(logErrorModule, "logError").mockImplementation(() => {});
    const html = renderToString(
      // Casting through unknown: the component contract expects a validated
      // string, and the guard is exactly about surviving a bad runtime value.
      createElement(SandboxedPreview, { content: 1234 as unknown as string, filename: "evil.html" }),
    );

    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy.mock.calls[0][0].context).toBe("sandboxed_preview");
    expect(spy.mock.calls[0][0].meta).toMatchObject({ filename: "evil.html" });
    // The fallback must NOT render an iframe or interpolate the payload.
    expect(html).not.toContain("<iframe");
    expect(html).not.toContain("1234");
    expect(html).toContain("Preview unavailable");
    expect(html).toContain('role="alert"');
  });

  it("logs and falls back for an empty payload", () => {
    const spy = vi.spyOn(logErrorModule, "logError").mockImplementation(() => {});
    const html = renderToString(createElement(SandboxedPreview, { content: "   " }));
    expect(spy).toHaveBeenCalledTimes(1);
    expect(html).not.toContain("<iframe");
  });
});
