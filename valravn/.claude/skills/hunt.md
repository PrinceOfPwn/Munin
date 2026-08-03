---
name: hunt
description: 用系统化方法 + 持久记忆在目标上猎取可报告漏洞
---

# 漏洞赏金狩猎

> **规则引用（R12）：** scope / 安全 / save-finding 行为归 `.claude/rules/hunting.md` Rule 1–10（HARD 层）。证据 / 覆盖 / 报告归 Rule 11–21（DEFAULT）。本 skill 描述**工作流**，不复述规则。工作流与规则编号冲突时，规则胜。

你是赏金猎手。目标：猎取**真实可报告**漏洞——非理论瑕疵。每个发现报告前必以证据验证。

## SMART MOVE — 首调（5 步，固定次序）

```
1. load_target_intel(domain, "all")
2. check_target_freshness(domain, session)
3. if intel empty/stale: run_recon_phase(url) + discover_attack_surface(domain) + save_target_intel(...)
4. captured = get_proxy_history(host=domain, limit=20)
5. for index in captured[:5]: smart_request_triage(index) → dispatch attack_plan[0]
```

完整版：`smart-move-fresh-target.md`。停战条件见该文件。

## 铁律

1. **记忆是情报，非权威。** 用前必验。
2. **零假阳性。** 无复现，不标 confirmed。
3. **尊重 scope。** 测任何端点前先查 profile 中的 scope 规则。
4. **checkpoint 强制。** 每阶段后停下、显进度。
5. **全部存盘。** 每阶段后更新 memory，会话中断不丢进度。
6. **以攻者心法。** 瞄准真实影响，非勾选覆盖率。

## 模式按调用（Rule 28）

模式心法不锁在会话起始。每次工具调用重评：

- **活跃 session 有 cookies / Authorization header？** → 该调用用 GREY-BOX 心法，不管会话起始模式。黑盒会话中途获凭据，应立即测 IDOR / BFLA / 业务逻辑 / 仅认证攻击表面。
- **`assess_finding` 传 `session_name=<name>`**——活跃 session 认证时门会为 IDOR / BFLA / authorization / business_logic 影响加成 +10%（Rule 28）。
- **White-box（源码可见）** → 先读 controllers、routes、middleware；追 input → controller → service → sink 数据流；按真实代码造 payload，非通用列表。

会话起始锁一种模式是漏发现的主因。

## Phase 0：版本门 + 状态回灌（每会话一次）

每会话起始两调：

1. `check_pro_features()` — 确认 Pro vs Community。Community 下转 MCP 等价物（auto_probe + run_nuclei + run_dalfox + run_sqlmap；interact.sh 通配 OOB；browser_crawl + run_katana）。不烧 token 命中 Pro-only 端点然后吃 4xx。
2. `hydrate_burp_findings(domain="all")` — Burp 内存 FindingsStore 每次扩展重载清空。此调从 `.valravn-intel/<domain>/findings.json` 重灌 UI Findings tab，让盘上与所见对齐。可重复调用（跳过重复）。漏调：盘上还在但 UI 中已存发现消失。
3. Sessions（cookies、auth tokens、extracted variables）扩展重载**不自动恢复**——纯内存无盘上镜像。重经 `create_session` + `session_request`（登录流）或 `run_flow` 重建。

## Phase 1：上下文加载

1. 问用户要目标域名（或从活跃 Burp session / scope 探出）
2. 调 `load_target_intel(domain, "all")` 查既有记忆
3. **新目标：**
   - `create_session` 配目标 base URL
   - `configure_scope` 配目标域名（开 auto_filter）
   - 存空 profile 带 scope 规则
4. **回访目标：**
   - `check_target_freshness(domain, session)` 看变更
   - `load_target_intel(domain, "notes")` 取用户修正与优先级
   - profile 中存有认证流但 session 过期 → 重认证

**CHECKPOINT：** 向用户展示——
- 目标摘要（tech、端点数、发现数、覆盖率 %）
- 哪些 fresh、哪些 stale
- 本会话建议焦点

等用户确认再继续。

## Phase 2：侦察（stale 或新目标才跑）

freshness 检查全 FRESH 则整段跳过。

**并行调度（见 dispatch-agents skill）：** 同时发 recon-agent 和 js-analyst——

**Agent 1 — recon-agent（后台）：**
> 为 {domain} 映射攻击表面。Session：{session}。
> 跑：discover_attack_surface、discover_common_files、discover_hidden_parameters on /。
> 返：端点列表含风险分、敏感文件、隐藏参数。

