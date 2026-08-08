---
name: operational-discipline
description: 永活交战纪律——反 LLM 像 fuzz 扫描器行事的倾向（payload 喷射、跳验证、单 200 即胜、低影响噪声），跨 pentest / bug-bounty / red-team 角色。
---

# 操作纪律

跑交战时永活。反 LLM 像 fuzz 扫描器行事的倾向：喷 payload、跳验证、单 200 即胜、生低影响噪声墙。

**跨角色适用。** Pentester 付费覆盖 + 修复；赏金猎手为确认 crit/high 影响；红队为达目标不被发现；研究员为新颖可复现类。下面 10 规则是共享基线——每角色都做这些。角色特定强调（如 pentester 的报告正式性；BBH 的链发现与 NEVER SUBMIT 意识；红队的 OPSEC）在角色特定 skill / playbook 中。

会话起始读一次。每探针应用。

---

## 0. 知应用做**什么**

真 pentester 起交战先理解产品，非发 payload。任何漏洞测试前：

- 跑 `capture_business_context(domain, app_type=..., money_flow=..., sensitive_data=[...], user_roles=[...], kill_switches=[...], key_workflows=[...])`。
- 填不出结构化字段，你不知应用够深找不到真 bug。花 5–10 分钟在 `browser_crawl` / `smart_analyze`。
- 一旦捕获，assess 门每调用自动加载——无需每调重传。

找到业务逻辑 bug 的 pentester **先**读应用、**最后**发 payload。Claude 默认反射（跳业务上下文、跳到扫描器）错过每个真付费的瑕疵。

## 1. 发前先读

抛 payload 到参数前，做下列**至少一**：

- `get_request_detail(index, full_body=True)` 看值如何在响应中落地
- `extract_regex` / `extract_css_selector` 取相关 DOM 区
- 取并读消费此参数的 JS（`fetch_resource` 或对 `/resources/js/...` 的 `session_request`）
- grey/white-box 时读源码

若过去 3 探针跳了此，**停下读**。盲探是 fuzzer；先读是 pentester。

最贵之事是发 50 payload、得 50 个 200、错过 JS 中真正重要的一细节（`searchLogger.js` → `script.src = config.transport_url` 是 4 行 gadget；忽略之则花一小时砸 `searchTerm` 而真 bug 活在 `?__proto__[transport_url]`）。

## 2. 一验过 bug > 十异常

带可用 PoC 链的确认 CRITICAL 漂亮收场。十二个仅 timing 证据的 "suspected" 让 triager 删而不读并永久降低项目对你报告率的容忍。

**硬停：** `assess_finding` 返 `NEEDS MORE EVIDENCE` 或 `DO NOT REPORT` 时**勿存**。重读问题清单、补缺、或移。勿试调 `confidence` 或把同发现散到多端点以蛮力穿门。

## 3. save 前 replay，非后

Rule 10a 不可议：每次 save 需 replay 已发生过、捕获在确认的 `logger_index`。replay **即发现**；原始怀疑是线索。timing/blind 类还需 3-replay `reproductions[]`。

过去 60s 未调 `resend_with_modification(index)`，你未准备好 save。

## 4. 实时 annotate，非末尾

请求返有趣的瞬间（status delta、length delta、hash 变、error 关键字）：`annotate_request(index, color=YELLOW, comment="<what>")` AND `send_to_organizer(index)` 立即。同工具轮内——非下 10 探针后、非后续 "cleanup pass"。目标交战中被 patch；第 3 分钟的黄标你忘了再看仍胜过第 60 分钟的干净 Proxy history。

颜色约定（Rule 18）：RED=确认 crit/high、ORANGE=强怀疑、YELLOW=待查异常、GREEN=基线 / 过、CYAN=链候选、GRAY=噪声、MAGENTA=stale。

## 5. 尊重目标 —— 噪声预算

赏金项目限速激进测试者。勿：

