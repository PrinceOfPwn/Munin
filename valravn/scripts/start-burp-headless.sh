#!/usr/bin/env bash
set -euo pipefail

# Unattended Burp runtime for Valravn labs and operator hosts.
# Valravn no longer ships a custom Burp REST extension. Burp MCP Ultimate is
# preloaded as the execution backend and Munin talks to it through Talons.
BURP_VERSION="${BURP_VERSION:-2026.7.1}"
DEFAULT_BURP_SHA256="21aaf2b965e0932ca2a4d94c189c472a519c7f5bc71e01fb9b700db359bafb27"
BURP_SHA256="${BURP_SHA256:-$DEFAULT_BURP_SHA256}"
BURP_DOWNLOAD_URL="${BURP_DOWNLOAD_URL:-https://portswigger.net/burp/releases/startdownload?product=desktop&type=jar&version=${BURP_VERSION}}"

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
BURP_HOME="${BURP_HOME:-$REPO_ROOT/.valravn-burp}"
BURP_JAR="${BURP_JAR:-$BURP_HOME/burpsuite-desktop-${BURP_VERSION}.jar}"
BURP_USER_CONFIG="${BURP_USER_CONFIG:-$BURP_HOME/user-config.json}"
BURP_LOG="${BURP_LOG:-$BURP_HOME/burp.log}"
BURP_PID_FILE="${BURP_PID_FILE:-$BURP_HOME/burp.pid}"
BURP_MAX_HEAP="${BURP_MAX_HEAP:-2g}"
BURP_MCP_HOST="${BURP_MCP_HOST:-127.0.0.1}"
BURP_MCP_PORT="${BURP_MCP_PORT:-9444}"
BURP_MCP_TOKEN="${BURP_MCP_TOKEN:-}"
BURP_ULTIMATE_JAR="${BURP_ULTIMATE_JAR:-}"

mkdir -p "$BURP_HOME/home"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "required command not found: $1" >&2
    exit 2
  }
}

require_cmd java
require_cmd curl
require_cmd python
require_cmd sha256sum

JAVA_MAJOR="$(java -version 2>&1 | awk -F'[\".]' '/version/ {print $2; exit}')"
if [[ -z "$JAVA_MAJOR" || "$JAVA_MAJOR" -lt 21 ]]; then
  echo "Burp MCP Ultimate requires Java 21+; detected: $(java -version 2>&1 | head -1)" >&2
  exit 2
fi

if [[ -z "$BURP_ULTIMATE_JAR" || ! -s "$BURP_ULTIMATE_JAR" ]]; then
  echo "BURP_ULTIMATE_JAR must point to a built burp-mcp-ultimate shadow JAR" >&2
  echo "Build the pinned provider with: ./gradlew test shadowJar" >&2
  exit 3
fi
BURP_ULTIMATE_JAR="$(cd "$(dirname "$BURP_ULTIMATE_JAR")" && pwd)/$(basename "$BURP_ULTIMATE_JAR")"

if [[ -f "$BURP_JAR" ]]; then
  CURRENT_SHA="$(sha256sum "$BURP_JAR" | awk '{print $1}')"
else
  CURRENT_SHA=""
fi

if [[ "$CURRENT_SHA" != "$BURP_SHA256" ]]; then
  echo "Downloading Burp Suite Desktop ${BURP_VERSION} from PortSwigger..."
  rm -f "$BURP_JAR"
  curl --fail --location --retry 3 --retry-delay 2 \
    --connect-timeout 20 --max-time 600 \
    "$BURP_DOWNLOAD_URL" \
    --output "$BURP_JAR"
fi

echo "${BURP_SHA256}  ${BURP_JAR}" | sha256sum --check --status || {
  echo "Burp JAR checksum verification failed" >&2
  rm -f "$BURP_JAR"
  exit 4
}

# Reject HTML/error pages even if a caller overrides the checksum incorrectly.
BURP_JAR="$BURP_JAR" python - <<'PY'
import os
from pathlib import Path

path = Path(os.environ["BURP_JAR"])
if path.stat().st_size < 10_000_000:
    raise SystemExit(f"Burp JAR is implausibly small: {path.stat().st_size} bytes")
with path.open("rb") as fh:
    if fh.read(2) != b"PK":
        raise SystemExit("Burp download is not a ZIP/JAR payload")
PY

# Burp user settings can preload Java extensions. This removes the interactive
# Extensions -> Add step and keeps proxy interception disabled on startup.
BURP_ULTIMATE_JAR="$BURP_ULTIMATE_JAR" BURP_USER_CONFIG="$BURP_USER_CONFIG" python - <<'PY'
import json
import os
from pathlib import Path

