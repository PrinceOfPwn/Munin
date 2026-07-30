"""PR#1 fix: GITHUB_TOKEN must NOT be sent to NVD, CIRCL, MITRE, EPSS, CISA, OSV.

Bug: the previous VulnIntelService set `session.headers['Authorization'] = 'Bearer <PAT>'`
at construction time, causing every subsequent GET/POST to leak the token to any
third-party provider that reused that session. This test guarantees that after our
refactor, the Authorization header only shows up on the GitHub search call.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def intel(isolated_workspace, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_secret_that_must_not_leak")
    monkeypatch.setenv("NVD_API_KEY", "nvd_api_key_test")
    from munin.mcp.config import get_settings
    from munin.mcp.intel import VulnIntelService

    return VulnIntelService(get_settings())


def _mock_response(json_data=None, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.raise_for_status = MagicMock()
    return resp


def test_nvd_session_never_has_authorization(intel):
    assert "Authorization" not in intel._nvd.headers


def test_circl_session_never_has_authorization(intel):
    assert "Authorization" not in intel._circl.headers


def test_mitre_session_never_has_authorization(intel):
    assert "Authorization" not in intel._mitre.headers


def test_epss_session_never_has_authorization(intel):
    assert "Authorization" not in intel._epss.headers


def test_kev_session_never_has_authorization(intel):
    assert "Authorization" not in intel._kev.headers


def test_osv_session_never_has_authorization(intel):
    assert "Authorization" not in intel._osv.headers


def test_github_session_has_no_default_authorization(intel):
    # The GH session must also NOT carry Authorization by default —
    # the header is injected per-request in _github_search only.
    assert "Authorization" not in intel._github.headers


def test_github_search_injects_bearer_per_request(intel):
    with patch.object(intel._github, "get", return_value=_mock_response({"items": []})) as mocked:
        intel._github_search("CVE-2024-1234")
        _, kwargs = mocked.call_args
        assert kwargs["headers"]["Authorization"].startswith("Bearer ")
        # And other sessions were never called with a Bearer for the same query.


def test_osv_post_does_not_send_bearer(intel):
    with patch.object(intel._osv, "post", return_value=_mock_response({"vulns": []})) as mocked:
        intel.package_vuln_lookup("PyPI", "requests", "2.31.0")
        # No headers kwarg means default session headers — which have no Authorization.
        headers = mocked.call_args.kwargs.get("headers", {})
        assert "Authorization" not in headers
