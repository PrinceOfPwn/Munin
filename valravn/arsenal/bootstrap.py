#!/usr/bin/env python3
"""Bootstrap third-party capability surfaces used by the Valravn mesh.

Upstream repositories stay in ``valravn/upstreams`` and are gitignored. This
keeps Munin's source tree small while pinning reproducible revisions and
retaining each upstream project's own notices/licenses.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UPSTREAMS = ROOT / "valravn" / "upstreams"
MANIFEST = ROOT / "valravn" / "arsenal" / "security_hub.json"

ULTIMATE_REPO = "https://github.com/3ntr0pyX/burp-mcp-ultimate.git"
ULTIMATE_COMMIT = "1c2ffc541e15d7fcd45d750485e23b979e875295"
AWESOME_REPO = "https://github.com/vvvvvvvvvvel/burp-awesome-mcp.git"
AWESOME_COMMIT = "4d6b8c1aaccaf56e383430790fa67c463f83d72f"
SECURITY_HUB_REPO = "https://github.com/FuzzingLabs/mcp-security-hub.git"

BOUNTY_SERVICES = (
    "pd-tools-mcp",
    "whatweb-mcp",
    "nuclei-mcp",
    "ffuf-mcp",
    "waybackurls-mcp",
    "gitleaks-mcp",
    "semgrep-mcp",
    "externalattacker-mcp",
    "dnstwist-mcp",
    "searchsploit-mcp",
)


def run(*args: str, cwd: Path | None = None) -> None:
    print("+", " ".join(args))
    subprocess.run(args, cwd=cwd, check=True)


def clone_pinned(url: str, dest: Path, commit: str) -> None:
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        run("git", "clone", "--filter=blob:none", url, str(dest))
    run("git", "fetch", "--tags", "--prune", cwd=dest)
    run("git", "checkout", "--detach", commit, cwd=dest)


def bootstrap_burp(build: bool) -> None:
    ultimate = UPSTREAMS / "burp-mcp-ultimate"
    clone_pinned(ULTIMATE_REPO, ultimate, ULTIMATE_COMMIT)
    awesome = UPSTREAMS / "burp-awesome-mcp"
    clone_pinned(AWESOME_REPO, awesome, AWESOME_COMMIT)
    if build:
        run("./gradlew", "test", "shadowJar", cwd=ultimate)
        run("./gradlew", "test", "shadowJar", cwd=awesome)


def bootstrap_arsenal(profile: str) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    security_hub = UPSTREAMS / "mcp-security-hub"
    clone_pinned(SECURITY_HUB_REPO, security_hub, str(manifest["upstream_commit"]))
    if profile == "none":
        return
    if not shutil.which("docker"):
        raise SystemExit("docker is required to build Valravn Arsenal images")
    services = [item["service"] for item in manifest["servers"]] if profile == "all" else list(BOUNTY_SERVICES)
    run("docker", "compose", "build", *services, cwd=security_hub)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap Valravn Talons + Arsenal")
    parser.add_argument("--burp", action="store_true", help="clone pinned Ultimate and Awesome Burp MCP providers")
    parser.add_argument("--build-burp", action="store_true", help="also run tests and build shadow JARs for both Burp providers")
    parser.add_argument(
        "--arsenal",
        choices=("none", "bounty", "all"),
        default="bounty",
        help="clone Security Hub and optionally build a useful subset or every MCP image",
    )
    args = parser.parse_args()

    if args.burp or args.build_burp:
        bootstrap_burp(build=args.build_burp)
    bootstrap_arsenal(args.arsenal)
    print(f"Valravn upstreams ready under {UPSTREAMS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
