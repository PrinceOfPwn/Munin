#!/usr/bin/env bash
# Abre un tunnel público hacia el MCP server local y exporta la URL.
# Usa localhost.run (SSH, sin instalación) como primer intento,
# cloudflared como fallback.
# Escribe la URL en $GITHUB_ENV si la variable existe.

set -euo pipefail

PORT="${1:-8890}"
LOG="/tmp/tunnel.log"

log() { echo "[tunnel] $*"; }

# ── Intento 1: localhost.run (solo SSH, sin instalar nada) ───────────────────
try_localhost_run() {
    log "Intentando localhost.run..."
    ssh -o StrictHostKeyChecking=no \
        -o ServerAliveInterval=30 \
        -R "80:localhost:${PORT}" \
        plan.localhost.run 2>"$LOG" &
    SSH_PID=$!
    sleep 8
    URL=$(grep -oP 'https://[a-z0-9-]+\.lhr\.life' "$LOG" 2>/dev/null | head -1)
    if [ -n "$URL" ]; then
        log "URL (localhost.run): $URL"
        echo "$URL"
        return 0
    fi
    kill "$SSH_PID" 2>/dev/null || true
    return 1
}

# ── Intento 2: cloudflared Quick Tunnel ─────────────────────────────────────
try_cloudflared() {
    log "Intentando cloudflared Quick Tunnel..."
    local BIN="/usr/local/bin/cloudflared"
    if [ ! -f "$BIN" ]; then
        curl -fsSL \
            "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64" \
            -o "$BIN" && chmod +x "$BIN"
    fi
    "$BIN" tunnel --url "http://localhost:${PORT}" --no-autoupdate 2>"$LOG" &
    CF_PID=$!
    for i in $(seq 1 20); do
        URL=$(grep -oP 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" 2>/dev/null | head -1)
        if [ -n "$URL" ]; then
            log "URL (cloudflared): $URL"
            echo "$URL"
            return 0
        fi
        sleep 1
    done
    kill "$CF_PID" 2>/dev/null || true
    return 1
}

# ── Main ─────────────────────────────────────────────────────────────────────
URL=""

URL=$(try_localhost_run 2>/dev/null) || true
if [ -z "$URL" ]; then
    URL=$(try_cloudflared 2>/dev/null) || true
fi

if [ -z "$URL" ]; then
    log "ERROR: no se pudo abrir tunnel. Ver $LOG"
    cat "$LOG" >&2
    exit 1
fi

# Exportar para GitHub Actions
if [ -n "${GITHUB_ENV:-}" ]; then
    echo "MUNIN_PUBLIC_URL=${URL}" >> "$GITHUB_ENV"
fi

echo "$URL"
