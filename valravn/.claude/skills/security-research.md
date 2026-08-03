---
name: security-research
description: 深挖可疑发现——挖披露报告、writeup、类特定冷门向量。用于异常可能可利用但还看不到链时。
prerequisite: 至少一——已确认异常（vs 基线的 status/length/timing delta）、版本化技术栈指纹、或高价值端点上的开放 "what would an attacker want here?" 问题。
stop_condition: 6 web-fetch + 2 可测 MCP 探针未产 reproducer 或值得追的新假设 → 回 router。研究是攻击之手段，非替代。
---

# Security Research Skill

将 Rule 27 的 20%-creative-hunting 命令操作化。非 "let me Google something"，跑一次 `research_attack_vector` 取策展 bundle、WebFetch 高信号 URL、把所学转成**一个**可测 MCP 探针。

## 何时调

| 触发 | 用 research？ |
|---|---|
| `auto_probe` 返 score 30-60（含糊） | YES —— 加载类特定冷门向量 |
| 指纹了特定框架 + 版本 | YES —— 查披露 CVE + 近期 writeup |
| 高价值端点（auth / payment / admin / file upload）无显 bug | YES —— 拉 "what would an attacker want here?" 提示 |
| 确认 bug，想升级 severity | YES —— 加载链假设 |
| `auto_probe` confidence ≥ 80 + 清晰 PoC | NO —— 直 `assess_finding` |
| 预侦察（无指纹） | NO —— 先跑 `full_recon` |

## 那一次调用

```python
research_attack_vector(
    vuln_type="ssrf",                              # required
    tech_stack="express,redis",                    # narrows code-search + CVE queries
    finding_summary="Image-proxy /api/preview?url= fetches arbitrary URLs",
    endpoint="/api/preview",
    target_domain="target.com",
)
```

得回七段。按此序分诊：

```
1. DEEP-DIVE QUESTIONS   →  pick the question your finding doesn't yet answer
2. OBSCURE VECTORS       →  pick ONE you haven't tested
3. CHAIN HYPOTHESES      →  bank these for after you confirm the primitive
4. METHODOLOGY DEEP-LINKS→  WebFetch — verified-static PortSwigger Academy + HackTricks + PAYLOADs + OWASP WSTG
5. SUGGESTED WEB SEARCHES→  WebSearch — disclosed reports / writeups / tech-specific bypass
6. ADVISORY DATABASES    →  WebFetch — Exploit-DB / OSV / GH Advisory / Snyk DB / AttackerKB
7. GITHUB CODE SEARCH    →  WebFetch — sink-pattern hunt in similar codebases
```

**为何两输出模式？** 部分源对普通 curl 返富内容（PortSwigger Academy、HackTricks、OWASP WSTG、Exploit-DB、OSV）——这些进 WebFetch 段。其他是 JS 渲染 SPA（HackerOne hacktivity）、Cloudflare 拦（NCC、CISA KEV）、或对 bot 403（OpenBugBounty）——这些进 WebSearch 段因搜索引擎爬它们返 Claude 可见的摘录。

## 工作流

```
┌─────────────────────────────────────────────────────────────────┐
│ Anomaly observed → research_attack_vector(vuln_type, ...)       │
│                                                                 │
│ FREE (no fetch): read DEEP-DIVE + OBSCURE + CHAIN inline        │
│   → pick ONE hypothesis you haven't tested                      │
│                                                                 │
│ WebFetch METHODOLOGY DEEP-LINKS (PortSwigger Academy + HackTricks)│
│   → class methodology + canonical payload patterns              │
│                                                                 │
│ WebSearch SUGGESTED queries (top 2-3)                           │
│   → disclosed reports, writeups, tech-specific CVEs             │
│   → look for: exact tech match / similar param / same chain     │
│                                                                 │
│ Optional: WebFetch 1-2 ADVISORY DBs if tech_stack is versioned  │
│   → Exploit-DB / OSV / Snyk DB for known CVEs + PoCs            │
│                                                                 │
│ If a writeup or PoC shows a working pattern:                    │
│   → ADAPT, don't copy — match to your target                    │
│   → craft_payload skill if needed                               │
│                                                                 │
│ Build ONE testable probe — auto_probe / test_* / curl_request   │
│   → measured against the recorded baseline (Rule 11)            │
│   → through Burp (Rule 26a — never raw requests/httpx)          │
│                                                                 │
│ Outcome:                                                        │
│   PASS  → assess_finding → save_finding (with chain_with[])     │
│   FAIL  → did you exhaust DEEP-DIVE questions?                  │
│           YES → return to router, this isn't the vuln           │
│           NO  → cycle one more time                             │
└─────────────────────────────────────────────────────────────────┘
```

