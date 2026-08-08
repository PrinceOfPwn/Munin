---
description: 漏洞赏金狩猎永久铁律。每次调用 Burp Suite MCP 工具时强制适用。
globs:
---

# 狩猎铁律

永久生效，覆盖任何冲突行为。每条规则只管一件事。读完所有规则再判断是否重叠。

## 层级（R11/R16）

- **HARD（1–10）**：scope、安全、save-finding 流水线。永远适用。工具层也强制——Valravn 不会让你静默绕过。
- **DEFAULT（11–21）**：证据、覆盖、持久化。每次交战适用。用 `assess_finding` / `save_finding` 的显式 `overrides=[...]` + 审计理由覆盖。
- **ADVISORY（22–32）**：工具选择、可见性、模式心法、输出节俭。会话开始读一次；按需查阅 skill 文件。

层文本与 skill 文本冲突时，规则编号胜出。Skill 文件按编号引用规则——不复述。

## Scope（1–4）— HARD

1. **绝不向 scope 外域名发包。** 新域名发包前必调 `check_scope(url)`。不在 scope，停。
   **1a. 交战模式（默认 operator）：**
   - `configure_scope(mode='operator')` — 默认。scope 外请求追加到 `.valravn-intel/_audit.log`（JSONL）后继续。信任模型：操作者拥有 scope 授权（私约 / SOW）。
   - `configure_scope(mode='strict')` — Rule 1 硬阻断。用于公开赏金项目。
   - 安全规则 5–9 任何模式下都 HARD。`confirm_*` 破坏性 denylist 不放松。
2. **绝不跟随 redirect 到 scope 外域名。** 记下 redirect，不跟随。
3. **尊重排除路径**（`/logout`、`/delete-account` 等按项目策略）。
4. **scope 存疑就问。** 不臆断子域或 API 在 scope 内。

## 安全（5–9）— HARD

5. **绝不发破坏性 payload**（`DROP TABLE`、`rm -rf`、`shutdown`、`format`、`DELETE FROM`、`TRUNCATE`）。用良性检测 payload（SLEEP、数学表达式、Collaborator 回调）。
6. **绝不暴力破解凭据。** 默认/常见凭据（admin:admin、test:test）可以。字典攻击不行。**Scope 澄清：** 本规则仅覆盖凭据/认证爆破。ID 枚举（顺序/可预测 ID、fuzz `user_id` / `order_id` / `account_id` 范围、走 UUIDv1 单调空间）是**授权测试——IDOR/BOLA**，不是凭据爆破，在 scope 内。可预测 ID 范围泄露 PII 或跨应用数据是 HIGH 影响；不要为省 token 塞进 Rule 6。用 `vuln_type='idor'`（或 `id_enumeration`）保存，evidence 含 "sequential"/"predictable"/"enumeration" 让 `assess_finding` 加权影响。
7. **绝不外泄真实用户数据。** SQLi PoC = `SELECT version()` / `SELECT current_user()`，不是 `SELECT * FROM users`。
8. **绝不修改或删除他人数据。** 用 READ 访问证明 IDOR，不是 WRITE。
9. **盲测优先用 Collaborator**，而非可见副作用的 payload。
9a. **OOB 测试必用 Burp Collaborator 或操作者提供的回调域。** 两种情况：
    - **OOB 数据外泄**（盲 SQLi via DNS、盲 XXE、盲 SSRF、盲 SSTI、盲命令注入）：调 `generate_collaborator_payload()` 拿真实 Collaborator 子域，注入 payload。Collaborator 不可用（Community Edition）时问操作者："提供你的 OOB 回调 URL（interact.sh、webhook.site 或类似）"。绝不捏造回调域。
    - **Redirect/reflection 测试**（open redirect、OAuth redirect_uri、CORS origin、SSRF filter bypass）：用 `evil.com` 作占位目标可接受——测的是应用是否 redirect/reflect 到外部域，不是回调是否收到。
    - **绝不**硬编码自有域或真实攻击者域。知识库 payload 用 `COLLABORATOR` 占位——运行时必替换为真实 Collaborator URL。

## Save-Finding 流水线（10）— HARD，唯一规范规则

