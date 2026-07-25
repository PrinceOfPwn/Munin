#!/usr/bin/env bash
# Munin — Mock LDAP toggle
# Usage: ./scripts/ldap_mock.sh {up|down|status|logs}
#
# Levanta un OpenLDAP de prueba con usuarios, service accounts, grupos y OUs
# pre-sembrados con escenarios ofensivos (Kerberoastable, AS-REP, Domain Admins).

set -euo pipefail

CONTAINER="munin_ldap_mock"
IMAGE="bitnami/openldap:latest"
HOST_PORT="${LDAP_MOCK_PORT:-389}"
LDAP_ROOT="dc=meli,dc=com"
ADMIN_PASS="itachi"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LDIF="$SCRIPT_DIR/ldap_mock.ldif"

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

_seed() {
    if [ ! -f "$LDIF" ]; then
        err "LDIF file not found: $LDIF"
    fi
    log "Seeding mock data from $(basename $LDIF) ..."
    ldapadd -H ldap://localhost:${HOST_PORT} -x \
        -D "cn=admin,${LDAP_ROOT}" -w "${ADMIN_PASS}" \
        -f "$LDIF" 2>&1 | grep -v "^$" || true
    log "Seed complete."
}

# ── commands ─────────────────────────────────────────────────────────────────

cmd_up() {
    _require_docker

    if _container_running; then
        log "Container '${CONTAINER}' is already running on port ${HOST_PORT}."
        _print_env
        return 0
    fi

    if _container_exists; then
        log "Restarting existing container..."
        docker start "$CONTAINER"
    else
        log "Pulling ${IMAGE} and starting container..."
        docker run -d \
            --name "$CONTAINER" \
            -p "${HOST_PORT}:1389" \
            -e LDAP_ROOT="${LDAP_ROOT}" \
            -e LDAP_ADMIN_USERNAME="admin" \
            -e LDAP_ADMIN_PASSWORD="${ADMIN_PASS}" \
            -e LDAP_SKIP_DEFAULT_TREE="yes" \
            "$IMAGE"
    fi

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
    echo "  Mock users:"
    echo "    jdoe / asmith / rgarcia / mlopez — usuarios normales"
    echo "    administrator               — Domain Admin"
    echo "    htarget                     — AS-REP Roastable (simulado)"
    echo "    svc_backup / svc_mssql / svc_http / svc_jenkins — Kerberoastable"
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
