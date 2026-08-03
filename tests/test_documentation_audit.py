# tags: [tests, automation, documentation-audit, local-llm]
"""Deterministic tests for the local documentation-audit orchestration."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "documentation_audit.py"
SPEC = importlib.util.spec_from_file_location("documentation_audit", MODULE_PATH)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUDIT
SPEC.loader.exec_module(AUDIT)


def test_prompt_contains_complete_file_and_external_context_guard() -> None:
    content = "from package import helper\n\ndef run(value):\n    return helper(value)\n"
    prompt = AUDIT.build_file_prompt(Path("sample.py"), "python", content, 8)

    assert content in prompt
    assert "Do not assume how external dependencies work" in prompt
    assert "only in English" in prompt
    assert "Do not modify code" in prompt


def test_normalization_downgrades_unconfirmed_error_and_filters_low_confidence() -> None:
    raw = {
        "summary": "Review",
        "findings": [
            {
                "line": 99,
                "end_line": 100,
                "symbol": "run",
                "category": "external_behavior_claim",
                "verification": "requires_external_context",
                "severity": "error",
                "confidence": 0.95,
                "title": "External claim",
                "explanation": "Cannot be verified locally.",
                "recommendation": "Review the dependency contract.",
            },
            {
                "line": 1,
                "end_line": 1,
                "symbol": "x",
                "category": "other",
                "verification": "likely",
                "severity": "notice",
                "confidence": 0.2,
                "title": "Weak",
                "explanation": "Weak",
                "recommendation": "Weak",
            },
        ],
    }
    content = "def run():\n    pass\n"
    normalized = AUDIT.normalize_file_report(
        raw,
        Path("sample.py"),
        "python",
        content,
        AUDIT.ScanConfig(minimum_confidence=0.72),
    )

    assert len(normalized["findings"]) == 1
    finding = normalized["findings"][0]
    assert finding["severity"] == "warning"
    assert finding["line"] == 2
    assert finding["end_line"] == 2


def test_merge_audit_block_appends_then_replaces() -> None:
    first = f"{AUDIT.AUDIT_START}\nfirst\n{AUDIT.AUDIT_END}"
    second = f"{AUDIT.AUDIT_START}\nsecond\n{AUDIT.AUDIT_END}"

    appended = AUDIT.merge_audit_block("Original description", first)
    replaced = AUDIT.merge_audit_block(appended, second)

    assert "Original description" in replaced
    assert "second" in replaced
    assert "first" not in replaced
    assert replaced.count(AUDIT.AUDIT_START) == 1


def test_source_selection_is_incremental_and_ignores_generated_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "dist").mkdir()
    (tmp_path / "src" / "one.py").write_text("def one():\n    pass\n", encoding="utf-8")
    (tmp_path / "dist" / "bundle.js").write_text("const x = 1;", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, stdout=subprocess.DEVNULL)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()

    (tmp_path / "src" / "one.py").write_text("def one(value):\n    return value\n", encoding="utf-8")
    (tmp_path / "src" / "two.ts").write_text("export const two = 2;\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "change"], cwd=tmp_path, check=True, stdout=subprocess.DEVNULL)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()

    monkeypatch.chdir(tmp_path)
    selected = AUDIT.select_files("changed", base, head, AUDIT.ScanConfig())

    assert [path.as_posix() for path in selected] == ["src/one.py", "src/two.ts"]


def test_large_file_is_not_silently_truncated() -> None:
    report = AUDIT.skipped_report(
        Path("large.py"),
        "python",
        "Full file exceeds context; it was not truncated.",
        "x = 1\n",
    )

    assert report["metadata"]["status"] == "skipped"
    assert "not truncated" in report["summary"]
