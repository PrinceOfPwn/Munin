"""LDAP injection guard — every parameter in `ldap_search` gets escaped via escape_filter_chars.

We don't need a live LDAP server for this test — we just verify that the escaped filter
string produced by `ldap_search` when given an injection attempt does NOT contain the
raw payload.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_escape_filter_chars_neutralizes_injection(isolated_workspace, monkeypatch):
    monkeypatch.setenv("LDAP_BIND_DN", "cn=admin,dc=meli,dc=com")
    monkeypatch.setenv("LDAP_PASSWORD", "itachi")

    from munin.mcp.tools import ldap_tools

    injection = "*)(uid=*"
    with patch.object(ldap_tools, "_connect") as mock_connect:
        conn = MagicMock()
        conn.entries = []
        mock_connect.return_value = conn
        ldap_tools.ldap_search(
            base_dn="dc=meli,dc=com",
            filter_template="(uid={u})",
            params_json=f'{{"u": "{injection}"}}',
            attributes_csv="dn,uid",
        )
        # Inspect the filter that was actually sent.
        call_kwargs = conn.search.call_args.kwargs
        used_filter = call_kwargs["search_filter"]
        # The raw injection segment `*)(uid=*` must NOT appear literally.
        assert "*)(uid=*" not in used_filter
        # The escaped stars/parens should be present.
        assert "\\2a" in used_filter or "\\28" in used_filter


def test_get_user_groups_escapes_username(isolated_workspace, monkeypatch):
    monkeypatch.setenv("LDAP_BIND_DN", "cn=admin,dc=meli,dc=com")
    monkeypatch.setenv("LDAP_PASSWORD", "itachi")

    from munin.mcp.tools import ldap_tools

    with patch.object(ldap_tools, "_connect") as mock_connect:
        conn = MagicMock()
        conn.entries = []
        mock_connect.return_value = conn
        ldap_tools.get_user_groups("*)(objectClass=*")
        used_filter = conn.search.call_args.kwargs["search_filter"]
        assert "*)(objectClass=*" not in used_filter