10. **`save_finding` 须按序三阶段：**
    - **a) Replay（`verify-finding.md` Step 0）：** 取候选 Logger/Proxy 条目，`resend_with_modification(index)` 确认异常持续。**确认 replay**（非原始怀疑）的 Logger index 进 `evidence.logger_index`。timing/blind 类（`*_blind`、`sqli_time`、`race_condition`、`request_smuggling`）再 replay 2 次——每次捕获 `{logger_index, elapsed_ms, status_code}` → `reproductions[]`（≥3 条）。
    - **b) Assess（`assess_finding`）：** `save_finding` 前调 `assess_finding(vuln_type, evidence, endpoint, parameter, domain)`。判 `DO NOT REPORT` 或 `NEEDS MORE EVIDENCE` → 不存。Advisor 处理 scope、重复、NEVER-SUBMIT、弱证据、triager 群发检查。
    - **c) Save：** `save_finding` 的 `evidence` 至少含 `logger_index` / `proxy_history_index` / `collaborator_interaction_id` 之一（每个须能在活跃 Burp 数据中解析）。NEVER-SUBMIT vuln_types 须供 `chain_with[]`。服务端硬拒违规，返 400。

## 证据（11–13）— DEFAULT

11. **永远对照已记录基线。** 探针序列前捕获干净请求的 `{status, length, response_hash}`。异常声明 = 相对基线的 delta（"500 vs 基线 200，len delta +1842，error 'pg_query'"），非绝对观察。无基线，证据不可证伪。
12. **先存证据再深挖。** 一发现有趣（Rule 18）立即 Annotate + Organize。目标会被 patch。
13. **验证证据 > 理论。** 堆栈跟踪 / 解析错误 / 状态变化是线索，非证明。匹配 `verify-finding.md` 的按类证据门槛（如 XSS 需 payload 在可执行上下文，非仅 reflection）。

## 报告（14–17）— DEFAULT

14. **绝不虚报 severity。** 反射 XSS 不是 CRITICAL。信息泄露不是 HIGH。单独 open redirect 不是 MEDIUM。诚实封顶。
15. **绝不提交需要荒谬受害者操作**（"用户往 devtools 粘贴 500 字符 payload"）的发现。Self-XSS、受害者侧 DoS 等过不了此门。
16. **报告只含真阳性。** 删假阳性，不跟踪。`generate_report` 只包含 `status='confirmed'` 发现，并硬删 `.valravn-intel/<domain>/findings.json` 中的 `likely_false_positive` 条目（无墓碑、无移除 FP 列表、无审计尾）。跟踪死发现每会话重载，永远烧 token。
16a. **最终报告禁虚荣指标。** 报告 = 已确认发现 + 其影响——永不是活动计数。不写"测了 22 次"、"跑了 33 用例"、"发了 500 payload"、"扫描 N 端点"、覆盖率百分比、请求计数。那些测努力不测风险，对 triager 或客户读着像注水。按专业渗透/红队交付物写：**执行摘要**（业务影响框架、风险姿态——无计数）→ **按发现技术细节**（标题、severity + CVSS vector、受影响端点/参数、描述、真实影响、复现步骤、证据：请求/响应 + 截图 + PoC、修复）→ **修复指引**。复现计数活在 `evidence.reproductions[]` 供内部验证（Rule 10a），永不进客户报告。红队报告是到 objective 的 kill chain 叙事，非行动日志。
17. **NEVER SUBMIT 列表**（仅 informative，见下表）只有在与另一发现 CHAIN 出真实影响（`chain_with[]`）时才可报告。

## 覆盖策略（18–21）— DEFAULT

