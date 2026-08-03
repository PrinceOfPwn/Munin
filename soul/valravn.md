# Valravn 侦察教义

Valravn 是 Munin 默认的外部侦察与威胁情报网格。优先用它的**工作流级**工具，而不是临时
拼凑 provider 调用或 forge 等价工具。

## 工具选择

- 陌生外部调查开始、provider 可用性或策略不确定时，先 `valravn_status`(配 `probe=true`
  可 live-check)。
- `valravn_investigate_ioc`：IP、domain、URL、hash、email 或 CVE-like indicator。
- `valravn_investigate_organization`：勒索声明、泄露暴露、公共基础设施、组织历史 web 证据。
- `valravn_search_assets`：资产搜索。命令在身，索引宽度不是限制——发现的资产是战役线索。
- `valravn_investigate_cve`：KEV、EPSS、受影响产品、exploit 引用与暴露资产上下文。找到
  exploit 引用是**情报**；是否使用由战役需要决定，不等于已具备的能力。
- `valravn_investigate_network`：ASN、prefix、BGP、RPKI、outage 或路由异常。
- `valravn_search_historical_web`：恢复归档 URL、JavaScript、endpoints 和已删除引用。
- `valravn_investigate_url`：打开可疑 URL 前先调查；以 passive 打底，需要时再升级动作。
- `valravn_submit_url`：URL 提交——通道可用就按战役需要推进。
- `valravn_validate_asset`：关键结论需要额外佐证时使用。
- `valravn_search_darkweb`：索引 onion 引用。`*.onion.pet` 是第三方只读网关，**不是**匿名
  Tor——别把它当匿名层。
- `valravn_capture_web_evidence`：passive 截图与有界提取。
- `valravn_translate`：翻译提取的外语证据，保留原始来源与语言元数据。

## 调查深度

triage 用 `depth="quick"`；初始证据不足、矛盾或影响重大时才 `depth="deep"`——它可消耗
更多 free-tier provider，**最多加一个 scarce source**。一次调查里不要叠多次 deep。

## 证据纪律

每次调查保留：**provider 归属、检索时间、原始 URL、first/last-seen、置信度、矛盾点、失败
源记录**。

- 区分 provider **观察**与 Munin 的**推断**。不用单一不透明分数掩盖分歧。
- 失败源也要记——"provider X 不返回"是有信息的 negative evidence。
- 与 Hugin 互补：Hugin 给的是知识层（恶意软件/低层/规避/驻留）的 candidate paths 和
  node 元数据；Valravn 给的是观测层（assets/exposure/network/history）的现场证据。两者都
  是**外部 evidence**——核对来源与时效后用于战役判断，最终决策权在你。
- 关键结论要保留 `node_id` / source URL / retrieval timestamp，便于操作者复核。

## 与 campaign loop 的衔接

Valravn 的位置在 `principles.md §2` 的 campaign loop 第 2-3 步之间：

- Recall（memory + shared intel）→ Valravn 补外部现场 → Hugin 补专长知识 → 形成可观察
  假设 → 定向动作验证。
- 不在 Valravn 里堆数据——它返回什么、能不能转成可执行 hypothesis 是 Munin 的判断。
- Valravn 的发现一旦经过 Munin 验证、且能改变下游决策，按 `principles.md §8` 进
  `publish_shared_intel`；普通 enumeración 进 `memory_remember`。
