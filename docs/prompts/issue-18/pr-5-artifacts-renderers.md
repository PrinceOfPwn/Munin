# Prompt PR-5 — Workspace de Artefactos y Renderers Generativos Seguros

> Issue: #18 · Fase 5 · Ola 2 · **Requiere PR-1 en `main`**
> Ejecutar en paralelo con `pr-3` y `pr-4`.
> Contexto compartido obligatorio: `docs/prompts/issue-18/00-master.md` — léelo primero.

---

## 1. Instrucciones para la IA ejecutora

Eres un desarrollador Frontend Senior especializado en Seguridad de Aplicaciones Web y UI de Artefactos. Debes construir el **Workspace Contextual de Artefactos** (Zona 3 del Shell) e implementar el catálogo de **Renderers Generativos Confiables**, incluyendo el sandbox aislado mediante `<iframe>` endurecido para vistas previas HTML.

---

## 2. Rutas que SOLO este PR modifica (Rutas Permitidas)

PUEDES crear o editar ÚNICAMENTE estas rutas:
- `app/src/components/workspace/WorkspaceTabs.tsx` (NUEVO)
- `app/src/components/workspace/ArtifactsTab.tsx` (NUEVO)
- `app/src/components/workspace/EvidenceTab.tsx` (NUEVO)
- `app/src/components/workspace/RunDetailsTab.tsx` (NUEVO)
- `app/src/components/workspace/AgentsTab.tsx` (NUEVO)
- `app/src/components/renderers/IocTableRenderer.tsx` (NUEVO)
- `app/src/components/renderers/CveAssessmentRenderer.tsx` (NUEVO)
- `app/src/components/renderers/MermaidRenderer.tsx` (NUEVO)
- `app/src/components/renderers/SandboxedHtmlRenderer.tsx` (NUEVO)
- `app/src/components/ai-elements/artifact.tsx` (NUEVO)
- `app/src/components/ai-elements/code-block.tsx` (NUEVO)
- `app/src/components/ai-elements/web-preview.tsx` (NUEVO)
- `app/src/components/ArtifactActions.tsx`
- `docs/issue-18-artifacts-security.md` (NUEVO)
- `changes.md` (AÑADIR entrada)

### Rutas Prohibidas
- `app/src/components/AppShell.tsx` (PR-3)
- `app/src/components/chat/*Part.tsx` (PR-4)
- `munin/**` y `tests/**`

---

## 3. Especificación detallada paso a paso

### Paso 3.1: Workspace Contextual (Tabs)

Crea la carpeta `app/src/components/workspace/` con las siguientes pestañas:

1. **`ArtifactsTab.tsx`**:
   - Lista de artefactos generados en la conversación activa.
   - Acciones por artefacto: Ver, Pin (fijar arriba), Pantalla completa, Copiar, Descargar, Promover a Evidencia.

2. **`EvidenceTab.tsx`**:
   - Muestra hallazgos estructurados promovidos a evidencia (IOCs, capturas, logs firmados).

3. **`RunDetailsTab.tsx`**:
   - Lectura de telemetría del run actual: ID, estado, timers, consumo de tokens, conteo de herramientas ejecutadas.

4. **`AgentsTab.tsx`**:
   - Estado de subagentes activos y cola de tareas compartidas.

### Paso 3.2: Renderers Generativos y Seguridad (`SandboxedHtmlRenderer.tsx`)

Implementa el renderer de HTML inseguro mediante un iframe aislado:

```tsx
// app/src/components/renderers/SandboxedHtmlRenderer.tsx
export function SandboxedHtmlRenderer({ htmlContent }: { htmlContent: string }) {
  return (
    <div className="w-full h-[500px] border border-border rounded overflow-hidden bg-white">
      <iframe
        title="Sandboxed Artifact Preview"
        srcDoc={htmlContent}
        sandbox="allow-scripts" // SIN allow-same-origin, SIN allow-forms, SIN allow-top-navigation
        className="w-full h-full border-none"
      />
    </div>
  );
}
```

**Reglas de Seguridad Obligatorias**:
- El iframe NUNCA debe incluir `allow-same-origin`. Esto garantiza que el contenido generado no pueda leer ni `localStorage`, ni `document.cookie`, ni interactuar con el DOM padre ni realizar peticiones autenticadas.
- NUNCA renderices JSX o React dinámico inyectado desde strings mediante `eval` o `exec`.

### Paso 3.3: Registrar Renderers en `app/src/renderers/registry.ts`

Registra cada componente creado en el registry central (creado en PR-1):
- `ioc-table` -> `IocTableRenderer`
- `cve-assessment` -> `CveAssessmentRenderer`
- `mermaid` -> `MermaidRenderer`
- `sandboxed-html` -> `SandboxedHtmlRenderer`

---

## 4. Verificación Obligatoria

```bash
cd app
npm run lint
npm run typecheck
npm run build
npm test
```

Prueba la renderización de un artefacto HTML con scripts e intenta acceder a `window.parent` en la prueba para verificar que el sandbox bloquee el acceso.

---

## 5. Instrucciones de Commit y PR

- Rama: `feat/issue-18-5-artifacts-workspace`
- Commit: `feat(issue-18-5): implement artifact workspace tabs and sandboxed HTML renderer`
- Abre el PR contra `main`.
