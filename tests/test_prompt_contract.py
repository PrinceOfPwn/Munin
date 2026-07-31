"""Regression tests for Munin's multilingual prompt contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from munin.core.prompting import (
    coordinator_runtime_prompt,
    model_family,
    subagent_runtime_prompt,
)


@pytest.mark.parametrize(
    ("model_name", "expected"),
    [
        ("glm-5", "GLM"),
        ("GLM-5.2-Air", "GLM"),
        ("mimo-v2-flash", "MiMo"),
        ("mimo-v2.5-free", "MiMo"),
        ("Qwen3-32B", "Qwen"),
        ("deepseek-chat", "DeepSeek"),
        ("moonshot-v1", "Kimi"),
        ("Yi-Large", "Yi"),
        ("custom-agent-model", "OpenAI-compatible"),
    ],
)
def test_model_family_profiles(model_name: str, expected: str) -> None:
    assert model_family(model_name) == expected


def test_coordinator_contract_is_chinese_first_and_operator_adaptive() -> None:
    prompt = coordinator_runtime_prompt("glm-5", "es")

    assert "Model family: `GLM`" in prompt
    assert "Operator language preference: `es`" in prompt
    assert "内部任务分解" in prompt
    assert "代码、文件名、标识符" in prompt
    assert "最近一条操作者消息的语言" in prompt
    assert "不向操作者、Discord 或其他代理泄露隐藏思维链" in prompt


def test_coordinator_contract_teaches_hugin_and_same_run_extension() -> None:
    prompt = coordinator_runtime_prompt("qwen3-32b")

    assert "`hugin_plan_for`" in prompt
    assert "`hugin_node_detail`" in prompt
    assert "最多调用一次 `hugin_refresh`" in prompt
    assert "Forge 成功后在下一次迭代刷新目录" in prompt
    assert "不能停在“tool created”" in prompt
    assert "Apache 2.4.49 authorized lab assessment" in prompt


def test_subagent_contract_requires_chinese_handoff_and_english_artifacts() -> None:
    prompt = subagent_runtime_prompt("web-correlation", "specialist")

    assert "代理间消息和交接使用简体中文" in prompt
    assert "JSON keys、代码、文件名" in prompt
    assert "`post_agent_message` 向 `munin` 汇报" in prompt
    assert "不得无限轮询" in prompt


def test_native_subagent_inherits_runtime_contract(store) -> None:
    from munin.subagents.base import ReActSubagentBase

    class CapturingLLM:
        messages: list[dict] = []

        def chat(self, *, messages, tools, temperature):
            self.messages = list(messages)
            return {"choices": [{"message": {"role": "assistant", "content": "完成"}}]}

    class TestSubagent(ReActSubagentBase):
        name = "prompt_test"
        role = "test_specialist"
        system_prompt = "Specific role contract."
        allowed_tools = set()
        max_iterations = 1

    llm = CapturingLLM()
    result = TestSubagent(store, llm=llm).handle_task({"prompt": "执行测试"})
    system = llm.messages[0]["content"]

    assert result["ok"] is True
    assert "Specific role contract." in system
    assert "子代理运行时合同" in system
    assert "Agent: `prompt_test`" in system


def test_forge_prompts_keep_machine_artifacts_english() -> None:
    from munin.subagents.graph_forge import _SYSTEM_PROMPT as graph_prompt
    from munin.subagents.tool_forge import _SYSTEM_PROMPT as tool_prompt

    assert "`system_prompt` 使用简体中文" in graph_prompt
    assert "输入 `tool_whitelist` 是起始建议，不是上限" in graph_prompt
    assert "增加必要的已注册" in graph_prompt

    assert "Python code、docstring、comments、errors" in tool_prompt
    assert "两个关键词相似不代表同一工具" in tool_prompt
    assert "`function_name=\"summarize_ldap_entries_by_ou\"`" in tool_prompt


def test_graph_forge_can_expand_requested_whitelist(store) -> None:
    import json

    from munin.subagents.graph_forge import GraphForgeSubagent

    class ExpandingLLM:
        def chat(self, *, messages, temperature):
            payload = {
                "name": "renamed-by-model",
                "purpose": "Correlate evidence",
                "system_prompt": "使用允许的工具完成任务。",
                "tool_whitelist": [
                    "memory_recall",
                    "hugin_plan_for",
                    "memory_list",
                ],
            }
            return {"choices": [{"message": {"content": json.dumps(payload)}}]}

    result = GraphForgeSubagent(store, llm=ExpandingLLM()).forge(
        name="evidence-correlator",
        purpose="Correlate evidence",
        hints=[],
        tool_whitelist=["memory_recall", "hugin_plan_for"],
    )

    assert result["ok"] is True
    assert result["name"] == "evidence-correlator"
    assert result["tool_whitelist"] == [
        "memory_recall",
        "hugin_plan_for",
        "memory_list",
    ]


def test_soul_is_campaign_wide_not_ldap_only() -> None:
    root = Path(__file__).resolve().parents[1] / "soul"
    identity = (root / "identity.md").read_text(encoding="utf-8")
    goals = (root / "goals.md").read_text(encoding="utf-8")
    principles = (root / "principles.md").read_text(encoding="utf-8")

    assert "**Hugin** 是“思想之鸦”" in identity
    assert "LDAP 是重要能力" in goals
    assert "但不是唯一中心" in goals
    assert "Campaign loop" in principles
    assert "Hugin：思想兄弟" in principles


def test_operator_language_env_is_configurable(isolated_workspace, monkeypatch) -> None:
    from munin.mcp.config import get_settings

    monkeypatch.setenv("MUNIN_OPERATOR_LANGUAGE", "pt-BR")
    assert get_settings().operator_language == "pt-BR"
