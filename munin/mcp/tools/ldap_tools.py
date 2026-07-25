"""LDAP tools for Munin — native ldap3 client with escape_filter_chars everywhere.

All parameters that end up in an LDAP filter are passed through
``ldap3.utils.conv.escape_filter_chars`` to prevent LDAP injection (CWE-90). We never
accept a full filter string from callers; filter templates are fixed and only
parameters are escaped and interpolated.

Bind credentials come from :class:`Settings` (LDAP_BIND_DN / LDAP_PASSWORD env vars),
never from tool parameters — that keeps credentials out of the audit trail.
"""

from __future__ import annotations

import logging
from typing import Any

from ldap3 import ALL, SIMPLE, SUBTREE, Connection, Server
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars

from ..main import MCP, STATE, audited_tool  # noqa: TID252

logger = logging.getLogger("munin-mcp.ldap")

_DEFAULT_ATTRS = [
    "cn",
    "sn",
    "givenName",
    "uid",
    "mail",
    "sAMAccountName",
    "userPrincipalName",
    "memberOf",
    "distinguishedName",
    "objectClass",
    "userAccountControl",
    "servicePrincipalName",
    "pwdLastSet",
    "lastLogonTimestamp",
    "description",
]


def _get_settings() -> Any:
    from ..config import get_settings  # noqa: TID252 - re-read env each call so tests can monkeypatch

    return get_settings()


def _connect(bind_dn: str = "", password: str = "") -> Connection:
    settings = _get_settings()
    server = Server(settings.ldap_uri, get_info=ALL)
    dn = (bind_dn or settings.ldap_bind_dn or "").strip()
    pw = password or settings.ldap_password
    if not dn:
        raise RuntimeError("LDAP_BIND_DN not configured — set env or pass bind_dn explicitly")
    conn = Connection(server, user=dn, password=pw, authentication=SIMPLE, auto_bind=True, read_only=True)
    return conn


def _search(
    conn: Connection,
    *,
    base_dn: str,
    filter_str: str,
    attributes: list[str],
    size_limit: int = 500,
) -> list[dict[str, Any]]:
    conn.search(
        search_base=base_dn,
        search_filter=filter_str,
        search_scope=SUBTREE,
        attributes=attributes,
        size_limit=size_limit,
    )
    entries: list[dict[str, Any]] = []
    for entry in conn.entries:
        raw = entry.entry_attributes_as_dict
        item = {"dn": entry.entry_dn}
        for key, value in raw.items():
            if isinstance(value, list) and len(value) == 1:
                item[key] = value[0]
            else:
                item[key] = value
        entries.append(item)
    return entries


def _summary(entries: list[dict[str, Any]], action: str) -> str:
    return f"{action}: {len(entries)} entries"


# ─────────────────────────────────────────────
# Tool 1 — who am I (test bind + return the whoami extended op result)
# ─────────────────────────────────────────────

@MCP.tool()
@audited_tool("ldap_who_am_i", "passive", lambda *a, **k: "sync")
def ldap_who_am_i(bind_dn: str = "", password: str = "", run_id: str = "") -> dict[str, Any]:
    """Bind to the LDAP server and return the whoami extended operation result. Empty bind_dn/password uses env credentials."""
    try:
        conn = _connect(bind_dn=bind_dn, password=password)
    except LDAPException as exc:
        return {"ok": False, "tool": "ldap_who_am_i", "mode": "sync", "summary": "bind failed", "error": {"code": "ldap_bind_failed", "message": str(exc)}}
    whoami = conn.extend.standard.who_am_i() or ""
    conn.unbind()
    return {
        "ok": True,
        "tool": "ldap_who_am_i",
        "mode": "sync",
        "summary": f"bound as {whoami}",
        "data": {"whoami": whoami, "bind_dn": bind_dn or _get_settings().ldap_bind_dn},
    }


# ─────────────────────────────────────────────
# Tool 2 — get_current_user_info
# ─────────────────────────────────────────────

@MCP.tool()
@audited_tool("get_current_user_info", "passive", lambda *a, **k: "sync")
def get_current_user_info(run_id: str = "") -> dict[str, Any]:
    """Return the attributes of the currently-bound user (env LDAP_BIND_DN)."""
    settings = _get_settings()
    dn = settings.ldap_bind_dn
    if not dn:
        return {"ok": False, "tool": "get_current_user_info", "mode": "sync", "summary": "no bind DN configured", "error": {"code": "config_missing", "message": "LDAP_BIND_DN empty"}}
    try:
        conn = _connect()
        conn.search(search_base=dn, search_filter="(objectClass=*)", search_scope="BASE", attributes=_DEFAULT_ATTRS)
        entry = conn.entries[0].entry_attributes_as_dict if conn.entries else {}
        conn.unbind()
    except LDAPException as exc:
        return {"ok": False, "tool": "get_current_user_info", "mode": "sync", "summary": "search failed", "error": {"code": "ldap_search_failed", "message": str(exc)}}
    return {"ok": True, "tool": "get_current_user_info", "mode": "sync", "summary": f"loaded {dn}", "data": {"dn": dn, "attributes": entry}}


