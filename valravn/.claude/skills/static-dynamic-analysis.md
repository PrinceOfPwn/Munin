---
name: static-dynamic-analysis
description: 深度静态文件分析 + 动态行为分析——JS 源码审、响应行为画像、页面变更检测、状态相关漏洞发现
---

# 静态 & 动态分析

你有强分析原语。本 skill 教你**组合**它们：静态分析揭应用**能**做什么、动态分析揭它**实际**做什么、行为差异揭它**藏**什么。

## 何时用此 skill

- 侦察后，测试前深析 JavaScript
- 怀疑状态相关行为（按 cookies、角色、时序不同响应）
- 页面访问间变化（动态内容、A/B 测试、session 相关渲染）
- 想经 source-to-sink 流追踪找 DOM XSS
- 想画像应用行为模式

---

## Part 1：静态 JavaScript 分析流水线

JavaScript 文件是应用送你的免费源码。

### Step 1：盘点全 JS 文件

```python
fetch_page_resources(url="https://target.com")
# 若结果少，自动取页全资源：
fetch_page_resources(index=ROOT_PAGE_INDEX)
```

### Step 2：扫 secrets（高价值、低费力）

每 JS 文件：`extract_js_secrets(index=JS_FILE_INDEX)`

**按 severity 优先：**
- CRITICAL：云凭据（AWS/GCP/Azure）、DB 连接串
- HIGH：API key（Stripe、Twilio、SendGrid）、JWT 签名 secret
- MEDIUM：内部 URL、staging 环境、硬编码密码
- LOW：debug flag、feature toggle、开发者注释

**跨文件狩猎：**
```python
search_history(query="apiKey", in_response_body=True)
search_history(query="secret", in_response_body=True)
search_history(query="config", in_response_body=True)
```

### Step 3：DOM sink/source 分析

```python
analyze_dom(index=PAGE_INDEX)
```

DOM XSS 需：**用户可控 SOURCE** 流入**危险 SINK**。

**Sources**（用户控这些）：
| Source | 攻击向量 |
|---|---|
| `location.hash` | URL fragment：`https://target.com/#PAYLOAD` |
| `location.search` | Query：`https://target.com/?q=PAYLOAD` |
| `document.referrer` | 攻击者页链接 |
| `window.name` | 经 `window.open('target', 'PAYLOAD')` 设 |
| `postMessage` | 攻击者 iframe 跨源消息 |
| `localStorage/sessionStorage` | 若攻击者可经 XSS 写 |

**Sinks**（这些不安全执行 / 渲染）：
| Sink | 风险 | 结果 |
|---|---|---|
| `innerHTML` / `outerHTML` | HIGH | HTML tag 执行 |
| `setTimeout(string)` / `setInterval(string)` | HIGH | 字符串跑为 JS |
| `location =` / `location.href =` | MEDIUM | 导航到 javascript: URL |
| `element.src =` | MEDIUM | 加载攻击者资源 |
| jQuery `.html()` / `.append()` | HIGH | HTML 解析 |
| 框架 unsafe 绑定（v-html、[innerHTML]） | HIGH | 绕框架转义 |

### Step 4：手动追 source-to-sink

analyze_dom 报 sources 和 sinks 时：
```python
get_request_detail(index=JS_FILE_INDEX, full_body=True)
```

**追踪清单：**
1. 找 SOURCE 值在哪读
2. 追变换（净化？编码？校验？）
3. 找到 SINK
4. 查净化：DOMPurify = 通常安全；自定义 regex = 大概率可绕；无 = DOM XSS

**测确认流：**
```python
# 对 hash source + innerHTML sink：
session_request(session, "GET", "/page#<img src=x onerror=alert(document.domain)>")
```

### Step 5：发现隐藏功能

```python
extract_api_endpoints(index=JS_FILE_INDEX)
search_history(query="admin", in_response_body=True)
search_history(query="debug", in_response_body=True)
search_history(query="/api/v2", in_response_body=True)
```

**揭隐藏特性的模式：**
- `if (user.role === 'admin')` —— admin 专属 UI 路径
- `if (config.debug)` —— debug 模式
- `// TODO:` / `// FIXME:` —— 弱点
- `fetch('/api/v2/...')` —— 未文档化 API 版本
- `feature_flags` —— 未发布特性
- `staging.target.com` —— 内部环境
- 注释掉的代码 —— 移除的特性但服务端可能仍可用

---

## Part 2：动态行为分析

### 行为画像

```python
# 画像响应一致性：同请求 3 次
r1 = session_request(session, "GET", "/api/users")
r2 = session_request(session, "GET", "/api/users")
r3 = session_request(session, "GET", "/api/users")
# 不一致 = 动态内容（时间戳、CSRF、广告）
```

### Auth-State 差分（最强技术）

**同端点，不同认证上下文：**
```python
test_auth_matrix(
    endpoints=[
        {"method": "GET", "path": "/dashboard"},
        {"method": "GET", "path": "/api/profile"},
        {"method": "GET", "path": "/admin"},
    ],
    auth_states={
        "admin": {"session": "admin_session"},
        "user": {"session": "user_session"},
        "anon": {"remove_auth": True},
    }
)
```

| 观察 | 含义 | 行动 |
|---|---|---|
| admin vs user 同响应 | IDOR / broken access control | 立即验证 |
| 不同内容，同 status | 正确 auth——查共享元素泄露 | 审 JS、错 |
| anon 的 admin 200 | Critical auth 绕 | 立即记录 |
| anon 403、user 200 | auth 可——测 user-to-user IDOR | 不同用户 ID |

