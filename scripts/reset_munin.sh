#!/usr/bin/env bash
# Reset Munin — restores soul, wipes memory + generated tools + graphs.
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v poetry >/dev/null 2>&1; then
  echo "poetry not installed — install with 'curl -sSL https://install.python-poetry.org | python3 -'" >&2
  exit 2
fi

echo "[*] running munin reset..."
poetry run munin reset --yes
echo "[✓] reset complete."
