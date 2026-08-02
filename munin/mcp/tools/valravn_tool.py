# tags: [valravn, recon, intel, osint, mcp-tool, scanning, ValravnGateway, valravn_investigate_ioc, valravn_search_assets, valravn_investigate_cve, valravn_search_darkweb, valravn_investigate_network, valravn_search_historical_web, valravn_capture_web_evidence, threat_intelligence_mesh]
"""Valravn capability tools — unified reconnaissance and threat intelligence for Munin."""

from __future__ import annotations

import threading
from typing import Any

from ...valravn import ValravnGateway  # noqa: TID252
from ..main import MCP, audited_tool  # noqa: TID252

_GATEWAY: ValravnGateway | None = None
_GATEWAY_LOCK = threading.Lock()

VALRAVN_TOOLS = frozenset({
    "valravn_status",
    "valravn_investigate_ioc",
    "valravn_investigate_organization",
    "valravn_search_assets",
    "valravn_investigate_cve",
    "valravn_investigate_network",
    "valravn_search_historical_web",
    "valravn_investigate_url",
    "valravn_submit_url",
    "valravn_validate_asset",
    "valravn_search_darkweb",
    "valravn_capture_web_evidence",
    "valravn_translate",
})


def _gateway() -> ValravnGateway:
    global _GATEWAY
    if _GATEWAY is None:
        with _GATEWAY_LOCK:
            if _GATEWAY is None:
                _GATEWAY = ValravnGateway()
    return _GATEWAY


