# Prompt PR-5 — Artifact Workspace + Renderers Generativos + Sandbox HTML Seguro

> Issue: #18 · Fase 2 · Ola 2 · **Requiere PR-1 mergeado en `main`**
> Ejecutar en paralelo con `pr-3` y `pr-4`.
> Contexto compartido obligatorio: `docs/prompts/issue-18/00-master.md` — léelo primero.

---

## 1. Instrucciones para la IA ejecutora

El Workspace es la **tercera columna** del shell de Munin (PR-3 la reserva como placeholder). Aquí viven los artifacts producidos por Munin y sus tools: reportes, evidencia, snapshots, findings, tablas IOC/CVE, timelines, mapas Mermaid, previews web sandboxed.

Tu trabajo es:
1. Construir el componente `WorkspacePane` con tabs (Artifacts / Evidence / Run / Agents).
2. Implementar los renderers generativos listados en `renderer_key` del schema Zod de PR-1 (`ioc-table`, `cve-assessment`, `timeline`, `evidence`, `json`, `csv-table`, `markdown`, `code`, `diff`, `mermaid`, `graph`, `finding-card`, `screenshot`, `terminal`, `download`, `sandboxed-html`).
3. Construir el `SandboxedHTML` endurecido.

---

## 2. Rutas Permitidas

- `app/src/components/WorkspacePane.tsx` (NUEVO)
- `app/src/components/workspace/` (NUEVO):
  - `ArtifactsTab.tsx`, `EvidenceTab.tsx`, `RunTab.tsx`, `AgentsTab.tsx`
- `app/src/renderers/components/` (NUEVO o EDITAR junto con PR-4):
  - `IOCTable.tsx`, `CVEAssessment.tsx`, `TimelineView.tsx`, `EvidenceCard.tsx`
  - `JSONViewer.tsx`, `CSVTable.tsx`, `MarkdownView.tsx`, `CodeViewer.tsx`, `DiffViewer.tsx`
  - `MermaidDiagram.tsx`, `GraphViewer.tsx`, `FindingCard.tsx`
  - `ScreenshotViewer.tsx`, `DownloadCard.tsx`, `SandboxedHTML.tsx`
- `app/src/hooks/useQueryArtifact.ts` (NUEVO — TanStack Query hook al endpoint de PR-6)
- `app/src/lib/utils/sanitize.ts` (NUEVO — DOMPurify wrapper para HTML userspace)
- `changes.md` (AÑADIR entrada)

### Rutas Prohibidas
- `munin/**`
- `app/src/types/**`
- `app/src/renderers/registry.ts`

---

## 3. Spec: `WorkspacePane.tsx`

```tsx
type WorkspaceTab = "artifacts" | "evidence" | "run" | "agents";

export function WorkspacePane({ conversationId }: { conversationId: string }) {
  const [tab, setTab] = useState<WorkspaceTab>("artifacts");
  return (
    <aside className="h-full bg-surface border-l border-border flex flex-col min-w-0 min-h-0">
      <header className="h-10 flex items-center gap-1 px-3 border-b border-border bg-surface">
        {(["artifacts","evidence","run","agents"] as const).map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cn(
              "px-3 py-1 rounded-md text-xs font-mono uppercase tracking-wide transition-colors",
              tab === t ? "bg-active text-accent border border-borderStrong" : "text-muted hover:text-body border border-transparent"
            )}
          >
            {t}
          </button>
        ))}
      </header>
      <div className="flex-1 min-h-0 overflow-y-auto">
        {tab === "artifacts" && <ArtifactsTab conversationId={conversationId} />}
        {tab === "evidence" && <EvidenceTab conversationId={conversationId} />}
        {tab === "run" && <RunTab conversationId={conversationId} />}
        {tab === "agents" && <AgentsTab conversationId={conversationId} />}
      </div>
    </aside>
  );
}
```

---

## 4. Spec: Tabs

### 4.1 `ArtifactsTab`
- TanStack Query `useQuery(['artifacts', conversationId], () => fetch('/api/chat/'+conversationId+'/artifacts').then(r=>r.json()))` (endpoint en PR-6).
- Lista de artifacts con `filename`, `media_type`, `renderer_key`.
- Click → abre drawer lateral con renderer apropiado por `renderer_key`.
- Drag-and-drop reordenar (futuro); por ahora orden cronológico descendente.

### 4.2 `EvidenceTab`
- Lista archivos generados por tools (`evidence/` reflejado en `artifacts` con `media_type` de imagen/HTML).
- Componente ScreenshotViewer muestra `image/png` con zoom (lightbox) usando `@radix-ui/react-dialog`.
- EvidenceCard con caption, source-tool, timestamp.

### 4.3 `RunTab`
- GET `/api/chat/{conv}/runs/{run}` (endpoint en PR-6).
- Muestra: estado (`RunStatePill` de PR-4), duración, token usage (si viene), graph de decisiones (mini-timeline de eventos).
- Reusa `TerminalOutput` (PR-4) para mostrar el log truncado.

