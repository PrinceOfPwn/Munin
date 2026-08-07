#!/usr/bin/env bash
set -euo pipefail

# Bootstrap the real Valravn Talons execution backend for Munin Live Session.
# This intentionally mirrors the required CI gate: pinned Ultimate provider,
# unattended Burp, real MCP negotiation, and a real Juice Shop round-trip.
ULTIMATE_REPO="${VALRAVN_ULTIMATE_REPO:-https://github.com/3ntr0pyX/burp-mcp-ultimate.git}"
ULTIMATE_COMMIT="${VALRAVN_ULTIMATE_COMMIT:-1c2ffc541e15d7fcd45d750485e23b979e875295}"
ULTIMATE_ROOT="${VALRAVN_ULTIMATE_ROOT:-${RUNNER_TEMP:-/tmp}/burp-mcp-ultimate}"
BURP_HOME="${BURP_HOME:-${RUNNER_TEMP:-/tmp}/valravn-burp}"
BURP_MCP_HOST="${BURP_MCP_HOST:-127.0.0.1}"
BURP_MCP_PORT="${BURP_MCP_PORT:-9444}"
BURP_MCP_TOKEN="${BURP_MCP_TOKEN:-valravn-live-local-only}"
BURP_PROXY_HOST="${BURP_PROXY_HOST:-127.0.0.1}"
BURP_PROXY_PORT="${BURP_PROXY_PORT:-8080}"
VALRAVN_TALON_ULTIMATE_URL="${VALRAVN_TALON_ULTIMATE_URL:-http://${BURP_MCP_HOST}:${BURP_MCP_PORT}/mcp}"
JUICE_SHOP_URL="${JUICE_SHOP_URL:-${MUNIN_LAB_WEB_URL:-http://juiceshop:3000}}"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "::error::Valravn live bootstrap requires '$1'" >&2
    exit 2
  }
}

for command in git java curl sha256sum xvfb-run; do
  require_cmd "$command"
done

if command -v python >/dev/null 2>&1; then
  PYTHON_BIN=python
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN=python3
else
  echo "::error::Valravn live bootstrap requires Python 3" >&2
  exit 2
fi

JAVA_MAJOR="$(java -version 2>&1 | awk -F'[\".]' '/version/ {print $2; exit}')"
if [[ -z "$JAVA_MAJOR" || "$JAVA_MAJOR" -lt 21 ]]; then
  echo "::error::Burp MCP Ultimate requires Java 21+; got $(java -version 2>&1 | head -1)" >&2
  exit 2
fi

rm -rf "$ULTIMATE_ROOT"
git clone --filter=blob:none --no-checkout "$ULTIMATE_REPO" "$ULTIMATE_ROOT"
git -C "$ULTIMATE_ROOT" checkout --detach "$ULTIMATE_COMMIT"

test "$(git -C "$ULTIMATE_ROOT" rev-parse HEAD)" = "$ULTIMATE_COMMIT"
chmod +x "$ULTIMATE_ROOT/gradlew"
(
  cd "$ULTIMATE_ROOT"
  ./gradlew test shadowJar --no-daemon
)

ULTIMATE_JAR="$(find "$ULTIMATE_ROOT/build/libs" -maxdepth 1 -name 'burp-mcp-ultimate*.jar' -print -quit)"
test -n "$ULTIMATE_JAR"
test -s "$ULTIMATE_JAR"

export BURP_HOME BURP_MCP_HOST BURP_MCP_PORT BURP_MCP_TOKEN
export BURP_PROXY_HOST BURP_PROXY_PORT VALRAVN_TALON_ULTIMATE_URL JUICE_SHOP_URL
export BURP_ULTIMATE_JAR="$ULTIMATE_JAR"

chmod +x valravn/scripts/start-burp-headless.sh
valravn/scripts/start-burp-headless.sh

# Prove the same path that operators will use before Munin announces presence.
# If this fails, Live Session fails rather than presenting a half-ready mesh.
if command -v poetry >/dev/null 2>&1; then
  poetry run python scripts/valravn_burp_juiceshop_e2e.py
else
  "$PYTHON_BIN" scripts/valravn_burp_juiceshop_e2e.py
fi

if [[ -n "${GITHUB_ENV:-}" ]]; then
  {
    echo "BURP_HOME=${BURP_HOME}"
    echo "BURP_MCP_HOST=${BURP_MCP_HOST}"
    echo "BURP_MCP_PORT=${BURP_MCP_PORT}"
    echo "BURP_MCP_TOKEN=${BURP_MCP_TOKEN}"
    echo "BURP_PROXY_HOST=${BURP_PROXY_HOST}"
    echo "BURP_PROXY_PORT=${BURP_PROXY_PORT}"
    echo "VALRAVN_TALON_ULTIMATE_URL=${VALRAVN_TALON_ULTIMATE_URL}"
    echo "JUICE_SHOP_URL=${JUICE_SHOP_URL}"
    echo "VALRAVN_ULTIMATE_COMMIT=${ULTIMATE_COMMIT}"
  } >> "$GITHUB_ENV"
fi

if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    echo "### Valravn live mesh"
    echo
    echo "- Burp MCP Ultimate: pinned at \`${ULTIMATE_COMMIT}\`"
    echo "- Talons endpoint: \`${VALRAVN_TALON_ULTIMATE_URL}\`"
    echo "- Burp Proxy: \`${BURP_PROXY_HOST}:${BURP_PROXY_PORT}\`"
    echo "- Authorized Juice Shop target: \`${JUICE_SHOP_URL}\`"
    echo "- Validation: Ultimate/Montoya request + real Burp Proxy history round-trip passed"
  } >> "$GITHUB_STEP_SUMMARY"
fi

echo "Valravn live bootstrap ready: Talons -> Burp MCP Ultimate -> Burp -> ${JUICE_SHOP_URL}"
