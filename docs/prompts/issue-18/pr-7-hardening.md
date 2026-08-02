# Prompt PR-7 — Production Hardening: a11y, Atajos, Virtualización, Test de Estrés

> Issue: #18 · Fase 3 · Ola 3 · **Requiere Ola 2 (PR-1, PR-2, PR-3, PR-4, PR-5, PR-6) mergeado en `main`**
> Este es el PR final que consolida la experiencia AI-native y la hace production-ready.
> Contexto compartido obligatorio: `docs/prompts/issue-18/00-master.md` — léelo primero.

---

## 1. Instrucciones para la IA ejecutora

Tras Ola 2 la consola Munin es funcional pero no está pulida. Tu trabajo es llevar el frontend a estándares de producción:

1. **Accesibilidad WCAG AA**: roles ARIA en partes dinámicas, focus management, screen reader announcer para eventos SSE.
2. **Atajos de teclado**:捧 abrir command palette, ⌘K focus prompt input, ⌘B toggle sidebar, ⌘J toggle workspace, Esc cerrar dialogs/HITL.
3. **Virtualización de lists largas**: 5000 eventos en una conversación sin lag.
4. **Test de estrés E2E**: playwright o cypress corriendo una conversación mock con 5000 tool_output events y midiendo FPS / tiempo de scroll.
5. **Documentación operator**: actualiza `README.md` y `docs/operator-guide.md` con el nuevo flujo UI.

---

## 2. Rutas Permitidas

- `app/src/index.css` / `app/src/app/globals.css` (EDITAR — focus-visible rings consistentes)
- `app/src/hooks/useKeyboardShortcuts.ts` (NUEVO)
- `app/src/components/KeyboardHelpDialog.tsx` (NUEVO)
- `app/src/components/AgentConsole.tsx` (EDITAR — virtualización con `@tanstack/react-virtual`)
- `app/src/components/ai-elements/Message.tsx` (EDITAR — atributos ARIA)
- `app/src/components/ai-elements/Conversation.tsx` (EDITAR — aria-live region anunciadora)
- `app/src/components/FlightDeckStable.tsx` (EDITAR — atajos)
- `app/src/extensions/registry.tsx` (EDITAR — añadir `a11y` metadata a widgets)
- `CHANGELOG.md` (EDITAR)
- `README.md` (EDITAR — sección "Frontend AI-native console" + screenshot)
- `docs/operator-guide.md` (EDITAR)
- `app/tests/perf.test.ts` (NUEVO — test de estrés con mock streams)
- `app/playwright/perf.spec.ts` (NUEVO — playwright specs)
- `changes.md` (AÑADIR entrada)

### Rutas Prohibidas
- `munin/**`
- `app/src/types/**`
- `app/src/renderers/registry.ts`
- `soul/**`

---

## 3. Spec: a11y

### 3.1 ARIA roles en partes dinámicas

Cada `Message` debe tener:
- `role="article"` en el wrapper.
- `aria-label` `{role} message at {timestamp}`.
- `aria-live="polite"` cuando contiene contenido streaming.

Cada `ToolCard`:
- `role="region"`.
- `aria-label` `tool {tool_name} {state}`.
- `aria-busy="true"` cuando `state==="running"`.

Cada `HumanInterruptCard`:
- `role="alertdialog"` (Radix AlertDialog ya lo hace, pero verifica).
- `aria-label` `Operator decision required: {prompt}`.
- Focus trap activo (Radix AlertDialog ya lo hace).

### 3.2 Announcer para eventos SSE

Añade un `<div aria-live="polite" role="status" className="sr-only" />` oculto en `Conversation.tsx`. Entra cada nuevo evento SSE:
- `run_state` change → announce `Run {state}`.
- `artifact` → announce `Artifact created: {filename}`.
- `human_interrupt` → announce `Decision required from operator`.
- `tool_failed` → announce `Tool {tool_name} failed: {error}`.

Implementa un helper `useAnnouncer()` que mantenga un ref al div y un string concatenado (no más de 200 chars, oldest-first FIFO):

```tsx
function useAnnouncer() {
  const ref = useRef<HTMLDivElement>(null);
  const announce = useCallback((text: string) => {
    if (ref.current) {
      // Trick: clear first then set in next tick para screen readers
      ref.current.textContent = "";
      setTimeout(() => { if (ref.current) ref.current.textContent = text; }, 50);
    }
  }, []);
  return { ref, announce };
}
```