### Parameter-State 差分

```python
fuzz_parameter(index, parameter="id",
    payloads=["1", "2", "0", "-1", "999999", "null", "undefined"],
    grep_match=["email", "phone", "address", "admin", "password"])
```

- 每 ID 不同用户数据 = IDOR
- 错信息变 = info 泄露
- redirect 变 = 访问控制逻辑
- 隐藏字段现 / 没 = 基于角色渲染

### 页面变更检测

```python
# 会话起始指纹
save_target_intel(domain, "fingerprint", {"pages": [
    {"path": "/", "response_hash": "sha256:...", "response_length": 12345, "status": 200},
    {"path": "/login", "response_hash": "...", "response_length": 5678, "status": 200},
]})

# 之后查变
check_target_freshness(domain, session)
```

**信号 vs 噪声：**
| 变更 | 信号 | 噪声 |
|---|---|---|
| 新 HTML 元素 | 新攻击表面 | 广告轮换 |
| 新 JS 文件 | 新代码待析 | CDN 版本升 |
| Length +/- 5% | 微动态 | 时间戳、CSRF |
| Length +/- 20% | 显著变更 | 通常有意义 |
| Status 变 | 主要行为变 | 总有意义 |

### 动作触发变更

```python
# 登录后变什么？
before = session_request(session, "GET", "/dashboard")  # 未认证
run_flow(session, steps=[login_steps...])
after = session_request(session, "GET", "/dashboard")   # 已认证
compare_responses(before_index, after_index)
# 找：新端点、admin 链接、隐藏表单、不同 JS 加载
```

---

## Part 3：行为异常分类

| 类型 | 指示 | 可能原因 | 行动 |
|---|---|---|---|
| Status 异常 | 200->500 注入 | 注入点 | 查错 body 找 SQL/堆栈跟踪 |
| Length 增 | 响应长得多 | 数据泄露、UNION 成功 | compare_responses 看 diff |
| Length 减 | 响应短 | 内容被滤、盲假 | 查数据是否消失 |
| Timing 峰 | >3x 基线时间 | 盲注入（SLEEP） | 带 3 次、不带 3 次测 |
| Content diff | 同长不同内容 | Boolean-blind、IDOR | 比特定内容元素 |
| Header 变 | 新 / 缺 header | CRLF 注入、代码路径变 | 查注入 header |
| Redirect 变 | 不同 Location header | Open redirect、auth 绕 | 查 Location、测 Collaborator |

**决策流：**
- Score >= 50：可能真，快验后确认
- Score 30-49：可疑，跑全调查（见 investigate skill）
- Score < 30：大概噪声，最多 5 工具调后移
- Collaborator 交互：总真，立即记录

---

## Part 4：跨分析工作流

### 工作流 1：JS Secrets → 已验证访问
静态：找 API key → 动态：测 key → 评权限 → 定 severity

### 工作流 2：DOM Sinks → 可利用 XSS
静态：找 sink+source → 读 JS 验流 → 动态：经 source 注入 → 验执行

### 工作流 3：隐藏端点 → Auth 绕过
静态：JS 中找 /api/v2/admin → 动态：未认证测 → 动态：低权用户测

### 工作流 4：页面变更 → 新攻击表面
动态：freshness 检查显变 → 重爬 → Diff 端点 → 析新的 → 探

### 工作流 5：行为画像 → 逻辑 bug
动态：用不同值画像 → 静态：查 JS 校验 → 动态：直绕校验

### 工作流 6：多页状态分析
动态：映射 checkout 流 → 测：跳步、replay、改值 → 测：coupon race condition

---

## Part 5：分析用代理派发

**派 js-analyst（后台）：**
> 为 {domain} 析全 JS 文件。Session：{session}。
> 每：extract_js_secrets + analyze_dom。
> 搜 history 中响应 body 的 "apiKey"、"secret"、"config"、"admin"。
> 返：secrets、DOM XSS 流、隐藏端点。

**派 recon-agent（后台）：**
> 为 {domain} 画像行为模式。Session：{session}。
> 测一致性（3 相同请求）。测 auth 差分。
> 返：行为画像、IDOR 候选。

**编排者（前台）：**
> 合并结果。立即验 secrets。测顶级 DOM XSS 流。深挖 IDOR 候选。

---

## 速查：工具选择

| 我想... | 用 |
|---|---|
| 列全 JS 文件 | `fetch_page_resources(url=<page>)` |
| 取一 JS 文件 | `fetch_resource(url)` |
| 取页全资源 | `fetch_page_resources(index)` |
| 扫 secrets | `extract_js_secrets(index)` |
| 找 DOM sinks/sources | `analyze_dom(index)` |
| 从 JS 抽端点 | `extract_api_endpoints(index)` |
| 单页全分析 | `smart_analyze(index)` |
| 比两响应 | `compare_responses(i1, i2)` |
| 快 diff | `get_response_diff(i1, i2)` |
| Auth 差分 | `compare_auth_states(index, ...)` |
| 多 auth 矩阵 | `test_auth_matrix(endpoints, states)` |
| 搜 history 中模式 | `search_history(query, in_response_body=True)` |
| 指纹页面 | `save_target_intel(domain, "fingerprint", ...)` |
| 查新鲜度 | `check_target_freshness(domain, session)` |
| 读全响应 | `get_request_detail(index, full_body=True)` |
