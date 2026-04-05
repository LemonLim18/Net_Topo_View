from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

ALLOWED_SCOPES = {"global", "site", "role", "device"}
DEFAULT_DB_PATH = "db/netauto.db"


def _connect(db_path: str):
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_common_services_table(db_path: str = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS common_service_profiles (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_type          TEXT NOT NULL,
                scope_value         TEXT NOT NULL,
                snmp_community      TEXT,
                snmp_location       TEXT,
                snmp_contact        TEXT,
                snmp_host_ip        TEXT,
                syslog_servers_json TEXT,
                ntp_servers_json    TEXT,
                syslog_host_ip      TEXT,
                ntp_server_ip       TEXT,
                created_at          TEXT DEFAULT (datetime('now')),
                updated_at          TEXT DEFAULT (datetime('now')),
                UNIQUE(scope_type, scope_value)
            )
            """
        )
        # Safe column additions for existing DBs.
        for col, col_def in (
            ("snmp_location", "TEXT"),
            ("snmp_contact", "TEXT"),
            ("snmp_host_ip", "TEXT"),
            ("syslog_host_ip", "TEXT"),
            ("ntp_server_ip", "TEXT"),
        ):
            try:
                conn.execute(f"ALTER TABLE common_service_profiles ADD COLUMN {col} {col_def}")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise


def _normalize_scope(scope_type: str, scope_value: str) -> tuple[str, str]:
    st = str(scope_type or "").strip().lower()
    sv = str(scope_value or "").strip()
    if st not in ALLOWED_SCOPES:
        raise ValueError(f"Invalid scope_type '{scope_type}'. Allowed: {sorted(ALLOWED_SCOPES)}")
    if st == "global":
        return st, "global"
    if not sv:
        raise ValueError("scope_value is required for site/role/device scopes")
    return st, sv


def _decode_list(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
    except Exception:
        return []
    return []


def _row_to_profile(row: sqlite3.Row) -> Dict[str, Any]:
    # Backward compatibility for rows created before explicit host columns existed.
    legacy_syslog = _decode_list(row["syslog_servers_json"]) if "syslog_servers_json" in row.keys() else []
    legacy_ntp = _decode_list(row["ntp_servers_json"]) if "ntp_servers_json" in row.keys() else []
    return {
        "id": row["id"],
        "scope_type": row["scope_type"],
        "scope_value": row["scope_value"],
        "snmp_community": row["snmp_community"],
        "snmp_location": row["snmp_location"] if "snmp_location" in row.keys() else None,
        "snmp_contact": row["snmp_contact"] if "snmp_contact" in row.keys() else None,
        "snmp_host_ip": row["snmp_host_ip"] if "snmp_host_ip" in row.keys() else None,
        "syslog_host_ip": (
            row["syslog_host_ip"] if ("syslog_host_ip" in row.keys() and row["syslog_host_ip"]) else (legacy_syslog[0] if legacy_syslog else None)
        ),
        "ntp_server_ip": (
            row["ntp_server_ip"] if ("ntp_server_ip" in row.keys() and row["ntp_server_ip"]) else (legacy_ntp[0] if legacy_ntp else None)
        ),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_profiles(db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    ensure_common_services_table(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM common_service_profiles
            ORDER BY
              CASE scope_type
                WHEN 'global' THEN 1
                WHEN 'site' THEN 2
                WHEN 'role' THEN 3
                WHEN 'device' THEN 4
                ELSE 9
              END,
              scope_value
            """
        ).fetchall()
        return [_row_to_profile(r) for r in rows]


def get_profile(scope_type: str, scope_value: str, db_path: str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    ensure_common_services_table(db_path)
    st, sv = _normalize_scope(scope_type, scope_value)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM common_service_profiles WHERE scope_type = ? AND scope_value = ?",
            (st, sv),
        ).fetchone()
        return _row_to_profile(row) if row else None


