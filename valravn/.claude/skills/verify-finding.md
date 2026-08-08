---
name: verify-finding
description: 用可复现证据验证疑似漏洞为真，方标 confirmed。三步按序——Logger replay → assess_finding → save_finding。跳任一步导致服务端拒或报告假阳性。
---

# 验证发现

> **规则引用（R12）：** 本 skill 中规则不复述。下文流水线引用 `hunting.md` Rule 10（save-finding 流水线）、Rule 11（基线）、Rule 13（已验证证据）。本 skill 文本与规则编号冲突时，规则胜。

```
Step 0: Logger replay  →  Step 1: assess_finding  →  Step 2: save_finding
证可复现                  证可报告                   持久化
```

跳 Step 0 → 服务端 400 硬拒（无可解析 evidence index）。
跳 Step 1 → 为下游 7 问门会 fail 的发现白费 token 起草报告。

## SMART MOVE — 首调

按手中之物路由：
- 可疑请求的 logger_index → `resend_with_modification(index)`（直接 Step 0）
- 前会话的 finding_id → `load_target_intel(domain, "findings")` 再 resend 其中记录的基线 index
- 仅 raw curl 输出，无 Logger 条目 → **先**经 `curl_request` 重发穿 Burp（R26a）让 logger_index 存在，再 Step 0
- timing/blind 类（sqli_blind / sqli_time / ssrf_blind / race / smuggling / xxe_blind）→ Step 1 前需 3 次 replay，捕获 `{logger_index, elapsed_ms, status_code}`
- 无对基线异常 delta（R11）→ 停，标 `likely_false_positive`；**勿**调 `save_finding`

## Step 0 — Logger Replay（强制）

1. 经 `get_proxy_history`（带过滤）或 `search_history` 识别可疑请求。记其 index。
2. `resend_with_modification(index)` — 确认同异常（status、body delta、error string）。
3. **确认 replay** 的 Logger index 进 `evidence.logger_index`。
4. **Timing/blind 类**（`sqli_blind`、`sqli_time`、`ssrf_blind`、`race_condition`、`request_smuggling`、`ssti_blind`、`command_injection_blind`、`xxe_blind`）：确认后再 replay 2 次。每次捕获 `{logger_index, elapsed_ms, status_code}` → 成为 `reproductions[]`。
5. 第二发异常不复现 → 标 `likely_false_positive` 并停。**勿**调 `save_finding`。

服务端对无 `evidence.logger_index` / `proxy_history_index` / `collaborator_interaction_id` 的 `save_finding` 调用硬拒。

## Step 1 — assess_finding（强制）

```
assess_finding(
  vuln_type="<class>",          # "sqli", "xss", "idor", ...
  evidence="<what you saw>",    # include "3/3" for timing
  endpoint="<full URL>",
  parameter="<name>",
  domain="<domain>",            # required: enables Q1 scope + Q4 dup
)
```

Verdict → 行动：

| Verdict | 行动 |
|---|---|
| `REPORT` | 进 Step 2，传 `confidence=<suggested>` |
| `NEEDS MORE EVIDENCE` | 强化标记项，重做 Step 0，再 assess |
| `DO NOT REPORT` | 标 `likely_false_positive`，移下一目标 |

不调 `assess_finding` 直接 `save_finding` 违 Rule 25。

## Step 2 — save_finding

直传建议的 confidence。Burp 门仅在 Step 0 index 错时拒。

## 高效证据提取

用专用工具，非整响应：
- `extract_regex(index, '<proof_pattern>', group=1)` — 仅证据
- `extract_json_path(index, '$.error')` — JSON 字段
- `get_response_hash(index)` — 比对 hash 验一致性
- `extract_headers(index, ['Set-Cookie', 'Location'])` — 仅 headers

## 捷径：`confirm_*` exploit-confirmation 工具