18. **边干边 Annotate + Organize。** 每个有趣的已捕获请求 `annotate_request(index, color='RED|ORANGE|YELLOW|GREEN|CYAN|BLUE|PINK|MAGENTA|GRAY', comment='<f-id> | <vuln> | <evidence>')` AND `send_to_organizer(index)`。颜色约定：RED=确认 crit/high，ORANGE=强怀疑，YELLOW=异常，GREEN=基线/通过，CYAN=链候选，GRAY=噪声。不做这些，报告时得重搜整段历史。
19. **默认全覆盖。** 每个用户可控参数 × 每个可达端点 × 每个适用漏洞类。仅在三者全满足时跳过：(a) 该类对该 stack 不可能（如 Laravel 上的 PHP CVE、Linux 上的 Windows LFI），(b) 知识库 matchers 已清且 param-name 信号对**该** (endpoint, param, class) 元组缺席，(c) `coverage.json` 在当前 `knowledge_version` 下记录了同元组的文档化阴性。知识更新时重测。无"省 token 跳类"路径——那是漏发现的失败模式。Token 经济：`auto_probe(skip_already_covered=True)` 防冗余；`load_target_intel` 分页保召回廉价；`discover_attack_surface` 中等成本且预筛——这些是杠杆，不是跳覆盖。
20. **测试前查覆盖。** 不重测本会话已覆盖参数。`load_target_intel(domain, "coverage")`。
20a. **会话起始 recon gate。** 目标域名可识别时的第一动作：调 `load_target_intel(domain, "all")` AND `check_target_freshness(domain, session)`。用返回的 profile（tech stack、auth model、scope 规则）和 findings 列表作主上下文——不重发现。跳此 gate 是重复工作、漏链、浪费 token 的最常见原因。`.valravn-intel/<domain>/` 为空 = 新目标：跑 recon phase（`browser_crawl` → `full_recon` → `discover_attack_surface`）并 `save_target_intel` 结果后再测。无加载旧 intel 或记录新 recon，不开始测试。
21. **每个 checkpoint 存进度。** 会话结束 → 不重做地 resume。每阶段后 `save_target_intel(domain, ...)`。

## 工具选择（22–25）— ADVISORY

22. **一次智能调用 > 五次啰嗦调用。** `smart_analyze`、`auto_probe`、`run_flow`、`discover_attack_surface` 优于多次单独调用。`extract_regex/json_path/css_selector` 优于 `get_request_detail(full_body=True)`。
23. **取证据优先 captured-first。** `search_history` / `get_proxy_history` / `extract_*` 针对已有索引。别用 `curl_request` 重取已捕获的——captured 请求带真实会话状态。
24. **按意图匹配工具——所有 Burp 表面都在桌上。** 按意图选，不按排名：
    - 一次性微调已捕获请求 → `resend_with_modification(index, modify_*)` 或 `probe_with_diff(index, ...)` 自动 diff
    - Burp UI 内可见迭代 → `send_to_repeater(index, tab_name='<f-id>-<vuln>')` + `repeater_resend`
    - 绑定已捕获基线的批量 → `send_to_intruder_configured`
    - 自定义批量 / 爆破 / 垃圾 / 限速 + 分支解码逻辑 Intruder 表达不了 → `concurrent_requests(requests=[...], concurrency=N)`（并行）或顺序 `curl_request` / `session_request` 循环
    - 竞争条件（服务端 latch）→ `test_race_condition`
    - 多步业务逻辑流 → `run_flow`（线性）或显式 `session_request` 链（分支）
    - 多参数 fuzz + 异常检测 → `fuzz_parameter`
    - 知识驱动漏洞扫描 → `auto_probe`
    - 全新首触 / 全控请求 → `curl_request` / `send_raw_request` / `session_request`
25. **看上去像真实客户端时用真实 header profile；测服务端时用 bare headers。**
    - 真实模式（默认——工具层自动）：`curl_request`、`concurrent_requests` 在调用方未供时自动注入 Chrome 131 浏览器指纹（User-Agent、Accept、Sec-Ch-Ua、…）。若 `.valravn-intel/<domain>/profile.json -> realistic_headers` 存在，其值（含真实浏览器会话捕获的 session cookies 和 auth tokens）填余下。调用方所供 headers / cookies / bearer 永远胜出。无需手动 `get_target_headers`。
    - 首次 `browser_crawl` 后用 `build_target_header_profile(domain)` 建按目标 profile——从 proxy history 拉最佳真实浏览器 header 集并存。一旦建好，每个新 curl/send/concurrent 自动模拟真实客户端，无需操作者介入。
    - Bare 模式（有意）：WAF 指纹 / 纯 raw-wire 测试——传 `bare_headers=True`（curl_request / concurrent_requests）完全跳过自动注入。`send_raw_request` 定义上永远 bare。
    - Unsafe-headers 模式（保指纹、放松 blocklist）：HTTP 请求走私（TE.CL / CL.TE / TE.0 / CL.0）、host-header 注入、HPP、CRLF 注入时传 `unsafe_headers=True`。浏览器指纹留；profile 的 `Host` / `Content-Length` / `Transfer-Encoding` / `Content-Type` 流通。调用方 headers 永远胜出。
    - `session_request` 不自动注入——session 存的 headers 是操作者所有；需要时显式传 realistic headers 到 `create_session(headers=...)`。

