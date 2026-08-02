# tags: [tool-forge, subagent, workflow, langgraph, orchestrator, runtime, core, hitl-approval, coordination, GraphForgeSubagent, generated_graphs, emit_forge_stage, forge_propose, forge_completed, tool_whitelist]
"""Graph-forge subagent — refines a natural-language spec into a ReAct subagent config.

Produces (name, purpose, system_prompt, tool_whitelist) that gets persisted in
``generated_graphs``. The subagent runner reads that row at wake-time and builds a
``create_react_agent`` on the fly using the operator's LLM and the whitelisted MCP tools.

v3.1.1 hardening — canonical forge stages
-----------------------------------------
When ``store`` + ``run_id`` are passed at construction time (the production
dispatcher does; MCP callers may not), the graph-forge lifecycle also emits
canonical ``forge_*`` reasoning events for the UI floating window.  Passing
``store=None`` is a no-op, so the base behaviour is untouched for legacy
callers and unit tests.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..core.llm_client import LLMClient
from ..mcp.shared_state import SharedStateStore

logger = logging.getLogger("munin.graph_forge")

_SYSTEM_PROMPT = """你是 Munin 的 Graph-Forge 子代理。输入包含 NAME、PURPOSE、
HINTS 和 TOOL WHITELIST。你的唯一任务是生成可持久化、可审计、可终止的 ReAct
子代理配置。

## 生成规则
- `name`, `purpose`, JSON keys、tool names 保持英文；`system_prompt` 使用简体中文。
- 输入 `tool_whitelist` 是起始建议，不是上限。可以为实现 PURPOSE 增加必要的已注册
  tools，也可以删除无关 tools；最终 whitelist 应最小、完整并可实际执行。
- prompt 必须说明：角色、单一目标、范围、输入、工作流、证据标准、停止条件和中文交接格式。
- 代理间 `post_agent_message` 使用简体中文；代码、JSON keys、工具参数和查询语法使用英文。
- 禁止泄露隐藏思维链；只交付目标、动作、证据、风险、未知项和下一步。
- 始终包含：只使用 whitelist；不清楚范围时通过 `post_agent_message` 请求父代理；
  重要发现用 `publish_shared_intel`（仅当 whitelist 中存在）；完成后必须向父代理交接。
- whitelist 含 `ldap_search` 时，强制使用 `filter_template` + `params_json`，禁止拼接 filter。
- whitelist 含 Hugin tools 时，定义其角色为候选知识/关系证据：
  查询用英文安全术语，分析用中文，结果不构成授权；refresh 最多一次。
- whitelist 含 active tool 时，要求明确 target 与 active authorization，并尊重 OPSEC failure。
- system prompt 必须让代理在得到 evidence-backed result 或精确 blocker 后结束，禁止无限轮询。

## Few-shot shape
输入 purpose: `Correlate LDAP service identities with exposed web services`
输入 whitelist: `["memory_recall","hugin_plan_for","ldap_search","nmap_scan",
"publish_shared_intel","post_agent_message"]`
正确输出中的 system_prompt 应表达以下动作链：
`memory_recall` → `hugin_plan_for` → 验证 scope → 最小 LDAP/web 查询 →
交叉验证 → `publish_shared_intel` → 中文 `post_agent_message` → stop。
如果起始 whitelist 缺少完成目标所需的 Hugin detail、memory 或 messaging tool，
应补充对应的已注册 tool；不要添加与 PURPOSE 无关的能力。

