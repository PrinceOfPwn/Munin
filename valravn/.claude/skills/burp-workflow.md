---
name: burp-workflow
description: 高效 Burp Suite 工具编排——知何时用何工具、如何链
---

# Burp Suite 工作流编排

你经 MCP 连 Burp Suite。160+ 工具在手。知**何时**用**哪个**是浪费 50 调与 5 调找到 bug 之差。本 skill 教高效 Burp 编排。

**ADVISOR 捷径：** 调 `pick_tool('your task')` 即取对工具 + 例。调 `get_hunt_plan(target)` 取全分阶段测试计划。

## SMART MOVE —— 首调

- "X 用啥工具？" → `pick_tool('X')`
- "此目标计划？" → `get_hunt_plan(domain)`
- "捕获请求看着怪" → `smart_request_triage(index)`（一调替 get_request_detail → extract_* → smart_analyze → 推理 → 选的四步循环）
- "找到 JS bundle" → `smart_js_analyze(index | url | urls=[])` —— 发按优先序的 (target, vuln_class, suggested_call, canary) 元组
- "已知 CVE / 公开 PoC 需 payload 调" → `probe_cve_with_variants(cve_id, target_url)` —— 有界变体扫，首 CONFIRMED 短路

## DEFAULTS（Rule 29 / 30 / 31 / 32）—— 偏好，非手铐

两模式并行。按**意图**选：

**A) 证据取** —— 你想读 / replay / 引用户已浏览生成的请求。
- 首工具：`search_history` / `get_proxy_history` / `extract_*` 对已捕获 index。勿用 `curl_request` 重造已有之物——捕获请求带真实 session 状态；新 curl 丢之。

**B) 造流量** —— 你跑测试本身：fuzz、爆破、限速、race、业务流、WAF 探、首触、受控变体。新请求是正确首选。用合适的：
- 自定义量 / 爆破 / 限速 / 垃圾 + 分支解码逻辑 Intruder 表达不了 → 手卷循环用 `curl_request` / `session_request`（+ `asyncio.gather` 并行发）
- Race condition → `test_race_condition`（服务端 latch——优于客户端并发）
- 多步业务流 → `run_flow`（线性）或显式 `session_request` 链（分支）
- 多参数 fuzz + 异常检测 → `fuzz_parameter` / `auto_probe`
- 绑已捕获基线的量活 → `send_to_intruder_configured`
- 已捕获请求一击微调 → `resend_with_modification` OR `send_to_repeater` + `repeater_resend`

**边干边 Annotate + Organize（Rule 31）。** 每个有趣的捕获 index 得 `annotate_request(index, color='RED|ORANGE|YELLOW|GREEN|CYAN|GRAY', comment='<f-id> | <vuln> | <evidence>')` AND `send_to_organizer(index)`。否则报告时重搜整段历史。

**Header profile（Rule 32）。** 两子模式：
- 像真客户端（默认常规流量）→ `get_target_headers(domain)` 一次，传 `headers=`。默认 httpx 签名被 WAF 拦并扭曲测试结果。
- 故意**不**像浏览器（WAF 检测、header 注入、smuggling、指纹探针、malformed-input）→ bare 或手卷 headers——那**就是**测试。

全表面图：`evidence-and-tabs.md`。

## 核心原则：少工具调，多信号

每工具调烧 token 和时间。在正确抽象层用正确工具。

## 工具选择决策树

### "我需理解目标"

```
首访？
  YES → full_recon(session, depth="standard")     # 一调：tech + endpoints + headers + secrets + robots
  NO  → check_target_freshness(domain, session)    # 查自上次何变

需端点图？
  快览  → get_unique_endpoints()           # 去重列表带参数
  全细节 → discover_attack_surface(session) # 爬 + 风险分 + 攻击优先
  API 规 → export_sitemap(format="json")    # 结构 API 图带参数类型

需分析一页？
  全分析 → smart_analyze(index)             # tech + params + forms + endpoints + secrets 一调
  否则   → 别 detect_tech_stack + smart_analyze + smart_analyze 分开调（费 3 调）
```

### "我需发请求"

```
简单一击请求？
  → curl_request(url, method, headers, body)        # 如 curl，带 redirect + auth

跨请求需持久认证？
  → create_session() THEN session_request()          # Cookie jar 自动更新

需改已捕获请求？
  → resend_with_modification(index, modify_*)        # 改 headers/body/path/method

需精确字节级控？
  → send_raw_request(raw, host, port)                # smuggling、CRLF、malformed 请求

多步流（登录 → 抽 CSRF → 利用）？
  → run_flow(session, steps=[...])                   # 一调替 5+

想在 Burp UI 可见？
  → send_to_repeater(index)                          # Burp 手动跟进
  → send_to_intruder(index)                          # Burp 基位置攻击
```

