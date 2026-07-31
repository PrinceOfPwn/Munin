#!/usr/bin/env bash
# Opens a public tunnel to the local MCP server and exports the URL.
# Uses localhost.run (SSH-only) as primary attempt, cloudflared as fallback.
# Writes the URL to $GITHUB_ENV if present.

set -euo pipefail

PORT="${1:-8890}"
ENV_NAME="${2:-MUNIN_PUBLIC_URL}"
LOG="${3:-/tmp/tunnel.log}"

log() { echo "[tunnel] $*" >&2; }

# ── Attempt 1: localhost.run (SSH-only, no installation) ───────────────────
try_localhost_run() {
    log "Attempting localhost.run..."
    ssh -o StrictHostKeyChecking=no \
        -o ServerAliveInterval=30 \
        -o ConnectTimeout=10 \
        -R "80:localhost:${PORT}" \
        plan.localhost.run >>"$LOG" 2>&1 &
    SSH_PID=$!
    sleep 12
    URL=$(grep -oP 'https://[a-zA-Z0-9.-]+\.(lhr\.life|lhrtunnel\.link|lhr\.rocks)' "$LOG" 2>/dev/null | head -1)
    if [ -n "$URL" ]; then
        log "URL (localhost.run): $URL"
        echo "$URL"
        return 0
    fi
    kill "$SSH_PID" 2>/dev/null || true
    return 1
}

# ── Attempt 2: cloudflared Quick Tunnel ─────────────────────────────────────
try_cloudflared() {
    log "Attempting cloudflared Quick Tunnel..."
    local BIN="/usr/local/bin/cloudflared"
    if [ ! -f "$BIN" ]; then
        curl -fsSL \
            "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64" \
            -o "$BIN" && chmod +x "$BIN"
    fi
    "$BIN" tunnel --url "http://localhost:${PORT}" --no-autoupdate >>"$LOG" 2>&1 &
    CF_PID=$!
    for i in $(seq 1 30); do
        URL=$(grep -oP 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" 2>/dev/null | head -1)
        if [ -n "$URL" ]; then
            log "URL (cloudflared): $URL"
            echo "$URL"
            return 0
        fi
        sleep 2
    done
    kill "$CF_PID" 2>/dev/null || true
    return 1
}

# ── Main ─────────────────────────────────────────────────────────────────────
URL=""

URL=$(try_localhost_run) || true
if [ -z "$URL" ]; then
    URL=$(try_cloudflared) || true
fi

if [ -z "$URL" ]; then
    log "ERROR: failed to open tunnel. See $LOG"
    cat "$LOG" >&2
    exit 1
fi

# Export for GitHub Actions
if [ -n "${GITHUB_ENV:-}" ]; then
    echo "${ENV_NAME}=${URL}" >> "$GITHUB_ENV"
fi

echo "$URL"
