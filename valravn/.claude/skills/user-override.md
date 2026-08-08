---
name: user-override
description: 操作者如何在默认规则挡合法发现或降真实影响时路由 Claude。用于门拒 / 降操作者手验之物、项目 / 场景异于默认策略、severity 评分错于交战、或攻击向量活于编目类之外时。
---

# User Override —— 场景路由

> **规则引用：** 此处所有 override 叠在 `.claude/rules/hunting.md` 层级分类上（HARD 1–10 = 工具强制、DEFAULT 11–21 = 审计 override、ADVISORY 22–28 = 按需）。HARD 规则不可静默。DEFAULT 和 ADVISORY 可。

默认规则集为**赏金 triage 均值**校准。真实交战多变：明确 scope 的 pentest 可冒破坏性风险；高付项目奖他者拒之类；目标技术栈倒置 severity；编目未见的场景仍真。本 skill 文档化**操作者**如何在默认规则与交战现实冲突时指导 **Claude**。

## 操作者控的 override 表面

五表面。用最轻适合者。

### 1. 每调 override（最轻）

当 `assess_finding` 错拒 / 降**单一**特定发现时用。

```
assess_finding(
  vuln_type="open_redirect",
  evidence="redirected to attacker.com, then OAuth code captured at /callback",
  endpoint="https://target.com/oauth/authorize",
  domain="target.com",
  chain_with=["f014"],                    # OAuth code-theft 链
  human_verified=True,                     # 操作者在浏览器确认
  overrides=["q5_evidence:operator_confirmed_in_burp_ui",
             "q7_triager:chained_with_account_takeover"],
)
```

认可的 override 门：

| 门 | 效果 | 何时 override |
|---|---|---|
| `q1_scope` | 跳 scope 检查 | 端点在 scope 但 `check_scope` 返 false（项目 scope 语法不同） |
| `q2_repro` | 跳可复现检查 | auth-state 相关 bug 已被 Q2 EXEMPT 逻辑覆盖；仅 exemption 漏你类时 override |
| `q4_dedup` | 跳 dedup 检查 | 两发现看似 dup 但影响 / sink / 受影响用户集不同 |
| `q5_evidence` | 跳证据关键字检查 | 操作者在 Burp UI / 浏览器 DevTools 验——等价 `human_verified=True` |
| `q6_never_submit` | 跳 NEVER SUBMIT 类阻 | 项目显式接受此类（如某些赏金付 OAuth 流上的 tabnabbing） |
| `q7_triager` | 跳 triager-mass-report 启发式 | 目标项目对低影响但干净的发现有已知接受度 |
| `recon_gate` | 跳 Rule 20a recon-intel 检查 | 侦察在带外做（在另一工具记录） |

每条目必为 `<gate>:<reason>`。Reason 进审计追踪并随发现存。

### 2. Severity / 链提示（中重）

门会 PASS 但推断 severity 错于场景时用。

```
assess_finding(...,
  business_context="banking",          # 影响加 +10%（金融数据）
  environment="production",            # +5%（活影响）
  session_name="hunt",                 # 若 session 认证，IDOR/BFLA 加 +10%（Rule 28）
  reproductions=[                      # timing/blind：3 条跳 Q5 timing 规则
    {"logger_index": 41, "elapsed_ms": 5230, "status_code": 200},
    {"logger_index": 42, "elapsed_ms": 5180, "status_code": 200},
    {"logger_index": 43, "elapsed_ms": 5310, "status_code": 200},
  ],
)
```

然后 `save_finding` 上：

```
save_finding(
  ...,
  severity="HIGH",          # 操作者锁；非自动推断
  confidence=0.85,           # 操作者设（用 assess_finding 的建议值）
  chain_with=["f014"],
)
```

Severity 级：`CRITICAL`、`HIGH`、`MEDIUM`、`LOW`、`INFO`。Burp 的 ZERO-NOISE 门不判 severity 超 NEVER_SUBMIT 类成员——操作者拥 severity 决策。

### 3. 每项目策略（交战级）

当整**类**需在此交战不同处理时用，每次。

```
set_program_policy(
  slug="acme-banking-program",
  never_submit_remove=["tabnabbing", "rate_limit_absent_non_sensitive"],   # 项目付这些
  never_submit_add=["cors_no_creds"],                                       # 项目拒此
  confidence_floor=0.65,                                                    # 此项目降门槛
  notes="Acme accepts tabnabbing on OAuth flow (CVE chain bonus). Confidence floor 0.65 per program rules."
)
```

持久化到 `.valravn-intel/programs/<slug>.json`。`assess_finding` 自动加载活跃策略。用 `get_program_policy` 查、`clear_program_policy` 重置。

### 4. Scope override（每域）

当 auto-filter 剥实际在 scope 的域时用（目标的 CDN、OAuth provider、asset host）。

```
configure_scope(
  include=["https://target.com", "https://api.target.com", "https://cdn.target.com"],
  auto_filter=True,
  keep_in_scope=["cloudflare", "apis.google", "googleapis"],   # 留这些即便看着像 tracker
)
```

对 auto-filter 列表做子串匹配。用例：测 OAuth-via-Google 流需 `apis.google.com` 在 scope；测 Cloudflare 前置目标的子域接管需 `cloudflare.com` 可测。

### 5. 仅引用文件 override

当整个知识文件在此交战**不应**被 `auto_probe` 跳过时用。

