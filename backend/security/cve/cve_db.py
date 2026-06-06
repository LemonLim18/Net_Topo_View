"""
M13d: CVE Mapping — SQLite-backed CVE Database
================================================
Persistent storage layer for known CVE records.  Complements the static
``cve_data.json`` file used by ``CVEMatcher`` with a live, updateable
SQLite table (``known_cves``) so newly published CVEs can be ingested
without redeploying the application.

The ``CVEDatabase`` class supports:
* ``upsert_cve(cve)``           — insert or update a single CVE record
* ``upsert_batch(cves)``        — bulk import (e.g. from NVD API)
* ``get_cves_for_vendor(vendor)``      — all CVEs for a vendor
* ``get_cves_for_version(vendor, ver)``— version-prefix matched CVEs
* ``get_by_id(cve_id)``         — lookup by CVE-ID string
* ``search(query)``             — full-text search on description
* ``export_json()``             — dump as JSON (compatible with cve_data.json)

Typical usage
-------------
    db = CVEDatabase(db_manager)
    db.upsert_cve({
        "id": "CVE-2024-20399",
        "vendor": "cisco_ios",
        "affected_versions": ["17.9", "17.10"],
        "cvss": 9.8,
        "severity": "critical",
        "description": "Unauthenticated RCE in IOS XE Web UI",
    })
    matches = db.get_cves_for_version("cisco_ios", "17.9.4a")
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from db.manager import DatabaseManager

logger = logging.getLogger("security.cve.db")

_DDL = """
CREATE TABLE IF NOT EXISTS known_cves (
    id              TEXT    PRIMARY KEY,        -- e.g. CVE-2024-20399
    vendor          TEXT    NOT NULL,           -- cisco_ios | huawei_vrp | juniper_junos | all
    affected_versions TEXT  NOT NULL,           -- JSON list of version prefix strings
    cvss            REAL    NOT NULL DEFAULT 0,
    severity        TEXT    NOT NULL DEFAULT 'unknown',  -- critical|high|medium|low
    description     TEXT    NOT NULL DEFAULT '',
    references      TEXT,                       -- JSON list of URLs
    published_at    TEXT,
    updated_at      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cve_vendor ON known_cves(vendor);
"""


class CVEDatabase:
    """
    SQLite-backed CRUD for CVE records.

    Parameters
    ----------
    db_manager : M14 DatabaseManager instance.
    """

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self._ensure_table()

    def _ensure_table(self) -> None:
        with self.db._connect() as conn:
            conn.executescript(_DDL)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def upsert_cve(self, cve: Dict[str, Any]) -> None:
        """
        Insert or update a CVE record.

        Required keys: ``id``, ``vendor``, ``affected_versions`` (list[str]).
        Optional keys: ``cvss``, ``severity``, ``description``, ``references``,
        ``published_at``.
        """
        ts = datetime.now(tz=timezone.utc).isoformat()
        affected = cve.get("affected_versions", [])
        if isinstance(affected, list):
            affected_str = json.dumps(affected)
        else:
            affected_str = str(affected)

        refs = cve.get("references", [])
        refs_str = json.dumps(refs) if isinstance(refs, list) else str(refs)

        with self.db._connect() as conn:
            conn.execute(
                """
                INSERT INTO known_cves
                    (id, vendor, affected_versions, cvss, severity, description,
                     references, published_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    vendor            = excluded.vendor,
                    affected_versions = excluded.affected_versions,
                    cvss              = excluded.cvss,
                    severity          = excluded.severity,
                    description       = excluded.description,
                    references        = excluded.references,
                    published_at      = excluded.published_at,
                    updated_at        = excluded.updated_at
                """,
                (
                    cve["id"],
                    cve.get("vendor", "all"),
                    affected_str,
                    float(cve.get("cvss", 0)),
                    cve.get("severity", "unknown"),
                    cve.get("description", ""),
                    refs_str,
                    cve.get("published_at"),
                    ts,
                ),
            )

    def upsert_batch(self, cves: List[Dict[str, Any]]) -> int:
        """
        Bulk upsert a list of CVE dicts.
        Returns count of processed records.
        """
        count = 0
        for cve in cves:
            try:
                self.upsert_cve(cve)
                count += 1
            except Exception as exc:
                logger.warning("Failed to upsert CVE %s: %s", cve.get("id", "?"), exc)
        logger.info("CVEDatabase: upserted %d/%d records", count, len(cves))
        return count

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_by_id(self, cve_id: str) -> Optional[Dict[str, Any]]:
        """Lookup a CVE by exact ID string (e.g. 'CVE-2024-20399')."""
        with self.db._connect() as conn:
            row = conn.execute(
                "SELECT * FROM known_cves WHERE id = ?", (cve_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def get_cves_for_vendor(self, vendor: str) -> List[Dict[str, Any]]:
        """Return all CVEs for a given vendor, sorted by CVSS descending."""
        with self.db._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM known_cves WHERE vendor = ? OR vendor = 'all' ORDER BY cvss DESC",
                (vendor,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_cves_for_version(self, vendor: str, os_version: str) -> List[Dict[str, Any]]:
        """
        Return CVEs that affect *vendor* and match *os_version* by prefix.

        Example: os_version='17.9.4a' matches affected_versions=['17.9', '17'].
        """
        all_vendor = self.get_cves_for_vendor(vendor)
        matches = []
        for cve in all_vendor:
            for prefix in cve.get("affected_versions", []):
                if os_version.startswith(str(prefix)):
                    matches.append(cve)
                    break
        # Sort by CVSS descending
        matches.sort(key=lambda c: c.get("cvss", 0), reverse=True)
        return matches

    def search(self, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Full-text search on CVE description (case-insensitive LIKE)."""
        with self.db._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM known_cves WHERE description LIKE ? ORDER BY cvss DESC LIMIT ?",
                (f"%{query}%", limit),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_critical(self, vendor: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return all critical (CVSS >= 9.0) CVEs, optionally filtered by vendor."""
        with self.db._connect() as conn:
            if vendor:
                rows = conn.execute(
                    "SELECT * FROM known_cves WHERE cvss >= 9.0 AND (vendor = ? OR vendor = 'all') ORDER BY cvss DESC",
                    (vendor,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM known_cves WHERE cvss >= 9.0 ORDER BY cvss DESC"
                ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def count(self) -> int:
        with self.db._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM known_cves").fetchone()[0]

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_json(self) -> str:
        """
        Dump all CVEs as a JSON string compatible with ``cve_data.json``
        format used by ``CVEMatcher``.
        """
        with self.db._connect() as conn:
            rows = conn.execute("SELECT * FROM known_cves").fetchall()
        cves = [self._row_to_dict(r) for r in rows]
        return json.dumps({"cves": cves}, indent=2)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row) -> Dict[str, Any]:
        d = dict(row)
        # Deserialise JSON fields
        for field in ("affected_versions", "references"):
            if isinstance(d.get(field), str):
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        return d