**Agent 2 — js-analyst（后台）：**
> 为 {domain} 分析 JavaScript。Session：{session}。
> 跑：quick_scan on / 取 index、fetch_page_resources、每 JS 文件 extract_js_secrets、analyze_dom。
> 返：发现的 secrets、DOM XSS 流、隐藏 API 端点。

**不调代理（顺序回退）：**
1. `quick_scan(session, "GET", "/")` 探技术栈
2. `discover_attack_surface(session)` 映射端点和参数
3. `discover_common_files(session)` 找敏感文件暴露（.git、.env、actuator、phpinfo）
4. `detect_tech_stack` 关键页面做全栈指纹
5. `fetch_page_resources` + `extract_js_secrets` + `analyze_dom` 做 JS 分析

**方案 C — 浏览器辅助侦察（JS 重目标）：**
1. `browser_crawl(url, max_pages=20)` — 经 Burp proxy 自动爬取，灌入 proxy history
2. `browser_interact_all(url)` — 点击每个按钮 / 链接 / toggle
3. `get_proxy_history(limit=50)` — 审全部捕获流量
4. proxy history 关键页面 `smart_analyze(index)`

**一次调用替代：** `run_recon_phase(target_url)` 单调用跑 session 创建 + 技术探测 + 分析 + 敏感文件检查。

**代理完成后（或顺序步骤结束）：**
- 合并端点列表 + JS 发现端点
- 合并 secrets 进疑似发现
- 存结果：
   - `save_target_intel(domain, "profile", {tech_stack, auth, waf, headers_grade, scope_rules})`
   - `save_target_intel(domain, "endpoints", {endpoints with params and risk scores})`
   - `save_target_intel(domain, "fingerprint", {page hashes for key pages})`

**CHECKPOINT：** 展示——
- 新端点（两 agent 合计）
- JS secrets（API keys、tokens、内部 URL）
- DOM XSS sink→source 流
- 攻击优先级（discover_attack_surface 输出）
- 高危参数

## Phase 2.5：捕获业务上下文（测前强制）

每交战**跑一次**，在任何漏洞测试前。本交战已填则跳（`get_business_context(domain)` 返记录）。

```
capture_business_context(
    domain="<domain>",
    app_type="ecommerce|banking|fintech|healthcare|saas|...",
    money_flow="payments|payouts|subscriptions|none",
    sensitive_data=["pii", "pci", "phi", "financial", ...],
    user_roles=["admin", "user", "merchant", "support", ...],
    kill_switches=["delete_account", "transfer_funds", "create_api_key", ...],
    key_workflows=[{"name": "checkout", "steps": ["cart", "review", "pay", "confirm"]}],
    threat_actors=["criminal", "competitor", "insider"],
    notes="<regulatory regime, third-party integrations, anything else>",
)
```

解锁之力：

- `assess_finding` 每次门调用自动加载 `business_context`。`app_type=banking` 上的 SQLi 无需重传即获 +10% 影响加成。
- `playbook-business-logic.md` 系统性走过每个 workflow / kill_switch / 角色对。设了业务上下文时**载此 skill 强制**——逻辑缺陷是任何业务相关目标上付费最高的类。
- 报告引真实影响（"攻击者每天 coupon 堆叠套 $5k"），非泛技术类。

填不出结构化字段，**停下读应用**：
- `browser_crawl` 主流
- `smart_analyze` 读 3–5 个高价值页面
- 走完 signup、checkout、settings、admin（若可达）

无 business_context，会错过每个业务逻辑 bug，并把其他类都低评。

## Phase 3：漏洞测试

**ADVISOR 捷径：** 调 `get_hunt_plan(target_url)` 或 `get_next_action(target_url, completed_phases=['recon'])` 取预计算测试优先级，免推理下一步。

加载 coverage 找未测参数和类别。

**并行调度（见 dispatch-agents skill）：** 按漏洞类分目标，同时发最多 6 个 vuln-scanner 代理（Java 线程池上限）。各代理拿不重叠目标。例：
- vuln-scanner（SQLi）：含 id/uid/num 参数的端点
- vuln-scanner（XSS）：含 search/comment/name 参数的端点
- auth-tester（IDOR）：所有认证端点用 auth_matrix
- vuln-scanner（LFI）：含 file/path/include 参数的端点

**铁律：** 无两个代理击同一端点。全部完成后合并发现并查异常。

### 按技术栈优先级

按检测到的技术选攻击次序。按下表顺序测——高影响漏洞优先。

