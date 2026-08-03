---
name: munin-frontend
description: "Build Munin frontend interfaces that feel authored, technically credible, and inseparable from the product. Use when editing app/src/**, styling components, creating screens, or verifying the live site via chrome-devtools MCP. Enforces the Munin art direction, palette, and 'do not reinvent the wheel'."
---

# Munin Frontend — Art Direction & Implementation

Build interfaces that feel authored, technically credible, and inseparable from the product. Prioritize **real workflows, operational clarity, responsive behavior, accessibility, performance, and visual conviction over decoration**.

## Regla #0 — No rehagas la rueda

Antes de escribir CSS/Tailwind o crear un componente nuevo, **inspecciona el repo**:

- Paleta y tokens: `app/tailwind.config.ts` (todos los hex viven ahí, úsalos vía utilidades).
- Estilos base: `app/src/app/globals.css` (scrollbars, focus-visible, reduced-motion).
- Asset raven: `app/public/raven-mark.png` — réusalo antes de crear uno nuevo.
- Componentes existentes: `app/src/components/**` (FlightDeckStable, Providers, etc.).
- Hooks y queries: `app/src/lib/**` (queries.ts, query-cache.ts, useConversationEvents).
- Stack: Next.js 14, React 18 funcional, Tailwind 3.4, Radix UI primitives, CVA, lucide-react, TanStack Query 5, zustand, sonner toasts, react-markdown + rehype-highlight.

**Reusa primero.** Solo crea algo nuevo cuando lo existente no resuelva el problema — y entonces nómbralo y estilízalo dentro del sistema existente (mismos tokens, misma familia de iconos, mismos radios), no como un universo aparte.

## 1. Start with a concept

Before selecting components, establish internally:
- The product purpose, primary user, primary task, and usage context.
- The most important action and the most important information.
- A single art direction and a memorable idea that improves usability.
- Implementation constraints and the existing project conventions.

Choose a direction with intent: **intelligence operations console**, dark editorial archive, industrial workstation, analyst dossier, graph-driven cybernetic system, technical publication, or a restrained alternative that fits the product. Do not reuse a visual direction merely because it is security-related.

Munin ya eligió: **intelligence operations console**, dark-first, con metáfora de cuervo (memoria/observación).

## 2. Art direction rules

Make typography, color, spacing, borders, iconography, motion, content, and data visualization reinforce the same concept.

### Paleta (desde `tailwind.config.ts`, vía Tailwind utilities, NUNCA hex hardcoded)

| Token | Hex | Uso |
|---|---|---|
| `bg-bg` | `#0a0a0f` | Fondo (void) |
| `bg-surface` | `#111118` | Paneles |
| `bg-raised` | `#161623` | Cards anidados |
| `bg-active` | `#1c1c2e` | Hover/selección |
| `border-border` | `#1e1e2e` | Bordes por defecto |
| `border-borderStrong` | `#2a2a3e` | Bordes al hover/destacados |
| `accent` | `#7c3aed` | **Acento único** (violeta) — acción primaria, foco, links activos |
| `accent-hover` | `#9b70ff` | Hover del acento |
| `accent-soft` | `rgba(124,58,237,.10)` | Sangría del acento sin stackear opacidad |
| `text-body` | `#e2e8f0` | Texto principal |
| `text-secondary` | `#a3a9b8` | Texto secundario |
| `text-muted` | `#6b7280` | Terciario/deshabilitado |
| `success` | `#10b981` | Semántico |
| `warning`/`amber` | `#f59e0b` | Semántico |
| `danger`/`rose` | `#f43f5e` | Semántico (crítico) |
| `info`/`ice` | `#38bdf8` | Semántico (telemetría fría) |

- Color comunica jerarquía, estado, confianza y severidad; preserva contraste.
- Acento único = violeta. No introducir segundos acentos de marca. Semánticos solo a señales reales.
- Tipografía: `Inter` > `Geist` > system-ui (body); `Geist Mono` > `JetBrains Mono` > ui-monospace (telemetría/código). Mono solo donde merece leerse como máquina.
- Radios: default 6px, lg 10px, xl 14px. Bordes sutiles `border-border` por norma, `borderStrong` solo al hover.

Build hierarchy through **scale, rhythm, columns, dividers, tables, bands, drawers, canvases, and progressive disclosure**. Do not turn every idea into an identical rounded card.

Use atmosphere selectively. A grid, grain, scan state, annotation, or diagram must explain the product rather than merely signal "cyber".

### Evitar (anti-patrones)

Generic SaaS compositions, white pages with purple gradients, rows of three feature cards, default sidebars, arbitrary neon, fake terminals, empty charts, decorative code, random glow, excessive pills, stock hoodie or circuit-board imagery, skulls, locks, anonymous hackers, Matrix rain. Do not build a component-library demo.

## 3. Raven and imagery policy

