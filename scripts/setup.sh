#!/usr/bin/env bash
# One-shot setup: poetry install, ensure directories, snapshot the initial soul.
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v poetry >/dev/null 2>&1; then
  echo "installing poetry..."
  curl -sSL https://install.python-poetry.org | python3 -
fi

echo "[*] poetry install..."
poetry install --no-interaction

mkdir -p data soul munin/generated

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "[!] created .env from .env.example — populate LLM_* and LDAP_* before running."
fi

echo "[*] snapshotting current soul into data/soul.snapshot.json..."
poetry run munin snapshot-soul

echo "[✓] setup complete. Try: poetry run munin config"
