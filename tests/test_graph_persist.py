from __future__ import annotations

import json


def test_graph_manifest_roundtrip(store):
    from munin.mcp.graph_persist import persist_graph_manifest, rehydrate_graph_manifests

    graph = {
        "name": "kerberos specialist",
        "purpose": "Investigate Kerberos paths",
        "system_prompt": "Follow evidence and report uncertainty.",
        "tool_whitelist": ["hugin_search", "ldap_search"],
        "reset_policy": "persistent",
        "created_by_agent": "graph_forge",
        "active": True,
    }
    path = persist_graph_manifest(store.settings, graph, queue_git=False)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["tool_whitelist"] == ["hugin_search", "ldap_search"]

    store.graph_drop(graph["name"])
    assert store.graph_get(graph["name"]) is None
    result = rehydrate_graph_manifests(store, store.settings)
    assert result == {"loaded": 1, "errors": []}
    assert store.graph_get(graph["name"])["purpose"] == graph["purpose"]


def test_runtime_cache_can_serve_stale_entries(store):
    store.cache_put("hugin", "graph", {"nodes": 3}, ttl_seconds=1)
    fresh = store.cache_get("hugin", "graph")
    assert fresh and fresh["value"]["nodes"] == 3

    with store._connect() as conn:
        conn.execute(
            "UPDATE runtime_cache SET expires_at_epoch = 0 WHERE namespace = ? AND cache_key = ?",
            ("hugin", "graph"),
        )
    assert store.cache_get("hugin", "graph") is None
    stale = store.cache_get("hugin", "graph", allow_stale=True)
    assert stale and stale["is_stale"] is True
