---
name: resume
description: 从前会话恢复赏金测试——重验发现、查覆盖缺口
---

# 恢复测试

你接续前会话的赏金交战。优先：高效恢复上下文、验无变更、定最高价值下一步。

## SMART MOVE — 首调

R20a 会话起始门按此序跑在任何测试前：
1. `load_target_intel(domain, "all")` —— 恢复 profile / findings / coverage / fingerprint
1b. `load_checkpoint(domain)` —— 恢复持久任务台账：phase、round、`next_action`、open tasks、open threads。这是跨上下文压缩存活的那次读——从其 `next_action` 起，不从散文 `notes.md` 重推状态。空 → 落到步骤 5 的新目标路径。
2. `check_target_freshness(domain, session)` —— 标指纹漂移端点；这些重回测试队列
3. `get_business_context(domain)` —— 确认结构化评分输入在；空则**重测前**跑 `capture_business_context(...)`（assess_finding 依赖它）
4. `list_sessions` → 活则复用，否则从存档 profile `create_session`
5. 若 `.valravn-intel/<domain>/` 空 → 此为新目标。落 hunt.md / smart-move-fresh-target.md，非本 skill。

## Step 1：加载上下文

1. 问用户目标域名（或从 Burp scope / 活跃 session 探出）
2. 调 `load_target_intel(domain, "all")` 取全摘要
3. **调 `get_business_context(domain)`** —— 确认结构化业务上下文在。空则**重测前**跑 `capture_business_context(...)`；assess 门用这些字段评分影响，多个 playbook（尤 `playbook-business-logic.md`）需它们。
4. 无 intel："{domain} 无前会话数据。用 hunt skill 起新狩猎。"

## Step 2：建 / 恢 Session

1. 查 `list_sessions` 找针对此域的现存 session
2. 无 session：从 profile 的 target base URL `create_session`
3. profile 中存有认证：
   - 发快速认证请求查 session cookies 是否仍有效
   - 401/403：用存的登录流（`run_flow` 配 profile 中 auth 步骤）重认证
   - 200：session 仍有效，续
4. 验 scope 配：`get_scope`——空则从 profile 的 scope_rules 重套

## Step 3：查新鲜度

1. 调 `check_target_freshness(domain, session)`
2. 把 staleness 报告分三桶：
   - **FRESH 段** —— 信 memory，跳重扫
   - **STALE 段** —— 需部分重扫（仅变页 / 端点）
   - **UPDATED 知识** —— 有新探针，重测曾"clean"的参数

## Step 4：重验发现（按 severity 排序）

按 severity 排已确认发现（CRITICAL 优先，再 HIGH 等）并重验：

每个 status `confirmed` 发现：

1. **FRESH 且近期已验（< 24h）跳：** 信 memory，不浪费请求
2. **端点变 OR last_verified > 24h 重验：**
   - 经 `session_request` 重发发现的 `poc_request`
   - 查预期行为是否仍现
   - 是：更新 memory 中 `last_verified` 时间戳
   - 否：标 `stale`，增 `verification_failures`
   - `verification_failures >= 2`：标 `likely_false_positive`
3. **优先级规则：** 新测试前先重验 CRITICAL/HIGH 发现——但每发现封顶 2 次尝试。重验 2 次失败，标 `stale` 并 pivot 到未测类。**勿**无限循环重验不稳发现；那饿死未测攻击表面的覆盖。

**模式提醒（Rule 28）：** 恢复的 session 有 cookies / Authorization header，新测试切 GREY-BOX 心法——`assess_finding` 传 `session_name=<name>` 让认证影响加成（IDOR/BFLA/business_logic/authorization +10%）。

存更新发现：`save_target_intel(domain, "findings", updated_data)`

## Step 5：检测攻击表面变化

若 freshness 检查显 STALE 端点：

1. 跑 `discover_attack_surface(session)` 取当前端点
2. 对比存的 `endpoints.json`：
   - **新端点** —— 高优先测试（新代码 = 新 bug）
   - **移除端点** —— 标相关发现为 stale
   - **变参数** —— 即便曾 clean 也重测
3. 存更新端点：`save_target_intel(domain, "endpoints", new_data)`

若 knowledge 版本变：
1. 加载 `coverage.json` —— 识别用旧 knowledge 版本测过的参数
2. 这些是用新探针重测的候选（有新检测技术可用）
3. 优先曾 clean 的高危参数

## Step 6：呈现仪表板

向用户呈清晰状态报告：

```
TARGET: example.com (PHP 8.1 / Apache / MySQL)
SESSION: target1 (active, authenticated)

FINDINGS:
  2 confirmed (last verified: just now)
    [CRITICAL] SQL Injection in GET /api/users?id — time-based blind, 3.2s delay
    [HIGH] Reflected XSS in GET /search?q — unencoded in HTML body
  1 stale (endpoint changed — needs re-verification)
    [HIGH] IDOR in GET /api/orders?order_id — compare_auth_states showed identical
  1 likely false positive (2 verification failures)
    [LOW] Open redirect in /login?next — no Collaborator interaction

ATTACK SURFACE CHANGES:
  3 new endpoints found (NEW — test these first):
    POST /api/v2/users — has 'role' param (mass assignment risk)
    GET /api/export — has 'format' param (SSTI risk)
    POST /api/upload/avatar — file upload endpoint
  1 endpoint removed: GET /api/legacy/users

COVERAGE: 15/42 endpoints tested (36%)
  sqli:         8/15 high-risk params tested
  xss:          5/15 tested
  idor:         2/15 tested
  lfi:          0/15 tested  <-- UNTESTED
  file_upload:  0/3 forms tested  <-- UNTESTED
  ssti:         0/5 template params  <-- UNTESTED
  jwt:          not tested  <-- AUTH USES JWT

FRESHNESS:
  profile:   FRESH
  endpoints: STALE (root page changed — re-crawl recommended)
  knowledge: UPDATED (new probes available for sqli, xss — v{old} -> v{new})

NOTES (from last session):
  "Try IDOR on /api/v2/ — less hardened than v1"
  "WAF only on /admin paths — other paths unprotected"
  "JWT uses HS256 — try weak secret brute force"
```

## Step 7：建议下一步（按预期价值排）

基于仪表板建议优先行动：

### 档 1：速胜（每 1–5 请求，高价值）
1. **重验 stale 发现** —— 便确认，仍有效则高价值
2. **测新端点** —— 新代码最可能有 bug
3. **查新 upload 端点** —— file upload 漏洞高 severity

### 档 2：覆盖缺口（每 10–30 请求）
4. **测未测类别** —— LFI 和 SSTI 0% 覆盖，PHP 栈下高优先
5. **JWT 分析** —— 认证用 JWT，`test_jwt` 单调用高回报
6. **跑新知识库探针** —— 用更新探针重测曾 clean 参数

### 档 3：深调查（30+ 请求）
7. **新端点的隐藏参数发现** —— `discover_hidden_parameters`
8. **状态变更端点的 race condition 测试**
9. **跟下前会话 notes** —— 用户和 Claude 对有前景目标的观察
10. **若攻击表面 delta 出现新 JS 文件**重扫 JS secret

问用户：**"想聚焦哪块？"**

随后交 hunt skill 执行（从相关 phase 起）。
