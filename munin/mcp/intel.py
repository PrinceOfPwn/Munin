# tags: [mcp, intel, osint, recon, active-recon, VulnIntelService, cve_lookup, package_vuln_lookup, cve_search, exploit_search, searchsploit, NVD, OSV, EPSS, CISA-KEV]
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import requests

from .config import Settings


def _make_session() -> requests.Session:
    """Fresh session with only User-Agent — no Authorization ever set globally.

    Fix for PR #1 finding: the previous implementation set
    `session.headers['Authorization'] = f"Bearer {github_token}"` at construction time,
    which caused that PAT to leak to every subsequent request (NVD, CIRCL, MITRE,
    EPSS, CISA KEV, OSV). We now use per-provider sessions and only inject the
    Authorization header on the exact call that needs it (GitHub search).
    """
    session = requests.Session()
    session.headers.update({"User-Agent": "munin-mcp/1.0"})
    return session


class VulnIntelService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # One session per external provider — no shared Authorization header.
        self._nvd = _make_session()
        self._circl = _make_session()
        self._mitre = _make_session()
        self._epss = _make_session()
        self._kev = _make_session()
        self._osv = _make_session()
        self._github = _make_session()

    def cve_lookup(self, cve_id: str) -> dict[str, Any]:
        cve_id = cve_id.upper().strip()
        nvd = self._nvd_lookup(cve_id)
        circl = self._circl_lookup(cve_id)
        mitre = self._mitre_lookup(cve_id)
        epss = self._epss_lookup(cve_id)
        kev = self._kev_lookup(cve_id)
        exploits = self.exploit_search(cve_id=cve_id, query="")
        return self._assemble(cve_id, [nvd, circl, mitre], epss=epss, kev=kev, exploits=exploits["data"]["matches"])

    def cve_search(self, query: str, limit: int = 10) -> dict[str, Any]:
        params = {"keywordSearch": query, "resultsPerPage": str(limit)}
        headers = {"apiKey": self.settings.nvd_api_key} if self.settings.nvd_api_key else {}
        response = self._nvd.get(
            "https://services.nvd.nist.gov/rest/json/cves/2.0",
            params=params,
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        matches = []
        for item in data.get("vulnerabilities", []):
            cve = item.get("cve", {})
            cve_id = cve.get("id", "")
            description = self._description(cve)
            metrics = self._metrics(cve)
            matches.append(
                {
                    "cve_id": cve_id,
                    "description": description,
                    "severity": metrics.get("severity", ""),
                    "cvss": metrics.get("score"),
                    "references": [ref.get("url", "") for ref in cve.get("references", [])[:5]],
                    "source": "NVD",
                }
            )
        return {
            "ok": True,
            "tool": "cve_search",
            "mode": "sync",
            "summary": f"found {len(matches)} CVE matches",
            "data": {"query": query, "matches": matches, "source_coverage": ["NVD"]},
        }

    def package_vuln_lookup(self, ecosystem: str, package_name: str, version: str) -> dict[str, Any]:
        payload = {"version": version, "package": {"ecosystem": ecosystem, "name": package_name}}
        response = self._osv.post("https://api.osv.dev/v1/query", json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        matches = []
        for item in data.get("vulns", []):
            aliases = item.get("aliases", [])
            matches.append(
                {
                    "id": item.get("id", ""),
                    "aliases": aliases,
                    "summary": item.get("summary", ""),
                    "details": item.get("details", "")[:600],
                    "references": [ref.get("url", "") for ref in item.get("references", [])[:5]],
                }
            )
        return {
            "ok": True,
            "tool": "package_vuln_lookup",
            "mode": "sync",
            "summary": f"found {len(matches)} package vulnerability matches",
            "data": {
                "query": {"ecosystem": ecosystem, "package_name": package_name, "version": version},
                "matches": matches,
                "source_coverage": ["OSV"],
            },
        }

    def cve_enrich(self, cve_id: str) -> dict[str, Any]:
        return {
            "ok": True,
            "tool": "cve_enrich",
            "mode": "sync",
            "summary": f"enriched {cve_id.upper()}",
            "data": self.cve_lookup(cve_id)["data"],
        }

    def exploit_search(self, cve_id: str = "", query: str = "") -> dict[str, Any]:
        needle = cve_id.upper().strip() or query.strip()
        matches: list[dict[str, Any]] = []
        source_coverage: list[str] = []
        if needle:
            searchsploit_matches = self._searchsploit(needle)
            if searchsploit_matches:
                matches.extend(searchsploit_matches)
                source_coverage.append("SearchSploit")
            github_matches = self._github_search(needle)
            if github_matches:
                matches.extend(github_matches)
                source_coverage.append("GitHub")
            nuclei_matches = self._nuclei_lookup(needle)
            if nuclei_matches:
                matches.extend(nuclei_matches)
                source_coverage.append("Nuclei Templates")
        return {
            "ok": True,
            "tool": "exploit_search",
            "mode": "sync",
            "summary": f"found {len(matches)} exploit references",
            "data": {"query": needle, "matches": matches, "source_coverage": source_coverage},
        }

    def _assemble(
        self,
        cve_id: str,
        records: list[dict[str, Any]],
        *,
        epss: dict[str, Any],
        kev: dict[str, Any],
        exploits: list[dict[str, Any]],
    ) -> dict[str, Any]:
        descriptions = [record.get("description", "") for record in records if record.get("description")]
        affected = []
        refs: list[str] = []
        severity = ""
        cvss = None
        coverage = []
        for record in records:
            if not record:
                continue
            coverage.extend(record.get("source_coverage", []))
            refs.extend(record.get("references", []))
            affected.extend(record.get("affected_products", []))
            if not severity and record.get("severity"):
                severity = record["severity"]
            if cvss is None and record.get("cvss") is not None:
                cvss = record["cvss"]
        return {
            "query": cve_id,
            "matches": [
                {
                    "cve_id": cve_id,
                    "severity": severity,
                    "cvss": cvss,
                    "epss": epss.get("epss"),
                    "kev": kev.get("known_exploited", False),
                    "description": next((item for item in descriptions if item), ""),
                    "affected_products": affected[:20],
                    "references": list(dict.fromkeys([ref for ref in refs if ref]))[:20],
                    "exploits": exploits[:20],
                    "source_coverage": sorted(
                        {
                            item
                            for item in (
                                coverage
                                + (["EPSS"] if epss.get("epss") else [])
                                + (["CISA KEV"] if kev.get("known_exploited") else [])
                            )
                            if item
                        }
                    ),
                }
            ],
        }

    def _nvd_lookup(self, cve_id: str) -> dict[str, Any]:
        headers = {"apiKey": self.settings.nvd_api_key} if self.settings.nvd_api_key else {}
        response = self._nvd.get(
            "https://services.nvd.nist.gov/rest/json/cves/2.0",
            params={"cveId": cve_id},
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        vulns = data.get("vulnerabilities", [])
        if not vulns:
            return {}
        cve = vulns[0].get("cve", {})
        return {
            "description": self._description(cve),
            "severity": self._metrics(cve).get("severity", ""),
            "cvss": self._metrics(cve).get("score"),
            "references": [ref.get("url", "") for ref in cve.get("references", [])[:10]],
            "affected_products": self._affected_products(cve),
            "source_coverage": ["NVD"],
        }

    def _circl_lookup(self, cve_id: str) -> dict[str, Any]:
        response = self._circl.get(f"https://cve.circl.lu/api/cve/{cve_id}", timeout=30)
        if response.status_code != 200:
            return {}
        data = response.json()
        return {
            "description": data.get("summary", ""),
            "references": data.get("references", [])[:10],
            "affected_products": data.get("vulnerable_configuration", [])[:20],
            "source_coverage": ["CIRCL"],
        }

    def _mitre_lookup(self, cve_id: str) -> dict[str, Any]:
        response = self._mitre.get(f"https://cveawg.mitre.org/api/cve/{cve_id}", timeout=30)
        if response.status_code != 200:
            return {}
        data = response.json()
        metadata = data.get("containers", {}).get("cna", {})
        descs = metadata.get("descriptions", [])
        refs = metadata.get("references", [])
        return {
            "description": descs[0].get("value", "") if descs else "",
            "references": [ref.get("url", "") for ref in refs[:10]],
            "source_coverage": ["MITRE"],
        }

    def _epss_lookup(self, cve_id: str) -> dict[str, Any]:
        response = self._epss.get("https://api.first.org/data/v1/epss", params={"cve": cve_id}, timeout=30)
        if response.status_code != 200:
            return {}
        data = response.json().get("data", [])
        if not data:
            return {}
        row = data[0]
        return {"epss": row.get("epss"), "percentile": row.get("percentile")}

    def _kev_lookup(self, cve_id: str) -> dict[str, Any]:
        response = self._kev.get(
            "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
            timeout=30,
        )
        response.raise_for_status()
        for item in response.json().get("vulnerabilities", []):
            if item.get("cveID", "").upper() == cve_id:
                return {"known_exploited": True, "kev": item}
        return {"known_exploited": False}

    def _searchsploit(self, needle: str) -> list[dict[str, Any]]:
        if not shutil.which("searchsploit"):
            return []
        completed = subprocess.run(
            ["searchsploit", "--json", needle],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return []
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return []
        matches = []
        for item in payload.get("RESULTS_EXPLOIT", [])[:10]:
            matches.append({"title": item.get("Title", ""), "path": item.get("Path", ""), "type": "searchsploit"})
        return matches

    def _github_search(self, needle: str) -> list[dict[str, Any]]:
        """Only method that legitimately needs the GitHub PAT — inject Authorization here, per-request."""
        if not self.settings.github_token:
            return []
        headers = {"Authorization": f"Bearer {self.settings.github_token}"}
        response = self._github.get(
            "https://api.github.com/search/repositories",
            params={"q": needle, "sort": "updated", "order": "desc", "per_page": "10"},
            headers=headers,
            timeout=30,
        )
        if response.status_code != 200:
            return []
        items = response.json().get("items", [])
        return [{"title": item.get("full_name", ""), "url": item.get("html_url", ""), "type": "github"} for item in items]

    def _nuclei_lookup(self, needle: str) -> list[dict[str, Any]]:
        candidates = [
            Path("/root/nuclei-templates"),
            Path("/opt/nuclei-templates"),
            Path("/workspace/nuclei-templates"),
        ]
        root = next((candidate for candidate in candidates if candidate.exists()), None)
        if not root:
            return []
        matches: list[dict[str, Any]] = []
        lowered = needle.lower()
        for path in root.rglob("*"):
            if len(matches) >= 10:
                break
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if lowered in content.lower() or lowered in path.name.lower():
                matches.append({"title": path.name, "path": str(path), "type": "nuclei_template"})
        return matches

    def _description(self, cve: dict[str, Any]) -> str:
        descs = cve.get("descriptions", [])
        for item in descs:
            if item.get("lang") == "en":
                return item.get("value", "")
        return descs[0].get("value", "") if descs else ""

    def _metrics(self, cve: dict[str, Any]) -> dict[str, Any]:
        metrics = cve.get("metrics", {})
        for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV40", "cvssMetricV2"):
            rows = metrics.get(key, [])
            if not rows:
                continue
            data = rows[0].get("cvssData", {})
            return {"score": data.get("baseScore"), "severity": data.get("baseSeverity", "")}
        return {}

    def _affected_products(self, cve: dict[str, Any]) -> list[str]:
        items: list[str] = []
        for config in cve.get("configurations", []):
            for node in config.get("nodes", []):
                for match in node.get("cpeMatch", []):
                    crit = match.get("criteria", "")
                    if crit:
                        items.append(crit)
        return items[:20]
