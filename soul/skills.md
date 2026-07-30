# Skills

## Nativas (siempre disponibles)

### LDAP (Active-Directory-like)
- `ldap_who_am_i`, `get_current_user_info`, `get_user_groups`
- `ldap_search(filter_template, params_json)` — el patrón seguro parametrizado
- `find_kerberoastable_users`, `find_asrep_roastable_users`, `find_domain_admins`,
  `dump_domain_structure`

### Passive intel
- `cve_lookup`, `cve_search`, `cve_enrich`, `exploit_search`, `package_vuln_lookup`
- `tavily_search` — web search de propósito general
- `hugin_search`, `hugin_refresh` — knowledge base propia (proyecto hermano)

### Active offense
- `nmap_scan`, `nmap_advanced_scan`, `httpx_probe`, `nuclei_scan`
- `sqlmap_scan`, `hydra_attack`, `netexec_scan`, `smbmap_scan`
- `feroxbuster_scan`, `ffuf_scan`, `katana_crawl`, `web_evidence_screenshotter`

### Multi-agent bus
- `shared_state_overview`, `publish_shared_intel`, `query_shared_intel`
- `claim_shared_task`, `heartbeat_shared_task`, `complete_shared_task`, `list_shared_tasks`
- `upsert_agent_presence`, `list_agent_presence`
- `post_agent_message`, `fetch_agent_messages`, `ack_agent_message`

### Munin-specific
- `soul_read`, `soul_list`, `soul_propose_edit`
- `memory_remember`, `memory_recall`, `memory_list`, `episodic_query`
- `munin_wake`, `munin_wake_list`
- `tool_forge`, `graph_forge`
- `list_generated_tools`, `describe_generated_tool`, `run_generated_tool`,
  `deactivate_generated_tool`
- `list_generated_graphs`, `describe_generated_graph`, `drop_generated_graph`

## Autogeneradas

El catálogo de tools `gen__*` se refresca en cada iteración. Vos siempre ves el
inventario actual — está en el bloque "Generated tools" del system prompt que se
compone dinámicamente.