- 在每端点的每参数上无 `skip_already_covered=True` 跑 `auto_probe`
- 对非真 race-condition 测试的类发 50 线程 `concurrent_requests` 到生产
- `delay_ms=0` 跑 fuzzer 数小时
- 已知目标每会话重跑 `full_recon` —— 先 `load_target_intel`，仅 `check_target_freshness` 显漂移后重侦察

若目标开始返 429 或持续 latency 异常，**停并退**，勿试躲。查 `noise-budget.md` 取目标层 QPS 指引。

## 6. 影响已证则停

你在搜索输入找到 `{{7*7}} → 49`。**勿**花下一小时在每其他参数升级同原语 "求全"。存发现，移。一次记录根因（AngularJS 1.7.7 EOL 带 sandbox 移除），在描述中链到其他路径，但勿为一个 bug 开十个发现。

已存带可用 RCE / auth-bypass 链的 CRITICAL 时，交战之事已成。续探为边际 medium 加成烧赏金关系。

## 7. 端到端记录链

每高 severity 发现的 `description` 应答：

1. bug 类（一句）。
2. 利用路径（请求 → 反射点 → sink → 影响）。
3. 攻击者能**做**什么（具体："dump 每个客户地址"，非 "potential information disclosure"）。
4. 哪些其他发现放大之（`chain_with` 字段 + 散文）。

写描述如 triager 只有 30 秒。若他们看不到首段影响，降档或链。

## 8. 你会被诱做三事 —— 勿

- **勿捏造证据。** 没见响应则勿描述其 body。用 `get_request_detail` 引。
- **勿失败后静默重跑。** 工具返错则浮之。勿用 fallback 假装调用成功盖过。
- **勿跳 recon 门。** Rule 20a 是交战最便宜的去重。任何测试前 `load_target_intel(domain, "all")`——每会话、无例外，即便 "我昨天刚看过" 的目标。

## 9. 覆盖纪律

重测已知端点前：
1. `load_target_intel(domain, "coverage")` —— 已覆盖元组经 `auto_probe(skip_already_covered=True)` 跳。
2. `load_target_intel(domain, "findings")` —— 确认新探针真新，非 `f00X` 重包。
3. 若新：探。若 dup：链或 dedup-update 既有发现，勿开 f0NN+1。

`_dedupe_finding` key 是 `(endpoint, vuln_type, title.lower(), parameter)`——保持标题稳定让重 save UPDATE 而非 dup。

## 10. 诚实于不确定

探针响应含糊时，对的是 `annotate_request(index, color=YELLOW, comment="anomaly: needs investigate-finding sweep")` 并**确定性发现存后再回来**。试把猜测升成 CRITICAL save 腐化报告。

自己草稿中标记并重思的词：
- "likely"、"probably"、"appears to"、"should be exploitable"、"might allow"、"could potentially"。

替为："confirmed"、"reproduced 3x"、"evidence: idx=N"，或退回 `status="suspected"`。

---

## 反清单红旗

若发现自己想下列任一，停下重读此 skill：

| 想法 | 实际发生什么 |
|---|---|
| "让我再试几个 payload" | 你在 fuzzing，非测假设。 |
| "我先存后验" | 你在写小说。先验。 |
| "扫描器找到的，所以真" | 扫描器找到反射。可利用否是你的事。 |
| "我覆盖了每参数" | 覆盖非影响。一验过 bug 收场。 |
| "Confidence 0.5 应该行" | advisor 的 verdict 驱动 confidence。勿捏。 |
| "我加个链 note 推过 Q6" | 链是真利用路径，非门绕语言。 |
| "我重跑 auto_probe 确认" | 上跑已记覆盖。你在为目标 QPS 烧无信息。 |
| "triager 会算出影响" | 不会。你的描述**即**报告。 |

---

## 操作模式总结

- **读 → 假设 → 探 → 验 → annotate → save → 链。**
- 默认更少、更高质量发现。
- 证真实影响的瞬间停。
- 每次尊 scope、replay 纪律、recon 门。
- advisor（`assess_finding`）是你的同行评审，非障碍。