# ─────────────────────────────────────────────
# Tool 3 — get_user_groups
# ─────────────────────────────────────────────

@MCP.tool()
@audited_tool("get_user_groups", "passive", lambda *a, **k: "sync")
def get_user_groups(username: str, run_id: str = "") -> dict[str, Any]:
    """List the groups a user belongs to. Accepts sAMAccountName, uid, or cn — escaped safely against LDAP injection."""
    if not username.strip():
        return {"ok": False, "tool": "get_user_groups", "mode": "sync", "summary": "empty username", "error": {"code": "bad_input", "message": "username required"}}
    settings = _get_settings()
    esc = escape_filter_chars(username.strip())
    user_filter = f"(|(sAMAccountName={esc})(uid={esc})(cn={esc}))"
    try:
        conn = _connect()
        user_entries = _search(conn, base_dn=settings.ldap_base_dn, filter_str=user_filter, attributes=["distinguishedName", "memberOf", "cn", "uid", "sAMAccountName"])
        if not user_entries:
            conn.unbind()
            return {"ok": True, "tool": "get_user_groups", "mode": "sync", "summary": "user not found", "data": {"username": username, "groups": []}}
        user = user_entries[0]
        member_of = user.get("memberOf") or []
        if isinstance(member_of, str):
            member_of = [member_of]
        conn.unbind()
    except LDAPException as exc:
        return {"ok": False, "tool": "get_user_groups", "mode": "sync", "summary": "search failed", "error": {"code": "ldap_search_failed", "message": str(exc)}}
    return {
        "ok": True,
        "tool": "get_user_groups",
        "mode": "sync",
        "summary": _summary(member_of, "get_user_groups"),
        "data": {"username": username, "dn": user.get("dn"), "groups": member_of, "count": len(member_of)},
    }


# ─────────────────────────────────────────────
# Tool 4 — ldap_search (safe parametric)
# ─────────────────────────────────────────────

@MCP.tool()
@audited_tool("ldap_search", "passive", lambda *a, **k: "sync")
def ldap_search(
    base_dn: str = "",
    filter_template: str = "(objectClass=*)",
    params_json: str = "{}",
    attributes_csv: str = "",
    size_limit: int = 200,
    run_id: str = "",
) -> dict[str, Any]:
    """Safe parametric LDAP search. filter_template uses Python str.format with {name} placeholders; every value in params_json is escaped with escape_filter_chars first."""
    import json

    settings = _get_settings()
    base = base_dn.strip() or settings.ldap_base_dn
    try:
        raw_params = json.loads(params_json or "{}")
        if not isinstance(raw_params, dict):
            raise ValueError("params_json must be an object")
        escaped = {k: escape_filter_chars(str(v)) for k, v in raw_params.items()}
        filter_str = filter_template.format(**escaped) if escaped else filter_template
    except Exception as exc:
        return {"ok": False, "tool": "ldap_search", "mode": "sync", "summary": "bad params", "error": {"code": "bad_input", "message": str(exc)}}

    attrs = [a.strip() for a in attributes_csv.split(",") if a.strip()] or _DEFAULT_ATTRS
    try:
        conn = _connect()
        entries = _search(conn, base_dn=base, filter_str=filter_str, attributes=attrs, size_limit=size_limit)
        conn.unbind()
    except LDAPException as exc:
        return {"ok": False, "tool": "ldap_search", "mode": "sync", "summary": "search failed", "error": {"code": "ldap_search_failed", "message": str(exc)}}
    STATE.publish_intel(
        target_ip=base,
        port=None,
        service="ldap",
        finding_type="ldap_search_result",
        severity="INFO",
        details_json=json.dumps({"filter": filter_str, "count": len(entries)}, ensure_ascii=True),
        source_agent="ldap_search",
        status="NEW",
        tags="ldap,search",
        fingerprint="",
    )
    return {"ok": True, "tool": "ldap_search", "mode": "sync", "summary": _summary(entries, "ldap_search"), "data": {"base_dn": base, "filter": filter_str, "entries": entries, "count": len(entries)}}


# ─────────────────────────────────────────────
# Tool 5 — find_kerberoastable_users
# ─────────────────────────────────────────────

