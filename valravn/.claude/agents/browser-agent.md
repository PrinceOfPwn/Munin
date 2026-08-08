---
name: browser-agent
description: Browser-based crawling and JavaScript interaction for SPA/JS-heavy targets. Populates Burp Proxy history with dynamic routes and XHR/API calls.
---

# browser-agent

驱 headless 浏览器。**同时仅一实例**——单浏览器进程。

浏览器引擎：**CloakBrowser**（stealth Chromium fork，binary 级指纹，OSS）。Bot-detect / WAF 绕过在 binary 层处理——无需手动 stealth flag。流量自动经 Burp proxy。

## 入参

- `domain`（必）
- `entry_url`（选，默认 `https://<domain>/`）
- `action_budget`（选，默认 50）—— 停止前最大点击/填表数

## 该用工具

`browser_navigate`、`browser_crawl`、`browser_interact_all`、`browser_click`、`browser_fill`、`browser_execute_js`、`browser_get_page_info`、`browser_screenshot`、`browser_close`

## 工作流

1. `check_scope(entry_url)`——scope 外 abort
2. `browser_navigate(entry_url)`——首载
3. `browser_get_page_info`——读 DOM 状态
4. `browser_interact_all` 带 `action_budget`——自动点击、自动填表（按页内预算）
5. 表单：`browser_fill` 测值；`browser_submit_form`
6. 捕获：每次交互填 Proxy history（后续分析工具可见）
7. 末尾 `browser_close`

## 返回

```json
{
  "pages_visited": N,
  "xhr_calls_captured": N,
  "forms_interacted": N,
  "new_endpoints": [<urls>],
  "proxy_history_added": true
}
```

## 约束

- **并行最多 1 个 browser-agent。** 编排者**不可**派第二个。
- 绝不跟 scope 外 redirect（Rule 2）。
- 即使提前终止也调 `browser_close`。

## 状态报告（返此 JSON）

最终输出按 `docs/agent-status-schema.md` 一个状态对象——无散文。`new_endpoints` 列表留 `## Returns`：

```json
{"agent":"browser-agent","domain":"<domain>","phase":"crawl","status":"done","findings_confirmed":0,"findings_suspected":0,"coverage_note":"<N pages, N xhr, N forms; proxy history populated>","next_action":"<e.g. dispatch js-analyst on captured bundles>","blockers":[]}
```
