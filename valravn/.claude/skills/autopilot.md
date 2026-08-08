---
name: autopilot
description: 自主狩猎循环——带熔断、限速、checkpoint 模式、安全控制
---

# Autopilot 狩猎

你跑自主漏洞狩猎。本 skill 把狩猎方法包以安全控制、进度跟踪、可配 checkpoint 模式。

## 激活

用户说："autopilot [domain]" 或 "auto-hunt [domain]"
可选 flag：
- `--paranoid` —— 每发现停供审（默认）
- `--normal` —— 批发现，每阶段后停
- `--aggressive` —— 最少停，仅 critical 停
- `--max-iterations N` —— 工具调用硬上限（默认 100）
- `--categories [list]` —— 仅测特定漏洞类

## 安全控制

### 熔断器
跟踪连续错误响应。触发则停并报。

```
规则：
- 5 连续 403 → 停："WAF 拦我。暂停避 IP 封禁。"
- 3 连续 429 → 停："被限速。等 60 秒再续。"
- 10 连续 timeout → 停："目标无响应。查目标是否在。"
- Connection refused → 立即停："目标端口闭或防火墙活。"
```

任何成功（2xx/3xx）响应重置计数。

### 限速
请求间强制延迟避被探：

```
模式        | 延迟        | 何时
------------|-------------|---------------------------
recon       | 0.5-1s      | discover_attack_surface, common_files
testing     | 1-2s        | auto_probe, fuzz_parameter
aggressive  | 0s          | test_race_condition (需速度)
cooldown    | 5-10s       | 熔断近触发后 (3/5 错误)
```

### 安全方法策略
自主请求默认限制：

```
始终安全（无需确认）：
- GET、HEAD、OPTIONS 请求
- 只读 MCP 工具 (get_*, search_*, load_*, extract_*, detect_*, analyze_*)

--paranoid 模式需确认：
- POST、PUT、PATCH、DELETE 到非读端点
- 高 payload 数 fuzz_parameter
- 任何改服务端状态的请求

绝不于 autopilot：
- 到 scope 外域请求
- 破坏性 payload（DROP TABLE、rm -rf、shutdown）
- 可能致目标数据丢失的请求
```

### Scope 卫士
每请求前验目标在 scope：
```
1. check_scope(url) → 必返 true
2. URL 含未见新子域 → 停并确认
3. 绝不跟 redirect 到 scope 外域
```

## 自主模式（风险分层行动门——Burp AT 平价）

三个具名自主级别，映射既有 checkpoint flag。同 Burp AT 出厂的同款梯度表（Manual / Smart / Autonomous），但门在**工具层**强制（HARD Rule 1–10、`confirm_*` 门、scope 模式）——架构上与模型分离，故 prompt-injected 代理仍不能越权。

| 模式 | Flag | 行为 |
|---|---|---|
| **Manual** | `--paranoid` | 每状态变更动作前问；仅自动跑 ALWAYS-SAFE 读 |
| **Smart** | `--normal` | 安全 + 低影响探针独立动；**任 HIGH-IMPACT 动作前停供准**（下表） |
| **Autonomous** | `--aggressive` | 不问就跑——**除 ALWAYS-APPROVAL 列表，此模式也停** |

**ALWAYS-APPROVAL 高影响动作（即便 Autonomous 也停）：**
- 任何已证可利用的状态变更写（ATO PoC、权限改、写他人对象数据）
- 移真实数据离目标的 OOB 外泄（盲 SQLi dump、超版本 banner 的 XXE 文件读）
- `msf_exploit` / `msfrpc_module_execute` / 任何超良性 `id`/`whoami` marker 的 RCE 确认 payload
- `save_finding` 提交格式导出 / 平台推送（`format_finding_for_platform`、生成报告交付）
- 对新发现 scope 内子域首请求（Scope 卫士 §2）
- 破坏性 denylist 上任何项（HARD Rule 5–9）——此为硬 BLOCK，非批准提示

Smart/Autonomous 永不放松 Rule 1–10。模式只管**已许动作的循环查频**——它不能授工具层拒的权限。

## Autopilot 循环

