#!/usr/bin/env bash
# Bring up the OpenLDAP challenge stack (openldap + phpldapadmin).
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not installed — install Docker Desktop or docker.io first." >&2
  exit 2
fi

# `docker compose` (v2) or `docker-compose` (v1) — support both.
if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  echo "docker compose not available." >&2
  exit 2
fi

echo "[*] starting OpenLDAP + phpLDAPAdmin (docker compose up -d)..."
$COMPOSE up -d openldap phpldapadmin

echo "[*] waiting 15s for ldap to boot..."
sleep 15

echo "[*] LDAP is on ldap://localhost:389 (admin: cn=admin,dc=meli,dc=com / itachi)"
echo "[*] phpLDAPAdmin on http://localhost:8080"