## Bundle 输出含义

**DEEP-DIVE QUESTIONS** —— 开放 "你应知此" 提示。**非**机械完成的清单。选你**未**想过的那个。

**OBSCURE VECTORS** —— 实际被错过的攻击表面。这些是经验研究员见而 auto_probe 不见之物。偏赏金披露报告模式。

**CHAIN HYPOTHESES** —— 原语 ENABLE 什么。对 severity 关键：
- Reflected XSS 单独 = MEDIUM
- XSS → CSRF email-change → ATO = CRITICAL

同原语、两不同报告、两不同赏金。

**METHODOLOGY DEEP-LINKS** —— 直 WebFetch URL 到 verified-static-HTML 参考页：
- **PortSwigger Web Security Academy** —— 类方法论、labs、payload 分类。
- **HackTricks book** —— 按技术的参考含引擎特定 gadget。
- **PayloadsAllTheThings** —— 按类策展 payload 档案。
- **OWASP WSTG** —— 官方测试方法论含证据要求。

总先 WebFetch 这些——稳定、内容富、避 bot-block 陷阱。

**SUGGESTED WEB SEARCHES** —— 为 Claude 原生 `WebSearch` 工具预烤查询。我们不直链 HackerOne hacktivity / Bugcrowd Crowdstream / OpenBugBounty 因它们是 JS-SPA / Cloudflare 拦。搜索引擎爬它们——我们经 `site:hackerone.com/reports` + `site:pentester.land` + `site:portswigger.net/research` 过滤器 + 技术特定 CVE/绕过关键字搜得内容。

跑 2-3 最高相关查询。WebSearch 返综合摘录；若一个看着有戏，WebFetch 它引的具体 URL。

**ADVISORY DATABASES** —— 直 WebFetch URL 到服务端渲染的漏洞库（全验返富内容、非 JS shell）：
- **Exploit-DB** —— 历史 PoC 档案。
- **OSV.dev** —— Google 开源漏洞库。
- **GitHub Advisory Database** —— 高质量、标注好。
- **Snyk Vulnerability DB** —— 商业级跟踪。
- **Rapid7 AttackerKB** —— "exploited in the wild" 情报供 severity 评估。

`tech_stack` 供时最有用——这些库按包名索引。

**GITHUB CODE SEARCH** —— 当你怀疑已知脆弱代码模式时。搜 URL 预建 `findByPk req.params.id`、`Object.assign req.body`、`render_template_string request` 等。WebFetch 看类似代码库中的 sink 形状。

## 改编披露 PoC

HackerOne 披露报告有看似相关的 PoC payload 时：

1. **确认原语匹配你的上下文。** 同引擎？同 auth 状态？同 content-type？
2. **剥破坏性部分。** 披露 PoC 有时含 `; DROP TABLE`——按 Rule 5 替为仅检测（`SLEEP(5)`、数学表达式、Collaborator 回调）。
3. **匹配 marker 约定。** 用唯一每调 marker 如原生 `test_*` 编排器做——基线 diff 中易见。
4. **经 Burp 发。** 勿经 raw `requests` / `httpx` 脚本（Rule 26a）。用 `curl_request` / `auto_probe` / `test_*` / `session_request`。

## 反模式（勿做）