```
INITIALIZE:
  iteration = 0
  max_iterations = 100 (或用户指定)
  findings = []
  errors_consecutive = 0
  phase = "recon"

LOOP:
  while iteration < max_iterations:
    iteration += 1

    // 熔断检查——区分 WAF block 与 auth-control 403。
    // IDOR/BFLA/auth-matrix 测试中通用 403 是 EXPECTED 控制
    // 响应（服务端在否定场景下正确执行访问控制）。仅当 403 带
    // WAF 类信号时计入熔断：
    //   - server: cloudflare / cloudfront / akamai / fastly / sucuri / incapsula
    //   - x-* WAF headers: cf-ray, x-amzn-waf-action, x-akamai-staging, x-incap-*
    //   - body contains "blocked by" / "ray id" / "request id" + "waf" / "firewall"
    //   - status 429 (rate limit) — 直计入
    // authz 测试中无 WAF 信号的 403 是信号，非噪声——继续。
    // 提示：用 phase=authz 标测试以触发此分支。
    if waf_blocks_consecutive >= 5 OR rate_limit_consecutive >= 5:
      REPORT("Circuit breaker triggered: {N} consecutive WAF/rate-limit blocks. Slow down or pivot to OOB/encoded payloads.")
      BREAK
    if errors_consecutive >= 10:
      REPORT("Circuit breaker triggered: {N} consecutive non-403 errors (5xx, network). Likely server overload or session expired.")
      BREAK

    // Phase 执行
    match phase:
      "recon":
        run Phase 1 + Phase 2 from hunt skill
        save all intel
        phase = "test"
        CHECKPOINT(mode)

      "test":
        select next untested category from priority list
        if no categories left:
          phase = "chain"
          continue
        run testing for selected category
        save coverage + findings
        CHECKPOINT(mode)

      "chain":
        if findings.length >= 2:
          attempt chain-findings skill on low/medium findings
        phase = "report"

      "report":
        generate summary
        BREAK

    // 错误跟踪——分 WAF/限速 与 auth-control / 通用
    if last_action_had_error:
      if last_status == 429:
        rate_limit_consecutive += 1
      elif last_status == 403 AND has_waf_signal(headers, body):
        waf_blocks_consecutive += 1
      elif last_status == 403 AND in_authz_test_phase:
        // IDOR/BFLA/auth-matrix 期间预期响应——不增
        pass
      else:
        errors_consecutive += 1
    else:
      errors_consecutive = 0
      waf_blocks_consecutive = 0
      rate_limit_consecutive = 0

    // 按模式处理发现
    if new_finding_detected:
      findings.append(new_finding)
      match checkpoint_mode:
        "paranoid":
          PAUSE("Found: {finding.summary}. Verify and continue? [y/skip/stop]")
        "normal":
          // 续，phase 末批报
        "aggressive":
          if finding.severity == "CRITICAL":
            PAUSE("CRITICAL finding: {finding.summary}. Review before continuing.")
          // 否则续
```

## Checkpoint 行为

### --paranoid（默认）
```
每发现后：
  显：发现摘要、severity、证据片段
  问："继续狩猎？[yes/skip-category/investigate/stop]"
  - yes → 续当前类
  - skip-category → 移下漏洞类
  - investigate → 此发现切 investigate skill
  - stop → 进 report phase

每阶段后：
  显：全进度仪表板
  问："进下阶段？"
```

### --normal
```
每阶段后：
  显：此阶段发现、总进度
  问："续下阶段？[yes/reprioritize/stop]"

阶段内发现静默累积。
```

### --aggressive
```
仅停于：
  - CRITICAL 发现（必审 critical）
  - 熔断触发
  - 达 max iterations
  - 类全耗尽

其余不停跑。
```

## 进度仪表板

每个 checkpoint 显此：

```
╔══════════════════════════════════════════════════╗
║  AUTOPILOT: {domain}                             ║
║  Mode: {paranoid|normal|aggressive}              ║
║  Iteration: {N}/{max}  Phase: {current_phase}    ║
╠══════════════════════════════════════════════════╣
║  FINDINGS                                        ║
║  Critical: {N}  High: {N}  Medium: {N}  Low: {N} ║
║                                                  ║
║  COVERAGE                                        ║
║  Endpoints: {tested}/{total} ({pct}%)            ║
║  Categories: {tested_cats}/{total_cats}           ║
║  ✓ {completed categories...}                     ║
║  → {current category}                            ║
║  · {remaining categories...}                     ║
║                                                  ║
║  HEALTH                                          ║
║  Consecutive errors: {N}/5                       ║
║  Last response: {status_code} ({elapsed}ms)      ║
║  Session: {session_name} (active)                ║
╚══════════════════════════════════════════════════╝
```

## 审计追踪

每动作记录供可复现：

```
save_target_notes(domain, notes + """
## Autopilot Session {timestamp}
Mode: {mode}, Max iterations: {max}
Duration: {start} → {end}

### Actions Log
| # | Action | Target | Result |
|---|--------|--------|--------|
| 1 | discover_attack_surface | / | 23 endpoints |
| 2 | auto_probe(sqli) | /api/users?id= | score 45 (suspected) |
| 3 | verify sqli | /api/users?id= | CONFIRMED (time-based) |
...

### Findings Summary
{findings table}

### Coverage Gaps
{what wasn't tested and why}
""")
```

## 恢复 Autopilot

autopilot 被中断（上下文限、用户停、错）：

1. `load_target_intel(domain, "all")` —— 取当前状态
2. `load_target_intel(domain, "coverage")` —— 看测过什么
3. `load_target_intel(domain, "notes")` —— 读审计追踪
4. 从最后未完 phase/category 续
5. 勿重测已覆盖参数（查 coverage 条目）

## 与代理集成

跑 autopilot 配代理调度（推荐提速）时：

```
Phase "recon":
  并行发 recon-agent + js-analyst（见 dispatch-agents skill）

Phase "test":
  在不重叠目标上发最多 3 个 vuln-scanner 代理
  编排者监控进度并合并结果

Phase "chain":
  顺序跑（需全发现上下文）

Phase "report":
  顺序跑（需全上下文供总结）
```

## 紧急停

若任一点检测到：
- 请求发往错误域（scope 破）
- 意外破坏性响应（数据被删）
- WAF 永封迹象（全请求 403 带封页）
- 目标像有真实用户数据风险的生产系统

**立即停。** 报用户。勿尝试恢复。
