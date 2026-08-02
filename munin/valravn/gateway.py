"""Cost-aware, evidence-preserving reconnaissance gateway."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .browser import CloakObserver
from .config import Depth, ValravnSettings
from .native_apis import NativeIntelClient
from .policy import Candidate, Tier, select_candidates
from .security import classify_indicator, is_onion_url, safe_artifact_dir, validate_public_url, write_artifact


@dataclass(frozen=True)
class EvidencePlan:
    source: str
    tier: Tier
    priority: int
    configured: Callable[[], bool]
    call: Callable[[], Any]


class ValravnGateway:
    def __init__(self, settings: ValravnSettings | None = None, *, native: NativeIntelClient | None = None, browser: CloakObserver | None = None) -> None:
        self.settings = settings or ValravnSettings.from_env()
        self.native = native or NativeIntelClient(connect_timeout=self.settings.http_connect_timeout, read_timeout=self.settings.http_read_timeout)
        self.browser = browser or CloakObserver(self.settings)

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _depth(value: str | bool) -> Depth:
        normalized = "deep" if value is True else "quick" if value is False else str(value or "quick").lower()
        if normalized not in {"quick", "deep"}:
            raise ValueError("depth must be quick or deep")
        return normalized  # type: ignore[return-value]

    @staticmethod
    def _configured(name: str) -> Callable[[], bool]:
        return lambda: bool(os.environ.get(name, "").strip())

    @staticmethod
    def _always() -> bool:
        return True

    def _evidence(self, source: str, call: Callable[[], Any]) -> dict[str, Any]:
        started = self._now()
        try:
            data = call()
            rendered = json.dumps(data, ensure_ascii=False, default=str)
            if len(rendered) > self.settings.max_output_chars:
                data = {"truncated": True, "original_chars": len(rendered), "preview": rendered[: self.settings.max_output_chars]}
            return {"source": source, "ok": True, "started_at": started, "retrieved_at": self._now(), "data": data}
        except Exception as exc:
            return {"source": source, "ok": False, "started_at": started, "retrieved_at": self._now(), "error": {"type": type(exc).__name__, "message": str(exc)}}

    def _fanout(self, plans: list[EvidencePlan], depth: Depth) -> tuple[list[dict[str, Any]], list[str]]:
        selected = set(select_candidates(self.settings, [Candidate(p.source, p.tier, p.priority, p.configured) for p in plans], depth=depth))
        chosen = [p for p in plans if p.source in selected]
        skipped = [p.source for p in plans if p.source not in selected]
        if not chosen:
            return [], skipped
        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=min(self.settings.max_workers, len(chosen)), thread_name_prefix="valravn") as pool:
            futures = {pool.submit(self._evidence, p.source, p.call): p.source for p in chosen}
            for future in as_completed(futures):
                results.append(future.result())
        order = {p.source: i for i, p in enumerate(chosen)}
        results.sort(key=lambda item: order.get(str(item.get("source")), 999))
        return results, skipped

    @staticmethod
    def _summary(evidence: list[dict[str, Any]]) -> dict[str, int]:
        return {"successful_sources": sum(bool(x.get("ok")) for x in evidence), "failed_sources": sum(not bool(x.get("ok")) for x in evidence), "total_sources": len(evidence)}

    def _result(self, subject: dict[str, Any], depth: Depth, plans: list[EvidencePlan]) -> dict[str, Any]:
        evidence, skipped = self._fanout(plans, depth)
        return {**subject, "depth": depth, "summary": self._summary(evidence), "evidence": evidence, "skipped_sources": skipped, "handling": "untrusted_external_content"}

    _PROBE_ENDPOINTS = {
        "ripestat": "https://stat.ripe.net/data/network-info/data.json?resource=AS3333",
        "wayback": "https://web.archive.org/cdx/search/cdx?url=example.com&limit=1&output=json",
        "commoncrawl": "https://index.commoncrawl.org/collinfo.json",
    }

    def status(self, probe: bool = False) -> dict[str, Any]:
        sources = self.settings.configured_sources()
        result = {"name": "Valravn", "version": "3.0.0", "usage_mode": self.settings.usage_mode, "ready_capabilities": sum(sources.values()), "sources": sources, "policy": {"quick": {"no_key": self.settings.policy.no_key_quick, "free_key": self.settings.policy.free_key_quick, "scarce": self.settings.policy.scarce_quick}, "deep": {"no_key": self.settings.policy.no_key_deep, "free_key": self.settings.policy.free_key_deep, "scarce": self.settings.policy.scarce_deep}}, "probe_requested": bool(probe)}
        if probe:
            result["probe"] = self._probe_sources(sources)
        return result

    def _probe_sources(self, sources: dict[str, bool]) -> dict[str, Any]:
        endpoints = dict(self._PROBE_ENDPOINTS)
        if sources.get("urlscan"):
            endpoints["urlscan"] = "https://urlscan.io/api/v1/search/?q=domain:example.com&size=1"
        if sources.get("netlas"):
            endpoints["netlas"] = "https://app.netlas.io/api/responses/?q=ip:1.1.1.1&start=0&size=1"
        if sources.get("shodan"):
            endpoints["shodan"] = "https://api.shodan.io/shodan/host/1.1.1.1?minify=true"
        if sources.get("censys"):
            endpoints["censys"] = "https://search.censys.io/api/v2/hosts/1.1.1.1"
        if sources.get("virustotal"):
            endpoints["virustotal"] = "https://www.virustotal.com/api/v3/ip_addresses/1.1.1.1"
        if sources.get("leakix"):
            endpoints["leakix"] = "https://leakix.net/api/host/1.1.1.1"
        if sources.get("zoomeye"):
            endpoints["zoomeye"] = "https://api.zoomeye.ai/v2/search"
        if sources.get("abuseipdb"):
            endpoints["abuseipdb"] = "https://api.abuseipdb.com/api/v2/check?ipAddress=1.1.1.1"
        results: dict[str, Any] = {}
        for name, url in endpoints.items():
            started = time.monotonic()
            try:
                response = self.native.session.get(url, timeout=(2.0, 5.0), allow_redirects=True)
                results[name] = {
                    "reachable": response.status_code < 500,
                    "http_status": response.status_code,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                }
            except Exception as exc:
                results[name] = {
                    "reachable": False,
                    "error": type(exc).__name__,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                }
        return results

    def investigate_ioc(self, indicator: str, *, depth: str = "quick") -> dict[str, Any]:
        item = classify_indicator(indicator)
        level = self._depth(depth)
        if item.kind == "cve":
            return self.investigate_cve(item.normalized, depth=level)
        value, kind = item.normalized, item.kind
        plans = [EvidencePlan("threatfox", "no_key", 100, self._always, lambda: self.native.threatfox(value))]
        if kind != "email":
            plans.append(EvidencePlan("otx", "no_key", 95, self._always, lambda: self.native.otx(value, kind)))
        if kind in {"ip", "domain", "url"}:
            plans.append(EvidencePlan("urlhaus", "no_key", 98, self._always, lambda: self.native.urlhaus(value, kind)))
        if kind in {"ip", "domain", "hash"}:
            plans.append(EvidencePlan("threatminer", "no_key", 75, self._always, lambda: self.native.threatminer(value, kind)))
        if kind == "hash":
            plans.append(EvidencePlan("malware-bazaar", "no_key", 110, self._always, lambda: self.native.malware_bazaar(value)))
        if kind == "ip":
            plans += [EvidencePlan("greynoise", "no_key", 110, self._always, lambda: self.native.greynoise(value)), EvidencePlan("abuseipdb", "free_key", 110, self._configured("ABUSEIPDB_API_KEY"), lambda: self.native.abuseipdb(value)), EvidencePlan("shodan", "free_key", 100, self._configured("SHODAN_API_KEY"), lambda: self.native.shodan_host(value)), EvidencePlan("censys", "free_key", 95, lambda: self._configured("CENSYS_API_ID")() and self._configured("CENSYS_API_SECRET")(), lambda: self.native.censys_host(value))]
        if kind in {"ip", "domain", "hash"}:
            plans.append(EvidencePlan("virustotal", "free_key", 105, self._configured("VT_API_KEY"), lambda: self.native.virustotal(value, kind)))
        if kind in {"ip", "domain"}:
            plans += [EvidencePlan("netlas", "free_key", 85, self._configured("NETLAS_API_KEY"), lambda: self.native.netlas_host(value)), EvidencePlan("leakix", "free_key", 80, self._configured("LEAKIX_API_KEY"), lambda: self.native.leakix_lookup(value, kind)), EvidencePlan("fullhunt", "scarce", 100, lambda: self.settings.fullhunt_enabled and self._configured("FULLHUNT_API_KEY")(), lambda: self.native.fullhunt_lookup(value, kind))]
        if kind == "domain":
            plans.append(EvidencePlan("urlscan-history", "free_key", 80, self._configured("URLSCAN_API_KEY"), lambda: self.native.urlscan_search(f"domain:{value}", 20)))
        if kind == "url":
            plans += [EvidencePlan("urlscan-history", "free_key", 100, self._configured("URLSCAN_API_KEY"), lambda: self.native.urlscan_search(f'page.url:"{value}"', 20)), EvidencePlan("google-safe-browsing", "free_key", 105, lambda: self.settings.safe_browsing_enabled and self.settings.usage_mode != "commercial" and self._configured("GOOGLE_SAFE_BROWSING_API_KEY")(), lambda: self.native.google_safe_browsing([value], usage_mode=self.settings.usage_mode))]
        return self._result({"indicator": {"value": value, "type": kind}}, level, plans)

    def investigate_organization(self, organization: str, domain: str = "", *, depth: str = "deep") -> dict[str, Any]:
        level = self._depth(depth)
        target = domain.strip().lower() or organization.strip()
        plans = [EvidencePlan("ransomware-live", "no_key", 110, self._always, lambda: self.native.ransomware_search(organization)), EvidencePlan("hibp-domain", "no_key", 90, lambda: bool(domain), lambda: self.native.hibp_domain_breaches(domain)), EvidencePlan("wayback", "no_key", 90, lambda: bool(domain), lambda: self.native.wayback_cdx(f"*.{domain}/*", limit=100)), EvidencePlan("commoncrawl", "no_key", 85, lambda: bool(domain), lambda: self.native.commoncrawl_index(f"*.{domain}/*", limit=100)), EvidencePlan("urlscan-history", "free_key", 95, lambda: bool(domain) and self._configured("URLSCAN_API_KEY")(), lambda: self.native.urlscan_search(f"domain:{domain}", 50)), EvidencePlan("netlas", "free_key", 80, self._configured("NETLAS_API_KEY"), lambda: self.native.netlas_search(target, 25)), EvidencePlan("leakix", "free_key", 85, self._configured("LEAKIX_API_KEY"), lambda: self.native.leakix_search(target, 25))]
        return self._result({"organization": organization, "domain": domain or None}, level, plans)

    def search_assets(self, query: str, limit: int = 25, *, depth: str = "quick") -> dict[str, Any]:
        level = self._depth(depth)
        plans = [EvidencePlan("shodan", "free_key", 110, self._configured("SHODAN_API_KEY"), lambda: self.native.shodan_search(query, limit)), EvidencePlan("censys", "free_key", 105, lambda: self._configured("CENSYS_API_ID")() and self._configured("CENSYS_API_SECRET")(), lambda: self.native.censys_search(query, limit)), EvidencePlan("netlas", "free_key", 100, self._configured("NETLAS_API_KEY"), lambda: self.native.netlas_search(query, limit)), EvidencePlan("zoomeye", "free_key", 95, self._configured("ZOOMEYE_API_KEY"), lambda: self.native.zoomeye_search(query, limit)), EvidencePlan("leakix", "free_key", 90, self._configured("LEAKIX_API_KEY"), lambda: self.native.leakix_search(query, limit))]
        return self._result({"query": query, "limit": limit}, level, plans)

    def investigate_cve(self, cve_or_product: str, version: str = "", *, depth: str = "quick") -> dict[str, Any]:
        level = self._depth(depth)
        cve_match = re.fullmatch(r"CVE-\d{4}-\d{4,}", cve_or_product, re.I)
        query = cve_or_product.upper() if cve_match else " ".join(x for x in (cve_or_product, version) if x)
        plans = [EvidencePlan("nvd", "no_key", 110, self._always, lambda: self.native.nvd(query))]
        if cve_match:
            plans += [EvidencePlan("cisa-kev", "no_key", 120, self._always, lambda: self.native.kev(query)), EvidencePlan("epss", "no_key", 115, self._always, lambda: self.native.epss(query)), EvidencePlan("otx", "no_key", 70, self._always, lambda: self.native.otx(query, "cve")), EvidencePlan("github-exploit-references", "no_key", 85, self._always, lambda: self.native.github_exploit_references(query, 10))]
        return self._result({"query": query, "version": version or None}, level, plans)

    def investigate_network(self, resource: str, prefix: str = "", location: str = "", *, depth: str = "quick", starttime: str = "", endtime: str = "") -> dict[str, Any]:
        level = self._depth(depth)
        asn_match = re.search(r"(?:AS)?(\d+)", resource, re.I)
        asn = int(asn_match.group(1)) if asn_match and resource.upper().startswith("AS") else 0
        plans = [EvidencePlan("ripestat", "no_key", 120, self._always, lambda: self.native.routing_context(resource, prefix=prefix, starttime=starttime, endtime=endtime)), EvidencePlan("cloudflare-radar", "free_key", 90, self._configured("CLOUDFLARE_RADAR_TOKEN"), lambda: self.native.cloudflare_radar(asn=asn, location=location))]
        return self._result({"resource": resource, "prefix": prefix or None, "location": location or None}, level, plans)

    def search_historical_web(self, domain: str, *, limit: int = 100, from_year: str = "", to_year: str = "", include_javascript: bool = True, depth: str = "deep") -> dict[str, Any]:
        level = self._depth(depth)
        pattern = f"*.{domain.rstrip('.')}/*"
        plans = [EvidencePlan("wayback", "no_key", 120, self._always, lambda: self.native.wayback_cdx(pattern, limit=limit, from_year=from_year, to_year=to_year)), EvidencePlan("commoncrawl", "no_key", 115, self._always, lambda: self.native.commoncrawl_index(pattern, limit=limit)), EvidencePlan("urlscan-history", "free_key", 80, self._configured("URLSCAN_API_KEY"), lambda: self.native.urlscan_search(f"domain:{domain}", min(limit, 100)))]
        result = self._result({"domain": domain, "pattern": pattern}, level, plans)
        urls: set[str] = set()
        for item in result["evidence"]:
            data = item.get("data") or {}
            for row in data.get("records", []) if isinstance(data, dict) else []:
                if not isinstance(row, dict):
                    continue
                value = row.get("original") or row.get("url")
                if value and (include_javascript or not str(value).lower().split("?", 1)[0].endswith(".js")):
                    urls.add(str(value))
            for row in data.get("results", []) if isinstance(data, dict) else []:
                value = (row.get("page") or {}).get("url") if isinstance(row, dict) else None
                if value:
                    urls.add(str(value))
        result["unique_urls"] = sorted(urls)[:limit]
        return result

    def investigate_url(self, url: str, *, depth: str = "quick") -> dict[str, Any]:
        validated = validate_public_url(url, resolve_host=self.settings.resolve_public_hosts)
        level = self._depth(depth)
        plans = [EvidencePlan("threatfox", "no_key", 100, self._always, lambda: self.native.threatfox(validated)), EvidencePlan("urlhaus", "no_key", 110, self._always, lambda: self.native.urlhaus(validated, "url")), EvidencePlan("otx", "no_key", 90, self._always, lambda: self.native.otx(validated, "url")), EvidencePlan("urlscan-history", "free_key", 100, self._configured("URLSCAN_API_KEY"), lambda: self.native.urlscan_search(f'page.url:"{validated}"', 25)), EvidencePlan("safe-browsing", "free_key", 95, lambda: self.settings.safe_browsing_enabled and self.settings.usage_mode != "commercial" and self._configured("GOOGLE_SAFE_BROWSING_API_KEY")(), lambda: self.native.google_safe_browsing([validated], usage_mode=self.settings.usage_mode))]
        return self._result({"url": validated}, level, plans)

    def submit_url(self, url: str, *, visibility: str = "unlisted", depth: str = "quick") -> dict[str, Any]:
        validated = validate_public_url(url, resolve_host=self.settings.resolve_public_hosts)
        level = self._depth(depth)
        plans = [EvidencePlan("urlscan-submit", "free_key", 130, self._configured("URLSCAN_API_KEY"), lambda: self.native.urlscan_submit(validated, visibility))]
        plans.append(EvidencePlan("cloudflare-url-scan", "free_key", 125, lambda: self._configured("CLOUDFLARE_ACCOUNT_ID")() and self._configured("CLOUDFLARE_URL_SCANNER_TOKEN")(), lambda: self.native.cloudflare_url_scan(validated, visibility)))
        return self._result({"url": validated, "visibility": visibility}, level, plans)

    def validate_asset(self, target: str, *, depth: str = "deep") -> dict[str, Any]:
        item = classify_indicator(target)
        if item.kind not in {"ip", "domain"}:
            raise ValueError("asset validation requires an IP or domain")
        level = self._depth(depth)
        plans = [EvidencePlan("netlas", "free_key", 110, self._configured("NETLAS_API_KEY"), lambda: self.native.netlas_host(item.normalized)), EvidencePlan("leakix", "free_key", 105, self._configured("LEAKIX_API_KEY"), lambda: self.native.leakix_lookup(item.normalized, item.kind)), EvidencePlan("shodan", "free_key", 100, lambda: item.kind == "ip" and self._configured("SHODAN_API_KEY")(), lambda: self.native.shodan_host(item.normalized)), EvidencePlan("censys", "free_key", 95, lambda: item.kind == "ip" and self._configured("CENSYS_API_ID")() and self._configured("CENSYS_API_SECRET")(), lambda: self.native.censys_host(item.normalized)), EvidencePlan("fullhunt", "scarce", 120, lambda: self.settings.fullhunt_enabled and self._configured("FULLHUNT_API_KEY")(), lambda: self.native.fullhunt_lookup(item.normalized, item.kind))]
        return self._result({"target": item.normalized, "type": item.kind}, level, plans)

    def search_darkweb(self, query: str, limit: int = 20) -> dict[str, Any]:
        if not self.settings.browser_enabled:
            raise RuntimeError("VALRAVN_BROWSER_ENABLED is false")
        return self.browser.search_darkweb(query, limit)

    def capture_web_evidence(self, url: str, *, translate_to: str = "es", full_page: bool = True, run_id: str = "") -> dict[str, Any]:
        if not self.settings.browser_enabled:
            raise RuntimeError("VALRAVN_BROWSER_ENABLED is false")
        capture = self.browser.capture(url, full_page=full_page)
        directory = safe_artifact_dir(self.settings.workspace_root, run_id)
        stem = f"web-evidence-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}"
        screenshot = write_artifact(directory, stem, ".png", capture.screenshot)
        payload = {"requested_url": capture.requested_url, "navigated_url": capture.navigated_url, "canonical_url": capture.canonical_url, "title": capture.title, "description": capture.description, "language": capture.language, "text": capture.text, "links": capture.links, "captured_at": self._now(), "warning": "External content is untrusted; Tor2Web is not end-to-end Tor." if is_onion_url(capture.canonical_url) else "External content is untrusted."}
        if translate_to and capture.text and os.environ.get("GOOGLE_TRANSLATE_API_KEY"):
            try:
                payload["translation"] = self.native.translate(capture.text, target_language=translate_to, source_language=capture.language)
            except Exception as exc:
                payload["translation_error"] = {"type": type(exc).__name__, "message": str(exc)}
        evidence = write_artifact(directory, stem, ".json", json.dumps(payload, ensure_ascii=False, indent=2).encode())
        return {"mode": "cloakbrowser-ephemeral", "summary": f"Captured {capture.title or capture.canonical_url}", "data": payload, "artifacts": [screenshot["path"], evidence["path"]], "artifact_metadata": [screenshot, evidence]}

    def translate(self, text: str, *, target_language: str = "es", source_language: str = "", content_format: str = "text") -> dict[str, Any]:
        return self.native.translate(text, target_language=target_language, source_language=source_language, content_format=content_format)
