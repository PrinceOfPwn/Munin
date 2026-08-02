"""Runtime configuration and provider-budget policy for Valravn."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

UsageMode = Literal["personal", "research", "commercial"]
Depth = Literal["quick", "deep"]


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, lower: int, upper: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(lower, min(value, upper))


def _float(name: str, default: float, lower: float, upper: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(lower, min(value, upper))


@dataclass(frozen=True)
class ProviderPolicy:
    """Limits fan-out by economic tier instead of exposing every provider."""

    no_key_quick: int = 3
    no_key_deep: int = 8
    free_key_quick: int = 2
    free_key_deep: int = 6
    scarce_quick: int = 0
    scarce_deep: int = 1

    def budget(self, tier: str, depth: Depth) -> int:
        return int(getattr(self, f"{tier}_{depth}"))


@dataclass(frozen=True)
class ValravnSettings:
    workspace_root: Path
    max_workers: int = 8
    max_output_chars: int = 180_000
    resolve_public_hosts: bool = True
    http_connect_timeout: float = 5.0
    http_read_timeout: float = 30.0
    usage_mode: UsageMode = "commercial"
    policy: ProviderPolicy = field(default_factory=ProviderPolicy)

    browser_enabled: bool = False
    browser_headless: bool = True
    browser_proxy: str = ""
    onion_gateway_domain: str = "onion.pet"
    darkweb_search_enabled: bool = True

    urlscan_submit_enabled: bool = False
    cloudflare_url_scan_enabled: bool = False
    fullhunt_enabled: bool = False
    safe_browsing_enabled: bool = False

    @staticmethod
    def _validated_gateway_domain(raw: str) -> str:
        import re as _re

        domain = (raw or "").strip().lower().lstrip(".")
        if not _re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*", domain):
            return "onion.pet"
        return domain

    @classmethod
    def from_env(cls) -> ValravnSettings:
        workspace = Path(
            os.environ.get("MUNIN_WORKSPACE")
            or os.environ.get("GITHUB_WORKSPACE")
            or os.getcwd()
        ).resolve()
        usage = os.environ.get("VALRAVN_USAGE_MODE", "commercial").strip().lower()
        if usage not in {"personal", "research", "commercial"}:
            usage = "commercial"
        return cls(
            workspace_root=workspace,
            max_workers=_int("VALRAVN_MAX_WORKERS", 8, 1, 16),
            max_output_chars=_int("VALRAVN_MAX_OUTPUT_CHARS", 180_000, 10_000, 2_000_000),
            resolve_public_hosts=_bool("VALRAVN_RESOLVE_PUBLIC_HOSTS", True),
            http_connect_timeout=_float("VALRAVN_HTTP_CONNECT_TIMEOUT", 5.0, 1.0, 60.0),
            http_read_timeout=_float("VALRAVN_HTTP_READ_TIMEOUT", 30.0, 1.0, 300.0),
            usage_mode=usage,  # type: ignore[arg-type]
            policy=ProviderPolicy(
                no_key_quick=_int("VALRAVN_NO_KEY_QUICK_BUDGET", 3, 0, 20),
                no_key_deep=_int("VALRAVN_NO_KEY_DEEP_BUDGET", 8, 0, 20),
                free_key_quick=_int("VALRAVN_FREE_KEY_QUICK_BUDGET", 2, 0, 20),
                free_key_deep=_int("VALRAVN_FREE_KEY_DEEP_BUDGET", 6, 0, 20),
                scarce_quick=_int("VALRAVN_SCARCE_QUICK_BUDGET", 0, 0, 5),
                scarce_deep=_int("VALRAVN_SCARCE_DEEP_BUDGET", 1, 0, 5),
            ),
            browser_enabled=_bool("VALRAVN_BROWSER_ENABLED", False),
            browser_headless=_bool("VALRAVN_BROWSER_HEADLESS", True),
            browser_proxy=os.environ.get("VALRAVN_BROWSER_PROXY", "").strip(),
            onion_gateway_domain=cls._validated_gateway_domain(
                os.environ.get("VALRAVN_ONION_GATEWAY_DOMAIN", "onion.pet")
            ),
            darkweb_search_enabled=_bool("VALRAVN_DARKWEB_SEARCH_ENABLED", True),
            urlscan_submit_enabled=_bool("VALRAVN_URLSCAN_SUBMIT_ENABLED", False),
            cloudflare_url_scan_enabled=_bool("VALRAVN_CLOUDFLARE_URL_SCAN_ENABLED", False),
            fullhunt_enabled=_bool("VALRAVN_FULLHUNT_ENABLED", False),
            safe_browsing_enabled=_bool("VALRAVN_SAFE_BROWSING_ENABLED", False),
        )

    def configured_sources(self) -> dict[str, bool]:
        """Return capability state without exposing credential values."""
        def key(name: str) -> bool:
            return bool(os.environ.get(name, "").strip())
        return {
            "ripestat": True,
            "wayback": True,
            "commoncrawl": True,
            "urlscan": key("URLSCAN_API_KEY"),
            "netlas": key("NETLAS_API_KEY"),
            "cloudflare_radar": key("CLOUDFLARE_RADAR_TOKEN"),
            "google_safe_browsing": (
                self.safe_browsing_enabled
                and self.usage_mode != "commercial"
                and key("GOOGLE_SAFE_BROWSING_API_KEY")
            ),
            "fullhunt": self.fullhunt_enabled and key("FULLHUNT_API_KEY"),
            "shodan": key("SHODAN_API_KEY"),
            "censys": key("CENSYS_API_ID") and key("CENSYS_API_SECRET"),
            "virustotal": key("VT_API_KEY"),
            "zoomeye": key("ZOOMEYE_API_KEY"),
            "leakix": key("LEAKIX_API_KEY"),
            "otx": True,
            "abusech": True,
            "greynoise": True,
            "abuseipdb": key("ABUSEIPDB_API_KEY"),
            "cloakbrowser": self.browser_enabled,
            "darkweb_gateway": self.browser_enabled and self.darkweb_search_enabled,
        }