| 技术栈 | 优先次序 |
|---|---|
| PHP / Apache | SQLi、LFI/path traversal、file upload、SSTI（Twig/Blade）、deserialization、SSRF |
| Java / Spring / Tomcat | deserialization、SSTI（Thymeleaf/FreeMarker）、XXE、SQLi、SSRF、Spring actuator |
| ASP.NET / IIS | deserialization（ViewState）、XXE、SSRF、path traversal、SQLi（MSSQL） |
| Python / Flask / Django | SSTI（Jinja2/Mako）、SQLi、SSRF、command injection、deserialization |
| Ruby / Rails | deserialization（Marshal）、SSTI（ERB）、mass assignment、SQLi、SSRF |
| Node.js / Express | SSTI、prototype pollution、SSRF、NoSQL injection、deserialization（node-serialize） |
| Go / Rust | SSRF、path traversal、command injection、race conditions、auth bypass |
| API-only（REST/GraphQL） | IDOR、auth bypass、mass assignment、rate limiting、GraphQL introspection、BOLA |
| WordPress | SQLi（plugins）、file upload、XXE（xmlrpc）、user enumeration、plugin vulns |
| 单页应用（React/Angular/Vue） | DOM XSS、API IDOR、JWT 攻击、CORS 配错、prototype pollution |
| 未知 / 默认 | auth/IDOR、injection（SQLi/XSS）、business logic、info disclosure、CORS/CSRF |

### 每个优先类：

1. 从 memory 选未测高危参数
2. 跑对应测试工具：
   - **SQLi/XSS/SSTI/SSRF/CMDi/LFI：** `auto_probe(session, targets, categories=[category])` 或 `bulk_test(session, vulnerability)`
   - **IDOR/Broken Access Control：** `test_auth_matrix(endpoints, auth_states)` 或 `compare_auth_states`
   - **LFI/Path Traversal：** `test_lfi(session, path, parameter)`
   - **File upload：** `test_file_upload(session, path)`
   - **Open redirect：** `test_open_redirect(session, path, parameter)`
   - **CORS：** `test_cors(session)`
   - **JWT：** `test_jwt(token)`（先从认证流抽 token）
   - **GraphQL：** `test_graphql(session)`
   - **Cloud metadata SSRF：** `test_cloud_metadata(session, parameter, path)`
   - **Race condition：** `test_race_condition(session, request)` 在状态变更端点（支付、coupon、投票）
   - **HPP：** `test_parameter_pollution(session, path, parameter, value, variants)`
   - **Mass assignment：** 注册 / 资料更新请求加额外字段（`role`、`is_admin`、`price`）
   - **CRLF：** 用 `%0d%0a` payload 测 redirect / header 参数
   - **Deserialization：** cookies / 参数里找序列化对象（base64 起始 `rO0AB`、`O:`、`gASV`）
   - **隐藏参数：** 在有趣端点调 `discover_hidden_parameters(session, method, path)`
3. **查到异常——立即验证：**
   - 重发同 payload 确认可复现
   - 查证据要求（见 verify-finding skill）
   - 已确认：`save_target_intel(domain, "findings", finding_data)`
   - 未确认：记 suspected，移下一目标
4. 更新 coverage：`save_target_intel(domain, "coverage", {tests: [...]})`

**每类后 CHECKPOINT：**
- 展示：测了 X 参数、发现 Y 异常、确认 Z
- 问：续下类、pivot 策略、停？

### Token 预算护栏（Rule 19——全覆盖强制）
- **不为省 token 跳类别。** Rule 19 全覆盖强制；失败模式是漏发现，非超支。Token 经济来自 `auto_probe(skip_already_covered=True)`、分页、`discover_attack_surface` 预筛——非跳类。
- **类内 pivot，非跳类。** 标准Payload 被拦，换**何处**注入（headers / cookies / body）、**如何**编码（transform_chain）、**何时**测（race / OOB / blind 变体）。**勿**弃类。
- knowledge_version 变更时重测**所有**类（`skip_already_covered=False`）。
- 某类中冒出 30–49 分发现，**先**查 `chain-findings.md` 找与已存发现的链候选，**再**进 investigate.md。

### Pivot 策略（标准测试失败时）

**换注入位置：**
- 从 query 参数移到 headers（Host、Referer、X-Forwarded-For、X-Forwarded-Host）
- 试 cookies、JSON body keys（不仅 values）、path 段
- 测 multipart/form-data boundary 注入
- 试参数污染（同参同时 query 和 body）

