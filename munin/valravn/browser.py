# tags: [valravn, recon, intel, osint, active-recon, web-ui, runtime, CloakObserver, BrowserCapture, BrowserUnavailable, cloakbrowser, CLOAKBROWSER_LICENSE_KEY, onion-routing, screenshot-capture, route-blocking]
"""Ephemeral browser evidence capture built on CloakBrowser."""

from __future__ import annotations

import base64
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, unquote, urlparse

from .config import ValravnSettings
from .security import gateway_to_onion, is_onion_url, onion_to_gateway, validate_public_url


class BrowserUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class BrowserCapture:
    requested_url: str
    navigated_url: str
    canonical_url: str
    title: str
    description: str
    language: str
    text: str
    links: list[dict[str, str]]
    screenshot: bytes


class CloakObserver:
    def __init__(self, settings: ValravnSettings, *, launcher: Callable[..., Any] | None = None) -> None:
        self.settings = settings
        self._launcher = launcher

    def _launch(self) -> Any:
        launcher = self._launcher
        if launcher is None:
            try:
                from cloakbrowser import launch  # type: ignore
            except ImportError as exc:
                raise BrowserUnavailable("cloakbrowser is not installed") from exc
            launcher = launch
        kwargs: dict[str, Any] = {
            "headless": self.settings.browser_headless,
            "humanize": False,
        }
        if self.settings.browser_proxy:
            kwargs["proxy"] = self.settings.browser_proxy
        key = os.environ.get("CLOAKBROWSER_LICENSE_KEY", "").strip()
        if key:
            kwargs["license_key"] = key
        return launcher(**kwargs)

    def _block_unsafe_route(self, route: Any) -> None:
        try:
            url = route.request.url
            validate_public_url(url, resolve_host=self.settings.resolve_public_hosts)
        except Exception:
            route.abort()
        else:
            route.continue_()

    def capture(self, url: str, *, full_page: bool = True) -> BrowserCapture:
        requested = url.strip()
        canonical = requested
        if is_onion_url(requested):
            canonical = requested
            navigated = onion_to_gateway(requested, self.settings.onion_gateway_domain)
        else:
            navigated = validate_public_url(requested, resolve_host=self.settings.resolve_public_hosts)
            canonical = gateway_to_onion(navigated, self.settings.onion_gateway_domain) or navigated

        browser = self._launch()
        context = None
        page = None
        try:
            if hasattr(browser, "new_context"):
                context = browser.new_context(
                    accept_downloads=False,
                    service_workers="block",
                    ignore_https_errors=False,
                )
                page = context.new_page()
            else:
                page = browser.new_page()
            if hasattr(page, "route"):
                page.route("**/*", self._block_unsafe_route)
            page.goto(navigated, wait_until="domcontentloaded", timeout=45_000)
            data = page.evaluate(
                """() => ({
                    title: document.title || '',
                    url: location.href,
                    language: document.documentElement.lang || '',
                    description: document.querySelector('meta[name="description"]')?.content || '',
                    text: (document.body?.innerText || '').slice(0, 80000),
                    links: Array.from(document.links).slice(0, 500).map(a => ({
                        text: (a.innerText || a.textContent || '').trim().slice(0, 250),
                        url: a.href
                    }))
                })"""
            ) or {}
            final_url = str(data.get("url") or navigated)
            validate_public_url(final_url, resolve_host=self.settings.resolve_public_hosts)
            screenshot = page.screenshot(full_page=bool(full_page), type="png")
            if isinstance(screenshot, str):
                screenshot = base64.b64decode(screenshot)
            return BrowserCapture(
                requested_url=requested,
                navigated_url=final_url,
                canonical_url=gateway_to_onion(final_url, self.settings.onion_gateway_domain) or canonical,
                title=str(data.get("title") or ""),
                description=str(data.get("description") or ""),
                language=str(data.get("language") or ""),
                text=str(data.get("text") or ""),
                links=[
                    {"text": str(item.get("text") or ""), "url": str(item.get("url") or "")}
                    for item in data.get("links", [])
                    if isinstance(item, dict) and item.get("url")
                ],
                screenshot=bytes(screenshot or b""),
            )
        finally:
            for obj in (page, context, browser):
                try:
                    if obj is not None and hasattr(obj, "close"):
                        obj.close()
                except Exception:  # noqa: S110 - best-effort teardown
                    pass

    def search_darkweb(self, query: str, limit: int = 20) -> dict[str, Any]:
        if not self.settings.darkweb_search_enabled:
            raise BrowserUnavailable("dark-web search is disabled")
        search_url = f"https://ahmia.fi/search/?q={quote(query)}"
        capture = self.capture(search_url, full_page=False)
        matches: list[dict[str, str]] = []
        seen: set[str] = set()
        onion_re = re.compile(
            r"https?://([a-z2-7]{16}|[a-z2-7]{56})\.onion(?:/[^\s<>'\"]*)?",
            re.IGNORECASE,
        )
        candidates = list(capture.links)
        candidates.extend({"text": "", "url": match.group(0)} for match in onion_re.finditer(capture.text))
        for item in candidates:
            value = unquote(str(item.get("url") or ""))
            parsed = urlparse(value)
            for part in (value, parsed.query):
                match = onion_re.search(part)
                if not match:
                    continue
                onion_url = match.group(0).rstrip(".,;")
                if onion_url in seen:
                    continue
                seen.add(onion_url)
                matches.append(
                    {
                        "title": str(item.get("text") or "").strip(),
                        "onion_url": onion_url,
                        "gateway_url": onion_to_gateway(onion_url, self.settings.onion_gateway_domain),
                    }
                )
                break
            if len(matches) >= max(1, min(limit, 100)):
                break
        return {
            "query": query,
            "search_url": search_url,
            "count": len(matches),
            "results": matches,
            "warning": "Gateway URLs are third-party Tor2Web views, not end-to-end Tor connections.",
        }
