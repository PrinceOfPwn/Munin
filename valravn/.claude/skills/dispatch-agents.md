---
name: dispatch-agents
description: 编排并行渗透代理——同时派专家做侦察、扫描、验证、payload 构造
---

# 派并行代理

你是编排者。任务：识别独立工作流并派专家代理并行跑，大幅缩总测试时间。一次 50 顺序工具调用的全 hunt 可用 15 个调并行收。

## SMART MOVE —— 按输入形状路由代理

| 手中输入 | 单代理派 |
|---|---|
| 新域，无 intel | `recon-agent` —— 跑 smart-move-fresh-target.md |
| 捕获的 proxy 条目（≥5） | `vuln-scanner` 或 `grow-agent` 每条目 smart_request_triage |
| 一堆 `.js` URL / chunks | `js-analyst` —— 包 smart_js_analyze（批 ≤25） |
| 已知 CVE-id，PoC 失败 | `grow-agent` 跑 probe_cve_with_variants（smart-move-known-cve-poc-fails.md） |
| 疑似发现需 replay | `finding-verifier` —— confirm_* + assess_finding |
| 需绕过 payload | `payload-crafter` —— get_payloads + mutate_payload 链 |
| OAuth/SAML/passkey 流 | `auth-payment-agent` —— oauth_flow_simulator + probe_passkey_stepup_bypass |
| 隐藏路径 / 40x 绕过 | `fuzz-agent` —— run_ffuf + run_dontgo403 |
| 移动 app 动态 | `mobile-dynamic-agent` —— Frida + adb（每设备 1 个） |
| Electron/Tauri 二进制 | `desktop-agent` —— IPC fuzz + auto-update MITM（每二进制 1 个） |

先选代理，再下简报——代理拥有其形状的 playbook。


**并发上限：最多 6 个同时代理。** Java 扩展的 HTTP 服务用固定 6 线程池（见 `ApiServer.java`），故 6 个在飞 MCP 请求真并行。超 6 排队。早期指南封 3-4——错；见即改。

### 费力缩放阶梯（Spec E2.2——有 FLOOR，非仅 ceiling）

过度派贵：代理比直调 token 成本 ~15×（Anthropic 多代理研究：token 用量解释 80% eval 方差）。匹配 fan-out 到工作：

| 工作形状 | 派 |
|---|---|
| 微不足道 / 单查 / 一已知调 | **不派代理** —— 内联直调工具 |
| 一漏洞类 ≤3 端点 | **1 worker** |
| 一类跨多端点，或 2-4 独立类 | **2-4 workers**，按不重叠端点集分 |
| 广覆盖扫 | **≤6 workers**，分区；不超上限 |

派 ≥4 workers 或 Tier-2 council panel 前，查 `check_cost_budget`——交战近封则缩。上限（6）是限；此阶梯是默认路由。

## 强制简报块

每个被派子代理除任务外**必**收到这三行逐字。子代理不见前对话，无此块它们幻觉文件路径、行号、函数名、发现——过往审计多次见。

```
ORIENT FIRST: your first call is `target_brief(domain)` — it returns tech/auth context, findings posture, top findings, next-action hints, and copy-paste follow-up queries in one lean response. Read it before acting so you build on peers' prior-round findings (the shared .valravn-intel/<domain>/ blackboard) instead of re-discovering. If it returns exists:False, the target is new — run recon first.

VERIFY before reporting: every file:line you cite must be opened and read in this run; every symbol you name must be grepped in this run; every finding you claim must reference an existing logger_index from get_proxy_history. If you cannot verify a claim, mark it UNVERIFIED rather than reporting it as fact.

EVIDENCE FORMAT: persist full output to .valravn-intel/<domain>/ and report back only lightweight references — finding ids, logger indices, top-N ranked — as `<severity> | <file>:<line> | <one-line problem> | <one-line fix>`. Never return full dumps; the orchestrator's context is finite across a long loop.

SCOPE GUARD: do not act outside your assigned scope. Recon agents do not test vulnerabilities. Vuln-scanner agents do not exfiltrate data. Verifier agents do not save findings (they report back; orchestrator persists). Least-privilege boundary (Spec E2.3): recon-agent / js-analyst do NOT call save_finding / assess_finding / confirm_* / msf_* / any exploit tool — they return intel; the orchestrator gates and persists.
```

写派 prompt 时把上面块贴到任务特定指令前。无捷径。

## 何时用此 skill

