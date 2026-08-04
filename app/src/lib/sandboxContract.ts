// tags: [utility-library, sandbox-contract, csp, iframe, security, PR-5A, PR-5C]
// -----------------------------------------------------------------------------
// PR-5A/5C — the sandboxed-iframe security contract, as a dependency-free
// module shared by:
//   * ``SandboxedPreview`` (the component that renders it),
//   * the vitest suite (``chat/blocks/__tests__/sandboxedPreview.test.ts``),
//   * the Playwright e2e spec (``app/tests/e2e/sandboxed_iframe_security.spec.ts``,
//     which imports this file via a plain relative path — no ``@/`` alias).
//
// THIS FILE IS THE SINGLE SOURCE OF TRUTH for the iframe sandbox token set
// and the CSP meta injected into the srcdoc head. Changing it without the
// vitest contract tests (and the live-browser spec) going green means the
// hardening was weakened.
// -----------------------------------------------------------------------------

/** The exact ``sandbox`` token set: scripts run, same-origin is never granted. */
export const SANDBOX_ATTRIBUTES = "allow-scripts";

/** CSP injected into the iframe head. External loads are impossible. */
export const SANDBOX_CSP =
  "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:;";

/** Wrap a validated payload in a full document carrying the CSP meta in head. */
export function buildSandboxedDocument(html: string): string {
  return [
    "<!doctype html><html><head>",
    `<meta http-equiv="Content-Security-Policy" content="${SANDBOX_CSP}">`,
    `</head><body>${html}</body></html>`,
  ].join("");
}
