from __future__ import annotations

from munin.rag import hugin_rag


def _bundle() -> dict[str, object]:
    return {
        "entities": [
            {"id": "cve-2021-41773", "label": "Apache path traversal", "category": "cve", "tags": ["apache", "httpd"], "content": "Apache HTTP Server 2.4.49"},
            {"id": "t1190", "label": "Exploit Public-Facing Application", "category": "technique", "tags": ["web", "initial-access"], "content": "public web service"},
        ],
        "edges": [{"source": "t1190", "target": "cve-2021-41773", "type": "references"}],
    }


def test_hugin_rag_returns_scored_evidence(monkeypatch) -> None:
    monkeypatch.setattr(hugin_rag.hugin_tool, "_load_cached", lambda allow_stale: (_bundle(), 4, False))
    result = hugin_rag.search("Apache httpd", limit=3)
    assert result["ok"]
    assert result["matches"][0]["id"] == "cve-2021-41773"
    assert result["matches"][0]["matched_terms"] == ["apache", "httpd"]


def test_hugin_plan_keeps_operator_scope_requirement(monkeypatch) -> None:
    monkeypatch.setattr(hugin_rag.hugin_tool, "_load_cached", lambda allow_stale: (_bundle(), 0, False))
    result = hugin_rag.plan_for("apache public web", limit=2)
    assert result["ok"]
    assert all(step["requires_operator_scope"] for step in result["steps"])