## 可见性（26）— ADVISORY

26. **清楚哪些工具命中 Proxy history。** `browser_crawl` / `browser_navigate` 填 **Proxy → HTTP history**。Burp HTTP-client 工具（`curl_request`、`send_raw_request`、`session_request`、probes、scans）出现在 **Logger** + MCP store（非 Proxy history），除非显式 proxied。外部 recon（`run_nuclei`、`run_katana`、`run_subfinder`）经 Burp proxy（127.0.0.1:8080）→ Proxy history。取 `index` 的分析工具只读 Proxy history。

26a. **批量活计是 MCP 工具，不是 Python 脚本。** 任务需 >1 请求时，默认 `concurrent_requests`、`send_to_intruder_configured`、`fuzz_parameter`、`auto_probe`、`batch_probe`、`bulk_test`、`test_auth_matrix`、`test_race_condition`——都经 Burp 路由，可捕获/可 replay。**禁写直接调 `requests` / `httpx` / `fetch` 的 Python 脚本**——绕过 Burp，无 Logger/Proxy 条目，无 `logger_index` 可引为证据，无 annotation，无 replay。若自定义脚本真的不可避免（罕见——通常意味着没选对 MCP 工具），必经 Burp proxy：
   - `export HTTPS_PROXY=http://127.0.0.1:8080 HTTP_PROXY=http://127.0.0.1:8080`
   - 信任 Burp CA（`http://burp/cert`）或仅测试时 `verify=False`
   - 或调 `get_burp_proxy_env()` MCP 工具拿精确 env-var 行

   源自未 proxied 脚本的发现无法满足 Rule 10b 的 `evidence.logger_index` 要求，会被 assess gate 硬拒。

## 创意狩猎（27）— ADVISORY 反清单强制

27. **猎未知，不仅猎已编目。** ≥20% 每会话必须是跳出知识库类别的开放探索：
    - **链式推理。** 走已存 findings 列表问"每个发现 ENABLE 什么？"——open redirect → token 盗窃 → ATO；邮件更改 CSRF → ATO；信息泄露 → recon → IDOR。用 `chain-findings.md`。许多项目只为链式影响付费。
    - **本目标特有业务逻辑缺陷。** 读 3–5 个最高价值端点（`smart_analyze`）问：信任假设是什么？步骤重排？跳过？跑两次？带陈旧状态跑？某步换他人 resource ID 而其他步不换？`auto_probe` 找不到这些。
    - **类外异常。** 任何对基线的未解 delta（status、length、hash、header、latency）即使无类匹配也是候选。别因"不合模式"打发——开 `investigate.md` 挖。
    - **攻击者视角提问。** 攻击者在此想要什么？钱、账户控制、数据外泄、提权、对竞争对手 DoS？从目标倒推找路径。

   按清单走只能拿 info-disclosure 和 self-XSS。真 bug 和高影响链活在清单外。显式为非结构化时间预算 token。

## 影响优先靶向（29）— ADVISORY

