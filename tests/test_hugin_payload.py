from __future__ import annotations

import pytest


def test_hugin_payload_recovers_lone_backslash_in_source_snippet():
    from munin.mcp.tools.hugin_tool import _decode_payload

    # This models the malformed Windows-style path emitted in Hugin's public
    # graph. The decoder must retain it as data while still using json.loads to
    # validate the whole document after repair.
    payload = _decode_payload(
        r'{"nodes":[{"id":"T-1","label":"C:\Temp\notes"}],"edges":[]}',
        source_url="https://example.invalid/hugin.json",
    )

    assert payload["nodes"][0]["label"] == r"C:\Temp\notes"


def test_hugin_payload_does_not_accept_broken_json_structure():
    from munin.mcp.tools.hugin_tool import _decode_payload

    with pytest.raises(ValueError):
        _decode_payload('{"nodes":[}', source_url="https://example.invalid/hugin.json")
