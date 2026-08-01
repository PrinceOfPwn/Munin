from __future__ import annotations

from pathlib import Path

import pytest

from munin.valravn.config import ProviderPolicy, ValravnSettings
from munin.valravn.gateway import ValravnGateway
from munin.valravn.native_apis import NativeIntelClient, PolicyDenied
from munin.valravn.policy import Candidate, select_candidates
from munin.valravn.security import (
    UnsafeTarget,
    classify_indicator,
    gateway_to_onion,
    onion_to_gateway,
    validate_public_url,
)


class FakeNative:
    def threatfox(self, value):
        return {"indicator": value, "verdict": "malicious"}

    def otx(self, value, kind):
        raise RuntimeError("OTX unavailable")

    def urlhaus(self, value, kind):
        return {"indicator": value, "query_status": "no_results"}

    def threatminer(self, value, kind):
        return {"indicator": value, "records": []}

    def greynoise(self, value):
        return {"ip": value, "classification": "unknown"}

    def abuseipdb(self, value):
        return {"ip": value}

    def shodan_host(self, value):
        return {"ip": value}

    def censys_host(self, value):
        return {"ip": value}

    def virustotal(self, value, kind):
        return {"value": value}

    def netlas_host(self, value):
        return {"host": value}

    def leakix_lookup(self, value, kind):
        return {"target": value}

    def fullhunt_lookup(self, value, kind):
        return {"target": value}


@pytest.fixture
def settings(tmp_path: Path) -> ValravnSettings:
    return ValravnSettings(
        workspace_root=tmp_path,
        resolve_public_hosts=False,
        policy=ProviderPolicy(
            no_key_quick=5,
            no_key_deep=8,
            free_key_quick=0,
            free_key_deep=0,
            scarce_quick=0,
            scarce_deep=0,
        ),
    )


def test_indicator_and_onion_roundtrip():
    assert classify_indicator("1.1.1.1").kind == "ip"
    assert classify_indicator("example.com").kind == "domain"
    assert classify_indicator("CVE-2026-12345").normalized == "CVE-2026-12345"

    onion = "http://abcdefghijklmnop.onion/path?q=1"
    gateway = onion_to_gateway(onion)
    assert gateway == "https://abcdefghijklmnop.onion.pet/path?q=1"
    assert gateway_to_onion(gateway) == onion


def test_private_and_metadata_urls_are_blocked():
    for value in (
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost/",
    ):
        with pytest.raises(UnsafeTarget):
            validate_public_url(value, resolve_host=False)
    assert validate_public_url("https://example.com/a", resolve_host=False) == "https://example.com/a"


def test_provider_budget_is_enforced(tmp_path: Path):
    config = ValravnSettings(
        workspace_root=tmp_path,
        policy=ProviderPolicy(no_key_quick=1, free_key_quick=1, scarce_quick=0),
    )
    candidates = [
        Candidate("public-a", "no_key", 100, lambda: True),
        Candidate("public-b", "no_key", 90, lambda: True),
        Candidate("free-a", "free_key", 100, lambda: True),
        Candidate("scarce-a", "scarce", 100, lambda: True),
    ]
    assert select_candidates(config, candidates, depth="quick") == ["public-a", "free-a"]


def test_ioc_fanout_preserves_partial_failures(settings: ValravnSettings):
    gateway = ValravnGateway(settings, native=FakeNative())
    result = gateway.investigate_ioc("1.1.1.1", depth="quick")
    evidence = {item["source"]: item for item in result["evidence"]}

    assert evidence["threatfox"]["ok"] is True
    assert evidence["otx"]["ok"] is False
    assert evidence["urlhaus"]["ok"] is True
    assert result["summary"]["successful_sources"] >= 2
    assert result["summary"]["failed_sources"] == 1
    assert result["handling"] == "untrusted_external_content"


def test_safe_browsing_fails_closed_in_commercial_mode():
    with pytest.raises(PolicyDenied):
        NativeIntelClient().google_safe_browsing(["https://example.com"], usage_mode="commercial")
