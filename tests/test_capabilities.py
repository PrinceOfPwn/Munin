from __future__ import annotations

from munin.mcp.capabilities import capabilities_catalog, generated_tool_context
from munin.mcp.config import get_settings


def test_capability_catalog_is_cross_domain_and_declarative(isolated_workspace):
    catalog = capabilities_catalog(get_settings())
    ids = {profile["id"] for profile in catalog["profiles"]}
    assert {"directory_read", "knowledge_graph", "web_recon", "agent_composition"} <= ids
    hugin = next(profile for profile in catalog["profiles"] if profile["id"] == "knowledge_graph")
    assert "hugin_neighbors" in hugin["native_tools"]
    recon = next(profile for profile in catalog["profiles"] if profile["id"] == "web_recon")
    assert recon["safety"] == "active_authorization_required"


def test_generated_context_exposes_defaults_but_not_credentials(isolated_workspace, monkeypatch):
    monkeypatch.setenv("LDAP_URI", "ldap://directory:389")
    monkeypatch.setenv("LDAP_BASE_DN", "dc=example,dc=test")
    monkeypatch.setenv("LDAP_BIND_DN", "cn=admin,dc=example,dc=test")
    monkeypatch.setenv("LDAP_PASSWORD", "must-not-leak")
    context = generated_tool_context(get_settings())
    assert context["ldap"]["uri"] == "ldap://directory:389"
    assert context["ldap"]["base_dn"] == "dc=example,dc=test"
    assert context["ldap"]["credentials_configured"] is True
    assert "must-not-leak" not in str(context)
    assert "cn=admin" not in str(context)


def test_catalog_optionally_includes_safe_generated_context(isolated_workspace):
    catalog = capabilities_catalog(get_settings(), include_context=True)
    assert catalog["generated_tool_context"]["contracts"]["parameters"].startswith("Use annotations")