```python
# 编辑 mcp-server/src/burpsuite_mcp/tools/scan.py:
# _REFERENCE_ONLY = { ... }   ← 从此集移文件
```

或给 `auto_probe` 传显式 `categories=[]` 列表含本排除类——`auto_probe` 直载文件。用例：特定交战期间的 file-upload race condition。

## 路由决策树

```
门拒了我知是真的发现
├── 是 Q1（scope）？
│   ├── 域真在 scope？ → overrides=["q1_scope:per_program_brief"]
│   └── 真不在 scope？ → 停，勿报（Rule 1 是 HARD）
│
├── 是 Q2（可复现）？
│   ├── 类是 auth-state 相关（idor/bfla/business_logic）？ → 已 exempt；复查 vuln_type 拼写
│   └── 真 flake？ → 重测 5 次，供 reproductions=[...]；仍 flaky，overrides=["q2_repro:race_window_5pct"]
│
├── 是 Q4（dedup）？
│   ├── 同根发现，不同影响路径？ → 留一发现，把新向量 ADD 到 evidence_text
│   └── 真不同（不同用户集、不同 sink）？ → overrides=["q4_dedup:distinct_sink_<name>"]
│
├── 是 Q5（弱证据）？
│   ├── Burp UI 验过？ → human_verified=True（无需 override）
│   ├── 有 logger_index？ → 传之；门自动推 marker
│   ├── 有 reproductions[]（timing/blind）？ → 传数组；门计条目
│   └── 都没？ → 先强化证据；勿轻 override Q5
│
├── 是 Q6（NEVER SUBMIT）？
│   ├── 有链？ → chain_with=[<id>]；门条件过
│   ├── 端点敏感（auth/reset/OTP/payment）？ → rate_limit_missing 时门自动过
│   ├── 项目付此类？ → set_program_policy(never_submit_remove=[<class>])
│   └── 无链、无策略？ → 勿单独报（此正是 Q6 之用）
│
├── 是 Q7（triager mass report）？
│   ├── 有链？ → 已自动跳
│   ├── 高 confidence + 干净证据？ → 强化证据推过 confidence_floor
│   └── 项目付低影响类？ → set_program_policy(confidence_floor=0.45)
│
└── 是项目 confidence floor？
    └── Floor 对此交战太高？ → set_program_policy(confidence_floor=<lower>)
```

## Severity 路由 —— 默认错时

advisor 推断 severity。操作者或知更准。三信号 override：

1. **交战独有业务上下文** —— 内部 HR 系统的 pentest 影响异于公开银行 app，即便同漏洞类。设 `business_context` 反**实际**影响，非技术类。
2. **链上下文** —— open-redirect 单独 LOW；open-redirect 链 OAuth code 盗窃 CRITICAL。传 `chain_with=[<oauth_finding_id>]` 并在 save_finding 锁 `severity="CRITICAL"`。
3. **环境** —— 同 SQLi 在 staging 是 HIGH、在 production 是 CRITICAL。传 `environment="production"`。

操作者 severity 在 `save_finding` 上总胜。advisor 推断的 severity 是**建议**，非 verdict。

## 攻击向量 —— 类缺或未知

当 bug 不符编目类：

1. 为 `vuln_type` 选最近已知类（如新颖 auth 缺陷用 "auth_bypass"）。advisor 跳 Q5（未知 vuln_type → DEFAULT REPORT，R2）。
2. 把实际技术放 `evidence_text` 和 `description`——报告用此，非类标签。
3. 若类真新且此交战再见，在 `mcp-server/src/burpsuite_mcp/knowledge/<class>.json` 加知识文件含至少一 context + matchers。`auto_probe` 下跑会取之。

## 不可 override 之物

HARD 层（Rule 1–10）在工具层（Java handler）强制。无 override flag 释这些：

- **Scope 外请求** 在 `check_scope` 被阻。无 `q1_scope` override 防此——它仅压 advisor 的 Q1 门；请求发出时仍被阻。
- **破坏性 payload**（`DROP TABLE`、`rm -rf`、`shutdown`）—— Rule 5 是 HARD。用良性 marker。
- **暴破凭据** —— Rule 6 是 HARD。ID 枚举许（Rule 6 carve-out）；凭据字典不。
- **真实用户数据外泄** —— Rule 7 是 HARD。PoC 意 1-2 记录示 distinctness，非全 dump。
- **改 / 删他人数据** —— Rule 8 是 HARD。用 READ 访问示 IDOR。
- **捏造 OOB 回调** —— Rule 9a 是 HARD。用 Collaborator 或操作者供回调；勿硬编 `evil.com` 做 OOB 外泄。

若 HARD 规则挡你真需之物，答案是交战契约 / SOW，非 Claude override。停并问操作者。

## 速查 —— 最常见操作者指令

```
# "我在 Burp 自验"
human_verified=True

# "此链发现 f014"
chain_with=["f014"]

# "此项目付 tabnabbing"
set_program_policy(slug="<slug>", never_submit_remove=["tabnabbing"])

# "为此项目降 confidence 门槛"
set_program_policy(slug="<slug>", confidence_floor=0.50)

# "按 production banking severity 对待"
business_context="banking", environment="production"

# "留 CDN 在 scope"
configure_scope(include=[...], keep_in_scope=["cloudflare"])

# "我现在 grey-box（以 user 登录）"
session_name="<authenticated_session>"

# "save 时强制 severity HIGH"
save_finding(..., severity="HIGH")
```
