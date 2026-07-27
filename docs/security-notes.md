# Notas de seguridad y límites operativos

Munin se diseñó para evaluación, investigación y automatización sobre activos
autorizados. Su funcionalidad de recon, LDAP, herramientas generadas y runners
Kali aumenta el impacto de una configuración equivocada: el operador debe fijar
alcance, credenciales y política antes de iniciar una sesión.

## Controles presentes

| Superficie | Control actual |
| --- | --- |
| HTTP MCP | El servidor exige `MUNIN_MCP_AUTH_TOKEN` para HTTP. Si el token está vacío, rechaza iniciar salvo `MUNIN_MCP_ALLOW_ANON=1`, solo para desarrollo. |
| GUI online | La GUI usa el proxy same-origin del runner y almacena solo el token MCP en `localStorage`. Turso y LLM permanecen en servidor. |
| LDAP | Las entradas de filtros pasan por `escape_filter_chars`; `ldap_search` usa plantillas y parámetros, no filtros crudos controlados por usuario. |
| URLs LLM | Se exigen HTTPS o loopback y se rechazan endpoints de metadata/rangos no seguros conocidos. |
| Soul | Las rutas se resuelven dentro de `soul/`; una edición es propuesta revisable, no aplicación automática. |
| Forge | AST guard, allowlist de imports, builtins reducidos, timeout y cwd temporal antes de registrar una tool. |
| Subagentes | `graph_forge` usa whitelist explícita; `munin_wake` rechaza agentes que no sean nativos ni grafos existentes. |
| Auditoría | Los registros intentan redactar tokens Bearer, claves y patrones de secretos conocidos. |

Las tools activas heredadas respetan `PREFLIGHT_POLICY` (`always`, `active_only`
u `off`). El valor `off` se reserva al LDAP mock del workflow o a un laboratorio
controlado: no equivale a una autorización implícita.

## Qué no garantiza Munin

### El sandbox de forge no es una frontera fuerte

El Python generado se valida y se ejecuta en proceso. Las defensas reducen el
riesgo accidental, pero un modelo/adversario suficientemente capaz puede hallar
formas de eludir una sandbox basada en AST/builtins. No aceptes specs no
confiables ni des permisos host sensibles a un runner que forja código. Para un
aislamiento fuerte, ejecuta el candidato en un contenedor/VM sin red, de solo
lectura, sin capabilities y con límites de CPU/memoria.

### La whitelist limita, no reemplaza la revisión

Un grafo solo recibe las tools enlistadas, pero la utilidad y el riesgo de cada
tool dependen del alcance/argumentos. Revisa la whitelist y el propósito antes
de despertar un subagente, y observa `subagent_trace` durante ejecuciones largas.
El HITL de la GUI es observabilidad y decisión operativa posterior; no es una
puerta de aprobación bloqueante por tool call.

### La persistencia no recupera lo que nunca se guardó

Turso confirma estado y el código fuente de tools nuevas. Una tool heredada que
solo dejó un `script_path` en un runner que ya murió necesita regenerarse. Los
artifacts de Actions son útiles como respaldo, pero están sujetos a cuota y no
son la fuente de verdad de Turso.

## Recomendaciones de operación

1. Mantén MCP en `127.0.0.1` localmente. Si abres un túnel, usa token robusto,
   duración mínima y no lo publiques.
2. Guarda LLM, Turso y token MCP como GitHub Secrets o variables de entorno;
   no los copies a issues, logs, PRs, screenshots o prompts de agentes.
3. Da a Turso un token limitado a la base de Munin y rota secretos cuando una
   sesión/túnel haya sido expuesto por error.
4. Ejecuta `munin_diagnostics(mode="deep")` antes de una demo. Investiga
   `hard_failures`; un advisory opcional no justifica deshabilitar controles.
5. Revisa PRs de Soul y commits de artefactos forjados como código de terceros.
6. Usa el LDAP mock incluido para validar flujos. Cualquier target externo exige
   autorización escrita y una política de preflight apropiada.

## Reportar o depurar un incidente

No adjuntes `.env`, tokens, dumps completos de Turso ni datos sensibles. Incluye
el ID de run, la tool/acción, un `trace_id` si existe, el modo de diagnostics y
logs redactados. Antes de borrar estado, conserva evidencia mínima y desactiva
la tool/grafo específico que causó el problema.