- **勿 fetch bundle 中每 URL。** 预算：每循环 2 WebFetch + 2-3 WebSearch。过度研究是失败模式。
- **勿逐字粘披露报告 payload。** 项目拒 "copy of public bug" 报告。改编到你目标的精确上下文。
- **勿研究你还没试探的类。** 总先探 → 观 → 研 → 重探。倒此产无证据的 "could be X" 报告。
- **勿忽略 CHAIN HYPOTHESES。** 若你的 bug 单独封 MEDIUM，链段告诉你项目是否付链——有时同 bug 在不同链目标上变 CRITICAL。
- **勿跳内联 KB。** DEEP-DIVE + OBSCURE + CHAIN 在工具回复中免费——任何 fetch 或 search 前先读这些。
- **勿 WebFetch JS-SPA URL 直。** 若段说 "WebSearch"（非 "WebFetch"），经 WebSearch ——底层源需搜索引擎渲染才可读。

## 与其他 skill 集成

- `craft-payload.md` —— research_attack_vector 说 "试 gopher://"，craft-payload 告诉你精确字节级 gadget。
- `verify-finding.md` —— 一旦研究产 reproducer，跑过 7 问门。
- `chain-findings.md` —— CHAIN HYPOTHESES 输出直喂链构造。
- `playbook-cve-research.md` —— research_attack_vector 配 `tech_stack=` 与 CVE playbook 重叠。若 bundle 的 `map_tech_to_cves` 提议命中，切该 playbook。

## Token 纪律

- 内联 KB（DEEP-DIVE + OBSCURE + CHAIN）免费——一次工具调用回。
- 每 WebFetch 约 1-3K token 取页面。预算每研究循环 2-3 fetch。
- 若 6+ fetch 深仍无假设，你过度研究了。回 router 选不同目标。

## 快例

**例 1 —— 含糊 SSRF 异常**
```
auto_probe → score=42 on ?url= param. Status delta but no error reflected.

research_attack_vector(vuln_type="ssrf", tech_stack="node,express",
                      finding_summary="image-proxy with allowlist that accepts http://target.com.evil.com")
> OBSCURE: "Webhook URL acceptance — slack/discord-style integration endpoints often SSRF"
> DEEP-DIVE: "DNS rebinding — does the app re-resolve between check and use?"
> DISCLOSED: WebFetch top H1 result on Express SSRF allowlist bypass

Adapted hypothesis: try DNS-rebinding TTL=0 host.
 test_ssrf(url=..., probes=['dns_rebind']) → confirm.
```

**例 2 —— 找到版本化 Spring Boot**
```
detect_tech_stack → Spring Boot 2.6.6 confirmed.

research_attack_vector(vuln_type="ssti", tech_stack="spring-boot",
                      finding_summary="Spring Boot 2.6.6 with Thymeleaf 3.0.15")
> CHAIN: "SSTI → cloud metadata read → temp creds → wider compromise"
> OBSCURE: "Spring Thymeleaf Spring EL preprocessing __${...}__::.x syntax"
> DISCLOSED: H1 search for Thymeleaf SpEL preprocessing
> map_tech_to_cves suggested → run that next

Adapted: test_ssti(endpoint=..., parameter=..., engine_hint='spring_el') → confirm.
```

**例 3 —— 高价值端点，尚无 bug**
```
Found /api/admin/migrate accepting POST with JSON body. Auth required but no
obvious vuln. What now?

research_attack_vector(vuln_type="auth_bypass", tech_stack="django",
                      endpoint="/api/admin/migrate")
> DEEP-DIVE: "Header smuggling: X-Original-URL / X-Rewrite-URL"
> OBSCURE: "Method confusion: GET protected but HEAD/OPTIONS/PROPFIND not"
> CHAIN: "Header smuggling → admin panel → ATO of all users"

Adapted: try X-Original-URL: /api/admin/migrate from a low-priv session.
 test_login_bypass(target=..., paths=['/api/admin/migrate']) → confirm.
```
