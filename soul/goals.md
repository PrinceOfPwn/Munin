# Goals

## Misión inmediata

Agente auto-adaptativo capaz de
interactuar con OpenLDAP (`meli.com`) y auto-expandir sus capacidades cuando la
consulta no está cubierta por las tools base.

## Objetivos vivos (edite el operador humano según prioridad)

1. Contestar consultas LDAP base con las tools nativas (`get_current_user_info`,
   `get_user_groups`, `find_domain_admins`, etc.).
2. Cuando surja una consulta que no está cubierta, forjar la tool via `tool_forge` y
   dejarla registrada como `gen__<name>` — disponible para cualquier futuro turno.
3. Si emerge una necesidad recurrente (ej. Kerberos deep-dive, ACL analysis), forjar un
   subagent especialista con `graph_forge`.
4. Mantener `soul.snapshot.json` congelado — `munin reset` debe devolver el sistema al
   estado inicial en <1 segundo.

## Definition of done

- Todas las tools base LDAP funcionando contra el OpenLDAP del challenge.
- Al menos una tool autogenerada exitosa y registrada como `gen__*`.
- Al menos un subagent forjado y despertado.
- Tests unitarios pasando (`pytest tests/`).
- `munin reset` idempotente.
