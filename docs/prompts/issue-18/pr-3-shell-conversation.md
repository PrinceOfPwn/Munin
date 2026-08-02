# Prompt PR-3 — Shell de 3 zonas, Conversación y Composer con AI Elements adaptado

> Issue: #18 · Fase 3 · Ola 2 · **Requiere PR-1 en `main`**
> Ejecutar en paralelo con `pr-4` y `pr-5`.
> Contexto compartido obligatorio: `docs/prompts/issue-18/00-master.md` — léelo primero.

---

## 1. Instrucciones para la IA ejecutora

Eres un desarrollador Frontend especialista en React/Tailwind. Debes reconstruir el shell principal de Munin (`AppShell.tsx`) implementando el layout de 3 zonas responsive y adaptando los componentes de conversación de **AI Elements** al stack actual del proyecto (**React 18.3 + Tailwind 3.4**).

---

## 2. Rutas que SOLO este PR modifica (Rutas Permitidas)

PUEDES crear o editar ÚNICAMENTE estas rutas:
- `app/src/components/AppShell.tsx` (Reconstrucción del shell)
- `app/src/components/shell/OperationsSidebar.tsx` (NUEVO)
- `app/src/components/shell/WorkspaceDrawer.tsx` (NUEVO — Slot colapsable)
- `app/src/components/ai-elements/conversation.tsx` (NUEVO — Adaptado de AI Elements)
- `app/src/components/ai-elements/message.tsx` (NUEVO — Adaptado de AI Elements)
- `app/src/components/ai-elements/prompt-input.tsx` (NUEVO — Adaptado de AI Elements)
- `app/src/components/ai-elements/reasoning.tsx` (NUEVO — Adaptado de AI Elements)
- `app/src/components/ai-elements/suggestion.tsx` (NUEVO — Adaptado de AI Elements)
- `app/src/lib/aiChat.ts` (Ajustes de integración con el nuevo shell)
- `docs/issue-18-shell-ai-elements.md` (NUEVO)
- `changes.md` (AÑADIR entrada)

### Rutas Prohibidas
- `app/src/components/chat/*Part.tsx` (PR-4 se encarga de los bloques de ejecución)
- `app/src/components/workspace/**` (PR-5 se encarga del contenido del workspace)
- `app/src/renderers/**` (PR-1 / PR-5)
- `munin/**` y `tests/**`

---

## 3. Especificación detallada paso a paso

### Paso 3.1: Adaptación de AI Elements a React 18 + Tailwind 3.4

Crea la carpeta `app/src/components/ai-elements/`.
Adapta los componentes desde el repositorio de AI Elements (`@vercel/ai-elements`):

1. **`conversation.tsx`**:
   - Componentes: `Conversation`, `ConversationContent`, `ConversationScrollButton`, `ConversationEmptyState`.
   - Lógica de auto-scroll pegado al fondo durante streaming usando `use-stick-to-bottom` o implementación CSS/Ref equivalente compatible con React 18.
   - Botón `ConversationScrollButton` visible solo cuando el usuario hace scroll hacia arriba.

2. **`message.tsx`**:
   - Componentes: `Message`, `MessageContent`, `MessageResponse`.
   - Prop `from="user" | "assistant"`.
   - Jerarquía visual: Respuestas finales del asistente destacadas con tipografía clara y buen contraste sobre `bg-surface`.

3. **`prompt-input.tsx`**:
   - Componentes: `PromptInput`, `PromptInputTextarea`, `PromptInputSubmit`.
   - Auto-resize del Textarea conforme el usuario escribe.
   - Botón submit con estado `status="streaming" | "ready"`.
   - Integración visual de controles: "Stop Viewing (Detach)" y "Cancel Run (Backend)".

4. **`reasoning.tsx`**:
   - Componentes: `Reasoning`, `ReasoningTrigger`, `ReasoningContent`.
   - Reasoning colapsable. Muestra indicador de pulso animado (`animate-pulse` o `feather`) cuando `isStreaming={true}`.

### Paso 3.2: Reconstrucción de `AppShell.tsx` (Layout de 3 Zonas)

Implementa la estructura de 3 zonas resizables/colapsables:

```tsx
<div className="flex h-screen w-screen overflow-hidden bg-bg text-body">
  {/* Zona 1: Operations Sidebar (Izquierda - 260px o colapsado 60px) */}
  <OperationsSidebar className="w-64 border-r border-border shrink-0" />

  {/* Zona 2: Conversation & Execution (Centro - flex-1 min-w-0) */}
  <main className="flex-1 flex flex-col min-w-0 h-full relative bg-bg">
    <Conversation className="flex-1 min-h-0 overflow-y-auto">
      <ConversationContent>
        {/* Renderizado de mensajes */}
      </ConversationContent>
      <ConversationScrollButton />
    </Conversation>

    {/* Composer Sticky en el fondo */}
    <div className="p-4 border-t border-border bg-surface shrink-0">
      <PromptInput />
    </div>
  </main>

  {/* Zona 3: Contextual Workspace (Derecha - Slot colapsable 380px) */}
  <WorkspaceDrawer className="w-96 border-l border-border shrink-0" />
</div>
```

**Regla de Responsive**:
- En pantallas `< 1024px`, la Zona 1 (Sidebar) y Zona 3 (Workspace) se convierten en paneles deslizables (Drawers) sobrepuestos, dejando la Zona 2 (Conversación) ocupando el 100% del ancho.

---

## 4. Verificación Obligatoria

```bash
cd app
npm run lint
npm run typecheck
npm run build
npm test
```

Asegúrate de que NO haya ningún overflow horizontal en los 5 viewports (1366×768, 1440×900, 1920×1080, 768, 360).

---

## 5. Instrucciones de Commit y PR

- Rama: `feat/issue-18-3-shell-conversation`
- Commit: `feat(issue-18-3): adapt AI Elements conversation primitives and rebuild 3-zone shell`
- Abre el PR contra `main`.
