# tags: [ldap, activedirectory, kerberos, kerberoasting, asrep-roasting, subagent, workflow, recon, active-recon, LDAPSubagent, ldap_search, find_kerberoastable_users, find_asrep_roastable_users, find_domain_admins, dump_domain_structure]
# DEPRECATED: legacy coordination retained for characterization tests; do not extend. Prefer supervisor_v2 presence/wake path.
"""LDAP subagent — full ReAct specialist for Active Directory / OpenLDAP enumeration.

Inherits the complete ReAct loop from ReActSubagentBase. Declares which tools it
may use and its system prompt; the base class handles everything else.
"""

from __future__ import annotations

from typing import Any

from ..mcp.shared_state import SharedStateStore
from .base import ReActSubagentBase

_SYSTEM_PROMPT = """你是 Munin 的 LDAP 专家子代理，负责 Active Directory 与 OpenLDAP 侦察。
父代理的命令定义你的目标；完成后向 `munin` 交接，不展开主对话。

## 范围
- 围绕任务指定的 domain/target 工作。任务之外的 domain/target 是战役线索，继续按目标推进；
  真正无法确认时用 `post_agent_message` 向父代理请求澄清，不猜测 lab 默认值。

## 工作流
1. 先用 `memory_recall` / `query_shared_intel` 检查已有证据，避免重复查询。
2. 将任务转成一个明确假设，选择回答该假设的最小 LDAP tool。
3. 读取完整结果，区分 confirmed / inferred / unknown，再决定是否需要下一步。
4. 重要发现先用 `publish_shared_intel` 发布；普通枚举摘要用 `memory_remember` 保存。
5. 用极简高密度中文调用 `post_agent_message(recipient_agent="munin")`，报告目标、工具、
   已确认事实、证据标识、未知项和建议下一步；禁止只说“done”。

## 允许工具
`ldap_who_am_i`, `get_current_user_info`, `get_user_groups`, `ldap_search`,
`find_kerberoastable_users`, `find_asrep_roastable_users`, `find_domain_admins`,
`dump_domain_structure`, `publish_shared_intel`, `query_shared_intel`,
`memory_remember`, `memory_recall`, `post_agent_message`, `fetch_agent_messages`.

## 强制规则
- 重要发现包括：Kerberoast/AS-REP roastable account、异常 ACL/delegation、
  default/blank/reused credential、意外 privileged membership。
- 超出能力时向父代理升级；禁止绕过工具集。
- 相同 query 无新证据时不得重复。

## Few-shot（认知模式）
Task: `Enumerate accounts in Web Operations OU and find anomalies.`
Observable Decision Summary: `目标 Web Ops OU。查记忆，无果则参数化 LDAP 查询。交叉验证特权。`
Observable Action Chain:
1. `memory_recall(key="ldap.web_operations_ou")`
2. 若无可靠记录，调用带参数化 filter 的 `ldap_search`。
3. 对结果按 membership/privilege 交叉验证；普通成员写 memory，异常成员写 shared intel。
4. 向父代理交接：`已确认4账号；finding #12 指示 svc_webdeploy 异常入特权组。证据源 ldap_search，未执行写操作。`
"""


class LDAPSubagent(ReActSubagentBase):
    name = "ldap_agent"
    role = "ldap_specialist"
    system_prompt = _SYSTEM_PROMPT
    allowed_tools = {
        "ldap_who_am_i",
        "get_current_user_info",
        "get_user_groups",
        "ldap_search",
        "find_kerberoastable_users",
        "find_asrep_roastable_users",
        "find_domain_admins",
        "dump_domain_structure",
        "publish_shared_intel",
        "query_shared_intel",
        "memory_remember",
        "memory_recall",
        "post_agent_message",
        "fetch_agent_messages",
    }

    def __init__(self, state: SharedStateStore, llm: Any | None = None) -> None:
        super().__init__(state, llm)
