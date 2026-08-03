# tags: [capabilities, mcp, mcp-tool, registry, orchestrator, CAPABILITY_PROFILES, generated_tool_context, capabilities_catalog, Settings, directory_read, knowledge_graph, web_recon, network_service_recon, agent_composition, preflight_policy]
"""Declarative capability catalog and safe execution context for Munin.

The catalog is intentionally independent of the MCP transport.  It gives agents a
single, stable description of what is available without teaching them to infer a
tool's contract from a prose ``skills.md`` file.  It also provides *non-secret*
configuration that a forged tool may receive as an optional ``context`` argument.
"""

from __future__ import annotations

from typing import Any

from .config import Settings


# Keep this declarative: the list is useful to an LLM even if a binary is absent.
# Calls to active native tools still perform their existing dependency and OPSEC
# checks, and are never implied to be authorised by appearing here.
CAPABILITY_PROFILES: tuple[dict[str, Any], ...] = (
    {
        "id": "directory_read",
        "title": "Directory and identity analysis",
        "safety": "read_only",
        "native_tools": (
            "ldap_who_am_i", "get_current_user_info", "get_user_groups", "ldap_search",
            "find_kerberoastable_users", "find_asrep_roastable_users", "find_domain_admins",
            "dump_domain_structure",
        ),
        "generated_tool_guidance": "Use ldap3, escape every caller-controlled LDAP filter value, and use context.ldap defaults rather than hard-coded hosts.",
    },
    {
        "id": "knowledge_graph",
        "title": "Hugin knowledge graph and passive intelligence",
        "safety": "passive",
        "native_tools": ("hugin_search", "hugin_neighbors", "hugin_refresh", "tavily_search"),
        "generated_tool_guidance": "Prefer Hugin search/neighbors before creating a one-off parser; retain source URLs and cache freshness in results.",
    },
    {
        "id": "web_recon",
        "title": "Web and service reconnaissance",
        "safety": "active_authorization_required",
        "native_tools": ("nmap_scan", "nmap_advanced_scan", "httpx_probe", "feroxbuster_scan", "ffuf_scan", "katana_crawl", "sqlmap_scan"),
        "generated_tool_guidance": "Use native tools for authorised targets. Forged tools may transform their structured results, but must not bypass target, timeout, or OPSEC checks.",
    },
    {
        "id": "network_service_recon",
        "title": "Network service reconnaissance",
        "safety": "active_authorization_required",
        "native_tools": ("netexec_scan", "smbmap_scan", "hydra_attack"),
        "generated_tool_guidance": "These calls are active and may affect a target. Require explicit scope and rely on the native implementation for policy enforcement.",
    },
    {
        "id": "agent_composition",
        "title": "Persistent agents, tools, graphs, and memory",
        "safety": "controlled",
        "native_tools": (
            "tool_forge", "graph_forge", "munin_wake", "list_generated_tools",
            "describe_generated_tool", "run_generated_tool", "list_generated_graphs",
            "memory_remember", "memory_recall", "episodic_query", "publish_shared_intel",
        ),
        "generated_tool_guidance": "Forge narrow, deterministic helpers with typed parameters. Store durable findings in memory/intel, not module globals.",
    },
    {
        "id": "burp_dast",
        "title": "Valravn Burp extension — agentic DAST bridge",
        "safety": "active_authorization_required",
        "native_tools": (
            "burp_status", "burp_health_check", "burp_check_scope",
            "burp_get_proxy_count", "burp_invoke",
        ),
        "generated_tool_guidance": (
            "Target the Valravn Burp extension REST API at 127.0.0.1:8111 (BURP_API_HOST / BURP_API_PORT "
            "override via env). burp_invoke is the generic dispatcher; prefer the typed wrappers "
            "(burp_status, burp_check_scope, burp_get_proxy_count) when they fit. The extension owns scope "
            "enforcement; this wrapper never bypasses check_scope. The wrapper is resilient: an unreachable "
            "extension returns ok=False with code=extension_unreachable and never cancels the Munin run."
        ),
    },
)


def generated_tool_context(settings: Settings) -> dict[str, Any]:
    """Return safe runtime defaults for a generated tool.

    Credentials deliberately never appear here. A tool that needs an authenticated
    LDAP connection should use the native LDAP tool or obtain credentials only from
    its controlled runtime, never from an MCP argument or generated source.
    """
    return {
        "version": 1,
        "ldap": {
            "uri": settings.ldap_uri,
            "base_dn": settings.ldap_base_dn,
            "credentials_configured": bool(settings.ldap_bind_dn and settings.ldap_password),
            "mode": "read_only_native_tools_preferred",
        },
        "hugin": {
            "source_url": settings.hugin_url,
            "ttl_seconds": settings.hugin_ttl_seconds,
            "mode": "passive_cached_graph",
        },
        "runtime": {
            "default_timeout_seconds": settings.default_timeout,
            "max_output_chars": settings.max_output_chars,
            "preflight_policy": settings.preflight_policy,
        },
        "contracts": {
            "result_envelope": "Return JSON-serializable dict/list/scalar; runtime adds ok/tool/mode/summary.",
            "parameters": "Use annotations and JSON-schema-compatible primitives. Do not accept secrets as parameters.",
            "failure": "Return a structured {error: {code, message}} for expected input/config errors; never leak secrets.",
        },
    }


def capabilities_catalog(settings: Settings, *, include_context: bool = False) -> dict[str, Any]:
    """Build a serializable catalog suitable for the agent and UI."""
    profiles = [
        {**profile, "native_tools": list(profile["native_tools"])}
        for profile in CAPABILITY_PROFILES
    ]
    result: dict[str, Any] = {
        "profiles": profiles,
        "count": len(profiles),
        "contract_version": 1,
        "notes": [
            "Availability is checked at call time; installed binaries and configured providers can vary by runner.",
            "Active profiles require explicit authorised scope and retain native OPSEC/dependency checks.",
            "Generated tools are for bounded transformations and safe integrations, not a bypass around native policy.",
        ],
    }
    if include_context:
        result["generated_tool_context"] = generated_tool_context(settings)
    return result