- 起新交战（侦察可并行）
- hunt 的 Phase 3（多类漏洞测试）
- 恢复会话（多发现待重验）
- 调查异常同时续扫
- 任何 2+ 独立任务不共享状态时

## 何时**不**并行

- 登录 / 认证流（必顺序——cookies 依赖前步）
- `run_flow` 多步攻击（设计上顺序）
- Race condition 测试（需协调时序）
- 目标重叠时（两代理击同端点 = 限速触发）

## 派模式 1：侦察 Fanout

交战起始同时派两代理：

```
Launch Agent 1 (recon-agent, background):
  "You are a reconnaissance agent for {domain}. Session name: {session}.
  
  1. Run discover_attack_surface(session='{session}', max_pages=20)
  2. Run discover_common_files(session='{session}')
  3. Run discover_hidden_parameters(session='{session}', path='/', wordlist='extended')
  
  Return: complete endpoint list with risk scores, sensitive files found, hidden params.
  Do NOT test for vulnerabilities — only map the surface."

Launch Agent 2 (js-analyst, background):
  "You are a JavaScript analysis agent for {domain}. Session name: {session}.
  
  1. Run quick_scan(session='{session}', method='GET', path='/') to get the root page index
  2. Run fetch_page_resources(index=ROOT_INDEX) to grab all JS files
  3. For each JS file (up to 10): run extract_js_secrets(index=JS_INDEX)
  4. Run analyze_dom(index=ROOT_INDEX) for DOM sink/source analysis
  
  Return: all secrets found (with severity), DOM XSS flows, hidden API endpoints in JS.
  Do NOT test for vulnerabilities — only analyze."
```

**两者完成后：** 合并结果。recon 代理给端点 + 风险分。JS 分析师给 secrets + DOM XSS 线索。合成优先攻击计划。

## 派模式 2：并行漏洞测试

侦察后按类分目标。**铁律：代理间无目标重叠。**

```
# 先按参数风险分类分目标
sqli_targets = [endpoints with id/uid/num/page params]
xss_targets = [endpoints with search/q/comment/name params]
lfi_targets = [endpoints with file/path/include/template params]
auth_endpoints = [all authenticated endpoints for IDOR testing]

Launch Agent 1 (vuln-scanner, background):
  "You are a SQL injection scanner for {domain}. Session: {session}.
  
  Test these specific targets for SQLi ONLY:
  {sqli_targets_json}
  
  Use: auto_probe(session='{session}', targets=TARGETS, categories=['sqli'])
  
  For any finding with score >= 30: re-send the payload once to confirm.
  
  Return: list of findings with scores, tested params count, confirmed vs suspected."

Launch Agent 2 (vuln-scanner, background):
  "You are an XSS scanner for {domain}. Session: {session}.
  
  Test these specific targets for XSS ONLY:
  {xss_targets_json}
  
  Use: auto_probe(session='{session}', targets=TARGETS, categories=['xss'])
  
  For any reflected payload: check if it's in an executable context (not encoded).
  
  Return: list of findings with reflection context, tested params count."

Launch Agent 3 (vuln-scanner, background):
  "You are an LFI/path traversal scanner for {domain}. Session: {session}.
  
  Test these specific targets:
  {lfi_targets_json}
  
  For each target: run test_lfi(session='{session}', path=PATH, parameter=PARAM)
  
  Return: list of findings with file content indicators, tested params count."

Launch Agent 4 (auth-tester, background):
  "You are an authorization tester for {domain}. Sessions: admin={admin_session}, user={user_session}.
  
  Test these endpoints for IDOR:
  {auth_endpoints_json}
  
  Use: test_auth_matrix(endpoints=ENDPOINTS, auth_states={
    'admin': {'session': '{admin_session}'},
    'user': {'session': '{user_session}'},
    'anon': {'remove_auth': true}
  })
  
  Return: IDOR findings with similarity scores, potential access control issues."
```

**全部完成后：** 合并所有发现。按 severity 排。查高分异常。

## 派模式 3：验证批

会话恢复带多 stale 发现时：

