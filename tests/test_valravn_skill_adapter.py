"""Valravn's flat Claude skills are exposed as native Munin skill packages."""
from __future__ import annotations

from pathlib import Path

from munin.core.autonomy.skill_library import bundled_skill_library


def _valravn_source_names() -> set[str]:
    source_root = (
        Path(__file__).resolve().parents[1]
        / "valravn"
        / ".claude"
        / "skills"
    )
    return {path.stem for path in source_root.glob("*.md")}


def test_all_valravn_skills_are_available_through_munin():
    library = bundled_skill_library()
    source_names = _valravn_source_names()

    assert source_names
    adapted_names = {
        name.removeprefix("valravn-")
        for name in library.available()
        if name.startswith("valravn-")
    }
    assert adapted_names == source_names
    assert library.validation_errors() == ()


def test_valravn_adapter_rewrites_identity_and_sibling_links():
    library = bundled_skill_library()
    rendered_root = library._rendered_root()

    skill = (
        rendered_root
        / "valravn-burp-workflow"
        / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert skill.startswith("---\nname: valravn-burp-workflow\n")
    assert "/valravn-evidence-and-tabs/SKILL.md" in skill