def upsert_profile(scope_type: str, scope_value: str, payload: Dict[str, Any], db_path: str = DEFAULT_DB_PATH) -> Dict[str, Any]:
    ensure_common_services_table(db_path)
    st, sv = _normalize_scope(scope_type, scope_value)

    snmp_community = payload.get("snmp_community")
    snmp_location = payload.get("snmp_location")
    snmp_contact = payload.get("snmp_contact")
    snmp_host_ip = payload.get("snmp_host_ip")
    syslog_host_ip = payload.get("syslog_host_ip")
    ntp_server_ip = payload.get("ntp_server_ip")

    # Maintain legacy json columns for compatibility with older readers.
    syslog_json = json.dumps([str(syslog_host_ip).strip()]) if str(syslog_host_ip or "").strip() else json.dumps([])
    ntp_json = json.dumps([str(ntp_server_ip).strip()]) if str(ntp_server_ip or "").strip() else json.dumps([])

    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO common_service_profiles
                (
                    scope_type, scope_value, snmp_community, snmp_location, snmp_contact, snmp_host_ip,
                    syslog_servers_json, ntp_servers_json, syslog_host_ip, ntp_server_ip, updated_at
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(scope_type, scope_value) DO UPDATE SET
                snmp_community = excluded.snmp_community,
                snmp_location = excluded.snmp_location,
                snmp_contact = excluded.snmp_contact,
                snmp_host_ip = excluded.snmp_host_ip,
                syslog_servers_json = excluded.syslog_servers_json,
                ntp_servers_json = excluded.ntp_servers_json,
                syslog_host_ip = excluded.syslog_host_ip,
                ntp_server_ip = excluded.ntp_server_ip,
                updated_at = datetime('now')
            """,
            (
                st,
                sv,
                snmp_community,
                snmp_location,
                snmp_contact,
                snmp_host_ip,
                syslog_json,
                ntp_json,
                syslog_host_ip,
                ntp_server_ip,
            ),
        )

    result = get_profile(st, sv, db_path=db_path)
    if not result:
        raise RuntimeError("Failed to persist common services profile")
    return result


def delete_profile(scope_type: str, scope_value: str, db_path: str = DEFAULT_DB_PATH) -> bool:
    ensure_common_services_table(db_path)
    st, sv = _normalize_scope(scope_type, scope_value)
    with _connect(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM common_service_profiles WHERE scope_type = ? AND scope_value = ?",
            (st, sv),
        )
        return cur.rowcount > 0


def get_effective_profile(
    db_path: str = DEFAULT_DB_PATH,
    *,
    site: Optional[str] = None,
    role: Optional[str] = None,
    device_id: Optional[int] = None,
) -> Dict[str, Any]:
    ensure_common_services_table(db_path)

    chain: List[tuple[str, str]] = [("global", "global")]
    if site:
        chain.append(("site", str(site).strip()))
    if role:
        chain.append(("role", str(role).strip().lower()))
    if device_id is not None:
        chain.append(("device", str(int(device_id))))

    merged: Dict[str, Any] = {
        "snmp_community": None,
        "snmp_location": None,
        "snmp_contact": None,
        "snmp_host_ip": None,
        "syslog_host_ip": None,
        "ntp_server_ip": None,
    }
    sources: Dict[str, Optional[str]] = {
        "snmp_community": None,
        "snmp_location": None,
        "snmp_contact": None,
        "snmp_host_ip": None,
        "syslog_host_ip": None,
        "ntp_server_ip": None,
    }

    with _connect(db_path) as conn:
        for st, sv in chain:
            row = conn.execute(
                "SELECT * FROM common_service_profiles WHERE scope_type = ? AND scope_value = ?",
                (st, sv),
            ).fetchone()
            if not row:
                continue

            label = f"{st}:{sv}"
            if row["snmp_community"]:
                merged["snmp_community"] = row["snmp_community"]
                sources["snmp_community"] = label
            if "snmp_location" in row.keys() and row["snmp_location"]:
                merged["snmp_location"] = row["snmp_location"]
                sources["snmp_location"] = label
            if "snmp_contact" in row.keys() and row["snmp_contact"]:
                merged["snmp_contact"] = row["snmp_contact"]
                sources["snmp_contact"] = label
            if "snmp_host_ip" in row.keys() and row["snmp_host_ip"]:
                merged["snmp_host_ip"] = row["snmp_host_ip"]
                sources["snmp_host_ip"] = label

            syslog_host_ip = row["syslog_host_ip"] if "syslog_host_ip" in row.keys() else None
            if not syslog_host_ip:
                syslog_servers = _decode_list(row["syslog_servers_json"]) if "syslog_servers_json" in row.keys() else []
                syslog_host_ip = syslog_servers[0] if syslog_servers else None
            if syslog_host_ip:
                merged["syslog_host_ip"] = syslog_host_ip
                sources["syslog_host_ip"] = label

            ntp_server_ip = row["ntp_server_ip"] if "ntp_server_ip" in row.keys() else None
            if not ntp_server_ip:
                ntp_servers = _decode_list(row["ntp_servers_json"]) if "ntp_servers_json" in row.keys() else []
                ntp_server_ip = ntp_servers[0] if ntp_servers else None
            if ntp_server_ip:
                merged["ntp_server_ip"] = ntp_server_ip
                sources["ntp_server_ip"] = label

    merged["sources"] = sources
    merged["chain"] = [f"{st}:{sv}" for st, sv in chain]
    return merged
