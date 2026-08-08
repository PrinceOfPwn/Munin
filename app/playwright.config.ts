// tags: [playwright, e2e, config, PR-5C, security, sandboxed-iframe]
// -----------------------------------------------------------------------------
// PR-5C — Playwright runner config for the security lab specs.
//
// The sandboxed-iframe spec is SELF-CONTAINED: it builds a throwaway page via
// ``page.setContent`` and loads the sandbox contract through a real browser
// (srcdoc, opaque origin, CSP) without needing the Munin server. The
// ``PLAYWRIGHT_BASE_URL`` env var is honoured for any future spec that does
// talk to the app (default: the local dev server).
//
// CI note: the verified v1.0.0 CI does not run Playwright yet. These specs
// are CI-ready — ``npx playwright test`` after ``npx playwright install
// chromium`` — but wiring them into GitHub Actions belongs to a separate,
// operator-owned change (workflows are off-limits for the rendering wave).
// -----------------------------------------------------------------------------
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  outputDir: "test-results",
  timeout: 30_000,
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:3000",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