### "我需测漏洞"

```
多参数测多漏洞类型？
  → auto_probe(session, targets, categories)         # 知识驱动，服务端 matchers

一特定漏洞跨多端点测？
  → bulk_test(session, vulnerability="sqli")         # 一漏洞类，自动发现目标

一参数深测？
  → probe_endpoint(session, method, path, param)     # 自适应：自动探 tech、选 payload

自定义 payload 列表上一参？
  → fuzz_parameter(index, parameter, payloads)       # 你的 payload，异常检测

需同时请求（race condition）？
  → test_race_condition(session, request, concurrent) # 服务端 CountDownLatch

需 N 端点 × M auth 状态？
  → test_auth_matrix(endpoints, auth_states)          # IDOR 矩阵一调
```

### "我需浏览目标并灌 proxy history"

```
自动爬（最快）  → browser_crawl(url, max_pages=20)           # 经 Burp proxy 的隐身 Chromium
点一切        → browser_interact_all(url, max_clicks=30)   # 按钮、链接、toggle
导航 + 观 DOM  → browser_navigate(url) then browser_execute_js(script)
填提交表单     → browser_submit_form(fields, submit_selector)
取页面总览     → browser_get_page_info()                     # 表单、cookies、inputs
```

### "我需从响应抽特定数据"

```
勿读整响应——用抽取工具（10× 省 token）：
  HTML 抽 CSRF token → extract_css_selector(index, 'input[name=csrf]', attribute='value')
  JSON 字段          → extract_json_path(index, '$.data.user.role')
  自定义模式         → extract_regex(index, 'pattern', group=1)
  仅安全 headers     → extract_headers(index, ['CSP', 'X-Frame-Options'])
  全页链接           → extract_links(index, link_filter='internal')
  快变检测           → get_response_hash(index)
```

### "我需控 proxy"

```
  Intercept 开/关      → intercept(action="on") / intercept(action="off")
  自动改流量           → match_replace(action="set", rules=[{type, match, replace}])
  标注条目             → annotate_request(index, color='RED', comment='SQLi')
  流量统计             → get_proxy_stats()
  监模式               → traffic_monitor(action="register", tag, patterns)
  轮询新流量           → get_live_requests(since_index)
```

### "我需迭代一请求"

```
  Repeater 跟踪        → send_to_repeater_tracked(index, 'tab-name')
  改后重发             → repeater_resend('tab-name', modify_path='/new/path')
  可复用多步           → create_macro(name, steps) then run_macro(name)
```

### "我需战略帮助"

```
  全 hunt 计划         → get_hunt_plan(target_url)
  下一步最佳行动       → get_next_action(target_url, completed_phases)
  验证发现             → assess_finding(vuln_type, evidence, endpoint)
  选对工具             → pick_tool('task description')
```

### "我需确认发现"

```
盲漏洞（无可见输出）？
  → auto_collaborator_test(index, parameter)          # 生成 + 注入 + 轮询一调

需比两响应？
  快 diff   → get_response_diff(index1, index2)    # 显 diff 行
  全比较    → compare_responses(index1, index2)     # headers + body + unique words + similarity %
  Burp UI   → send_to_comparer(index1, index2)     # Burp Comparer tab 可视

Auth 比较（IDOR）？
  → compare_auth_states(index, alt_cookies/alt_token)  # 同请求，不同 auth
```

## Proxy History 模式

Proxy history 是主数据源。高效用之：

### 找有趣请求
```python
# 勿手翻 history——搜之
search_history(query="admin", in_url=True)           # 找 admin 端点
search_history(query="token", in_response_body=True)  # 找 token 泄露
search_history(query="password", in_request_body=True) # 找 auth 流
search_history(query="upload", in_url=True)            # 找 upload 端点
search_history(query="api/v", in_url=True)             # 找 API 版本

# 按 status 筛有趣响应
get_proxy_history(filter_status="500")                 # 服务端错（注入候选）
get_proxy_history(filter_status="302")                 # 重定向（open redirect 候选）
get_proxy_history(filter_status="403")                 # 禁（auth 绕过候选）
get_proxy_history(filter_method="POST")                # 状态变更请求
```

### 详读一请求
```python
# 总查请求 AND 响应
detail = get_request_detail(index)
# 找：auth headers、CSRF tokens、有趣 cookies、JSON 结构
# 响应中：error message、堆栈跟踪、版本 string、反射输入
```

## Collaborator 工作流（盲漏洞检测）

