# Reporte Maestro: Auditoría de Código y Matriz de Preparación para Issue #18

Este informe presenta la evaluación exhaustiva y final del repositorio `PrinceOfPwn/Munin` (`munin/`, `app/`, `soul/`, `docs/`) comparado contra el `README.md`, `AGENTS.md` y las especificaciones del **Issue #18** (`docs/issue-18-agent-prompt.md`).

---

## 1. Inventario de Código Muerto, Módulos Obsoletos y Rutas Rotas (Arch A Leftovers)

### A. Módulos de Backend Huérfanos y Desconectados (`munin/`)

| Archivo / Módulo | Estado / Impacto Técnico | Acción Recomendada |
| :--- | :--- | :--- |
| `munin/core/orchestrator.py` & `munin_wake` | **RUTA ROTA**: `_spawn_runner()` intenta ejecutar `[sys.executable, "-m", "munin.subagents.runner"]`. Como ese runner fue eliminado en la migración a v1.0.0, invocar la herramienta MCP `munin_wake` provoca un crash por `ModuleNotFoundError`. | Corregir `Orchestrator` para usar `supervisor_runner` / grafos. |
| `munin/mcp/main.py` & `valravn_tool.py` | **REGISTRO FRÁGIL**: `main.py` importa 11 módulos de herramientas pero **omite `valravn_tool`**. Solo se registra porque `tavily_tool.py` incluye una importación por efecto secundario al final. Refactorizar `tavily_tool` deshabilitaría 13 herramientas CTI silenciosamente. | Importar `valravn_tool` explícitamente en `main.py`. |
| `munin/core/coordination/swarm.py` & `handoff_tools.py` | **CÓDIGO HUÉRFANO**: Funciones `build_swarm()` y `make_handoff_tool()` nunca son importadas ni ejecutadas en el runtime del supervisor v1.0.0 (`supervisor.py`). | Eliminar o mover a módulo experimental. |
| `munin/subagents/ldap_agent.py` & `base.py:ReActSubagentBase` | **CÓDIGO MUERTO**: Clase ReAct de Arch A que nunca es instanciada ni importada en el runtime de producción. | Eliminar. |
| `munin/production/page_agent.py`, `skills_catalog.py`, `extensions.py`, `memory.py`, `agents.py` | **CÓDIGO MUERTO**: 5 módulos de producción sin importaciones activas ni uso tras la eliminación de la era del dispatcher. | Eliminar en limpieza previa. |

---

### B. Código Muerto en el Frontend (`app/src/`)

1. `app/src/lib/mcp.ts` (24 KB): Cliente JSON-RPC MCP en frontend. **Nunca se importa** en la app React (`AgentConsole.tsx`, `AppShell.tsx`); el frontend opera exclusivamente a través de los proxies BFF Next.js (`/api/chat`, `/api/production`).
2. `app/src/extensions/registry.tsx`: Manifiesto para widgets del antiguo Flight Deck (`ExtensionSlot`). Debe ser adaptado al *Generative UI Renderer Registry* con Zod.

---

### C. Advertencia de Compatibilidad: React 18.3 / Tailwind 3.4 vs AI Elements

> [!WARNING]
> **Compatibilidad de Stack UI**:
> - Las primitivas de **AI Elements** (`elements.ai-sdk.dev`) presuponen **React 19 + Tailwind 4**.
> - El repositorio `app/package.json` opera sobre **React 18.3.1** y **Tailwind 3.4.17**.
> - **Estrategia Recomendada para PR-1**: Vendorizar y adaptar las primitivas seleccionadas de AI Elements al stack actual (React 18 + Tailwind 3.4) sin forzar un upgrade global de React 19/Tailwind 4, evitando romper componentes de Radix UI o TanStack Query existentes.

---

## 2. Matriz de Preparación (Readiness Matrix) para la Implementación del Issue #18

A continuación se detalla la matriz de trabajo organizada en **6 Fases de Pull Requests (PRs)**:

