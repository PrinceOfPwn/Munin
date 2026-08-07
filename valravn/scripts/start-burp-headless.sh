#!/usr/bin/env bash
set -euo pipefail

# Reproducible Burp Community runtime for Valravn labs and unattended hosts.
# Burp 2026.7.1 is the current stable release as of 2026-08-07. The checksum is
# pinned so CI never executes an unverified download.
BURP_VERSION="${BURP_VERSION:-2026.7.1}"
DEFAULT_BURP_SHA256="21aaf2b965e0932ca2a4d94c189c472a519c7f5bc71e01fb9b700db359bafb27"
BURP_SHA256="${BURP_SHA256:-$DEFAULT_BURP_SHA256}"
BURP_DOWNLOAD_URL="${BURP_DOWNLOAD_URL:-https://portswigger-cdn.net/burp/releases/download?product=community&version=${BURP_VERSION}&type=Jar}"

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
BURP_HOME="${BURP_HOME:-$REPO_ROOT/.valravn-burp}"
BURP_JAR="${BURP_JAR:-$BURP_HOME/burpsuite-community-${BURP_VERSION}.jar}"
BURP_USER_CONFIG="${BURP_USER_CONFIG:-$BURP_HOME/user-config.json}"
BURP_LOG="${BURP_LOG:-$BURP_HOME/burp.log}"
BURP_PID_FILE="${BURP_PID_FILE:-$BURP_HOME/burp.pid}"
BURP_API_HOST="${BURP_API_HOST:-127.0.0.1}"
BURP_API_PORT="${BURP_API_PORT:-8111}"
BURP_PROXY_HOST="${BURP_PROXY_HOST:-127.0.0.1}"
BURP_PROXY_PORT="${BURP_PROXY_PORT:-8080}"
BURP_MAX_HEAP="${BURP_MAX_HEAP:-2g}"

EXTENSION_DIR="$REPO_ROOT/valravn/burp-extension"
EXTENSION_JAR="$EXTENSION_DIR/target/valravn-burp-ext-1.0.0.jar"

mkdir -p "$BURP_HOME/home"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "required command not found: $1" >&2
    exit 2
  }
}

require_cmd java
require_cmd mvn
require_cmd curl
require_cmd python
require_cmd sha256sum

JAVA_MAJOR="$(java -version 2>&1 | awk -F'[\".]' '/version/ {print $2; exit}')"
if [[ -z "$JAVA_MAJOR" || "$JAVA_MAJOR" -lt 21 ]]; then
  echo "Burp/Valravn requires Java 21+; detected: $(java -version 2>&1 | head -1)" >&2
  exit 2
fi

if curl -fsS "http://${BURP_API_HOST}:${BURP_API_PORT}/api/health" >/dev/null 2>&1; then
  echo "Valravn Burp API already healthy at ${BURP_API_HOST}:${BURP_API_PORT}"
  exit 0
fi

echo "Building Valravn Burp extension..."
mvn -q -f "$EXTENSION_DIR/pom.xml" clean package
[[ -s "$EXTENSION_JAR" ]] || {
  echo "extension JAR missing after Maven build: $EXTENSION_JAR" >&2
  exit 3
}

if [[ -f "$BURP_JAR" ]]; then
  CURRENT_SHA="$(sha256sum "$BURP_JAR" | awk '{print $1}')"
else
  CURRENT_SHA=""
fi

if [[ "$CURRENT_SHA" != "$BURP_SHA256" ]]; then
  echo "Downloading Burp Suite Community ${BURP_VERSION} from PortSwigger..."
  rm -f "$BURP_JAR"
  curl --fail --location --retry 3 --retry-delay 2 \
    "$BURP_DOWNLOAD_URL" \
    --output "$BURP_JAR"
fi

echo "${BURP_SHA256}  ${BURP_JAR}" | sha256sum --check --status || {
  echo "Burp JAR checksum verification failed" >&2
  rm -f "$BURP_JAR"
  exit 4
}

# Generate a Burp user configuration that loads Valravn automatically and
# leaves Proxy interception off. No interactive extension install or startup
# wizard is needed. The extension itself detects headless mode and skips Swing.
EXTENSION_JAR="$EXTENSION_JAR" BURP_USER_CONFIG="$BURP_USER_CONFIG" python - <<'PY'
import json
import os
from pathlib import Path

extension = str(Path(os.environ["EXTENSION_JAR"]).resolve())
config = {
    "user_options": {
        "extender": {
            "extensions": [
                {
                    "errors": "console",
                    "extension_file": extension,
                    "extension_type": "java",
                    "loaded": True,
                    "name": "Valravn MCP",
                    "output": "console",
                    "use_ai": False,
                }
            ],
            "settings": {
                "automatically_reload_extensions_on_startup": True,
                "automatically_update_bapps_on_startup": False,
                "suppress_extension_loaded_popup": True,
            },
        },
        "misc": {
            "enable_proxy_interception_at_startup": "never",
            "pause_tasks_at_startup_default": False,
            "submit_feedback": False,
        },
    }
}
Path(os.environ["BURP_USER_CONFIG"]).write_text(
    json.dumps(config, indent=2) + "\n", encoding="utf-8"
)
PY

if [[ -f "$BURP_PID_FILE" ]]; then
  OLD_PID="$(cat "$BURP_PID_FILE" 2>/dev/null || true)"
  if [[ -n "$OLD_PID" ]] && kill -0 "$OLD_PID" >/dev/null 2>&1; then
    echo "stopping stale Burp process $OLD_PID"
    kill "$OLD_PID" || true
    sleep 2
  fi
fi

: > "$BURP_LOG"

echo "Starting Burp headless with Valravn preloaded..."
nohup java \
  -Djava.awt.headless=true \
  -Duser.home="$BURP_HOME/home" \
  -Dvalravn.proxy.host="$BURP_PROXY_HOST" \
  -Dvalravn.proxy.port="$BURP_PROXY_PORT" \
  -Xmx"$BURP_MAX_HEAP" \
  -jar "$BURP_JAR" \
  --use-defaults \
  --user-config-file="$BURP_USER_CONFIG" \
  >"$BURP_LOG" 2>&1 &
BURP_PID=$!
echo "$BURP_PID" > "$BURP_PID_FILE"

for _ in $(seq 1 120); do
  if ! kill -0 "$BURP_PID" >/dev/null 2>&1; then
    echo "Burp exited before Valravn API became healthy" >&2
    cat "$BURP_LOG" >&2 || true
    exit 5
  fi
  if curl -fsS "http://${BURP_API_HOST}:${BURP_API_PORT}/api/health" >/dev/null 2>&1; then
    echo "Burp is ready: Valravn API http://${BURP_API_HOST}:${BURP_API_PORT}, proxy ${BURP_PROXY_HOST}:${BURP_PROXY_PORT}"
    exit 0
  fi
  sleep 1
done

echo "Timed out waiting for Valravn Burp API" >&2
cat "$BURP_LOG" >&2 || true
exit 6