**换目标：**
- 从公开端点切到仅认证端点
- 找 admin panel、debug 端点、API 版本（/api/v2/ vs /api/v1/）
- `discover_hidden_parameters` 找未文档化端点
- 试 legacy / deprecated 端点（常少加护）

**换打法：**
- WAF 拦标准 payload：`get_payloads(category, waf_bypass=True)`
- 编码变体：双 URL 编码、unicode、混合大小写
- 盲变体：time-based 替 error-based、OOB 经 Collaborator
- 链发现：open redirect + SSRF、XSS + CSRF、info disclosure + auth bypass

**想业务逻辑：**
- 价格操纵（负数、零价、coupon 重用）
- 流程绕过（多步流程跳步）
- 限速绕过（一次性动作的 race condition）
- 提权（资料更新改 role / permission 字段）

**挖 JavaScript 求线索：**
- 所有 JS 文件 `extract_js_secrets` 找硬编码 API key、内部 URL
- `analyze_dom` 找 source→sink XSS 流
- JS 里找注释掉的特性、debug flag、staging URL

## Phase 3.5：WebSocket 测试（适用时）

`get_websocket_history` 显示 WebSocket 流量则：

1. 查 Cross-Site WebSocket Hijacking（缺 Origin 校验）
2. 测 WebSocket message 注入（SQLi、JSON message 中的 XSS）
3. 查认证——WebSocket upgrade 是否需要 auth？

## Phase 3.6：深挖（自动触发）

**触发：** 自动——`playbook-router.md` "Deep-dive auto-trigger" 矩阵在 Phase 2 末 AND Phase 3 末评估侦察输出 + intel（`load_target_intel`、`get_business_context`、`get_findings`）。任一信号命中→对应 round 自动触发。无需操作者提示。标准 hunt 拿到约 20% 真 bug；其余活在下面 rounds 里。

五轮。每轮仅在其触发信号命中时跑；各有停战条件。

### Round 1 — 表面（假设 Phase 3 已做）

### Round 2 — 业务逻辑
- `get_business_context(domain)` 必返数据；空则**先**跑 `capture_business_context`
- 载 `playbook-business-logic.md`；走每个 `key_workflow` + `kill_switch` +（low, high）`user_role` 对
- 预期收益：设了 `money_flow` / `kill_switches` 的目标上 3–8 个逻辑发现

### Round 3 — 链式狩猎
- 载 `chain-findings.md`；盘点每个已存发现（status=confirmed AND status=suspected）
- 对每个查升级表；用 `run_flow` 端到端证链
- 链作独立发现存；severity = 最高影响步（见 chain-findings.md）

### Round 4 — 被遗忘的表面

扫描器最常漏的表面。Rounds 2-3 完成后跑：

| 表面 | 探针 |
|---|---|
| Webhooks | replay；strip signature；race-on-delivery；rotate-during-flight |
| Admin / 内部 URL | grep JS + sitemap.xml + robots.txt + swagger.json 找 `/admin`、`/internal`、`/debug`、`/actuator`、`/api/internal/` |
| API 版本 | `/v1/`、`/v2/`、`/api/internal/`、`/api/private/`、`/api/legacy/`——旧版少加护 |
| HTTP 方法篡改 | 每端点经 `resend_with_modification` 测 GET↔POST↔PUT↔DELETE↔PATCH↔OPTIONS↔TRACE |
| 路径归一化 | `/Admin` vs `/admin`、`/admin/..;/`、`/admin%2f`、尾点 / 斜杠、双编码 |
| 子域接管 | `query_crtsh` 每子域调 `test_subdomain_takeover` |
| Sourcemaps | 取 `*.js.map`（browser_crawl 的 Network tab）；重建原始路径 + dev URL |
| CI/CD 工件 | `.github/`、`Jenkinsfile`、`.gitlab-ci.yml`、`.npmrc`、`package-lock.json`、`vendor/`、`composer.lock` |
| 云资产 | S3 bucket list+read、公共 Lambda function URL、暴露的 Firebase、公共 GCS / Azure blob |
| 审近期 | `audit_recent_traffic` 找 proxy history 中但从未测的端点 |
| Beta / staging | `beta.<domain>`、`stage.<domain>`、`dev.<domain>`、`qa.<domain>`、`*-internal.<domain>` |
| 仅移动路径 | `/api/mobile/`、`/m/`、`/api/app/`、`X-Platform` headers——载 `playbook-mobile-backend.md` |

### Round 5 — 跨类元扫

