"""Input classification, artifact confinement and browser safety checks."""

from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

_CVE_RE = re.compile(r"^CVE-\d{4}-\d{4,}$", re.IGNORECASE)
_HASH_LENGTHS = {32: "md5", 40: "sha1", 64: "sha256", 128: "sha512"}
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[A-Za-z]{2,63}$"
)


class UnsafeTarget(ValueError):
    pass


@dataclass(frozen=True)
class Indicator:
    value: str
    kind: str
    normalized: str


def classify_indicator(value: str) -> Indicator:
    raw = (value or "").strip()
    if not raw:
        raise ValueError("indicator is empty")
    if _CVE_RE.fullmatch(raw):
        return Indicator(raw, "cve", raw.upper())
    compact = raw.lower()
    if len(compact) in _HASH_LENGTHS and all(ch in "0123456789abcdef" for ch in compact):
        return Indicator(raw, "hash", compact)
    try:
        ip = ipaddress.ip_address(raw.strip("[]"))
        return Indicator(raw, "ip", ip.compressed)
    except ValueError:
        pass
    parsed = urlparse(raw)
    if parsed.scheme.lower() in {"http", "https"} and parsed.hostname:
        return Indicator(raw, "url", raw)
    domain = raw.rstrip(".").lower()
    if _DOMAIN_RE.fullmatch(domain):
        return Indicator(raw, "domain", domain)
    if "@" in raw and raw.count("@") == 1:
        local, _, host = raw.rpartition("@")
        if local and _DOMAIN_RE.fullmatch(host.lower()):
            return Indicator(raw, "email", f"{local}@{host.lower()}")
    return Indicator(raw, "query", raw)


def _forbidden_ip(ip: ipaddress._BaseAddress) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_public_url(url: str, *, resolve_host: bool = True) -> str:
    parsed = urlparse((url or "").strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise UnsafeTarget("only absolute http/https URLs are allowed")
    if parsed.username or parsed.password:
        raise UnsafeTarget("URLs containing credentials are not allowed")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(
        (".localhost", ".local", ".internal", ".home", ".lan")
    ):
        raise UnsafeTarget("local/internal hostnames are blocked")
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        ip = None
    if ip is not None and _forbidden_ip(ip):
        raise UnsafeTarget("private, loopback, metadata and reserved IPs are blocked")
    if host == "169.254.169.254" or host.endswith("metadata.google.internal"):
        raise UnsafeTarget("cloud metadata endpoints are blocked")
    if resolve_host and ip is None:
        try:
            addresses = {
                entry[4][0]
                for entry in socket.getaddrinfo(
                    host,
                    parsed.port or (443 if parsed.scheme.lower() == "https" else 80),
                )
            }
        except socket.gaierror as exc:
            raise UnsafeTarget(f"hostname could not be resolved: {exc}") from exc
        for address in addresses:
            resolved = ipaddress.ip_address(address)
            if _forbidden_ip(resolved):
                raise UnsafeTarget(f"hostname resolves to blocked address {resolved.compressed}")
    return parsed.geturl()


def safe_artifact_dir(workspace: Path, run_id: str) -> Path:
    root = workspace.resolve()
    safe_run = re.sub(r"[^a-zA-Z0-9_.-]+", "-", (run_id or "adhoc").strip()).strip("-.") or "adhoc"
    path = (root / "runs" / safe_run / "valravn").resolve()
    if root not in path.parents:
        raise UnsafeTarget("artifact path escaped workspace")
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_artifact(directory: Path, stem: str, suffix: str, content: bytes) -> dict[str, str | int]:
    safe_stem = re.sub(r"[^a-zA-Z0-9_.-]+", "-", stem).strip("-.") or "evidence"
    safe_suffix = suffix if suffix.startswith(".") else f".{suffix}"
    path = (directory / f"{safe_stem}{safe_suffix}").resolve()
    if directory.resolve() not in path.parents:
        raise UnsafeTarget("artifact filename escaped directory")
    path.write_bytes(content)
    return {
        "path": str(path),
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }


_ONION_HOST_RE = re.compile(r"^(?P<label>[a-z2-7]{16}|[a-z2-7]{56})\.onion$", re.IGNORECASE)


def is_onion_url(url: str) -> bool:
    parsed = urlparse((url or "").strip())
    return parsed.scheme.lower() in {"http", "https"} and bool(
        parsed.hostname and _ONION_HOST_RE.fullmatch(parsed.hostname)
    )


def onion_to_gateway(url: str, gateway_domain: str = "onion.pet") -> str:
    """Convert a canonical onion URL into a read-only Tor2Web gateway URL."""
    parsed = urlparse((url or "").strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise UnsafeTarget("an absolute .onion URL is required")
    match = _ONION_HOST_RE.fullmatch(parsed.hostname.lower())
    if not match:
        raise UnsafeTarget("invalid v2/v3 .onion hostname")
    gateway = gateway_domain.strip().lower().lstrip(".")
    if not _DOMAIN_RE.fullmatch(gateway):
        raise UnsafeTarget("invalid onion gateway domain")
    host = f"{match.group('label')}.{gateway}"
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path or "/"
    suffix = f"?{parsed.query}" if parsed.query else ""
    fragment = f"#{parsed.fragment}" if parsed.fragment else ""
    return f"https://{host}{port}{path}{suffix}{fragment}"


def gateway_to_onion(url: str, gateway_domain: str = "onion.pet") -> str | None:
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower()
    suffix = f".{gateway_domain.strip().lower().lstrip('.')}"
    if not host.endswith(suffix):
        return None
    label = host[: -len(suffix)]
    if not re.fullmatch(r"[a-z2-7]{16}|[a-z2-7]{56}", label, flags=re.IGNORECASE):
        return None
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path or "/"
    query = f"?{parsed.query}" if parsed.query else ""
    fragment = f"#{parsed.fragment}" if parsed.fragment else ""
    return f"http://{label}.onion{port}{path}{query}{fragment}"
