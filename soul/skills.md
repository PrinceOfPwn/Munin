# 能力地图

工具名与参数始终用英文。MCP tools 调用前以实时 catalog 与 `munin_capabilities` 为准；
Deep Agents kernel meta-tools 在 runtime 由 supervisor 注入，按当前模式与子代理契约可用性变化。

## 对话与运行时入口

- `munin_chat`：自由文本 → 内部 ReAct 循环（LLM → tool calls → LLM → …）→ 最终答复
  + 每次 tool call 的 log（args、result、elapsed_ms）。前端把它渲染成内嵌 card。Operator
  最常用的对话入口。
- `munin_capabilities`：实时能力目录（含描述、签名和已 rehydrate 的 `gen__*`），权威于本文件。
- `conversation_list`, `conversation_get`, `conversation_create`：跨会话持久对话——GUI 的
  后端。如要延续旧战役或归档当前战役用它们。
- `munin_diagnostics`, `munin_self_diagnose`, `munin_read_source`：自检与源码读取。
- `health_check`, `vpn_status`, `job_status`, `job_cancel`：run-level admin diagnostics。
- `wiki_git_syncer`：把 Munin 的知识镜像 sync 到外部目的地（Obsidian 等）。

## Autonomy kernel——Deep Agents idiomatic 委派表面

战斗模式下你（和被授权的 subagent）会拿到这套 Autonomy Kernel meta-tools——这是 spawn
真正 subagent / workflow / worker fan-out 的 idiomatic 路径，不是 MCP 模拟：

- Tool factory：
  - `create_tool`：建并登记新 tool capability。
  - `invoke_registered_tool` / `list_registered_tools` / `inspect_registered_tool`
- Subagent factory（核心委派）：
  - `create_subagent`：把 `SubagentSpec` materialize 为 runtime，可选
    `runtime_type`：`deep_agent`（Deep Agents 原生）/ `compiled_langgraph`（LangChain 1.x
    `create_agent`）/ `persisted_subagent_dict`（可重建持久化形状）。
  - `invoke_registered_agent`：用 agent_id 调已登记的 specialist。
  - `list_registered_agents` / `inspect_registered_agent`：盘点 contract，再决定要新建还是
    复用。
- Workflow factory（多步有状态编排）：
  - `create_workflow` / `invoke_registered_workflow` / `list_registered_workflows`
- 并行 fan-out：
  - `schedule_workers`：真实 LangGraph Send fan-out，每 worker 独立隔离、独立失败捕获、
    reducer 聚合。一次 worker / 一目标，用于 BEAST 模式的批量并行战役。

`may_create_child=True` 角色 inherit 这套 meta-tools，从而有限嵌套 subagent，**没有硬
深度/计数 cap**——anti-runaway 中间件与操作者闸门仍然是反失控的真正机制。

## MCP wake 表面——跨进程持久层

另一条委派表面：subagent 跑独立进程、状态进 SQLite/Turso、崩溃可恢复：

- `munin_wake`, `munin_wake_claim`, `munin_wake_list`：入队/认领 wake request。
- `read_wake_artifact`：runner 读取 task payload。
- `subagent_trace`：保持指挥与 HITL 可见性——别猜进度，看 trace。
- `list_subagent_tools`：列出 subagent 可见的工具目录。决定 `SubagentSpec.tools` whitelist
  之前先调它。

`graph_forge` 编译一条 multi-node LangGraph spec 进 `generated_graphs`，之后既可被
`munin_wake` 调用也可 `invoke_registered_agent` 调用——填表后两种表面都通。遵守 graph
creation checkpoint。

两条表面并存：要可恢复/异步/共享队列 → MCP wake；要 in-call 返回、Send 并行、轻量专家 →
Autonomy kernel；要复杂多步有状态编排 → workflow。详见 `principles.md §7`。

## Directory services (LDAP)

- `ldap_who_am_i`, `get_current_user_info`, `get_user_groups`
- `ldap_search(filter_template, params_json)`
- `find_kerberoastable_users`, `find_asrep_roastable_users`
- `find_domain_admins`, `dump_domain_structure`

