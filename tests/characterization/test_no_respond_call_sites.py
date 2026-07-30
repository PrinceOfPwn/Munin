"""
Deletion lock: verify MuninAgent.respond() has no call sites.

This test uses AST analysis to ensure that once respond() is removed,
no code accidentally re-introduces a call to it.
"""
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
MUNIN_PKG = REPO_ROOT / "munin"


def find_respond_calls(tree: ast.AST, filepath: Path) -> list[tuple[Path, int]]:
    """Return (file, line) for each MuninAgent.respond() call site found."""
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if (
                node.attr == "respond"
                and isinstance(node.value, ast.Name)
                and node.value.id in ("agent", "munin_agent", "MuninAgent")
            ):
                violations.append((filepath, node.lineno))
        # Also catch bare .respond( patterns on any receiver
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "respond":
                # Could be a false positive (e.g. HTTP response.respond)
                # Only flag if receiver looks like an agent
                if isinstance(func.value, ast.Name):
                    if "agent" in func.value.id.lower() or "munin" in func.value.id.lower():
                        violations.append((filepath, node.lineno))
    return violations


def test_no_respond_call_sites():
    """MuninAgent.respond() must not be called anywhere in munin/ after PR-04."""
    if not MUNIN_PKG.exists():
        import pytest
        pytest.skip("munin package not found")

    violations = []
    for py_file in MUNIN_PKG.rglob("*.py"):
        # Skip the definition file itself and test files
        if py_file.name == "munin_agent.py":
            continue
        if "test_" in py_file.name or "characterization" in str(py_file):
            continue
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
            violations.extend(find_respond_calls(tree, py_file))
        except SyntaxError:
            pass  # Skip files with syntax errors

    if violations:
        lines = "\n".join(f"  {p}:{ln}" for p, ln in violations)
        assert False, f"MuninAgent.respond() call sites found (must be removed):\n{lines}"