### 4.4 `AgentsTab`
- Estado de subagentes (`SubagentCard` reusado de PR-4) con sus métricas: duración, herramienta usadas, errores.
- Pull de `/api/agents/status` (si existe) o desde eventos `subagent_lifecycle` del stream.

---

## 5. Renderers Generativos (Spec mínima por renderer)

| Renderer | Renderización | Notas |
|---|---|---|
| `ioc-table` | Tabla con cols: Indicator, Type, Confidence, Source, First Seen | Filas con `bg-active` hover. CSV download button. |
| `cve-assessment` | Card con CVE-ID en mono accent (si KEV), CVSS score pill (success/warning/danger por tramo), EPSS bar horizontal, fecha published | Links a NVD |
| `timeline` | Lista vertical de eventos con timestamp mono + dot color | Usa `vertical` border `border-l border-border` |
| `evidence` | Thumbnail + caption + lightbox | Detecta mime-type |
| `json` | Árbol expandible con `text-secondary`, claves `text-muted`, strings accent | `shiki` highlight si está instalado |
| `csv-table` | Tabla con header `bg-raised` sticky | Scroll horizontal contenido en `overflow-x-auto` |
| `markdown` | React-markdown con rehypeRaw disabled, code blocks con shiki | Reusa componente Markdown existente |
| `code` | CodeBlock con copy button, shiki highlight por `language`, line-numbers opt in | Adaptación de AI Elements CodeBlock |
| `diff` | DiffViewer con sintaxis unified diff, +green / -red | Soporta git diff y unified |
| `mermaid` | `mermaid` lib render en contenedor fijo | Llamar `mermaid.run()` en useEffect con target |
| `graph` | Cyto ou similar (futuro). PR-5 placeholder con `GraphViewer` mostrando JSON graph en `code` viewer | |
| `finding-card` | Card con sev pill, title, evidence chips, remediation | Reusa `cve-assessment` con más campos |
| `screenshot` | Imagen con zoom (lightbox) | lazy loading |
| `terminal` | Igual que `TerminalOutput` de PR-4 | Reusa |
| `download` | Card con filename + size + download button que pega a `/api/chat/{conv}/artifacts/{id}/raw` | |
| `sandboxed-html` | Ver sección 6 | Crítico: seguridad |

---

## 6. `SandboxedHTML.tsx` — Especificación de Seguridad

**El renderer `sandboxed-html` debe ser INQUEBRANTABLE**. Sigue este contrato:

```tsx
import sanitizeHtml from "isomorphic-dompurify";  // o DOMPurify wrapper en app/src/lib/utils/sanitize.ts
import { useEffect, useRef } from "react";

export function SandboxedHTML({ html, title }: { html: string; title?: string }) {
  const iframeRef = useRef<HTMLIFrameElement>(null);

  // Sanitización doble: primero DOMPurify, luego iframe sandboxed双重
  const sanitized = sanitizeHtml(html, {
    ALLOWED_TAGS: ["div","span","p","a","b","i","em","strong","ul","ol","li","table","thead","tbody","tr","th","td","h1","h2","h3","h4","h5","h6","br","hr","img","svg","path","g","style"],
    ALLOWED_ATTR: ["class","style","href","src","alt","width","height","viewBox","fill","stroke","d","points","transform"],
    FORBID_ATTR: ["onload","onclick","onerror","onmouseover","onfocus","onblur","onmouseenter","onmouseleave"],
    FORBID_TAGS: ["script","iframe","object","embed","link","meta","form","input","button","textarea"],
    ALLOW_DATA_ATTR: false,
  });

  // iframe sandbox: NO allow-same-origin (crítico)
  return (
    <iframe
      ref={iframeRef}
      sandbox="allow-scripts"
      referrerPolicy="no-referrer"
      className="w-full min-w-0 h-[420px] bg-bg border border-border rounded"
      title={title ?? "Sandboxed preview"}
      srcDoc={sanitized}
    />
  );
}
```

**Prohibido agregar** al `sandbox`:
- `allow-same-origin` (dejaría al contenido acceder al storage del padre)
- `allow-forms`
- `allow-popups`

`srcDoc` (no `src`) mantiene el contenido dentro del atributo y respeta CSP.

---

## 7. `useQueryArtifact.ts`

```ts
export function useQueryArtifact(conversationId: string, artifactId: string) {
  return useQuery({
    queryKey: ["artifact", conversationId, artifactId],
    queryFn: () =>
      fetch(`/api/chat/${conversationId}/artifacts/${artifactId}`).then(r => r.json()),
    enabled: !!conversationId && !!artifactId,
    staleTime: 60_000,
  });
}
```

---

## 8. Verificación

```bash
cd app
npm run lint
npm run typecheck
npm run build
```

Crítico:
- **Sanity test manual**: insertar un `sandboxed-html` artifact con payload `<script>document.cookie</script>` y confirmar que NO se ejecuta. Insertar `<img src=x onerror=alert(1)>` y confirmar que la imagen no carga.

## 9. Commit / PR

- Branch: `feat/issue-18-5-artifacts-renderers`
- Commit: `feat(issue-18-5): workspace pane tabs + 16 generative renderers + sandboxed HTML hardening`
- PR contra `main`. Requiere PR-1 merged.