def _ok(tool: str, summary: str, data: dict[str, Any], *, artifacts: list[Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": True, "tool": tool, "mode": "sync", "summary": summary, "data": data}
    if artifacts:
        result["artifacts"] = artifacts
    return result


def _error(tool: str, exc: Exception) -> dict[str, Any]:
    return {
        "ok": False,
        "tool": tool,
        "mode": "sync",
        "summary": f"{tool} failed: {exc}",
        "error": {"code": "valravn_failed", "message": str(exc)},
    }


@MCP.tool()
@audited_tool("valravn_status", "passive", lambda *a, **k: "sync")
def valravn_status(probe: bool = False, run_id: str = "") -> dict[str, Any]:
    """Inspect Valravn source availability, budgets and policy guards without revealing secret values."""
    try:
        data = _gateway().status(probe=probe)
        return _ok("valravn_status", f"Valravn has {data['ready_capabilities']} configured capabilities", data)
    except Exception as exc:
        return _error("valravn_status", exc)


@MCP.tool()
@audited_tool("valravn_investigate_ioc", "passive", lambda *a, **k: "sync")
def valravn_investigate_ioc(indicator: str, depth: str = "quick", run_id: str = "") -> dict[str, Any]:
    """Enrich an IP, domain, URL, hash, email or CVE using bounded quick/deep provider fan-out."""
    try:
        data = _gateway().investigate_ioc(indicator, depth=depth)
        summary = data.get("summary", {})
        return _ok("valravn_investigate_ioc", f"Gathered {summary.get('successful_sources', 0)} evidence sources", data)
    except Exception as exc:
        return _error("valravn_investigate_ioc", exc)


@MCP.tool()
@audited_tool("valravn_investigate_organization", "passive", lambda *a, **k: "sync")
def valravn_investigate_organization(
    organization: str,
    domain: str = "",
    depth: str = "deep",
    run_id: str = "",
) -> dict[str, Any]:
    """Investigate ransomware, breach, historical web and external exposure evidence for an organization."""
    try:
        data = _gateway().investigate_organization(organization, domain, depth=depth)
        summary = data.get("summary", {})
        return _ok("valravn_investigate_organization", f"Gathered {summary.get('successful_sources', 0)} organization sources", data)
    except Exception as exc:
        return _error("valravn_investigate_organization", exc)


@MCP.tool()
@audited_tool("valravn_search_assets", "passive", lambda *a, **k: "sync")
def valravn_search_assets(query: str, limit: int = 25, depth: str = "quick", run_id: str = "") -> dict[str, Any]:
    """Search authorized internet assets across configured global scanning and exposure sources."""
    try:
        data = _gateway().search_assets(query, limit, depth=depth)
        summary = data.get("summary", {})
        return _ok("valravn_search_assets", f"Completed {summary.get('successful_sources', 0)} asset sources", data)
    except Exception as exc:
        return _error("valravn_search_assets", exc)


@MCP.tool()
@audited_tool("valravn_investigate_cve", "passive", lambda *a, **k: "sync")
def valravn_investigate_cve(
    cve_or_product: str,
    version: str = "",
    depth: str = "quick",
    run_id: str = "",
) -> dict[str, Any]:
    """Resolve CVEs, KEV, EPSS, public exploit references and internet exposure context."""
    try:
        data = _gateway().investigate_cve(cve_or_product, version, depth=depth)
        summary = data.get("summary", {})
        return _ok("valravn_investigate_cve", f"Gathered {summary.get('successful_sources', 0)} vulnerability sources", data)
    except Exception as exc:
        return _error("valravn_investigate_cve", exc)


@MCP.tool()
@audited_tool("valravn_investigate_network", "passive", lambda *a, **k: "sync")
def valravn_investigate_network(
    resource: str,
    prefix: str = "",
    location: str = "",
    depth: str = "quick",
    starttime: str = "",
    endtime: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    """Investigate ASN/IP/prefix routing, RPKI, BGP history, hijacks and outages."""
    try:
        data = _gateway().investigate_network(
            resource,
            prefix,
            location,
            depth=depth,
            starttime=starttime,
            endtime=endtime,
        )
        return _ok("valravn_investigate_network", "Network routing context collected", data)
    except Exception as exc:
        return _error("valravn_investigate_network", exc)


@MCP.tool()
@audited_tool("valravn_search_historical_web", "passive", lambda *a, **k: "sync")
def valravn_search_historical_web(
    domain: str,
    limit: int = 100,
    from_year: str = "",
    to_year: str = "",
    include_javascript: bool = True,
    depth: str = "deep",
    run_id: str = "",
) -> dict[str, Any]:
    """Recover historical URLs, JavaScript and archived references from web archives."""
    try:
        data = _gateway().search_historical_web(
            domain,
            limit=limit,
            from_year=from_year,
            to_year=to_year,
            include_javascript=include_javascript,
            depth=depth,
        )
        return _ok("valravn_search_historical_web", f"Recovered {len(data.get('unique_urls', []))} unique URLs", data)
    except Exception as exc:
        return _error("valravn_search_historical_web", exc)


@MCP.tool()
@audited_tool("valravn_investigate_url", "passive", lambda *a, **k: "sync")
def valravn_investigate_url(
    url: str,
    depth: str = "quick",
    run_id: str = "",
) -> dict[str, Any]:
    """Check URL reputation and history against passive sources without submitting the URL anywhere."""
    try:
        data = _gateway().investigate_url(url, depth=depth)
        summary = data.get("summary", {})
        return _ok("valravn_investigate_url", f"Gathered {summary.get('successful_sources', 0)} URL sources", data)
    except Exception as exc:
        return _error("valravn_investigate_url", exc)


@MCP.tool()
@audited_tool("valravn_submit_url", "active", lambda *a, **k: "sync")
def valravn_submit_url(
    url: str,
    visibility: str = "unlisted",
    depth: str = "quick",
    run_id: str = "",
) -> dict[str, Any]:
    """Submit a URL to configured isolated scanners (urlscan/Cloudflare). Active: requires approval."""
    try:
        data = _gateway().submit_url(url, visibility=visibility, depth=depth)
        summary = data.get("summary", {})
        return _ok("valravn_submit_url", f"Completed {summary.get('successful_sources', 0)} submission sources", data)
    except Exception as exc:
        return _error("valravn_submit_url", exc)


@MCP.tool()
@audited_tool("valravn_validate_asset", "passive", lambda *a, **k: "sync")
def valravn_validate_asset(target: str, depth: str = "deep", run_id: str = "") -> dict[str, Any]:
    """Cross-check a critical IP or domain using higher-confidence and scarce external sources."""
    try:
        data = _gateway().validate_asset(target, depth=depth)
        summary = data.get("summary", {})
        return _ok("valravn_validate_asset", f"Validated through {summary.get('successful_sources', 0)} sources", data)
    except Exception as exc:
        return _error("valravn_validate_asset", exc)


@MCP.tool()
@audited_tool("valravn_search_darkweb", "passive", lambda *a, **k: "sync")
def valravn_search_darkweb(query: str, limit: int = 20, run_id: str = "") -> dict[str, Any]:
    """Search indexed onion services and return canonical .onion plus read-only .onion.pet gateway URLs."""
    try:
        data = _gateway().search_darkweb(query, limit)
        return _ok("valravn_search_darkweb", f"Found {data.get('count', 0)} onion references", data)
    except Exception as exc:
        return _error("valravn_search_darkweb", exc)


@MCP.tool()
@audited_tool("valravn_capture_web_evidence", "passive", lambda *a, **k: "sync")
def valravn_capture_web_evidence(
    url: str,
    translate_to: str = "es",
    full_page: bool = True,
    run_id: str = "",
) -> dict[str, Any]:
    """Open a public or .onion URL through the configured read-only browser path and persist evidence."""
    try:
        data = _gateway().capture_web_evidence(url, translate_to=translate_to, full_page=full_page, run_id=run_id)
        return _ok("valravn_capture_web_evidence", data.get("summary", "web evidence captured"), data, artifacts=data.get("artifacts", []))
    except Exception as exc:
        return _error("valravn_capture_web_evidence", exc)


@MCP.tool()
@audited_tool("valravn_translate", "passive", lambda *a, **k: "sync")
def valravn_translate(
    text: str,
    target_language: str = "es",
    source_language: str = "",
    content_format: str = "text",
    run_id: str = "",
) -> dict[str, Any]:
    """Translate extracted foreign-source text while preserving language metadata."""
    try:
        data = _gateway().translate(text, target_language=target_language, source_language=source_language, content_format=content_format)
        return _ok("valravn_translate", f"Translated {data.get('characters', 0)} characters", data)
    except Exception as exc:
        return _error("valravn_translate", exc)
