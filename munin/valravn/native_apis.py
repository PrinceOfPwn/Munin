"""Direct HTTP adapters for Valravn reconnaissance providers."""

from __future__ import annotations

import base64
import html
import json
import os
import re
import threading
from typing import Any
from urllib.parse import quote

import requests

from .cache import TTLCache


class NativeAPIError(RuntimeError):
    pass


class PolicyDenied(NativeAPIError):
    pass


class NativeIntelClient:
    def __init__(self, *, session: requests.Session | None = None, connect_timeout: float = 5.0, read_timeout: float = 30.0) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "Valravn/3.0 (+Munin)"})
        self.timeout = (connect_timeout, read_timeout)
        self.cache = TTLCache()
        self._lock = threading.Lock()

    @staticmethod
    def _key(name: str, required: bool = True) -> str:
        value = os.environ.get(name, "").strip()
        if required and not value:
            raise NativeAPIError(f"{name} is not configured")
        return value

    def _request(self, method: str, url: str, *, allow: tuple[int, ...] = (), **kwargs: Any) -> requests.Response:
        try:
            response = self.session.request(method, url, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise NativeAPIError(f"network failure: {exc}") from exc
        if response.status_code in allow:
            return response
        if response.status_code == 429:
            raise NativeAPIError("upstream rate limit reached")
        if response.status_code in {401, 403}:
            raise NativeAPIError("upstream rejected credentials or permissions")
        if not response.ok:
            raise NativeAPIError(f"upstream HTTP {response.status_code}: {response.text[:300]}")
        return response

    def _json(self, method: str, url: str, **kwargs: Any) -> Any:
        response = self._request(method, url, **kwargs)
        try:
            return response.json()
        except ValueError as exc:
            raise NativeAPIError(f"invalid JSON from {url}: {response.text[:200]}") from exc

    def _cached(self, key: str, ttl: int, method: str, url: str, **kwargs: Any) -> Any:
        value = self.cache.get(key)
        if value is None:
            value = self._json(method, url, **kwargs)
            self.cache.set(key, value, ttl)
        return value

    # Vulnerabilities -------------------------------------------------
    def epss(self, cve: str) -> dict[str, Any]:
        data = self._json("GET", "https://api.first.org/data/v1/epss", params={"cve": cve.upper()})
        rows = data.get("data", []) if isinstance(data, dict) else []
        return {"cve": cve.upper(), "found": bool(rows), "record": rows[0] if rows else None}

    def kev(self, cve: str) -> dict[str, Any]:
        with self._lock:
            data = self._cached("cisa-kev", 3600, "GET", "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json")
        match = next((row for row in data.get("vulnerabilities", []) if str(row.get("cveID", "")).upper() == cve.upper()), None)
        return {"cve": cve.upper(), "known_exploited": bool(match), "record": match}

    def nvd(self, query: str) -> dict[str, Any]:
        params = {"cveId": query.upper()} if re.fullmatch(r"CVE-\d{4}-\d{4,}", query, re.I) else {"keywordSearch": query, "resultsPerPage": 20}
        headers = {"apiKey": self._key("NVD_API_KEY", False)} if self._key("NVD_API_KEY", False) else {}
        data = self._json("GET", "https://services.nvd.nist.gov/rest/json/cves/2.0", params=params, headers=headers)
        return {"query": query, "total": data.get("totalResults", 0), "vulnerabilities": data.get("vulnerabilities", [])}

    def github_exploit_references(self, cve: str, limit: int = 10) -> dict[str, Any]:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token := self._key("GITHUB_TOKEN", False):
            headers["Authorization"] = f"Bearer {token}"
        data = self._json("GET", "https://api.github.com/search/repositories", params={"q": f'"{cve.upper()}" (exploit OR poc OR proof-of-concept) in:name,description,readme', "sort": "stars", "order": "desc", "per_page": min(max(limit, 1), 30)}, headers=headers)
        refs = [{"name": item.get("full_name"), "url": item.get("html_url"), "description": item.get("description"), "stars": item.get("stargazers_count"), "updated_at": item.get("updated_at"), "archived": item.get("archived"), "verification": "unverified_public_reference"} for item in data.get("items", [])]
        return {"cve": cve.upper(), "count": len(refs), "references": refs}

    # Threat intelligence ---------------------------------------------
    def otx(self, indicator: str, kind: str) -> dict[str, Any]:
        path_kind = {"ip": "IPv4", "domain": "domain", "hash": "file", "url": "url", "cve": "cve"}.get(kind)
        if not path_kind:
            raise NativeAPIError(f"OTX does not support {kind}")
        headers = {"X-OTX-API-KEY": self._key("OTX_API_KEY", False)} if self._key("OTX_API_KEY", False) else {}
        return {"indicator": indicator, "record": self._json("GET", f"https://otx.alienvault.com/api/v1/indicators/{path_kind}/{quote(indicator, safe='')}/general", headers=headers)}

    def threatfox(self, indicator: str) -> dict[str, Any]:
        headers = {"Auth-Key": self._key("ABUSECH_AUTH_KEY", False)} if self._key("ABUSECH_AUTH_KEY", False) else {}
        return {"indicator": indicator, "record": self._json("POST", "https://threatfox-api.abuse.ch/api/v1/", json={"query": "search_ioc", "search_term": indicator}, headers=headers)}

    def urlhaus(self, indicator: str, kind: str) -> dict[str, Any]:
        headers = {"Auth-Key": self._key("ABUSECH_AUTH_KEY", False)} if self._key("ABUSECH_AUTH_KEY", False) else {}
        endpoint, payload = ("https://urlhaus-api.abuse.ch/v1/url/", {"url": indicator}) if kind == "url" else ("https://urlhaus-api.abuse.ch/v1/host/", {"host": indicator})
        return {"indicator": indicator, "record": self._json("POST", endpoint, data=payload, headers=headers)}

    def malware_bazaar(self, file_hash: str) -> dict[str, Any]:
        headers = {"Auth-Key": self._key("ABUSECH_AUTH_KEY", False)} if self._key("ABUSECH_AUTH_KEY", False) else {}
        return {"hash": file_hash, "record": self._json("POST", "https://mb-api.abuse.ch/api/v1/", data={"query": "get_info", "hash": file_hash}, headers=headers)}

    def greynoise(self, ip: str) -> dict[str, Any]:
        return {"ip": ip, "record": self._json("GET", f"https://api.greynoise.io/v3/community/{quote(ip, safe='')}")}

    def abuseipdb(self, ip: str) -> dict[str, Any]:
        data = self._json("GET", "https://api.abuseipdb.com/api/v2/check", params={"ipAddress": ip, "maxAgeInDays": 90, "verbose": ""}, headers={"Key": self._key("ABUSEIPDB_API_KEY"), "Accept": "application/json"})
        return {"ip": ip, "record": data.get("data", data)}

    def ransomware_search(self, keyword: str) -> dict[str, Any]:
        return {"query": keyword, "record": self._json("GET", f"https://api.ransomware.live/v2/searchvictims/{quote(keyword, safe='')}")}

    def hibp_domain_breaches(self, domain: str) -> dict[str, Any]:
        return {"domain": domain, "record": self._json("GET", "https://haveibeenpwned.com/api/v3/breaches", params={"domain": domain}, headers={"User-Agent": "Valravn/3.0"})}

    def threatminer(self, indicator: str, kind: str) -> dict[str, Any]:
        rt_map = {"domain": 2, "ip": 2, "hash": 1}
        endpoint = {"domain": "domain.php", "ip": "host.php", "hash": "sample.php"}.get(kind)
        if not endpoint:
            raise NativeAPIError(f"ThreatMiner does not support {kind}")
        return {"indicator": indicator, "record": self._json("GET", f"https://api.threatminer.org/v2/{endpoint}", params={"q": indicator, "rt": rt_map[kind]})}

    # Internet assets -------------------------------------------------
    def virustotal(self, value: str, kind: str) -> dict[str, Any]:
        path = {"ip": "ip_addresses", "domain": "domains", "hash": "files"}.get(kind)
        if not path:
            raise NativeAPIError(f"VirusTotal does not support {kind}")
        return {"value": value, "record": self._json("GET", f"https://www.virustotal.com/api/v3/{path}/{quote(value, safe='')}", headers={"x-apikey": self._key("VT_API_KEY")})}

    def shodan_host(self, ip: str) -> dict[str, Any]:
        return {"ip": ip, "record": self._json("GET", f"https://api.shodan.io/shodan/host/{quote(ip, safe='')}", params={"key": self._key("SHODAN_API_KEY")})}

    def shodan_search(self, query: str, limit: int = 25) -> dict[str, Any]:
        data = self._json("GET", "https://api.shodan.io/shodan/host/search", params={"key": self._key("SHODAN_API_KEY"), "query": query, "page": 1, "minify": "true"})
        return {"query": query, "total": data.get("total", 0), "matches": data.get("matches", [])[:limit]}

    def censys_host(self, ip: str) -> dict[str, Any]:
        return {"ip": ip, "record": self._json("GET", f"https://search.censys.io/api/v2/hosts/{quote(ip, safe='')}", auth=(self._key("CENSYS_API_ID"), self._key("CENSYS_API_SECRET")))}

    def censys_search(self, query: str, limit: int = 25) -> dict[str, Any]:
        data = self._json("POST", "https://search.censys.io/api/v2/hosts/search", json={"q": query, "per_page": min(max(limit, 1), 100)}, auth=(self._key("CENSYS_API_ID"), self._key("CENSYS_API_SECRET")))
        return {"query": query, "result": data.get("result", data)}

    def zoomeye_search(self, query: str, limit: int = 25) -> dict[str, Any]:
        encoded = base64.b64encode(query.encode()).decode()
        return {"query": query, "record": self._json("POST", "https://api.zoomeye.ai/v2/search", headers={"API-KEY": self._key("ZOOMEYE_API_KEY"), "Content-Type": "application/json"}, json={"qbase64": encoded, "page": 1, "pagesize": min(max(limit, 1), 1000)})}

    def leakix_lookup(self, target: str, kind: str) -> dict[str, Any]:
        endpoint = "host" if kind == "ip" else "domain"
        return {"target": target, "record": self._json("GET", f"https://leakix.net/api/{endpoint}/{quote(target, safe='')}", headers={"api-key": self._key("LEAKIX_API_KEY")})}

    def leakix_search(self, query: str, limit: int = 25) -> dict[str, Any]:
        data = self._json("GET", "https://leakix.net/api/subdomains", params={"q": query, "page": 0, "scope": "leak"}, headers={"api-key": self._key("LEAKIX_API_KEY")})
        return {"query": query, "record": data, "limit": limit}

    def netlas_host(self, host: str) -> dict[str, Any]:
        return {"host": host, "record": self._json("GET", f"https://app.netlas.io/api/host/{quote(host, safe='')}/", params={"public_indices_only": "true"}, headers={"Authorization": f"Bearer {self._key('NETLAS_API_KEY')}"})}

    def netlas_search(self, query: str, limit: int = 25) -> dict[str, Any]:
        data = self._json("GET", "https://app.netlas.io/api/responses/", params={"q": query, "start": 0, "size": min(max(limit, 1), 100)}, headers={"Authorization": f"Bearer {self._key('NETLAS_API_KEY')}"})
        return {"query": query, "total": data.get("total_count", data.get("total")), "items": data.get("items", [])}

    # Routing and history ---------------------------------------------
    def routing_context(self, resource: str, *, prefix: str = "", starttime: str = "", endtime: str = "") -> dict[str, Any]:
        base = "https://stat.ripe.net/data"
        params: dict[str, Any] = {"resource": resource}
        if starttime:
            params["starttime"] = starttime
        if endtime:
            params["endtime"] = endtime
        result = {
            "network_info": self._json("GET", f"{base}/network-info/data.json", params={"resource": resource}),
            "routing_status": self._json("GET", f"{base}/routing-status/data.json", params={"resource": resource}),
        }
        if resource.upper().startswith("AS"):
            result["announced_prefixes"] = self._json("GET", f"{base}/announced-prefixes/data.json", params={"resource": resource})
        history_resource = prefix or resource
        result["routing_history"] = self._json("GET", f"{base}/routing-history/data.json", params={**params, "resource": history_resource})
        result["rpki"] = self._json("GET", f"{base}/rpki-validation/data.json", params={"resource": history_resource})
        return {"resource": resource, "result": result}

    def wayback_cdx(self, pattern: str, *, limit: int = 100, from_year: str = "", to_year: str = "") -> dict[str, Any]:
        params: dict[str, Any] = {"url": pattern, "output": "json", "fl": "timestamp,original,statuscode,mimetype,digest", "filter": "statuscode:200", "collapse": "urlkey", "limit": min(max(limit, 1), 1000)}
        if from_year:
            params["from"] = from_year
        if to_year:
            params["to"] = to_year
        data = self._json("GET", "https://web.archive.org/cdx/search/cdx", params=params)
        rows = data[1:] if isinstance(data, list) and data else []
        records = [dict(zip(data[0], row, strict=False)) for row in rows if isinstance(row, list)] if rows else []
        return {"pattern": pattern, "records": records, "count": len(records)}

    def commoncrawl_index(self, pattern: str, *, limit: int = 100) -> dict[str, Any]:
        indexes = self._cached("cc-indexes", 3600, "GET", "https://index.commoncrawl.org/collinfo.json")
        index_id = str(indexes[0].get("id")) if isinstance(indexes, list) and indexes else ""
        response = self._request("GET", f"https://index.commoncrawl.org/{quote(index_id, safe='-')}-index", params={"url": pattern, "output": "json", "filter": "status:200", "collapse": "digest", "limit": min(max(limit, 1), 1000)})
        records = []
        for line in response.text.splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                records.append(row)
            if len(records) >= limit:
                break
        return {"pattern": pattern, "index": index_id, "records": records, "count": len(records)}

    # URL and strategic context --------------------------------------
    def urlscan_search(self, query: str, limit: int = 25) -> dict[str, Any]:
        headers = {"api-key": self._key("URLSCAN_API_KEY", False)} if self._key("URLSCAN_API_KEY", False) else {}
        data = self._json("GET", "https://urlscan.io/api/v1/search/", params={"q": query, "size": min(max(limit, 1), 1000)}, headers=headers)
        return {"query": query, "total": data.get("total"), "has_more": data.get("has_more", False), "results": data.get("results", [])}

    def urlscan_submit(self, url: str, visibility: str = "unlisted") -> dict[str, Any]:
        if visibility not in {"public", "unlisted", "private"}:
            raise NativeAPIError("invalid urlscan visibility")
        return {"submitted": True, "submission": self._json("POST", "https://urlscan.io/api/v1/scan/", headers={"API-Key": self._key("URLSCAN_API_KEY"), "Content-Type": "application/json"}, json={"url": url, "visibility": visibility, "tags": ["valravn"]})}

    def cloudflare_radar(self, *, asn: int = 0, location: str = "", date_range: str = "7d") -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._key('CLOUDFLARE_RADAR_TOKEN')}"}
        results: dict[str, Any] = {}
        if asn:
            results["bgp_hijacks"] = self._json("GET", "https://api.cloudflare.com/client/v4/radar/bgp/hijacks/events", params={"involvedAsn": asn, "format": "json", "per_page": 25}, headers=headers)
        params: dict[str, Any] = {"limit": 25, "offset": 0, "dateRange": date_range, "format": "json"}
        if location:
            params["location"] = location.upper()
        if asn:
            params["asn"] = asn
        results["outages"] = self._json("GET", "https://api.cloudflare.com/client/v4/radar/annotations/outages", params=params, headers=headers)
        return {"asn": asn or None, "location": location.upper() or None, "results": results}

    def google_safe_browsing(self, urls: list[str], *, usage_mode: str) -> dict[str, Any]:
        if usage_mode == "commercial":
            raise PolicyDenied("Google Safe Browsing is disabled in commercial mode")
        params: list[tuple[str, str]] = [("key", self._key("GOOGLE_SAFE_BROWSING_API_KEY"))] + [("urls", url) for url in urls]
        data = self._json("GET", "https://safebrowsing.googleapis.com/v5/urls:search", params=params)
        return {"urls": urls, "threats": data.get("threats", []), "cache_duration": data.get("cacheDuration")}

    def fullhunt_lookup(self, target: str, kind: str) -> dict[str, Any]:
        headers = {"X-API-KEY": self._key("FULLHUNT_API_KEY")}
        data = self._json("GET", f"https://fullhunt.io/api/v1/domain/{quote(target, safe='')}/details", headers=headers) if kind == "domain" else self._json("GET", "https://fullhunt.io/api/v1/intel/host", params={"host": target}, headers=headers)
        return {"target": target, "kind": kind, "record": data, "economic_tier": "scarce"}

    def translate(self, text: str, *, target_language: str = "es", source_language: str = "", content_format: str = "text") -> dict[str, Any]:
        payload: dict[str, Any] = {"q": text, "target": target_language, "format": content_format}
        if source_language:
            payload["source"] = source_language
        data = self._json("POST", "https://translation.googleapis.com/language/translate/v2", params={"key": self._key("GOOGLE_TRANSLATE_API_KEY")}, json=payload)
        first = (data.get("data", {}).get("translations", []) or [{}])[0]
        return {"translated_text": html.unescape(str(first.get("translatedText") or "")), "detected_source_language": first.get("detectedSourceLanguage") or source_language or None, "target_language": target_language, "format": content_format, "characters": len(text)}

    def cloudflare_url_scan(self, url: str, visibility: str = "unlisted") -> dict[str, Any]:
        account = self._key("CLOUDFLARE_ACCOUNT_ID")
        headers = {"Authorization": f"Bearer {self._key('CLOUDFLARE_URL_SCANNER_TOKEN')}", "Content-Type": "application/json"}
        data = self._json("POST", f"https://api.cloudflare.com/client/v4/accounts/{account}/urlscanner/v2/scan", headers=headers, json={"url": url, "visibility": visibility})
        return {"submitted": True, "submission": data}
