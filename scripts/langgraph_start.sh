#!/usr/bin/env bash
# Start LangGraph dev server and wait for it to be healthy.
set -euo pipefail

PORT="${MUNIN_LANGGRAPH_PORT:-8123}"
CONFIG="${MUNIN_LANGGRAPH_CONFIG:-langgraph.json}"
LOG_FILE="${MUNIN_LANGGRAPH_LOG:-/tmp/langgraph_server.log}"

# Generate a local API key if not set
if [ -z "${LANGGRAPH_API_KEY:-}" ]; then
  LANGGRAPH_API_KEY="$(python3 scripts/langgraph_generate_key.py)"
  export LANGGRAPH_API_KEY
fi

echo "Starting LangGraph dev server on port $PORT..."
langgraph dev \
  --port "$PORT" \
  --config "$CONFIG" \
  --no-browser \
  >> "$LOG_FILE" 2>&1 &

LANGGRAPH_PID=$!
echo "LangGraph PID: $LANGGRAPH_PID"
echo "$LANGGRAPH_PID" > /tmp/langgraph.pid

# Wait for health endpoint
echo "Waiting for LangGraph server to be healthy..."
for i in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:${PORT}/health" > /dev/null 2>&1; then
    echo "LangGraph server healthy after ${i} attempts."
    echo "MUNIN_LANGGRAPH_URL=http://127.0.0.1:${PORT}" >> "${GITHUB_ENV:-/dev/null}" 2>/dev/null || true
    exit 0
  fi
  sleep 2
done

echo "ERROR: LangGraph server did not become healthy within 60s" >&2
cat "$LOG_FILE" >&2
exit 1
