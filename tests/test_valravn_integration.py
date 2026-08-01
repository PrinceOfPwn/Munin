from __future__ import annotations


def test_valravn_tools_register_in_fastmcp(monkeypatch):
    monkeypatch.setenv("VALRAVN_RESOLVE_PUBLIC_HOSTS", "false")
    from munin.mcp.main import MCP

    records = getattr(getattr(MCP, "_tool_manager", None), "_tools", {})
    expected = {
        "valravn_status",
        "valravn_investigate_ioc",
        "valravn_investigate_organization",
        "valravn_search_assets",
        "valravn_investigate_cve",
        "valravn_investigate_network",
        "valravn_search_historical_web",
        "valravn_investigate_url",
        "valravn_validate_asset",
        "valravn_search_darkweb",
        "valravn_capture_web_evidence",
        "valravn_translate",
    }
    assert expected <= set(records)


def test_valravn_status_is_offline_safe(monkeypatch):
    monkeypatch.setenv("VALRAVN_RESOLVE_PUBLIC_HOSTS", "false")
    from munin.mcp.tools.valravn_tool import valravn_status

    result = valravn_status(probe=False)
    assert result["ok"] is True
    assert result["data"]["name"] == "Valravn"
    assert result["data"]["sources"]["ripestat"] is True
    assert result["data"]["sources"]["wayback"] is True
    assert result["data"]["sources"]["commoncrawl"] is True
