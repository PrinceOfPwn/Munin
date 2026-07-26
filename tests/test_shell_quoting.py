"""PR#1 fix: `additional_args` must be tokenized (shlex.split), not appended as a single
quoted token that would collapse `-Pn --top-ports 100` into `'-Pn --top-ports 100'`."""

from __future__ import annotations

import pytest


def test_split_extra_args_empty():
    from munin.mcp.utils import split_extra_args

    assert split_extra_args("") == []
    assert split_extra_args("   ") == []


def test_split_extra_args_multi_tokens():
    from munin.mcp.utils import split_extra_args

    assert split_extra_args("-Pn --top-ports 100") == ["-Pn", "--top-ports", "100"]


def test_split_extra_args_respects_quotes():
    from munin.mcp.utils import split_extra_args

    assert split_extra_args("-p 'top 100'") == ["-p", "top 100"]


def test_shell_join_filters_none_and_empty():
    from munin.mcp.utils import shell_join

    assert shell_join(["nmap", "", None, "-sV", "scanme.nmap.org"]) == "nmap -sV scanme.nmap.org"


def test_shell_join_quotes_special_chars():
    from munin.mcp.utils import shell_join

    result = shell_join(["echo", "hello world; whoami"])
    quoted = result.replace("'hello world; whoami'", "").replace('"hello world; whoami"', "")
    assert ";" not in quoted


def test_nmap_command_splits_additional_args(isolated_workspace):
    from munin.mcp.main import _nmap_command

    cmd = _nmap_command("scanme.nmap.org", "-sV", "", "-Pn --top-ports 100")
    # Three separate tokens must be present; not a single quoted blob.
    assert " -Pn " in cmd or cmd.endswith(" -Pn scanme.nmap.org") or " -Pn --top-ports" in cmd
    assert "'-Pn --top-ports 100'" not in cmd
    assert "--top-ports" in cmd
    assert "100" in cmd
    assert cmd.endswith("scanme.nmap.org")


@pytest.mark.parametrize("scan_type", ["-sV", "-sS", "-sU"])
def test_nmap_command_variants(isolated_workspace, scan_type):
    from munin.mcp.main import _nmap_command

    cmd = _nmap_command("10.0.0.1", scan_type, "80,443", "")
    assert cmd.startswith("nmap")
    assert scan_type in cmd
    assert "-p" in cmd and "80,443" in cmd
    assert cmd.endswith("10.0.0.1")