| Área / Fase | Componentes Afectados | Estado Actual | Brecha vs Especificación Issue #18 | Requisitos / Acciones Previas para PR-1 |
| :--- | :--- | :--- | :--- | :--- |
| **PR-1: Fix Cancelación Real** | `munin/production/chat.py`, `app/src/components/AgentConsole.tsx`, BFF | `Stop` en UI llama a `stop()` del AI SDK (browser). El ejecutor Python sigue activo en background. | Requiere endpoint `POST /api/chat/{run_id}/cancel`, *fencing* de ejecución, eventos SSE `cancelling`/`cancelled` e idempotencia. | Crear handler de cancelación durable en Python y conectar botón Cancel en la UI. |
| **PR-1: Fix Guidance Lifecycle** | `munin/core/middleware/operator_guidance.py`, `chat.py` | El hint se encola y audita, pero no emite eventos de entrega ni seguimiento de estado. | Requiere ciclo de vida completo: `queued` $\rightarrow$ `delivered_to_runtime` $\rightarrow$ `applied_to_model_step` $\rightarrow$ `expired`/`superseded`/`run_finished_undelivered`. | Modificar middleware para reportar consumo/aplicación al model step y emitir eventos SSE. |
| **PR-1: Contrato UX & Schemas Zod** | `app/src/types/`, `app/src/extensions/registry.tsx` | Tipos sueltos en `mcp.ts` y `translator.ts`. Registry de widgets arcaico. | Falta versión en Zod (`munin-ui/*`), registry de renderers con allow-list y conciliación por ID estable. | Definir schemas Zod para partes de UI; adaptar `registry.tsx`. |
| **PR-2: Layout 3 Zonas & Shell** | `app/src/components/AppShell.tsx`, `AgentConsole.tsx` | Vista consola monocanal / debug. | Layout responsive de 3 zonas (sidebar de operaciones, chat/ejecución, workspace contextual con tabs). | Depende del éxito de PR-1. |
| **PR-3: Execution UX & HITL** | `HitlRequestPart.tsx`, `ToolInvocationPart.tsx` | HITL resuelto via API, UI preliminar. | Experiencia de ejecución completa con telemetría de herramientas, terminal, subagentes y HITL durable. | Depende de PR-1 y PR-2. |
| **PR-4: Generative UI & Sandbox** | `ArtifactPart.tsx`, Iframe Sandbox | Sin sandbox de ejecución de HTML/JS generativo. | Requiere `iframe` endurecido sin access origin/DOM para HTML generativo y tabs de artefactos. | Depende de PR-1 a PR-3. |
| **PR-5: Backend Read-Model** | `munin/production/store.py`, `chat.py` | Eventos planos SSE basados en `run_events`. | Enriquecimiento de eventos en read-model backend para consumo nativo de AI SDK. | Depende de PR-1 a PR-4. |
| **PR-6: Calidad & Virtualización** | `app/`, Vitest, E2E | Renderizado lineal del 100% de los mensajes (lento). | Virtualización de mensajes, nav por teclado, accesibilidad y tests E2E de alto volumen streaming. | Fase final de cierre. |

---

## 3. Hoja de Ruta de Trabajo Recomendada

1. **Limpieza Previa y Fixes de Rutas Rotas**:
   - Reparar `Orchestrator` para eliminar la llamada a `munin.subagents.runner`.
   - Agregar la importación explícita `from . import valravn_tool` en `munin/mcp/main.py`.
   - Eliminar los módulos huérfanos `page_agent.py`, `skills_catalog.py`, `extensions.py`, `memory.py`, `agents.py`, `app/src/lib/mcp.ts`, `swarm.py` y `handoff_tools.py`.
2. **Aplicar Fixes de Rendimiento de Latencia** (ver `docs/reports/latency_audit_report.md`):
   - Crear índices de SQLite en `store.py`.
   - Reemplazar polling loop de 300 ms en SSE por Pub/Sub async.
   - Corregir en `queries.ts` la destrucción de caché al buscar y agregar `key={conversationId}` a `<LiveConsole>`.
3. **Ejecución en 6 Fases (PR-1 a PR-6)** comenzando con la corrección de Cancelación Real, Operator Guidance Lifecycle y Schemas Zod.