5 个高频类有审计过的一击工具，跑标准证明，返 VerdictResult dict，给你可直接进 `save_finding` 的 `logger_index`。**手动造 payload 前先用这些**——与 `confirm_*` 家族共享破坏性 payload denylist，永不外泄真实数据。

| 类 | 工具 | 证明方式 |
|---|---|---|
| SQLi | `confirm_sqli(endpoint, parameter, dbms, strategy)` | Marker reflection（union/error）或 5s timing（time strategy） |
| SSTI | `confirm_ssti(endpoint, parameter, engine='')` | Engine 数学表达式反射（jinja2/freemarker/twig/...） |
| SSRF | `confirm_ssrf(endpoint, parameter)` | 轮询窗口内 Collaborator HTTP/DNS 回调 |
| XXE | `confirm_xxe(endpoint, mode='inband')` | 取出 `/etc/hostname` 内容（inband）或 Collaborator 回调（oob） |
| RCE | `confirm_rce(endpoint, parameter, command='id')` | 取出 `M-<token>-START`/`-END` 括号间输出 |

返 `{verdict, confidence, evidence_summary, logger_indices[], collaborator_interactions[], details, human_summary}`。直接管道：
`save_finding(..., evidence=to_assess_evidence(verdict_dict))`。

verdict 即规范证据——这些 CONFIRM 时无需手动 `resend_with_modification` 循环。timing/blind 变体仍需 Step 0 reproductions。

## 证据要求（按漏洞类）

每类有 SPECIFIC 门槛。不满足，发现**非** confirmed。

### SQL Injection
- **Time-based：** 响应时间 > 基线 3×；replay 3 次；对比基线
- **Error-based：** SQL error string（unclosed quote、ORA-、mysql_fetch、pg_query、ODBC、OLE DB）
- **Union：** UNION SELECT 注入数据可见（version、表名、distinct column count）
- **Boolean blind：** AND 1=1 与 AND 1=2 间一致 content delta
- **Blind OOB：** 经 `auto_collaborator_test` 的 Collaborator DNS/HTTP
- **不足：** 仅 status code、通用错误页、无 error string 或 boolean 稳定性的 length

### XSS
- **Reflected：** payload UNENCODED 进 body 在可执行上下文
- **Stored：** payload 提交后出现在**不同**页面
- **DOM：** payload 到 innerHTML/eval——经 `analyze_dom` source→sink 验
- **不足：** URL 编码 / HTML 编码反射、payload 在 JS 字符串里正确转义、在 HTML 注释里

### SSRF
- **已确认：** 经 `auto_collaborator_test` 或 `get_collaborator_interactions` 的 Collaborator HTTP/DNS
- **已确认：** cloud-metadata 内容（ami-id、AccessKeyId、subscriptionId）
- **部分：** 内部服务响应（内部 IP、Redis/SSH banner）
- **不足：** 仅 status、无 Collaborator 的 timeout、connection-refused

### Open Redirect
- **已确认：** 经 `test_open_redirect` 的 Collaborator 交互
- **部分：** Location header 指攻击者控外部 URL（单独 LOW）
- **不足：** 参数在 body 反射但无 redirect headers

### LFI / Path Traversal
- **Linux：** `root:x:`、`daemon:`、`/bin/bash`、`/bin/sh`、`nobody:`
- **Windows：** `[fonts]`、`[extensions]`、`for 16-bit app`、`[mail]`
- **PHP wrappers：** base64 内容（PD9...、PCFE...）
- **源代码：** PHP / config 可见
- **不足：** 不同 error、status 变化、"file not found"、无文件内容的 length 异常

