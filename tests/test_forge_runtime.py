from __future__ import annotations


def test_tool_forge_matches_only_explicit_requested_name(store, monkeypatch):
    from munin.mcp.tools import forge_tool

    monkeypatch.setattr(forge_tool, "STATE", store)
    store.procedural_register(
        name="gen__ldap_enum_summary",
        description="LDAP enumeration summary",
        script_path="munin/generated/ldap_enum_summary.py",
        signature={"function_name": "ldap_enum_summary"},
        tags=["ldap"],
        created_by_agent="test",
    )

    # Similar words are not enough to replace a requested specialised audit.
    assert forge_tool._existing_match("Create a tool named akatsuki_ldap_audit for LDAP security") is None
    reused = forge_tool._existing_match("Create a tool named ldap_enum_summary for LDAP security")
    assert reused and reused["name"] == "gen__ldap_enum_summary"


def test_tool_forge_coerces_mcp_string_flags_and_iterations(store, monkeypatch):
    from munin.mcp.tools import forge_tool

    monkeypatch.setattr(forge_tool, "STATE", store)
    captured: dict[str, object] = {}

    class FakeForge:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def forge(self, _spec):
            return {
                "ok": True,
                "slug": "typed_probe",
                "description": "probe",
                "script_path": "unused.py",
                "function_name": "typed_probe",
                "signature": {"function_name": "typed_probe"},
                "tags": [],
                "iterations": 1,
            }

    monkeypatch.setattr("munin.subagents.tool_forge.ToolForgeSubagent", FakeForge)
    monkeypatch.setattr(forge_tool.registry, "register", lambda *_args, **_kwargs: {"name": "gen__typed_probe"})
    monkeypatch.setattr("munin.mcp.git_persist.commit_forged_tool", lambda **_kwargs: None)

    result = forge_tool.tool_forge(
        "Create a tool named typed_probe",
        max_iterations="5",
        force_regenerate="True",
    )

    assert result["ok"] is True
    assert captured["max_iterations"] == 5
    assert callable(captured["on_progress"])


def test_tool_forge_emits_operator_safe_lifecycle_events(store):
    from munin.subagents.tool_forge import ToolForgeSubagent

    class FakeLlm:
        def chat(self, **_kwargs):
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"description":"returns a value","function_name":"lifecycle_probe",'
                                '"allowed_imports":[],"tags":["test"],'
                                '"python":"def lifecycle_probe(value: str = \'ok\') -> dict:\\n    return {\'value\': value}\\n"}'
                            )
                        }
                    }
                ]
            }

    events: list[dict] = []
    outcome = ToolForgeSubagent(
        store,
        max_iterations=1,
        llm=FakeLlm(),
        on_progress=events.append,
    ).forge("Create a lifecycle probe")

    assert outcome["ok"] is True
    assert [event["stage"] for event in events] == [
        "forge_generation",
        "forge_validation",
        "forge_persist",
        "forge_ready",
    ]
    assert all("reasoning" not in event for event in events)


def test_generated_wrapper_coerces_types_and_injects_safe_context(store):
    from munin.mcp import registry

    def typed_probe(port: int, enabled: bool, context: dict | None = None) -> dict:
        return {
            "port_type": type(port).__name__,
            "port": port,
            "enabled": enabled,
            "ldap_base_dn": (context or {}).get("ldap", {}).get("base_dn"),
        }

    result = registry.wrap_generated_callable(
        typed_probe,
        tool_name="gen__typed_probe",
        state=store,
    )(port="389", enabled="false")

    assert result["ok"] is True
    assert result["data"] == {
        "port_type": "int",
        "port": 389,
        "enabled": False,
        "ldap_base_dn": store.settings.ldap_base_dn,
    }


def test_state_only_tool_syncs_into_live_runtime_without_restart(store):
    from munin.mcp import registry

    registry.clear_callable_cache()
    source = store.settings.generated_tools_dir / "wake_probe.py"
    source.write_text(
        "def wake_probe(value: str = 'ok') -> dict:\n"
        "    return {'value': value}\n",
        encoding="utf-8",
    )
    registry.register_state_only(
        store,
        slug="wake_probe",
        description="wake result",
        script_path=source,
        function_name="wake_probe",
        signature={"function_name": "wake_probe"},
    )

    class FakeMcp:
        def __init__(self):
            self.attached: list[str] = []

        def tool(self):
            def decorator(fn):
                self.attached.append(fn.__name__)
                return fn
            return decorator

        def remove_tool(self, name: str):
            return None

    runtime = FakeMcp()
    synced = registry.sync_runtime(runtime, store, store.settings)

    assert synced["attached"] == 1
    assert synced["errors"] == []
    assert runtime.attached == ["gen__wake_probe"]


def test_runtime_sync_quietly_waits_for_a_legacy_source_to_reappear(store):
    from munin.mcp import registry

    legacy_path = store.settings.generated_tools_dir / "legacy_resume.py"
    store.procedural_register(
        name="gen__legacy_resume",
        description="legacy",
        script_path="munin/generated/legacy_resume.py",
        signature={"function_name": "legacy_resume"},
        tags=[],
        created_by_agent="test",
    )

    class FakeMcp:
        def __init__(self):
            self.attached: list[str] = []

        def tool(self):
            def decorator(fn):
                self.attached.append(fn.__name__)
                return fn
            return decorator

        def remove_tool(self, _name: str):
            return None

    runtime = FakeMcp()
    first = registry.sync_runtime(runtime, store, store.settings)
    second = registry.sync_runtime(runtime, store, store.settings)
    assert first["errors"] and first["errors"][0]["name"] == "gen__legacy_resume"
    assert second == {"attached": 0, "errors": []}

    legacy_path.write_text(
        "def legacy_resume() -> dict:\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    recovered = registry.sync_runtime(runtime, store, store.settings)
    assert recovered == {"attached": 1, "errors": []}
    assert runtime.attached == ["gen__legacy_resume"]
