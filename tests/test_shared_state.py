"""Shared state extensions — Munin's memory tables and wake queue."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def store(isolated_workspace):
    from munin.mcp.config import get_settings
    from munin.mcp.shared_state import SharedStateStore

    return SharedStateStore(get_settings())


def test_episodic_record_and_query(store):
    ids = [
        store.episodic_record(agent="munin", action="user_input", input_data={"text": f"hi {i}"}, tags=["dialog"])
        for i in range(3)
    ]
    assert all(i > 0 for i in ids)
    rows = store.episodic_query(agent="munin", action="user_input", limit=5)
    assert len(rows) == 3
    assert rows[0]["input"]["text"].startswith("hi ")


def test_semantic_upsert(store):
    store.semantic_remember("admin_creds", {"user": "admin", "pw": "itachi"})
    got = store.semantic_recall("admin_creds")
    assert got == {"user": "admin", "pw": "itachi"}
    # Overwrite
    store.semantic_remember("admin_creds", {"user": "admin", "pw": "changed"})
    assert store.semantic_recall("admin_creds")["pw"] == "changed"
    facts = store.semantic_list()
    assert any(f["key"] == "admin_creds" for f in facts)


def test_procedural_register_list_deactivate(store):
    store.procedural_register(
        name="gen__find_x",
        description="find X",
        script_path="/tmp/x.py",
        signature={"function_name": "find_x", "type": "object"},
        tags=["ldap", "kerberos"],
        created_by_agent="tool_forge",
    )
    active = store.procedural_list()
    assert any(t["name"] == "gen__find_x" for t in active)
    assert store.procedural_deactivate("gen__find_x") is True
    active_after = store.procedural_list()
    assert not any(t["name"] == "gen__find_x" for t in active_after)


def test_graph_register_and_drop(store):
    store.graph_register(
        name="kerberos_specialist",
        purpose="Kerberos deep-dive",
        system_prompt="be a Kerberos expert",
        tool_whitelist=["ldap_search", "find_kerberoastable_users"],
        reset_policy="on_reset",
        created_by_agent="munin",
    )
    graphs = store.graph_list()
    assert any(g["name"] == "kerberos_specialist" for g in graphs)
    assert store.graph_drop("kerberos_specialist") is True
    graphs_after = store.graph_list()
    assert not any(g["name"] == "kerberos_specialist" for g in graphs_after)


def test_wake_queue_enqueue_claim_order(store):
    a = store.enqueue_wake(target_agent="ldap_agent", task={"action": "get_user_groups", "username": "neji"}, priority=0)
    b = store.enqueue_wake(target_agent="ldap_agent", task={"action": "find_domain_admins"}, priority=5)
    c = store.enqueue_wake(target_agent="tool_forge", task={"spec": "generate X"}, priority=0)
    assert a > 0 and b > 0 and c > 0

    # First LDAP claim goes to the higher-priority one.
    claimed = store.claim_wake_item(target_agent="ldap_agent", claimer_pid=1234)
    assert claimed is not None
    assert claimed["task"]["action"] == "find_domain_admins"

    # Second LDAP claim is the FIFO older one.
    claimed2 = store.claim_wake_item(target_agent="ldap_agent", claimer_pid=1234)
    assert claimed2 is not None
    assert claimed2["task"]["action"] == "get_user_groups"

    # Third: nothing left for ldap_agent.
    claimed3 = store.claim_wake_item(target_agent="ldap_agent", claimer_pid=1234)
    assert claimed3 is None

    # tool_forge queue still has one.
    tf = store.claim_wake_item(target_agent="tool_forge", claimer_pid=1234)
    assert tf is not None and tf["task"]["spec"] == "generate X"