### 3.3 Focus-visible rings consistentes en `globals.css`

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
```

---

## 4. Spec: Atajos de Teclado

### `useKeyboardShortcuts.ts`

```tsx
const SHORTCUTS = {
  "mod+k": () => focusPromptInput(),       // Mod = ⌘ en mac, Ctrl en win/linux
  "mod+b": () => toggleSidebar(),
  "mod+j": () => toggleWorkspace(),
  "mod+/": () => openKeyboardHelpDialog(),
  "escape": () => closeTopmostDialog(),
} as const;
```

Implementación:
- Hook global `useEffect` con `keydown` listener en `window`.
- `mod` significa `event.metaKey || event.ctrlKey`.
- Cuando `event.target` es un `input`/`textarea`/`[contenteditable]`:
  - Solo `escape` y `mod+k` se interceptan (no interferir con escritura).
  - Otros atajos se ignoran.
- `preventDefault` y `stopPropagation` cuando se intercepta.
- `KeyboardHelpDialog`: modal Radix que lista todos los atajos. Trigger por `mod+/`.

---

## 5. Spec: Virtualización

### `AgentConsole.tsx`

La lista de messages puede crecer a miles de items con tool_output streams largos. Usa `@tanstack/react-virtual` (verificar si ya instalado; si no, instalar en `app/package.json` junto con PR-7):

```tsx
import { useVirtualizer } from "@tanstack/react-virtual";

function VirtualizedConversation({ messages }: { messages: UIMessage[] }) {
  const parentRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 200,            //altura media estimada por message
    overscan: 8,
  });

  return (
    <div ref={parentRef} className="h-full overflow-y-auto">
      <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
        {virtualizer.getVirtualItems().map(vItem => (
          <div
            key={messages[vItem.index].id}
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              transform: `translateY(${vItem.start}px)`,
              width: "100%",
            }}
          >
            <Message message={messages[vItem.index]} />
          </div>
        ))}
      </div>
    </div>
  );
}
```

**Cuidado con streaming**: cuando un message está en streaming, no puede colapsarse en altura estática. Implementar `measureElement` dinámico (vía `ref` callback que mide `scrollHeight` y llama `virtualizer.measureElement(el)`). Verificar Context7 `/tanstack/virtual` para la API exacta de `measureElement`.

---

## 6. Spec: Test de Estrés E2E

### `app/tests/perf.test.ts` (vitest)

```ts
describe("Stress test: 5000 SSE events", () => {
  it("renders 5000 tool_output events under 2s incremental paint budget", async () => {
    const { container } = render(<MockConversationWithStream n={5000} />);
    // Mock streams 5000 tool_output con ansi escape codes
    // Assert: tiempo total < 5000ms, scroll rápido después
    // Assert: DOM elements totales < 50000 (no leak de nodos)
  });
});
```

### `app/playwright/perf.spec.ts`

Playwright scenario:
1. Navega a `/` y mock el fetch SSE devolviendo 5000 eventos pre-renderizados.
2. Mide FPS durante scroll rápido.
3. Assert: promedio FPS ≥ 30 durante scroll de 5000 items virtualizados.

```ts
test("5000 events scroll performance", async ({ page }) => {
  await page.route("**/api/chat/**/stream", async route => {
    const events = buildMockEventStream(5000);
    await route.fulfill({ status: 200, contentType: "text/event-stream", body: events });
  });
  await page.goto("/");
  await page.waitForSelector("[role='article']", { timeout: 10000 });
  const metrics = await page.evaluate(() => {
    // fpsObserver using requestAnimationFrame
    // returnar { avgFps, minFps, frames }
  });
  await page.mouse.wheel(0, 50000);
  expect(metrics.avgFps).toBeGreaterThan(30);
});
```

---

## 7. Documentación

### `README.md`

Añade sección (después de la sección de uso existente):

```markdown
## Frontend AI-native console

Munin incluye una consola operacional web AI-native construida con Vercel AI SDK UI v4 + AI Elements (adaptados a React 18.3 / Tailwind 3.4). Tres zonas responsivas:

- **Sidebar**: lista de conversaciones + crear nuevo + historial persistente.
- **Conversation**: flujo de mensajes con auto-scroll inteligente, renderers por part kind (texto, razonamiento, tool cards, terminal ANSI, HITL dialogs).
- **Workspace**: tabs (Artifacts / Evidence / Run / Agents) con 16 renderers generativos + sandboxed HTML iframe para previews de Munin.

Atajos: ⌘K (focus input), ⌘B (sidebar), ⌘J (workspace), ⌘/ (help dialog).
```

### `docs/operator-guide.md`

Añade sección "Using the console" con captura (si hay screenshots en `docs/screenshots/`), explicación de las tres zonas, y referencia a la tabla de renderers de PR-5.

### `changes.md`

Entrada consolidada para el issue #18 completo (referencia los 7 PRs).

---

## 8. Verificación

```bash
cd app
npm install      # si @tanstack/react-virtual nuevo
npm run lint
npm run typecheck
npm run build
npm test
npx playwright test
python -m pytest -q  # backend no afectado
```

## 9. Commit / PR

- Branch: `feat/issue-18-7-hardening`
- Commit: `feat(issue-18-7): production hardening — a11y, keyboard shortcuts, virtualization, stress tests`
- PR contra `main`. **Requiere todos los PRs 1-6 merged.** En el cuerpo del PR:
  - Closes #18
  - Lista los 7 PRs anteriores como dependencias.
  - Adjunta métricas de performance del test de estrés.
