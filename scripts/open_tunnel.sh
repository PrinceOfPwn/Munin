#!/usr/bin/env bash
# Opens a public tunnel to the local MCP server and exports the URL.
# Three supported providers: authenticated ngrok, cloudflared Quick Tunnel, and
# localhost.run.  ``auto`` prefers ngrok when an Actions secret is configured,
# then cloudflared, then localhost.run.
# Writes the URL to $GITHUB_ENV if present.

set -euo pipefail

PORT="${1:-8890}"
ENV_NAME="${2:-MUNIN_PUBLIC_URL}"
LOG="${3:-/tmp/tunnel.log}"
PROVIDER="${MUNIN_TUNNEL_PROVIDER:-auto}"

log() { echo "[tunnel] $*" >&2; }

require_provider() {
    case "$PROVIDER" in
        auto|ngrok|cloudflared|localhost-run) ;;
        *)
            log "ERROR: invalid MUNIN_TUNNEL_PROVIDER='$PROVIDER' (use auto|ngrok|cloudflared|localhost-run)"
            exit 2
            ;;
    esac
}

# ── Attempt 1: ngrok (authenticated, stable when token is configured) ───────
try_ngrok() {
    local token="${NGROK_AUTHTOKEN:-${NGROK_AUTH_TOKEN:-}}"
    if [ -z "$token" ]; then
        log "Skipping ngrok: neither NGROK_AUTHTOKEN nor NGROK_AUTH_TOKEN is configured"
        return 1
    fi
    log "Attempting ngrok..."
    local BIN="/usr/local/bin/ngrok"
    if [ ! -x "$BIN" ]; then
        local archive="/tmp/ngrok.tgz"
        curl -fsSL "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz" -o "$archive"
        tar -xzf "$archive" -C /tmp ngrok
        install -m 0755 /tmp/ngrok "$BIN"
        rm -f "$archive" /tmp/ngrok
    fi
    "$BIN" config add-authtoken "$token" >/dev/null 2>&1
    "$BIN" http "$PORT" --log=stdout --log-format=json >>"$LOG" 2>&1 &
    NGROK_PID=$!
    for _ in $(seq 1 30); do
        URL=$(grep -oE 'https://[a-zA-Z0-9.-]+\.(ngrok-free\.app|ngrok-free\.dev|ngrok\.io)' "$LOG" 2>/dev/null | head -1)
        if [ -n "$URL" ]; then
            log "URL (ngrok): $URL"
            echo "$URL"
            return 0
        fi
        sleep 2
    done
    kill "$NGROK_PID" 2>/dev/null || true
    return 1
}

# ── localhost.run (SSH-only, no installation) ───────────────────────────────
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

# ── cloudflared Quick Tunnel ─────────────────────────────────────────────────
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
require_provider

if [ "$PROVIDER" = "ngrok" ]; then
    URL=$(try_ngrok) || true
elif [ "$PROVIDER" = "cloudflared" ]; then
    URL=$(try_cloudflared) || true
elif [ "$PROVIDER" = "localhost-run" ]; then
    URL=$(try_localhost_run) || true
else
    URL=$(try_ngrok) || true
    if [ -z "$URL" ]; then URL=$(try_cloudflared) || true; fi
    if [ -z "$URL" ]; then URL=$(try_localhost_run) || true; fi
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
