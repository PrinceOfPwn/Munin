from __future__ import annotations


def test_hugin_graph_normalisation_attaches_contents():
    from munin.mcp.tools.hugin_tool import _normalise_payload

    bundle = _normalise_payload({
        "nodes": [{"id": "T-001", "label": "Recycled Gate", "tags": ["syscalls"]}],
        "edges": [{"source": "T-001", "target": "T-002", "type": "requires"}],
        "contents": {"T-001": "operator playbook"},
    })
    assert bundle["source_format"] == "hugin-graph"
    assert bundle["entities"][0]["content"] == "operator playbook"
    assert bundle["edges"][0]["type"] == "requires"


def test_hugin_legacy_entity_list_is_supported():
    from munin.mcp.tools.hugin_tool import _normalise_payload

    bundle = _normalise_payload([{"title": "legacy"}])
    assert bundle == {
        "entities": [{"title": "legacy"}],
        "edges": [],
        "source_format": "entity-list",
    }


def test_hugin_boolean_coercion_handles_mcp_strings():
    from munin.mcp.tools.hugin_tool import _coerce_bool

    assert _coerce_bool("false") is False
    assert _coerce_bool("True") is True