### IDOR
- **已确认：** `compare_auth_states` 显示 >90% 相似度用**不同**用户凭据
- **已确认：** 用低权凭据读他人 PII
- **已确认：** 经他人 ID 修改 / 删除他人资源
- **已确认（ID 枚举 / BOLA / BFLA）：** 顺序 / 可预测 ID 空间（auto-increment、UUIDv1 monotonic、base32 timestamp）遍历得跨用户数据。按 Rule 6 此为授权测试，**非**凭据爆破。门槛：(a) 演示模式（如 ID 1001→1010 各返不同用户记录），(b) 确认至少 2–3 个 ID 返他人数据，(c) PoC 计数封顶避 mass exfil 按 Rule 7。证据标 "sequential"、"predictable"、"id enumeration"、"cross-app"、"uuidv1" 让 `assess_finding` 加权影响。
- **已确认（BFLA——功能级）：** 低权角色可调 admin / 内部函数（如 `user` 角色调 `/api/admin/users/delete` 成功）。与对象级 IDOR 不同。
- **不足：** 同 status 但内容完全不同、admin 端点返 200 带 "access denied" body、顺序 ID 但未验证不同用户内容

### File Upload
- **已确认：** 上传文件 URL 可访问 AND 服务端处理它（PHP exec、SVG XSS、JSP exec）
- **部分：** 上传接受（200）但位置未知——仅危险扩展名接受才可报
- **不足：** 上传被拒但 200 + body error、文件以安全扩展名存储

### SSTI
- **已确认：** 数学 eval（7*7→49）——验非客户端（Angular）通过查 pre-JS 响应
- **已确认：** RCE 探针返系统输出（uid=、hostname、whoami）
- **已确认：** config/env 泄露（SECRET_KEY、DB 凭据）
- **引擎区分：** `7*'7'` = "7777777" → Jinja2；"49" → Twig
- **不足：** 字面表达式反射、客户端模板、JS 上下文表达式

### Command Injection
- **已确认：** 唯一 marker 回显（`; echo UNIQUE_STRING` → 响应中 UNIQUE_STRING）
- **已确认：** 系统输出（uid=、whoami、hostname）
- **Time-based：** `; sleep 5` 致 5s+ 延迟（3 次 replay，对比基线）
- **Blind OOB：** 经 `; curl COLLAB` 或 `; nslookup COLLAB` 的 Collaborator
- **不足：** 仅 status 500、不同 error、无 timing 相关的 timeout

### CSRF
- **已确认：** 无 CSRF token 即状态变更（全删；动作执行）
- **已确认：** 动作用**不同**用户 session 的 token 成功
- **已确认：** POST→GET method-override 绕过
- **影响：** 动作必含真实影响（password、funds、settings）
- **不足：** GET 上缺 CSRF、无副作用、存在 SameSite

### Race Condition
- **已确认：** `test_race_condition` 显示动作执行**多**于预期（coupon 3×、余额扣 3×）
- **已确认：** 同时相同请求致重复记录
- **量化：** 多少钱 / 信用获益，反复
- **不足：** 多个 200（服务可能返 200 但只处理一次）、<30% 成功率（timing，非 TOCTOU）

### JWT
- **alg:none：** 服务端接受 `alg=none` + 空签名 JWT
- **弱 secret：** 用常见 secret 重签被接受（secret、password、123456）
- **算法混乱：** RS256→HS256，用公钥签，被接受
- **kid 注入：** `kid` 中 path traversal/SQLi 返不同数据
- **不足：** 解码 JWT（预期）、200 但忽略 JWT

### CORS
- **已确认：** 服务端在 ACAO 反射任意 Origin 且 ACAC: true
- **已确认：** 服务端接受 Origin: null 带 credentials
- **部分：** Origin 反射但无 credentials（LOW——仅读公开数据）
- **不足：** 通配 ACAO 但无 credentials（浏览器阻凭据）

### Mass Assignment
- **已确认：** `role=admin` / `is_admin=true` 实改权限（事后查 profile 验）
- **已确认：** `price=0` / `discount=100` 实改金额
- **已确认：** `verified=true` 绕邮箱验证
- **不足：** 服务端接受额外字段但不用