只回复一个有效 JSON object，不要 markdown fence 或额外 prose：
{
  "name": "<english-kebab-case>",
  "purpose": "<one-line English purpose>",
  "system_prompt": "<multiline Simplified Chinese prompt>",
  "tool_whitelist": ["<exact-tool-name>"]
}
"""


def _execution_contract(tool_whitelist: list[str]) -> dict[str, Any]:
    """Give every forged graph the same observable, evidence-first shape."""
    contexts = ["semantic_memory", "shared_intel"]
    if {
        "hugin_search",
        "hugin_neighbors",
        "hugin_refresh",
        "hugin_rag_search",
        "hugin_plan_for",
        "hugin_node_detail",
    } & set(tool_whitelist):
        contexts.append("hugin_cached_graph")
    return {
        "version": 1,
        "mode": "evidence_mesh",
        "context_sources": contexts,
        "human_checkpoints": [
            "scope_unclear",
            "before_active_or_irreversible_action",
            "before_final_publication",
        ],
        "delivery": {
            "format": "markdown",
            "sections": ["Summary", "Evidence", "Next steps"],
            "post_to_parent": True,
        },
        "termination": {
            "required": "Return a final evidence-backed answer after the assigned objective is complete; do not poll indefinitely.",
            "max_iterations": 8,
        },
    }


class GraphForgeSubagent:
    """Compile a natural-language spec into a persistable subagent config.

    v3.1.1 kwargs (all backwards-compatible):

    * ``store`` — a ``ProductionStore`` with the v3.1 extensions installed.
      Enables canonical ``forge_*`` reasoning-event emission.
    * ``run_id`` — the run to attribute forge events to; required when
      ``store`` is set.
    * ``agent_name`` — AgentProfile id used as the floating-window key.
      Defaults to ``"graph-engineer"``.
    """

    def __init__(
        self,
        state: SharedStateStore,
        llm: LLMClient | None = None,
        *,
        store: Any = None,
        run_id: str = "",
        agent_name: str = "graph-engineer",
    ) -> None:
        self.state = state
        self.llm = llm or LLMClient(state.settings)
        self.store = store
        self.run_id = run_id
        self.agent_name = agent_name

    def _emit_forge(
        self,
        stage: str,
        message: str,
        *,
        step: int = 0,
        **extra: Any,
    ) -> None:
        if self.store is None or not self.run_id:
            return
        try:
            from ..production.forge_progress import emit_forge_stage

            emit_forge_stage(
                self.store,
                run_id=self.run_id,
                agent_name=self.agent_name,
                stage=stage,
                message=message,
                step=step,
                **extra,
            )
        except Exception:
            logger.debug("emit_forge_stage failed for stage %s", stage, exc_info=True)

    def forge(
        self,
        *,
        name: str,
        purpose: str,
        hints: list[str],
        tool_whitelist: list[str],
    ) -> dict[str, Any]:
        self._emit_forge(
            "forge_propose",
            f"Composing graph subagent '{name}'",
            step=1,
            purpose=purpose,
            tool_whitelist=tool_whitelist,
        )
        user_prompt = json.dumps(
            {"name": name, "purpose": purpose, "hints": hints, "tool_whitelist": tool_whitelist},
            ensure_ascii=True,
        )
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        try:
            completion = self.llm.chat(messages=messages, temperature=0.2)
        except Exception as exc:
            self._emit_forge(
                "forge_failed",
                f"LLM call failed: {exc}",
                step=1,
                error_code="llm_failed",
            )
            return {"ok": False, "summary": "LLM failed", "error": {"code": "llm_failed", "message": str(exc)}}
        content = completion["choices"][0]["message"]["content"] or ""
        self._emit_forge(
            "forge_diff_ready",
            "Received subagent config draft; validating fields",
            step=1,
            content_bytes=len(content),
        )
        self._emit_forge(
            "forge_typecheck_start",
            "Parsing JSON + verifying required fields",
            step=1,
        )
        try:
            payload = json.loads(content.strip().strip("`").replace("json\n", "", 1))
        except Exception:
            self._emit_forge(
                "forge_typecheck_done",
                "Draft was not valid JSON",
                step=1,
                ok=False,
            )
            self._emit_forge(
                "forge_failed",
                "Graph subagent draft was not valid JSON",
                step=1,
                error_code="bad_json",
            )
            return {"ok": False, "summary": "bad JSON reply", "error": {"code": "bad_json", "message": content[:400]}}
        if not payload.get("system_prompt"):
            self._emit_forge(
                "forge_typecheck_done",
                "Draft missing required `system_prompt`",
                step=1,
                ok=False,
            )
            self._emit_forge(
                "forge_failed",
                "Graph subagent draft missing required `system_prompt`",
                step=1,
                error_code="bad_reply",
            )
            return {"ok": False, "summary": "missing system_prompt", "error": {"code": "bad_reply", "message": "no system_prompt"}}
        self._emit_forge(
            "forge_typecheck_done",
            "Draft passed required-field checks",
            step=1,
            ok=True,
        )
        requested_tools = list(dict.fromkeys(tool_whitelist))
        returned_tools = payload.get("tool_whitelist")
        if not isinstance(returned_tools, list):
            returned_tools = requested_tools
        effective_tools = list(
            dict.fromkeys(
                tool.strip()
                for tool in returned_tools
                if isinstance(tool, str) and tool.strip()
            )
        )
        if not effective_tools:
            effective_tools = requested_tools
        self.state.episodic_record(
            agent="graph_forge",
            action="forge_success",
            input_data={"name": name, "purpose": purpose, "hints": hints, "tool_whitelist": tool_whitelist},
            output_data={"name": name, "tool_whitelist": effective_tools},
            tags=["forge", "graph"],
        )
        contract = _execution_contract(effective_tools)
        self._emit_forge(
            "forge_awaiting_approval",
            f"Graph subagent '{name}' ready — awaiting operator approval",
            step=1,
            name=name,
            tool_whitelist=effective_tools,
        )
        self._emit_forge(
            "forge_completed",
            f"Graph subagent '{name}' compiled",
            step=1,
            name=name,
            tool_whitelist=effective_tools,
        )
        return {
            "ok": True,
            "summary": f"graph forged: {name}",
            "name": name,
            "purpose": str(payload.get("purpose") or purpose),
            "system_prompt": payload["system_prompt"],
            "tool_whitelist": effective_tools,
            "execution_contract": contract,
        }
