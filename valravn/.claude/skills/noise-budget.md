---
name: noise-budget
description: 不漏真 bug 同时削浪费 token。区分 IMPOSSIBLE 工作（技术不匹配 CVE、编码反射、WAF 反弹）与 EXPENSIVE-BUT-REAL 覆盖（框架级模式、race condition、深 auth 矩阵）——跳前者、全覆盖后者。用于异常看着边界 OR 决定是否继续探未产出的类时。
---

# Noise Budget —— 跳不可能的工作，永不跳真覆盖

目标是**覆盖，非速度**。此工具存在让猎手不漏真 bug。Token 自由花于任何可能是真发现之物——即便贵。被削的是**构造上不可能**（错技术栈、编码击败、scope 外）和**显证噪声**（3/3 验证失败、知识库被自适应 matcher 清）的工作。

## 两列表

| SKIP —— 不可能 / 错表面 | COVER —— 贵但真 |
|---|---|
| Laravel/Node/Java 站上的 PHP 特定 CVE | React `dangerouslySetInnerHTML` 跨整 SPA——测每渲染用户数据的页 |
| 非 WordPress 目标上的 WordPress 插件 RCE | Node/Express 栈上的 Prototype pollution——每 merge 用户 JSON 的端点 |
| Linux 容器上的 Windows 风 LFI（`C:\boot.ini`） | JWT 算法混乱 / `alg:none`——每消费 token 的端点 |
| Python app 上的 `.NET ViewState` 反序列化 | Java 反序列化 gadget——每接序列化对象的端点 |
| Go/Rust 服务上的 `phpinfo` 枚举 | Mass assignment——每 PUT/PATCH/POST 更新用户拥记录 |
| Spring Boot 上的 ASP.NET 特定 `__VIEWSTATE` | IDOR 矩阵——每认证端点、两 auth 状态 |
| Rails app 上的 `wp-config.php` 发现 | 状态变更端点上的 race condition（coupon、balance、vote、ticket） |
| payload 语法在检测的 DB 引擎上不解析（MySQL 上的 Oracle-only） | SSRF Collaborator 探针——每接 URL/host/IP 的端点 |
| `match_tech_stack` 标的技术不匹配 nuclei 模板 | 每状态变更端点上的 CSRF，带 method-override 变体 |
| 对基线无 body delta 的 410-Gone 或 404 端点的探针 | Open redirect 链 token 盗窃——每 redirect 风 param |

**分割规则：** 若漏洞类在检测的技术栈和参数形状下可能存在，它获全覆盖即便单探针贵。若漏洞类**不能**存在（错运行时、错 DB、错框架），从计划中整移。

## 预探针：除不可能

在任何 payload 上花 token 前：

1. **知栈。** `detect_tech_stack(index)` 或读 `load_target_intel(domain, "profile")`。无栈，你分不出不可能与贵。
2. **丢不兼容的 CVE/探针类。** `match_tech_stack` 已做此——信之。勿在 Laravel 站上手重测 PHP CVE。
3. **翻译，非丢。** 看似框架特定的漏洞类常翻译：SQLi 在每 SQL 后端用对语法可用；SSRF 与语言无关；mass assignment 在每 auto-bind JSON 的框架存在。把 payload 翻译到检测的栈，勿跳类。

## 框架级模式 —— 强制全覆盖

当检测的技术栈暗示一类 bug 时，每适用表面**必**测。例：

- **检测到 React：** 每渲染服务端供内容的组件路径 → 经 `dangerouslySetInnerHTML`、`innerHTML`、JSX 表达式注入的 DOM XSS。用 `analyze_dom` 枚举 sink；遍每页。
- **检测到 Node/Express：** 每 JSON merge、每 spread、每从 request body 的 `Object.assign` → prototype pollution。测每接 body 的端点。
- **检测到 Spring Boot / Java：** 每接序列化对象或 XML 的端点 → 反序列化 + XXE。探每 binary/XML 接受端点。
- **检测到 GraphQL：** introspection + 每 mutation 参数 → 注入、batching 滥用、alias 过载。
- **JWT 在 Authorization header：** `alg:none`、弱 HMAC secret、RS→HS 混乱、kid path traversal → 测每 token 承载端点。
- **多角色 auth（admin/user/guest）：** 每认证端点上 test_auth_matrix，两方向。

