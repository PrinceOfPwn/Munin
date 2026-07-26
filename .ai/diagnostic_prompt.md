# Master AI Refactoring & Fixes Prompt for Munin

> Este prompt está diseñado para entregarse directamente a otra IA (ej. Claude 3.5 Sonnet, GPT-4o o Gemini 1.5 Pro) para que refactorice el código de Munin y solucione todos los problemas de herramientas, esquemas y tipos identificados.

---

## 🎯 Instrucciones de Refactorización para la IA

Eres un Ingeniero Principal de Software especializado en Python, FastAPI/Starlette, MCP (Model Context Protocol) y Next.js.
Tu objetivo es aplicar correcciones definitivas al repositorio `Munin` basadas en la lista de diagnósticos en `.ai/issues.md`.

---

### 1. Incompatibilidad de Esquema LDAP (OpenLDAP vs Active Directory)
- **Problema:** Las herramientas `get_current_user_info`, `get_user_groups`, `find_kerberoastable_users`, `find_asrep_roastable_users`, `find_domain_admins` y `dump_domain_structure` en `munin/mcp/tools/ldap_tools.py` asumen atributos de Active Directory (`sAMAccountName`, `objectClass=group`, `objectClass=container`).
- **Solución:**
  1. Modificar las consultas de `ldap_tools.py` para usar filtros flexibles de búsqueda que soporten tanto OpenLDAP como AD:
     - Reemplazar `(sAMAccountName={username})` por `(|(sAMAccountName={username})(uid={username})(cn={username}))`.
     - Reemplazar `(objectClass=group)` por `(|(objectClass=group)(objectClass=groupOfNames)(objectClass=posixGroup))`.
     - Reemplazar `(objectClass=container)` por `(|(objectClass=container)(objectClass=organizationalUnit)(objectClass=domain))`.

---

### 2. OPSEC / Limpieza de Mock LDIF
- **Problema:** El archivo `scripts/ldap_mock.ldif` contiene cadenas como `"AS-REP Roastable — DONT_REQ_PREAUTH simulado"` y `"Kerberoastable"`.
- **Solución:**
  - Limpiar el LDIF para remover cualquier etiqueta explícita de vulnerabilidad en los campos `description`. El agente debe deducir las vulnerabilidades analizando los atributos reales (como SPNs o banderas de cuentas) y no leyendo pistas explícitas.

---

### 3. Bugs de Comparación de Tipos (`int` vs `str`)
- **Problema:** En `munin/mcp/tools/munin_tools.py` y `munin/mcp/tools/`, funciones como `memory_list`, `episodic_query` y `query_shared_intel` arrojan `TypeError: '<' not supported between instances of 'int' and 'str'` cuando se reciben parámetros numéricos o de filtrado como strings (ej: `limit="20"`, `severity="HIGH"`).
- **Solución:**
  - Castear de forma segura todos los argumentos de entrada numéricos (`int(limit) if limit is not None else 50`).
  - Asegurar que la función de ordenamiento/filtrado compare tipos homogéneos (`int` con `int` o `str` con `str`).

---

### 4. Herramientas MCP y Subagentes Inexistentes o sin Configuración
- **`shared_state_overview`:** Registrar la función expuesta en `main.py` dentro de `_NATIVE_TOOLS` en `munin/core/munin_agent.py`.
- **`tavily_search`:** Agregar un mensaje amigable y capturado en lugar de romper cuando `TAVILY_API_KEY` no esté configurado.
- **Binarios Faltantes:** Implementar verificación `shutil.which("<tool>")` antes de ejecutar `nmap`, `feroxbuster`, `nuclei`, etc., devolviendo un JSON con `ok: false, error: "binary_missing"` si la herramienta no está en el sistema.

---

### 5. Formularios y UI en Frontend (Next.js)
- **Problema:** En `app/src/components/`, el ejecutor de herramientas del Tool Explorer envía llamadas sin completar parámetros obligatorios (`recipient_agent` en `fetch_agent_messages`, `path` en `soul_read`).
- **Solución:** Validar campos requeridos antes de emitir la petición POST a `/mcp`.