盲 SSRF、盲 XXE、盲 SQLi、盲命令注入：

```
首选（一调）：
  auto_collaborator_test(index, parameter, injection_point, poll_seconds=10)
  # 生成 payload → 注入 → 发 → 等 → 轮询 → 报

手动（需控时）：
  1. generate_collaborator_payload()         # 取唯一 URL
  2. 手注 URL 入参数                            # 经 session_request 或 resend_with_modification
  3. 等 5-10 秒
  4. get_collaborator_interactions()          # 查 DNS/HTTP 回调
```

**何时用 Collaborator：**
- 参数接 URL 但无可见 SSRF 输出
- XXE 无 error message（盲）
- 命令注入无输出（盲）
- 任何怀疑服务端处理但看不到结果的参数

## Session 管理策略

### 何时用 session
- 目标需认证（多数真目标）
- 多步测试（需 cookies 持久）
- 比对不同用户角色行为

### Session 模式
```python
# 模式 1：单认证用户
create_session(name="user1", base_url="https://target.com", bearer_token="eyJ...")

# 模式 2：多用户做 IDOR 测试
create_session(name="admin", base_url="https://target.com", cookies={"session": "admin_cookie"})
create_session(name="user_b", base_url="https://target.com", cookies={"session": "user_cookie"})
# 然后：用两 session test_auth_matrix

# 模式 3：带 CSRF 的登录流
run_flow(session="s1", steps=[
  {"method": "GET", "path": "/login", "extract": {"csrf": {"from": "body", "regex": "csrf.*value=\"([^\"]+)\""}}},
  {"method": "POST", "path": "/login", "data": "user=admin&pass=test&_token={{csrf}}"},
])
```

## 响应比较策略

比响应是检测多数漏洞之法。选对比较：

| 场景 | 工具 | 找什么 |
|---|---|---|
| 同请求，不同 auth | `compare_auth_states` | 相同响应 = IDOR |
| 同请求，带 / 不带参数 | `compare_responses` | 新内容 = 参数有效 |
| 基线 vs 注入 | `fuzz_parameter` 异常检测 | status/length/timing 变 |
| 两不同端点 | `get_response_diff` | 共享模式或差异 |

## JavaScript 分析流水线

JS 文件是金矿。高效流水线：

```
1. fetch_page_resources(index)        # 取页全 JS/CSS
2. 每 JS 文件：
   extract_js_secrets(js_index)       # API key、token、内部 URL
   analyze_dom(js_index)              # Sinks、sources、流
3. 找 secrets → 立即测（击 API key、访问 URL）
4. 找 DOM sinks → 造针对特定 sink 的 DOM XSS payload
```

## 扫描器集成（Burp Professional）

战略用 Burp 内置扫描器——勿扫一切：

```python
# 对特定可疑请求定点扫
scan_url(index=42)                    # 扫一已捕获请求

# 爬 + 扫一段
crawl_target(url="https://target.com/api/")
# 等，然后：
get_scan_status()                     # 查进度
get_scanner_findings(severity="HIGH") # 取结果
```

**何时用扫描器 vs 手测：**
- 扫描器：良端点上广覆盖、被动检测
- 手动（你的工具）：创意测试、业务逻辑、auth 绕过、链攻击

## Web TLS 审计（WSTG-CRYP-01）

TLS 版本 + 密码套件无法从 Burp 内可靠观察（Burp 自握手）。每 in-scope host 跑一次 nmap 并记入 intel。

```bash
nmap --script ssl-enum-ciphers -p 443 <host>
# FAIL: TLS 1.0, TLS 1.1, SSLv3, RC4, 3DES, NULL, EXPORT, anonymous DH
# PASS: TLS 1.2+ only, no broken cipher
```

然后：`save_target_intel(<host>, "fingerprint", {"tls_audit": "<nmap-summary>"})`。同 `playbook-mobile-dynamic.md` Phase 3 MASTG-TEST-0218 流——独立于其他漏洞的单独发现。

## 反模式（勿做）

1. **勿分调 detect_tech_stack + smart_analyze + smart_analyze** —— 用 `smart_analyze`（一调）
2. **勿 run_flow 一调搞定的事发 10 个 session_request** —— 批多步攻击
3. **勿手建 cookie header** —— 用 `create_session` 让 cookie jar 自动更新
4. **勿每端点测每漏洞类型** —— 用 `discover_attack_surface` 按风险分排
5. **勿忘搜 history** —— 你要的请求可能已被捕获
6. **勿需 auth 时用 curl_request** —— 用 `session_request`
7. **勿扫全站** —— 对特定可疑端点点扫