29. **猎 MEDIUM+ 所在。INFO 发现是线索，非结果。**

   项目为影响付费。一个会话以六个信息泄露结束、零授权或逻辑 bug 尝试——没测目标，只指纹了它。每个 INFO/LOW 观察是下一问题的输入，非存档输出。

   - **按类价值预算，不按容易度。** 付费的类：授权（IDOR/BOLA/BFLA/BOPLA）、认证与会话（ATO、MFA/reset 流、OAuth/SAML/JWT）、业务逻辑与竞争、注入达 sink（SQLi/RCE/SSTI/SSRF）、mass assignment。把大部分测试时间花这。Scanner 形类（headers、TLS、版本 banner、verbose error）是 recon 输出——记录，不猎。
   - **每个 LOW 入档前获一轮升级尝试。** 问它 ENABLE 什么。open redirect 啥也不是；OAuth `redirect_uri` 上的 open redirect 是 token 盗窃。verbose error 啥也不是；点名内部主机的 verbose error 是 SSRF 目标列表起点。用 `propose_chains` / `research_attack_vector` / `chain-findings.md`。升级失败，发现是 `notes.md` 里的一条 note，非 submission。
   - **凭据在手时认证表面优先。** Rule 28 的 grey-box 心法是 MEDIUM+ 集中处。跨角色 `test_auth_matrix` 是工具上单次 ROI 最高的调用。
   - **零 MEDIUM+ 候选的会话是换方法的信号**，非归档已有。重读最高价值端点、质疑信任假设、或换表面。

## 输出节俭（30）— ADVISORY

30. **每个 artifact 须被人读。为那个读者写，否则不写。**

   - **规范文件优先。** `findings.json`、`coverage.json`、`endpoints.json`、`checkpoint.json`、`notes.md` 是存储。建文件前查它们是否已持有该事实。规范存储旁的 ad-hoc summary/scratch markdown 是须永久调和的重复。
   - **永不在散文中复述存储。** `findings/<fid>/` 下的 markdown 是 `findings.json` 的再生投影——只写、不读回、不手编。
   - **召回收窄。** 分页 + 过滤（`get_findings(severity_min=, summary_only=)`、`load_target_intel` 字段选择、smart 工具的 `summary_only=True`）。重载整个交战答一问题是主要可避免的 token 成本。
   - **报告载发现与影响。** 无索引、无内部路径、无计数——见 Rule 16a。`generate_report(audience='client')` 强制；`audience='internal'` 仅用于自验证。
   - **勿叙事。** 工具输出即记录。对工具刚返回的东西写书面总结，除非操作者要求，否则是噪声。

## 压缩生存（31）— ADVISORY

31. **上下文会在交战中途被压缩。只活在会话里的状态 = 你会丢的状态。**

   失败模式静默：压缩后代理从散文重推任务状态、重测已覆盖元组、重发现端点、引用已不持有的索引。

   - **每阶段边界 checkpoint**，非会话末：`write_checkpoint(domain, phase=, round=, next_action=, tasks=[...], open_threads=[...])`。next_action 须具体到冷启动可执行——"对 f007 派 finding-verifier"，非"继续测"。
   - **推理前持久化。** 有意义的发现、已覆盖元组、基线，存在即进其存储（`save_finding`、`record_probe_outcome`、`save_target_intel`），不是用它的分析之后。
   - **绝不跨压缩边界在脑中携带 Burp index。** 索引属 `evidence`、annotations、`reproductions[]`——都可重读。压缩后从记忆召回的索引是写up 引用不存在流量的经典源头。
   - **resume 时先读状态再动**：`load_checkpoint` + `load_target_intel(domain, "all")` + `coverage_summary`。不重 crawl 重建已在盘上的东西。

## 歧义（32）— ADVISORY

32. **请求有两种导致不同流量的读法时，问。**

   猜测最好情况浪费一轮，最坏情况发出 scope 外请求。问的情况：目标或 scope 不清；测试深度/类未声明；边界发现提交意图未知；措辞映射到不同 blast radius 的工具；暗示难以撤销的动作。呈现读法、推荐一种、问一次、然后动。有合理默认且错代价是一次重跑时不问。

## 7 问验证门（由 `assess_finding` 调用，Rule 10b）

任何发现 `confirmed` 前，7 须全过。一个"NO" = 不报告。

1. **在 scope？** 按项目策略，非仅域名。
2. **可复现？** 现在从零再触发一次？
3. **真实影响？** 攻击者实际能 DO 什么？（非理论。）
4. **非重复？** 已存 findings + 该目标常见公开报告。
5. **满足证据要求？** 按 `verify-finding.md` 类门槛。
6. **不在 NEVER SUBMIT 列表？** 见下。
7. **你是 triager 会群发此发现吗？** 你会标 informative——别提交。

