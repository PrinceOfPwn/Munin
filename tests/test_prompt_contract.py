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
        ("deepseek-v4-flash", "DeepSeek"),
        ("deepseek-v4-pro", "DeepSeek"),
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
    """Soul must read as a campaign agent, not an LDAP-only specialist.

    The regression this test was written for is still real: the soul must not
    hypertrophy around one capability (LDAP) or cite maintainer-facing
    infrastructure (Turso / GitHub Actions / pytest). The earlier version of
    this test asserted the literal phrases "LDAP 是重要能力" and "但不是唯一中心"
    as a *negative* guard — i.e. they were the tell that an author was trying
    to over-balance against an LDAP-only soul. Once the soul was properly
    rebuilt, those phrases stopped needing to appear, and the guard evolved to
    check for the campaign-wide shape directly.
    """
    root = Path(__file__).resolve().parents[1] / "soul"
    identity = (root / "identity.md").read_text(encoding="utf-8")
    goals = (root / "goals.md").read_text(encoding="utf-8")
    principles = (root / "principles.md").read_text(encoding="utf-8")
    skills = (root / "skills.md").read_text(encoding="utf-8")
    valravn = (root / "valravn.md").read_text(encoding="utf-8")

    # Identity must frame Hugin as the knowledge brother, not generic encyclo.
    assert "**Hugin**" in identity and "思想之鸦" in identity

    # Goals must be operational excellence, not a product roadmap. The soul
    # must not demand maintainer-facing infrastructure as success criteria.
    assert "战役" in goals
    for forbidden in ("Turso online", "GitHub Actions", "pytest tests", "munin reset"):
        assert forbidden not in goals, f"maintainer-facing phrase leaked into goals: {forbidden!r}"
    # The old LDAP-balance phrases were a hack around a hypertrophied soul;
    # the rebuilt soul should neither need them nor center LDAP.
    assert "LDAP 是重要能力" not in goals
    assert "但不是唯一中心" not in goals

    # Principles keep the immutable campaign loop and the Hugin section.
    assert "战役循环" in principles
    assert "Hugin" in principles

    # Skills must surface the operator-facing chat portal and the idiomatic
    # in-process delegation surface (Deep Agents Autonomy Kernel meta-tools).
    assert "munin_chat" in skills
    assert "create_subagent" in skills
    assert "schedule_workers" in skills
    # The nonexistent hardcoded default subagent must not be re-introduced.
    assert "ldap_agent" not in skills

    # Valravn stays operational only — no provider TOS / quota noise.
    assert "valravn_investigate_ioc" in valravn
    for forbidden in ("Safe Browsing", "FullHunt opt-in", "配额"):
        assert forbidden not in valravn, f"operator / maintainer phrase leaked into valravn: {forbidden!r}"


def test_operator_language_env_is_configurable(isolated_workspace, monkeypatch) -> None:
    from munin.mcp.config import get_settings

    monkeypatch.setenv("MUNIN_OPERATOR_LANGUAGE", "pt-BR")
    assert get_settings().operator_language == "pt-BR"


def test_soul_load_order_goals_first_and_kernel_separate(tmp_path) -> None:
    """Soul persona loads in deliberate order; README and kernel stay out.

    The identity preamble opens the prompt, then the files follow the
    canonical sequence goals → identity → principles → skills → valravn.
    ``README.md`` is documentation and must never be injected, and
    ``kernel.md`` is a separate instruction block, not part of the persona.
    """
    from munin.core.soul import SoulManager

    soul = tmp_path / "soul"
    soul.mkdir()
    (soul / "README.md").write_text("# docs for humans", encoding="utf-8")
    (soul / "kernel.md").write_text("KERNEL_BLOCK", encoding="utf-8")
    for name, content in {
        "valravn.md": "VALRAVN",
        "goals.md": "GOALS",
        "identity.md": "IDENTITY",
        "skills.md": "SKILLS",
        "principles.md": "PRINCIPLES",
    }.items():
        (soul / name).write_text(content, encoding="utf-8")

    manager = SoulManager(soul, tmp_path / "data")

    names = [str(p.relative_to(soul)) for p in manager.files()]
    assert names == ["goals.md", "identity.md", "principles.md", "skills.md", "valravn.md"]

    prompt = manager.as_system_prompt()
    # Canonical order in the assembled prompt.
    assert prompt.index("soul/goals.md") < prompt.index("soul/identity.md")
    assert prompt.index("soul/identity.md") < prompt.index("soul/principles.md")
    assert prompt.index("soul/principles.md") < prompt.index("soul/skills.md")
    assert prompt.index("soul/skills.md") < prompt.index("soul/valravn.md")
    # README and kernel must never be injected into the persona.
    assert "soul/README.md" not in prompt
    assert "KERNEL_BLOCK" not in prompt
    # Kernel instructions are a separate block.
    assert manager.kernel_instructions() == "KERNEL_BLOCK"

    # Snapshot includes kernel.md; restore round-trips both persona and kernel.
    report = manager.snapshot()
    assert "kernel.md" in report["files"]
    restored = manager.restore()
    assert "kernel.md" in restored["restored"]
    assert manager.kernel_instructions() == "KERNEL_BLOCK"


def test_soul_preamble_opens_with_identity_and_war_raven(tmp_path) -> None:
    """The preamble characterizes Munin as the war-raven before any file loads."""
    from munin.core.soul import SoulManager

    soul = tmp_path / "soul"
    soul.mkdir()
    manager = SoulManager(soul, tmp_path / "data")
    prompt = manager.as_system_prompt()
    assert prompt.startswith("你是 Munin——战争之鸦")
    assert "命令即授权" in prompt
    assert "Пусть мир горит" in prompt
