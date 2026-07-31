#!/usr/bin/env bash
# Munin — Mock LDAP toggle
# Usage: ./scripts/ldap_mock.sh {up|down|status|logs}
#
# Launches a test OpenLDAP server pre-seeded with users, service accounts, groups, and OUs
# for offensive security scenarios (Kerberoastable accounts, AS-REP roasting, Domain Admins).

set -euo pipefail

CONTAINER="munin_ldap_mock"
IMAGE="osixia/openldap:1.5.0"
HOST_PORT="${LDAP_MOCK_PORT:-389}"
LDAP_ROOT="dc=akatsuki,dc=com"
ADMIN_PASS="itachi"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LDIF="$SCRIPT_DIR/ldap_mock.ldif"
WEB_LAB_LDIF="$SCRIPT_DIR/ldap_seed/60-web-lab.ldif"

# ── helpers ──────────────────────────────────────────────────────────────────

log()  { echo "[ldap-mock] $*"; }
err()  { echo "[ldap-mock] ERROR: $*" >&2; exit 1; }

_require_docker() {
    command -v docker &>/dev/null || err "docker not found — install Docker Desktop first"
}

_container_running() {
    docker inspect "$CONTAINER" --format '{{.State.Running}}' 2>/dev/null | grep -q true
}

_container_exists() {
    docker inspect "$CONTAINER" &>/dev/null
}

_wait_ldap() {
    log "Waiting for LDAP to accept connections..."
    for i in $(seq 1 30); do
        if ldapsearch -H ldap://localhost:${HOST_PORT} -x \
           -D "cn=admin,${LDAP_ROOT}" -w "${ADMIN_PASS}" \
           -b "${LDAP_ROOT}" "(objectClass=*)" dn 2>/dev/null | grep -q "result: 0"; then
            log "LDAP ready (${i}s)"
            return 0
        fi
        sleep 1
    done
    err "LDAP did not become ready after 30s — check: docker logs ${CONTAINER}"
}

_verify_populated() {
    ldapsearch -H ldap://localhost:${HOST_PORT} -x \
        -D "cn=admin,${LDAP_ROOT}" -w "${ADMIN_PASS}" \
        -b "${LDAP_ROOT}" "(ou=users)" dn 2>/dev/null | grep -q "ou=users"
}

_seed() {
    if [ ! -f "$LDIF" ]; then
        err "LDIF file not found: $LDIF"
    fi
    if _verify_populated; then
        log "Directory structure '${LDAP_ROOT}' already populated."
        return 0
    fi
    log "Seeding mock data from $(basename $LDIF) ..."
    for seed_file in "$LDIF" "$WEB_LAB_LDIF"; do
        [ -f "$seed_file" ] || continue
        ldapadd -c -H ldap://localhost:${HOST_PORT} -x \
            -D "cn=admin,${LDAP_ROOT}" -w "${ADMIN_PASS}" \
            -f "$seed_file" 2>&1 | grep -v "^$" || true
    done
    log "Seed complete."
}

# ── commands ─────────────────────────────────────────────────────────────────

cmd_up() {
    _require_docker

    if _container_running; then
        if _verify_populated; then
            log "Container '${CONTAINER}' is running and populated on port ${HOST_PORT}."
            _print_env
            return 0
        else
            log "Existing container lacks valid '${LDAP_ROOT}' base DN or entries. Recreating..."
            docker rm -f "$CONTAINER" &>/dev/null || true
        fi
    elif _container_exists; then
        log "Removing stale container before recreation..."
        docker rm -f "$CONTAINER" &>/dev/null || true
    fi

    log "Pulling ${IMAGE} and starting fresh container..."
    docker run -d \
        --name "$CONTAINER" \
        -p "${HOST_PORT}:389" \
        -e LDAP_ORGANISATION="AKATSUKI" \
        -e LDAP_DOMAIN="akatsuki.com" \
        -e LDAP_BASE_DN="${LDAP_ROOT}" \
        -e LDAP_ADMIN_PASSWORD="${ADMIN_PASS}" \
        -e LDAP_CONFIG_PASSWORD="${ADMIN_PASS}" \
        -e LDAP_TLS="false" \
        "$IMAGE"

    _wait_ldap
    _seed
    echo ""
    _print_env
}

cmd_down() {
    _require_docker
    if _container_exists; then
        docker stop "$CONTAINER" 2>/dev/null || true
        docker rm   "$CONTAINER" 2>/dev/null || true
        log "Container '${CONTAINER}' stopped and removed."
    else
        log "Container '${CONTAINER}' does not exist — nothing to stop."
    fi
}

cmd_status() {
    _require_docker
    if _container_running; then
        echo "[ldap-mock] Status: RUNNING"
        echo "[ldap-mock] Port  : ${HOST_PORT}"
        echo "[ldap-mock] Bind  : cn=admin,${LDAP_ROOT}"
        echo "[ldap-mock] Pass  : ${ADMIN_PASS}"
        echo ""
        # Show user/group count
        USERS=$(ldapsearch -H ldap://localhost:${HOST_PORT} -x \
            -D "cn=admin,${LDAP_ROOT}" -w "${ADMIN_PASS}" \
            -b "${LDAP_ROOT}" "(objectClass=inetOrgPerson)" uid 2>/dev/null \
            | grep -c "^uid:" || echo "?")
        GROUPS=$(ldapsearch -H ldap://localhost:${HOST_PORT} -x \
            -D "cn=admin,${LDAP_ROOT}" -w "${ADMIN_PASS}" \
            -b "${LDAP_ROOT}" "(objectClass=groupOfNames)" cn 2>/dev/null \
            | grep -c "^cn:" || echo "?")
        echo "[ldap-mock] Entries: ${USERS} users, ${GROUPS} groups"
    elif _container_exists; then
        echo "[ldap-mock] Status: STOPPED (container exists, not running)"
        echo "[ldap-mock] Run: ./scripts/ldap_mock.sh up"
    else
        echo "[ldap-mock] Status: NOT CREATED"
        echo "[ldap-mock] Run: ./scripts/ldap_mock.sh up"
    fi
}

cmd_logs() {
    _require_docker
    docker logs "$CONTAINER" --tail 50
}

_print_env() {
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Mock LDAP  LIVE"
    echo "  URI  : ldap://localhost:${HOST_PORT}"
    echo "  Base : ${LDAP_ROOT}"
    echo "  Bind : cn=admin,${LDAP_ROOT}"
    echo "  Pass : ${ADMIN_PASS}"
    echo ""
    echo "  .env settings:"
    echo "    LDAP_URI=ldap://localhost:${HOST_PORT}"
    echo "    LDAP_BASE_DN=${LDAP_ROOT}"
    echo "    LDAP_BIND_DN=cn=admin,${LDAP_ROOT}"
    echo "    LDAP_PASSWORD=${ADMIN_PASS}"
    echo ""
    echo "  Mock accounts:"
    echo "    jdoe / asmith / rgarcia / mlopez — standard domain users"
    echo "    administrator               — Domain Admin"
    echo "    htarget                     — AS-REP Roastable account"
    echo "    svc_backup / svc_mssql / svc_http / svc_jenkins — Kerberoastable service accounts"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

# ── entrypoint ────────────────────────────────────────────────────────────────

CMD="${1:-status}"
case "$CMD" in
    up)     cmd_up ;;
    down)   cmd_down ;;
    status) cmd_status ;;
    logs)   cmd_logs ;;
    *)
        echo "Usage: $0 {up|down|status|logs}"
        exit 1
        ;;
esac
