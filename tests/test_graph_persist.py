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
    assert payload["schema_version"] == 2
    assert payload["tool_whitelist"] == ["hugin_search", "ldap_search"]
    assert payload["execution_contract"] == {}

    store.graph_drop(graph["name"])
    assert store.graph_get(graph["name"]) is None
    result = rehydrate_graph_manifests(store, store.settings)
    assert result == {"loaded": 1, "errors": []}
    assert store.graph_get(graph["name"])["purpose"] == graph["purpose"]


def test_graph_execution_contract_survives_store_and_manifest(store):
    from munin.mcp.graph_persist import persist_graph_manifest, rehydrate_graph_manifests

    contract = {
        "version": 1,
        "mode": "evidence_mesh",
        "context_sources": ["semantic_memory", "shared_intel", "hugin_cached_graph"],
        "delivery": {"sections": ["Summary", "Evidence", "Next steps"]},
    }
    store.graph_register(
        name="evidence-researcher",
        purpose="Research with durable evidence",
        system_prompt="Use evidence.",
        tool_whitelist=["hugin_search"],
        reset_policy="persistent",
        created_by_agent="test",
        execution_contract=contract,
    )
    graph = store.graph_get("evidence-researcher")
    assert graph and graph["execution_contract"] == contract
    persist_graph_manifest(store.settings, graph, queue_git=False)

    store.graph_drop("evidence-researcher")
    rehydrate_graph_manifests(store, store.settings)
    restored = store.graph_get("evidence-researcher")
    assert restored and restored["execution_contract"] == contract


def test_evidence_mesh_builds_auditable_hugin_context(store, monkeypatch):
    from munin.mcp.tools import hugin_tool
    from munin.subagents.runner import _ForgedGraphRunner

    store.semantic_remember("engagement.scope", {"target": "example.org"})
    monkeypatch.setattr(
        hugin_tool,
        "_load_cached",
        lambda **_kwargs: (
            {
                "entities": [
                    {
                        "id": "technique-1",
                        "label": "Example discovery technique",
                        "category": "discovery",
                        "tags": ["example", "research"],
                    }
                ]
            },
            12,
            False,
        ),
    )
    # Context generation is deterministic and should not require an LLM client.
    runner = object.__new__(_ForgedGraphRunner)
    runner.name = "researcher"
    runner.state = store
    runner.execution_contract = {
        "context_sources": ["semantic_memory", "shared_intel", "hugin_cached_graph"],
        "delivery": {"sections": ["Summary", "Evidence", "Next steps"]},
    }

    text, meta = runner._task_context({"prompt": "Research example discovery"})

    assert "Evidence Mesh context" in text
    assert "technique-1" in text
    assert meta == {
        "context_sources": ["semantic_memory", "shared_intel", "hugin_cached_graph"],
        "fact_count": 1,
        "intel_count": 0,
        "hugin_count": 1,
    }


def test_reset_purge_removes_only_on_reset_manifests(store):
    from munin.mcp.graph_persist import (
        persist_graph_manifest,
        purge_resettable_graph_manifests,
        rehydrate_graph_manifests,
    )

    transient = {
        "name": "transient",
        "purpose": "Dropped by reset",
        "reset_policy": "on_reset",
    }
    persistent = {
        "name": "persistent",
        "purpose": "Survives reset",
        "reset_policy": "persistent",
    }
    transient_path = persist_graph_manifest(store.settings, transient, queue_git=False)
    persistent_path = persist_graph_manifest(store.settings, persistent, queue_git=False)

    result = purge_resettable_graph_manifests(store.settings)
    assert result == {"removed": 1, "errors": []}
    assert not transient_path.exists()
    assert persistent_path.exists()

    store.graph_purge_on_reset()
    rehydrate_graph_manifests(store, store.settings)
    assert store.graph_get("transient") is None
    assert store.graph_get("persistent") is not None


def test_graph_names_with_same_slug_get_distinct_manifests(store):
    from munin.mcp.graph_persist import persist_graph_manifest, rehydrate_graph_manifests

    first_path = persist_graph_manifest(
        store.settings,
        {"name": "audit graph", "purpose": "first"},
        queue_git=False,
    )
    second_path = persist_graph_manifest(
        store.settings,
        {"name": "audit-graph", "purpose": "second"},
        queue_git=False,
    )

    assert first_path != second_path
    assert first_path.exists() and second_path.exists()
    result = rehydrate_graph_manifests(store, store.settings)
    assert result["loaded"] >= 2
    assert {row["name"] for row in store.graph_list()} >= {"audit graph", "audit-graph"}


def test_manifest_aware_drop_prevents_probe_rehydration(store, monkeypatch):
    from munin.mcp.tools import graph_forge_tool

    store.graph_register(
        name="e2e_probe_test",
        purpose="diagnostic",
        system_prompt="test",
        tool_whitelist=[],
        reset_policy="on_reset",
        created_by_agent="diagnostics",
    )
    graph = store.graph_get("e2e_probe_test")
    assert graph is not None
    from munin.mcp.graph_persist import persist_graph_manifest, rehydrate_graph_manifests

    path = persist_graph_manifest(store.settings, graph, queue_git=False)
    monkeypatch.setattr(graph_forge_tool, "STATE", store)
    monkeypatch.setenv("MUNIN_AUTO_COMMIT", "0")

    result = graph_forge_tool.drop_generated_graph("e2e_probe_test")

    assert result["ok"] is True
    assert json.loads(path.read_text(encoding="utf-8"))["active"] is False
    store.graph_purge_on_reset()
    rehydrated = rehydrate_graph_manifests(store, store.settings)
    assert rehydrated["loaded"] == 0
    assert store.graph_get("e2e_probe_test") is None


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
