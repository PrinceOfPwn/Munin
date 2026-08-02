# Prompt PR-4 — Execution UX: Tools, Terminal, Reasoning, Subagentes y HITL

> Issue: #18 · Fase 4 · Ola 2 · **Requiere PR-1 en `main`**
> Ejecutar en paralelo con `pr-3` y `pr-5`.
> Contexto compartido obligatorio: `docs/prompts/issue-18/00-master.md` — léelo primero.

---

## 1. Instrucciones para la IA ejecutora

Eres un desarrollador Frontend especializado en Interfaces de Herramientas de Seguridad y Consolas de Comando. Debes reconstruir los **bloques de ejecución del chat** para que las llamadas a herramientas, salidas de comandos (terminal), razonamiento de modelos, subagentes y aprobaciones HITL se consoliden por ID estable en tarjetas limpias y contenidas.

---

## 2. Rutas que SOLO este PR modifica (Rutas Permitidas)

PUEDES crear o editar ÚNICAMENTE estas rutas:
- `app/src/components/chat/ToolInvocationPart.tsx`
- `app/src/components/chat/CommandOutputPart.tsx`
- `app/src/components/chat/ReasoningPart.tsx`
- `app/src/components/chat/HitlRequestPart.tsx`
- `app/src/components/chat/SubagentPresencePart.tsx`
- `app/src/components/chat/OperationalTracePart.tsx`
- `app/src/components/chat/ToolHeartbeatPart.tsx`
- `app/src/components/chat/GuidancePart.tsx`
- `app/src/components/chat/NotePart.tsx`
- `app/src/components/chat/HeartbeatPart.tsx`
- `app/src/components/chat/ArtifactPart.tsx`
- `app/src/components/ai-elements/tool.tsx` (NUEVO — Adaptado de AI Elements)
- `app/src/components/ai-elements/terminal.tsx` (NUEVO — Adaptado de AI Elements)
- `app/src/lib/format.ts` (Helpers de formato)
- `docs/issue-18-execution-ux.md` (NUEVO)
- `changes.md` (AÑADIR entrada)

### Rutas Prohibidas
- `app/src/components/AppShell.tsx` (PR-3)
- `app/src/components/workspace/**` (PR-5)
- `munin/**` y `tests/**`

---

## 3. Especificación detallada paso a paso

### Paso 3.1: Consolidación de Herramientas por ID Estable

El problema actual es que cada evento SSE (`tool_started`, `tool_heartbeat`, `tool_result`) crea una tarjeta pequeña en el timeline.
Debes implementar la **consolidación por `tool_call_id`**:

1. `ToolInvocationPart.tsx`:
   - Agrupa todos los eventos del mismo `tool_call_id` en **una sola tarjeta**.
   - Encabezado: Nombre de la tool + badge de estado (`running` con spinner / `success` verde / `error` rojo) + `elapsed_ms`.
   - Contenido colapsable: Argumentos de entrada (JSON formateado) y Resultado de salida.
   - Si se reciben eventos `tool_heartbeat`, actualiza el tiempo en vivo en la misma tarjeta sin duplicarla.

### Paso 3.2: Terminal Contenida para Salida de Comandos (`CommandOutputPart.tsx`)

Las salidas de comandos de escaneo (nmap, nuclei, feroxbuster) suelen romper la maquetación.
En `CommandOutputPart.tsx`:

1. Adapta `Terminal` de AI Elements en `app/src/components/ai-elements/terminal.tsx`.
2. Requisitos de contención:
   - `min-w-0 w-full max-w-full overflow-x-auto` en el contenedor.
   - Botón de **Toggle Line Wrap** (alternar entre scroll horizontal y ajuste de línea).
   - Botón **Copy Output** (copiar al portapapeles).
   - Botón **Fullscreen Modal** (pantalla completa para inspección de logs largos).
   - Botón **Download Transcript** (descargar archivo `.log`).
   - Soporte para códigos de color ANSI mediante `ansi-to-react`.

### Paso 3.3: Razonamiento del Proveedor (`ReasoningPart.tsx`)

1. Muestra exclusivamente el `provider_reasoning` o `reasoning` que el modelo emite de forma transparente.
2. Renderízalo en un bloque colapsable estilizado con `border-border` y texto en tono `text-muted`.
3. NUNCA inventes cadenas de pensamiento ocultas.

### Paso 3.4: Subagentes Resumidos (`SubagentPresencePart.tsx`)

1. Muestra la actividad de subagentes en filas compactas tipo badge: `[Subagente: Recon] -> Running (12s)` o `[Subagente: LDAP] -> Completed (1.4s)`.
2. Evita inundar el timeline principal con la conversación interna del subagente.

---

## 4. Verificación Obligatoria

```bash
cd app
npm run lint
npm run typecheck
npm run build
npm test
```

Prueba la tarjeta de terminal con una salida de comando de más de 500 líneas y verifica que no cause overflow horizontal de la página.

---

## 5. Instrucciones de Commit y PR

- Rama: `feat/issue-18-4-execution-ux`
- Commit: `feat(issue-18-4): consolidate execution parts by stable ID and implement contained terminal`
- Abre el PR contra `main`.