```
# 按 severity 分组
critical_findings = [findings where severity == 'CRITICAL']
high_findings = [findings where severity == 'HIGH']
other_findings = [findings where severity in ('MEDIUM', 'LOW')]

Launch Agent 1 (finding-verifier, foreground — need results immediately):
  "You are verifying CRITICAL findings for {domain}. Session: {session}.
  
  For each finding, re-send the exact poc_request and check:
  {critical_findings_json}
  
  Evidence requirements:
  - SQLi: timing > 3x baseline (test 3 times)
  - XSS: payload unencoded in response
  - SSRF: Collaborator interaction
  - RCE: command output in response
  
  Return: updated status (confirmed/stale/likely_false_positive) with evidence for each."

Launch Agent 2 (finding-verifier, background):
  "You are verifying HIGH findings for {domain}. Session: {session}.
  
  For each finding, re-send the poc_request:
  {high_findings_json}
  
  Return: updated status with evidence for each."

Launch Agent 3 (finding-verifier, background):
  "You are verifying MEDIUM/LOW findings for {domain}. Session: {session}.
  
  For each finding, re-send the poc_request:
  {other_findings_json}
  
  Return: updated status with evidence for each."
```

## 派模式 4：调查 + 续扫

扫描现值得调查的异常时：

```
Launch Agent 1 (payload-crafter, foreground — need results):
  "You are investigating a potential {vuln_type} on {domain}.
  Session: {session}. Target: {method} {path}?{param}
  
  The auto_probe returned score {score} with these anomalies: {anomalies}
  
  Follow the investigate skill:
  1. Establish baseline behavior
  2. Map what characters/keywords are filtered
  3. Try context-specific payloads
  4. If filter found, use craft-payload approach to build bypass
  5. Verify any working payload 2-3 times
  
  Return: confirmed finding with evidence, OR 'false positive' with explanation."

Launch Agent 2 (vuln-scanner, background — continue the hunt):
  "You are continuing vulnerability scanning for {domain}.
  Session: {session}.
  
  Test the next category ({next_category}) on these targets:
  {next_targets_json}
  
  Use: auto_probe or bulk_test as appropriate.
  
  Return: findings with scores."
```

## 派模式 5：全并行侦察 + 边缘测试

全面首扫：

```
Launch Agent 1 (recon-agent, background):
  "Map attack surface: discover_attack_surface + discover_common_files"

Launch Agent 2 (js-analyst, background):
  "Analyze JavaScript: fetch_page_resources + extract_js_secrets + analyze_dom"

Launch Agent 3 (vuln-scanner, background):
  "Test edge cases: test_cors + test_graphql (if /graphql exists) + test_jwt (if JWT auth)"
```

## Prompt 模板最佳实践

派代理时总含：

1. **域和 session 名** —— 代理需这些调 MCP 工具
2. **具体目标** —— 精确端点 / 参数，非 "find them yourself"
3. **用什么工具** —— 勿让代理猜
4. **返什么** —— 结构化结果格式
5. **不做什么** —— 防代理跑偏
6. **证据要求** —— 对验证代理

**好 prompt：**
```
"You are a SQL injection scanner for example.com. Session: 'target1'.

Test these 5 endpoints for SQLi:
[{"method":"GET","path":"/api/users","parameter":"id","baseline_value":"1","location":"query"},
 {"method":"GET","path":"/api/products","parameter":"pid","baseline_value":"100","location":"query"}]

Use: auto_probe(session='target1', targets=ABOVE, categories=['sqli'])

For any finding with score >= 30, re-send the payload to confirm timing or error.

Return: JSON array of findings. Do NOT test other vuln types. Do NOT modify the session."
```

**坏 prompt：**
```
"Test example.com for vulnerabilities."  # 太泛，浪费 token 探索
```

## 合并结果

代理完成后，编排者必：

1. **收全发现** 入单列表
2. **去重** —— 同端点 + 同漏洞类型 = 留最高分
3. **按 severity 排** —— CRITICAL 优先
4. **识别调查候选** —— 30-50 分异常
5. **更新 memory：**
   ```python
   save_target_intel(domain, "findings", merged_findings)
   save_target_intel(domain, "coverage", merged_coverage)
   ```
6. **呈用户** —— 显合并仪表板

## 效率收益

| 方案 | 顺序 | 并行（代理） | 提速 |
|---|---|---|---|
| 全侦察 | 6 调，~2 分 | 2 代理，~1 分 | 2x |
| 4 漏洞类 | 20 调，~5 分 | 4 代理，~2 分 | 2.5x |
| 验 6 发现 | 12 调，~3 分 | 3 代理，~1.5 分 | 2x |
| 全 hunt（侦察 + 测 + 验） | 50+ 调，~15 分 | 分阶段并行，~6 分 | 2.5x |

真收益不止挂钟时间——**上下文窗保留**。每代理用自己上下文，故编排者上下文干净留战略决策。