extension = str(Path(os.environ["BURP_ULTIMATE_JAR"]).resolve())
config = {
    "user_options": {
        "extender": {
            "extensions": [
                {
                    "errors": "console",
                    "extension_file": extension,
                    "extension_type": "java",
                    "loaded": True,
                    "name": "burp-mcp-ultimate",
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
    echo "stopping stale Burp launcher $OLD_PID"
    pkill -TERM -P "$OLD_PID" 2>/dev/null || true
    kill "$OLD_PID" 2>/dev/null || true
    sleep 2
  fi
fi

: > "$BURP_LOG"

JAVA_ARGS=(
  -Duser.home="$BURP_HOME/home"
  -Xmx"$BURP_MAX_HEAP"
  -jar "$BURP_JAR"
  --use-defaults
  --user-config-file="$BURP_USER_CONFIG"
)

# Ultimate registers a suite tab, so CI uses a virtual display rather than
# forcing java.awt.headless=true. Operator hosts with DISPLAY use it directly.
if [[ -z "${DISPLAY:-}" ]]; then
  require_cmd xvfb-run
  LAUNCH=(xvfb-run -a java "${JAVA_ARGS[@]}")
else
  LAUNCH=(java "${JAVA_ARGS[@]}")
fi

echo "Starting Burp with burp-mcp-ultimate preloaded..."
# Bash bug: `script` is util-linux pty wrapper. Launching java under a pty
# makes the JVM treat stdout/stderr as a TTY and flush line-by-line. A plain
# `nohup java >burp.log 2>&1` redirects to a non-tty fd, java buffers 8KB of
# stdout internally, and the log stays empty while Burp hangs — so we would
# have no way to diagnose why the MCP port never opened.
if command -v script >/dev/null 2>&1; then
  nohup script -q -c "${LAUNCH[*]}" "$BURP_LOG" </dev/null >/dev/null 2>&1 &
else
  nohup "${LAUNCH[@]}" >"$BURP_LOG" 2>&1 &
fi
BURP_PID=$!
echo "$BURP_PID" > "$BURP_PID_FILE"

# Probe the real MCP surface, not a custom health endpoint. The probe follows
# the Streamable HTTP initialize -> initialized -> tools/list lifecycle.
BURP_MCP_HOST="$BURP_MCP_HOST" \
BURP_MCP_PORT="$BURP_MCP_PORT" \
BURP_MCP_TOKEN="$BURP_MCP_TOKEN" \
BURP_PID="$BURP_PID" \
BURP_LOG="$BURP_LOG" \
python - <<'PY'
import json
import os
import time
import urllib.error
import urllib.request

url = f"http://{os.environ['BURP_MCP_HOST']}:{os.environ['BURP_MCP_PORT']}/mcp"
token = os.environ.get("BURP_MCP_TOKEN", "")
pid = int(os.environ["BURP_PID"])
log = os.environ["BURP_LOG"]


def alive() -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def decode(body: str, ctype: str) -> dict:
    if "text/event-stream" in ctype:
        for line in body.splitlines():
            if line.startswith("data:"):
                try:
                    return json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    pass
        raise RuntimeError("SSE response had no JSON data frame")
    return json.loads(body)


def post(payload: dict, session: str = ""):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if session:
        headers["Mcp-Session-Id"] = session
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return decode(body, resp.headers.get("Content-Type", "")), resp.headers.get("Mcp-Session-Id", session)


deadline = time.time() + 240
last_error = "not ready"
last_report = time.time()
while time.time() < deadline:
    if not alive():
        raise SystemExit(f"Burp exited before MCP became ready; inspect {log}")
    try:
        init, sid = post({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "valravn-bootstrap", "version": "1"},
            },
        })
        if "error" in init:
            raise RuntimeError(init["error"])
        post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}, sid)
        listed, _ = post({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, sid)
        tools = listed.get("result", {}).get("tools", [])
        names = {item.get("name") for item in tools if isinstance(item, dict)}
        required = {"burp_version", "http_send_raw", "intercept_set_mode"}
        if len(tools) < 100 or not required <= names:
            raise RuntimeError(f"unexpected Ultimate catalog: tools={len(tools)}, missing={sorted(required - names)}")
        print(f"Burp MCP Ultimate ready at {url}: tools={len(tools)}")
        raise SystemExit(0)
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
        last_error = f"{type(exc).__name__}: {exc}"
        # Report progress so the runner log shows the probe is alive instead
        # of a silent 4-minute hang. The tail includes the live Burp log so a
        # pty-captured stack trace is visible in CI even before timeout.
        if time.time() - last_report >= 15:
            last_report = time.time()
            print(f"probe waiting for Burp MCP: {last_error}", flush=True)
        time.sleep(2)

log_tail = ""
if os.path.exists(log):
    try:
        with open(log, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        log_tail = "".join(lines[-120:])
    except OSError:
        log_tail = ""
raise SystemExit(
    f"Timed out waiting for Burp MCP Ultimate: {last_error}; inspect {log}\n"
    f"--- {log} tail ---\n{log_tail}"
)
PY
