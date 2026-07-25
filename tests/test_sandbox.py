"""AST guard + restricted-exec sandbox tests.

We're deliberately paranoid here: the sandbox is one of Munin's higher-risk components
(see docs/security-notes.md). These tests document what MUST fail before we let a
generated script run.
"""

from __future__ import annotations

import pytest


def test_plain_arithmetic_ok():
    from munin.subagents.sandbox import run_code

    result = run_code("result = 40 + 2", allowed_imports=set())
    assert result.ok
    assert result.return_value == 42


def test_import_os_blocked_by_allowlist():
    from munin.subagents.sandbox import run_code

    result = run_code("import os\nresult = os.getcwd()", allowed_imports={"json"})
    assert not result.ok
    assert "not in allowlist" in (result.error or "")


def test_subprocess_hard_banned_even_with_allowlist():
    from munin.subagents.sandbox import run_code

    result = run_code("import subprocess", allowed_imports={"subprocess"})
    assert not result.ok
    assert "hard-banned" in (result.error or "")


def test_dunder_class_attr_blocked():
    from munin.subagents.sandbox import run_code

    result = run_code("result = ().__class__.__mro__", allowed_imports=set())
    assert not result.ok


def test_exec_name_blocked():
    from munin.subagents.sandbox import run_code

    result = run_code("exec('print(1)')", allowed_imports=set())
    assert not result.ok


def test_ldap3_allowlisted_ok():
    from munin.subagents.sandbox import run_code

    code = """import ldap3\nfrom ldap3.utils.conv import escape_filter_chars\nresult = escape_filter_chars('a*b')"""
    result = run_code(code, allowed_imports={"ldap3"})
    assert result.ok, result.error
    assert isinstance(result.return_value, str)


def test_infinite_loop_hits_timeout():
    from munin.subagents.sandbox import run_code

    code = "while True:\n    pass"
    result = run_code(code, allowed_imports=set(), timeout_seconds=2)
    assert not result.ok
    assert "timeout" in (result.error or "").lower()


def test_runtime_import_of_banned_module_blocked():
    from munin.subagents.sandbox import run_code

    # __import__ is blocked at AST level; even so, the guarded __import__ in restricted
    # builtins refuses banned modules if it were reachable.
    result = run_code(
        "result = __import__('subprocess')",
        allowed_imports={"subprocess"},
    )
    assert not result.ok
