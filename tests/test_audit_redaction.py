"""Audit trail must redact obvious secret shapes before persisting to disk."""

from __future__ import annotations


def test_bearer_token_redacted(isolated_workspace):
    from munin.mcp.audit import AuditTrailLogger

    logger = AuditTrailLogger(isolated_workspace)
    event = logger.record(
        run_id="test-run",
        tool="mock",
        level="admin",
        mode="sync",
        status="ok",
        target="",
        source_context="pytest",
        command_or_params="curl -H 'Authorization: Bearer nvapi-abcdefghijklmnop1234567890'",
        summary="test",
    )
    assert "nvapi-abcdefghijklmnop1234567890" not in event["command_or_params"]
    assert "REDACTED" in event["command_or_params"]


def test_api_key_kv_redacted(isolated_workspace):
    from munin.mcp.audit import AuditTrailLogger

    logger = AuditTrailLogger(isolated_workspace)
    event = logger.record(
        run_id="test-run",
        tool="mock",
        level="admin",
        mode="sync",
        status="ok",
        target="",
        source_context="pytest",
        command_or_params={"cmd": 'export LLM_API_KEY="sk-abcd1234567890abcd1234567890abcd"'},
        summary="",
    )
    serialized = str(event["command_or_params"])
    assert "sk-abcd1234567890abcd1234567890abcd" not in serialized
    assert "REDACTED" in serialized


def test_ghp_token_redacted(isolated_workspace):
    from munin.mcp.audit import AuditTrailLogger

    logger = AuditTrailLogger(isolated_workspace)
    event = logger.record(
        run_id="test-run",
        tool="mock",
        level="admin",
        mode="sync",
        status="ok",
        target="",
        source_context="pytest",
        command_or_params="the token is ghp_abcdefghijklmnop1234567890",
        summary="",
    )
    assert "ghp_abcdefghijklmnop1234567890" not in event["command_or_params"]


def test_trusted_lab_mode_preserves_exact_audit_output(monkeypatch, isolated_workspace):
    from munin.mcp.audit import AuditTrailLogger

    monkeypatch.setenv("MUNIN_REDACTION_MODE", "off")
    logger = AuditTrailLogger(isolated_workspace)
    event = logger.record(
        run_id="trusted-run",
        tool="mock",
        level="admin",
        mode="sync",
        status="ok",
        target="",
        source_context="pytest",
        command_or_params={"password": "lab-secret", "token": "lab-token"},
        summary="exact lab output",
    )
    assert event["command_or_params"] == {
        "password": "lab-secret",
        "token": "lab-token",
    }