### CRLF
- **已确认：** 响应 headers 中注入 header（X-Injected: true 可见）
- **已确认：** Set-Cookie 注入在受害者设 cookie
- **已确认：** response splitting——双 CRLF 后 body 内容
- **不足：** CRLF 在 body 反射（非 headers）、Location value 中 URL 编码 CRLF

### HPP
- **已确认：** 重复参数致不同行为（status、content、返数据）
- **已确认：** 后端用与前端不同 param 实例（WAF/auth 绕过）
- **不足：** 服务端一致用 first/last、length 差 <10%

### Deserialization
- **已确认：** 构造序列化对象 → 服务端错含反序列化栈（ObjectInputStream、unserialize、Marshal.load）
- **已确认：** 经 gadget chain 的 RCE（响应中输出、Collaborator 回调）
- **已确认：** 深嵌套对象致 DoS（可量退化）
- **不足：** 参数中 base64（可能未反序列化）、输入被接受无错

### GraphQL
- **Introspection：** `__schema` 返完整 schema
- **Injection：** resolver 参数致 SQL error
- **Batch abuse：** 无界 batch 被处理（DoS 向量）
- **Persisted-query 攻击：** APQ hash 冲突被接受、hash preimage 攻击者控、或旧 hash 永久缓存
- **Alias 放大：** 同字段 1000+ alias 被接受（DoS 级）
- **不足：** 端点存在、field-suggestion 开（低信息泄露）

### Password Reset
- **已确认（token 重用）：** 同 reset 链成功 2+ 次——token 首用后未失效
- **已确认（跨账户）：** 为受害者发的 token 在攻击者账户有效或反之（account_id 参数操纵）
- **已确认（race）：** 同时 reset 请求发不同 token 均有效（TOCTOU）
- **已确认（弱 token）：** 顺序 / 可预测 / 时间相关 reset token（epoch ms、顺序 int）
- **已确认（host header poison）：** `Host: attacker.com` 致 reset 链指攻击者主机
- **已确认（auth 无限速）：** 此处 rate-limit-missing **可报**（敏感端点）
- **不足：** 仅弱密码策略、signup 无邮箱确认

### API Key / Token 泄露
- **已确认：** key 见于 JS bundle、source map、error 响应或 git 历史 AND key 是 live（对 API 验）
- **已确认：** token scope 越权（如只读 key 允许写）
- **已确认：** 内部 key 经 debug header / verbose error 暴露
- **Severity 缩放：** CRITICAL 若 AWS/GCP root key；HIGH 若第三方带计费影响（Stripe、SendGrid）；MEDIUM 若 scoped/limited
- **不足：** dummy / sample / 注释掉的 key、无权限的 key

### Auth Bypass（链或独立）
- **已确认（session fixation）：** 服务端接受认证**前**设的 session ID；受害者 session 被攻击者控
- **已确认（forced browsing / direct object access）：** `/admin/dashboard` 无有效 session 或低权角色可访问
- **已确认（HTTP method bypass）：** `GET /api/users/delete/1` 在仅 POST 应可行时有效
- **已确认（路径归一化绕过）：** `/admin/..;/dashboard`、`/admin%2f`、`/admin/.`、`/admin/%2e/`
- **已确认（header bypass）：** `X-Original-URL`、`X-Rewrite-URL`、`X-Forwarded-For: 127.0.0.1` 获 admin 访问
- **已确认（referer-based auth）：** 改 Referer 为内部 URL 获访问
- **不足：** login 缺 rate limit（独立发现）、缺 CAPTCHA

## 决策

### VERIFIED → 带全证据存

```
save_target_intel(domain, "findings", {
  "endpoint": "...", "vulnerability_type": "...", "parameter": "...",
  "status": "confirmed", "severity": "CRITICAL/HIGH/MEDIUM/LOW",
  "evidence": {payload, baseline, exploit, collaborator_proof},
  "poc_request": {method, path, headers, body, expected_behavior},
  "impact": "...",
  "last_verified": "<ts>", "verification_failures": 0
})
```