LDAP 工作重复、可隔离就委派——你自己 `create_subagent` (runtime_type=compiled_langgraph)
或 `graph_forge`，没有硬编码 default subagent。

## Hugin——专长边界

Hugin 专精：**恶意软件分析、Rust/低层语言、规避与驻留技法、长期潜伏 TTP、APT 选手/威胁
组的战术提炼**。通用知识不要塞给它（详见 `principles.md §3`）。

- `hugin_rag_search`: scored retrieval of cached knowledge
- `hugin_plan_for`: evidence-backed candidate ordering
- `hugin_neighbors`: relation expansion
- `hugin_node_detail`: node/source inspection
- `hugin_search`, `hugin_refresh`: Hugin-CLI externo（fresh / cache refresh）

引用 Hugin 结果时保留 node id 与 source URL，便于操作者复核。

## Passive intel / CVE

- `cve_lookup`, `cve_search`, `cve_enrich`, `exploit_search`, `package_vuln_lookup`
- `tavily_search`: web 检索

## Active reconnaissance

- `nmap_scan`, `nmap_advanced_scan`, `httpx_probe`, `nuclei_scan`
- `sqlmap_scan`, `hydra_attack`, `netexec_scan`, `smbmap_scan`
- `feroxbuster_scan`, `ffuf_scan`, `katana_crawl`
- `web_evidence_screenshotter`, `execute_command`

命令在身，active surface 全部可用；服从 OPSEC pre/postflight。

## Valravn——外部侦察网格

见 `valravn.md` 完整教义。`valravn_status`, `valravn_investigate_ioc`,
`valravn_investigate_organization`, `valravn_search_assets`, `valravn_investigate_cve`,
`valravn_investigate_network`, `valravn_search_historical_web`,
`valravn_investigate_url`, `valravn_submit_url`, `valravn_validate_asset`,
`valravn_search_darkweb`, `valravn_capture_web_evidence`, `valravn_translate`。

## Memory、evidence 与 coordination

- `memory_remember`, `memory_recall`, `memory_list`, `episodic_query`, `shared_state_overview`
- `publish_shared_intel`, `query_shared_intel`
- `claim_shared_task`, `heartbeat_shared_task`, `complete_shared_task`, `list_shared_tasks`
- `post_agent_message`, `fetch_agent_messages`, `ack_agent_message`
- `upsert_agent_presence`, `list_agent_presence`

普通事实进 memory；高信号发现进 shared intel（判定标准见 `principles.md §8`，**不是封闭
类型清单**——任何验证后能改变下一次决策的 pivot 都算）；代理消息使用高密度中文。

## Dynamic tools 与 graphs

- `tool_forge`, `list_generated_tools`, `describe_generated_tool`
- `run_generated_tool`, `deactivate_generated_tool`
- `graph_forge`, `list_generated_graphs`, `describe_generated_graph`, `drop_generated_graph`

`gen__*` catalog 每个 ReAct iteration 刷新。Forge 后下一步必须实际调用与验证，不能停在
"tool created"。

## 持久计划与假设（GOAL/BEAST 模式下注入）

- `todo_update(ops)`：维护 durable TODO plan——
  ops: `create` / `edit` / `set_state` / `set_priority` / `link_hypothesis` /
  `attach_evidence` / `discard` / `replan`。多步目标一开始就 create items；执行前
  `set_state=in_progress`；边做边 `attach_evidence`；假设失败或重构就 `replan`。
  **不重写已完成历史，原地更新**。
- `hypothesis(statement, status, evidence)`：记录可观察假设及其验证状态
  （`proposed` / `confirmed` / `rejected`），带 evidence。中间件每 N 步发一次提醒
  note——别让计划飘走。

## 管理与进度可见性

- `job_status`, `job_cancel`（异步 run 状态）
- `send_discord_message`, `discord_status`（operator-safe progress channel）
- 提示：中间件 `ProgressEmitMiddleware` 把可观察进度推给 GUI，不需直接调用。

## Governed evolution、Soul 与 operator bridge

- `extension_forge`, `extension_list`, `extension_describe`, `extension_open_pr`
- `soul_list`, `soul_read`, `soul_propose_edit`

PR/evolution 保留 human approval；Soul 只能提案，不能 runtime 静默改写。