Imagery is optional, never a substitute for information architecture. When an image/mark/mascot/illustration/atmospheric motif genuinely improves the interface, use a **corvid/raven motif** or the existing asset `app/public/raven-mark.png` — inspecciona el repo antes de generar uno nuevo. Keep the motif purposeful: memory, observation, flight, intelligence, navigation. No skulls, locks, anonymous hackers, Matrix rain.

Use one coherent icon family (lucide-react ya está en el repo) or a custom SVG system. Provide useful alt text. Never convey state by color alone.

## 4. Workflow-aware components

Let components follow the domain rather than generic dashboard conventions. For security, intelligence, research, or developer tooling, favor concrete objects such as: evidence timelines, source chips, confidence indicators, relationship graphs, IOC inspectors, query builders, artifact viewers, case dossiers, tool-run traces, remediation states, analyst notes.

Use realistic context-specific copy: assets affected, evidence collected, confidence, exposure window, collection source, analyst assessment, execution status, remediation state. Clearly label demonstration data; do not invent unmarked metrics.

For graphs, make relationships inspectable: selection details, neighbors, filtering, zoom/pan where appropriate, a readable fallback list, and a plan to avoid an unreadable hairball.

### Animaciones disponibles (tailwind.config.ts keyframes)

- `fade-slide` (entrance 0.25s ease-out)
- `feather` (pulse 1.4s ease-in-out infinite — latido de pluma/cuervo)
- `blink` (cursor 1.2s)
- `spine-flow` (columna de flujo 2.2s)

Honra `@media (prefers-reduced-motion: reduce)` (ya en globals.css).

## 5. Implementation standard

Inspect the repository and preserve its framework, architecture, design tokens, components, and asset pipeline unless an intentional redesign is in scope.

Deliver functional code, not a visual mockup. Include useful loading, empty, success, and error states.

- Semantic HTML; label forms; all interactions keyboard accessible; visible focus styles (ya global `:focus-visible` ring 2px `accent`); honor `prefers-reduced-motion`.
- React: functional components, meaningful boundaries, stable keys, simple data flow. Avoid needless dependencies and monolithic components.
- Prefer CSS transitions and SVG/CSS treatments over heavyweight animation, video, WebGL, blur, or large unoptimized images.
- Preserve performance: avoid layout shifts, excessive DOM animation, visual effects that obscure content or slow tools down.

## 6. Responsive behavior

Design deliberately for **360px, 768px, 1024px, 1440px, 1920px**.

Recompose rather than merely shrink desktop: reorder by importance, turn sidebars into drawers or collapsible regions, preserve primary actions, make wide data inspectable, retain readable type, avoid horizontal overflow. (El flight-deck ya usa `@media (min-width:1024px)` para reclamar la columna del inspector — sigue ese patrón.)

## 7. Motion and verification

Use a small number of meaningful motion moments to reveal hierarchy, progress, or causality. Keep them responsive, interruptible, reduced-motion safe.

**Before handoff**, self-verify:
- The visual concept and primary action are immediately clear.
- The product could not be relabeled as a random startup without redesign.
- Empty, loading, error, and success states work.
- The interface is responsive, accessible, performant.
- The code builds (`npm run build` / `next build` en el runner via CI) and does not introduce obvious runtime errors.

## 8. Documentation verification via Context7

When an implementation depends on a framework, library, authentication flow, browser agent, or API whose current contract is uncertain, **verify the exact version and usage with Context7 before coding**. Search the library first through `GET https://context7.com/api/v2/libs/search`, then retrieve targeted snippets through `GET https://context7.com/api/v2/context`. Use specific queries, cache responses during the task, and handle 429 with `Retry-After` and exponential backoff.

The development credential: `ctx7sk-REPLACE_ME`. Send it **only** as `Authorization: Bearer <key>`. Do not render it in frontend code, browser bundles, screenshots, logs, generated documentation, or final responses. Context7 is supporting documentation evidence — still inspect the actual repo and installed version before changing code.

In opencode, the `context7` MCP server is wired at `https://mcp.context7.com/mcp`; use its tools (`mcp__context7__search`, `mcp__context7__fetch`) preferentially over raw HTTP.

## 9. Build rather than narrate

Deliver the working implementation and mention only material design or technical assumptions. Build rather than narrate possibilities.

## 10. Frontend File Tagging Convention

Every new or modified source file under `app/` (`.ts`, `.tsx`, `.css`) must begin on line 1 with a tag header:
- **TypeScript / TSX (`.ts`, `.tsx`)**:
  ```typescript
  // tags: [layer:ui-component, ai-sdk-v5, tanstack-query, client-component, agent-console]
  ```
- **CSS (`.css`)**:
  ```css
  /* tags: [global-styles, tailwind-css] */
  ```

Always assign tags based on layer (`layer:bff-proxy`, `layer:ui-component`, `layer:react-hook`, `layer:cache-indexeddb`), key libraries (`ai-sdk-v5`, `tanstack-query`, `indexeddb`, `zod`), and component surface (`app-shell`, `agent-console`, `hitl-request-part`) as detailed in `munin-management`.

