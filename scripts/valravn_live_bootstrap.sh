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

# The normal Live Session Kali image intentionally starts small. Keep the
# workflow itself lean and make this component own the runtime it needs.
# On developer hosts we never mutate the system automatically.
if [[ "${GITHUB_WORKFLOW:-}" == "Munin Live Session" ]]; then
  JAVA_MAJOR="$(java -version 2>&1 | awk -F'[\".]' '/version/ {print $2; exit}' || true)"
  if ! command -v java >/dev/null 2>&1 \
      || [[ -z "$JAVA_MAJOR" ]] \
      || [[ "$JAVA_MAJOR" -lt 21 ]] \
      || ! command -v xvfb-run >/dev/null 2>&1; then
    if [[ "$(id -u)" != "0" ]] || ! command -v apt-get >/dev/null 2>&1; then
      echo "::error::Live Session needs Java 21+ and Xvfb, and automatic package installation is unavailable" >&2
      exit 2
    fi
    echo "Installing Java/Xvfb runtime required by Burp MCP Ultimate..."
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      default-jdk xvfb xauth expect
  fi
fi

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

# start-burp-headless.sh historically calls `python`; Kali guarantees
# python3 but not the compatibility alias. Create only a process-host alias
# in the disposable Live Session container, never on normal operator hosts.
if ! command -v python >/dev/null 2>&1 \
    && [[ "${GITHUB_WORKFLOW:-}" == "Munin Live Session" ]] \
    && [[ "$(id -u)" == "0" ]]; then
  ln -sf "$(command -v python3)" /usr/local/bin/python
fi

JAVA_MAJOR="$(java -version 2>&1 | awk -F'[\".]' '/version/ {print $2; exit}')"
if [[ -z "$JAVA_MAJOR" || "$JAVA_MAJOR" -lt 21 ]]; then
  echo "::error::Burp MCP Ultimate requires Java 21+; got $(java -version 2>&1 | head -1)" >&2
  exit 2
fi

# Service containers are launched by GitHub before the job steps, but the app
# may still be warming up when the pre-server bootstrap begins. Explicitly
# require the vulnerable box to be alive before validating the Burp path.
echo "Waiting for authorized Juice Shop fixture at ${JUICE_SHOP_URL}..."
for i in $(seq 1 90); do
  if curl -fsS "${JUICE_SHOP_URL}/" >/dev/null; then
    echo "Juice Shop ready"
    break
  fi
  if [[ "$i" -eq 90 ]]; then
    echo "::error::Juice Shop did not become ready at ${JUICE_SHOP_URL}" >&2
    exit 5
  fi
  sleep 2
done

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

# GitHub Actions kills child processes that keep the per-step tracking cookie.
# Clearing it is the same pattern the Live Session already uses for Munin and
# the GUI, and lets Burp survive into the subsequent `munin serve` step.
if [[ "${GITHUB_WORKFLOW:-}" == "Munin Live Session" ]]; then
  export RUNNER_TRACKING_ID=""
fi

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
