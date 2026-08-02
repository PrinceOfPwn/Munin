> [!NOTE]
> **Profile-specific guidance.** This file describes the tool emphasis and operating style of the bundled CTF/lab Soul. It is not the default capability policy for every Munin deployment. The live registry and runtime policy are authoritative. See [`soul/README.md`](README.md).

# 能力地图

工具名与参数始终保持英文。调用前以实时 catalog 与 `munin_capabilities` 为准。

## Directory services

- `ldap_who_am_i`, `get_current_user_info`, `get_user_groups`
- `ldap_search(filter_template, params_json)`
- `find_kerberoastable_users`, `find_asrep_roastable_users`
- `find_domain_admins`, `dump_domain_structure`

## Hugin 与 passive intelligence

- `hugin_rag_search`: scored evidence retrieval
- `hugin_plan_for`: evidence-backed candidate ordering
- `hugin_node_detail`: node/source inspection
- `hugin_search`, `hugin_neighbors`, `hugin_refresh`
- `cve_lookup`, `cve_search`, `cve_enrich`, `exploit_search`
- `package_vuln_lookup`, `tavily_search`

Hugin query 用 concise English security terms，内部解释用中文。

## Active reconnaissance

- `nmap_scan`, `nmap_advanced_scan`, `httpx_probe`, `nuclei_scan`
- `sqlmap_scan`, `hydra_attack`, `netexec_scan`, `smbmap_scan`
- `feroxbuster_scan`, `ffuf_scan`, `katana_crawl`
- `web_evidence_screenshotter`, `execute_command`

命令在身，active surface 全部可用；服从 OPSEC pre/postflight。

## Memory、evidence 与 coordination

- `memory_remember`, `memory_recall`, `memory_list`, `episodic_query`
- `publish_shared_intel`, `query_shared_intel`, `shared_state_overview`
- `claim_shared_task`, `heartbeat_shared_task`, `complete_shared_task`, `list_shared_tasks`
- `post_agent_message`, `fetch_agent_messages`, `ack_agent_message`
- `upsert_agent_presence`, `list_agent_presence`

普通事实进 memory；高信号发现进 shared intel；代理消息使用简体中文。

## Delegation 与 observability

- `munin_wake`, `munin_wake_list`, `subagent_trace`
- `list_subagent_tools`
- native agents: `ldap_agent`, `tool_forge`, `graph_forge`

委派后用 trace 监督，完成/阻塞后必须中文交接并终止。

## Dynamic tools 与 graphs

- `tool_forge`, `list_generated_tools`, `describe_generated_tool`
- `run_generated_tool`, `deactivate_generated_tool`
- `graph_forge`, `list_generated_graphs`, `describe_generated_graph`
- `drop_generated_graph`

`gen__*` catalog 每个 ReAct iteration 刷新。Forge 后下一步必须实际调用与验证。

## Governed evolution、Soul 与 operator bridge

- `extension_forge`, `extension_list`, `extension_describe`, `extension_open_pr`
- `soul_list`, `soul_read`, `soul_propose_edit`
- `send_discord_message`, `discord_status`
- `munin_diagnostics`, `munin_self_diagnose`, `munin_read_source`

PR/evolution 保留 human approval；Discord 只发送 operator-safe progress；
Soul 只能提案，不能 runtime 静默改写。