### NOT VERIFIED → 更新状态

- **首次失败：** `stale`（间歇或被 patch）
- **二次失败（verification_failures >= 2）：** `likely_false_positive`
- 记录变化（不同响应、被 patch、WAF 拦）

**硬删现在是 save 时，非 report 时。**

- `save_finding(status='likely_false_positive', ...)` 不再持久化。若
  匹配记录存在且 confidence < 0.6，从 `.valravn-intel/<domain>/findings.json`
  AND Burp 内存 store **硬删**。confidence ≥ 0.6 须经显式工具。
- `mark_finding_false_positive(finding_id, domain, ...)` 是显式删
  路径。Confidence 分层审查：
  - **conf < 0.6** → 立即删，无提示。
  - **0.6 ≤ conf < 0.8** → 返全证据转储并要求操作者以
    `confirmed_by_user=True, reason='<why>'` 重调。
  - **conf ≥ 0.8** → 拒除非 `force=True` + `reason`——看着像真
    发现；操作者须显式 override。
- `generate_report` 仍清除残留 `likely_false_positive` 条目作最终安全网。

## 按业务上下文定 severity

上面各类证据门槛是门（无证据 = 无发现）。档位由 `hunt.md` Phase 4 准则定：

```
severity = base_class × business_context_multiplier ± floor/ceiling
```

传 `domain` AND 确保 `capture_business_context(domain)` 跑过——`assess_finding` 读两者并套乘子。各类档位提示：

- **RCE / SQLi / SSTI / deserialization / command injection** —— 基线 CRITICAL；下限保 CRITICAL 跨大多上下文。仅在真公开只读数据时降档。
- **IDOR / BOLA / BFLA** —— 基线 HIGH。CRITICAL 当 (a) mass-enumerable PII，(b) `money_flow != none` 且资源是 payment/account，(c) admin 函数以 user 可达。
- **XSS** —— Reflected MEDIUM 上限除非 admin / 敏感上下文。Stored MEDIUM→HIGH 按观看者权限。Self-XSS INFO（Rule 17）。
- **CSRF** —— MEDIUM 除非状态变更触 `kill_switch`（delete_account、transfer_funds、role_change）→ HIGH/CRITICAL。
- **JWT / OAuth / FIDO** —— `alg:none`、`redirect_uri` 指攻击者、passkey 删无 re-auth = CRITICAL 下限。单缺 PKCE = MEDIUM（链到 code-theft 升档）。
- **Payment 流** —— 跨订单 token replay / sandbox-on-prod / tokenize 后金额篡改 = CRITICAL 下限。3DS 绕 = HIGH 除非单笔。
- **API key 泄露** —— Live AWS root / GCP service-account write = CRITICAL。Scoped 第三方（Stripe restricted、SendGrid send-only）= HIGH。只读公开数据 key = LOW-MEDIUM。
- **Open redirect / clickjacking / verbose errors / missing rate-limit** —— 单独 INFO/LOW（Rule 17 NEVER SUBMIT）；仅链时可报（见 `chain-findings.md`）。
- **Mobile pinning / root-JB 检测绕过** —— 单独 INFO（加固，非漏洞）；仅链到后端 bug 时可报（`playbook-mobile-dynamic.md`）。

## 交叉引用

- **7 问门 + NEVER SUBMIT：** `.claude/rules/hunting.md`（永远加载）
- **Severity 准则（全）：** `hunt.md` Phase 4
- **条件有效 + 链：** `chain-findings.md`
- **费力 vs 噪声调用：** `noise-budget.md`

## 铁律

- 不达上面证据要求绝不 confirm
- 绝不跳 Step 0 replay
- 绝不信单请求 timing（jitter——replay 3 次，对比基线）
- 无访问**他人**数据绝不报 IDOR
- payload 不在可执行上下文（非编码、非注释）绝不报 XSS
