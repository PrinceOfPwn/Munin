# Munin System Known Issues & Task Backlog

Este documento registra los errores, fallos de esquema, advertencias y mejoras identificados durante las pruebas de integración del sistema Munin y sus componentes (MCP, Subagentes, UI y Mock Data).

---

## 1. Agente ReAct & Iteraciones (`munin_chat`)

- **Límite de Iteraciones Alcanzado (`max_iterations`):**
  - **Síntoma:** El agente devuelve `(max iterations reached)` tras 6 a 15 iteraciones en tareas complejas sin finalizar el pipeline completo.
  - **Solución / Acción:**
    - Elevar o eliminar el tope rígido de iteraciones (configurar a un valor alto como 400 o configurable por request).
    - Eliminar `break` tempranos en la lógica de bucle ReAct para evitar interrumpir prematuramente la ejecución del sistema.
    - Asegurar que la respuesta y los resultados de las herramientas nunca se trunquen en la salida entregada al cliente.

---

## 2. Mock Data de LDAP & OPSEC / Prompting

- **Pistas Explícitas en Atributos `description`:**
  - **Síntoma:** El LDIF de prueba contiene textos explícitos como `"AS-REP Roastable — DONT_REQ_PREAUTH simulado"` o `"Kerberoastable"`.
  - **Impacto:** Entrega pistas artificiales al LLM que no existirían en un entorno Active Directory / LDAP real.
  - **Solución / Acción:**
    - Limpiar los atributos `description` en `scripts/ldap_mock.ldif` para remover etiquetas artificiales.
    - El LLM debe inferir la vulnerabilidad analizando atributos reales (ej: `userAccountControl`, SPNs configurados) en lugar de leer descripciones en texto plano.

---

## 3. Incompatibilidad de Esquema LDAP (OpenLDAP vs Active Directory)

Muchas de las herramientas de subagente intentan consultar esquemas de Active Directory contra el servidor OpenLDAP de prueba:

- **`sAMAccountName` no existente:**
  - **Herramientas afectadas:** `get_current_user_info`, `get_user_groups`, `find_kerberoastable_users`, `find_asrep_roastable_users`.
  - **Error:** `ldap_search_failed: invalid attribute type sAMAccountName`.
  - **Causa:** OpenLDAP utiliza `uid` o `cn` en lugar de `sAMAccountName`.
- **`objectClass: group` no válido:**
  - **Herramienta afectada:** `find_domain_admins`.
  - **Error:** `ldap_search_failed: invalid class in objectClass attribute: group`.
  - **Causa:** OpenLDAP utiliza `groupOfNames` o `posixGroup` en lugar del `objectClass=group` de AD.

---

## 4. Errores Internos en Herramientas MCP

- **`memory_list`, `episodic_query` y `query_shared_intel` Type Error (`int` vs `str`):**
  - **Error:** `'<' not supported between instances of 'int' and 'str'`.
  - **Síntoma:** Ocurre al llamar `memory_list`, o al pasar filtros no por defecto en `episodic_query` (ej: `action="FAILED"`, `limit="20"`) y `query_shared_intel` (ej: `severity="HIGH"`, `limit="20"`).
  - **Causa:** Comparación directa (`<` / `sort`) entre enteros y strings al filtrar o listar registros.

- **Herramientas MCP Faltantes o No Registradas:**
  - **`shared_state_overview`:** Referenciada en `skills.md` pero no está registrada en el servidor MCP ni exportada como herramienta.

- **Herramientas de Reconocimiento Externo sin Configuración:**
  - **`tavily_search`:** Falla con `TAVILY_API_KEY empty` / `TAVILY_API_KEY not configured`.

- **Integración de Hugin (Passive Intel Provider):**
  - **`hugin_search`:** Timeout en la conexión HTTPS (`Read timed out (read timeout=20)`).
  - **`hugin_refresh`:** `HTTP 404 Not Found` en la URL `https://princeofpwn.github.io/Hugin/data/entities.json`.
  - **Acción:** Corregir el endpoint del repositorio Hugin o actualizar el dataset distribuido.

---

## 5. Validación de Argumentos en la Web UI / Forms

- **`fetch_agent_messages` Validation Error:**
  - **Error:** `1 validation error for fetch_agent_messagesArguments: recipient_agent Field required`.
  - **Causa:** Formulario en el Tool Explorer permitiendo la ejecución sin completar el campo obligatorio `recipient_agent`.

- **`soul_read` Validation Error:**
  - **Error:** `1 validation error for soul_readArguments: path Field required`.
  - **Causa:** El formulario envió un objeto mal formateado o JSON escapado (`{"name": "path": "identity.md"}`) en lugar del valor primitivo string para `path`.

---

## 6. Advertencias en Consola del Navegador (Frontend)

- **`MaxListenersExceededWarning`:**
  - **Advertencia:** `Possible EventEmitter memory leak detected. 11 close listeners added. Use emitter.setMaxListeners() to increase limit`.
- **Stream Inactivo (`ObjectMultiplex`):**
  - **Advertencia:** `ObjectMultiplex - orphaned data for stream "app-init-liveness"` y `background-liveness`.

---

## 7. Paquetes y Binarios de Reconocimiento / Pentesting Faltantes

- **Herramientas de Escaneo Inoperativas (`nmap`, `feroxbuster`, `nuclei`, `ffuf`, `sqlmap`, etc.):**
  - **Síntoma:** Herramientas como `nmap_scan`, `feroxbuster_scan` o `cve_enrich` fallan o expiran al no encontrar los paquetes/binarios del sistema operativo instalados en el runner o contenedor.
  - **Acción / Solución:**
    - Asegurar la instalación de binarios requeridos (`nmap`, `feroxbuster`, `nuclei`, `ffuf`, `sqlmap`, `httpx`, `katana`) en la receta de instalación del entorno / CI (`live-session.yml` / Dockerfile).
    - Implementar validaciones en preflight para notificar de forma limpia cuando una herramienta de sistema no está instalada en el `PATH`.