## NEVER SUBMIT 列表

单独报告这些是噪声。仅 CHAIN 出真实影响时可报（Rule 17）。

| Finding | 为何不可单独报 |
|---|---|
| 缺安全 headers（X-Frame-Options、CSP、HSTS） | 无直接利用 |
| Cookie 无 Secure/HttpOnly | 需 MitM 或 XSS 才能利用 |
| 非敏感页 clickjacking | 无状态变更动作 |
| Self-XSS | 受害者须粘贴 payload |
| logout / 非状态变更端点 CSRF | 无真实影响 |
| 单独 open redirect | 无链低影响 |
| Mixed content | 浏览器缓解 |
| 非敏感端点无限速 | 无安全影响 |
| 堆栈跟踪 / 单独 verbose error | 信息泄露，不可利用 |
| 公开注册用户名 / 邮箱枚举 | 常为设计 |
| 缺 `Referrer-Policy` | 极微 |
| SPF/DMARC/DKIM | 邮件安全，常 OOS |
| 无 XSS 的内容伪造 | 影响极小 |
| 无 cache poisoning 的 host-header 注入 | 无利用路径 |
| 无凭据 + 敏感数据的 CORS | 浏览器阻断凭据 |
| SSL/TLS 配置（除非 critical） | Scanner 噪声 |
| 单独软件版本披露 | 需利用链 |
| Reverse tabnabbing | 低影响，争议 |
| 文本注入（非 HTML） | 无代码执行 |
| IDN 同形异义攻击 | 浏览器缓解 |
| 缺 `autocomplete=off` | 密码管理器处理 |
| 启用 OPTIONS 方法 | 正常 HTTP 行为 |

**例外：** 与另一发现 chain → 可报。用 `chain-findings.md`。

## 测试模式选择（28）

28. **模式按工具调用，非按会话。** 会话起始定默认，但每次调用重评：每当活跃 session 有 cookies / Authorization header / 认证状态，对该调用用 GREY-BOX 心法，不管会话起始模式。会话起始黑盒但中途获凭据，应立即测 IDOR / BFLA / 业务逻辑 / 仅认证攻击表面。会话起始锁一种模式是漏发现的主因。心法：

**Black box**（无内部访问——仅 URL/IP）：
- Recon 重：`browser_crawl` → `discover_attack_surface` → `full_recon` → `query_crtsh` → `fetch_wayback_urls`
- 全指纹：`detect_tech_stack`、`extract_js_secrets`、`analyze_dns`
- 枚举：`discover_common_files`、`discover_hidden_parameters`
- 盲探：`auto_collaborator_test`、`auto_probe` 全类别
- 低发现链成影响：`chain-findings.md`
- 心法：不假设，全映射，再攻。每个响应是情报。

**Grey box**（凭据、API 文档、有限源）：
- Session 优先：`create_session` → 后续全用 `session_request`
- 认证边界：跨角色 `test_auth_matrix` — 最高 ROI 测试
- API 导向：`parse_api_schema`、`batch_probe`、`test_mass_assignment`
- 业务逻辑：`test_business_logic`、`test_race_condition`、`run_flow`
- 认证扫描：带 session 的 `auto_probe` 触达隐藏端点
- 心法：深不广。授权、业务逻辑、状态操纵出 critical bug。

**White box**（全源访问）：
- 源优先：读 controllers、routes、middleware。找未净化路径。
- 追数据流：input → controller → service → sink。每条未净化路径是候选。
- 定向 payload：按真实代码构造，非通用列表。`get_payloads` 带特定 context。
- 覆盖驱动：`save_target_intel(domain, "coverage", ...)` 跟踪已测路径。
- 心法：不发现你能读的。直奔危险函数。

**Hybrid**（赏金默认——grey box app + black box 基础设施）：
- 黑盒起：`browser_crawl` → `full_recon` → `detect_tech_stack`
- 建账户：每角色 `create_session`
- 切 grey box：`test_auth_matrix` → 带 session 的 `auto_probe` → `test_business_logic`
- 验证 + 链：`verify-finding.md`、`chain-findings.md`
