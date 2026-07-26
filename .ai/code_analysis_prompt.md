# Prompt de Análisis y Arquitectura para Munin

> Este prompt permite a cualquier modelo de IA analizar la arquitectura y código fuente de Munin utilizando la herramienta `munin_read_source` y proponer refactorizaciones o mejoras.

---

## 🔍 Instrucciones de Análisis de Código para la IA

### Contexto
Munin es un agente de ciberseguridad autónomo (ReAct) expuesto vía **MCP (Model Context Protocol)** con backend en Python (Starlette/Uvicorn/FastMCP) y Web UI en Next.js.

### Herramientas de Inspección Disponibles
- **`munin_read_source(action="list")`**: Enumera la lista completa de archivos del código fuente (`munin/` y `app/`).
- **`munin_read_source(action="read", rel_path="<path>")`**: Lee el contenido exacto de cualquier archivo del proyecto.
- **`munin_self_diagnose()`**: Ejecuta un diagnóstico del estado actual del sistema y devuelve los problemas registrados en `.ai/issues.md`.

### Objetivo de la Evaluación
1. **Revisión de Arquitectura:** Analizar la interacción entre `MuninAgent` (`munin/core/munin_agent.py`), el servidor MCP (`munin/mcp/main.py`), los subagentes (`munin/subagents/`) y el frontend (`app/src/`).
2. **Robustez del Protocolo MCP:** Verificar la compatibilidad con el cliente web (`mcp.ts`), manejo de sesiones (`mcp-session-id`), headers CORS y SSE parser.
3. **Generación de Tool Forging:** Evaluar el módulo de creación dinámica de tools (`munin/mcp/tools/forge_tool.py`) para garantizar la persistencia e importación segura de scripts.
