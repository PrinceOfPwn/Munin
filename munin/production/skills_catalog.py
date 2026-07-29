"""Metadata-first Hugin/Strix catalog synchronization.

External skill text is untrusted.  We only index a pinned Git revision after
the caller records its license decision.  Retrieval returns at most five
metadata records; a caller must explicitly open content in a sandboxed reader.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from urllib.parse import quote
from urllib.request import Request, urlopen

STRIX_REPOSITORY = "usestrix/strix"
STRIX_SKILLS_PATH = "strix/skills"
ACCEPTED_LICENSES = frozenset({"Apache-2.0"})


@dataclass(frozen=True)
class SkillMetadata:
    slug: str
    category: str
    technology: tuple[str, ...]
    vulnerabilities: tuple[str, ...]
    tools: tuple[str, ...]
    source_url: str
    commit_sha: str
    license: str
    provenance: str
    content_hash: str
    indexed_at_ms: int


def _github_json(url: str) -> object:
    if not url.startswith(f"https://api.github.com/repos/{STRIX_REPOSITORY}/"):
        raise PermissionError("catalog source must be the pinned Strix GitHub API")
    request = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "munin-production-suite"})  # noqa: S310 - validated fixed HTTPS host
    with urlopen(request, timeout=15) as response:  # noqa: S310 - validated fixed HTTPS host
        return json.loads(response.read().decode("utf-8"))


def sync_strix_metadata(*, commit_sha: str, license_name: str, indexed_at_ms: int) -> list[SkillMetadata]:
    """Read a pinned GitHub tree without vendoring unreviewed external prompts."""
    if license_name not in ACCEPTED_LICENSES:
        raise PermissionError("Strix license must be explicitly accepted before catalog synchronization")
    safe_sha = quote(commit_sha, safe="")
    api_url = f"https://api.github.com/repos/{STRIX_REPOSITORY}/contents/{STRIX_SKILLS_PATH}?ref={safe_sha}"
    payload = _github_json(api_url)
    if not isinstance(payload, list):
        raise RuntimeError("unexpected Strix catalog response")
    results: list[SkillMetadata] = []
    for item in payload:
        if not isinstance(item, dict) or item.get("type") != "dir" or not isinstance(item.get("name"), str):
            continue
        slug = item["name"]
        source_url = f"https://github.com/{STRIX_REPOSITORY}/tree/{commit_sha}/{STRIX_SKILLS_PATH}/{slug}"
        results.append(SkillMetadata(slug=slug, category="strix", technology=(), vulnerabilities=(), tools=(), source_url=source_url, commit_sha=commit_sha, license=license_name, provenance="github-pinned-metadata", content_hash=hashlib.sha256(source_url.encode()).hexdigest(), indexed_at_ms=indexed_at_ms))
    return results


def rank_skills(catalog: list[SkillMetadata], *, query: str, limit: int = 5) -> list[SkillMetadata]:
    """Small lexical retrieval boundary; content is never injected wholesale."""
    terms = {term.lower() for term in query.split() if len(term) > 2}
    def score(item: SkillMetadata) -> int:
        haystack = " ".join((item.slug, item.category, *item.technology, *item.vulnerabilities, *item.tools)).lower()
        return sum(term in haystack for term in terms)
    return sorted(catalog, key=lambda item: (score(item), item.slug), reverse=True)[: max(1, min(limit, 5))]
