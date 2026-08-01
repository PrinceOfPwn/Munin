"""Native Deep Agents skill wiring stays explicit and read-only."""
from __future__ import annotations

import pytest

from munin.core.autonomy.skill_library import bundled_skill_library
from munin.core.autonomy.spec import SubagentSpec
from munin.core.autonomy.subagent_factory import SubagentFactory


def test_bundled_skill_library_exposes_a_direct_deepagents_source():
    binding = bundled_skill_library().bind(["hugin-research"])

    assert binding is not None
    assert binding.names == ("hugin-research",)
    assert binding.sources == ["/"]
    entries = binding.backend.ls("/").entries or []
    assert any(item["path"].rstrip("/") == "/hugin-research" for item in entries)
    assert binding.backend.download_files(["/hugin-research/SKILL.md"])[0].error is None
    assert binding.permissions[0].mode == "deny"
    assert binding.permissions[0].operations == ["write"]


def test_bundled_skill_library_rejects_arbitrary_paths():
    with pytest.raises(ValueError, match="Unknown bundled skill"):
        bundled_skill_library().bind(["../../unreviewed-prompt-tree"])


def test_bundled_skill_library_discovers_only_direct_child_packages(tmp_path):
    approved = tmp_path / "approved-skill"
    approved.mkdir()
    (approved / "SKILL.md").write_text("---\nname: approved-skill\ndescription: test\n---\n", encoding="utf-8")
    nested = tmp_path / "category" / "not-auto-mounted"
    nested.mkdir(parents=True)
    (nested / "SKILL.md").write_text("---\nname: not-auto-mounted\ndescription: test\n---\n", encoding="utf-8")

    from munin.core.autonomy.skill_library import BundledSkillLibrary

    library = BundledSkillLibrary(root=tmp_path)

    assert library.available() == ("approved-skill",)
    assert library.bind(["approved-skill"]) is not None
    with pytest.raises(ValueError, match="Unknown bundled skill"):
        library.bind(["not-auto-mounted"])


def test_deep_agent_forwards_explicit_skills_to_native_runtime(monkeypatch):
    import deepagents

    captured = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)
    factory = SubagentFactory(tools=[], model=object())
    result = factory.create_subagent(
        SubagentSpec(
            name="hugin_librarian",
            purpose="retrieve provenance-labelled Hugin evidence",
            runtime_type="deep_agent",
            skills=["hugin-research"],
        )
    )

    assert result is not None
    assert captured["skills"] == ["/"]
    assert captured["backend"].cwd.name == "agent_skills"
    assert captured["permissions"][0].mode == "deny"


def test_persisted_subagent_declares_its_own_skill_source():
    factory = SubagentFactory(tools=[], model=object())
    agent = factory.create_subagent(
        SubagentSpec(
            name="hugin_librarian",
            purpose="retrieve provenance-labelled Hugin evidence",
            runtime_type="persisted_subagent_dict",
            skills=["hugin-research"],
        )
    )

    assert agent["skills"] == ["/"]


def test_supervisor_uses_the_native_skills_arguments(monkeypatch):
    import deepagents

    from munin.core.supervisor import build_supervisor

    captured = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)
    result = build_supervisor(tools=[], model=object(), checkpointer=False)

    assert result is not None
    assert captured["skills"] == ["/"]
    assert captured["backend"].cwd.name == "agent_skills"
    assert captured["permissions"][0].mode == "deny"
