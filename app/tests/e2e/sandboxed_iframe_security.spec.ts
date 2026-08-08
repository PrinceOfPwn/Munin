// tags: [playwright, e2e, sandboxed-iframe, security, PR-5C, csp, opaque-origin]
// -----------------------------------------------------------------------------
// PR-5C — live-browser security lab for the sandboxed-iframe contract.
//
// The iframe host, CSP meta and payload wrapper are the EXACT production
// strings: the spec imports ``SANDBOX_ATTRIBUTES``, ``SANDBOX_CSP`` and
// ``buildSandboxedDocument`` from ``app/src/lib/sandboxContract.ts`` (the
// same module ``SandboxedPreview`` renders) via a plain relative import — no
// bundler aliases, so Playwright can resolve it as-is.
//
// Self-contained by design: the page is built with ``page.setContent``, so
// NO Munin server is required. Network attempts are recorded with a page
// route and must stay EMPTY for every prohibited resource class:
//
//   * any request to the host API (``/api/*``) — blocked by ``default-src
//     'none'``,
//   * any request to an external host (``evil.test``) — blocked by CSP for
//     scripts/images and by the opaque origin for everything else.
//
// Every payload writes ``data-*`` outcome attributes on the iframe body so
// the lab asserts what ran (and what never happened). A broken contract
// (sandbox without tokens, or a missing CSP) fails these tests in a real
// Chromium, which renderToString-style unit tests cannot observe.
// -----------------------------------------------------------------------------
import { expect, test, type Page } from "@playwright/test";

import {
  SANDBOX_ATTRIBUTES,
  SANDBOX_CSP,
  buildSandboxedDocument,
} from "../../src/lib/sandboxContract";
import {
  CONTROL_PAYLOAD,
  EXTERNAL_IMAGE_PAYLOAD,
  EXTERNAL_SCRIPT_PAYLOAD,
  FETCH_ATTACK_PAYLOAD,
  MALICIOUS_HTML_PAYLOADS,
  PARENT_ACCESS_PAYLOAD,
  STORAGE_ACCESS_PAYLOAD,
} from "../fixtures/malicious_html_payloads";

const SANDBOXED_IFRAME = '#sandboxed[name="sandboxed"]';

async function mountSandboxedPayload(
  page: Page,
  payloadHtml: string,
): Promise<void> {
  const documentHtml = buildSandboxedDocument(payloadHtml);
  const escaped = documentHtml
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/"/g, "&quot;");
  await page.setContent(
    [
      "<!doctype html><html><body>",
      `<iframe id="sandboxed" name="sandboxed" `,
      `sandbox="${SANDBOX_ATTRIBUTES}" srcdoc="${escaped}"></iframe>`,
      "</body></html>",
    ].join(""),
  );
  // Mount marker on the host page: proves later that parent-DOM attacks never
  // touched it (a broken sandbox would have replaced this marker).
  await page.evaluate(() => {
    document.body.setAttribute("data-host", "pristine");
  });
}

test.describe("sandboxed iframe security contract (PR-5C)", () => {
  // Per-test network sweep, shared with the route handler via closure.
  const harnessRef: { apiRequests: string[]; externalRequests: string[] } = {
    apiRequests: [],
    externalRequests: [],
  };

  test.beforeEach(async ({ page }) => {
    harnessRef.apiRequests = [];
    harnessRef.externalRequests = [];
    await page.route("**/*", (route) => {
      const url = route.request().url();
      if (url.includes("/api/")) {
        harnessRef.apiRequests.push(url);
      }
      if (url.includes("evil.test")) {
        harnessRef.externalRequests.push(url);
      }
      void route.abort();
    });
  });

  test("renders the EXACT production contract in the live iframe", async ({
    page,
  }) => {
    await mountSandboxedPayload(page, CONTROL_PAYLOAD);

    const iframe = page.locator(SANDBOXED_IFRAME);
    const sandboxAttr = await iframe.getAttribute("sandbox");
    expect(sandboxAttr).toBe(SANDBOX_ATTRIBUTES);
    expect(sandboxAttr).not.toContain("allow-same-origin");
    expect(sandboxAttr).not.toContain("allow-top-navigation");

    const srcdoc = await iframe.getAttribute("srcdoc");
    expect(srcdoc).toContain('http-equiv="Content-Security-Policy"');
    expect(srcdoc).toContain(`content="${SANDBOX_CSP}"`);

    // Control: inline scripts DO run (the sandbox is not a no-scripts cage).
    await expect(
      page.frameLocator(SANDBOXED_IFRAME).locator("body"),
    ).toHaveAttribute("data-ran", "1");
  });

  test("blocks every request to the host API", async ({ page }) => {
    await mountSandboxedPayload(page, FETCH_ATTACK_PAYLOAD);

    const body = page.frameLocator(SANDBOXED_IFRAME).locator("body");
    await expect(body).toHaveAttribute("data-fetch", "rejected");
    await page.waitForTimeout(500);
    expect(harnessRef.apiRequests).toEqual([]);
  });

  test("gives the payload an opaque origin: parent DOM access throws", async ({
    page,
  }) => {
    await mountSandboxedPayload(page, PARENT_ACCESS_PAYLOAD);

    const body = page.frameLocator(SANDBOXED_IFRAME).locator("body");
    await expect(body).toHaveAttribute("data-parent", "SecurityError");
    // The host page is untouched — no "pwned" content ever landed there.
    await expect(page.locator("body")).toHaveAttribute("data-host", "pristine");
    await expect(page.getByText("pwned")).toHaveCount(0);
  });

  test("blocks cookie and storage access from inside the sandbox", async ({
    page,
  }) => {
    await mountSandboxedPayload(page, STORAGE_ACCESS_PAYLOAD);

    const body = page.frameLocator(SANDBOXED_IFRAME).locator("body");
    await expect(body).toHaveAttribute("data-storage", "SecurityError");
  });

  test("never loads an external script", async ({ page }) => {
    await mountSandboxedPayload(page, EXTERNAL_SCRIPT_PAYLOAD);

    const body = page.frameLocator(SANDBOXED_IFRAME).locator("body");
    await expect(body).toHaveAttribute("data-inline-ran", "1");
    await page.waitForTimeout(500);
    expect(harnessRef.externalRequests).toEqual([]);
  });

  test("never loads an external image", async ({ page }) => {
    await mountSandboxedPayload(page, EXTERNAL_IMAGE_PAYLOAD);

    await page.waitForTimeout(500);
    expect(harnessRef.externalRequests).toEqual([]);
  });

  test.describe("parametrized fixture sweep", () => {
    for (const { name, html } of MALICIOUS_HTML_PAYLOADS) {
      test(`${name}: payload mounts without breaking the host page`, async ({
        page,
      }) => {
        await mountSandboxedPayload(page, html);
        await page.waitForTimeout(300);
        await expect(page.locator("body")).toHaveAttribute("data-host", "pristine");
      });
    }
  });
});
