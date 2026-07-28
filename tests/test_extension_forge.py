from __future__ import annotations

from pathlib import Path

from munin.forge.extension_guard import validate_extension_diff
from munin.forge.extension_manifest import ExtensionManifest


def _diff(path: str, old: str, new: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1 @@\n"
        f"-{old}\n"
        f"+{new}\n"
    )


def test_extension_guard_accepts_applyable_allowlisted_doc(tmp_path: Path) -> None:
    path = tmp_path / "docs" / "sample.md"
    path.parent.mkdir()
    path.write_text("old\n", encoding="utf-8")
    report = validate_extension_diff(tmp_path, _diff("docs/sample.md", "old", "new"), ["docs/sample.md"])
    assert report.ok
    assert report.touched_paths == ["docs/sample.md"]


def test_extension_guard_rejects_dangerous_added_code(tmp_path: Path) -> None:
    path = tmp_path / "munin" / "mcp" / "tools" / "sample.py"
    path.parent.mkdir(parents=True)
    path.write_text("value = 1\n", encoding="utf-8")
    diff = _diff("munin/mcp/tools/sample.py", "value = 1", "import subprocess")
    report = validate_extension_diff(tmp_path, diff, ["munin/mcp/tools/sample.py"])
    assert not report.ok
    assert any("dangerous" in error for error in report.errors)


def test_manifest_protects_mcp_control_plane() -> None:
    try:
        ExtensionManifest(
            slug="disable-auth",
            kind="python",
            rationale="bad idea",
            target_paths=["munin/mcp/main.py"],
            diff=_diff("munin/mcp/main.py", "old", "new"),
        )
    except ValueError as exc:
        assert "allowlist" in str(exc)
    else:  # pragma: no cover - explicit regression guard
        raise AssertionError("control-plane proposal was accepted")
