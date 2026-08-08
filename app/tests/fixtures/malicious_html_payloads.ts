// tags: [e2e, fixtures, malicious-html, payloads, PR-5C, security]
// -----------------------------------------------------------------------------
// PR-5C — adversarial HTML payloads for the sandboxed-iframe security spec.
//
// Each fixture is the RAW BODY of an HTML artifact (no document wrapper). The
// spec wraps it with the real production contract via
// ``buildSandboxedDocument`` — the exact srcdoc ``SandboxedPreview`` renders —
// so the browser lab exercises the same CSP + sandbox strings as production.
//
// Every payload records its outcome as ``data-*`` attributes on the iframe
// document's ``<body>`` so the spec can assert what happened (and what never
// happened) inside the sandbox.
// -----------------------------------------------------------------------------

/** Inline script runs (proves ``allow-scripts`` + ``unsafe-inline`` work). */
export const CONTROL_PAYLOAD = `
  <script>document.body.setAttribute('data-ran', '1');</script>
  <p id="control">benign inline script</p>
`;

/** Marked fetch to the host API — CSP ``default-src 'none'`` must kill it. */
export const FETCH_ATTACK_PAYLOAD = `
  <script>
    document.body.setAttribute('data-attack', 'fetch');
    fetch('/api/chat', { method: 'POST', body: JSON.stringify({ pwn: true }) })
      .then(() => document.body.setAttribute('data-fetch', 'resolved'))
      .catch(() => document.body.setAttribute('data-fetch', 'rejected'));
  </script>
`;

/** Attempt to touch the parent document — the opaque origin must throw. */
export const PARENT_ACCESS_PAYLOAD = `
  <script>
    document.body.setAttribute('data-attack', 'parent');
    try {
      window.parent.document.body.innerHTML = 'pwned';
      document.body.setAttribute('data-parent', 'resolved');
    } catch (e) {
      document.body.setAttribute('data-parent', e.name);
    }
  </script>
`;

/** Attempt to read the host's cookies/storage — must throw as well. */
export const STORAGE_ACCESS_PAYLOAD = `
  <script>
    document.body.setAttribute('data-attack', 'storage');
    try {
      const read = { cookie: document.cookie, local: localStorage.length };
      document.body.setAttribute('data-storage', JSON.stringify(read));
    } catch (e) {
      document.body.setAttribute('data-storage', e.name);
    }
  </script>
`;

/** External <script src> — CSP must never let the network request happen. */
export const EXTERNAL_SCRIPT_PAYLOAD = `
  <script>document.body.setAttribute('data-inline-ran', '1');</script>
  <script src="https://evil.test/pwn.js"></script>
`;

/** External image — CSP ``img-src data:`` must never let it load. */
export const EXTERNAL_IMAGE_PAYLOAD = `
  <img src="https://evil.test/pixel.png" alt="tracking pixel">
`;

/** Full fixture list for the parametrized spec cases. */
export const MALICIOUS_HTML_PAYLOADS = [
  { name: "fetch-to-host-api", html: FETCH_ATTACK_PAYLOAD },
  { name: "parent-dom-access", html: PARENT_ACCESS_PAYLOAD },
  { name: "storage-access", html: STORAGE_ACCESS_PAYLOAD },
  { name: "external-script-src", html: EXTERNAL_SCRIPT_PAYLOAD },
  { name: "external-image-src", html: EXTERNAL_IMAGE_PAYLOAD },
] as const;
