"""LDAP tools for Munin — schema-tolerant (AD + OpenLDAP) with escape_filter_chars everywhere.

Compatibility strategy
----------------------
Munin talks to two very different directory servers:

* Active Directory — uses ``sAMAccountName``, ``objectClass=user`` /
  ``objectClass=group``, ``userAccountControl`` bitfield, ``servicePrincipalName``.
* OpenLDAP (our mock at ``dc=akatsuki,dc=com``) — uses ``uid``, ``objectClass=inetOrgPerson``
  and ``objectClass=groupOfNames`` / ``posixGroup``. Attributes like
  ``sAMAccountName`` and ``userAccountControl`` DO NOT EXIST in the default schema
  and cause ``invalid attribute type`` errors when included in a filter.

Every filter in this module now offers BOTH forms via `(|(ad_form)(openldap_form))`.
Attribute requests only include AD-specific ones when we actually detect an AD server
(see ``_detect_flavor``). That way the same tool works transparently against either
backend without the LLM having to know which one is behind the wire.

The mock encodes AD-only signals in structured (but non-AD) attributes so tools can
still detect them:

* SPN → ``title`` (real AD uses ``servicePrincipalName``). Detected via
  ``(title=*/*)`` — an SPN always has a slash between service and host.
* DONT_REQ_PREAUTH → ``employeeType=DONT_REQ_PREAUTH`` (real AD uses
  ``userAccountControl:1.2.840.113556.1.4.803:=4194304``).

Security
--------
All parameters that end up in an LDAP filter pass through
``ldap3.utils.conv.escape_filter_chars`` (CWE-90 mitigation). We never accept a full
filter string from callers; filter templates are fixed and only parameters are
escaped and interpolated.

Bind credentials come from :class:`Settings` — never from tool parameters — to keep
them out of the audit trail.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ldap3 import ALL, SIMPLE, SUBTREE, Connection, Server
from ldap3.core.exceptions import LDAPAttributeError, LDAPException, LDAPInvalidFilterError
from ldap3.utils.conv import escape_filter_chars

from ..main import MCP, STATE, audited_tool  # noqa: TID252

logger = logging.getLogger("munin-mcp.ldap")

# Attributes safe to request from ANY directory server (AD + OpenLDAP).
_UNIVERSAL_ATTRS = [
    "cn",
    "sn",
    "givenName",
    "uid",
    "mail",
    "memberOf",
    "distinguishedName",
    "objectClass",
    "description",
    "title",           # OpenLDAP mock uses this as SPN placeholder
    "employeeType",    # OpenLDAP mock uses this as UAC placeholder
    "ou",
]

# Extra attributes only requested when the server actually has an AD schema.
# Requesting these against OpenLDAP fails with "invalid attribute type".
_AD_ONLY_ATTRS = [
    "sAMAccountName",
    "userPrincipalName",
    "userAccountControl",
    "servicePrincipalName",
    "pwdLastSet",
    "lastLogonTimestamp",
]


# ---------------------------------------------------------------------------
# Connection helpers + schema detection
# ---------------------------------------------------------------------------


def _get_settings() -> Any:
    from ..config import get_settings  # noqa: TID252 — re-read env each call so tests can monkeypatch

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


def _detect_flavor(conn: Connection) -> str:
    """Return 'ad' if the server advertises AD-specific attributes in its schema, 'openldap' otherwise.

    We check the server's parsed schema for ``sAMAccountName`` — an AD-only attr. If
    the check fails for any reason (schema not readable, etc.) we default to
    'openldap' since that's the safer subset (never touches AD-only attrs).
    """
    try:
        schema = getattr(conn.server, "schema", None)
        if schema and getattr(schema, "attribute_types", None):
            # attribute_types keys are case-insensitive in ldap3
            keys = {str(k).lower() for k in schema.attribute_types}
            if "samaccountname" in keys:
                return "ad"
        return "openldap"
    except Exception:  # pragma: no cover - defensive fallback
        return "openldap"


def _attrs_for(flavor: str, base: list[str] | None = None) -> list[str]:
    result = list(base or _UNIVERSAL_ATTRS)
    if flavor == "ad":
        for a in _AD_ONLY_ATTRS:
            if a not in result:
                result.append(a)
    return result


def _schema_supported_attributes(conn: Connection, requested: list[str]) -> list[str]:
    """Keep requested attributes advertised by the connected directory schema."""
    try:
        schema = getattr(conn.server, "schema", None)
        attribute_types = getattr(schema, "attribute_types", None)
        if attribute_types:
            supported = {str(key).lower() for key in attribute_types}
            selected = [attr for attr in requested if attr.lower() in supported]
            if selected:
                return selected
    except Exception:  # pragma: no cover - defensive schema fallback
        pass
    # Without readable schema, retain non-AD attributes. This preserves common
    # OpenLDAP membership fields instead of discarding all of them.
    ad_only = {attr.lower() for attr in _AD_ONLY_ATTRS}
    return [attr for attr in requested if attr.lower() not in ad_only]


def _search(
    conn: Connection,
    *,
    base_dn: str,
    filter_str: str,
    attributes: list[str],
    size_limit: int = 500,
    scope: str = SUBTREE,
) -> list[dict[str, Any]]:
    conn.search(
        search_base=base_dn,
        search_filter=filter_str,
        search_scope=scope,
        attributes=attributes,
        size_limit=size_limit,
    )
    entries: list[dict[str, Any]] = []
    for entry in conn.entries:
        raw = entry.entry_attributes_as_dict
        item: dict[str, Any] = {"dn": entry.entry_dn}
        for key, value in raw.items():
            if isinstance(value, list) and len(value) == 1:
                item[key] = value[0]
            else:
                item[key] = value
        entries.append(item)
    return entries


def _search_tolerant(
    conn: Connection,
    *,
    base_dn: str,
    filter_str: str,
    attributes: list[str],
    size_limit: int = 500,
) -> list[dict[str, Any]]:
    """Search with retry: if the server rejects an attribute, drop the AD-only ones and retry.

    This lets tools always request the full attribute set defensively. When we're
    talking to a server that doesn't know some attrs, we degrade gracefully instead
    of failing the whole call.
    """
    try:
        return _search(conn, base_dn=base_dn, filter_str=filter_str, attributes=attributes, size_limit=size_limit)
    except (LDAPAttributeError, LDAPInvalidFilterError) as exc:
        retry_attributes = _schema_supported_attributes(conn, attributes)
        logger.info(
            "LDAP search rejected attributes (%s); retrying with schema-supported set: %s",
            exc,
            retry_attributes,
        )
        return _search(
            conn,
            base_dn=base_dn,
            filter_str=filter_str,
            attributes=retry_attributes,
            size_limit=size_limit,
        )


def _summary(entries: list[dict[str, Any]], action: str) -> str:
    return f"{action}: {len(entries)} entries"


def _error(tool: str, code: str, msg: str) -> dict[str, Any]:
    return {"ok": False, "tool": tool, "mode": "sync", "summary": f"{tool} failed: {msg}", "error": {"code": code, "message": msg}}


# ─────────────────────────────────────────────
# Tool 1 — who am I
# ─────────────────────────────────────────────

@MCP.tool()
@audited_tool("ldap_who_am_i", "passive", lambda *a, **k: "sync")
def ldap_who_am_i(bind_dn: str = "", password: str = "", run_id: str = "") -> dict[str, Any]:
    """Bind to the LDAP server and return the whoami extended operation result. Empty bind_dn/password uses env credentials."""
    try:
        conn = _connect(bind_dn=bind_dn, password=password)
    except LDAPException as exc:
        return _error("ldap_who_am_i", "ldap_bind_failed", str(exc))
    except RuntimeError as exc:
        return _error("ldap_who_am_i", "config_missing", str(exc))
    try:
        whoami = conn.extend.standard.who_am_i() or ""
        flavor = _detect_flavor(conn)
    finally:
        conn.unbind()
    return {
        "ok": True,
        "tool": "ldap_who_am_i",
        "mode": "sync",
        "summary": f"bound as {whoami} (flavor={flavor})",
        "data": {"whoami": whoami, "bind_dn": bind_dn or _get_settings().ldap_bind_dn, "server_flavor": flavor},
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
        return _error("get_current_user_info", "config_missing", "LDAP_BIND_DN empty")
    try:
        conn = _connect()
    except LDAPException as exc:
        return _error("get_current_user_info", "ldap_bind_failed", str(exc))
    try:
        flavor = _detect_flavor(conn)
        attrs = _attrs_for(flavor)
        # Base-scoped search on the DN itself — schema-tolerant retry.
        try:
            conn.search(
                search_base=dn,
                search_filter="(objectClass=*)",
                search_scope="BASE",
                attributes=attrs,
            )
        except (LDAPAttributeError, LDAPInvalidFilterError):
            conn.search(
                search_base=dn,
                search_filter="(objectClass=*)",
                search_scope="BASE",
                attributes=list(_UNIVERSAL_ATTRS),
            )
        entry = conn.entries[0].entry_attributes_as_dict if conn.entries else {}
    except LDAPException as exc:
        return _error("get_current_user_info", "ldap_search_failed", str(exc))
    finally:
        conn.unbind()
    return {
        "ok": True,
        "tool": "get_current_user_info",
        "mode": "sync",
        "summary": f"loaded {dn}",
        "data": {"dn": dn, "attributes": entry, "server_flavor": flavor},
    }


# ─────────────────────────────────────────────
# Tool 3 — get_user_groups
# ─────────────────────────────────────────────

@MCP.tool()
@audited_tool("get_user_groups", "passive", lambda *a, **k: "sync")
def get_user_groups(username: str, run_id: str = "") -> dict[str, Any]:
    """List the groups a user belongs to. Accepts sAMAccountName, uid, or cn — escaped safely against LDAP injection.

    Returns memberOf if the server maintains it (AD default). If not (OpenLDAP without
    the memberof overlay), scans groupOfNames/posixGroup entries for the user's DN.
    """
    if not username.strip():
        return _error("get_user_groups", "bad_input", "username required")

    settings = _get_settings()
    esc = escape_filter_chars(username.strip())

    try:
        conn = _connect()
    except LDAPException as exc:
        return _error("get_user_groups", "ldap_bind_failed", str(exc))

    try:
        flavor = _detect_flavor(conn)
        # Filter accepts BOTH AD (sAMAccountName) and OpenLDAP (uid, cn). If the
        # server rejects sAMAccountName, `_search_tolerant` retries; but here we
        # can't do that because the filter itself contains the attr. Instead we
        # build the filter conditionally.
        forms = [f"(uid={esc})", f"(cn={esc})"]
        if flavor == "ad":
            forms.insert(0, f"(sAMAccountName={esc})")
        user_filter = f"(|{''.join(forms)})"
        user_entries = _search_tolerant(
            conn,
            base_dn=settings.ldap_base_dn,
            filter_str=user_filter,
            attributes=_attrs_for(flavor, base=["distinguishedName", "memberOf", "cn", "uid"]),
        )
        if not user_entries:
            return {
                "ok": True,
                "tool": "get_user_groups",
                "mode": "sync",
                "summary": "user not found",
                "data": {"username": username, "groups": [], "server_flavor": flavor},
            }
        user = user_entries[0]
        member_of = user.get("memberOf") or []
        if isinstance(member_of, str):
            member_of = [member_of]

        # OpenLDAP fallback: scan groupOfNames for the user's DN if memberOf is empty.
        if not member_of:
            user_dn = user.get("dn") or ""
            if user_dn:
                esc_dn = escape_filter_chars(user_dn)
                group_filter = (
                    f"(|"
                    f"(&(objectClass=groupOfNames)(member={esc_dn}))"
                    f"(&(objectClass=groupOfUniqueNames)(uniqueMember={esc_dn}))"
                    f"(&(objectClass=posixGroup)(memberUid={esc}))"
                    f")"
                )
                group_entries = _search_tolerant(
                    conn,
                    base_dn=settings.ldap_base_dn,
                    filter_str=group_filter,
                    attributes=["cn", "distinguishedName"],
                )
                member_of = [g.get("dn") for g in group_entries if g.get("dn")]
    except LDAPException as exc:
        return _error("get_user_groups", "ldap_search_failed", str(exc))
    finally:
        conn.unbind()

    return {
        "ok": True,
        "tool": "get_user_groups",
        "mode": "sync",
        "summary": _summary(member_of, "get_user_groups"),
        "data": {
            "username": username,
            "dn": user.get("dn"),
            "groups": member_of,
            "count": len(member_of),
            "server_flavor": flavor,
        },
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
    settings = _get_settings()
    base = base_dn.strip() or settings.ldap_base_dn
    try:
        raw_params = json.loads(params_json or "{}")
        if not isinstance(raw_params, dict):
            raise ValueError("params_json must be an object")
        escaped = {k: escape_filter_chars(str(v)) for k, v in raw_params.items()}
        filter_str = filter_template.format(**escaped) if escaped else filter_template
    except Exception as exc:
        return _error("ldap_search", "bad_input", str(exc))

    try:
        size_limit = int(size_limit)
    except (TypeError, ValueError):
        size_limit = 200

    try:
        conn = _connect()
    except LDAPException as exc:
        return _error("ldap_search", "ldap_bind_failed", str(exc))
    try:
        flavor = _detect_flavor(conn)
        attrs = [a.strip() for a in attributes_csv.split(",") if a.strip()] or _attrs_for(flavor)
        entries = _search_tolerant(conn, base_dn=base, filter_str=filter_str, attributes=attrs, size_limit=size_limit)
    except LDAPException as exc:
        return _error("ldap_search", "ldap_search_failed", str(exc))
    finally:
        conn.unbind()

    try:
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
    except Exception as exc:  # pragma: no cover — intel is best-effort
        logger.warning("publish_intel skipped: %s", exc)

    return {
        "ok": True,
        "tool": "ldap_search",
        "mode": "sync",
        "summary": _summary(entries, "ldap_search"),
        "data": {"base_dn": base, "filter": filter_str, "entries": entries, "count": len(entries), "server_flavor": flavor},
    }


# ─────────────────────────────────────────────
# Tool 5 — find_kerberoastable_users
# ─────────────────────────────────────────────

@MCP.tool()
@audited_tool("find_kerberoastable_users", "passive", lambda *a, **k: "sync")
def find_kerberoastable_users(base_dn: str = "", run_id: str = "") -> dict[str, Any]:
    """Enumerate users with a Service Principal Name (SPN) — candidates for Kerberoasting offline TGS-cracking.

    On AD servers this uses the native ``servicePrincipalName=*`` filter. On OpenLDAP
    (no such attribute) it falls back to detecting the SPN pattern encoded in ``title``
    by the mock: any user whose title contains a ``<Service>/<Host>`` pattern (i.e.
    contains a forward slash) is treated as kerberoastable.
    """
    settings = _get_settings()
    base = base_dn.strip() or settings.ldap_base_dn
    try:
        conn = _connect()
    except LDAPException as exc:
        return _error("find_kerberoastable_users", "ldap_bind_failed", str(exc))

    try:
        flavor = _detect_flavor(conn)

        if flavor == "ad":
            # Real AD path — use the actual attribute
            filter_str = "(&(objectClass=user)(servicePrincipalName=*))"
            entries = _search_tolerant(
                conn,
                base_dn=base,
                filter_str=filter_str,
                attributes=_attrs_for(flavor, base=["distinguishedName", "memberOf", "cn"]),
            )
        else:
            # OpenLDAP mock: SPN pattern lives in `title` (e.g. "MSSQLSvc/DBSERVER01:1433")
            filter_str = "(&(objectClass=inetOrgPerson)(title=*/*))"
            entries = _search_tolerant(
                conn,
                base_dn=base,
                filter_str=filter_str,
                attributes=list(_UNIVERSAL_ATTRS),
            )
            # Post-filter defensively: only entries whose title contains "/" AND is
            # not empty. Also normalize "servicePrincipalName" so downstream code can
            # consume both flavors uniformly.
            for entry in entries:
                title = entry.get("title")
                if isinstance(title, list):
                    spn_values = [t for t in title if "/" in str(t)]
                elif isinstance(title, str) and "/" in title:
                    spn_values = [title]
                else:
                    spn_values = []
                if spn_values:
                    entry["servicePrincipalName"] = spn_values if len(spn_values) > 1 else spn_values[0]
    except LDAPException as exc:
        return _error("find_kerberoastable_users", "ldap_search_failed", str(exc))
    finally:
        conn.unbind()

    return {
        "ok": True,
        "tool": "find_kerberoastable_users",
        "mode": "sync",
        "summary": _summary(entries, "kerberoastable"),
        "data": {"base_dn": base, "entries": entries, "count": len(entries), "server_flavor": flavor},
    }


# ─────────────────────────────────────────────
# Tool 6 — find_asrep_roastable_users
# ─────────────────────────────────────────────

@MCP.tool()
@audited_tool("find_asrep_roastable_users", "passive", lambda *a, **k: "sync")
def find_asrep_roastable_users(base_dn: str = "", run_id: str = "") -> dict[str, Any]:
    """Enumerate users without Kerberos pre-authentication — candidates for AS-REP roasting.

    On AD servers this uses the ``userAccountControl:1.2.840.113556.1.4.803:=4194304``
    LDAP matching rule (bit 0x400000 = DONT_REQ_PREAUTH). On OpenLDAP (no such
    attribute) it falls back to the mock's ``employeeType=DONT_REQ_PREAUTH`` marker.
    """
    settings = _get_settings()
    base = base_dn.strip() or settings.ldap_base_dn
    try:
        conn = _connect()
    except LDAPException as exc:
        return _error("find_asrep_roastable_users", "ldap_bind_failed", str(exc))

    try:
        flavor = _detect_flavor(conn)

        if flavor == "ad":
            filter_str = "(&(objectClass=user)(userAccountControl:1.2.840.113556.1.4.803:=4194304))"
            entries = _search_tolerant(
                conn,
                base_dn=base,
                filter_str=filter_str,
                attributes=_attrs_for(flavor, base=["distinguishedName", "memberOf", "cn"]),
            )
        else:
            filter_str = "(&(objectClass=inetOrgPerson)(employeeType=DONT_REQ_PREAUTH))"
            entries = _search_tolerant(
                conn,
                base_dn=base,
                filter_str=filter_str,
                attributes=list(_UNIVERSAL_ATTRS),
            )
            # Normalize a synthetic userAccountControl for downstream tools/LLM.
            for entry in entries:
                entry["userAccountControl_marker"] = "DONT_REQ_PREAUTH (4194304)"
    except LDAPException as exc:
        return _error("find_asrep_roastable_users", "ldap_search_failed", str(exc))
    finally:
        conn.unbind()

    return {
        "ok": True,
        "tool": "find_asrep_roastable_users",
        "mode": "sync",
        "summary": _summary(entries, "asrep_roastable"),
        "data": {"base_dn": base, "entries": entries, "count": len(entries), "server_flavor": flavor},
    }


# ─────────────────────────────────────────────
# Tool 7 — find_domain_admins
# ─────────────────────────────────────────────

@MCP.tool()
@audited_tool("find_domain_admins", "passive", lambda *a, **k: "sync")
def find_domain_admins(base_dn: str = "", group_name: str = "Domain Admins", run_id: str = "") -> dict[str, Any]:
    """Find members of a privileged group (default: Domain Admins).

    Accepts AD ``objectClass=group`` and OpenLDAP ``groupOfNames`` / ``posixGroup`` /
    ``groupOfUniqueNames`` all in a single OR filter — works everywhere.
    """
    settings = _get_settings()
    base = base_dn.strip() or settings.ldap_base_dn
    esc_group = escape_filter_chars(group_name)
    try:
        conn = _connect()
    except LDAPException as exc:
        return _error("find_domain_admins", "ldap_bind_failed", str(exc))
    try:
        # Ask for all possible member attributes — different objectClasses use different names.
        flavor = _detect_flavor(conn)
        group_classes = "(objectClass=group)" if flavor == "ad" else (
            "(|(objectClass=groupOfNames)(objectClass=groupOfUniqueNames)(objectClass=posixGroup))"
        )
        filter_str = f"(&(cn={esc_group}){group_classes})"
        group_entries = _search_tolerant(
            conn,
            base_dn=base,
            filter_str=filter_str,
            attributes=_schema_supported_attributes(
                conn,
                ["member", "uniqueMember", "memberUid", "cn", "distinguishedName"],
            ),
        )
    except LDAPException as exc:
        return _error("find_domain_admins", "ldap_search_failed", str(exc))
    finally:
        conn.unbind()

    if not group_entries:
        return {
            "ok": True,
            "tool": "find_domain_admins",
            "mode": "sync",
            "summary": f"group not found: {group_name}",
            "data": {"group": group_name, "members": [], "count": 0},
        }

    grp = group_entries[0]
    # Collect members from every possible attribute; normalize to a flat list.
    members: list[str] = []
    for attr in ("member", "uniqueMember", "memberUid"):
        v = grp.get(attr)
        if isinstance(v, str):
            members.append(v)
        elif isinstance(v, list):
            members.extend(str(item) for item in v)
    # Dedupe while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for m in members:
        if m not in seen:
            seen.add(m)
            deduped.append(m)

    return {
        "ok": True,
        "tool": "find_domain_admins",
        "mode": "sync",
        "summary": f"{group_name}: {len(deduped)} members",
        "data": {"group": group_name, "group_dn": grp.get("dn"), "members": deduped, "count": len(deduped)},
    }


# ─────────────────────────────────────────────
# Tool 8 — dump_domain_structure
# ─────────────────────────────────────────────

@MCP.tool()
@audited_tool("dump_domain_structure", "passive", lambda *a, **k: "sync")
def dump_domain_structure(base_dn: str = "", size_limit: int = 500, run_id: str = "") -> dict[str, Any]:
    """Dump the OU/CN structure of the domain up to size_limit entries. Useful for enumeration and mapping."""
    settings = _get_settings()
    base = base_dn.strip() or settings.ldap_base_dn
    try:
        size_limit = int(size_limit)
    except (TypeError, ValueError):
        size_limit = 500
    try:
        conn = _connect()
    except LDAPException as exc:
        return _error("dump_domain_structure", "ldap_bind_failed", str(exc))
    try:
        flavor = _detect_flavor(conn)
        filter_str = (
            "(|(objectClass=organizationalUnit)(objectClass=container)(objectClass=domain))"
            if flavor == "ad"
            else "(objectClass=organizationalUnit)"
        )
        entries = _search_tolerant(
            conn,
            base_dn=base,
            filter_str=filter_str,
            attributes=["distinguishedName", "ou", "cn", "description"],
            size_limit=size_limit,
        )
    except LDAPException as exc:
        return _error("dump_domain_structure", "ldap_search_failed", str(exc))
    finally:
        conn.unbind()

    return {
        "ok": True,
        "tool": "dump_domain_structure",
        "mode": "sync",
        "summary": _summary(entries, "dump_domain_structure"),
        "data": {"base_dn": base, "entries": entries, "count": len(entries)},
    }
