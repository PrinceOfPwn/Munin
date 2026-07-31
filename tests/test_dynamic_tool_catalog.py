"""Regression coverage for the Deep Agents ↔ FastMCP catalog boundary."""
from __future__ import annotations


def test_gateway_discovers_live_fastmcp_tools_not_in_legacy_static_catalog(store):
    """Active FastMCP scan tools must reach the LangGraph supervisor surface."""
    from munin.core.tool_gateway import catalog_names, gateway_tools

    names = catalog_names(store, include_generated=False)
    assert {"nmap_scan", "httpx_probe"} <= names

    gateway_names = {tool.name for tool in gateway_tools(store, include_generated=False)}
    assert {"nmap_scan", "httpx_probe"} <= gateway_names


def test_active_fastmcp_tools_get_native_deepagents_human_review_policy(store):
    from munin.core.tool_gateway import approval_policy_for_tools, gateway_tools

    policy = approval_policy_for_tools(gateway_tools(store, include_generated=False))
    assert policy["nmap_scan"]["allowed_decisions"] == ["approve", "reject"]
    assert policy["httpx_probe"]["allowed_decisions"] == ["approve", "reject"]