此处花 token 做完整扫正确。因 "已找到一个" 早停漏 80% 真 bug。

## 探针耗尽 —— 推理，非计数

丢老的 "10 探针后弃" 启发。用这些信号，按序：

| 信号 | 行动 |
|---|---|
| `auto_probe` 知识库 matcher 在优先类上清 AND 技术栈匹配 matcher 的运行时 | 类对框架真不太可能——移下类。仅当 param 名强暗示（`?cmd=`、`?file=`、`?next=`）时手造一变体点测。 |
| 同 payload 经 3+ 探针反射编码（同 WAF、同编码器） | **勿**弃类——换技术：`craft-payload.md` 做编码绕过、变换链、替代注入点（header/cookie/path/Content-Type）、或 WAF 不滤的不同漏洞类。 |
| 来自 WAF 的 2+ 连续 403/406/429 | 慢（`delay_ms`）、轮换 session/origin、或换技术。**勿**跳类——WAF 存在常信号开发者认为此类攻值得阻，是 tell。 |
| 干净和探针输入同响应 hash | Cache、debug 围栏、或只读端点。移不同端点，非不同类。 |
| 3/3 replay 产不一致 timing（方差 > 中位数 30%） | Timing 声称是 jitter，非 bug。**勿**存。续其他类。 |
| 同 (endpoint, parameter, vuln class) 上探针序列超 ~30 探针且全程 c<0.30 | 在 `coverage.json` 记录阴性结果并 pivot。结果**是**覆盖——记之让下会话不重做。 |

## 费力有据类 —— 花预算

这些漏洞类烧多 token AND 多请求，但是真赏金项目中付费最高的 bug。**永不**因预算跳这些：

- **Race condition**（经 `test_race_condition` 的 10–50 并发请求）——coupon/balance/vote/role-grant
- **IDOR auth 矩阵**（经 `test_auth_matrix` 的每端点 × 每 auth 状态）
- **反序列化**（每接序列化数据端点的 gadget chain 探针）
- **Request smuggling**（每 upstream/CDN 组合的 CL.TE / TE.CL / TE.TE）
- **Cache poisoning**（每 cache-key 排列的 header 注入）
- **Mass assignment**（每可写字段、每权限 param、每 PUT/PATCH）
- **业务逻辑链**（经 `run_flow` 的多步流——replay、操纵中间状态）

若你的 hunt 会话未触这些，未完。此处花 token 正确；假装预算花在这些上是真浪费。

## 怀疑验证 —— 每怀疑封顶，非每类

不同于类预算——一旦**特定**怀疑上桌：

- **可复现性：** 经 `resend_with_modification` 的 3 replay。不一致（< 3/3）→ 噪声。停此怀疑（非类）。
- **Timing/blind：** 3 replay——每记 `elapsed_ms`。方差 > 中位数 30% → jitter。停此怀疑。
- **Boolean blind：** 2 payload（TRUE/FALSE 变体）。无稳定 delta → 停此怀疑。

失败怀疑**不**关漏洞类。续测同类中其他 param/端点。

## 阴性也记的内容

每类扫——即便 0 发现——应经 `save_target_intel(domain, "coverage", {...})` 写 `coverage.json`。此即让下会话不重做你跑过的 30 探针。阴性结果**是**覆盖；仅静默停才是浪费。

## 此 skill 不授权什么

- 因 "站有 React" 跳 JWT 测试——JWT 是端点侧、框架无关
- 因 "已找到 XSS" 跳 mass assignment——不同类、不同赔付
- 因 "可能需 100 探针" 跳反序列化——那正是真 critical 所需时间
- 因 "版本未披露" 跳 CVE 匹配——CVE 可能按行为适用，非按 banner
- 在一类中首个发现即停——先全扫、存发现、再移

## 交叉引用

- **强怀疑失败时做什么：** `verify-finding.md` Step 0 / Step 1
- **payload 被滤时做什么：** `craft-payload.md`
- **何处 pivot：** `get_next_action(target_url, completed_phases, findings_count, tech_stack)`
- **技术栈 CVE 过滤器：** `match_tech_stack` —— 自动丢不兼容 CVE
- **这些限背后的硬规则：** `.claude/rules/hunting.md` Rule 14、17、25、26、27、28