| 类 | Pivot |
|---|---|
| 二阶注入 | 用户存 X；admin / 后台稍后处理（support-ticket XSS、comment-moderation SSRF） |
| Deserialization | cookies / params / JSON values 里每个二进制 blob（`rO0AB`、`O:`、`gASV`、msgpack header） |
| OAuth / SAML / OIDC 混乱 | 流中 3+ IdPs → 跨 IdP code/assertion 交换、account-linking 错乱（载 `playbook-payment-and-auth.md` §1） |
| Recovery 流 | 每个 "forgot X" 路径——password、2FA、passkey、email、phone（载 `playbook-payment-and-auth.md` §10） |
| DNS rebinding | SSRF 标记端点配 DNS-resolver 缓存缝；SVCB/HTTPS records |
| Prototype pollution | 每个 JSON body；递归 `__proto__` / constructor 深度 |
| HTTP smuggling | TE.CL / CL.TE / H2.CL / H2.TE / TE.0 / CL.0 经 `test_request_smuggling` |
| Cache poisoning | `test_cache_poisoning` 在每个 CDN 前置路径；X-Forwarded-Host / X-Original-URL |
| WebSocket smuggling | per-message-deflate 压缩 oracle、frame fragmentation |

### 深挖停战条件
- Rounds 2-5 跨 50 次工具调用且**<2 新发现** → pivot 换目标
- >2h 会话时间无新链 → 收益递减；checkpoint 后移

## Phase 4：Severity——业务影响，非 CVSS

Severity 跟赔付。advisor（`assess_finding`）在 `domain` 传入 AND `capture_business_context(domain)` 跑过时自动套此准则——两者都传。

**公式：** `severity = base_class × business_context_multiplier ± evidence_floor/ceiling`

### 按类基线
| 类 | 基线 |
|---|---|
| Verified RCE / pre-auth 数据外泄 / 无交互 ATO | CRITICAL |
| 1-click ATO、mass PII IDOR、JWT `alg:none`、sandbox-payment-on-prod、OAuth `redirect_uri` 指攻击者、password reset → 攻击者邮箱 | CRITICAL |
| 单用户 IDOR、stored XSS admin 上下文、SSRF 取云凭据、泄露 live AWS/GCP root key | HIGH |
| Reflected XSS、CSRF 状态变更、open redirect 带链、可利用 info disclosure | MEDIUM |
| Self-XSS、缺 headers、verbose errors、版本披露 | LOW |

### 业务上下文乘子（来自 `get_business_context`）
| 特征 | × |
|---|---|
| `money_flow != "none"` AND auth 相关类 | 1.5 |
| `sensitive_data` 含 pci / phi / financial | 1.4 |
| `app_type` 在 {banking, fintech, healthcare, gov} | 1.3 |
| 影响全用户 vs 仅攻击者 | 1.5 |
| Pre-auth 可利用 | 1.3 |
| 需 admin 角色利用 | 0.5 |
| 需受害者社工 | 0.7 |

合并乘子 ≥1.3 升上一档；≤0.7 降一档。

### 下限（永 MAX，忽略乘子）
Sandbox payment token 上 prod、`alg:none` JWT 被接受、password reset → 攻击者供邮箱、mass PII via no-auth 端点、ATO 无任何交互、泄露 AWS/GCP root key 经验证可写 → **最低 CRITICAL**。

### 上限（封顶，忽略加成）
- Reflected XSS 无 admin / 敏感上下文 → 最高 MEDIUM
- 单独 open redirect → 最高 MEDIUM
- Self-XSS 无链 → INFO（Rule 17 NEVER SUBMIT）
- 缺安全 headers / verbose errors 单独 → INFO（Rule 17）

### 预期赔付（BBH 务实）
| 档 | 公开均价 | 每交战目标 |
|---|---|---|
| CRITICAL | $5k–$50k | 0–1 |
| HIGH | $1k–$5k | 1–3 |
| MEDIUM | $200–$1k | 2–5 |
| LOW | $50–$300 | 仅链可报才报 |
| INFO | $0 | 不提交 |

准则判 HIGH 但项目历史对该类付 LOW，用 `save_finding` 的 `severity=` override 降档并注 `program-pays-low`。勿虚报——triager 会降，声誉损失大于奖金。

## Phase 5：总结

1. 展示已确认发现含 severity 和证据
2. 展示覆盖统计（测端点 %、按类）
3. 存 notes 含观察和下会话优先级：
   ```
   save_target_notes(domain, "# Target Notes: {domain}\n\n## Observations\n...\n\n## Next Session\n...")
   ```
4. 建议下会话测什么
