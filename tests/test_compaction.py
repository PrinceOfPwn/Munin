# tags: [tests, compaction, cti, deepagents, summarization, summary_prompt, red-team, checkpoint, ioc, provenance, orchestration]
"""Regression tests for the CTI-aware compaction prompt composer.

``munin.core.autonomy.compaction.compose_cti_summary_prompt`` inserts the
operator's ``<cti_compaction_rules>`` block into the Deep Agents default
summary prompt WITHOUT replacing it — the framework's ``{messages}``
load-bearing placeholder and media-reference contract must survive intact.

These tests run offline: they exercise the composer logic and the rules text
loaded from the plain-text file, but they stub ``DEEPAGENTS_DEFAULT_SUMMARY_PROMPT``
so the tests do not require the ``deepagents`` wheel to be importable (the
live runner has it; this dev host may not).
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Stub the deepagents summarization module so the composer can be exercised
# on a host that does not have the wheel installed (dev host without poetry).
# ---------------------------------------------------------------------------


def _install_deepagents_stub(monkeypatch: pytest.MonkeyPatch, prompt: str) -> None:
    pkg = types.ModuleType("deepagents")
    mw = types.ModuleType("deepagents.middleware")
    summ = types.ModuleType("deepagents.middleware.summarization")
    summ.DEEPAGENTS_DEFAULT_SUMMARY_PROMPT = prompt
    mw.summarization = summ
    pkg.middleware = mw
    monkeypatch.setitem(sys.modules, "deepagents", pkg)
    monkeypatch.setitem(sys.modules, "deepagents.middleware", mw)
    monkeypatch.setitem(sys.modules, "deepagents.middleware.summarization", summ)


# ---------------------------------------------------------------------------
# compose_cti_summary_prompt
# ---------------------------------------------------------------------------


def test_compose_inserts_rules_before_messages_when_sentinel_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default behaviour: rules spliced in right before the <messages> sentinel
    and the {messages} placeholder survives intact."""
    default_prompt = (
        "You are a summarizer.\n"
        "<media_reference_information>\nmedia refs here\n</media_reference_information>\n"
        "<messages>\n{messages}\n</messages>\n"
    )
    _install_deepagents_stub(monkeypatch, prompt=default_prompt)

    # Force a re-import of the composer so it picks up the stubbed module.
    for mod in list(sys.modules):
        if mod.endswith(".compaction") or mod == "munin.core.autonomy.compaction":
            sys.modules.pop(mod, None)
    from munin.core.autonomy.compaction import compose_cti_summary_prompt

    out = compose_cti_summary_prompt()
    assert out is not None
    assert "{messages}" in out, "the {messages} load-bearing placeholder must survive"
    assert "<cti_compaction_rules>" in out, "the CTI rules block must be present"
    assert "</cti_compaction_rules>" in out
    # Splice order: rules appears before the <messages> sentinel, after the
    # media-reference block (deepagents contract).
    rules_idx = out.index("<cti_compaction_rules>")
    media_idx = out.index("<media_reference_information>")
    messages_idx = out.index("<messages>")
    assert media_idx < rules_idx < messages_idx, "rules must sit between media-ref and <messages>"
    # We must not have duplicated the rules block.
    assert out.count("<cti_compaction_rules>") == 1


def test_compose_falls_back_to_append_when_sentinel_missing(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """If deepagents changed the template and <messages> sentinel is gone,
    the rules are appended at the end (never dropped). {messages} if present
    is still preserved."""
    default_prompt = "You are a summarizer.\nConversation:\n{messages}\n"
    _install_deepagents_stub(monkeypatch, prompt=default_prompt)

    for mod in list(sys.modules):
        if mod == "munin.core.autonomy.compaction":
            sys.modules.pop(mod, None)
    from munin.core.autonomy.compaction import compose_cti_summary_prompt

    with caplog.at_level("WARNING"):
        out = compose_cti_summary_prompt()
    assert out is not None
    assert "<cti_compaction_rules>" in out
    assert "{messages}" in out
    # The original body must still be there at the front (append, not overwrite).
    assert out.startswith("You are a summarizer.")


def test_compose_returns_none_when_deepagents_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """If DEEPAGENTS_DEFAULT_SUMMARY_PROMPT cannot be imported, return None so
    the supervisor falls back to the framework default rather than crashing."""
    # Make sure the stub is not present so the import inside the composer fails.
    for mod in list(sys.modules):
        if mod.startswith("deepagents"):
            sys.modules.pop(mod, None)

    # Block any future import of the deepagents package.
    import builtins

    real_import = builtins.__import__

    def _blocked_import(name: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name.startswith("deepagents"):
            raise ModuleNotFoundError(f"stub: {name} not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)

    for mod in list(sys.modules):
        if mod == "munin.core.autonomy.compaction":
            sys.modules.pop(mod, None)
    from munin.core.autonomy.compaction import compose_cti_summary_prompt

    assert compose_cti_summary_prompt() is None


def test_rules_text_file_is_present_and_well_formed() -> None:
    """The plain-text rules file ships next to the composer and contains a
    single well-formed <cti_compaction_rules> block. An operator must be able
    to edit THIS file to tune compaction per-operation."""
    rules_file = Path(__file__).resolve().parents[1] / "munin" / "core" / "autonomy" / "cti_compaction_rules.txt"
    assert rules_file.exists(), f"missing CTI rules file: {rules_file}"
    text = rules_file.read_text(encoding="utf-8").strip()
    assert text.startswith("<cti_compaction_rules>"), "rules file must open with the wrapper element"
    assert text.endswith("</cti_compaction_rules>"), "rules file must close the wrapper element"
    assert text.count("<cti_compaction_rules>") == 1
    assert text.count("</cti_compaction_rules>") == 1
    # The operator-defined invariants: these key phrases must always be there
    # so a careless edit cannot silently drop a contract clause.
    assert "命令即授权" in text, "rules must reference the soul command-as-authorization contract"
    assert "IOC" in text
    assert "provenance" in text.lower() or "provenance" in text
    assert "next executable actions" in text.lower() or "next executable action" in text.lower()


def test_rules_file_does_not_contain_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rules file is a plain-text prompt fragment: it must not contain
   executable Python or shell syntax that could turn the operator-editable
    file into an injection vector. This is a soft integrity guard, not a full
    sandbox — the file content is consumed as prompt text only."""
    rules_file = Path(__file__).resolve().parents[1] / "munin" / "core" / "autonomy" / "cti_compaction_rules.txt"
    text = rules_file.read_text(encoding="utf-8")
    banned = ("import ", "def ", "class ", "__import__", "exec(", "eval(", "os.system", "subprocess")
    for token in banned:
        assert token not in text, f"rules file must not contain Python/shell token: {token!r}"


def test_compose_is_stable_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated calls produce identical output (no state leak between
    supervisor builds)."""
    default_prompt = "Summarize:\n<messages>\n{messages}\n</messages>\n"
    _install_deepagents_stub(monkeypatch, prompt=default_prompt)

    for mod in list(sys.modules):
        if mod == "munin.core.autonomy.compaction":
            sys.modules.pop(mod, None)
    from munin.core.autonomy.compaction import compose_cti_summary_prompt

    a = compose_cti_summary_prompt()
    b = compose_cti_summary_prompt()
    assert a == b
    assert a is not None
