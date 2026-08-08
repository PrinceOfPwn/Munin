// tags: [ui-component, sandboxed-preview, iframe, csp, artifact-renderer, PR-5A, client-component, react-memo, lucide-icons]
"use client";
// -----------------------------------------------------------------------------
// PR-5A — SandboxedPreview: hardened iframe host for LLM/user-generated HTML.
//
// Security contract (EXACT — the e2e spec in ``app/tests/e2e/`` asserts these
// constants verbatim):
//   * ``sandbox="allow-scripts"`` ONLY. ``allow-same-origin`` is deliberately
//     absent, so the frame gets an opaque origin: it can neither reach the
//     parent document nor carry the host's cookies/storage identity.
//   * A CSP ``<meta http-equiv="Content-Security-Policy">`` is injected into
//     the iframe head: ``default-src 'none'`` blocks every fetch/connect,
//     ``script-src 'unsafe-inline'`` lets the payload's inline scripts run
//     (they are the point of a live preview), ``style-src 'unsafe-inline'``
//     keeps embedded styles working and ``img-src data:`` restricts images
//     to inline data URIs. External scripts/images are therefore impossible
//     to load even if a payload tries.
//
// The HTML source arrives as a Zod-validated string (see the block registry
// in ``../registry.ts`` — PR-5B). An invalid payload never reaches this
// component's iframe: the registry logs via ``logError`` and renders its
// fallback card; this component additionally defends the render itself with
// the ``logError`` contract (never a silent catch).
//
// srcdoc stability: the document string is derived with ``useMemo`` keyed on
// ``content`` only, so an unchanged payload yields a referentially stable
// srcdoc and React never re-sets the attribute (no reload churn / render
// loops while the parent console re-renders streamed siblings).
// -----------------------------------------------------------------------------
import { memo, useMemo } from "react";
import { ShieldAlert } from "lucide-react";

import { logError } from "@/lib/logError";
import { cn } from "@/lib/utils";
import {
  buildSandboxedDocument,
  SANDBOX_ATTRIBUTES,
  SANDBOX_CSP,
} from "@/lib/sandboxContract";

// ---------------------------------------------------------------------------
// Security contract constants — single source of truth lives in
// ``app/src/lib/sandboxContract.ts`` (shared with the vitest contract tests
// and the Playwright e2e security spec without any ``@/`` alias resolution).
// Re-exported here so the parts barrel keeps its public surface stable.
// ---------------------------------------------------------------------------

export { buildSandboxedDocument, SANDBOX_ATTRIBUTES, SANDBOX_CSP };

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface SandboxedPreviewProps {
  /** Zod-validated sandboxed-html body (see ``sandboxedHtmlArtifactSchema``). */
  content: string;
  /** Display name for the fallback card / aria-label. */
  filename?: string;
  downloadUrl?: string;
  previewUrl?: string;
}

export const SandboxedPreview = memo(function SandboxedPreview({
  content,
  filename,
  downloadUrl,
  previewUrl,
}: SandboxedPreviewProps) {
  // Runtime defense-in-depth: the registry validates before dispatch, but a
  // direct consumer could still hand us garbage. The payload MUST be a
  // string — anything else is logged and rendered as a safe fallback, never
  // interpolated into srcdoc.
  const payloadValid = typeof content === "string" && content.trim().length > 0;
  const srcdoc = useMemo(
    () => (payloadValid ? buildSandboxedDocument(content) : ""),
    [content, payloadValid],
  );

  if (!payloadValid) {
    logError({
      context: "sandboxed_preview",
      error: new TypeError("sandboxed-html artifact payload is not a non-empty string"),
      meta: { filename, hasContent: typeof content === "string", previewUrl, downloadUrl },
      ts: new Date().toISOString(),
    });
    return (
      <div
        role="alert"
        aria-label="Sandboxed preview unavailable"
        className={cn(
          "flex w-full items-start gap-2 rounded-md border border-danger/40 bg-danger/5",
          "px-3 py-2 text-xs text-danger",
        )}
      >
        <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
        <div className="min-w-0">
          <p className="font-semibold uppercase tracking-wide">Preview unavailable</p>
          <p className="mt-0.5 break-words text-danger/70">
            The HTML payload failed validation and was not rendered.
            {filename ? (
              <>
                {" "}
                <code className="font-mono">{filename}</code>
              </>
            ) : null}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex w-full max-w-full flex-col items-start gap-2">
      <div className="flex w-full items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-xs text-secondary">
          <ShieldAlert className="h-3.5 w-3.5 text-accent" aria-hidden />
          <span>sandboxed preview</span>
        </span>
        {downloadUrl ? (
          <a
            href={downloadUrl}
            download
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-muted transition-colors hover:text-accent"
          >
            download html
          </a>
        ) : null}
      </div>
      <iframe
        title={`Sandboxed HTML preview${filename ? ` — ${filename}` : ""}`}
        // The security contract: allow-scripts ONLY, no allow-same-origin.
        sandbox={SANDBOX_ATTRIBUTES}
        srcDoc={srcdoc}
        className="h-96 w-full rounded-md border border-border bg-bg"
      />
    </div>
  );
});
