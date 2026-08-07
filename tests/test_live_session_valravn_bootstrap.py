from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts" / "valravn_live_bootstrap.sh"
TURSO_SMOKE = ROOT / "scripts" / "turso_smoke.py"


def test_live_session_chains_valravn_before_server_step() -> None:
    smoke = TURSO_SMOKE.read_text(encoding="utf-8")
    assert 'GITHUB_WORKFLOW", "") != "Munin Live Session"' in smoke
    assert "valravn_live_bootstrap.sh" in smoke
    assert smoke.index("_bootstrap_live_session_mesh()") > smoke.index("Turso online roundtrip OK")


def test_valravn_live_bootstrap_is_real_and_pinned() -> None:
    script = BOOTSTRAP.read_text(encoding="utf-8")
    assert "1c2ffc541e15d7fcd45d750485e23b979e875295" in script
    assert "default-jdk xvfb xauth" in script
    assert "MUNIN_LAB_WEB_URL" in script
    assert "start-burp-headless.sh" in script
    assert "valravn_burp_juiceshop_e2e.py" in script
    assert 'export RUNNER_TRACKING_ID=""' in script
    assert "VALRAVN_TALON_ULTIMATE_URL" in script
    assert "BURP_MCP_TOKEN" in script
    assert "GITHUB_ENV" in script


def test_live_bootstrap_fails_closed_before_munin_presence() -> None:
    smoke = TURSO_SMOKE.read_text(encoding="utf-8")
    script = BOOTSTRAP.read_text(encoding="utf-8")
    assert "check=True" in smoke
    assert "set -euo pipefail" in script
    assert "Juice Shop did not become ready" in script
    assert "./gradlew test shadowJar --no-daemon" in script