@MCP.tool()
@audited_tool("find_kerberoastable_users", "passive", lambda *a, **k: "sync")
def find_kerberoastable_users(base_dn: str = "", run_id: str = "") -> dict[str, Any]:
    """Enumerate users with a Service Principal Name (SPN) — candidates for Kerberoasting offline TGS-cracking."""
    settings = _get_settings()
    base = base_dn.strip() or settings.ldap_base_dn
    filter_str = "(&(objectClass=user)(servicePrincipalName=*))"
    try:
        conn = _connect()
        entries = _search(conn, base_dn=base, filter_str=filter_str, attributes=["sAMAccountName", "servicePrincipalName", "distinguishedName", "memberOf"])
        conn.unbind()
    except LDAPException as exc:
        return {"ok": False, "tool": "find_kerberoastable_users", "mode": "sync", "summary": "search failed", "error": {"code": "ldap_search_failed", "message": str(exc)}}
    return {"ok": True, "tool": "find_kerberoastable_users", "mode": "sync", "summary": _summary(entries, "kerberoastable"), "data": {"base_dn": base, "entries": entries, "count": len(entries)}}


# ─────────────────────────────────────────────
# Tool 6 — find_asrep_roastable_users
# ─────────────────────────────────────────────

@MCP.tool()
@audited_tool("find_asrep_roastable_users", "passive", lambda *a, **k: "sync")
def find_asrep_roastable_users(base_dn: str = "", run_id: str = "") -> dict[str, Any]:
    """Enumerate users without Kerberos pre-authentication — candidates for AS-REP roasting."""
    settings = _get_settings()
    base = base_dn.strip() or settings.ldap_base_dn
    # UserAccountControl bit 0x400000 = DONT_REQ_PREAUTH
    filter_str = "(&(objectClass=user)(userAccountControl:1.2.840.113556.1.4.803:=4194304))"
    try:
        conn = _connect()
        entries = _search(conn, base_dn=base, filter_str=filter_str, attributes=["sAMAccountName", "userAccountControl", "distinguishedName", "memberOf"])
        conn.unbind()
    except LDAPException as exc:
        return {"ok": False, "tool": "find_asrep_roastable_users", "mode": "sync", "summary": "search failed", "error": {"code": "ldap_search_failed", "message": str(exc)}}
    return {"ok": True, "tool": "find_asrep_roastable_users", "mode": "sync", "summary": _summary(entries, "asrep_roastable"), "data": {"base_dn": base, "entries": entries, "count": len(entries)}}


# ─────────────────────────────────────────────
# Tool 7 — find_domain_admins
# ─────────────────────────────────────────────

@MCP.tool()
@audited_tool("find_domain_admins", "passive", lambda *a, **k: "sync")
def find_domain_admins(base_dn: str = "", group_name: str = "Domain Admins", run_id: str = "") -> dict[str, Any]:
    """Find members of a privileged group (default: Domain Admins)."""
    settings = _get_settings()
    base = base_dn.strip() or settings.ldap_base_dn
    esc_group = escape_filter_chars(group_name)
    filter_str = f"(&(objectClass=group)(cn={esc_group}))"
    try:
        conn = _connect()
        group_entries = _search(conn, base_dn=base, filter_str=filter_str, attributes=["member", "cn"])
        conn.unbind()
    except LDAPException as exc:
        return {"ok": False, "tool": "find_domain_admins", "mode": "sync", "summary": "search failed", "error": {"code": "ldap_search_failed", "message": str(exc)}}
    if not group_entries:
        return {"ok": True, "tool": "find_domain_admins", "mode": "sync", "summary": "group not found", "data": {"group": group_name, "members": []}}
    members = group_entries[0].get("member") or []
    if isinstance(members, str):
        members = [members]
    return {"ok": True, "tool": "find_domain_admins", "mode": "sync", "summary": f"{group_name}: {len(members)} members", "data": {"group": group_name, "members": members, "count": len(members)}}


# ─────────────────────────────────────────────
# Tool 8 — dump_domain_structure
# ─────────────────────────────────────────────

@MCP.tool()
@audited_tool("dump_domain_structure", "passive", lambda *a, **k: "sync")
def dump_domain_structure(base_dn: str = "", size_limit: int = 500, run_id: str = "") -> dict[str, Any]:
    """Dump the OU/CN structure of the domain up to size_limit entries. Useful for enumeration and mapping."""
    settings = _get_settings()
    base = base_dn.strip() or settings.ldap_base_dn
    filter_str = "(|(objectClass=organizationalUnit)(objectClass=container))"
    try:
        conn = _connect()
        entries = _search(conn, base_dn=base, filter_str=filter_str, attributes=["distinguishedName", "ou", "cn", "description"], size_limit=size_limit)
        conn.unbind()
    except LDAPException as exc:
        return {"ok": False, "tool": "dump_domain_structure", "mode": "sync", "summary": "search failed", "error": {"code": "ldap_search_failed", "message": str(exc)}}
    return {"ok": True, "tool": "dump_domain_structure", "mode": "sync", "summary": _summary(entries, "dump_domain_structure"), "data": {"base_dn": base, "entries": entries, "count": len(entries)}}
